"""
Polymarket BTC 15-Minute Trading Bot - Main Orchestrator

Coordinates all modules:
- Market discovery
- Data feeds (Binance, Polymarket CLOB, Chainlink)
- Strategy evaluation
- Order execution
- Risk management
- Monitoring dashboard

Usage:
    python -m polymarket_btc_bot.main --mode simulation
    python -m polymarket_btc_bot.main --mode paper --size 10
    python -m polymarket_btc_bot.main --mode live --size 50
    python -m polymarket_btc_bot.main --backtest --days 30
    python -m polymarket_btc_bot.main --optimize --days 30
"""

import argparse
import asyncio
import logging
import signal
import sys
import time
from typing import Optional

from polymarket_btc_bot.config import BotConfig, TradingMode
from polymarket_btc_bot.data.binance_feed import BinanceFeed
from polymarket_btc_bot.data.polymarket_clob import PolymarketCLOB
from polymarket_btc_bot.data.chainlink_feed import ChainlinkFeed
from polymarket_btc_bot.data.market_discovery import MarketDiscovery, MarketInfo
from polymarket_btc_bot.strategy.signal_aggregator import SignalAggregator, TradeAction
from polymarket_btc_bot.strategy.market_maker import MarketMaker
from polymarket_btc_bot.execution.order_manager import OrderManager, OrderSide, OrderStatus, OrderType
from polymarket_btc_bot.execution.position_tracker import PositionTracker
from polymarket_btc_bot.execution.risk_manager import RiskManager
from polymarket_btc_bot.monitoring.logger import setup_logging, TradeLogger
from polymarket_btc_bot.monitoring.dashboard import Dashboard

logger = logging.getLogger(__name__)


