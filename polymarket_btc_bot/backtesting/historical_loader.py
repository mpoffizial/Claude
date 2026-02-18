"""
Historical Data Loader: Fetches and stores historical BTC price data
from Binance and historical trade data from Polymarket for backtesting.
Stores data in SQLite for fast querying.
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import aiohttp

from polymarket_btc_bot.config import BacktestConfig, PolymarketConfig

logger = logging.getLogger(__name__)


@dataclass
class HistoricalKline:
    timestamp: int          # Unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class HistoricalTrade:
    trade_id: str
    market_id: str
    price: float
    size: float
    side: str
    timestamp: int


class HistoricalLoader:
    """Loads and caches historical data for backtesting."""

    def __init__(self, config: BacktestConfig, poly_config: Optional[PolymarketConfig] = None):
        self.config = config
        self.poly_config = poly_config
        self._db_path = os.path.join(config.data_dir, "backtest.db")
        self._session: Optional[aiohttp.ClientSession] = None

        os.makedirs(config.data_dir, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database schema."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS btc_klines (
                timestamp INTEGER PRIMARY KEY,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS polymarket_trades (
                trade_id TEXT PRIMARY KEY,
                market_id TEXT,
                price REAL,
                size REAL,
                side TEXT,
                timestamp INTEGER
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS markets (
                condition_id TEXT PRIMARY KEY,
                slug TEXT,
                start_timestamp INTEGER,
                end_timestamp INTEGER,
                up_token_id TEXT,
                down_token_id TEXT,
                resolution TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_klines_ts ON btc_klines(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_market ON polymarket_trades(market_id, timestamp)
        """)

        conn.commit()
        conn.close()

    async def start(self):
        self._session = aiohttp.ClientSession()

    async def stop(self):
        if self._session:
            await self._session.close()

    async def fetch_binance_klines(
        self,
        start_time: int,
        end_time: int,
        interval: str = "1s",
    ) -> list[HistoricalKline]:
        """
        Fetch historical kline data from Binance.

        Args:
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds
            interval: Kline interval (1s, 1m, etc.)

        Returns:
            List of HistoricalKline objects
        """
        if not self._session:
            raise RuntimeError("Loader not started")

        url = "https://api.binance.com/api/v3/klines"
        all_klines = []
        current_start = start_time

        while current_start < end_time:
            params = {
                "symbol": "BTCUSDT",
                "interval": interval,
                "startTime": current_start,
                "endTime": min(current_start + 1000 * 60000, end_time),  # Batch
                "limit": 1000,
            }

            try:
                async with self._session.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 429:
                        logger.warning("Binance rate limit hit, waiting 60s")
                        await asyncio.sleep(60)
                        continue
                    if resp.status != 200:
                        logger.error("Binance klines returned %d", resp.status)
                        break

                    data = await resp.json()
                    if not data:
                        break

                    for k in data:
                        kline = HistoricalKline(
                            timestamp=int(k[0]),
                            open=float(k[1]),
                            high=float(k[2]),
                            low=float(k[3]),
                            close=float(k[4]),
                            volume=float(k[5]),
                        )
                        all_klines.append(kline)

                    current_start = int(data[-1][0]) + 1

                    # Rate limit: be nice to Binance
                    await asyncio.sleep(0.5)

            except Exception as e:
                logger.error("Binance kline fetch error: %s", e)
                await asyncio.sleep(5)

        logger.info("Fetched %d klines from Binance", len(all_klines))
        return all_klines

    async def fetch_polymarket_trades(
        self,
        condition_id: str,
        limit: int = 1000,
    ) -> list[HistoricalTrade]:
        """Fetch historical trades for a Polymarket market."""
        if not self._session:
            raise RuntimeError("Loader not started")

        poly_url = self.poly_config.clob_rest_url if self.poly_config else "https://clob.polymarket.com"
        url = f"{poly_url}/trades"
        params = {
            "market": condition_id,
            "limit": limit,
        }

        try:
            async with self._session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    logger.error("Polymarket trades returned %d", resp.status)
                    return []

                data = await resp.json()
                trades = []

                for t in data if isinstance(data, list) else data.get("data", []):
                    trade = HistoricalTrade(
                        trade_id=t.get("id", ""),
                        market_id=condition_id,
                        price=float(t.get("price", 0)),
                        size=float(t.get("size", 0)),
                        side=t.get("side", ""),
                        timestamp=int(t.get("timestamp", 0)),
                    )
                    trades.append(trade)

                logger.info("Fetched %d trades for market %s", len(trades), condition_id[:16])
                return trades

        except Exception as e:
            logger.error("Polymarket trades fetch error: %s", e)
            return []

    def save_klines(self, klines: list[HistoricalKline]):
        """Save klines to SQLite database."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        cursor.executemany(
            "INSERT OR REPLACE INTO btc_klines VALUES (?, ?, ?, ?, ?, ?)",
            [(k.timestamp, k.open, k.high, k.low, k.close, k.volume) for k in klines],
        )

        conn.commit()
        conn.close()
        logger.info("Saved %d klines to database", len(klines))

    def save_trades(self, trades: list[HistoricalTrade]):
        """Save trades to SQLite database."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        cursor.executemany(
            "INSERT OR REPLACE INTO polymarket_trades VALUES (?, ?, ?, ?, ?, ?)",
            [(t.trade_id, t.market_id, t.price, t.size, t.side, t.timestamp) for t in trades],
        )

        conn.commit()
        conn.close()
        logger.info("Saved %d trades to database", len(trades))

    def load_klines(
        self,
        start_time: int,
        end_time: int,
    ) -> list[HistoricalKline]:
        """Load klines from database."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM btc_klines WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start_time, end_time),
        )

        klines = [
            HistoricalKline(
                timestamp=row[0],
                open=row[1],
                high=row[2],
                low=row[3],
                close=row[4],
                volume=row[5],
            )
            for row in cursor.fetchall()
        ]

        conn.close()
        return klines

    def load_trades(self, market_id: str) -> list[HistoricalTrade]:
        """Load trades for a specific market from database."""
        conn = sqlite3.connect(self._db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM polymarket_trades WHERE market_id = ? ORDER BY timestamp",
            (market_id,),
        )

        trades = [
            HistoricalTrade(
                trade_id=row[0],
                market_id=row[1],
                price=row[2],
                size=row[3],
                side=row[4],
                timestamp=row[5],
            )
            for row in cursor.fetchall()
        ]

        conn.close()
        return trades

    async def collect_data(self, days: int = 30):
        """
        Collect historical data for backtesting.

        Args:
            days: Number of days of historical data to collect
        """
        logger.info("Starting data collection for %d days", days)

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (days * 24 * 60 * 60 * 1000)

        # Fetch Binance klines in 1-minute intervals (more practical than 1s)
        logger.info("Fetching Binance 1m klines...")
        klines = await self.fetch_binance_klines(start_ms, now_ms, "1m")
        if klines:
            self.save_klines(klines)

        logger.info("Data collection complete. %d klines stored.", len(klines))
