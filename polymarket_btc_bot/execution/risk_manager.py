"""
Risk Manager: Enforces all risk limits, position sizing,
stop-loss, and daily exposure constraints.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from polymarket_btc_bot.config import RiskConfig, ExecutionConfig
from polymarket_btc_bot.execution.position_tracker import PositionTracker
from polymarket_btc_bot.strategy.signal_aggregator import AggregatedSignal, TradeAction

logger = logging.getLogger(__name__)


@dataclass
class RiskDecision:
    approved: bool
    adjusted_size: float
    reason: str
    risk_score: float           # 0.0 (safe) to 1.0 (maximum risk)


class RiskManager:
    """
    Central risk management enforcing all hard limits.
    Must approve every trade before execution.
    """

    def __init__(
        self,
        risk_config: RiskConfig,
        exec_config: ExecutionConfig,
        position_tracker: PositionTracker,
    ):
        self.config = risk_config
        self.exec_config = exec_config
        self.tracker = position_tracker
        self._bot_active = True
        self._halt_reason: Optional[str] = None
        self._daily_trades: int = 0
        self._daily_start: float = time.time()

    @property
    def is_halted(self) -> bool:
        return not self._bot_active

    @property
    def halt_reason(self) -> Optional[str]:
        return self._halt_reason

    def halt_bot(self, reason: str):
        """Emergency halt of all trading."""
        self._bot_active = False
        self._halt_reason = reason
        logger.critical("BOT HALTED: %s", reason)

    def resume_bot(self):
        """Resume trading after halt."""
        self._bot_active = True
        self._halt_reason = None
        logger.info("Bot resumed")

    def evaluate_signal(self, signal: AggregatedSignal) -> RiskDecision:
        """
        Evaluate a trading signal against all risk parameters.

        Returns:
            RiskDecision with approval status and adjusted position size
        """
        # Check if bot is halted
        if not self._bot_active:
            return RiskDecision(
                approved=False,
                adjusted_size=0.0,
                reason=f"Bot halted: {self._halt_reason}",
                risk_score=1.0,
            )

        # Check if signal is actionable
        if not signal.is_actionable:
            return RiskDecision(
                approved=False,
                adjusted_size=0.0,
                reason="Signal not actionable",
                risk_score=0.0,
            )

        # Check position limits
        can_open, pos_reason = self.tracker.can_open_position()
        if not can_open:
            return RiskDecision(
                approved=False,
                adjusted_size=0.0,
                reason=pos_reason,
                risk_score=0.8,
            )

        # Check daily loss limit
        today = self.tracker.get_today_stats()
        if today and today.total_pnl_after_fee <= self.config.max_daily_loss:
            self.halt_bot(f"Daily loss limit reached: ${today.total_pnl_after_fee:.2f}")
            return RiskDecision(
                approved=False,
                adjusted_size=0.0,
                reason="Daily loss limit reached",
                risk_score=1.0,
            )

        # Check edge requirements
        if signal.expected_edge < self.config.min_edge_threshold:
            return RiskDecision(
                approved=False,
                adjusted_size=0.0,
                reason=f"Edge {signal.expected_edge:.4f} below minimum {self.config.min_edge_threshold}",
                risk_score=0.3,
            )

        # Adjust position size
        adjusted_size = self._adjust_position_size(signal)
        if adjusted_size < 1.0:
            return RiskDecision(
                approved=False,
                adjusted_size=0.0,
                reason="Adjusted size too small",
                risk_score=0.2,
            )

        # Calculate risk score
        risk_score = self._calculate_risk_score(signal, adjusted_size)

        # Check if risk is acceptable
        if risk_score > 0.9:
            return RiskDecision(
                approved=False,
                adjusted_size=0.0,
                reason=f"Risk score too high: {risk_score:.2f}",
                risk_score=risk_score,
            )

        logger.info(
            "RISK APPROVED: %s | size=$%.2f | edge=%.4f | risk=%.2f",
            signal.action.value,
            adjusted_size,
            signal.expected_edge,
            risk_score,
        )

        return RiskDecision(
            approved=True,
            adjusted_size=adjusted_size,
            reason="Approved",
            risk_score=risk_score,
        )

    def _adjust_position_size(self, signal: AggregatedSignal) -> float:
        """Adjust position size based on risk constraints."""
        size = signal.position_size

        # Cap at max per market
        size = min(size, self.config.max_position_per_market)

        # Cap at default trade size
        size = min(size, self.exec_config.default_trade_size)

        # Reduce if approaching daily exposure limit
        current_exposure = self.tracker.total_open_exposure
        remaining_capacity = self.config.max_daily_exposure - current_exposure
        if remaining_capacity < size:
            size = max(0, remaining_capacity)

        # Scale down based on daily loss
        today = self.tracker.get_today_stats()
        if today and today.total_pnl_after_fee < 0:
            # Reduce size proportionally to losses
            loss_ratio = abs(today.total_pnl_after_fee) / abs(self.config.max_daily_loss)
            scale = max(0.25, 1.0 - loss_ratio)
            size *= scale

        return round(size, 2)

    def _calculate_risk_score(self, signal: AggregatedSignal, size: float) -> float:
        """Calculate overall risk score (0.0 = safe, 1.0 = max risk)."""
        scores = []

        # Position concentration risk
        exposure = self.tracker.total_open_exposure + size
        exposure_ratio = exposure / self.config.max_daily_exposure
        scores.append(exposure_ratio * 0.3)

        # Daily loss proximity risk
        today = self.tracker.get_today_stats()
        if today:
            loss_ratio = abs(min(0, today.total_pnl_after_fee)) / abs(self.config.max_daily_loss)
            scores.append(loss_ratio * 0.4)
        else:
            scores.append(0.0)

        # Edge-based risk (lower edge = higher risk)
        edge_risk = max(0, 1.0 - signal.expected_edge / 0.10)
        scores.append(edge_risk * 0.3)

        return min(1.0, sum(scores))

    def check_position_timeout(self, position) -> bool:
        """Check if a position has been held too long."""
        max_hold = self.config.max_hold_time_minutes * 60
        return position.hold_time_seconds > max_hold

    def should_exit_early(self, position, current_ask_price: float) -> tuple[bool, str]:
        """
        Determine if a position should be exited early.

        Returns:
            (should_exit, reason)
        """
        # Check hold time
        if self.check_position_timeout(position):
            return True, f"Position held for {position.hold_time_seconds:.0f}s (max {self.config.max_hold_time_minutes}min)"

        # Check unrealized loss
        unrealized = current_ask_price * position.size - position.cost
        if unrealized < self.config.max_single_trade_loss:
            return True, f"Unrealized loss ${unrealized:.2f} exceeds limit ${self.config.max_single_trade_loss}"

        return False, ""

    def reset_daily(self):
        """Reset daily counters."""
        self._daily_trades = 0
        self._daily_start = time.time()
        if not self._bot_active and "daily" in (self._halt_reason or "").lower():
            self.resume_bot()
