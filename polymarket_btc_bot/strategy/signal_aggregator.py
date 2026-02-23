"""
Signal Aggregator: Combines signals from all strategy layers into
a unified trading decision with weighted confidence scoring.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from polymarket_btc_bot.config import StrategyConfig, RiskConfig
from polymarket_btc_bot.data.binance_feed import BinanceFeed, MomentumData
from polymarket_btc_bot.data.polymarket_clob import MarketOrderbook
from polymarket_btc_bot.strategy.lag_arbitrage import LagArbitrage, LagSignal, SignalDirection
from polymarket_btc_bot.strategy.intra_arbitrage import IntraArbitrage, ArbitrageSignal
from polymarket_btc_bot.strategy.late_momentum import LateMomentum, LateMomentumSignal

logger = logging.getLogger(__name__)


class TradeAction(Enum):
    BUY_UP = "buy_up"
    BUY_DOWN = "buy_down"
    BUY_BOTH = "buy_both"       # Arbitrage
    HOLD = "hold"


@dataclass
class AggregatedSignal:
    action: TradeAction
    confidence: float
    position_size: float
    target_token: str           # "up", "down", or "both"
    target_price: float         # Price to buy at
    expected_edge: float
    source_strategy: str        # Which strategy triggered this
    lag_signal: Optional[LagSignal] = None
    arb_signal: Optional[ArbitrageSignal] = None
    late_signal: Optional[LateMomentumSignal] = None
    timestamp: float = 0.0
    reason: str = ""

    @property
    def is_actionable(self) -> bool:
        # Individual strategies apply their own quality filters before
        # returning a signal; the aggregator should trust them.
        return self.action != TradeAction.HOLD


class SignalAggregator:
    """
    Aggregates signals from all three strategy layers and produces
    a single, weighted trading decision.

    Priority order:
    1. Intra-market arbitrage (risk-free, always take it)
    2. Lag arbitrage (primary alpha source)
    3. Late-period momentum (secondary, lower risk)
    """

    def __init__(self, strategy_config: StrategyConfig, risk_config: RiskConfig):
        self.config = strategy_config
        self.risk = risk_config

        self.lag_strategy = LagArbitrage(strategy_config, risk_config)
        self.arb_strategy = IntraArbitrage(strategy_config, risk_config)
        self.late_strategy = LateMomentum(strategy_config, risk_config)

        self._trade_count: int = 0
        self._last_trade_time: float = 0.0

    def evaluate(
        self,
        momentum: Optional[MomentumData],
        orderbook: Optional[MarketOrderbook],
        current_btc_price: float,
        opening_btc_price: float,
        time_remaining: float,
        available_balance: float,
        max_position: float,
    ) -> AggregatedSignal:
        """
        Evaluate all strategies and return the best trading decision.

        Args:
            momentum: Momentum data from Binance
            orderbook: Orderbook from Polymarket CLOB
            current_btc_price: Current BTC price
            opening_btc_price: BTC price at market open
            time_remaining: Seconds until market close
            available_balance: Available USDC balance
            max_position: Maximum position size

        Returns:
            AggregatedSignal with the recommended action
        """
        hold = AggregatedSignal(
            action=TradeAction.HOLD,
            confidence=0.0,
            position_size=0.0,
            target_token="none",
            target_price=0.0,
            expected_edge=0.0,
            source_strategy="none",
            timestamp=time.time(),
            reason="No actionable signal",
        )

        if orderbook is None:
            hold.reason = "No orderbook data"
            return hold

        if available_balance < 1.0:
            hold.reason = "Insufficient balance"
            return hold

        # === Priority 1: Intra-market Arbitrage (risk-free) ===
        arb_signal = self.arb_strategy.evaluate(orderbook)
        if arb_signal.is_valid:
            size = self.arb_strategy.compute_position_size(
                arb_signal, available_balance, max_position
            )
            return AggregatedSignal(
                action=TradeAction.BUY_BOTH,
                confidence=arb_signal.confidence,
                position_size=size,
                target_token="both",
                target_price=arb_signal.combined_ask,
                expected_edge=arb_signal.guaranteed_profit,
                source_strategy="intra_arbitrage",
                arb_signal=arb_signal,
                timestamp=time.time(),
                reason=arb_signal.reason,
            )

        # === Priority 2: Lag Arbitrage ===
        lag_signal = None
        if momentum is not None:
            lag_signal = self.lag_strategy.evaluate(momentum, orderbook, time_remaining)
            if lag_signal.is_valid:
                size = self.lag_strategy.compute_position_size(
                    lag_signal, available_balance, max_position
                )
                action = (
                    TradeAction.BUY_UP
                    if lag_signal.direction == SignalDirection.UP
                    else TradeAction.BUY_DOWN
                )
                return AggregatedSignal(
                    action=action,
                    confidence=lag_signal.confidence * self.config.lag_weight * 2,
                    position_size=size,
                    target_token=lag_signal.target_token,
                    target_price=lag_signal.ask_price,
                    expected_edge=lag_signal.expected_edge,
                    source_strategy="lag_arbitrage",
                    lag_signal=lag_signal,
                    timestamp=time.time(),
                    reason=lag_signal.reason,
                )

        # === Priority 3: Late-Period Momentum ===
        if opening_btc_price > 0 and current_btc_price > 0:
            late_signal = self.late_strategy.evaluate(
                current_btc_price, opening_btc_price, orderbook, time_remaining
            )
            if late_signal.is_valid:
                size = self.late_strategy.compute_position_size(
                    late_signal, available_balance, max_position
                )
                action = (
                    TradeAction.BUY_UP
                    if late_signal.direction == "up"
                    else TradeAction.BUY_DOWN
                )
                return AggregatedSignal(
                    action=action,
                    confidence=late_signal.confidence * self.config.late_weight * 2,
                    position_size=size,
                    target_token=late_signal.direction,
                    target_price=late_signal.ask_price,
                    expected_edge=late_signal.expected_edge,
                    source_strategy="late_momentum",
                    late_signal=late_signal,
                    timestamp=time.time(),
                    reason=late_signal.reason,
                )

        # Collect reasons for no action
        reasons = []
        if lag_signal:
            reasons.append(f"Lag: {lag_signal.reason}")
        if arb_signal:
            reasons.append(f"Arb: {arb_signal.reason}")
        hold.reason = " | ".join(reasons) if reasons else "No signals"

        return hold

    def reset(self):
        """Reset all strategies for a new market."""
        self.lag_strategy.reset()
        self.arb_strategy.reset()
        self.late_strategy.reset()
        self._trade_count = 0
        self._last_trade_time = 0.0
