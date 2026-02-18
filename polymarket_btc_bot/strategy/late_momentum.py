"""
Layer 3: Late-Period Momentum Strategy

Exploits mispricing in the final minutes of a 15-minute market.
When BTC has been clearly trending in one direction for most of
the period, the outcome becomes increasingly certain, but the
orderbook may still offer value.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from polymarket_btc_bot.config import StrategyConfig, RiskConfig
from polymarket_btc_bot.data.binance_feed import BinanceFeed
from polymarket_btc_bot.data.polymarket_clob import MarketOrderbook

logger = logging.getLogger(__name__)


@dataclass
class LateMomentumSignal:
    direction: str              # "up" or "down" or "none"
    confidence: float
    current_price: float
    opening_price: float
    price_deviation: float      # (current - opening) / opening
    ask_price: float
    expected_edge: float
    time_remaining: float
    timestamp: float
    reason: str

    @property
    def is_valid(self) -> bool:
        return self.direction != "none" and self.confidence > 0


class LateMomentum:
    """
    Generates signals in the last minutes of a 15m market when the
    outcome direction is becoming clear but the token is still mispriced.
    """

    def __init__(self, strategy_config: StrategyConfig, risk_config: RiskConfig):
        self.config = strategy_config
        self.risk = risk_config
        self._signal_count: int = 0
        self._last_signal_time: float = 0.0
        self._cooldown_seconds: float = 15.0

    def evaluate(
        self,
        current_btc_price: float,
        opening_btc_price: float,
        orderbook: MarketOrderbook,
        time_remaining: float,
    ) -> LateMomentumSignal:
        """
        Evaluate late-period conditions for a trading signal.

        Args:
            current_btc_price: Current BTC price from Binance
            opening_btc_price: BTC price at market open (from Chainlink)
            orderbook: Current Polymarket orderbook
            time_remaining: Seconds until market close

        Returns:
            LateMomentumSignal
        """
        no_signal = LateMomentumSignal(
            direction="none",
            confidence=0.0,
            current_price=current_btc_price,
            opening_price=opening_btc_price,
            price_deviation=0.0,
            ask_price=0.0,
            expected_edge=0.0,
            time_remaining=time_remaining,
            timestamp=time.time(),
            reason="",
        )

        # Only activate in the late period
        if time_remaining > self.config.late_period_activation_seconds:
            no_signal.reason = (
                f"Not in late period ({time_remaining:.0f}s > "
                f"{self.config.late_period_activation_seconds}s)"
            )
            return no_signal

        # Don't trade in the very last seconds
        if time_remaining < self.risk.no_trade_last_seconds:
            no_signal.reason = f"Too close to close ({time_remaining:.0f}s)"
            return no_signal

        # Check cooldown
        now = time.time()
        if now - self._last_signal_time < self._cooldown_seconds:
            no_signal.reason = "Signal cooldown"
            return no_signal

        if opening_btc_price <= 0:
            no_signal.reason = "Invalid opening price"
            return no_signal

        # Calculate price deviation
        deviation = (current_btc_price - opening_btc_price) / opening_btc_price

        # Determine direction and ask price
        if deviation > self.config.late_period_price_deviation:
            direction = "up"
            ask_price = orderbook.up_ask
        elif deviation < -self.config.late_period_price_deviation:
            direction = "down"
            ask_price = orderbook.down_ask
        else:
            no_signal.price_deviation = deviation
            no_signal.reason = (
                f"Price deviation {deviation:+.5f} within threshold "
                f"(+/-{self.config.late_period_price_deviation})"
            )
            return no_signal

        if ask_price is None:
            no_signal.reason = f"No ask price for {direction}"
            return no_signal

        # Check ask price is below threshold
        if ask_price > self.config.late_period_max_ask:
            no_signal.price_deviation = deviation
            no_signal.reason = (
                f"{direction} ask {ask_price:.3f} > max {self.config.late_period_max_ask}"
            )
            return no_signal

        # Calculate expected edge
        payout = 1.0 - self.risk.winner_fee
        expected_edge = (payout - ask_price) / ask_price

        if expected_edge < self.risk.min_edge_threshold:
            no_signal.price_deviation = deviation
            no_signal.reason = (
                f"Edge {expected_edge:.4f} below minimum {self.risk.min_edge_threshold}"
            )
            return no_signal

        # Confidence increases with:
        # 1. Larger price deviation (stronger trend)
        # 2. Less time remaining (more certain outcome)
        # 3. Better edge (cheaper price)
        deviation_confidence = min(1.0, abs(deviation) / (self.config.late_period_price_deviation * 5))
        time_confidence = min(1.0, (self.config.late_period_activation_seconds - time_remaining) /
                             self.config.late_period_activation_seconds)
        edge_confidence = min(1.0, expected_edge / 0.15)

        confidence = (
            deviation_confidence * 0.4
            + time_confidence * 0.35
            + edge_confidence * 0.25
        )

        self._last_signal_time = now
        self._signal_count += 1

        signal = LateMomentumSignal(
            direction=direction,
            confidence=confidence,
            current_price=current_btc_price,
            opening_price=opening_btc_price,
            price_deviation=deviation,
            ask_price=ask_price,
            expected_edge=expected_edge,
            time_remaining=time_remaining,
            timestamp=now,
            reason=(
                f"Late momentum: {direction} | dev={deviation:+.5f} | "
                f"ask={ask_price:.3f} | edge={expected_edge:.4f} | "
                f"time_left={time_remaining:.0f}s"
            ),
        )

        logger.info(
            "LATE SIGNAL: %s | confidence=%.2f | deviation=%+.5f | "
            "ask=%.3f | edge=%.4f | remaining=%.0fs",
            direction.upper(),
            confidence,
            deviation,
            ask_price,
            expected_edge,
            time_remaining,
        )

        return signal

    def compute_position_size(
        self,
        signal: LateMomentumSignal,
        available_balance: float,
        max_position: float,
    ) -> float:
        """Calculate position size for late momentum."""
        if not signal.is_valid:
            return 0.0

        base_size = min(available_balance, max_position)
        size = base_size * signal.confidence

        # Increase size as outcome becomes more certain (less time left)
        time_multiplier = 1.0 + (1.0 - signal.time_remaining / self.config.late_period_activation_seconds) * 0.5
        size *= min(time_multiplier, 1.5)

        return max(1.0, min(round(size, 2), max_position))

    def reset(self):
        """Reset state for new market."""
        self._signal_count = 0
        self._last_signal_time = 0.0
