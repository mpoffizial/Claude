"""
Maker Backtest Engine
=====================
Simulates the Maker-Rebate strategy on real BTC price history.

Data source:
  Real Binance BTCUSDT 1-minute klines (fetched via REST API).

Polymarket price derivation:
  Each 15-minute market is a binary option: "Will BTC be UP in 15 min?"
  The fair value of the Yes token evolves according to binary-option pricing
  (Black-Scholes digital cash-or-nothing call), giving us a realistic
  intrawindow price series without needing Polymarket orderbook history.

  P_yes(t) = Φ(d2)
  where d2 = ln(S_t / S_0) / (σ * √T_remaining)
  and Φ is the standard normal CDF.

Fill simulation:
  Each 1-minute tick, the maker has bid/ask orders.
  Fill probability per tick per side:
    p_fill = min(base_prob + |Δprice| × vol_sensitivity, max_prob)
  Direction: price-up tick → ask more likely; price-down → bid more likely.
  This models taker order flow arriving in the direction of price movement.

Adverse selection:
  For each fill, we look at the NEXT tick's price change.
  If the market moved against us (we bought and price fell, or we sold and
  price rose), we record an adverse-selection loss proportional to the move.

Settlement:
  At market close, remaining inventory is settled at binary outcome:
    Yes tokens: worth $1 if BTC_close > BTC_open, else $0
    No tokens:  worth $1 if BTC_close < BTC_open, else $0
  Settlement PnL = payout - cost_basis of remaining inventory.

P&L attribution:
  Gross income = spread_capture + maker_rebate + arb_income + settlement_pnl
  Net PnL      = Gross income - adverse_selection_loss
"""

import logging
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from polymarket_btc_bot.maker.config import MakerBotConfig
from polymarket_btc_bot.maker.inventory_manager import InventoryManager
from polymarket_btc_bot.maker.rebate_tracker import RebateTracker
from polymarket_btc_bot.maker.pricing_engine import PricingEngine
from polymarket_btc_bot.backtesting.historical_loader import HistoricalKline

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backtest-specific configuration
# ---------------------------------------------------------------------------

@dataclass
class MakerBacktestConfig:
    """Parameters that control how the backtest simulation works."""

    # --- BTC volatility model (for deriving token prices) ---
    btc_annual_vol: float = 0.65            # 65% annualised BTC volatility

    # --- Fill simulation ---
    base_fill_prob_per_min: float = 0.12    # Base fill probability per side per 1-min tick
    vol_sensitivity: float = 25.0           # Multiplier: |Δprice| → extra fill prob
    max_fill_prob_per_min: float = 0.65     # Cap on fill probability per tick
    partial_fill_rate: float = 0.90         # 90% of fills are fully filled

    # --- Micro-arb simulation ---
    # Arb opportunities arise when the market is mis-priced (typically near open).
    arb_prob_per_window: float = 0.12       # 12% of windows have at least one arb opp
    arb_profit_range: tuple = (0.005, 0.025)  # Arb profit in USDC per execution

    # --- Seed ---
    random_seed: Optional[int] = 42         # None = non-deterministic


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class WindowFill:
    """Record of one simulated fill within a market window."""
    side: str           # "yes" or "no"
    direction: str      # "buy" | "sell"
    price: float
    size_usdc: float
    fair_value: float
    elapsed_s: float
    spread_captured: float
    adverse_selection: float
    estimated_rebate: float


@dataclass
class WindowResult:
    """Simulation results for one 15-minute market window."""
    window_start_ts: int        # Unix ms
    btc_open: float
    btc_close: float
    yes_won: bool

    # --- Activity ---
    fills: list[WindowFill] = field(default_factory=list)
    arb_profit: float = 0.0
    arb_executions: int = 0

    # --- P&L breakdown ---
    spread_income: float = 0.0
    rebate_income: float = 0.0
    adverse_selection_loss: float = 0.0
    settlement_pnl: float = 0.0     # Gain/loss from inventory held at expiry

    @property
    def btc_return_pct(self) -> float:
        if self.btc_open <= 0:
            return 0.0
        return (self.btc_close - self.btc_open) / self.btc_open * 100.0

    @property
    def gross_income(self) -> float:
        return self.spread_income + self.rebate_income + self.arb_profit

    @property
    def net_pnl(self) -> float:
        return self.gross_income - self.adverse_selection_loss + self.settlement_pnl

    @property
    def total_fills(self) -> int:
        return len(self.fills)

    @property
    def volume_usdc(self) -> float:
        return sum(f.size_usdc for f in self.fills)


