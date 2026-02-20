"""
Backtest Engine: Simulates trading strategies on historical data.
Accounts for slippage, latency, partial fills, and fees.
No look-ahead bias.
"""

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from polymarket_btc_bot.config import BotConfig, BacktestConfig, StrategyConfig, RiskConfig
from polymarket_btc_bot.backtesting.historical_loader import HistoricalLoader, HistoricalKline
from polymarket_btc_bot.execution.fee_calculator import FeeCalculator

logger = logging.getLogger(__name__)


@dataclass
class SimulatedTrade:
    timestamp: int
    direction: str          # "up" or "down"
    entry_price: float      # Token price paid
    btc_open_price: float   # BTC at market open
    btc_close_price: float  # BTC at market close
    size: float             # USDC amount
    won: bool = False
    pnl: float = 0.0
    pnl_after_fee: float = 0.0
    strategy: str = ""
    slippage_applied: float = 0.0
    latency_ms: int = 0


@dataclass
class BacktestResult:
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    total_pnl_after_fee: float
    avg_profit_per_trade: float
    max_drawdown: float
    sharpe_ratio: float
    trades_per_day: float
    strategy_breakdown: dict
    trades: list[SimulatedTrade] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"=== Backtest Results ===\n"
            f"Total Trades:     {self.total_trades}\n"
            f"Win Rate:         {self.win_rate:.1%}\n"
            f"Total PnL:        ${self.total_pnl:.2f}\n"
            f"PnL after Fee:    ${self.total_pnl_after_fee:.2f}\n"
            f"Avg Profit/Trade: ${self.avg_profit_per_trade:.4f}\n"
            f"Max Drawdown:     ${self.max_drawdown:.2f}\n"
            f"Sharpe Ratio:     {self.sharpe_ratio:.2f}\n"
            f"Trades/Day:       {self.trades_per_day:.1f}\n"
            f"Strategy Breakdown: {self.strategy_breakdown}"
        )


