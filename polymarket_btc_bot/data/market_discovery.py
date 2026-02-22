"""
Auto-discovers the currently active BTC Up/Down 15-minute market on Polymarket.
Fetches market metadata including condition_id, token_ids, start/end times.
"""

import asyncio
import json as _json
import logging
import time
import urllib.request as _urllib_req
from dataclasses import dataclass
from typing import Optional

from polymarket_btc_bot.config import PolymarketConfig

logger = logging.getLogger(__name__)


@dataclass
class MarketInfo:
    condition_id: str
    question: str
    market_slug: str
    up_token_id: str
    down_token_id: str
    start_timestamp: int        # Unix timestamp of market open
    end_timestamp: int          # Unix timestamp of market close
    opening_price: Optional[float] = None  # Chainlink BTC price at open

    @property
    def duration_seconds(self) -> int:
        return self.end_timestamp - self.start_timestamp

    @property
    def time_remaining(self) -> float:
        return max(0.0, self.end_timestamp - time.time())

    @property
    def time_elapsed(self) -> float:
        return max(0.0, time.time() - self.start_timestamp)

    @property
    def is_active(self) -> bool:
        now = time.time()
        return self.start_timestamp <= now <= self.end_timestamp

    @property
    def is_expired(self) -> bool:
        return time.time() > self.end_timestamp


