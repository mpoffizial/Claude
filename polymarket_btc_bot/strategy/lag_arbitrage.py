"""
Layer 1: Chainlink Lag Arbitrage Strategy

Primary strategy exploiting the 30-90 second delay between BTC spot price
movements on Binance and the Polymarket orderbook reaction.

When BTC breaks strongly in one direction on Binance but the Polymarket
orderbook hasn't adjusted yet, the corresponding outcome tokens are mispriced.

For 5-minute markets (300s), the lag window is proportionally larger relative
to total market duration, making this strategy especially effective.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from polymarket_btc_bot.config import StrategyConfig, RiskConfig
from polymarket_btc_bot.data.binance_feed import BinanceFeed, MomentumData
from polymarket_btc_bot.data.polymarket_clob import MarketOrderbook
from polymarket_btc_bot.execution.fee_calculator import FeeCalculator

logger = logging.getLogger(__name__)


class SignalDirection(Enum):
    UP = "up"
    DOWN = "down"
    NONE = "none"


@dataclass
class LagSignal:
    direction: SignalDirection
    confidence: float           # 0.0 to 1.0
    momentum_score: float       # Raw momentum value
    target_token: str           # "up" or "down"
    ask_price: float            # Current ask price for the target
    expected_edge: float        # Expected edge after fee (conditional on win)
    break_even_probability: float = 0.0  # Min win-rate needed to be profitable
    timestamp: float = 0.0
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        return self.direction != SignalDirection.NONE and self.confidence > 0


class LagArbitrage:
    """
    Detects and generates trading signals based on the lag between
    Binance spot price movements and Polymarket orderbook prices.
    """

    def __init__(self, strategy_config: StrategyConfig, risk_config: RiskConfig):
        self.config = strategy_config
        self.risk = risk_config
        self._last_signal_time: float = 0.0
        self._signal_cooldown: float = 10.0  # Min seconds between signals
        self._signal_count: int = 0
        self._fees = FeeCalculator(
            market_type=risk_config.market_type,
            gas_cost=risk_config.gas_cost_usdc,
        )

    def evaluate(
        self,
        momentum: MomentumData,
        orderbook: MarketOrderbook,
        time_remaining: float,
    ) -> LagSignal:
        """
        Evaluate current market conditions and generate a trading signal.

        Args:
            momentum: Current momentum data from Binance feed
            orderbook: Current orderbook state from Polymarket
            time_remaining: Seconds until market closes

        Returns:
            LagSignal with direction and confidence
        """
        no_signal = LagSignal(
            direction=SignalDirection.NONE,
            confidence=0.0,
            momentum_score=momentum.momentum_score,
            target_token="none",
            ask_price=0.0,
            expected_edge=0.0,
            timestamp=time.time(),
            reason="",
        )

        # Check timing constraints
        if time_remaining < self.risk.no_trade_last_seconds:
            no_signal.reason = f"Too close to market close ({time_remaining:.0f}s remaining)"
            return no_signal

        market_duration = self.config.market_duration_seconds  # 300 or 900
        time_elapsed = market_duration - time_remaining
        if time_elapsed < self.risk.no_trade_first_seconds:
            no_signal.reason = f"Too early in market ({time_elapsed:.0f}s elapsed)"
            return no_signal

        # Check signal cooldown
        now = time.time()
        if now - self._last_signal_time < self._signal_cooldown:
            no_signal.reason = "Signal cooldown active"
            return no_signal

        # Check momentum threshold
        abs_momentum = abs(momentum.momentum_score)
        if abs_momentum < self.config.momentum_threshold:
            no_signal.reason = (
                f"Momentum too weak: {momentum.momentum_score:.5f} "
                f"(threshold: {self.config.momentum_threshold})"
            )
            return no_signal

        # Determine direction
        if momentum.momentum_score > self.config.momentum_threshold:
            direction = SignalDirection.UP
            ask_price = orderbook.up_ask
            target = "up"
        elif momentum.momentum_score < -self.config.momentum_threshold:
            direction = SignalDirection.DOWN
            ask_price = orderbook.down_ask
            target = "down"
        else:
            no_signal.reason = "No clear momentum direction"
            return no_signal

        if ask_price is None:
            no_signal.reason = f"No ask price available for {target}"
            return no_signal

        # Check ask price threshold
        if ask_price > self.config.max_ask_price_lag:
            no_signal.reason = (
                f"{target} ask price {ask_price:.3f} > max {self.config.max_ask_price_lag}"
            )
            return no_signal

        # Exakte Fee-Berechnung via FeeCalculator
        # Break-Even: minimale Win-Prob fuer Profitabilitaet
        # Edge: (1 - winner_fee - gas_per_token) / ask - 1
        be_prob = self._fees.break_even_probability(ask_price)
        payout = 1.0 - self._fees.taker_fee_rate(ask_price)
        expected_edge = (payout - ask_price) / ask_price

        if expected_edge < self.risk.min_edge_threshold:
            no_signal.reason = (
                f"Edge {expected_edge:.4f} below minimum {self.risk.min_edge_threshold}"
            )
            return no_signal

        # Also check minimum edge ratio from strategy config
        edge_ratio = (1.0 - ask_price) / ask_price
        if edge_ratio < self.config.min_edge_ratio:
            no_signal.reason = (
                f"Edge ratio {edge_ratio:.4f} below minimum {self.config.min_edge_ratio}"
            )
            return no_signal

        # Calculate confidence based on momentum strength and edge
        momentum_confidence = min(1.0, abs_momentum / (self.config.momentum_threshold * 3))
        edge_confidence = min(1.0, expected_edge / 0.10)
        confidence = (momentum_confidence * 0.6 + edge_confidence * 0.4)

        self._last_signal_time = now
        self._signal_count += 1

        signal = LagSignal(
            direction=direction,
            confidence=confidence,
            momentum_score=momentum.momentum_score,
            target_token=target,
            ask_price=ask_price,
            expected_edge=expected_edge,
            break_even_probability=be_prob,
            timestamp=now,
            reason=(
                f"Lag arb: momentum={momentum.momentum_score:+.5f}, "
                f"ask={ask_price:.3f}, edge={expected_edge:.4f}, "
                f"break_even={be_prob:.1%}"
            ),
        )

        logger.info(
            "LAG SIGNAL: %s | conf=%.2f | momentum=%+.5f | ask=%.3f | "
            "edge=%.4f | break_even=%.1%% | taker_fee=%.3f%% | gas=$%.3f",
            direction.value.upper(),
            confidence,
            momentum.momentum_score,
            ask_price,
            expected_edge,
            be_prob * 100,
            self._fees.taker_fee_rate(ask_price) * 100,
            self.risk.gas_cost_usdc,
        )

        return signal

    def compute_position_size(
        self,
        signal: LagSignal,
        available_balance: float,
        max_position: float,
    ) -> float:
        """Calculate optimal position size based on signal confidence."""
        if not signal.is_valid:
            return 0.0

        # Kelly-inspired sizing: bet proportional to edge and confidence
        # f = (bp - q) / b where b = odds, p = prob of winning, q = prob of losing
        # Simplified: scale by confidence * edge
        base_size = min(available_balance, max_position)
        size = base_size * signal.confidence * min(1.0, signal.expected_edge / 0.05)

        # Apply floor and ceiling
        size = max(1.0, min(size, max_position))
        return round(size, 2)

    def reset(self):
        """Reset signal state for new market."""
        self._last_signal_time = 0.0
        self._signal_count = 0
