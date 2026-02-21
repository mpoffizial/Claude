"""
Maker Bot: Top-level async orchestrator for the Polymarket Maker-Rebate strategy.

Responsibilities:
  - Initialise all components (CLOB feed, market discovery, order manager)
  - Discover active 5m/15m BTC markets and switch between them
  - Route live orderbook ticks to the active MarketMakerStrategy
  - Run a monitoring loop that processes fills, enforces risk limits,
    and periodically prints statistics
  - Gracefully cancel all orders and shut down on SIGINT/SIGTERM

Component map:
  MarketDiscovery   → finds active markets via Gamma API
  PolymarketCLOB    → WebSocket orderbook feed (shared, resubscribed per market)
  OrderManager      → places / cancels GTC and FOK orders on the CLOB
  PricingEngine     → shared; stateless per-tick quote calculator
  InventoryManager  → shared; tracks Yes/No token inventory across markets
  RebateTracker     → shared; accumulates P&L stats for the session
  MarketMakerStrategy → per-market; quote lifecycle logic
"""

import asyncio
import logging
import time
from typing import Optional

from polymarket_btc_bot.config import ExecutionConfig, PolymarketConfig, TradingMode
from polymarket_btc_bot.data.market_discovery import MarketDiscovery, MarketInfo
from polymarket_btc_bot.data.polymarket_clob import MarketOrderbook, PolymarketCLOB
from polymarket_btc_bot.execution.order_manager import OrderManager
from polymarket_btc_bot.maker.config import MakerBotConfig
from polymarket_btc_bot.maker.inventory_manager import InventoryManager
from polymarket_btc_bot.maker.pricing_engine import PricingEngine
from polymarket_btc_bot.maker.rebate_tracker import RebateTracker
from polymarket_btc_bot.maker.strategy import MarketMakerStrategy

logger = logging.getLogger(__name__)


