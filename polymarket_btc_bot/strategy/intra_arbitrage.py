"""
Layer 2: Intra-Market Arbitrage Strategy

Exploits mispricing when P(Up) + P(Down) < 1.0 (minus fees).
In a binary market, buying both sides guarantees profit if the
combined ask is below the threshold accounting for the 2% winner fee.
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from polymarket_btc_bot.config import StrategyConfig, RiskConfig
from polymarket_btc_bot.data.polymarket_clob import MarketOrderbook

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageSignal:
    is_opportunity: bool
    up_ask: float
    down_ask: float
    combined_ask: float
    guaranteed_profit: float    # Profit per $1 spent on both sides
    confidence: float
    timestamp: float
    reason: str

    @property
    def is_valid(self) -> bool:
        return self.is_opportunity and self.guaranteed_profit > 0


class IntraArbitrage:
    """
    Detects risk-free arbitrage opportunities when the sum of
    Up and Down ask prices falls below the threshold.

    The threshold accounts for the 2% winner fee:
    - Buy Up at up_ask, Buy Down at down_ask
    - Total cost = up_ask + down_ask
    - Guaranteed payout = $1.00 * (1 - 0.02) = $0.98
    - Profit = 0.98 - (up_ask + down_ask)
    - Opportunity exists when: up_ask + down_ask < 0.975 (conservative)
    """

    def __init__(self, strategy_config: StrategyConfig, risk_config: RiskConfig):
        self.config = strategy_config
        self.risk = risk_config
        self._opportunities_found: int = 0
        self._last_signal_time: float = 0.0

    def evaluate(self, orderbook: MarketOrderbook) -> ArbitrageSignal:
        """
        Evaluate current orderbook for arbitrage opportunities.

        Args:
            orderbook: Current market orderbook with Up and Down books

        Returns:
            ArbitrageSignal indicating whether an opportunity exists
        """
        no_signal = ArbitrageSignal(
            is_opportunity=False,
            up_ask=0.0,
            down_ask=0.0,
            combined_ask=0.0,
            guaranteed_profit=0.0,
            confidence=0.0,
            timestamp=time.time(),
            reason="",
        )

        up_ask = orderbook.up_ask
        down_ask = orderbook.down_ask

        if up_ask is None or down_ask is None:
            no_signal.reason = "Missing ask prices"
            return no_signal

        combined = up_ask + down_ask
        payout = 1.0 - self.risk.winner_fee  # $0.98
        guaranteed_profit = payout - combined

        if combined >= self.config.arb_threshold:
            no_signal.up_ask = up_ask
            no_signal.down_ask = down_ask
            no_signal.combined_ask = combined
            no_signal.guaranteed_profit = guaranteed_profit
            no_signal.reason = (
                f"Combined ask {combined:.4f} >= threshold {self.config.arb_threshold}"
            )
            return no_signal

        if guaranteed_profit < self.config.arb_min_profit:
            no_signal.up_ask = up_ask
            no_signal.down_ask = down_ask
            no_signal.combined_ask = combined
            no_signal.guaranteed_profit = guaranteed_profit
            no_signal.reason = (
                f"Profit {guaranteed_profit:.4f} below minimum {self.config.arb_min_profit}"
            )
            return no_signal

        # Check that there's sufficient liquidity on both sides
        up_size = orderbook.up_book.best_ask_size
        down_size = orderbook.down_book.best_ask_size
        if up_size is not None and down_size is not None:
            min_size = min(up_size, down_size)
            if min_size < 5.0:  # Need at least $5 available
                no_signal.reason = f"Insufficient liquidity: min size = {min_size:.2f}"
                return no_signal

        # Calculate confidence based on profit size
        confidence = min(1.0, guaranteed_profit / 0.02)

        self._opportunities_found += 1
        self._last_signal_time = time.time()

        signal = ArbitrageSignal(
            is_opportunity=True,
            up_ask=up_ask,
            down_ask=down_ask,
            combined_ask=combined,
            guaranteed_profit=guaranteed_profit,
            confidence=confidence,
            timestamp=time.time(),
            reason=(
                f"ARB: up_ask={up_ask:.4f} + down_ask={down_ask:.4f} = "
                f"{combined:.4f} < {self.config.arb_threshold} | "
                f"profit={guaranteed_profit:.4f}"
            ),
        )

        logger.info(
            "ARB SIGNAL: combined=%.4f | profit=%.4f | up=%.4f down=%.4f",
            combined,
            guaranteed_profit,
            up_ask,
            down_ask,
        )

        return signal

    def compute_position_size(
        self,
        signal: ArbitrageSignal,
        available_balance: float,
        max_position: float,
    ) -> float:
        """
        Calculate position size for arbitrage.
        For arb, we buy equal amounts of both sides.
        Total cost = size * (up_ask + down_ask).
        """
        if not signal.is_valid:
            return 0.0

        # Maximum tokens we can buy, constrained by both sides
        max_by_balance = available_balance / signal.combined_ask
        size = min(max_by_balance, max_position / signal.combined_ask)

        # Scale down for safety - don't use full position on arb
        size *= 0.8
        return max(1.0, round(size, 2))

    def reset(self):
        """Reset state for new market."""
        self._opportunities_found = 0
        self._last_signal_time = 0.0
