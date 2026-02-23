"""
Market-Maker Strategy: Limit-Order Ladder

Continuously maintains a staircase of GTC buy-limit orders on both the
UP and DOWN outcome tokens throughout the entire duration of the active
5- or 15-minute BTC market.

How the ladder works
--------------------
Given a current best-ask of A for a token, we place `num_levels` orders
at prices:

    level[0] = A - first_level_offset               (closest to market)
    level[1] = level[0] - level_spacing
    level[2] = level[1] - level_spacing
    ...

The same ladder is built independently for the UP token and the DOWN token
so that the bot always has active limit orders on both sides simultaneously.

Order lifecycle
---------------
* Every `refresh_interval_seconds` the strategy recomputes the desired
  ladder and compares it against currently active orders.
* If the market mid-price has moved >= `reprice_threshold` since the last
  quote the entire side is cancelled and re-quoted at the new prices.
* Orders older than `max_order_age_seconds` are force-cancelled and
  replaced regardless of price movement.
* No orders are placed in the first `start_after_seconds` of the market
  or in the last `min_time_remaining` seconds.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from polymarket_btc_bot.config import MarketMakerConfig, RiskConfig
from polymarket_btc_bot.data.polymarket_clob import MarketOrderbook

logger = logging.getLogger(__name__)


@dataclass
class LimitLevel:
    """A single rung in the order ladder."""
    side: str           # "up" or "down"
    price: float        # Limit price to bid
    size_usdc: float    # Dollar size of this level


@dataclass
class MarketMakerQuote:
    """
    The full desired quote for one evaluation cycle.
    Contains all the levels that should be active on both sides.
    """
    up_levels: list[LimitLevel] = field(default_factory=list)
    down_levels: list[LimitLevel] = field(default_factory=list)
    up_mid: float = 0.0
    down_mid: float = 0.0
    timestamp: float = field(default_factory=time.time)
    reason: str = ""

    @property
    def all_levels(self) -> list[LimitLevel]:
        return self.up_levels + self.down_levels

    @property
    def is_valid(self) -> bool:
        return bool(self.up_levels or self.down_levels)


class MarketMaker:
    """
    Generates a limit-order ladder for both UP and DOWN outcome tokens.

    The caller (TradingBot) is responsible for actually placing and
    cancelling orders via OrderManager; this class only *computes* what
    the desired state should look like.
    """

    def __init__(self, config: MarketMakerConfig, risk: RiskConfig):
        self.config = config
        self.risk = risk

        self._last_quote_time: float = 0.0
        self._last_up_mid: float = 0.0
        self._last_down_mid: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_refresh(self, orderbook: MarketOrderbook) -> bool:
        """Return True if it is time to re-evaluate the ladder."""
        now = time.time()

        # Periodic refresh
        if now - self._last_quote_time >= self.config.refresh_interval_seconds:
            return True

        # Price drift reprice
        up_mid = self._mid(orderbook.up_book.best_bid, orderbook.up_book.best_ask)
        down_mid = self._mid(orderbook.down_book.best_bid, orderbook.down_book.best_ask)

        if up_mid and abs(up_mid - self._last_up_mid) >= self.config.reprice_threshold:
            return True
        if down_mid and abs(down_mid - self._last_down_mid) >= self.config.reprice_threshold:
            return True

        return False

    def compute_quote(
        self,
        orderbook: MarketOrderbook,
        time_elapsed: float,
        time_remaining: float,
    ) -> MarketMakerQuote:
        """
        Compute the full desired ladder for the current market state.

        Args:
            orderbook:      Current Polymarket orderbook snapshot
            time_elapsed:   Seconds since market open
            time_remaining: Seconds until market close

        Returns:
            MarketMakerQuote with the target limit-order levels on each side
        """
        quote = MarketMakerQuote()

        # --- Timing guards ---
        if time_elapsed < self.config.start_after_seconds:
            quote.reason = (
                f"Waiting for market to settle ({time_elapsed:.0f}s / "
                f"{self.config.start_after_seconds}s elapsed)"
            )
            return quote

        if time_remaining < self.config.min_time_remaining:
            quote.reason = (
                f"Too close to market close ({time_remaining:.0f}s remaining)"
            )
            return quote

        # --- Build UP ladder ---
        up_ask = orderbook.up_book.best_ask
        up_bid = orderbook.up_book.best_bid
        up_levels = self._build_ladder("up", up_ask, up_bid)

        # --- Build DOWN ladder ---
        down_ask = orderbook.down_book.best_ask
        down_bid = orderbook.down_book.best_bid
        down_levels = self._build_ladder("down", down_ask, down_bid)

        if not up_levels and not down_levels:
            quote.reason = "No valid price levels available on either side"
            return quote

        quote.up_levels = up_levels
        quote.down_levels = down_levels
        quote.up_mid = self._mid(up_bid, up_ask) or 0.0
        quote.down_mid = self._mid(down_bid, down_ask) or 0.0
        quote.reason = (
            f"UP ladder x{len(up_levels)} | DOWN ladder x{len(down_levels)}"
        )

        # Record for next drift check
        self._last_quote_time = time.time()
        self._last_up_mid = quote.up_mid
        self._last_down_mid = quote.down_mid

        logger.info(
            "MM quote | UP %d levels @ %.3f–%.3f | DOWN %d levels @ %.3f–%.3f",
            len(up_levels),
            up_levels[0].price if up_levels else 0,
            up_levels[-1].price if up_levels else 0,
            len(down_levels),
            down_levels[0].price if down_levels else 0,
            down_levels[-1].price if down_levels else 0,
        )

        return quote

    def reset(self):
        """Reset state for a new market."""
        self._last_quote_time = 0.0
        self._last_up_mid = 0.0
        self._last_down_mid = 0.0
        logger.debug("MarketMaker reset")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_ladder(
        self,
        side: str,
        best_ask: Optional[float],
        best_bid: Optional[float],
    ) -> list[LimitLevel]:
        """
        Build `num_levels` limit orders below the current best ask.

        We anchor the first level at (best_ask - first_level_offset),
        i.e. we undercut the ask slightly so our orders sit just inside
        the spread, maximising the chance of being filled while still
        earning maker rebates.
        """
        if best_ask is None:
            # Fall back to bid-side anchor if no ask available
            if best_bid is None:
                return []
            anchor = best_bid - self.config.first_level_offset
        else:
            anchor = best_ask - self.config.first_level_offset

        levels: list[LimitLevel] = []
        for i in range(self.config.num_levels):
            price = round(anchor - i * self.config.level_spacing, 4)

            # Hard price bounds
            if price < self.config.min_ask_price:
                break
            if price > self.config.max_ask_price:
                continue
            # Sanity: price must be a valid probability (0, 1)
            if not (0.01 <= price <= 0.99):
                continue

            levels.append(
                LimitLevel(
                    side=side,
                    price=price,
                    size_usdc=self.config.size_per_level,
                )
            )

        return levels

    @staticmethod
    def _mid(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0
