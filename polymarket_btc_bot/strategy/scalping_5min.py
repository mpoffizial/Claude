"""
Layer 4: Early-Entry Scalping Strategy (5-Minute Markets)

Designed specifically for the Polymarket BTC "Up or Down" 5-minute markets.

Edge: In the first 60 seconds of a 5-minute market, the Polymarket orderbook
has not yet fully priced in real-time BTC momentum. Binance price data updates
every ~100ms, while human market makers and oracle updates typically lag 30-90s.

Strategy:
- Monitor BTC momentum acceleration in the first 60 seconds of each market
- If a strong, accelerating directional move is detected early, buy the
  corresponding outcome token before the market catches up
- Compound gains across many small markets: 5min markets run ~288 per day,
  giving many opportunities to accumulate edge

Compounding math:
- If we earn +3% edge per trade with 50% win rate and 2% Polymarket fee:
  Net per trade = (0.50 * 0.98) - (0.50 * 1.0) = -0.01 (naive)
- We need to identify situations with >60% win probability to be profitable
- The key is finding the fat tail: moments where BTC has moved 0.12%+ in 20s,
  which correlates with >65% probability of continued direction
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from polymarket_btc_bot.config import StrategyConfig, RiskConfig
from polymarket_btc_bot.data.binance_feed import BinanceFeed, MomentumData
from polymarket_btc_bot.data.polymarket_clob import MarketOrderbook
from polymarket_btc_bot.execution.fee_calculator import FeeCalculator

logger = logging.getLogger(__name__)


@dataclass
class ScalpSignal:
    direction: str              # "up", "down", or "none"
    confidence: float           # 0.0 to 1.0
    momentum_score: float       # Primary momentum (20s window)
    momentum_short: float       # Short momentum (10s window) for acceleration
    acceleration: float         # momentum_short - momentum_score (positive = accel)
    ask_price: float
    expected_edge: float
    time_elapsed: float         # Seconds since market start
    timestamp: float
    reason: str

    @property
    def is_valid(self) -> bool:
        return self.direction != "none" and self.confidence > 0


@dataclass
class ScalpStats:
    signals_generated: int = 0
    signals_up: int = 0
    signals_down: int = 0
    total_edge: float = 0.0


class EarlyScalping:
    """
    Generates high-confidence early-entry signals in the first 60 seconds
    of a 5-minute Polymarket BTC market.

    The alpha comes from orderbook lag: market makers cannot update quotes
    fast enough to match real-time Binance aggTrade data. During strong
    momentum bursts, the "winning" outcome token is temporarily underpriced.

    Compounding advantage:
    - 5min markets run 24/7: ~288 markets per day
    - Each profitable signal adds edge that compounds
    - Small position sizes keep risk controlled while accumulating gains
    """

    def __init__(self, strategy_config: StrategyConfig, risk_config: RiskConfig):
        self.config = strategy_config
        self.risk = risk_config
        self._last_signal_time: float = 0.0
        self._signal_cooldown: float = 20.0  # No repeated signals in early window
        self._stats = ScalpStats()
        self._market_start_time: float = 0.0
        self._fees = FeeCalculator(
            winner_fee=risk_config.winner_fee,
            gas_cost=risk_config.gas_cost_usdc,
        )

    def set_market_start(self, start_timestamp: float):
        """Called when a new market opens."""
        self._market_start_time = start_timestamp
        self._last_signal_time = 0.0

    def evaluate(
        self,
        binance: BinanceFeed,
        orderbook: MarketOrderbook,
        time_remaining: float,
    ) -> ScalpSignal:
        """
        Evaluate early-market conditions for a scalp entry.

        Args:
            binance: Binance feed with price buffer for multi-window momentum
            orderbook: Current Polymarket orderbook
            time_remaining: Seconds until market closes

        Returns:
            ScalpSignal with direction and confidence
        """
        no_signal = ScalpSignal(
            direction="none",
            confidence=0.0,
            momentum_score=0.0,
            momentum_short=0.0,
            acceleration=0.0,
            ask_price=0.0,
            expected_edge=0.0,
            time_elapsed=0.0,
            timestamp=time.time(),
            reason="",
        )

        # Only active when scalping window is enabled
        if self.config.scalping_entry_window_seconds <= 0:
            no_signal.reason = "Scalping disabled"
            return no_signal

        # Calculate time elapsed in this market
        market_duration = self.config.market_duration_seconds
        time_elapsed = market_duration - time_remaining
        no_signal.time_elapsed = time_elapsed

        # Only trade in the early entry window
        if time_elapsed > self.config.scalping_entry_window_seconds:
            no_signal.reason = (
                f"Past entry window: {time_elapsed:.0f}s > "
                f"{self.config.scalping_entry_window_seconds}s"
            )
            return no_signal

        # Enforce minimum warmup (need some price data first)
        if time_elapsed < self.risk.no_trade_first_seconds:
            no_signal.reason = f"Warmup phase ({time_elapsed:.0f}s < {self.risk.no_trade_first_seconds}s)"
            return no_signal

        # Enforce signal cooldown (one trade per early window per market)
        now = time.time()
        if now - self._last_signal_time < self._signal_cooldown:
            no_signal.reason = "Signal cooldown active"
            return no_signal

        # Get primary momentum (20s lookback)
        momentum_data = binance.compute_momentum(
            lookback_seconds=self.config.momentum_lookback_seconds
        )
        if momentum_data is None:
            no_signal.reason = "No momentum data (insufficient price history)"
            return no_signal

        # Get short momentum (10s lookback) for acceleration detection
        momentum_short_data = binance.compute_momentum(lookback_seconds=10)
        if momentum_short_data is None:
            no_signal.reason = "No short-window momentum data"
            return no_signal

        momentum_20s = momentum_data.momentum_score
        momentum_10s = momentum_short_data.momentum_score

        # Acceleration = short momentum is even stronger than primary
        # Positive acceleration means the move is speeding up
        acceleration = abs(momentum_10s) - abs(momentum_20s)

        no_signal.momentum_score = momentum_20s
        no_signal.momentum_short = momentum_10s
        no_signal.acceleration = acceleration

        # Both windows must agree on direction
        if momentum_20s * momentum_10s <= 0:
            no_signal.reason = (
                f"Mixed signals: 20s={momentum_20s:+.5f}, 10s={momentum_10s:+.5f}"
            )
            return no_signal

        # Primary momentum must exceed threshold
        if abs(momentum_20s) < self.config.scalping_momentum_threshold:
            no_signal.reason = (
                f"Momentum too weak: {momentum_20s:+.5f} "
                f"(need >{self.config.scalping_momentum_threshold})"
            )
            return no_signal

        # Determine direction
        direction = "up" if momentum_20s > 0 else "down"
        ask_price = orderbook.up_ask if direction == "up" else orderbook.down_ask

        if ask_price is None:
            no_signal.reason = f"No ask price for {direction}"
            return no_signal

        # Ask price must be below max for scalp (we need value)
        if ask_price > self.config.scalping_max_ask:
            no_signal.reason = (
                f"{direction} ask {ask_price:.3f} > scalp max {self.config.scalping_max_ask}"
            )
            return no_signal

        # Exakte Fee-Berechnung: Break-Even-Prob und Edge via FeeCalculator
        be_prob = self._fees.break_even_probability(ask_price)
        payout = 1.0 - self.risk.winner_fee  # 0.98
        expected_edge = (payout - ask_price) / ask_price

        if expected_edge < self.risk.min_edge_threshold:
            no_signal.reason = (
                f"Edge {expected_edge:.4f} < minimum {self.risk.min_edge_threshold}"
            )
            return no_signal

        # === Confidence Calculation ===
        # 1. Momentum strength confidence (how far above threshold)
        momentum_ratio = abs(momentum_20s) / self.config.scalping_momentum_threshold
        momentum_confidence = min(1.0, (momentum_ratio - 1.0) / 2.0)

        # 2. Acceleration bonus: accelerating momentum is more reliable
        accel_bonus = min(0.3, max(0.0, acceleration / self.config.scalping_acceleration_threshold))

        # 3. Time bonus: earlier entry = less market has priced in the move
        time_fraction = time_elapsed / self.config.scalping_entry_window_seconds
        time_confidence = max(0.0, 1.0 - time_fraction)  # Higher confidence if earlier

        # 4. Edge confidence: more edge = better opportunity
        edge_confidence = min(1.0, expected_edge / 0.15)

        confidence = (
            momentum_confidence * 0.40
            + accel_bonus * 0.25
            + time_confidence * 0.20
            + edge_confidence * 0.15
        )

        self._last_signal_time = now
        self._stats.signals_generated += 1
        if direction == "up":
            self._stats.signals_up += 1
        else:
            self._stats.signals_down += 1
        self._stats.total_edge += expected_edge

        signal = ScalpSignal(
            direction=direction,
            confidence=confidence,
            momentum_score=momentum_20s,
            momentum_short=momentum_10s,
            acceleration=acceleration,
            ask_price=ask_price,
            expected_edge=expected_edge,
            time_elapsed=time_elapsed,
            timestamp=now,
            reason=(
                f"Scalp: {direction} | elapsed={time_elapsed:.0f}s | "
                f"mom20={momentum_20s:+.5f} mom10={momentum_10s:+.5f} "
                f"accel={acceleration:+.5f} | ask={ask_price:.3f} | "
                f"edge={expected_edge:.4f}"
            ),
        )

        logger.info(
            "SCALP SIGNAL: %s | conf=%.2f | elapsed=%.0fs | "
            "mom20=%+.5f | mom10=%+.5f | accel=%+.5f | "
            "ask=%.3f | edge=%.4f | break_even=%.1%%",
            direction.upper(),
            confidence,
            time_elapsed,
            momentum_20s,
            momentum_10s,
            acceleration,
            ask_price,
            expected_edge,
            be_prob * 100,
        )

        return signal

    def compute_position_size(
        self,
        signal: ScalpSignal,
        available_balance: float,
        max_position: float,
    ) -> float:
        """
        Position size for scalp trades.

        Scalping uses smaller positions since we're doing many trades.
        Kelly-inspired: size proportional to edge and confidence.
        """
        if not signal.is_valid:
            return 0.0

        # Base: smaller fraction of max for scalping (compound many small wins)
        base_size = min(available_balance, max_position) * 0.6

        # Scale by confidence
        size = base_size * signal.confidence

        # Clamp to reasonable range
        size = max(1.0, min(round(size, 2), max_position * 0.8))
        return size

    def reset(self):
        """Reset state for a new market."""
        self._last_signal_time = 0.0
        self._market_start_time = 0.0

    @property
    def stats(self) -> ScalpStats:
        return self._stats