class TradingBot:
    """Main trading bot orchestrator."""

    def __init__(self, config: BotConfig):
        self.config = config
        self._running = False
        self._current_market: Optional[MarketInfo] = None

        # Initialize components
        self.binance = BinanceFeed(
            config.binance,
            lookback_seconds=config.strategy.momentum_lookback_seconds,
        )
        self.clob = PolymarketCLOB(config.polymarket)
        self.chainlink = ChainlinkFeed(config.chainlink)
        self.discovery = MarketDiscovery(config.polymarket)

        self.aggregator = SignalAggregator(config.strategy, config.risk)
        self.market_maker = MarketMaker(config.market_maker, config.risk)
        self.order_manager = OrderManager(config.polymarket, config.execution, config.mode)
        self.position_tracker = PositionTracker(config.risk)
        self.risk_manager = RiskManager(config.risk, config.execution, self.position_tracker)

        self.trade_logger: Optional[TradeLogger] = None
        self.dashboard: Optional[Dashboard] = None

    async def start(self):
        """Initialize all components and start the trading loop."""
        self.trade_logger = setup_logging(self.config.monitoring)

        logger.info("=" * 60)
        logger.info("Polymarket BTC 15m Trading Bot Starting")
        logger.info("Mode: %s | Trade Size: $%.2f", self.config.mode.value, self.config.trade_size)
        logger.info("=" * 60)

        # Start data sources
        await self.discovery.start()
        await self.chainlink.start()
        await self.order_manager.start()

        # Initialize dashboard
        self.dashboard = Dashboard(
            self.config,
            self.binance,
            self.clob,
            self.position_tracker,
            self.risk_manager,
        )

        self._running = True

        # Start all async tasks
        tasks = [
            asyncio.create_task(self.binance.connect(), name="binance"),
            asyncio.create_task(self.chainlink.run_polling_loop(), name="chainlink"),
            asyncio.create_task(self._market_loop(), name="market_loop"),
            asyncio.create_task(self._trading_loop(), name="trading_loop"),
            asyncio.create_task(self._market_maker_loop(), name="market_maker_loop"),
            asyncio.create_task(self.dashboard.run(), name="dashboard"),
        ]

        logger.info("All tasks started. Waiting for data feeds...")

        # Wait for initial data
        await self._wait_for_data()

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bot shutting down...")
        except Exception as e:
            logger.error("Fatal error: %s", e, exc_info=True)
        finally:
            await self.stop()

    async def stop(self):
        """Gracefully shut down all components."""
        self._running = False
        logger.info("Stopping bot...")

        await self.order_manager.stop()
        await self.binance.stop()
        await self.clob.stop()
        await self.chainlink.stop()
        await self.discovery.stop()
        if self.dashboard:
            await self.dashboard.stop()

        # Log final stats
        stats = self.position_tracker.get_all_stats()
        logger.info("=== Final Statistics ===")
        for key, value in stats.items():
            logger.info("  %s: %s", key, value)

        logger.info("Bot stopped.")

    async def _wait_for_data(self, timeout: float = 30.0):
        """Wait for data feeds to connect and provide initial data."""
        start = time.time()
        while time.time() - start < timeout:
            if self.binance.current_price > 0:
                logger.info("Binance feed active: BTC = $%.2f", self.binance.current_price)
                return
            await asyncio.sleep(0.5)
        logger.warning("Timeout waiting for data feeds")

    async def _market_loop(self):
        """Discover and track active markets."""
        logger.info("Market discovery loop started")

        while self._running:
            try:
                # Save old market BEFORE discovery (expiry check uses this)
                old_market = self._current_market
                market = await self.discovery.discover_current_market()

                # Check if the OLD market just expired (new slug appeared)
                new_slug = market.market_slug if market else None
                old_slug = old_market.market_slug if old_market else None
                if old_market and old_market.is_expired and new_slug != old_slug:
                    await self._on_market_expired()

                # Handle new market
                current_slug = self._current_market.market_slug if self._current_market else None
                if market and market.market_slug != current_slug:
                    await self._on_new_market(market)

                # Pre-fetch next market
                if self._current_market and self._current_market.time_remaining < 60:
                    await self.discovery.discover_next_market()

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Market loop error: %s", e, exc_info=True)
                await asyncio.sleep(10)

    async def _on_new_market(self, market: MarketInfo):
        """Handle a new market becoming active."""
        logger.info(
            "New market active: %s | Duration: %ds | Time remaining: %.0fs",
            market.market_slug,
            market.duration_seconds,
            market.time_remaining,
        )

        # Cancel any MM orders left over from the previous market
        await self.order_manager.cancel_ladder("up")
        await self.order_manager.cancel_ladder("down")

        self._current_market = market

        # Set up CLOB tracking for this market
        self.clob.set_market(market.up_token_id, market.down_token_id)

        # Connect CLOB WebSocket if not connected
        if not self.clob._ws:
            asyncio.create_task(self.clob.connect(), name="clob")

        # Fetch opening price from Chainlink
        opening_price = await self.chainlink.fetch_price_at_timestamp(market.start_timestamp)
        if opening_price:
            market.opening_price = opening_price
            logger.info("Market opening BTC price: $%.2f", opening_price)
        else:
            # Fallback to Binance price
            market.opening_price = self.binance.current_price
            logger.warning(
                "Using Binance price as fallback opening: $%.2f",
                market.opening_price,
            )

        # Reset strategies for new market
        self.aggregator.reset()
        self.market_maker.reset()

        if self.dashboard:
            self.dashboard.set_market(market)

    async def _on_market_expired(self):
        """Handle market expiration and position resolution."""
        market = self._current_market
        if not market:
            return

        logger.info("Market expired: %s", market.market_slug)

        # Determine outcome
        closing_price = self.binance.current_price
        opening_price = market.opening_price or 0

        if opening_price > 0 and closing_price > 0:
            up_won = closing_price > opening_price
            logger.info(
                "Market result: %s | Open: $%.2f | Close: $%.2f",
                "UP WON" if up_won else "DOWN WON",
                opening_price,
                closing_price,
            )

            # Close all positions for this market
            self.position_tracker.close_all_for_market(market.market_slug, up_won)

            # Log results
            if self.trade_logger:
                for pos in self.position_tracker.closed_positions:
                    if pos.market_slug == market.market_slug and pos.closed_at:
                        self.trade_logger.log_pnl(
                            market=market.market_slug,
                            strategy=pos.strategy,
                            direction=pos.side,
                            entry_price=pos.entry_price,
                            size=pos.size,
                            won=pos.status.value == "closed_win",
                            pnl=pos.pnl,
                            pnl_after_fee=pos.pnl_after_fee,
                        )

        self._current_market = None

    async def _trading_loop(self):
        """Main trading loop: evaluate signals and execute trades."""
        logger.info("Trading loop started")

        while self._running:
            try:
                await self._evaluate_and_trade()
                await asyncio.sleep(0.5)  # Evaluate every 500ms

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Trading loop error: %s", e, exc_info=True)
                await asyncio.sleep(2)

    async def _evaluate_and_trade(self):
        """Single evaluation cycle: compute signals, check risk, execute."""
        market = self._current_market
        if not market or not market.is_active:
            return

        # Get current data
        momentum = self.binance.compute_momentum()
        orderbook = self.clob.market_orderbook
        current_price = self.binance.current_price
        opening_price = market.opening_price or 0
        time_remaining = market.time_remaining

        if not momentum or not orderbook:
            return

        # Evaluate all strategies
        signal = self.aggregator.evaluate(
            momentum=momentum,
            orderbook=orderbook,
            current_btc_price=current_price,
            opening_btc_price=opening_price,
            time_remaining=time_remaining,
            available_balance=self.config.trade_size,
            max_position=self.config.risk.max_position_per_market,
        )

        if self.dashboard:
            self.dashboard.set_last_signal(
                f"{signal.source_strategy}: {signal.action.value}"
                if signal.is_actionable
                else signal.reason[:50]
            )

        if not signal.is_actionable:
            return

        # Log signal
        if self.trade_logger:
            self.trade_logger.log_signal({
                "action": signal.action.value,
                "strategy": signal.source_strategy,
                "confidence": signal.confidence,
                "edge": signal.expected_edge,
                "price": signal.target_price,
                "size": signal.position_size,
                "btc_price": current_price,
                "time_remaining": time_remaining,
            })

        # Check risk
        risk_decision = self.risk_manager.evaluate_signal(signal)
        if not risk_decision.approved:
            logger.debug("Risk rejected: %s", risk_decision.reason)
            return

        # Execute trade
        await self._execute_signal(signal, risk_decision.adjusted_size, market)

    async def _execute_signal(
        self,
        signal,
        size: float,
        market: MarketInfo,
    ):
        """Execute a trading signal."""
        logger.info(
            "EXECUTING: %s | size=$%.2f | strategy=%s | edge=%.4f",
            signal.action.value,
            size,
            signal.source_strategy,
            signal.expected_edge,
        )

        if signal.action == TradeAction.BUY_UP:
            order = await self.order_manager.place_order(
                token_id=market.up_token_id,
                side=OrderSide.BUY,
                price=signal.target_price,
                size=size / signal.target_price,  # Convert USDC to tokens
                order_type=OrderType.GTC,
            )
            if order.status == OrderStatus.FILLED:
                self.position_tracker.open_position(
                    order, market.market_slug, "up", signal.source_strategy
                )

        elif signal.action == TradeAction.BUY_DOWN:
            order = await self.order_manager.place_order(
                token_id=market.down_token_id,
                side=OrderSide.BUY,
                price=signal.target_price,
                size=size / signal.target_price,
                order_type=OrderType.GTC,
            )
            if order.status == OrderStatus.FILLED:
                self.position_tracker.open_position(
                    order, market.market_slug, "down", signal.source_strategy
                )

        elif signal.action == TradeAction.BUY_BOTH:
            # Arbitrage: buy both sides
            up_ask = signal.arb_signal.up_ask if signal.arb_signal else 0.5
            down_ask = signal.arb_signal.down_ask if signal.arb_signal else 0.5
            token_size = size / (up_ask + down_ask)

            up_order, down_order = await self.order_manager.place_arbitrage_orders(
                up_token_id=market.up_token_id,
                down_token_id=market.down_token_id,
                up_price=up_ask,
                down_price=down_ask,
                size=token_size,
            )

            if up_order.status == OrderStatus.FILLED:
                self.position_tracker.open_position(
                    up_order, market.market_slug, "up", "intra_arbitrage"
                )
            if down_order.status == OrderStatus.FILLED:
                self.position_tracker.open_position(
                    down_order, market.market_slug, "down", "intra_arbitrage"
                )

    # ------------------------------------------------------------------
    # Market-maker limit-order ladder loop
    # ------------------------------------------------------------------

    async def _market_maker_loop(self):
        """
        Continuously maintains a ladder of limit orders on both UP and DOWN
        tokens for the active market.

        Runs independently of the signal-based trading loop so that limit
        orders are refreshed on a predictable cadence even when no momentum
        signal fires.
        """
        mm_cfg = self.config.market_maker
        if not mm_cfg.enabled:
            logger.info("Market-maker disabled in config — skipping loop")
            return

        logger.info(
            "Market-maker loop started | levels=%d | spacing=%.3f | size=$%.2f/level | "
            "refresh=%.0fs",
            mm_cfg.num_levels,
            mm_cfg.level_spacing,
            mm_cfg.size_per_level,
            mm_cfg.refresh_interval_seconds,
        )

        while self._running:
            try:
                await self._run_market_maker_cycle()
                await asyncio.sleep(mm_cfg.refresh_interval_seconds)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Market-maker loop error: %s", e, exc_info=True)
                await asyncio.sleep(5)

    async def _run_market_maker_cycle(self):
        """Single market-maker evaluation cycle."""
        market = self._current_market
        mm_cfg = self.config.market_maker

        if not market or not market.is_active:
            return

        orderbook = self.clob.market_orderbook
        if not orderbook:
            return

        # Force-cancel stale MM orders before evaluating new ones
        await self.order_manager.cancel_stale_mm_orders(mm_cfg.max_order_age_seconds)

        # Check if hard cap on active MM orders is reached
        active_count = self.order_manager.count_active_mm_orders()
        if active_count >= mm_cfg.max_active_orders:
            logger.debug(
                "MM order cap reached (%d/%d) — skipping cycle",
                active_count,
                mm_cfg.max_active_orders,
            )
            return

        # Let the strategy decide whether to refresh
        if not self.market_maker.should_refresh(orderbook):
            return

        quote = self.market_maker.compute_quote(
            orderbook=orderbook,
            time_elapsed=market.time_elapsed,
            time_remaining=market.time_remaining,
            market_duration=market.duration_seconds,
        )

        if not quote.is_valid:
            logger.debug("MM: no valid quote — %s", quote.reason)
            return

        # --- UP side ---
        if quote.up_levels:
            await self.order_manager.cancel_ladder("up")
            up_levels = [
                (lvl.price, round(lvl.size_usdc / lvl.price, 2))
                for lvl in quote.up_levels
            ]
            up_orders = await self.order_manager.place_ladder(
                token_id=market.up_token_id,
                levels=up_levels,
                side=OrderSide.BUY,
                tag="up",
            )
            for order in up_orders:
                if order.status == OrderStatus.FILLED:
                    self.position_tracker.open_position(
                        order, market.market_slug, "up", "market_maker"
                    )

        # --- DOWN side ---
        if quote.down_levels:
            await self.order_manager.cancel_ladder("down")
            down_levels = [
                (lvl.price, round(lvl.size_usdc / lvl.price, 2))
                for lvl in quote.down_levels
            ]
            down_orders = await self.order_manager.place_ladder(
                token_id=market.down_token_id,
                levels=down_levels,
                side=OrderSide.BUY,
                tag="down",
            )
            for order in down_orders:
                if order.status == OrderStatus.FILLED:
                    self.position_tracker.open_position(
                        order, market.market_slug, "down", "market_maker"
                    )

        logger.info(
            "MM refresh complete | market=%s | UP=%d orders | DOWN=%d orders | "
            "total_active=%d",
            market.market_slug,
            len(quote.up_levels),
            len(quote.down_levels),
            self.order_manager.count_active_mm_orders(),
        )