class MakerBot:
    """
    Polymarket Maker-Rebate Bot.

    Typical usage::

        config = MakerBotConfig.from_env()
        bot = MakerBot(config)
        asyncio.run(bot.run())
    """

    # Print statistics every N seconds
    _STATS_INTERVAL = 30.0

    def __init__(self, config: MakerBotConfig):
        self.config = config

        # Build sub-configs that the existing modules expect
        self._poly_cfg = PolymarketConfig(
            clob_ws_url=config.clob_ws_url,
            clob_rest_url=config.clob_rest_url,
            gamma_api_url=config.gamma_api_url,
            chain_id=config.chain_id,
            private_key=config.private_key,
            api_key=config.api_key,
            api_secret=config.api_secret,
            api_passphrase=config.api_passphrase,
            proxy_wallet=config.proxy_wallet,
        )
        exec_cfg = ExecutionConfig(
            max_orders_per_minute=config.risk.max_orders_per_minute,
        )
        mode = TradingMode.SIMULATION if config.simulation_mode else TradingMode.LIVE

        # --- Core infrastructure components ---
        self._discovery = MarketDiscovery(self._poly_cfg)
        self._clob = PolymarketCLOB(self._poly_cfg)
        self._order_mgr = OrderManager(self._poly_cfg, exec_cfg, mode)

        # --- Maker-specific components (shared across market instances) ---
        self._pricing = PricingEngine(config.quote, config.arb)
        self._inventory = InventoryManager(config.inventory)
        self._rebate = RebateTracker(config.rebate)

        # --- Runtime state ---
        self._strategy: Optional[MarketMakerStrategy] = None
        self._current_market: Optional[MarketInfo] = None
        self._running = False
        self._start_time = 0.0
        self._last_stats_print = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all components."""
        self._start_time = time.time()
        self._last_stats_print = self._start_time

        logger.info("=" * 62)
        logger.info("  Polymarket Maker-Rebate Bot")
        logger.info("  Mode     : %s", "SIMULATION" if self.config.simulation_mode else "LIVE")
        logger.info("  QuoteSize: $%.2f  HalfSpread: %.4f",
                    self.config.quote.quote_size_usdc,
                    self.config.quote.default_half_spread)
        logger.info("  MaxExposure: $%.2f  MaxDailyLoss: $%.2f",
                    self.config.inventory.max_net_exposure_usdc,
                    self.config.risk.max_daily_loss_usdc)
        logger.info("  Micro-arb: %s (threshold=%.3f)",
                    "enabled" if self.config.arb.enabled else "disabled",
                    self.config.arb.threshold)
        logger.info("=" * 62)

        await self._discovery.start()
        await self._order_mgr.start()
        self._running = True

    async def stop(self) -> None:
        """Gracefully cancel all orders and shut down."""
        logger.info("Stopping Maker Bot...")
        self._running = False

        if self._strategy:
            await self._strategy.shutdown()
            self._strategy = None

        await self._clob.stop()
        await self._order_mgr.stop()
        await self._discovery.stop()

        self._print_stats()
        logger.info("Maker Bot stopped.")

    async def run(self) -> None:
        """
        Main entry point.  Runs all async loops concurrently until
        cancelled or a hard risk limit is reached.
        """
        await self.start()
        try:
            await asyncio.gather(
                self._market_discovery_loop(),
                self._clob_feed_loop(),
                self._monitoring_loop(),
            )
        except asyncio.CancelledError:
            logger.info("Bot tasks cancelled – shutting down")
        except Exception:
            logger.exception("Fatal error in MakerBot.run()")
        finally:
            await self.stop()

    # ------------------------------------------------------------------
    # Market discovery loop
    # ------------------------------------------------------------------

    async def _market_discovery_loop(self) -> None:
        """
        Poll the Gamma API every 5 seconds.

        - On new active market: switch the strategy to it.
        - On market expiry:     clean up and clear inventory.
        """
        while self._running:
            try:
                market = await self._discovery.discover_current_market()

                if market is not None and market != self._current_market:
                    await self._switch_market(market)

                # Detect expiry of the current market
                if self._current_market and self._current_market.is_expired:
                    logger.info(
                        "Market expired: %s", self._current_market.market_slug
                    )
                    await self._on_market_expired()

            except Exception:
                logger.exception("Error in market discovery loop")

            await asyncio.sleep(5)

    async def _switch_market(self, market: MarketInfo) -> None:
        """Tear down the old strategy and start a new one for `market`."""
        if self._strategy:
            old_slug = self._current_market.market_slug if self._current_market else "?"
            logger.info("Switching market: %s → %s", old_slug, market.market_slug)
            await self._strategy.shutdown()
            self._strategy = None

        self._current_market = market

        # Re-subscribe the CLOB WebSocket to the new market's tokens
        self._clob.set_market(market.up_token_id, market.down_token_id)

        self._strategy = MarketMakerStrategy(
            config=self.config,
            market=market,
            order_manager=self._order_mgr,
            pricing=self._pricing,
            inventory=self._inventory,
            rebate_tracker=self._rebate,
        )

        logger.info(
            "Market-making active: %s | Up=%s… Down=%s… | %.0fs remaining",
            market.market_slug,
            market.up_token_id[:8],
            market.down_token_id[:8],
            market.time_remaining,
        )

    async def _on_market_expired(self) -> None:
        """Handle market settlement: cancel orders, clear inventory."""
        if self._strategy:
            await self._strategy.shutdown()
            self._strategy = None

        # Clear inventory so the next market starts balanced.
        # Production: wait for on-chain settlement confirmation first.
        self._inventory.clear_market_inventory()
        self._current_market = None

    # ------------------------------------------------------------------
    # CLOB WebSocket feed loop
    # ------------------------------------------------------------------

    async def _clob_feed_loop(self) -> None:
        """
        Register the orderbook callback and maintain the WebSocket
        connection (reconnects automatically on disconnect).
        """
        self._clob.on_update(self._on_orderbook_update)
        await self._clob.connect()

    async def _on_orderbook_update(self, market_ob: MarketOrderbook) -> None:
        """Route a live orderbook update to the active strategy."""
        if self._strategy and self._running:
            try:
                await self._strategy.on_orderbook_update(market_ob)
            except Exception:
                logger.exception("Unhandled error in strategy.on_orderbook_update")

    # ------------------------------------------------------------------
    # Monitoring loop
    # ------------------------------------------------------------------

    async def _monitoring_loop(self) -> None:
        """
        Runs every `loop_interval_seconds`:
          - Detect and process filled orders.
          - Enforce daily loss limit → halt if breached.
          - Print periodic statistics.
        """
        while self._running:
            await asyncio.sleep(self.config.loop_interval_seconds)

            # --- Process fills ---
            if self._strategy:
                filled = self._order_mgr.get_filled_orders()
                if filled:
                    self._strategy.process_fill_events(filled)
                    self._order_mgr.clear_history()

            # --- Daily loss guard ---
            net_pnl = self._rebate.stats.net_pnl_usdc
            if net_pnl < self.config.risk.max_daily_loss_usdc:
                logger.critical(
                    "DAILY LOSS LIMIT REACHED: $%.4f (limit: $%.2f) – halting.",
                    net_pnl, self.config.risk.max_daily_loss_usdc,
                )
                self._running = False
                break

            # --- Total inventory guard ---
            total_inv = self._inventory.state.total_inventory_usdc
            if total_inv > self.config.risk.max_total_inventory_usdc:
                logger.warning(
                    "Total inventory $%.2f exceeds limit $%.2f – suppressing new quotes",
                    total_inv, self.config.risk.max_total_inventory_usdc,
                )

            # --- Periodic stats print ---
            now = time.time()
            if now - self._last_stats_print >= self._STATS_INTERVAL:
                self._print_stats()
                self._last_stats_print = now

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _print_stats(self) -> None:
        """Emit current P&L and inventory to the log."""
        stats = self._rebate.get_session_stats()
        inv = self._inventory.get_summary()
        runtime = time.time() - self._start_time
        market_slug = self._current_market.market_slug if self._current_market else "none"

        logger.info("─" * 62)
        logger.info("  MAKER BOT  runtime=%.0fs  market=%s", runtime, market_slug)
        logger.info(
            "  Fills: %d  buy=%d  sell=%d  arb=%d",
            stats["total_fills"], stats["buy_fills"],
            stats["sell_fills"], stats["arb_trades"],
        )
        logger.info("  Volume:  $%-.4f", stats["total_volume_usdc"])
        logger.info(
            "  Spread:  $%-.4f   Rebate: $%-.4f   Arb: $%-.4f",
            stats["spread_income_usdc"],
            stats["rebate_income_usdc"],
            stats["arb_income_usdc"],
        )
        logger.info(
            "  Adverse: -$%-.4f   Net P&L: $%-.4f",
            stats["adverse_selection_usdc"],
            stats["net_pnl_usdc"],
        )
        logger.info(
            "  Inventory: Yes=$%.2f  No=$%.2f  Net=%+.2f",
            inv["yes_inventory_usdc"],
            inv["no_inventory_usdc"],
            inv["net_exposure_usdc"],
        )
        logger.info(
            "  Rebate yield: %.4f%%   Spread yield: %.4f%%",
            stats["rebate_yield_pct"],
            stats["spread_yield_pct"],
        )
        logger.info("─" * 62)
