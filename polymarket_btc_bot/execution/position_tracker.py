"""
Position Tracker: Tracks open positions, calculates P&L,
and manages position lifecycle across markets.
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from polymarket_btc_bot.config import RiskConfig
from polymarket_btc_bot.execution.order_manager import Order, OrderStatus, OrderSide
from polymarket_btc_bot.execution.fee_calculator import FeeCalculator

logger = logging.getLogger(__name__)


class PositionStatus(Enum):
    OPEN = "open"
    CLOSED_WIN = "closed_win"
    CLOSED_LOSS = "closed_loss"
    CLOSED_EXPIRED = "closed_expired"


@dataclass
class Position:
    position_id: str
    market_slug: str
    token_id: str
    side: str                   # "up" or "down"
    entry_price: float
    size: float                 # Number of tokens
    cost: float                 # Total USDC spent
    status: PositionStatus = PositionStatus.OPEN
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_after_fee: float = 0.0
    opened_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None
    strategy: str = ""          # Which strategy opened this

    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN

    @property
    def hold_time_seconds(self) -> float:
        end = self.closed_at or time.time()
        return end - self.opened_at

    def close(self, won: bool, fee_calculator: Optional["FeeCalculator"] = None, winner_fee: float = 0.02):
        """
        Close this position with the market result.

        Nutzt FeeCalculator fuer exaktes PnL:
        - WIN:  Netto = tokens * (1 - winner_fee) - gas - cost
        - LOSE: Netto = -cost - gas
        """
        now = time.time()
        self.closed_at = now

        if fee_calculator is not None:
            self.pnl, self.pnl_after_fee = fee_calculator.close_position_pnl(
                tokens=self.size, cost=self.cost, won=won
            )
        else:
            # Fallback: nur Winner-Fee
            if won:
                gross = self.size * 1.0
                fee = gross * winner_fee
                self.pnl = gross - self.cost
                self.pnl_after_fee = gross - fee - self.cost
            else:
                self.pnl = -self.cost
                self.pnl_after_fee = -self.cost

        self.exit_price = 1.0 if won else 0.0
        self.status = PositionStatus.CLOSED_WIN if won else PositionStatus.CLOSED_LOSS

        logger.info(
            "Position closed: %s %s | %s | "
            "PnL gross: $%.4f | PnL nach Fee+Gas: $%.4f",
            self.side,
            self.market_slug,
            "WIN" if won else "LOSS",
            self.pnl,
            self.pnl_after_fee,
        )


@dataclass
class DailyStats:
    date: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    total_pnl_after_fee: float = 0.0
    total_volume: float = 0.0
    max_drawdown: float = 0.0
    peak_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.wins / self.total_trades

    @property
    def avg_pnl_per_trade(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.total_pnl_after_fee / self.total_trades


class PositionTracker:
    """Tracks all positions and computes aggregate statistics."""

    def __init__(self, risk_config: RiskConfig):
        self.risk = risk_config
        self._positions: dict[str, Position] = {}
        self._daily_stats: dict[str, DailyStats] = {}
        self._total_pnl: float = 0.0
        self._session_start: float = time.time()
        self._fee_calculator = FeeCalculator(
            market_type=risk_config.market_type,
            gas_cost=risk_config.gas_cost_usdc,
        )

    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.is_open]

    @property
    def closed_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if not p.is_open]

    @property
    def total_pnl(self) -> float:
        return self._total_pnl

    @property
    def open_position_count(self) -> int:
        return len(self.open_positions)

    @property
    def total_open_exposure(self) -> float:
        return sum(p.cost for p in self.open_positions)

    def open_position(
        self,
        order: Order,
        market_slug: str,
        side: str,
        strategy: str,
    ) -> Position:
        """Create a new position from a filled order."""
        position = Position(
            position_id=order.order_id,
            market_slug=market_slug,
            token_id=order.token_id,
            side=side,
            entry_price=order.avg_fill_price if order.avg_fill_price > 0 else order.price,
            size=order.filled_size if order.filled_size > 0 else order.size,
            cost=order.cost if order.cost > 0 else order.size * order.price,
            strategy=strategy,
        )

        self._positions[position.position_id] = position

        logger.info(
            "Position opened: %s %s | %.2f tokens @ %.4f | cost=$%.4f | strategy=%s",
            side,
            market_slug,
            position.size,
            position.entry_price,
            position.cost,
            strategy,
        )

        return position

    def close_position(self, position_id: str, won: bool):
        """Close a position with the market result."""
        position = self._positions.get(position_id)
        if not position or not position.is_open:
            return

        position.close(won, fee_calculator=self._fee_calculator)
        self._total_pnl += position.pnl_after_fee

        # Update daily stats
        self._update_daily_stats(position)

    def close_all_for_market(self, market_slug: str, up_won: bool):
        """Close all positions for a market that has resolved."""
        for position in self.open_positions:
            if position.market_slug == market_slug:
                won = (position.side == "up" and up_won) or \
                      (position.side == "down" and not up_won)
                self.close_position(position.position_id, won)

    def _update_daily_stats(self, position: Position):
        """Update daily statistics."""
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")

        if today not in self._daily_stats:
            self._daily_stats[today] = DailyStats(date=today)

        stats = self._daily_stats[today]
        stats.total_trades += 1
        stats.total_volume += position.cost

        if position.status == PositionStatus.CLOSED_WIN:
            stats.wins += 1
        else:
            stats.losses += 1

        stats.total_pnl += position.pnl
        stats.total_pnl_after_fee += position.pnl_after_fee

        # Update peak and drawdown
        if stats.total_pnl_after_fee > stats.peak_pnl:
            stats.peak_pnl = stats.total_pnl_after_fee
        drawdown = stats.peak_pnl - stats.total_pnl_after_fee
        if drawdown > stats.max_drawdown:
            stats.max_drawdown = drawdown

    def get_today_stats(self) -> Optional[DailyStats]:
        """Get today's trading statistics."""
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")
        return self._daily_stats.get(today)

    def get_all_stats(self) -> dict:
        """Get aggregate statistics across all days."""
        all_positions = self.closed_positions
        if not all_positions:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl_per_trade": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
            }

        wins = sum(1 for p in all_positions if p.status == PositionStatus.CLOSED_WIN)
        total = len(all_positions)
        pnls = [p.pnl_after_fee for p in all_positions]
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / total if total > 0 else 0.0

        # Compute Sharpe ratio (simplified)
        if len(pnls) > 1:
            mean_pnl = sum(pnls) / len(pnls)
            variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
            std_pnl = variance ** 0.5
            sharpe = (mean_pnl / std_pnl) * (252 ** 0.5) if std_pnl > 0 else 0.0
        else:
            sharpe = 0.0

        # Max drawdown
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for pnl in pnls:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        return {
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": wins / total if total > 0 else 0.0,
            "total_pnl": total_pnl,
            "avg_pnl_per_trade": avg_pnl,
            "max_drawdown": max_dd,
            "sharpe_ratio": sharpe,
            "total_volume": sum(p.cost for p in all_positions),
        }

    def can_open_position(self) -> tuple[bool, str]:
        """Check if we can open a new position based on risk limits."""
        # Check max open positions
        if self.open_position_count >= self.risk.max_open_positions:
            return False, f"Max open positions ({self.risk.max_open_positions}) reached"

        # Check daily exposure
        if self.total_open_exposure >= self.risk.max_daily_exposure:
            return False, f"Max daily exposure (${self.risk.max_daily_exposure}) reached"

        # Check daily loss
        today = self.get_today_stats()
        if today and today.total_pnl_after_fee <= self.risk.max_daily_loss:
            return False, f"Max daily loss (${self.risk.max_daily_loss}) reached"

        return True, "OK"