async def run_backtest(config: BotConfig, days: int):
    """Run backtest with historical data."""
    from polymarket_btc_bot.backtesting.historical_loader import HistoricalLoader
    from polymarket_btc_bot.backtesting.backtester import Backtester

    logger.info("Starting backtest for %d days", days)

    loader = HistoricalLoader(config.backtest, config.polymarket)
    await loader.start()

    # Collect data if needed
    await loader.collect_data(days)

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * 24 * 60 * 60 * 1000)

    backtester = Backtester(config, loader)
    result = backtester.run(start_ms, now_ms, config.trade_size)

    print(result.summary())

    await loader.stop()
    return result


async def run_optimize(config: BotConfig, days: int):
    """Run parameter optimization."""
    from polymarket_btc_bot.backtesting.historical_loader import HistoricalLoader
    from polymarket_btc_bot.backtesting.optimizer import ParameterOptimizer

    logger.info("Starting optimization for %d days", days)

    loader = HistoricalLoader(config.backtest, config.polymarket)
    await loader.start()
    await loader.collect_data(days)

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * 24 * 60 * 60 * 1000)

    optimizer = ParameterOptimizer(config, loader)

    # Grid search
    result = optimizer.grid_search(start_ms, now_ms, trade_size=config.trade_size)
    optimizer.save_results(result, "optimization_output/results.json")

    # Walk-forward analysis
    wf_results = optimizer.walk_forward(start_ms, now_ms, window_days=3, trade_size=config.trade_size)

    print(f"\nBest Parameters: {result.best_params}")
    print(f"Best Sharpe: {result.best_sharpe:.2f}")
    print(f"Best Win Rate: {result.best_win_rate:.1%}")
    print(f"Best PnL: ${result.best_pnl:.2f}")

    await loader.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Polymarket BTC 15-Minute Trading Bot",
    )
    parser.add_argument(
        "--mode",
        choices=["simulation", "paper", "live"],
        default="simulation",
        help="Trading mode (default: simulation)",
    )
    parser.add_argument(
        "--size",
        type=float,
        default=10.0,
        help="Trade size in USDC (default: 10.0)",
    )
    parser.add_argument(
        "--backtest",
        action="store_true",
        help="Run backtest instead of live trading",
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="Run parameter optimization",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Days of historical data for backtest/optimize (default: 30)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = BotConfig.from_args(mode=args.mode, size=args.size)

    # Setup basic logging before full init
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-7s] %(name)-25s | %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.backtest:
        asyncio.run(run_backtest(config, args.days))
    elif args.optimize:
        asyncio.run(run_optimize(config, args.days))
    else:
        # Live/simulation trading
        bot = TradingBot(config)

        # Handle shutdown signals
        loop = asyncio.new_event_loop()

        def shutdown_handler(sig):
            logger.info("Received signal %s, shutting down...", sig)
            for task in asyncio.all_tasks(loop):
                task.cancel()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown_handler, sig)

        try:
            loop.run_until_complete(bot.start())
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt, shutting down...")
        finally:
            loop.run_until_complete(bot.stop())
            loop.close()


if __name__ == "__main__":
    main()