class MarketDiscovery:
    """Discovers and tracks active BTC 15-minute markets on Polymarket."""

    def __init__(self, config: PolymarketConfig):
        self.config = config
        self._current_market: Optional[MarketInfo] = None
        self._next_market: Optional[MarketInfo] = None

    async def start(self):
        logger.info("MarketDiscovery started")

    async def stop(self):
        logger.info("MarketDiscovery stopped")

    @property
    def current_market(self) -> Optional[MarketInfo]:
        return self._current_market

    @property
    def next_market(self) -> Optional[MarketInfo]:
        return self._next_market

    def _http_get_json(self, url: str, timeout: int = 10) -> Optional[dict]:
        """Synchroner HTTP GET via urllib (funktioniert ohne asyncio DNS)."""
        try:
            req = _urllib_req.Request(
                url,
                headers={"User-Agent": "polymarket-mm-bot/1.0", "Accept": "application/json"},
            )
            with _urllib_req.urlopen(req, timeout=timeout) as resp:
                return _json.loads(resp.read())
        except Exception as e:
            logger.error("HTTP GET fehlgeschlagen (%s): %s", url[:60], e)
            return None

    async def fetch_active_markets(self) -> list[dict]:
        """Fetch active BTC up/down 15m markets from Gamma API."""
        url = (
            f"{self.config.gamma_api_url}/events"
            "?limit=10&active=true&closed=false&tag=btc"
        )

        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, self._http_get_json, url)

        if not data or not isinstance(data, list):
            logger.warning("No active BTC 15m markets found")
            return []

        # Filter für BTC Up/Down 15min Märkte
        btc_15m = []
        for event in data:
            slug = event.get("slug", "")
            title = event.get("title", "").lower()
            if "btc" in slug and "15m" in slug and ("up" in title or "down" in title):
                btc_15m.append(event)
            elif "btc" in title and "15" in title and ("up" in title or "down" in title):
                btc_15m.append(event)
        return btc_15m

    async def fetch_market_details(self, condition_id: str) -> Optional[dict]:
        """Fetch detailed market info from CLOB API."""
        url = f"{self.config.clob_rest_url}/markets/{condition_id}"
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._http_get_json, url)

    def _parse_timestamp_from_slug(self, slug: str) -> Optional[int]:
        """Extract unix timestamp from market slug like 'btc-updown-15m-1771443900'."""
        parts = slug.split("-")
        for part in reversed(parts):
            if part.isdigit() and len(part) >= 10:
                return int(part)
        return None

    def _parse_market_info(self, event: dict) -> Optional[MarketInfo]:
        """Parse event data into MarketInfo."""
        try:
            markets = event.get("markets", [])
            if not markets:
                return None

            # For binary markets, we expect exactly one market with two outcomes
            market = markets[0]
            condition_id = market.get("conditionId") or market.get("condition_id", "")
            slug = event.get("slug", "")

            # Extract tokens
            tokens = market.get("clobTokenIds")
            if not tokens or len(tokens) < 2:
                # Try outcomes approach
                outcomes = market.get("outcomes", ["Up", "Down"])
                outcome_prices = market.get("outcomePrices", [])
                tokens = market.get("clobTokenIds", ["", ""])

            if not tokens or len(tokens) < 2:
                logger.warning("Could not find token IDs for market %s", condition_id)
                return None

            # Determine which token is Up and which is Down
            outcomes = market.get("outcomes", [])
            up_token = tokens[0]
            down_token = tokens[1]

            if len(outcomes) >= 2:
                for i, outcome in enumerate(outcomes):
                    if "up" in outcome.lower() or "yes" in outcome.lower():
                        up_token = tokens[i]
                        down_token = tokens[1 - i]
                        break

            # Parse timestamps
            start_ts = self._parse_timestamp_from_slug(slug)
            if start_ts is None:
                # Try from market data
                start_ts = market.get("startTimestamp")
                if start_ts:
                    start_ts = int(start_ts)

            if start_ts is None:
                logger.warning("Could not determine start timestamp for %s", slug)
                return None

            end_ts = start_ts + 900  # 15 minutes = 900 seconds

            return MarketInfo(
                condition_id=condition_id,
                question=market.get("question", ""),
                market_slug=slug,
                up_token_id=up_token,
                down_token_id=down_token,
                start_timestamp=start_ts,
                end_timestamp=end_ts,
            )
        except Exception as e:
            logger.error("Error parsing market info: %s", e)
            return None

    async def discover_current_market(self) -> Optional[MarketInfo]:
        """Find the currently active BTC 15m market."""
        events = await self.fetch_active_markets()
        now = time.time()

        candidates = []
        for event in events:
            info = self._parse_market_info(event)
            if info and info.is_active:
                candidates.append(info)

        if not candidates:
            logger.warning("No active BTC 15m markets found")
            return None

        # Pick the one with the most time remaining
        candidates.sort(key=lambda m: m.time_remaining, reverse=True)
        self._current_market = candidates[0]
        logger.info(
            "Discovered active market: %s (%.0fs remaining)",
            self._current_market.market_slug,
            self._current_market.time_remaining,
        )
        return self._current_market

    async def discover_next_market(self) -> Optional[MarketInfo]:
        """Discover the upcoming market that hasn't started yet."""
        events = await self.fetch_active_markets()
        now = time.time()

        upcoming = []
        for event in events:
            info = self._parse_market_info(event)
            if info and info.start_timestamp > now:
                upcoming.append(info)

        if not upcoming:
            # Predict next market based on current
            if self._current_market:
                next_start = self._current_market.end_timestamp
                logger.info("Predicting next market starts at %d", next_start)
            return None

        upcoming.sort(key=lambda m: m.start_timestamp)
        self._next_market = upcoming[0]
        logger.info(
            "Discovered next market: %s (starts in %.0fs)",
            self._next_market.market_slug,
            self._next_market.start_timestamp - now,
        )
        return self._next_market

    async def run_discovery_loop(self, callback=None):
        """Continuously discover and track markets."""
        logger.info("Starting market discovery loop")

        while True:
            try:
                current = await self.discover_current_market()

                if current and callback:
                    await callback(current)

                # Pre-fetch next market 60s before current ends
                if current and current.time_remaining < 60:
                    await self.discover_next_market()

                # If current market expired, swap to next
                if current and current.is_expired:
                    if self._next_market and self._next_market.is_active:
                        self._current_market = self._next_market
                        self._next_market = None
                        if callback:
                            await callback(self._current_market)

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Discovery loop error: %s", e)
                await asyncio.sleep(10)
