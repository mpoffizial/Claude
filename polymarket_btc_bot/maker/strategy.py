"""
Market-Making Strategy: Manages the full quote lifecycle for a single
active binary market.

Per-tick flow (on each orderbook update):
  1. Check timing guards (skip first/last N seconds of market).
  2. Update volatility estimate in PricingEngine.
  3. Check for micro-arb opportunity (Yes+No < threshold) → execute if found.
  4. Generate fresh quotes for Yes and No tokens via PricingEngine.
  5. For each side:
     a. If existing orders need refreshing → cancel them.
     b. Place new GTC bid and ask orders.
  6. Evaluate HedgeSignal from InventoryManager → place hedge sell if needed.

Fill processing (called periodically by the bot):
  - Scan filled orders → update InventoryManager and RebateTracker.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from polymarket_btc_bot.data.market_discovery import MarketInfo
from polymarket_btc_bot.data.polymarket_clob import MarketOrderbook
from polymarket_btc_bot.execution.order_manager import (
    Order, OrderManager, OrderSide, OrderStatus, OrderType,
)
from polymarket_btc_bot.maker.config import MakerBotConfig
from polymarket_btc_bot.maker.inventory_manager import InventoryManager
from polymarket_btc_bot.maker.pricing_engine import PricingEngine, Quote
from polymarket_btc_bot.maker.rebate_tracker import RebateTracker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: links a Quote to its live GTC orders
# ---------------------------------------------------------------------------

@dataclass
class ActiveQuote:
    """Pairs a pricing Quote with the GTC orders it produced."""

    quote: Quote
    bid_order: Optional[Order]
    ask_order: Optional[Order]
    placed_at: float = field(default_factory=time.time)

    @property
    def bid_active(self) -> bool:
        return self.bid_order is not None and self.bid_order.is_active

    @property
    def ask_active(self) -> bool:
        return self.ask_order is not None and self.ask_order.is_active


# ---------------------------------------------------------------------------
# Per-market strategy
# ---------------------------------------------------------------------------

class MarketMakerStrategy:
    """
    Market-making strategy scoped to a single active binary market.

    One instance is created per market; it is shut down when the market
    expires and replaced by a new instance for the next market.
    """

    def __init__(
        self,
        config: MakerBotConfig,
        market: MarketInfo,
        order_manager: OrderManager,
        pricing: PricingEngine,
        inventory: InventoryManager,
        rebate_tracker: RebateTracker,
    ):
        self.config = config
        self.market = market
        self._orders = order_manager
        self._pricing = pricing
        self._inventory = inventory
        self._rebate = rebate_tracker

        # Side → ActiveQuote mapping.  Keys: "yes", "no"
        self._active_quotes: dict[str, ActiveQuote] = {}
        self._last_ob: Optional[MarketOrderbook] = None

        # Track fill IDs we have already processed to avoid double-counting.
        self._processed_fill_ids: set[str] = set()

        # Guards against issuing a hedge order while one is already in-flight.
        self._hedge_in_progress = False

    # ------------------------------------------------------------------
    # Timing guards
    # ------------------------------------------------------------------

    def _timing_ok(self) -> tuple[bool, str]:
        """Return (can_quote, reason)."""
        elapsed = self.market.time_elapsed
        remaining = self.market.time_remaining
        qcfg = self.config.quote

        if not self.market.is_active:
            return False, "Market not active"
        if elapsed < qcfg.no_quote_first_seconds:
            return False, f"Waiting for market to stabilise ({elapsed:.0f}s elapsed)"
        if remaining < qcfg.no_quote_last_seconds:
            return False, f"Market closing soon ({remaining:.0f}s remaining)"
        return True, "OK"

    # ------------------------------------------------------------------
    # Main entry point: orderbook update
    # ------------------------------------------------------------------

    async def on_orderbook_update(self, market_ob: MarketOrderbook) -> None:
        """
        Called on every live orderbook update.

        This is the hot path – keep it fast.  Heavy operations (REST calls,
        DB writes) should not happen here synchronously.
        """
        self._last_ob = market_ob

        # Update rolling volatility from the Yes mid-price
        yes_fv = self._pricing.compute_microprice(market_ob.up_book)
        if yes_fv is not None:
            self._pricing.update_volatility(yes_fv)

        can_quote, reason = self._timing_ok()
        if not can_quote:
            if self._active_quotes:
                logger.info("Pulling all quotes: %s", reason)
                await self._cancel_all_quotes()
            return

        # --- Micro-arb check (takes priority over regular quoting) ---
        if self.config.arb.enabled:
            await self._check_micro_arb(market_ob)

        # --- Generate and manage quotes ---
        quotes = self._pricing.generate_market_quotes(
            market_ob,
            self.market.up_token_id,
            self.market.down_token_id,
            self._inventory.yes_inventory,
            self._inventory.no_inventory,
        )

        if quotes.yes_quote:
            await self._manage_side("yes", quotes.yes_quote)

        if quotes.no_quote:
            await self._manage_side("no", quotes.no_quote)

        # --- Inventory hedge check ---
        if not self._hedge_in_progress:
            await self._check_and_hedge(market_ob)

    # ------------------------------------------------------------------
    # Quote management per side
    # ------------------------------------------------------------------

    async def _manage_side(self, side: str, new_quote: Quote) -> None:
        """
        Ensure the given side has valid, up-to-date GTC orders.

        1. If existing orders are stale → cancel them.
        2. If no orders exist → place fresh bid + ask.
        """
        existing = self._active_quotes.get(side)
        old_quote = existing.quote if existing else None

        needs_refresh = self._pricing.quotes_need_refresh(old_quote, new_quote.fair_value)
        if existing and needs_refresh:
            logger.debug(
                "Requoting %s: fv %.4f → %.4f",
                side, old_quote.fair_value, new_quote.fair_value,  # type: ignore[union-attr]
            )
            await self._cancel_side(side)
            existing = None

        if existing is None:
            await self._place_side(side, new_quote)

    async def _place_side(self, side: str, quote: Quote) -> None:
        """Place GTC bid and ask orders for one side."""
        token_id = quote.token_id

        # --- BID order ---
        can_bid, bid_reason = self._inventory.can_accept_fill(side, quote.bid_size_usdc)
        if can_bid:
            bid_size_tokens = quote.bid_size_usdc / quote.bid_price
            bid_order = await self._orders.place_order(
                token_id=token_id,
                side=OrderSide.BUY,
                price=quote.bid_price,
                size=round(bid_size_tokens, 2),
                order_type=OrderType.GTC,
            )
        else:
            logger.debug("Skipping %s bid: %s", side, bid_reason)
            bid_order = None

        # --- ASK order ---
        ask_size_tokens = quote.ask_size_usdc / quote.ask_price
        ask_order = await self._orders.place_order(
            token_id=token_id,
            side=OrderSide.SELL,
            price=quote.ask_price,
            size=round(ask_size_tokens, 2),
            order_type=OrderType.GTC,
        )

        self._active_quotes[side] = ActiveQuote(
            quote=quote,
            bid_order=bid_order,
            ask_order=ask_order,
        )

        logger.info(
            "Quoted %-3s  bid=%.3f ask=%.3f  "
            "fv=%.4f spread=%.4f skew=%+.4f  vol=%.5f",
            side.upper(),
            quote.bid_price, quote.ask_price,
            quote.fair_value, quote.half_spread, quote.skew_applied,
            self._pricing.current_volatility,
        )

    async def _cancel_side(self, side: str) -> None:
        """Cancel active orders for one side and remove the quote record."""
        aq = self._active_quotes.pop(side, None)
        if aq is None:
            return
        if aq.bid_order and aq.bid_order.is_active:
            await self._orders.cancel_order(aq.bid_order.order_id)
        if aq.ask_order and aq.ask_order.is_active:
            await self._orders.cancel_order(aq.ask_order.order_id)

    async def _cancel_all_quotes(self) -> None:
        """Cancel all active quotes on both sides."""
        for side in list(self._active_quotes.keys()):
            await self._cancel_side(side)

    # ------------------------------------------------------------------
    # Micro-arbitrage
    # ------------------------------------------------------------------

    async def _check_micro_arb(self, market_ob: MarketOrderbook) -> None:
        """
        Execute micro-arb if Yes+No < threshold.

        Uses FOK (Fill-or-Kill) for atomicity: either both legs fill or
        neither does, preventing one-sided exposure from the arb itself.
        """
        is_arb, profit_per_unit = self._pricing.check_micro_arb(market_ob)
        if not is_arb:
            return

        arb_cfg = self.config.arb
        yes_ask = market_ob.up_ask
        no_ask = market_ob.down_ask
        if yes_ask is None or no_ask is None:
            return

        # Estimate total profit for the configured USDC size
        arb_tokens = arb_cfg.size_usdc / yes_ask
        total_profit = profit_per_unit * arb_tokens

        if total_profit < arb_cfg.min_profit_usdc:
            logger.debug(
                "Micro-arb: profit $%.5f below minimum $%.4f (combined=%.4f)",
                total_profit, arb_cfg.min_profit_usdc, yes_ask + no_ask,
            )
            return

        logger.info(
            "Micro-arb: Yes@%.4f + No@%.4f = %.4f "
            "| profit/unit=%.5f total=$%.4f | executing",
            yes_ask, no_ask, yes_ask + no_ask, profit_per_unit, total_profit,
        )

        yes_size = arb_cfg.size_usdc / yes_ask
        yes_order, no_order = await self._orders.place_arbitrage_orders(
            up_token_id=self.market.up_token_id,
            down_token_id=self.market.down_token_id,
            up_price=yes_ask,
            down_price=no_ask,
            size=round(yes_size, 2),
        )

        if (
            yes_order.status == OrderStatus.FILLED
            and no_order.status == OrderStatus.FILLED
        ):
            self._rebate.record_arb_profit(total_profit, arb_cfg.size_usdc)
            logger.info("Micro-arb executed. Realised profit: $%.4f", total_profit)
        else:
            logger.warning(
                "Micro-arb partially failed: yes=%s no=%s",
                yes_order.status.value, no_order.status.value,
            )

    # ------------------------------------------------------------------
    # Auto-hedge
    # ------------------------------------------------------------------

    async def _check_and_hedge(self, market_ob: MarketOrderbook) -> None:
        """Place a hedge sell order when inventory imbalance is too large."""
        signal = self._inventory.evaluate_hedge_need()
        if not signal.should_hedge or signal.hedge_size_usdc <= 0:
            return

        self._hedge_in_progress = True
        try:
            logger.info("Hedge triggered: %s", signal.reason)
            is_yes = signal.hedge_side == "yes"
            book = market_ob.up_book if is_yes else market_ob.down_book
            token_id = self.market.up_token_id if is_yes else self.market.down_token_id

            fv = self._pricing.compute_microprice(book)
            if fv is None:
                return

            # Slightly aggressive price to ensure a fill
            sell_price = max(0.01, round(fv - 0.005, 3))
            sell_tokens = signal.hedge_size_usdc / sell_price

            order = await self._orders.place_order(
                token_id=token_id,
                side=OrderSide.SELL,
                price=sell_price,
                size=round(sell_tokens, 2),
                order_type=OrderType.GTC,
            )

            logger.info(
                "Hedge order placed: SELL %s %.2f tokens @ %.4f",
                signal.hedge_side, sell_tokens, sell_price,
            )

            # Give the hedge order a moment to settle before re-evaluating
            await asyncio.sleep(1.0)
        finally:
            self._hedge_in_progress = False

    # ------------------------------------------------------------------
    # Fill processing
    # ------------------------------------------------------------------

    def process_fill_events(self, filled_orders: list[Order]) -> None:
        """
        Reconcile filled maker orders against inventory and rebate trackers.

        Called periodically by the bot's monitoring loop.  Deduplicates on
        fill_id to prevent double-counting across calls.
        """
        for order in filled_orders:
            if order.order_id in self._processed_fill_ids:
                continue
            if order.filled_size <= 0 or order.avg_fill_price <= 0:
                continue

            self._processed_fill_ids.add(order.order_id)

            side = "yes" if order.token_id == self.market.up_token_id else "no"
            direction = "buy" if order.side == OrderSide.BUY else "sell"
            fill_usdc = order.filled_size * order.avg_fill_price

            if fill_usdc < self.config.risk.min_fill_to_track_usdc:
                continue

            # Fair value at fill time (best estimate from last observed book)
            fv = 0.50
            if self._last_ob:
                book = self._last_ob.up_book if side == "yes" else self._last_ob.down_book
                fv = self._pricing.compute_microprice(book) or 0.50

            # Update inventory
            self._inventory.record_fill(
                side=side,
                direction=direction,
                size_usdc=fill_usdc,
                price=order.avg_fill_price,
                fill_id=order.order_id,
            )

            # Update rebate / P&L tracker
            self._rebate.record_maker_fill(
                fill_id=order.order_id,
                market_slug=self.market.market_slug,
                token_id=order.token_id,
                side=side,
                direction=direction,
                price=order.avg_fill_price,
                size_usdc=fill_usdc,
                fair_value=fv,
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Cancel all open orders before this strategy is discarded."""
        logger.info("Shutting down maker strategy for %s", self.market.market_slug)
        await self._cancel_all_quotes()