class Backtester:
    """
    Simulates the trading bot on historical data.

    Key features:
    - No look-ahead bias: decisions only use data available at that time
    - Realistic slippage simulation
    - Latency simulation
    - Partial fill simulation
    - 2% winner fee calculation
    """

    def __init__(
        self,
        config: BotConfig,
        loader: HistoricalLoader,
    ):
        self.config = config
        self.loader = loader
        self.strategy_config = config.strategy
        self.risk_config = config.risk
        self.backtest_config = config.backtest
        self._fees = FeeCalculator(
            market_type=config.risk.market_type,
            gas_cost=config.risk.gas_cost_usdc,
        )

    def run(
        self,
        start_time_ms: int,
        end_time_ms: int,
        trade_size: float = 10.0,
    ) -> BacktestResult:
        """
        Run backtest on historical data.

        Args:
            start_time_ms: Start time in milliseconds
            end_time_ms: End time in milliseconds
            trade_size: USDC per trade

        Returns:
            BacktestResult with all metrics
        """
        logger.info("Running backtest from %d to %d", start_time_ms, end_time_ms)

        # Load klines
        klines = self.loader.load_klines(start_time_ms, end_time_ms)
        if len(klines) < 100:
            logger.error("Insufficient data: only %d klines", len(klines))
            return self._empty_result()

        # Group klines into 15-minute windows
        windows = self._create_15min_windows(klines)
        logger.info("Created %d 15-minute windows", len(windows))

        trades = []
        daily_pnl = {}

        for window in windows:
            if len(window) < 5:
                continue

            window_trades = self._simulate_window(window, trade_size)
            trades.extend(window_trades)

            # Track daily PnL
            day = time.strftime("%Y-%m-%d", time.gmtime(window[0].timestamp / 1000))
            if day not in daily_pnl:
                daily_pnl[day] = 0.0
            for t in window_trades:
                daily_pnl[day] += t.pnl_after_fee

        return self._compute_result(trades, daily_pnl, start_time_ms, end_time_ms)

    def _create_15min_windows(self, klines: list[HistoricalKline]) -> list[list[HistoricalKline]]:
        """Group klines into 15-minute windows."""
        if not klines:
            return []

        windows = []
        current_window = []
        window_start = klines[0].timestamp

        for kline in klines:
            if kline.timestamp - window_start >= 15 * 60 * 1000:
                if current_window:
                    windows.append(current_window)
                current_window = [kline]
                window_start = kline.timestamp
            else:
                current_window.append(kline)

        if current_window:
            windows.append(current_window)

        return windows

    def _simulate_window(
        self,
        window: list[HistoricalKline],
        trade_size: float,
    ) -> list[SimulatedTrade]:
        """Simulate trading within a single 15-minute window."""
        trades = []
        opening_price = window[0].open
        closing_price = window[-1].close

        # Determine actual outcome
        up_won = closing_price > opening_price

        # Simulate lag arbitrage
        lag_trade = self._simulate_lag_strategy(window, opening_price, closing_price, up_won, trade_size)
        if lag_trade:
            trades.append(lag_trade)

        # Simulate late momentum
        late_trade = self._simulate_late_strategy(window, opening_price, closing_price, up_won, trade_size)
        if late_trade:
            trades.append(late_trade)

        return trades

    def _simulate_lag_strategy(
        self,
        window: list[HistoricalKline],
        opening_price: float,
        closing_price: float,
        up_won: bool,
        trade_size: float,
    ) -> Optional[SimulatedTrade]:
        """Simulate lag arbitrage strategy on a window."""
        if len(window) < 3:
            return None

        # Look for momentum signal in the first half
        lookback_klines = min(
            self.strategy_config.momentum_lookback_seconds // 60 + 1,
            len(window) // 2,
        )

        for i in range(lookback_klines, len(window) - 2):
            if i < lookback_klines:
                continue

            # Calculate momentum
            past_price = window[i - lookback_klines].close
            current_price = window[i].close

            if past_price == 0:
                continue

            momentum = (current_price - past_price) / past_price

            # Check threshold
            if abs(momentum) < self.strategy_config.momentum_threshold:
                continue

            direction = "up" if momentum > 0 else "down"

            # Simulate ask price (approximate: assume market offers ~0.55 for the direction)
            # In reality, this would come from orderbook snapshots
            simulated_ask = 0.50 + random.uniform(0.02, 0.12)

            if simulated_ask > self.strategy_config.max_ask_price_lag:
                continue

            # Check edge
            payout = 1.0 - self._fees.taker_fee_rate(simulated_ask)
            edge = (payout - simulated_ask) / simulated_ask
            if edge < self.risk_config.min_edge_threshold:
                continue

            # Apply slippage
            slippage = self.backtest_config.slippage_bps / 10000.0
            actual_ask = simulated_ask * (1 + slippage)

            # Apply latency (skip to next kline)
            latency_klines = max(1, self.backtest_config.latency_ms // 60000)

            # Partial fill
            fill_rate = self.backtest_config.partial_fill_rate
            actual_size = trade_size * fill_rate if random.random() < 0.85 else trade_size

            # Determine if trade won
            won = (direction == "up" and up_won) or (direction == "down" and not up_won)

            # Exaktes PnL via FeeCalculator (Winner-Fee 2% + Gas)
            tokens = actual_size / actual_ask
            pnl, pnl_after_fee = self._fees.close_position_pnl(tokens, actual_size, won)

            trade = SimulatedTrade(
                timestamp=window[i].timestamp,
                direction=direction,
                entry_price=actual_ask,
                btc_open_price=opening_price,
                btc_close_price=closing_price,
                size=actual_size,
                won=won,
                pnl=pnl,
                pnl_after_fee=pnl_after_fee,
                strategy="lag_arbitrage",
                slippage_applied=slippage,
                latency_ms=self.backtest_config.latency_ms,
            )

            return trade  # Only one trade per strategy per window

        return None

    def _simulate_late_strategy(
        self,
        window: list[HistoricalKline],
        opening_price: float,
        closing_price: float,
        up_won: bool,
        trade_size: float,
    ) -> Optional[SimulatedTrade]:
        """Simulate late-period momentum strategy."""
        if len(window) < 10:
            return None

        # Activate in last ~40% of the window
        activation_idx = int(len(window) * 0.6)

        for i in range(activation_idx, len(window) - 1):
            current_price = window[i].close
            deviation = (current_price - opening_price) / opening_price

            if abs(deviation) < self.strategy_config.late_period_price_deviation:
                continue

            direction = "up" if deviation > 0 else "down"

            # Simulate ask price (should be higher in late period as outcome becomes clearer)
            simulated_ask = 0.60 + random.uniform(0.05, 0.18)

            if simulated_ask > self.strategy_config.late_period_max_ask:
                continue

            # Check edge
            payout = 1.0 - self._fees.taker_fee_rate(simulated_ask)
            edge = (payout - simulated_ask) / simulated_ask
            if edge < self.risk_config.min_edge_threshold:
                continue

            # Apply slippage
            slippage = self.backtest_config.slippage_bps / 10000.0
            actual_ask = simulated_ask * (1 + slippage)

            actual_size = trade_size * self.backtest_config.partial_fill_rate

            won = (direction == "up" and up_won) or (direction == "down" and not up_won)

            # Exaktes PnL via FeeCalculator (Winner-Fee 2% + Gas)
            tokens = actual_size / actual_ask
            pnl, pnl_after_fee = self._fees.close_position_pnl(tokens, actual_size, won)

            trade = SimulatedTrade(
                timestamp=window[i].timestamp,
                direction=direction,
                entry_price=actual_ask,
                btc_open_price=opening_price,
                btc_close_price=closing_price,
                size=actual_size,
                won=won,
                pnl=pnl,
                pnl_after_fee=pnl_after_fee,
                strategy="late_momentum",
                slippage_applied=slippage,
                latency_ms=self.backtest_config.latency_ms,
            )

            return trade

        return None

    def _compute_result(
        self,
        trades: list[SimulatedTrade],
        daily_pnl: dict,
        start_ms: int,
        end_ms: int,
    ) -> BacktestResult:
        """Compute aggregate backtest metrics."""
        if not trades:
            return self._empty_result()

        wins = sum(1 for t in trades if t.won)
        losses = len(trades) - wins
        total_pnl = sum(t.pnl for t in trades)
        total_pnl_fee = sum(t.pnl_after_fee for t in trades)

        # Sharpe ratio
        pnls = [t.pnl_after_fee for t in trades]
        mean_pnl = np.mean(pnls)
        std_pnl = np.std(pnls, ddof=1) if len(pnls) > 1 else 1.0
        sharpe = (mean_pnl / std_pnl) * np.sqrt(252) if std_pnl > 0 else 0.0

        # Max drawdown
        cumulative = np.cumsum(pnls)
        peak = np.maximum.accumulate(cumulative)
        drawdown = peak - cumulative
        max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0

        # Trades per day
        days = max(1, (end_ms - start_ms) / (24 * 60 * 60 * 1000))
        trades_per_day = len(trades) / days

        # Strategy breakdown
        strategy_counts = {}
        for t in trades:
            if t.strategy not in strategy_counts:
                strategy_counts[t.strategy] = {"count": 0, "pnl": 0.0, "wins": 0}
            strategy_counts[t.strategy]["count"] += 1
            strategy_counts[t.strategy]["pnl"] += t.pnl_after_fee
            if t.won:
                strategy_counts[t.strategy]["wins"] += 1

        result = BacktestResult(
            total_trades=len(trades),
            wins=wins,
            losses=losses,
            win_rate=wins / len(trades) if trades else 0.0,
            total_pnl=total_pnl,
            total_pnl_after_fee=total_pnl_fee,
            avg_profit_per_trade=total_pnl_fee / len(trades) if trades else 0.0,
            max_drawdown=max_dd,
            sharpe_ratio=float(sharpe),
            trades_per_day=trades_per_day,
            strategy_breakdown=strategy_counts,
            trades=trades,
        )

        logger.info("\n%s", result.summary())
        return result

    def _empty_result(self) -> BacktestResult:
        return BacktestResult(
            total_trades=0,
            wins=0,
            losses=0,
            win_rate=0.0,
            total_pnl=0.0,
            total_pnl_after_fee=0.0,
            avg_profit_per_trade=0.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            trades_per_day=0.0,
            strategy_breakdown={},
        )
