"""
Terminal Dashboard: Rich-based real-time terminal UI
showing bot status, positions, PnL, and market data.
"""

import asyncio
import logging
import time
from typing import Optional

from polymarket_btc_bot.config import BotConfig, TradingMode
from polymarket_btc_bot.data.binance_feed import BinanceFeed
from polymarket_btc_bot.data.polymarket_clob import PolymarketCLOB
from polymarket_btc_bot.data.market_discovery import MarketInfo
from polymarket_btc_bot.execution.position_tracker import PositionTracker
from polymarket_btc_bot.execution.risk_manager import RiskManager

logger = logging.getLogger(__name__)


class Dashboard:
    """Terminal dashboard using Rich library for real-time bot monitoring."""

    def __init__(
        self,
        config: BotConfig,
        binance_feed: BinanceFeed,
        clob: PolymarketCLOB,
        position_tracker: PositionTracker,
        risk_manager: RiskManager,
    ):
        self.config = config
        self.binance = binance_feed
        self.clob = clob
        self.tracker = position_tracker
        self.risk = risk_manager
        self._running = False
        self._current_market: Optional[MarketInfo] = None
        self._last_signal: str = "No signal"
        self._console = None
        self._live = None

    def set_market(self, market: MarketInfo):
        self._current_market = market

    def set_last_signal(self, signal: str):
        self._last_signal = signal

    async def run(self):
        """Run the dashboard update loop."""
        try:
            from rich.console import Console
            from rich.live import Live
            from rich.table import Table
            from rich.panel import Panel
            from rich.layout import Layout
            from rich.text import Text
        except ImportError:
            logger.warning("Rich library not installed. Dashboard disabled.")
            logger.warning("Install with: pip install rich")
            return

        self._running = True
        self._console = Console()

        try:
            with Live(
                self._render(),
                console=self._console,
                refresh_per_second=1,
            ) as live:
                self._live = live
                while self._running:
                    live.update(self._render())
                    await asyncio.sleep(self.config.monitoring.dashboard_refresh_seconds)
        except asyncio.CancelledError:
            pass

    def _render(self):
        """Render the dashboard layout."""
        try:
            from rich.table import Table
            from rich.panel import Panel
            from rich.layout import Layout
            from rich.text import Text
            from rich.columns import Columns
        except ImportError:
            return "Rich not available"

        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=5),
        )

        layout["body"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=1),
        )

        # Header
        mode_str = self.config.mode.value.upper()
        mode_color = {
            "simulation": "yellow",
            "paper": "cyan",
            "live": "red",
        }.get(self.config.mode.value, "white")

        header = Text()
        header.append("POLYMARKET BTC 15M BOT", style="bold white")
        header.append(f"  [{mode_str}]", style=f"bold {mode_color}")
        if self.risk.is_halted:
            header.append("  HALTED", style="bold red blink")
        layout["header"].update(Panel(header))

        # Left panel: Market & Price Info
        market_table = Table(title="Market Data", show_header=False, expand=True)
        market_table.add_column("Key", style="dim")
        market_table.add_column("Value")

        btc_price = self.binance.current_price
        market_table.add_row("BTC Price", f"${btc_price:,.2f}" if btc_price else "Connecting...")

        momentum = self.binance.compute_momentum()
        if momentum:
            mom_color = "green" if momentum.momentum_score > 0 else "red"
            market_table.add_row(
                f"Momentum ({momentum.lookback_seconds}s)",
                Text(f"{momentum.momentum_score:+.5f}", style=mom_color),
            )

        ob = self.clob.market_orderbook
        if ob:
            market_table.add_row("Up Ask", f"${ob.up_ask:.4f}" if ob.up_ask else "N/A")
            market_table.add_row("Down Ask", f"${ob.down_ask:.4f}" if ob.down_ask else "N/A")
            combined = ob.combined_ask
            if combined:
                market_table.add_row("Combined Ask", f"${combined:.4f}")

        if self._current_market:
            remaining = self._current_market.time_remaining
            minutes = int(remaining // 60)
            seconds = int(remaining % 60)
            market_table.add_row("Time Remaining", f"{minutes}:{seconds:02d}")
            market_table.add_row("Market", self._current_market.market_slug[-20:])

        market_table.add_row("Last Signal", self._last_signal)

        layout["left"].update(Panel(market_table, title="Market"))

        # Right panel: Positions & PnL
        pnl_table = Table(title="Performance", show_header=False, expand=True)
        pnl_table.add_column("Metric", style="dim")
        pnl_table.add_column("Value")

        stats = self.tracker.get_all_stats()
        total_pnl = stats.get("total_pnl", 0)
        pnl_color = "green" if total_pnl >= 0 else "red"
        pnl_table.add_row("Total PnL", Text(f"${total_pnl:.2f}", style=pnl_color))
        pnl_table.add_row("Total Trades", str(stats.get("total_trades", 0)))
        pnl_table.add_row(
            "Win Rate",
            f"{stats.get('win_rate', 0):.1%}",
        )
        pnl_table.add_row(
            "Avg PnL/Trade",
            f"${stats.get('avg_pnl_per_trade', 0):.4f}",
        )
        pnl_table.add_row(
            "Max Drawdown",
            f"${stats.get('max_drawdown', 0):.2f}",
        )
        pnl_table.add_row(
            "Sharpe Ratio",
            f"{stats.get('sharpe_ratio', 0):.2f}",
        )

        # Open positions
        open_pos = self.tracker.open_positions
        pnl_table.add_row("Open Positions", str(len(open_pos)))
        pnl_table.add_row(
            "Open Exposure",
            f"${self.tracker.total_open_exposure:.2f}",
        )

        for pos in open_pos:
            pnl_table.add_row(
                f"  {pos.side.upper()}",
                f"${pos.cost:.2f} @ {pos.entry_price:.4f} ({pos.strategy})",
            )

        layout["right"].update(Panel(pnl_table, title="Performance"))

        # Footer: Connection status
        footer_text = Text()
        footer_text.append("Binance: ", style="dim")
        footer_text.append(
            "Connected" if self.binance.is_connected else "Disconnected",
            style="green" if self.binance.is_connected else "red",
        )
        footer_text.append("  |  CLOB: ", style="dim")
        footer_text.append(
            "Connected" if self.clob._ws else "Disconnected",
            style="green" if self.clob._ws else "red",
        )
        footer_text.append(f"  |  Trade Size: ${self.config.trade_size:.0f}", style="dim")

        today = self.tracker.get_today_stats()
        if today:
            footer_text.append(
                f"  |  Today: {today.total_trades} trades, ${today.total_pnl_after_fee:.2f}",
                style="dim",
            )

        layout["footer"].update(Panel(footer_text, title="Status"))

        return layout

    async def stop(self):
        self._running = False
