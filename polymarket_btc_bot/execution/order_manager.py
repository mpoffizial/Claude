"""
Order Manager: Handles order placement, cancellation, and tracking
via the Polymarket CLOB API. Supports limit orders (maker) and
fill-or-kill orders (for arbitrage).

Uses the py-clob-client library for EIP-712 signed order submission.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import aiohttp

from polymarket_btc_bot.config import PolymarketConfig, ExecutionConfig, TradingMode

logger = logging.getLogger(__name__)


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(Enum):
    GTC = "GTC"     # Good-Till-Cancelled (maker)
    FOK = "FOK"     # Fill-or-Kill (taker, for arb)
    GTD = "GTD"     # Good-Till-Date


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class Order:
    order_id: str
    token_id: str
    side: OrderSide
    price: float
    size: float
    order_type: OrderType
    status: OrderStatus = OrderStatus.PENDING
    filled_size: float = 0.0
    avg_fill_price: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    clob_order_id: Optional[str] = None  # Polymarket's order ID
    error: Optional[str] = None

    @property
    def remaining_size(self) -> float:
        return self.size - self.filled_size

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)

    @property
    def cost(self) -> float:
        return self.filled_size * self.avg_fill_price


class TokenBucket:
    """Rate limiter using token bucket algorithm."""

    def __init__(self, rate: float, capacity: float):
        self.rate = rate            # Tokens per second
        self.capacity = capacity    # Max tokens
        self.tokens = capacity
        self.last_refill = time.time()

    def consume(self, tokens: float = 1.0) -> bool:
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    async def wait_for_token(self):
        while not self.consume():
            await asyncio.sleep(0.05)


class OrderManager:
    """
    Manages order lifecycle: creation, submission, tracking, cancellation.
    Interfaces with Polymarket CLOB API for live/paper trading,
    or simulates orders in simulation mode.
    """

    def __init__(
        self,
        poly_config: PolymarketConfig,
        exec_config: ExecutionConfig,
        mode: TradingMode,
    ):
        self.poly_config = poly_config
        self.exec_config = exec_config
        self.mode = mode
        self._orders: dict[str, Order] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = TokenBucket(
            rate=exec_config.max_orders_per_minute / 60.0,
            capacity=float(exec_config.max_orders_per_minute),
        )
        self._clob_client = None

    async def start(self):
        """Initialize HTTP session and CLOB client."""
        self._session = aiohttp.ClientSession(trust_env=True)

        if self.mode != TradingMode.SIMULATION:
            try:
                from py_clob_client.client import ClobClient

                self._clob_client = ClobClient(
                    self.poly_config.clob_rest_url,
                    key=self.poly_config.private_key,
                    chain_id=self.poly_config.chain_id,
                    creds={
                        "apiKey": self.poly_config.api_key,
                        "secret": self.poly_config.api_secret,
                        "passphrase": self.poly_config.api_passphrase,
                    },
                )
                logger.info("CLOB client initialized for %s mode", self.mode.value)
            except ImportError:
                logger.warning(
                    "py-clob-client not installed. Live trading disabled. "
                    "Install with: pip install py-clob-client"
                )
                self._clob_client = None
            except Exception as e:
                logger.error("Failed to initialize CLOB client: %s", e)
                self._clob_client = None

        logger.info("OrderManager started in %s mode", self.mode.value)

    async def stop(self):
        """Cancel all open orders and clean up."""
        await self.cancel_all()
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("OrderManager stopped")

    async def place_order(
        self,
        token_id: str,
        side: OrderSide,
        price: float,
        size: float,
        order_type: OrderType = OrderType.GTC,
    ) -> Order:
        """
        Place an order on Polymarket.

        Args:
            token_id: The outcome token to trade
            side: BUY or SELL
            price: Limit price (0.01 to 0.99)
            size: Number of tokens
            order_type: GTC, FOK, or GTD

        Returns:
            Order object with status
        """
        order = Order(
            order_id=str(uuid.uuid4()),
            token_id=token_id,
            side=side,
            price=round(price, 4),
            size=round(size, 2),
            order_type=order_type,
        )

        self._orders[order.order_id] = order

        logger.info(
            "Placing order: %s %s %.2f @ %.4f (%s) [%s]",
            side.value, token_id[:8], size, price, order_type.value, self.mode.value,
        )

        if self.mode == TradingMode.SIMULATION:
            return await self._simulate_order(order)

        # Rate limit
        await self._rate_limiter.wait_for_token()

        try:
            return await self._submit_order(order)
        except Exception as e:
            order.status = OrderStatus.FAILED
            order.error = str(e)
            logger.error("Order placement failed: %s", e)
            return order

    async def _simulate_order(self, order: Order) -> Order:
        """Simulate order fill for backtesting/simulation mode."""
        await asyncio.sleep(0.01)  # Minimal simulated latency

        # In simulation, assume full fill at limit price
        order.status = OrderStatus.FILLED
        order.filled_size = order.size
        order.avg_fill_price = order.price
        order.updated_at = time.time()

        logger.info(
            "SIMULATED FILL: %s %s %.2f @ %.4f",
            order.side.value, order.token_id[:8], order.filled_size, order.avg_fill_price,
        )

        return order

    async def _submit_order(self, order: Order) -> Order:
        """Submit order to Polymarket CLOB API."""
        if not self._clob_client:
            order.status = OrderStatus.FAILED
            order.error = "CLOB client not initialized"
            return order

        try:
            from py_clob_client.order_builder.constants import BUY, SELL

            side = BUY if order.side == OrderSide.BUY else SELL

            # Build signed order
            signed_order = self._clob_client.create_and_sign_order({
                "tokenID": order.token_id,
                "price": order.price,
                "size": order.size,
                "side": side,
            })

            # Submit to CLOB
            order_type_map = {
                OrderType.GTC: "GTC",
                OrderType.FOK: "FOK",
                OrderType.GTD: "GTD",
            }

            response = self._clob_client.post_order(
                signed_order,
                order_type=order_type_map[order.order_type],
            )

            if response and hasattr(response, "id"):
                order.clob_order_id = response.id
                order.status = OrderStatus.OPEN
                logger.info("Order submitted: clob_id=%s", order.clob_order_id)
            elif isinstance(response, dict):
                order.clob_order_id = response.get("orderID") or response.get("id")
                if response.get("success", True):
                    order.status = OrderStatus.OPEN
                else:
                    order.status = OrderStatus.FAILED
                    order.error = response.get("errorMsg", "Unknown error")
            else:
                order.status = OrderStatus.OPEN

            order.updated_at = time.time()

        except Exception as e:
            order.status = OrderStatus.FAILED
            order.error = str(e)
            logger.error("CLOB order submission error: %s", e)

        return order

    async def place_arbitrage_orders(
        self,
        up_token_id: str,
        down_token_id: str,
        up_price: float,
        down_price: float,
        size: float,
    ) -> tuple[Order, Order]:
        """
        Place simultaneous orders on both sides for arbitrage.
        Uses FOK (Fill-or-Kill) to ensure atomic execution.
        """
        logger.info(
            "Placing ARB orders: UP %.2f@%.4f + DOWN %.2f@%.4f",
            size, up_price, size, down_price,
        )

        # Submit both orders concurrently
        up_order_coro = self.place_order(
            up_token_id, OrderSide.BUY, up_price, size, OrderType.FOK,
        )
        down_order_coro = self.place_order(
            down_token_id, OrderSide.BUY, down_price, size, OrderType.FOK,
        )

        up_order, down_order = await asyncio.gather(up_order_coro, down_order_coro)

        # If one side failed, try to cancel the other
        if up_order.status == OrderStatus.FAILED and down_order.status != OrderStatus.FAILED:
            logger.warning("Up order failed, cancelling down order")
            await self.cancel_order(down_order.order_id)
        elif down_order.status == OrderStatus.FAILED and up_order.status != OrderStatus.FAILED:
            logger.warning("Down order failed, cancelling up order")
            await self.cancel_order(up_order.order_id)

        return up_order, down_order

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a specific order."""
        order = self._orders.get(order_id)
        if not order or not order.is_active:
            return False

        if self.mode == TradingMode.SIMULATION:
            order.status = OrderStatus.CANCELLED
            order.updated_at = time.time()
            return True

        if self._clob_client and order.clob_order_id:
            try:
                self._clob_client.cancel(order.clob_order_id)
                order.status = OrderStatus.CANCELLED
                order.updated_at = time.time()
                logger.info("Cancelled order %s", order.clob_order_id)
                return True
            except Exception as e:
                logger.error("Cancel error: %s", e)
                return False

        return False

    async def cancel_all(self):
        """Cancel all active orders."""
        active = [oid for oid, o in self._orders.items() if o.is_active]
        for order_id in active:
            await self.cancel_order(order_id)
        logger.info("Cancelled %d active orders", len(active))

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    def get_active_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.is_active]

    def get_filled_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status == OrderStatus.FILLED]

    def get_orders_for_token(self, token_id: str) -> list[Order]:
        return [o for o in self._orders.values() if o.token_id == token_id]

    def clear_history(self):
        """Clear non-active orders from history."""
        self._orders = {oid: o for oid, o in self._orders.items() if o.is_active}

    # ------------------------------------------------------------------
    # Market-maker multi-order helpers
    # ------------------------------------------------------------------

    def get_mm_orders(self, tag: str) -> list[Order]:
        """Return active market-maker orders that carry the given tag prefix."""
        return [
            o for o in self._orders.values()
            if o.is_active and (o.clob_order_id or "").startswith(f"MM:{tag}:")
               or (o.order_id.startswith(f"mm_{tag}_") and o.is_active)
        ]

    async def place_ladder(
        self,
        token_id: str,
        levels: list[tuple[float, float]],   # (price, size_tokens) per level
        side: OrderSide = OrderSide.BUY,
        tag: str = "",
    ) -> list[Order]:
        """
        Place a staircase of limit orders for the given token.

        Args:
            token_id:  Outcome token to trade
            levels:    List of (price, token_size) tuples, one per rung
            side:      BUY or SELL (almost always BUY for prediction markets)
            tag:       Identifier used to track which orders belong to this ladder
                       (stored in order_id prefix so we can cancel them later)

        Returns:
            List of Order objects (one per level, in the same order as *levels*)
        """
        orders: list[Order] = []
        for i, (price, size) in enumerate(levels):
            order_id_prefix = f"mm_{tag}_{i}_" if tag else f"mm_{i}_"
            order = Order(
                order_id=order_id_prefix + str(int(time.time() * 1000)),
                token_id=token_id,
                side=side,
                price=round(price, 4),
                size=round(size, 2),
                order_type=OrderType.GTC,
            )
            self._orders[order.order_id] = order

            logger.info(
                "MM ladder [%s] level %d/%d: %s %.2f @ %.4f",
                tag, i + 1, len(levels), side.value, size, price,
            )

            if self.mode == TradingMode.SIMULATION:
                await self._simulate_order(order)
            else:
                await self._rate_limiter.wait_for_token()
                try:
                    await self._submit_order(order)
                except Exception as e:
                    order.status = OrderStatus.FAILED
                    order.error = str(e)
                    logger.error("Ladder order placement error: %s", e)

            orders.append(order)

        return orders

    async def cancel_ladder(self, tag: str) -> int:
        """
        Cancel all active orders whose order_id starts with the given tag prefix.

        Returns:
            Number of orders cancelled
        """
        prefix = f"mm_{tag}_"
        targets = [
            order_id for order_id, o in self._orders.items()
            if order_id.startswith(prefix) and o.is_active
        ]
        for order_id in targets:
            await self.cancel_order(order_id)
        if targets:
            logger.info("Cancelled %d MM orders [tag=%s]", len(targets), tag)
        return len(targets)

    async def cancel_stale_mm_orders(self, max_age_seconds: float) -> int:
        """
        Cancel any active market-maker orders older than *max_age_seconds*.

        Returns:
            Number of orders cancelled
        """
        cutoff = time.time() - max_age_seconds
        stale = [
            o.order_id for o in self._orders.values()
            if o.is_active
            and o.order_id.startswith("mm_")
            and o.created_at < cutoff
        ]
        for order_id in stale:
            await self.cancel_order(order_id)
        if stale:
            logger.info("Cancelled %d stale MM orders (age > %.0fs)", len(stale), max_age_seconds)
        return len(stale)

    def count_active_mm_orders(self) -> int:
        """Return number of currently active market-maker orders."""
        return sum(
            1 for o in self._orders.values()
            if o.is_active and o.order_id.startswith("mm_")
        )
