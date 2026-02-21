"""
Rebate Tracker: Records every maker fill, estimates income from
spread capture and maker rebates, and tracks the full P&L breakdown.

Income breakdown (per fill):
  1. Spread capture  – we traded at a better price than fair value.
  2. Maker rebate    – Polymarket distributes taker fees to makers.
                       Estimated conservatively at 0.5% of traded USDC.
  3. Micro-arb       – profit from Yes+No < $1 opportunities.

Costs accounted for:
  Adverse selection  – a proportion of fills where takers likely
                       had superior information at fill time.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from polymarket_btc_bot.maker.config import MakerRebateConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class MakerFill:
    """Immutable record of a single maker fill."""

    fill_id: str
    market_slug: str
    token_id: str
    side: str               # "yes" or "no"
    direction: str          # "buy" | "sell"
    price: float
    size_usdc: float
    fill_time: float
    fair_value: float       # Microprice at fill time (for adverse-selection calc)
    spread_captured: float  # Estimated income from this fill
    estimated_rebate: float # Conservative rebate estimate for this fill
    adverse_selection: float  # Estimated loss to informed counterparty


@dataclass
class MakerPnLStats:
    """Cumulative P&L breakdown for the current session."""

    total_fills: int = 0
    buy_fills: int = 0
    sell_fills: int = 0
    arb_trades: int = 0

    total_volume_usdc: float = 0.0

    # --- Income ---
    spread_income_usdc: float = 0.0
    rebate_income_usdc: float = 0.0
    arb_income_usdc: float = 0.0

    # --- Estimated costs ---
    adverse_selection_usdc: float = 0.0

    @property
    def gross_income_usdc(self) -> float:
        return self.spread_income_usdc + self.rebate_income_usdc + self.arb_income_usdc

    @property
    def net_pnl_usdc(self) -> float:
        return self.gross_income_usdc - self.adverse_selection_usdc

    @property
    def rebate_yield(self) -> float:
        """Rebate income as a fraction of traded USDC volume."""
        if self.total_volume_usdc <= 0:
            return 0.0
        return self.rebate_income_usdc / self.total_volume_usdc

    @property
    def spread_yield(self) -> float:
        """Spread income as a fraction of traded USDC volume."""
        if self.total_volume_usdc <= 0:
            return 0.0
        return self.spread_income_usdc / self.total_volume_usdc


# ---------------------------------------------------------------------------
# Rebate Tracker
# ---------------------------------------------------------------------------

class RebateTracker:
    """
    Records all maker fills and estimates running P&L.

    Design notes:
      - Spread capture is estimated per-fill from (fill_price − fair_value).
        It is an approximation: real round-trip spread income is only fully
        realised when the opposing side is also filled.
      - Rebate income uses a flat conservative rate; real amounts depend on
        your share of total maker volume in the pool.
      - Adverse selection is estimated as a fraction of the spread earned.
        In practice it should be measured empirically.
    """

    # Fraction of spread income estimated to be adverse-selection loss.
    # Studies suggest 30–60% of MM spread income is lost to informed flow.
    _ADVERSE_SEL_FRACTION = 0.35

    def __init__(self, config: MakerRebateConfig):
        self._cfg = config
        self._stats = MakerPnLStats()
        self._fills: list[MakerFill] = []
        self._session_start = time.time()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def stats(self) -> MakerPnLStats:
        return self._stats

    # ------------------------------------------------------------------
    # Recording fills
    # ------------------------------------------------------------------

    def record_maker_fill(
        self,
        fill_id: str,
        market_slug: str,
        token_id: str,
        side: str,
        direction: str,
        price: float,
        size_usdc: float,
        fair_value: float,
    ) -> MakerFill:
        """
        Record a maker fill and update all running totals.

        Args:
            fill_id:     Unique identifier (usually order_id).
            market_slug: Human-readable market name.
            token_id:    Token traded.
            side:        "yes" or "no".
            direction:   "buy" (bid lifted) or "sell" (ask hit).
            price:       Actual fill price.
            size_usdc:   USDC value of the fill.
            fair_value:  Microprice at fill time.

        Returns:
            MakerFill record.
        """
        spread_cap = self._estimate_spread_capture(direction, price, fair_value, size_usdc)
        rebate = size_usdc * self._cfg.estimated_rebate_rate
        adverse = spread_cap * self._ADVERSE_SEL_FRACTION

        fill = MakerFill(
            fill_id=fill_id,
            market_slug=market_slug,
            token_id=token_id,
            side=side,
            direction=direction,
            price=price,
            size_usdc=size_usdc,
            fill_time=time.time(),
            fair_value=fair_value,
            spread_captured=spread_cap,
            estimated_rebate=rebate,
            adverse_selection=adverse,
        )
        self._fills.append(fill)

        # Update aggregates
        self._stats.total_fills += 1
        self._stats.total_volume_usdc += size_usdc
        self._stats.spread_income_usdc += spread_cap
        self._stats.rebate_income_usdc += rebate
        self._stats.adverse_selection_usdc += adverse

        if direction == "buy":
            self._stats.buy_fills += 1
        else:
            self._stats.sell_fills += 1

        logger.info(
            "Maker fill [%s]: %s %s $%.2f @ %.4f "
            "| spread=$%.4f rebate=$%.4f adverse=$%.4f | net_pnl=$%.4f",
            fill_id[:8],
            direction.upper(), side.upper(), size_usdc, price,
            spread_cap, rebate, adverse,
            self._stats.net_pnl_usdc,
        )
        return fill

    def record_arb_profit(self, profit_usdc: float, size_usdc: float) -> None:
        """Record realised profit from a micro-arb trade (both legs)."""
        self._stats.arb_income_usdc += profit_usdc
        self._stats.total_volume_usdc += size_usdc * 2  # Count both legs
        self._stats.arb_trades += 1
        logger.info("Arb profit recorded: $%.4f on $%.2f volume", profit_usdc, size_usdc)

    # ------------------------------------------------------------------
    # Internal estimation helpers
    # ------------------------------------------------------------------

    def _estimate_spread_capture(
        self,
        direction: str,
        fill_price: float,
        fair_value: float,
        size_usdc: float,
    ) -> float:
        """
        Estimate the spread income from a single maker fill.

        When our bid is lifted (direction="buy"):
          A taker sold to us at our bid.  Our bid < fair_value, so we
          bought at a discount: income ≈ (fair_value − fill_price) × volume.

        When our ask is hit (direction="sell"):
          A taker bought from us at our ask.  Our ask > fair_value:
          income ≈ (fill_price − fair_value) × volume.
        """
        if direction == "buy":
            half_spread = max(0.0, fair_value - fill_price)
        else:
            half_spread = max(0.0, fill_price - fair_value)
        return round(half_spread * size_usdc, 6)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_session_stats(self) -> dict:
        """Return a dictionary suitable for logging or dashboard display."""
        runtime = time.time() - self._session_start
        stats = self._stats
        return {
            "session_runtime_s": round(runtime, 1),
            "total_fills": stats.total_fills,
            "buy_fills": stats.buy_fills,
            "sell_fills": stats.sell_fills,
            "arb_trades": stats.arb_trades,
            "total_volume_usdc": round(stats.total_volume_usdc, 4),
            "spread_income_usdc": round(stats.spread_income_usdc, 4),
            "rebate_income_usdc": round(stats.rebate_income_usdc, 4),
            "arb_income_usdc": round(stats.arb_income_usdc, 4),
            "gross_income_usdc": round(stats.gross_income_usdc, 4),
            "adverse_selection_usdc": round(stats.adverse_selection_usdc, 4),
            "net_pnl_usdc": round(stats.net_pnl_usdc, 4),
            "spread_yield_pct": round(stats.spread_yield * 100, 4),
            "rebate_yield_pct": round(stats.rebate_yield * 100, 4),
            "fills_per_hour": round(stats.total_fills / (runtime / 3600), 2) if runtime > 60 else 0.0,
        }

    def get_recent_fills(self, n: int = 10) -> list[MakerFill]:
        """Return the last N fills."""
        return self._fills[-n:]
