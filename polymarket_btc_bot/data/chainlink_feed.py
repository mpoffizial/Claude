"""
Chainlink BTC/USD price feed.
Polls the Chainlink Data Streams API for the official settlement price
used by Polymarket to resolve 15-minute markets.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

from polymarket_btc_bot.config import ChainlinkConfig

logger = logging.getLogger(__name__)


@dataclass
class ChainlinkPrice:
    price: float
    timestamp: float
    round_id: Optional[int] = None


class ChainlinkFeed:
    """Polls Chainlink BTC/USD for settlement prices."""

    def __init__(self, config: ChainlinkConfig):
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._latest_price: Optional[ChainlinkPrice] = None
        self._price_history: list[ChainlinkPrice] = []
        self._max_history = 1000

    @property
    def latest_price(self) -> Optional[ChainlinkPrice]:
        return self._latest_price

    @property
    def latest_value(self) -> Optional[float]:
        return self._latest_price.price if self._latest_price else None

    async def start(self):
        self._session = aiohttp.ClientSession()
        logger.info("ChainlinkFeed started")

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("ChainlinkFeed stopped")

    async def fetch_price(self) -> Optional[ChainlinkPrice]:
        """Fetch latest BTC/USD price from Chainlink."""
        if not self._session:
            raise RuntimeError("ChainlinkFeed not started")

        # Use Chainlink data API
        url = self.config.api_url
        try:
            async with self._session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    logger.warning("Chainlink API returned %d", resp.status)
                    return None

                data = await resp.json()

                # Parse Chainlink response format
                price = None
                timestamp = time.time()

                if isinstance(data, dict):
                    # Try different response formats
                    if "answer" in data:
                        price = float(data["answer"])
                        if "updatedAt" in data:
                            timestamp = float(data["updatedAt"])
                    elif "price" in data:
                        price = float(data["price"])
                    elif "result" in data:
                        result = data["result"]
                        if isinstance(result, dict):
                            price = float(result.get("price", result.get("answer", 0)))
                        else:
                            price = float(result)

                if price is None:
                    logger.warning("Could not parse Chainlink price from response")
                    return None

                chainlink_price = ChainlinkPrice(
                    price=price,
                    timestamp=timestamp,
                    round_id=data.get("roundId"),
                )

                self._latest_price = chainlink_price
                self._price_history.append(chainlink_price)
                if len(self._price_history) > self._max_history:
                    self._price_history = self._price_history[-self._max_history:]

                return chainlink_price

        except Exception as e:
            logger.error("Chainlink fetch error: %s", e)
            return None

    async def fetch_price_at_timestamp(self, target_timestamp: int) -> Optional[float]:
        """
        Fetch the Chainlink BTC/USD price at a specific timestamp.
        Used to determine the opening price of a 15m market.
        """
        # Check history first
        best = None
        best_diff = float("inf")

        for hp in self._price_history:
            diff = abs(hp.timestamp - target_timestamp)
            if diff < best_diff:
                best_diff = diff
                best = hp.price

        if best and best_diff < 30:  # Within 30 seconds is acceptable
            return best

        # Otherwise fetch from API with timestamp parameter
        if not self._session:
            return None

        try:
            # Try fetching historical price - API may support timestamp query
            url = f"{self.config.api_url}"
            params = {"timestamp": target_timestamp}

            async with self._session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict):
                        for key in ("answer", "price", "result"):
                            if key in data:
                                val = data[key]
                                if isinstance(val, dict):
                                    return float(val.get("price", val.get("answer", 0)))
                                return float(val)
        except Exception as e:
            logger.error("Chainlink historical price fetch error: %s", e)

        return best  # Return best from history even if not ideal

    async def run_polling_loop(self):
        """Continuously poll Chainlink for price updates."""
        self._running = True
        logger.info("Starting Chainlink polling loop (interval: %.1fs)", self.config.poll_interval_seconds)

        while self._running:
            try:
                price = await self.fetch_price()
                if price:
                    logger.debug("Chainlink BTC/USD: $%.2f", price.price)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Chainlink polling error: %s", e)

            await asyncio.sleep(self.config.poll_interval_seconds)

    def get_price_nearest(self, target_time: float) -> Optional[float]:
        """Get the price closest to a target timestamp from history."""
        if not self._price_history:
            return None

        best = min(self._price_history, key=lambda p: abs(p.timestamp - target_time))
        return best.price
