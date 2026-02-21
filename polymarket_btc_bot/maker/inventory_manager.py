"""
Inventory Manager: Tracks Yes/No token holdings and manages inventory
risk through skew signals and optional auto-hedging.

Responsibilities:
  - Record every maker fill (buy or sell) on Yes/No tokens
  - Track current inventory in USDC-value terms
  - Emit HedgeSignal when net exposure exceeds the configured threshold
  - Gate new quote sizes against per-side and net-exposure limits
  - Clear inventory state on market settlement
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from polymarket_btc_bot.maker.config import MakerInventoryConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class InventoryState:
    """Point-in-time snapshot of held inventory."""

    yes_inventory_usdc: float = 0.0   # USDC value of Yes tokens
    no_inventory_usdc: float = 0.0    # USDC value of No tokens
    yes_tokens: float = 0.0           # Raw Yes token count
    no_tokens: float = 0.0            # Raw No token count
    updated_at: float = field(default_factory=time.time)

    @property
    def net_exposure_usdc(self) -> float:
        """Signed net exposure: positive = net long Yes, negative = net long No."""
        return self.yes_inventory_usdc - self.no_inventory_usdc

    @property
    def total_inventory_usdc(self) -> float:
        return self.yes_inventory_usdc + self.no_inventory_usdc

    @property
    def imbalance_ratio(self) -> float:
        """0 = perfectly balanced inventory, 1 = fully one-sided."""
        total = self.total_inventory_usdc
        if total <= 0:
            return 0.0
        return abs(self.net_exposure_usdc) / total


@dataclass
class HedgeSignal:
    """Instruction to reduce a specific side of inventory."""

    should_hedge: bool
    hedge_side: Optional[str]       # "yes" or "no" – sell this side
    hedge_size_usdc: float
    reason: str


# ---------------------------------------------------------------------------
# Inventory Manager
# ---------------------------------------------------------------------------

class InventoryManager:
    """
    Maintains a real-time view of maker inventory and emits hedge signals.

    Fill accounting:
      - BUY  → we received tokens (inventory increases)
      - SELL → we gave tokens (inventory decreases)

    Skew signal: the PricingEngine reads yes/no inventory directly and
    computes the appropriate quote shift.  This class is the single source
    of truth for those values.
    """

    def __init__(self, config: MakerInventoryConfig):
        self._cfg = config
        self._state = InventoryState()
        self._fill_log: list[dict] = []

    # ------------------------------------------------------------------
    # Properties (read by PricingEngine and strategy)
    # ------------------------------------------------------------------

    @property
    def state(self) -> InventoryState:
        return self._state

    @property
    def yes_inventory(self) -> float:
        return self._state.yes_inventory_usdc

    @property
    def no_inventory(self) -> float:
        return self._state.no_inventory_usdc

    @property
    def net_exposure(self) -> float:
        return self._state.net_exposure_usdc

    # ------------------------------------------------------------------
    # Fill recording
    # ------------------------------------------------------------------

    def record_fill(
        self,
        side: str,          # "yes" or "no"
        direction: str,     # "buy" (we got tokens) | "sell" (we gave tokens)
        size_usdc: float,   # USDC value of the fill
        price: float,       # Fill price in probability units
        fill_id: str = "",
    ) -> None:
        """
        Update inventory state after a maker fill.

        Args:
            side:      Which token was traded ("yes" or "no").
            direction: "buy" = taker sold to us (our bid was lifted);
                       "sell" = taker bought from us (our ask was hit).
            size_usdc: USDC value transferred.
            price:     Fill price (0.01 – 0.99).
            fill_id:   Optional identifier for logging/auditing.
        """
        tokens = size_usdc / price if price > 0 else 0.0

        if side == "yes":
            if direction == "buy":
                self._state.yes_inventory_usdc += size_usdc
                self._state.yes_tokens += tokens
            else:
                self._state.yes_inventory_usdc = max(0.0, self._state.yes_inventory_usdc - size_usdc)
                self._state.yes_tokens = max(0.0, self._state.yes_tokens - tokens)
        elif side == "no":
            if direction == "buy":
                self._state.no_inventory_usdc += size_usdc
                self._state.no_tokens += tokens
            else:
                self._state.no_inventory_usdc = max(0.0, self._state.no_inventory_usdc - size_usdc)
                self._state.no_tokens = max(0.0, self._state.no_tokens - tokens)
        else:
            logger.warning("Unknown fill side: %s", side)
            return

        self._state.updated_at = time.time()
        self._fill_log.append({
            "ts": time.time(),
            "side": side,
            "direction": direction,
            "size_usdc": size_usdc,
            "price": price,
            "fill_id": fill_id,
        })

        logger.info(
            "Inventory fill: %s %s $%.2f @ %.4f | "
            "Yes=$%.2f No=$%.2f Net=%+.2f",
            direction.upper(), side.upper(), size_usdc, price,
            self._state.yes_inventory_usdc,
            self._state.no_inventory_usdc,
            self._state.net_exposure_usdc,
        )

    # ------------------------------------------------------------------
    # Hedge evaluation
    # ------------------------------------------------------------------

    def evaluate_hedge_need(self) -> HedgeSignal:
        """
        Decide whether the current inventory imbalance requires hedging.

        Hedge logic:
          If |net_exposure| > hedge_trigger, sell down the long side
          targeting a reduced exposure of hedge_trigger * hedge_target_ratio.
        """
        if not self._cfg.auto_hedge_enabled:
            return HedgeSignal(
                should_hedge=False,
                hedge_side=None,
                hedge_size_usdc=0.0,
                reason="Auto-hedge disabled",
            )

        net = self._state.net_exposure_usdc
        trigger = self._cfg.hedge_trigger_usdc

        if abs(net) < trigger:
            return HedgeSignal(
                should_hedge=False,
                hedge_side=None,
                hedge_size_usdc=0.0,
                reason=f"Net exposure ${net:+.2f} within limit ${trigger:.2f}",
            )

        # Target: reduce |net| to hedge_trigger * hedge_target_ratio
        target_net = trigger * self._cfg.hedge_target_ratio
        hedge_size = abs(net) - target_net
        hedge_size = max(0.0, round(hedge_size, 2))

        if net > 0:
            return HedgeSignal(
                should_hedge=True,
                hedge_side="yes",
                hedge_size_usdc=hedge_size,
                reason=f"Net long Yes ${net:+.2f} > trigger ${trigger:.2f}",
            )
        else:
            return HedgeSignal(
                should_hedge=True,
                hedge_side="no",
                hedge_size_usdc=hedge_size,
                reason=f"Net long No ${-net:.2f} > trigger ${trigger:.2f}",
            )

    # ------------------------------------------------------------------
    # Pre-fill gating
    # ------------------------------------------------------------------

    def can_accept_fill(self, side: str, size_usdc: float) -> tuple[bool, str]:
        """
        Check whether accepting a BUY fill of `size_usdc` on `side` would
        breach per-side or net-exposure limits.

        Use this to downsize orders before they are placed.

        Returns:
            (allowed, reason_if_denied)
        """
        if side == "yes":
            projected = self._state.yes_inventory_usdc + size_usdc
            if projected > self._cfg.max_yes_inventory_usdc:
                return (
                    False,
                    f"Yes inventory ${projected:.2f} would exceed limit "
                    f"${self._cfg.max_yes_inventory_usdc:.2f}",
                )
        elif side == "no":
            projected = self._state.no_inventory_usdc + size_usdc
            if projected > self._cfg.max_no_inventory_usdc:
                return (
                    False,
                    f"No inventory ${projected:.2f} would exceed limit "
                    f"${self._cfg.max_no_inventory_usdc:.2f}",
                )

        # Check net exposure after fill
        net_delta = size_usdc if side == "yes" else -size_usdc
        projected_net = abs(self._state.net_exposure_usdc + net_delta)
        if projected_net > self._cfg.max_net_exposure_usdc:
            return (
                False,
                f"Net exposure ${projected_net:.2f} would exceed limit "
                f"${self._cfg.max_net_exposure_usdc:.2f}",
            )

        return True, "OK"

    # ------------------------------------------------------------------
    # Market lifecycle
    # ------------------------------------------------------------------

    def clear_market_inventory(self) -> None:
        """
        Reset inventory after a market settles.

        In a real implementation settlement would be confirmed on-chain
        before resetting.  Here we clear immediately so the next market
        starts from a clean slate.
        """
        logger.info(
            "Clearing market inventory: Yes=$%.2f No=$%.2f Net=%+.2f",
            self._state.yes_inventory_usdc,
            self._state.no_inventory_usdc,
            self._state.net_exposure_usdc,
        )
        self._state = InventoryState()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_summary(self) -> dict:
        s = self._state
        return {
            "yes_inventory_usdc": round(s.yes_inventory_usdc, 4),
            "no_inventory_usdc": round(s.no_inventory_usdc, 4),
            "yes_tokens": round(s.yes_tokens, 4),
            "no_tokens": round(s.no_tokens, 4),
            "net_exposure_usdc": round(s.net_exposure_usdc, 4),
            "total_inventory_usdc": round(s.total_inventory_usdc, 4),
            "imbalance_ratio": round(s.imbalance_ratio, 4),
            "total_fills_recorded": len(self._fill_log),
        }
