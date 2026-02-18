"""
Polymarket CLOB WebSocket handler for live orderbook data.
Tracks best bid/ask for Up and Down tokens of the active 15m market.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from polymarket_btc_bot.config import PolymarketConfig

logger = logging.getLogger(__name__)


@dataclass
class OrderbookLevel:
    price: float
    size: float


@dataclass
class OrderbookSnapshot:
    token_id: str
    bids: list[OrderbookLevel] = field(default_factory=list)
    asks: list[OrderbookLevel] = field(default_factory=list)
    timestamp: float = 0.0

    @property
    def best_bid(self) -> Optional[float]:
        if not self.bids:
            return None
        return max(b.price for b in self.bids)

    @property
    def best_ask(self) -> Optional[float]:
        if not self.asks:
            return None
        return min(a.price for a in self.asks)

    @property
    def best_bid_size(self) -> Optional[float]:
        if not self.bids:
            return None
        best_price = self.best_bid
        return sum(b.size for b in self.bids if b.price == best_price)

    @property
    def best_ask_size(self) -> Optional[float]:
        if not self.asks:
            return None
        best_price = self.best_ask
        return sum(a.size for a in self.asks if a.price == best_price)

    @property
    def mid_price(self) -> Optional[float]:
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    @property
    def spread(self) -> Optional[float]:
        bid = self.best_bid
        ask = self.best_ask
        if bid is None or ask is None:
            return None
        return ask - bid


@dataclass
class MarketOrderbook:
    up_book: OrderbookSnapshot
    down_book: OrderbookSnapshot

    @property
    def up_ask(self) -> Optional[float]:
        return self.up_book.best_ask

    @property
    def down_ask(self) -> Optional[float]:
        return self.down_book.best_ask

    @property
    def up_bid(self) -> Optional[float]:
        return self.up_book.best_bid

    @property
    def down_bid(self) -> Optional[float]:
        return self.down_book.best_bid

    @property
    def combined_ask(self) -> Optional[float]:
        """Sum of best asks. Should be ~1.0 in efficient market."""
        up = self.up_ask
        down = self.down_ask
        if up is None or down is None:
            return None
        return up + down


class PolymarketCLOB:
    """WebSocket client for Polymarket CLOB orderbook data."""

    def __init__(self, config: PolymarketConfig):
        self.config = config
        self._ws = None
        self._running = False
        self._orderbooks: dict[str, OrderbookSnapshot] = {}
        self._up_token_id: Optional[str] = None
        self._down_token_id: Optional[str] = None
        self._callbacks: list[Callable] = []
        self._reconnect_delay = 1.0

    @property
    def market_orderbook(self) -> Optional[MarketOrderbook]:
        if not self._up_token_id or not self._down_token_id:
            return None
        up = self._orderbooks.get(self._up_token_id)
        down = self._orderbooks.get(self._down_token_id)
        if not up or not down:
            return None
        return MarketOrderbook(up_book=up, down_book=down)

    def set_market(self, up_token_id: str, down_token_id: str):
        """Set which token IDs to track."""
        self._up_token_id = up_token_id
        self._down_token_id = down_token_id
        self._orderbooks[up_token_id] = OrderbookSnapshot(token_id=up_token_id)
        self._orderbooks[down_token_id] = OrderbookSnapshot(token_id=down_token_id)
        logger.info("Tracking market: Up=%s Down=%s", up_token_id, down_token_id)

    def on_update(self, callback: Callable):
        """Register callback for orderbook updates."""
        self._callbacks.append(callback)

    def _parse_book_update(self, data: dict):
        """Parse orderbook update message."""
        try:
            asset_id = data.get("asset_id", "")
            if asset_id not in self._orderbooks:
                return

            book = self._orderbooks[asset_id]

            # Handle snapshot
            if "bids" in data:
                book.bids = [
                    OrderbookLevel(price=float(b["price"]), size=float(b["size"]))
                    for b in data["bids"]
                    if float(b["size"]) > 0
                ]
            if "asks" in data:
                book.asks = [
                    OrderbookLevel(price=float(a["price"]), size=float(a["size"]))
                    for a in data["asks"]
                    if float(a["size"]) > 0
                ]

            book.timestamp = time.time()

        except (KeyError, ValueError) as e:
            logger.warning("Error parsing book update: %s", e)

    def _apply_delta(self, data: dict):
        """Apply incremental orderbook delta."""
        try:
            asset_id = data.get("asset_id", "")
            if asset_id not in self._orderbooks:
                return

            book = self._orderbooks[asset_id]

            for change in data.get("changes", []):
                side = change.get("side", "")
                price = float(change["price"])
                size = float(change["size"])

                if side == "BUY":
                    book.bids = [b for b in book.bids if b.price != price]
                    if size > 0:
                        book.bids.append(OrderbookLevel(price=price, size=size))
                elif side == "SELL":
                    book.asks = [a for a in book.asks if a.price != price]
                    if size > 0:
                        book.asks.append(OrderbookLevel(price=price, size=size))

            book.timestamp = time.time()

        except (KeyError, ValueError) as e:
            logger.warning("Error applying delta: %s", e)

    async def _handle_message(self, message: str):
        """Process WebSocket message."""
        try:
            data = json.loads(message)

            msg_type = data.get("type", "")

            if msg_type == "book":
                self._parse_book_update(data)
            elif msg_type == "book_delta":
                self._apply_delta(data)
            elif msg_type in ("price_change", "trade"):
                # Also handle price_change events
                asset_id = data.get("asset_id", "")
                if asset_id in self._orderbooks:
                    self._orderbooks[asset_id].timestamp = time.time()

            # Notify callbacks
            ob = self.market_orderbook
            if ob:
                for cb in self._callbacks:
                    try:
                        result = cb(ob)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("Orderbook callback error: %s", e)

        except json.JSONDecodeError:
            logger.warning("Invalid JSON from Polymarket WS")

    async def _subscribe(self, ws):
        """Send subscription message for tracked tokens."""
        tokens = []
        if self._up_token_id:
            tokens.append(self._up_token_id)
        if self._down_token_id:
            tokens.append(self._down_token_id)

        if not tokens:
            logger.warning("No tokens to subscribe to")
            return

        subscribe_msg = {
            "type": "market",
            "assets_ids": tokens,
        }
        await ws.send(json.dumps(subscribe_msg))
        logger.info("Subscribed to tokens: %s", tokens)

    async def connect(self):
        """Connect to Polymarket CLOB WebSocket with auto-reconnect."""
        self._running = True

        while self._running:
            try:
                logger.info("Connecting to Polymarket CLOB WebSocket")
                async with websockets.connect(
                    self.config.clob_ws_url,
                    ping_interval=30,
                    ping_timeout=15,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1.0
                    logger.info("Connected to Polymarket CLOB WebSocket")

                    await self._subscribe(ws)

                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(message)

            except ConnectionClosed as e:
                logger.warning("Polymarket WS closed: %s", e)
            except Exception as e:
                logger.error("Polymarket WS error: %s", e)

            self._ws = None

            if self._running:
                logger.info("Reconnecting in %.1fs...", self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)

    async def stop(self):
        """Disconnect from WebSocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("Polymarket CLOB feed stopped")

    async def fetch_orderbook_rest(self, token_id: str) -> Optional[OrderbookSnapshot]:
        """Fallback: fetch orderbook via REST API."""
        import aiohttp

        url = f"{self.config.clob_rest_url}/book"
        params = {"token_id": token_id}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    book = OrderbookSnapshot(token_id=token_id)
                    book.bids = [
                        OrderbookLevel(price=float(b["price"]), size=float(b["size"]))
                        for b in data.get("bids", [])
                    ]
                    book.asks = [
                        OrderbookLevel(price=float(a["price"]), size=float(a["size"]))
                        for a in data.get("asks", [])
                    ]
                    book.timestamp = time.time()
                    return book
        except Exception as e:
            logger.error("REST orderbook fetch error: %s", e)
            return None
