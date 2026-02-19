#!/usr/bin/env python3
"""
Chainlink Lag Logger - Records Spot Price vs Chainlink On-Chain Price.

This is a LOGGING-ONLY script. No trades are executed.
Run this for ~100 market resolutions to calibrate the lag threshold
before enabling live trading.

Usage:
    python -m polymarket_btc_bot.chainlink_lag.lag_logger
    python -m polymarket_btc_bot.chainlink_lag.lag_logger --duration 24h
    python -m polymarket_btc_bot.chainlink_lag.lag_logger --markets 100

Output:
    lag_data/lag_observations.csv    - Per-second price pairs
    lag_data/market_resolutions.csv  - Summary per market resolution
"""

import argparse
import asyncio
import csv
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed

from polymarket_btc_bot.chainlink_lag.config import (
    LagArbitrageConfig,
    LoggingConfig,
)
from polymarket_btc_bot.chainlink_lag.chainlink_onchain import (
    ChainlinkOnChainReader,
    OnChainPrice,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("lag_logger")


# --- Data Structures ---

@dataclass
class LagObservation:
    """A single point-in-time observation of spot vs chainlink."""
    timestamp: float
    datetime_utc: str
    spot_price: float
    chainlink_price: float
    chainlink_round_id: int
    chainlink_updated_at: int
    lag_delta: float            # spot - chainlink (USD)
    lag_delta_pct: float        # lag as percentage of spot
    on_chain_age_seconds: float # time since last chainlink update
    rpc_latency_ms: float
    market_slug: str
    time_to_resolution: float   # seconds until market closes
    opening_price: Optional[float]


@dataclass
class MarketResolution:
    """Summary of a single 15-minute market resolution."""
    market_slug: str
    start_timestamp: int
    end_timestamp: int
    opening_price: float
    # Spot at resolution
    spot_at_resolution: float
    spot_direction: str         # "UP" or "DOWN"
    spot_delta_from_open: float
    # Chainlink at resolution
    chainlink_at_resolution: float
    chainlink_direction: str    # "UP" or "DOWN"
    chainlink_delta_from_open: float
    chainlink_round_at_resolution: int
    chainlink_last_update_age: float  # age of last update at resolution time
    # Lag metrics
    lag_delta_at_resolution: float
    max_lag_delta_last_60s: float
    avg_lag_delta_last_60s: float
    # Direction mismatch (spot says UP but chainlink says DOWN)
    direction_match: bool
    # Would the trade have been correct?
    # (If we followed spot direction, did chainlink resolve same way?)
    spot_predicted_correctly: bool
    # Observation count
    num_observations: int
    resolution_datetime_utc: str


# --- Binance Spot Feed (lightweight, for logging only) ---

class SpotPriceFeed:
    """Lightweight Binance WebSocket feed for spot BTC/USDT."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._current_price: float = 0.0
        self._last_update: float = 0.0
        self._running = False
        self._price_buffer: list[tuple[float, float]] = []  # (timestamp, price)
        self._max_buffer = 10000

    @property
    def current_price(self) -> float:
        return self._current_price

    @property
    def is_connected(self) -> bool:
        return self._running and self._current_price > 0

    def get_price_n_seconds_ago(self, seconds: float) -> Optional[float]:
        """Get spot price approximately N seconds ago."""
        target = time.time() - seconds
        best = None
        best_diff = float("inf")
        for ts, price in self._price_buffer:
            diff = abs(ts - target)
            if diff < best_diff:
                best_diff = diff
                best = price
        if best_diff > 5.0:
            return None
        return best

    async def connect(self):
        """Connect to Binance WebSocket with auto-reconnect."""
        self._running = True
        reconnect_delay = 1.0

        while self._running:
            try:
                logger.info("Connecting to Binance: %s", self.ws_url)
                async with websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    reconnect_delay = 1.0
                    logger.info("Binance WebSocket connected")

                    async for msg in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(msg)
                            price = float(data["p"])
                            self._current_price = price
                            self._last_update = time.time()
                            self._price_buffer.append((time.time(), price))
                            if len(self._price_buffer) > self._max_buffer:
                                self._price_buffer = self._price_buffer[-self._max_buffer:]
                        except (KeyError, ValueError):
                            pass

            except ConnectionClosed:
                logger.warning("Binance WS closed")
            except Exception as e:
                logger.error("Binance WS error: %s", e)

            if self._running:
                logger.info("Reconnecting in %.1fs...", reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30.0)

    async def stop(self):
        self._running = False


# --- Market Tracker (lightweight, for logging) ---

@dataclass
class ActiveMarket:
    slug: str
    start_timestamp: int
    end_timestamp: int
    opening_price: Optional[float] = None

    @property
    def time_remaining(self) -> float:
        return max(0.0, self.end_timestamp - time.time())

    @property
    def is_active(self) -> bool:
        now = time.time()
        return self.start_timestamp <= now <= self.end_timestamp

    @property
    def is_expired(self) -> bool:
        return time.time() > self.end_timestamp


class MarketTracker:
    """Discovers active BTC 15m markets on Polymarket via Gamma API."""

    GAMMA_URL = "https://gamma-api.polymarket.com"

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self.current_market: Optional[ActiveMarket] = None

    async def start(self):
        self._session = aiohttp.ClientSession()

    async def stop(self):
        if self._session:
            await self._session.close()

    async def fetch_current_market(self) -> Optional[ActiveMarket]:
        """Find the currently active BTC 15m market."""
        if not self._session:
            return None

        try:
            url = f"{self.GAMMA_URL}/events"
            params = {"limit": 10, "active": "true", "closed": "false", "tag": "btc"}

            async with self._session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None
                events = await resp.json()

            now = time.time()
            for event in events:
                slug = event.get("slug", "")
                title = event.get("title", "").lower()

                is_btc_15m = (
                    ("btc" in slug and "15m" in slug)
                    or ("btc" in title and "15" in title)
                )
                if not is_btc_15m:
                    continue

                # Parse start timestamp from slug
                parts = slug.split("-")
                start_ts = None
                for part in reversed(parts):
                    if part.isdigit() and len(part) >= 10:
                        start_ts = int(part)
                        break

                if start_ts is None:
                    continue

                end_ts = start_ts + 900
                if start_ts <= now <= end_ts:
                    market = ActiveMarket(
                        slug=slug,
                        start_timestamp=start_ts,
                        end_timestamp=end_ts,
                    )
                    self.current_market = market
                    return market

        except Exception as e:
            logger.error("Market discovery error: %s", e)

        return None


# --- Main Lag Logger ---

class LagLogger:
    """
    Records Spot vs Chainlink price data for every active market.

    Outputs:
    1. lag_observations.csv - Per-second price pairs with lag data
    2. market_resolutions.csv - Per-market summary with resolution outcome
    """

    def __init__(self, config: LagArbitrageConfig):
        self.config = config
        self.log_config = config.logging

        # Components
        self.spot_feed = SpotPriceFeed(config.spot.ws_url)
        self.chainlink = ChainlinkOnChainReader(config.chainlink)
        self.market_tracker = MarketTracker()

        # State
        self._running = False
        self._observations: list[LagObservation] = []
        self._resolutions: list[MarketResolution] = []
        self._current_market_observations: list[LagObservation] = []
        self._markets_logged = 0
        self._target_markets: Optional[int] = None

        # File handles
        self._obs_writer = None
        self._res_writer = None
        self._obs_file = None
        self._res_file = None

    async def start(self, target_markets: Optional[int] = None):
        """Initialize all components and start logging."""
        self._target_markets = target_markets
        self._running = True

        # Create output directory
        Path(self.log_config.output_dir).mkdir(parents=True, exist_ok=True)

        # Initialize CSV files
        self._init_csv_files()

        # Connect components
        await self.market_tracker.start()
        await self.chainlink.connect()

        logger.info("=" * 60)
        logger.info("CHAINLINK LAG LOGGER STARTED")
        logger.info("=" * 60)
        logger.info("Output dir: %s", self.log_config.output_dir)
        if target_markets:
            logger.info("Target: %d market resolutions", target_markets)
        logger.info("Spot feed: Binance btcusdt@aggTrade")
        logger.info("Chainlink: Polygon on-chain (%s)", self.config.chainlink.contract_address)
        logger.info("=" * 60)

        # Run all tasks concurrently
        try:
            await asyncio.gather(
                self.spot_feed.connect(),
                self.chainlink.run_polling_loop(),
                self._market_discovery_loop(),
                self._observation_loop(),
                self._status_loop(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    def _init_csv_files(self):
        """Create CSV files with headers."""
        obs_path = os.path.join(self.log_config.output_dir, self.log_config.csv_filename)
        res_path = os.path.join(self.log_config.output_dir, self.log_config.resolutions_filename)

        obs_exists = os.path.exists(obs_path)
        res_exists = os.path.exists(res_path)

        self._obs_file = open(obs_path, "a", newline="")
        self._res_file = open(res_path, "a", newline="")

        obs_fields = [
            "timestamp", "datetime_utc", "spot_price", "chainlink_price",
            "chainlink_round_id", "chainlink_updated_at", "lag_delta",
            "lag_delta_pct", "on_chain_age_seconds", "rpc_latency_ms",
            "market_slug", "time_to_resolution", "opening_price",
        ]
        res_fields = [
            "market_slug", "start_timestamp", "end_timestamp", "opening_price",
            "spot_at_resolution", "spot_direction", "spot_delta_from_open",
            "chainlink_at_resolution", "chainlink_direction", "chainlink_delta_from_open",
            "chainlink_round_at_resolution", "chainlink_last_update_age",
            "lag_delta_at_resolution", "max_lag_delta_last_60s", "avg_lag_delta_last_60s",
            "direction_match", "spot_predicted_correctly",
            "num_observations", "resolution_datetime_utc",
        ]

        self._obs_writer = csv.DictWriter(self._obs_file, fieldnames=obs_fields)
        self._res_writer = csv.DictWriter(self._res_file, fieldnames=res_fields)

        if not obs_exists:
            self._obs_writer.writeheader()
            self._obs_file.flush()
        if not res_exists:
            self._res_writer.writeheader()
            self._res_file.flush()

    async def _market_discovery_loop(self):
        """Continuously discover and track active markets."""
        last_market_slug = None

        while self._running:
            try:
                market = await self.market_tracker.fetch_current_market()

                if market and market.slug != last_market_slug:
                    # New market detected
                    logger.info(
                        "NEW MARKET: %s (%.0fs remaining)",
                        market.slug, market.time_remaining,
                    )

                    # If we had a previous market, record its resolution
                    if last_market_slug and self._current_market_observations:
                        await self._record_resolution()

                    # Reset for new market
                    self._current_market_observations = []
                    last_market_slug = market.slug

                    # Try to get opening price from chainlink
                    if self.chainlink.latest_price is not None:
                        market.opening_price = self.chainlink.latest_price

                elif market and market.is_expired:
                    # Market just expired - record resolution
                    if self._current_market_observations:
                        await self._record_resolution()
                        self._current_market_observations = []
                        self._markets_logged += 1

                        if (
                            self._target_markets
                            and self._markets_logged >= self._target_markets
                        ):
                            logger.info(
                                "TARGET REACHED: %d markets logged. Stopping.",
                                self._markets_logged,
                            )
                            self._running = False
                            return

                    last_market_slug = None

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Market discovery error: %s", e)
                await asyncio.sleep(10)

    async def _observation_loop(self):
        """Record spot vs chainlink price pairs at regular intervals."""
        # Wait for both feeds to have data
        logger.info("Waiting for data feeds...")
        for _ in range(60):
            if self.spot_feed.is_connected and self.chainlink.latest is not None:
                break
            await asyncio.sleep(1)

        if not self.spot_feed.is_connected:
            logger.error("Spot feed not connected after 60s")
            return
        if self.chainlink.latest is None:
            logger.error("Chainlink feed not available after 60s")
            return

        logger.info("Both feeds active. Starting observations.")

        while self._running:
            try:
                market = self.market_tracker.current_market
                if market is None or not market.is_active:
                    await asyncio.sleep(1)
                    continue

                # Determine sampling interval
                time_remaining = market.time_remaining
                if time_remaining <= self.log_config.detailed_window_seconds:
                    interval = self.log_config.detailed_interval_seconds
                else:
                    interval = self.log_config.sample_interval_seconds

                # Record observation
                obs = self._create_observation(market)
                if obs:
                    self._current_market_observations.append(obs)
                    self._write_observation(obs)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Observation error: %s", e)
                await asyncio.sleep(1)

    def _create_observation(self, market: ActiveMarket) -> Optional[LagObservation]:
        """Create a lag observation from current feed data."""
        spot = self.spot_feed.current_price
        cl = self.chainlink.latest

        if spot <= 0 or cl is None:
            return None

        now = time.time()
        lag_delta = spot - cl.price
        lag_delta_pct = (lag_delta / spot) * 100 if spot > 0 else 0
        on_chain_age = now - cl.updated_at

        return LagObservation(
            timestamp=now,
            datetime_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            spot_price=round(spot, 2),
            chainlink_price=round(cl.price, 2),
            chainlink_round_id=cl.round_id,
            chainlink_updated_at=cl.updated_at,
            lag_delta=round(lag_delta, 2),
            lag_delta_pct=round(lag_delta_pct, 4),
            on_chain_age_seconds=round(on_chain_age, 1),
            rpc_latency_ms=round(cl.rpc_latency_ms, 1),
            market_slug=market.slug,
            time_to_resolution=round(market.time_remaining, 1),
            opening_price=market.opening_price,
        )

    def _write_observation(self, obs: LagObservation):
        """Write observation to CSV."""
        if self._obs_writer:
            self._obs_writer.writerow(asdict(obs))
            self._obs_file.flush()

    async def _record_resolution(self):
        """Record the summary of a market resolution."""
        market = self.market_tracker.current_market
        if not market or not self._current_market_observations:
            return

        observations = self._current_market_observations
        last_obs = observations[-1]

        # Get opening price (from first observation or market data)
        opening_price = market.opening_price
        if opening_price is None and observations:
            opening_price = observations[0].chainlink_price

        if opening_price is None or opening_price <= 0:
            logger.warning("No opening price for market %s, skipping resolution", market.slug)
            return

        # Spot at resolution
        spot_at_res = last_obs.spot_price
        spot_direction = "UP" if spot_at_res > opening_price else "DOWN"
        spot_delta = spot_at_res - opening_price

        # Chainlink at resolution
        cl_at_res = last_obs.chainlink_price
        cl_direction = "UP" if cl_at_res > opening_price else "DOWN"
        cl_delta = cl_at_res - opening_price

        # Lag in last 60 seconds
        cutoff_60s = market.end_timestamp - 60
        recent_obs = [o for o in observations if o.timestamp >= cutoff_60s]
        lag_deltas_60s = [abs(o.lag_delta) for o in recent_obs] if recent_obs else [0]
        max_lag_60s = max(lag_deltas_60s)
        avg_lag_60s = sum(lag_deltas_60s) / len(lag_deltas_60s)

        resolution = MarketResolution(
            market_slug=market.slug,
            start_timestamp=market.start_timestamp,
            end_timestamp=market.end_timestamp,
            opening_price=round(opening_price, 2),
            spot_at_resolution=round(spot_at_res, 2),
            spot_direction=spot_direction,
            spot_delta_from_open=round(spot_delta, 2),
            chainlink_at_resolution=round(cl_at_res, 2),
            chainlink_direction=cl_direction,
            chainlink_delta_from_open=round(cl_delta, 2),
            chainlink_round_at_resolution=last_obs.chainlink_round_id,
            chainlink_last_update_age=last_obs.on_chain_age_seconds,
            lag_delta_at_resolution=round(last_obs.lag_delta, 2),
            max_lag_delta_last_60s=round(max_lag_60s, 2),
            avg_lag_delta_last_60s=round(avg_lag_60s, 2),
            direction_match=(spot_direction == cl_direction),
            spot_predicted_correctly=(spot_direction == cl_direction),
            num_observations=len(observations),
            resolution_datetime_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )

        # Write to CSV
        if self._res_writer:
            self._res_writer.writerow(asdict(resolution))
            self._res_file.flush()

        self._resolutions.append(resolution)

        # Log summary
        logger.info("=" * 60)
        logger.info("MARKET RESOLVED: %s", market.slug)
        logger.info(
            "  Opening: $%.2f | Spot: $%.2f (%s) | Chainlink: $%.2f (%s)",
            opening_price, spot_at_res, spot_direction, cl_at_res, cl_direction,
        )
        logger.info(
            "  Lag at resolution: $%.2f | Max lag (60s): $%.2f | Avg lag (60s): $%.2f",
            last_obs.lag_delta, max_lag_60s, avg_lag_60s,
        )
        logger.info(
            "  Direction match: %s | Chainlink age: %.1fs",
            resolution.direction_match, last_obs.on_chain_age_seconds,
        )
        logger.info("  Markets logged: %d", self._markets_logged + 1)
        logger.info("=" * 60)

    async def _status_loop(self):
        """Print periodic status updates."""
        while self._running:
            try:
                spot = self.spot_feed.current_price
                cl = self.chainlink.latest
                market = self.market_tracker.current_market

                if spot > 0 and cl:
                    lag = spot - cl.price
                    cl_age = time.time() - cl.updated_at
                    market_info = ""
                    if market and market.is_active:
                        market_info = f" | Market: {market.slug} T-{market.time_remaining:.0f}s"

                    logger.info(
                        "SPOT: $%.2f | CHAINLINK: $%.2f | LAG: $%.2f | CL_AGE: %.1fs%s",
                        spot, cl.price, lag, cl_age, market_info,
                    )
                else:
                    connected = []
                    if spot > 0:
                        connected.append("spot")
                    if cl:
                        connected.append("chainlink")
                    logger.info("Waiting for feeds... Active: %s", connected or "none")

                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(10)

    async def stop(self):
        """Clean shutdown."""
        self._running = False

        # Record final resolution if needed
        if self._current_market_observations:
            await self._record_resolution()

        await self.spot_feed.stop()
        await self.chainlink.stop()
        await self.market_tracker.stop()

        if self._obs_file:
            self._obs_file.close()
        if self._res_file:
            self._res_file.close()

        # Print final summary
        self._print_summary()

    def _print_summary(self):
        """Print summary statistics."""
        logger.info("\n" + "=" * 60)
        logger.info("LAG LOGGER SESSION SUMMARY")
        logger.info("=" * 60)
        logger.info("Markets observed: %d", len(self._resolutions))

        if not self._resolutions:
            logger.info("No complete market resolutions recorded.")
            return

        # Lag statistics
        lag_deltas = [abs(r.lag_delta_at_resolution) for r in self._resolutions]
        max_lags = [r.max_lag_delta_last_60s for r in self._resolutions]
        avg_lags = [r.avg_lag_delta_last_60s for r in self._resolutions]

        logger.info("\nLag at Resolution:")
        logger.info("  Mean: $%.2f", sum(lag_deltas) / len(lag_deltas))
        logger.info("  Max:  $%.2f", max(lag_deltas))
        logger.info("  Min:  $%.2f", min(lag_deltas))

        logger.info("\nMax Lag in Last 60s:")
        logger.info("  Mean: $%.2f", sum(max_lags) / len(max_lags))
        logger.info("  Max:  $%.2f", max(max_lags))

        # Direction match rate
        matches = sum(1 for r in self._resolutions if r.direction_match)
        logger.info(
            "\nDirection Match Rate: %d/%d (%.1f%%)",
            matches, len(self._resolutions),
            100 * matches / len(self._resolutions),
        )

        # Chainlink age at resolution
        ages = [r.chainlink_last_update_age for r in self._resolutions]
        logger.info("\nChainlink Age at Resolution:")
        logger.info("  Mean: %.1fs", sum(ages) / len(ages))
        logger.info("  Max:  %.1fs", max(ages))

        logger.info("\nData saved to: %s/", self.log_config.output_dir)
        logger.info("=" * 60)


def parse_duration(s: str) -> Optional[int]:
    """Parse duration string like '24h', '2d', '30m' to seconds."""
    s = s.strip().lower()
    if s.endswith("h"):
        return int(float(s[:-1]) * 3600)
    elif s.endswith("d"):
        return int(float(s[:-1]) * 86400)
    elif s.endswith("m"):
        return int(float(s[:-1]) * 60)
    elif s.endswith("s"):
        return int(float(s[:-1]))
    return None


async def main():
    parser = argparse.ArgumentParser(
        description="Chainlink Lag Logger - Record spot vs on-chain prices"
    )
    parser.add_argument(
        "--markets", type=int, default=None,
        help="Stop after logging N market resolutions (default: run indefinitely)",
    )
    parser.add_argument(
        "--duration", type=str, default=None,
        help="Run for a duration, e.g. '24h', '2d', '30m' (default: indefinite)",
    )
    parser.add_argument(
        "--output-dir", type=str, default="lag_data",
        help="Output directory for CSV files (default: lag_data)",
    )
    parser.add_argument(
        "--sample-interval", type=float, default=1.0,
        help="Observation interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--chainlink-interval", type=float, default=2.0,
        help="Chainlink polling interval in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--rpc-url", type=str, default=None,
        help="Custom Polygon RPC URL (default: public endpoints)",
    )

    args = parser.parse_args()

    # Build config
    config = LagArbitrageConfig()
    config.logging.output_dir = args.output_dir
    config.logging.sample_interval_seconds = args.sample_interval
    config.chainlink.poll_interval_seconds = args.chainlink_interval

    if args.rpc_url:
        config.chainlink.rpc_urls = [args.rpc_url] + config.chainlink.rpc_urls

    # Create logger
    lag_logger = LagLogger(config)

    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(lag_logger.stop()))

    # Duration timeout
    if args.duration:
        duration_seconds = parse_duration(args.duration)
        if duration_seconds:
            logger.info("Will run for %s (%d seconds)", args.duration, duration_seconds)
            asyncio.get_event_loop().call_later(
                duration_seconds,
                lambda: asyncio.create_task(lag_logger.stop()),
            )

    await lag_logger.start(target_markets=args.markets)


if __name__ == "__main__":
    asyncio.run(main())
