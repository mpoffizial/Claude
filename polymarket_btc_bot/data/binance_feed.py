"""
Binance WebSocket handler for real-time BTC/USDT price data.
Maintains a rolling price buffer and computes momentum scores.

Fallback: Wenn Binance WebSocket nicht erreichbar ist (z.B. HTTP 403),
wird automatisch auf Kraken/Coinbase REST-Polling (alle 5s) umgeschaltet.
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import json as _json
import urllib.request as _urllib_req

import websockets
from websockets.exceptions import ConnectionClosed

from polymarket_btc_bot.config import BinanceConfig

logger = logging.getLogger(__name__)


@dataclass
class PricePoint:
    timestamp: float
    price: float
    quantity: float


@dataclass
class MomentumData:
    current_price: float
    price_ago: float
    momentum_score: float      # (current - past) / past
    lookback_seconds: int
    timestamp: float
    volume_in_window: float


class BinanceFeed:
    """
    Real-time BTC/USDT price feed.

    Primär: Binance aggTrades WebSocket.
    Fallback: Kraken/Coinbase REST-Polling alle 5s wenn WebSocket blockiert ist.
    """

    # REST-Fallback Endpunkte (werden der Reihe nach probiert)
    _REST_SOURCES = [
        {
            "name": "Kraken",
            "url": "https://api.kraken.com/0/public/Ticker?pair=BTCUSD",
            "parser": lambda d: float(d["result"]["XXBTZUSD"]["c"][0]),
        },
        {
            "name": "Coinbase",
            "url": "https://api.coinbase.com/v2/prices/BTC-USD/spot",
            "parser": lambda d: float(d["data"]["amount"]),
        },
    ]
    _REST_POLL_INTERVAL = 5.0   # Sekunden zwischen REST-Abfragen
    _WS_FAIL_THRESHOLD = 3      # Anzahl WS-Fehler bevor auf REST umgeschaltet wird

    def __init__(self, config: BinanceConfig, lookback_seconds: int = 60):
        self.config = config
        self.lookback_seconds = lookback_seconds
        self._ws = None
        self._running = False
        self._price_buffer: deque[PricePoint] = deque(maxlen=10000)
        self._current_price: float = 0.0
        self._callbacks: list[Callable] = []
        self._reconnect_delay = config.reconnect_delay
        self._last_trade_time: float = 0.0

        # Fallback-Tracking
        self._ws_fail_count: int = 0
        self._using_rest_fallback: bool = False

    @property
    def current_price(self) -> float:
        return self._current_price

    @property
    def last_trade_time(self) -> float:
        return self._last_trade_time

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._running

    def on_price(self, callback: Callable):
        """Register a callback for price updates."""
        self._callbacks.append(callback)

    def get_price_at(self, seconds_ago: float) -> Optional[float]:
        """Get the price approximately N seconds ago."""
        target_time = time.time() - seconds_ago
        best = None
        best_diff = float("inf")

        for point in self._price_buffer:
            diff = abs(point.timestamp - target_time)
            if diff < best_diff:
                best_diff = diff
                best = point.price

        if best_diff > 5.0:  # If best match is >5s off, unreliable
            return None
        return best

    def compute_momentum(self, lookback_seconds: Optional[int] = None) -> Optional[MomentumData]:
        """Compute momentum score over the lookback window."""
        lookback = lookback_seconds or self.lookback_seconds

        if not self._price_buffer:
            return None

        current = self._current_price
        past_price = self.get_price_at(lookback)

        if past_price is None or past_price == 0:
            return None

        # Calculate volume in window
        cutoff = time.time() - lookback
        volume = sum(
            p.quantity for p in self._price_buffer
            if p.timestamp >= cutoff
        )

        momentum = (current - past_price) / past_price

        return MomentumData(
            current_price=current,
            price_ago=past_price,
            momentum_score=momentum,
            lookback_seconds=lookback,
            timestamp=time.time(),
            volume_in_window=volume,
        )

    def get_vwap(self, seconds: int = 60) -> Optional[float]:
        """Compute volume-weighted average price over last N seconds."""
        cutoff = time.time() - seconds
        total_value = 0.0
        total_volume = 0.0

        for point in self._price_buffer:
            if point.timestamp >= cutoff:
                total_value += point.price * point.quantity
                total_volume += point.quantity

        if total_volume == 0:
            return None
        return total_value / total_volume

    def get_volatility(self, seconds: int = 300) -> Optional[float]:
        """Compute price volatility (std dev of returns) over last N seconds."""
        cutoff = time.time() - seconds
        prices = [p.price for p in self._price_buffer if p.timestamp >= cutoff]

        if len(prices) < 10:
            return None

        returns = []
        for i in range(1, len(prices)):
            if prices[i - 1] > 0:
                returns.append((prices[i] - prices[i - 1]) / prices[i - 1])

        if not returns:
            return None

        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return variance ** 0.5

    async def _handle_message(self, message: str):
        """Process a single aggTrade message from Binance."""
        try:
            data = json.loads(message)
            price = float(data["p"])
            quantity = float(data["q"])
            trade_time = data["T"] / 1000.0  # Convert ms to seconds

            self._current_price = price
            self._last_trade_time = trade_time

            point = PricePoint(
                timestamp=time.time(),
                price=price,
                quantity=quantity,
            )
            self._price_buffer.append(point)

            # Notify callbacks
            for cb in self._callbacks:
                try:
                    result = cb(point)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error("Price callback error: %s", e)

        except (KeyError, ValueError) as e:
            logger.warning("Invalid Binance message: %s", e)

    async def connect(self):
        """
        Verbinde mit Binance WebSocket. Bei wiederholten Fehlern (HTTP 403 etc.)
        automatischer Wechsel auf Kraken/Coinbase REST-Polling.
        """
        self._running = True

        while self._running:
            # REST-Fallback aktiv?
            if self._using_rest_fallback:
                await self._rest_polling_loop()
                return

            try:
                logger.info("Connecting to Binance WebSocket: %s", self.config.ws_url)
                async with websockets.connect(
                    self.config.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._reconnect_delay = self.config.reconnect_delay
                    self._ws_fail_count = 0
                    logger.info("Connected to Binance WebSocket")

                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(message)

            except ConnectionClosed as e:
                logger.warning("Binance WS closed: %s", e)
                self._ws_fail_count += 1
            except Exception as e:
                logger.error("Binance WS error: %s", e)
                self._ws_fail_count += 1

            self._ws = None

            # Nach mehreren Fehlern auf REST umschalten
            if self._ws_fail_count >= self._WS_FAIL_THRESHOLD:
                logger.warning(
                    "Binance WebSocket %d× fehlgeschlagen — wechsle auf REST-Fallback "
                    "(Kraken/Coinbase)",
                    self._ws_fail_count,
                )
                self._using_rest_fallback = True
                await self._rest_polling_loop()
                return

            if self._running:
                logger.info("Reconnecting in %.1fs...", self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    self.config.max_reconnect_delay,
                )

    async def _rest_polling_loop(self):
        """
        REST-Polling Fallback: Fragt Kraken/Coinbase alle 5s nach dem BTC-Preis.

        Nutzt urllib (synchron) via run_in_executor, da asyncio's DNS-Resolver
        in manchen Umgebungen nicht verfügbar ist. Schreibt in denselben
        _price_buffer wie der WebSocket-Handler.
        """
        logger.info(
            "REST-Polling gestartet (Intervall: %.0fs) | Quellen: %s",
            self._REST_POLL_INTERVAL,
            ", ".join(s["name"] for s in self._REST_SOURCES),
        )

        # Sicherstellen dass _running gesetzt ist
        if not self._running:
            self._running = True

        loop = asyncio.get_event_loop()

        while self._running:
            price = await loop.run_in_executor(None, self._fetch_rest_price_sync)
            if price and price > 0:
                now = time.time()
                point = PricePoint(timestamp=now, price=price, quantity=1.0)
                self._price_buffer.append(point)
                self._current_price = price
                self._last_trade_time = now

                for cb in self._callbacks:
                    try:
                        result = cb(point)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("Price callback error: %s", e)

            await asyncio.sleep(self._REST_POLL_INTERVAL)

    def _fetch_rest_price_sync(self) -> Optional[float]:
        """Synchroner BTC-Preis-Abruf via urllib (Kraken → Coinbase Fallback)."""
        for source in self._REST_SOURCES:
            try:
                req = _urllib_req.Request(
                    source["url"],
                    headers={"User-Agent": "polymarket-mm-bot/1.0"},
                )
                with _urllib_req.urlopen(req, timeout=6) as resp:
                    data = _json.loads(resp.read())
                    price = source["parser"](data)
                    logger.debug("[%s] BTC = $%.2f", source["name"], price)
                    return price
            except Exception as e:
                logger.debug("[%s] Fehler: %s", source["name"], e)
                continue
        logger.warning("REST-Fallback: Alle Quellen fehlgeschlagen")
        return None

    async def stop(self):
        """Disconnect from WebSocket and stop REST polling."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("Binance feed stopped")

    def _cleanup_old_data(self, max_age_seconds: int = 900):
        """Remove price points older than max_age_seconds."""
        cutoff = time.time() - max_age_seconds
        while self._price_buffer and self._price_buffer[0].timestamp < cutoff:
            self._price_buffer.popleft()