@dataclass
class MakerBacktestResult:
    """Aggregate results across all simulated windows."""
    windows: list[WindowResult] = field(default_factory=list)
    config_used: Optional[MakerBotConfig] = None
    bt_config_used: Optional[MakerBacktestConfig] = None
    backtest_days: float = 0.0

    # --- Computed aggregates (populated by .compute()) ---
    total_windows: int = 0
    total_fills: int = 0
    total_volume_usdc: float = 0.0
    spread_income: float = 0.0
    rebate_income: float = 0.0
    arb_income: float = 0.0
    adverse_selection_loss: float = 0.0
    settlement_pnl: float = 0.0
    net_pnl: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate_windows: float = 0.0   # % of windows with positive net_pnl
    fills_per_window: float = 0.0
    pnl_per_window: float = 0.0
    pnl_per_day: float = 0.0
    spread_yield_pct: float = 0.0   # spread_income / volume
    rebate_yield_pct: float = 0.0   # rebate_income / volume
    adverse_sel_pct: float = 0.0    # adverse_selection / volume
    net_yield_pct: float = 0.0      # net_pnl / volume

    def compute(self) -> "MakerBacktestResult":
        """Populate aggregate fields from windows list."""
        if not self.windows:
            return self

        self.total_windows = len(self.windows)
        self.total_fills = sum(w.total_fills for w in self.windows)
        self.total_volume_usdc = sum(w.volume_usdc for w in self.windows)
        self.spread_income = sum(w.spread_income for w in self.windows)
        self.rebate_income = sum(w.rebate_income for w in self.windows)
        self.arb_income = sum(w.arb_profit for w in self.windows)
        self.adverse_selection_loss = sum(w.adverse_selection_loss for w in self.windows)
        self.settlement_pnl = sum(w.settlement_pnl for w in self.windows)
        self.net_pnl = sum(w.net_pnl for w in self.windows)

        window_pnls = [w.net_pnl for w in self.windows]
        self.win_rate_windows = sum(1 for p in window_pnls if p > 0) / len(window_pnls)
        self.fills_per_window = self.total_fills / self.total_windows
        self.pnl_per_window = self.net_pnl / self.total_windows

        if self.backtest_days > 0:
            # One market per 15 min → 96 markets/day
            days = self.total_windows / 96.0
            self.backtest_days = days
            self.pnl_per_day = self.net_pnl / max(1.0, days)

        # Sharpe ratio (annualised, per-window PnL)
        if len(window_pnls) > 1:
            arr = np.array(window_pnls)
            mean = float(np.mean(arr))
            std = float(np.std(arr, ddof=1))
            if std > 0:
                # 96 windows per day, 365 days → annualisation factor
                self.sharpe_ratio = (mean / std) * math.sqrt(96 * 365)

        # Max drawdown
        cumulative = np.cumsum(window_pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        self.max_drawdown = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0

        # Yields
        if self.total_volume_usdc > 0:
            self.spread_yield_pct = self.spread_income / self.total_volume_usdc * 100
            self.rebate_yield_pct = self.rebate_income / self.total_volume_usdc * 100
            self.adverse_sel_pct = self.adverse_selection_loss / self.total_volume_usdc * 100
            self.net_yield_pct = self.net_pnl / self.total_volume_usdc * 100

        return self

    def summary(self) -> str:
        days = self.total_windows / 96.0
        return (
            f"\n{'=' * 60}\n"
            f"  MAKER BACKTEST RESULTS\n"
            f"{'=' * 60}\n"
            f"  Period         : {days:.1f} days ({self.total_windows} markets)\n"
            f"  Total Fills    : {self.total_fills}  ({self.fills_per_window:.1f}/market)\n"
            f"  Total Volume   : ${self.total_volume_usdc:,.2f}\n"
            f"{'─' * 60}\n"
            f"  P&L BREAKDOWN:\n"
            f"  Spread income  : ${self.spread_income:,.4f}  ({self.spread_yield_pct:.3f}%/vol)\n"
            f"  Rebate income  : ${self.rebate_income:,.4f}  ({self.rebate_yield_pct:.3f}%/vol)\n"
            f"  Arb income     : ${self.arb_income:,.4f}\n"
            f"  Settlement PnL : ${self.settlement_pnl:,.4f}\n"
            f"  Adverse sel.   : -${self.adverse_selection_loss:,.4f}  "
            f"({self.adverse_sel_pct:.3f}%/vol)\n"
            f"{'─' * 60}\n"
            f"  NET P&L        : ${self.net_pnl:,.4f}\n"
            f"  Per market     : ${self.pnl_per_window:,.4f}\n"
            f"  Per day        : ${self.pnl_per_day:,.2f}\n"
            f"  Net yield      : {self.net_yield_pct:.4f}%/vol\n"
            f"{'─' * 60}\n"
            f"  Win rate       : {self.win_rate_windows:.1%} of markets profitable\n"
            f"  Sharpe ratio   : {self.sharpe_ratio:.2f}  (annualised)\n"
            f"  Max drawdown   : ${self.max_drawdown:,.4f}\n"
            f"{'=' * 60}\n"
        )


# ---------------------------------------------------------------------------
# Core backtest engine
# ---------------------------------------------------------------------------

class MakerBacktester:
    """
    Simulates the Maker-Rebate strategy on a series of 1-minute BTC klines.

    The key design principle: decisions at time t only use data up to t
    (no look-ahead bias).  The one exception is adverse-selection estimation,
    which looks at t+1 (the "real" impact of informed flow).
    """

    def __init__(
        self,
        config: MakerBotConfig,
        bt_config: Optional[MakerBacktestConfig] = None,
    ):
        self.config = config
        self.bt_cfg = bt_config or MakerBacktestConfig()

        if self.bt_cfg.random_seed is not None:
            random.seed(self.bt_cfg.random_seed)
            np.random.seed(self.bt_cfg.random_seed)

    # ------------------------------------------------------------------
    # Token price derivation
    # ------------------------------------------------------------------

    def derive_token_price(
        self,
        btc_current: float,
        btc_open: float,
        elapsed_s: float,
        total_s: float = 900.0,
    ) -> float:
        """
        Fair value of Yes token using binary-option pricing (digital call).

        P_yes = Φ(d2)
        d2    = ln(S/K) / (σ * √T_remaining)

        This is the risk-neutral probability that the BTC price at expiry
        exceeds the opening price (the binary outcome).

        Args:
            btc_current: Current BTC price
            btc_open:    BTC price at market open (= strike)
            elapsed_s:   Seconds elapsed since market open
            total_s:     Total market duration in seconds (900 = 15 min)

        Returns:
            Yes fair value in [0.05, 0.95]
        """
        remaining_s = max(0.5, total_s - elapsed_s)
        remaining_years = remaining_s / (365.25 * 24.0 * 3600.0)

        if btc_open <= 0 or btc_current <= 0:
            return 0.50

        log_return = math.log(btc_current / btc_open)
        sigma_sqrt_t = self.bt_cfg.btc_annual_vol * math.sqrt(remaining_years)

        if sigma_sqrt_t < 1e-10:
            # At expiry – deterministic result
            return 1.0 if btc_current > btc_open else 0.0

        # d2 (ignoring drift term for simplicity – negligible over 15 min)
        d2 = log_return / sigma_sqrt_t
        prob_up = self._norm_cdf(d2)
        return max(0.05, min(0.95, prob_up))

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """Standard normal CDF via math.erf (no scipy dependency)."""
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    # ------------------------------------------------------------------
    # Fill simulation
    # ------------------------------------------------------------------

    def _simulate_fills(
        self,
        bid_price: float,
        ask_price: float,
        quote_size_usdc: float,
        yes_fv_prev: float,
        yes_fv_curr: float,
    ) -> tuple[float, float]:
        """
        Simulate whether bid and/or ask are filled in this 1-min tick.

        Args:
            bid_price:      Our bid (we buy if takers sell to us)
            ask_price:      Our ask (we sell if takers buy from us)
            quote_size_usdc: USDC per order
            yes_fv_prev:    Fair value in previous tick
            yes_fv_curr:    Fair value in current tick

        Returns:
            (bid_fill_usdc, ask_fill_usdc)
        """
        delta = yes_fv_curr - yes_fv_prev   # Positive = price moved up

        # Base fill probability (both sides equally)
        base = self.bt_cfg.base_fill_prob_per_min
        vol_boost = abs(delta) * self.bt_cfg.vol_sensitivity
        vol_boost = min(vol_boost, 0.40)

        # Price up → takers BUY → hit our ask
        # Price down → takers SELL → hit our bid
        if delta > 0:
            ask_prob = min(base + vol_boost, self.bt_cfg.max_fill_prob_per_min)
            bid_prob = max(base - vol_boost * 0.5, 0.02)
        elif delta < 0:
            bid_prob = min(base + vol_boost, self.bt_cfg.max_fill_prob_per_min)
            ask_prob = max(base - vol_boost * 0.5, 0.02)
        else:
            bid_prob = base
            ask_prob = base

        bid_fill = 0.0
        ask_fill = 0.0

        if random.random() < bid_prob:
            fill_frac = self.bt_cfg.partial_fill_rate if random.random() < 0.15 else 1.0
            bid_fill = quote_size_usdc * fill_frac

        if random.random() < ask_prob:
            fill_frac = self.bt_cfg.partial_fill_rate if random.random() < 0.15 else 1.0
            ask_fill = quote_size_usdc * fill_frac

        return bid_fill, ask_fill

    # ------------------------------------------------------------------
    # Adverse selection (per fill)
    # ------------------------------------------------------------------

    @staticmethod
    def _adverse_selection(
        direction: str,
        fill_price: float,
        next_fv: float,
        size_usdc: float,
    ) -> float:
        """
        Estimate adverse selection loss from a single fill.

        If we BOUGHT (direction="buy") and the next-tick price is LOWER,
        informed flow knew the price was going to fall.
        Adverse loss = (fill_price − next_fv) × size_usdc  [if positive]

        If we SOLD and next tick is HIGHER, similar logic.
        """
        if direction == "buy":
            loss = max(0.0, fill_price - next_fv) * size_usdc
        else:
            loss = max(0.0, next_fv - fill_price) * size_usdc
        return loss

    # ------------------------------------------------------------------
    # Inventory settlement
    # ------------------------------------------------------------------

    @staticmethod
    def _settlement_pnl(
        inventory: InventoryManager,
        yes_won: bool,
    ) -> float:
        """
        Compute P&L from settling remaining inventory at binary outcome.

        Yes tokens pay $1 if yes_won, $0 if not.
        No tokens pay $1 if not yes_won, $0 if yes_won.

        We approximate entry price from the token probability at the time
        of fill; here we use the average of all fills as tracked in the
        inventory USDC value (which equals tokens × avg_fill_price).

        settlement_pnl = payout − cost_basis
        """
        yes_tokens = inventory.state.yes_tokens
        no_tokens = inventory.state.no_tokens
        yes_cost = inventory.state.yes_inventory_usdc
        no_cost = inventory.state.no_inventory_usdc

        if yes_won:
            payout = yes_tokens * 1.0 + no_tokens * 0.0
        else:
            payout = yes_tokens * 0.0 + no_tokens * 1.0

        return round(payout - yes_cost - no_cost, 6)

    # ------------------------------------------------------------------
    # Single-window simulation
    # ------------------------------------------------------------------

    def run_window(self, klines: list[HistoricalKline]) -> Optional[WindowResult]:
        """
        Simulate the maker strategy on one 15-minute market window.

        Args:
            klines: 1-minute klines covering (approximately) 15 minutes.

        Returns:
            WindowResult, or None if data is insufficient.
        """
        if len(klines) < 3:
            return None

        btc_open = klines[0].open
        btc_close = klines[-1].close
        start_ts = klines[0].timestamp
        total_s = (klines[-1].timestamp - start_ts) / 1000.0
        if total_s <= 0:
            total_s = 900.0

        yes_won = btc_close > btc_open
        result = WindowResult(
            window_start_ts=start_ts,
            btc_open=btc_open,
            btc_close=btc_close,
            yes_won=yes_won,
        )

        # Per-window components that reset each market
        inventory = InventoryManager(self.config.inventory)
        rebate_tracker = RebateTracker(self.config.rebate)

        # Derive the per-tick token fair value series
        fv_series: list[float] = []
        for k in klines:
            elapsed_s = (k.timestamp - start_ts) / 1000.0
            fv = self.derive_token_price(k.close, btc_open, elapsed_s, total_s)
            fv_series.append(fv)

        # --- Main tick loop ---
        for i, kline in enumerate(klines):
            elapsed_s = (kline.timestamp - start_ts) / 1000.0
            remaining_s = total_s - elapsed_s

            # Timing guards
            if elapsed_s < self.config.quote.no_quote_first_seconds:
                continue
            if remaining_s < self.config.quote.no_quote_last_seconds:
                break

            yes_fv = fv_series[i]
            no_fv = 1.0 - yes_fv

            # Previous tick's fair value (for fill direction)
            prev_yes_fv = fv_series[i - 1] if i > 0 else yes_fv

            # Next tick's fair value (for adverse selection estimation)
            next_yes_fv = fv_series[i + 1] if i + 1 < len(fv_series) else yes_fv

            # Quote generation (use config half-spread + skew)
            yes_skew = self._compute_skew(inventory, "yes")
            no_skew = self._compute_skew(inventory, "no")

            yes_bid = round(yes_fv + yes_skew - self.config.quote.default_half_spread, 3)
            yes_ask = round(yes_fv + yes_skew + self.config.quote.default_half_spread, 3)
            no_bid  = round(no_fv  + no_skew  - self.config.quote.default_half_spread, 3)
            no_ask  = round(no_fv  + no_skew  + self.config.quote.default_half_spread, 3)

            yes_bid = max(0.01, min(0.97, yes_bid))
            yes_ask = max(0.02, min(0.99, yes_ask))
            no_bid  = max(0.01, min(0.97, no_bid))
            no_ask  = max(0.02, min(0.99, no_ask))

            q_size = self.config.quote.quote_size_usdc

            # --- Simulate fills for Yes side ---
            yes_bid_fill, yes_ask_fill = self._simulate_fills(
                yes_bid, yes_ask, q_size, prev_yes_fv, yes_fv
            )
            self._process_fill(
                result, inventory, rebate_tracker,
                "yes", "buy", yes_bid, yes_bid_fill, yes_fv, next_yes_fv, elapsed_s,
            )
            self._process_fill(
                result, inventory, rebate_tracker,
                "yes", "sell", yes_ask, yes_ask_fill, yes_fv, next_yes_fv, elapsed_s,
            )

            # --- Simulate fills for No side ---
            no_bid_fill, no_ask_fill = self._simulate_fills(
                no_bid, no_ask, q_size, 1.0 - prev_yes_fv, no_fv
            )
            self._process_fill(
                result, inventory, rebate_tracker,
                "no", "buy", no_bid, no_bid_fill, no_fv, 1.0 - next_yes_fv, elapsed_s,
            )
            self._process_fill(
                result, inventory, rebate_tracker,
                "no", "sell", no_ask, no_ask_fill, no_fv, 1.0 - next_yes_fv, elapsed_s,
            )

            # --- Micro-arb check (rare but realistic) ---
            combined_ask = yes_ask + no_ask
            if combined_ask < self.config.arb.threshold and random.random() < 0.3:
                arb_profit = (1.0 - combined_ask) * self.config.arb.size_usdc
                if arb_profit >= self.config.arb.min_profit_usdc:
                    result.arb_profit += arb_profit
                    result.arb_executions += 1
                    rebate_tracker.record_arb_profit(arb_profit, self.config.arb.size_usdc)

        # --- Settlement ---
        result.settlement_pnl = self._settlement_pnl(inventory, yes_won)

        # --- Aggregate from rebate tracker ---
        stats = rebate_tracker.stats
        result.spread_income = stats.spread_income_usdc
        result.rebate_income = stats.rebate_income_usdc
        result.adverse_selection_loss = stats.adverse_selection_usdc

        return result

    def _process_fill(
        self,
        result: WindowResult,
        inventory: InventoryManager,
        rebate_tracker: RebateTracker,
        side: str,
        direction: str,
        price: float,
        size_usdc: float,
        fv: float,
        next_fv: float,
        elapsed_s: float,
    ) -> None:
        """Record a single fill in inventory, rebate tracker, and result."""
        if size_usdc <= 0:
            return

        # Check inventory limits before recording
        if direction == "buy":
            ok, _ = inventory.can_accept_fill(side, size_usdc)
            if not ok:
                return

        inventory.record_fill(side, direction, size_usdc, price)

        fill_rec = rebate_tracker.record_maker_fill(
            fill_id=str(uuid.uuid4())[:8],
            market_slug="backtest",
            token_id=f"{side}_token",
            side=side,
            direction=direction,
            price=price,
            size_usdc=size_usdc,
            fair_value=fv,
        )

        # Compute per-fill adverse selection using next tick's price
        adverse = self._adverse_selection(direction, price, next_fv, size_usdc)

        result.fills.append(WindowFill(
            side=side,
            direction=direction,
            price=price,
            size_usdc=size_usdc,
            fair_value=fv,
            elapsed_s=elapsed_s,
            spread_captured=fill_rec.spread_captured,
            adverse_selection=adverse,
            estimated_rebate=fill_rec.estimated_rebate,
        ))

    def _compute_skew(self, inventory: InventoryManager, side: str) -> float:
        """
        Inventory skew adjustment for quote prices.
        Mirrors PricingEngine.compute_skew but avoids requiring a live book.
        """
        factor = self.config.quote.skew_factor
        max_sk = self.config.quote.max_skew
        net = inventory.yes_inventory - inventory.no_inventory

        skew = -net * factor if side == "yes" else net * factor
        return max(-max_sk, min(max_sk, skew))

    # ------------------------------------------------------------------
    # Full backtest over multiple windows
    # ------------------------------------------------------------------

    def run(
        self,
        klines: list[HistoricalKline],
        window_minutes: int = 15,
    ) -> MakerBacktestResult:
        """
        Run the full backtest on a list of 1-minute klines.

        Args:
            klines:          Chronological list of 1-minute BTCUSDT klines.
            window_minutes:  Size of each market window in minutes (default 15).

        Returns:
            MakerBacktestResult with all statistics computed.
        """
        if not klines:
            logger.error("No klines provided to backtest")
            return MakerBacktestResult()

        logger.info(
            "Starting maker backtest: %d klines, %.1f days of data",
            len(klines),
            len(klines) / (60 * 24),
        )

        # Slice klines into non-overlapping windows
        tick_per_window = window_minutes
        windows = [
            klines[i: i + tick_per_window]
            for i in range(0, len(klines) - tick_per_window + 1, tick_per_window)
        ]

        logger.info("Simulating %d market windows...", len(windows))
        result = MakerBacktestResult(
            config_used=self.config,
            bt_config_used=self.bt_cfg,
        )

        for idx, window in enumerate(windows):
            w_result = self.run_window(window)
            if w_result:
                result.windows.append(w_result)

            if (idx + 1) % 100 == 0:
                logger.info(
                    "  Simulated %d / %d windows...", idx + 1, len(windows)
                )

        result.compute()
        logger.info(result.summary())
        return result
