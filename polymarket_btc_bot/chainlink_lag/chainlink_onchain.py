"""
On-chain Chainlink BTC/USD price reader via Polygon RPC.

Reads directly from the Chainlink Aggregator contract on Polygon
to get the exact same price that Polymarket uses for settlement.

Contract: 0xc907E116054Ad103354f2D350FD2514433D57F6F (Chainlink BTC/USD on Polygon)
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.exceptions import Web3Exception

from polymarket_btc_bot.chainlink_lag.config import ChainlinkOnChainConfig

logger = logging.getLogger(__name__)

# Chainlink AggregatorV3Interface ABI (minimal, only what we need)
AGGREGATOR_ABI = [
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [
            {"internalType": "uint8", "name": "", "type": "uint8"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [{"internalType": "uint80", "name": "_roundId", "type": "uint80"}],
        "name": "getRoundData",
        "outputs": [
            {"internalType": "uint80", "name": "roundId", "type": "uint80"},
            {"internalType": "int256", "name": "answer", "type": "int256"},
            {"internalType": "uint256", "name": "startedAt", "type": "uint256"},
            {"internalType": "uint256", "name": "updatedAt", "type": "uint256"},
            {"internalType": "uint80", "name": "answeredInRound", "type": "uint80"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


@dataclass
class OnChainPrice:
    """A single Chainlink on-chain price observation."""
    price: float               # USD price
    round_id: int              # Chainlink round ID
    updated_at: int            # On-chain updatedAt timestamp (unix)
    started_at: int            # On-chain startedAt timestamp
    fetched_at: float          # Local time when we fetched this
    rpc_latency_ms: float      # How long the RPC call took


class ChainlinkOnChainReader:
    """
    Reads Chainlink BTC/USD price directly from Polygon.

    This gives us the exact price Polymarket uses for settlement,
    including the on-chain `updatedAt` timestamp which reveals the lag.
    """

    def __init__(self, config: ChainlinkOnChainConfig):
        self.config = config
        self._w3: Optional[AsyncWeb3] = None
        self._contract = None
        self._decimals: Optional[int] = None
        self._running = False
        self._current_rpc_index = 0

        # Price state
        self._latest: Optional[OnChainPrice] = None
        self._price_history: list[OnChainPrice] = []
        self._max_history = 5000

        # Callbacks for price updates
        self._callbacks: list = []

    @property
    def latest(self) -> Optional[OnChainPrice]:
        return self._latest

    @property
    def latest_price(self) -> Optional[float]:
        return self._latest.price if self._latest else None

    @property
    def latest_round_id(self) -> Optional[int]:
        return self._latest.round_id if self._latest else None

    @property
    def last_on_chain_update(self) -> Optional[int]:
        """Unix timestamp of last on-chain price update."""
        return self._latest.updated_at if self._latest else None

    @property
    def on_chain_lag_seconds(self) -> Optional[float]:
        """Seconds since last on-chain update."""
        if self._latest is None:
            return None
        return time.time() - self._latest.updated_at

    @property
    def history(self) -> list[OnChainPrice]:
        return self._price_history

    def on_update(self, callback):
        """Register callback for new round detection."""
        self._callbacks.append(callback)

    async def connect(self):
        """Connect to Polygon RPC and initialize contract."""
        for i, rpc_url in enumerate(self.config.rpc_urls):
            try:
                w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
                if await w3.is_connected():
                    self._w3 = w3
                    self._current_rpc_index = i
                    self._contract = w3.eth.contract(
                        address=AsyncWeb3.to_checksum_address(self.config.contract_address),
                        abi=AGGREGATOR_ABI,
                    )
                    # Fetch decimals
                    self._decimals = await self._contract.functions.decimals().call()
                    logger.info(
                        "Connected to Polygon RPC: %s (decimals: %d)",
                        rpc_url, self._decimals,
                    )
                    return
            except Exception as e:
                logger.warning("Failed to connect to %s: %s", rpc_url, e)
                continue

        raise ConnectionError("Could not connect to any Polygon RPC endpoint")

    async def _rotate_rpc(self):
        """Switch to next RPC endpoint on failure."""
        self._current_rpc_index = (self._current_rpc_index + 1) % len(self.config.rpc_urls)
        rpc_url = self.config.rpc_urls[self._current_rpc_index]
        try:
            self._w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
            self._contract = self._w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(self.config.contract_address),
                abi=AGGREGATOR_ABI,
            )
            logger.info("Rotated to RPC: %s", rpc_url)
        except Exception as e:
            logger.error("RPC rotation failed: %s", e)

    async def fetch_latest(self) -> Optional[OnChainPrice]:
        """Fetch latest round data from on-chain."""
        if not self._contract:
            raise RuntimeError("Not connected. Call connect() first.")

        t_start = time.monotonic()
        try:
            result = await self._contract.functions.latestRoundData().call()
            rpc_latency = (time.monotonic() - t_start) * 1000

            round_id, answer, started_at, updated_at, _ = result
            decimals = self._decimals or self.config.decimals
            price = answer / (10 ** decimals)

            observation = OnChainPrice(
                price=price,
                round_id=round_id,
                updated_at=updated_at,
                started_at=started_at,
                fetched_at=time.time(),
                rpc_latency_ms=rpc_latency,
            )

            # Detect new round
            is_new_round = (
                self._latest is None
                or observation.round_id != self._latest.round_id
            )

            self._latest = observation
            self._price_history.append(observation)
            if len(self._price_history) > self._max_history:
                self._price_history = self._price_history[-self._max_history:]

            # Notify callbacks on new round
            if is_new_round:
                logger.info(
                    "New Chainlink round %d: $%.2f (updated_at: %d, lag: %.1fs)",
                    round_id, price, updated_at, time.time() - updated_at,
                )
                for cb in self._callbacks:
                    try:
                        result = cb(observation)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error("Chainlink callback error: %s", e)

            return observation

        except Web3Exception as e:
            logger.error("Web3 error fetching Chainlink: %s", e)
            await self._rotate_rpc()
            return None
        except Exception as e:
            logger.error("Error fetching Chainlink price: %s", e)
            return None

    async def run_polling_loop(self):
        """Continuously poll Chainlink on-chain price."""
        self._running = True
        logger.info(
            "Starting Chainlink on-chain polling (interval: %.1fs)",
            self.config.poll_interval_seconds,
        )

        while self._running:
            try:
                await self.fetch_latest()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Polling loop error: %s", e)

            await asyncio.sleep(self.config.poll_interval_seconds)

    async def stop(self):
        """Stop polling."""
        self._running = False
        logger.info("Chainlink on-chain reader stopped")

    def get_price_at_time(self, target_time: float, tolerance: float = 30.0) -> Optional[OnChainPrice]:
        """Find the on-chain price closest to a target timestamp."""
        if not self._price_history:
            return None

        best = min(self._price_history, key=lambda p: abs(p.fetched_at - target_time))
        if abs(best.fetched_at - target_time) > tolerance:
            return None
        return best
