"""
MMDashboard: Echtzeit-Terminal-Dashboard für den Market Making Bot.

Zeigt an:
  ┌─────────────────────────────────────────────────────────┐
  │  POLYMARKET MM BOT  [SIMULATION]           [LIVE]       │
  ├─────────────────────────────────────────────────────────┤
  │ MARKTDATEN           │  PERFORMANCE                     │
  │ BTC:  $42,150.00     │  Session PnL: +$1.23             │
  │ FV:   0.5320 (UP)    │  Spread-Einnahmen: $0.85          │
  │ EMA5: 42,100         │  Märkte: 1 | Orders: 2           │
  │ EMA15: 41,980        │  Fills: 4 (B:2 A:2) ↕ 2x        │
  │ ATR:  $25.00         │  Inventar-Imbalance: +12%        │
  ├──────────────────────┼──────────────────────────────────┤
  │ AKTIVE QUOTES        │  LETZTE FILLS                    │
  │ BID: 0.5070 @ $5.00  │  14:32:01 BID 10.5 @ 0.4980     │
  │ ASK: 0.5570 @ $5.00  │  14:32:05 ASK 10.5 @ 0.5420     │
  │ Spread: 0.0500 (5%)  │  14:33:15 BID  8.2 @ 0.5020     │
  │ Skew: +0.0025        │  14:34:01 ASK  8.2 @ 0.5520     │
  └──────────────────────┴──────────────────────────────────┘
  Binance: Connected | CLOB: Connected | Refresh: 25s ago

──────────────────────────────────────────────────────────────
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class MMDashboard:
    """
    Rich-basiertes Terminal-Dashboard für den Market Making Bot.

    Aktualisiert sich automatisch jede Sekunde mit aktuellen Daten.
    Verwendet Rich's Live-Rendering für flimmerfreie Updates.

    Verwendung:
        dashboard = MMDashboard(config, binance_feed)
        await dashboard.run()  # Als asyncio Task starten
    """

    def __init__(
        self,
        config: dict,
        binance_feed,
        market_maker,
        inventory_manager,
        trade_db,
        refresh_interval: float = 1.0,
    ):
        """
        Args:
            config:             Bot-Konfiguration
            binance_feed:       Binance Preisfeed-Instanz
            market_maker:       MarketMaker-Instanz
            inventory_manager:  InventoryManager-Instanz
            trade_db:           TradeDatabase-Instanz
            refresh_interval:   Sekunden zwischen Dashboard-Updates
        """
        self.config = config
        self.binance = binance_feed
        self.mm = market_maker
        self.inv_mgr = inventory_manager
        self.db = trade_db
        self.refresh_interval = refresh_interval

        self._running = False
        self._mode = config.get("mode", "simulation").upper()
        self._start_time = time.time()
        self._last_fair_value: Optional[object] = None
        self._last_quotes: Optional[object] = None
        self._status_message: str = "Starte..."
        self._recent_fills: list[dict] = []
        self._last_db_refresh = 0.0

    def update_fair_value(self, fv_result):
        """Aktualisiere den aktuellen Fair Value (von Hauptloop aufgerufen)."""
        self._last_fair_value = fv_result

    def update_quotes(self, quotes):
        """Aktualisiere die aktuellen Quotes (von Hauptloop aufgerufen)."""
        self._last_quotes = quotes

    def set_status(self, message: str):
        """Setze eine Statusmeldung (z.B. Fehlermeldung oder Info)."""
        self._status_message = message

    async def run(self):
        """
        Hauptloop des Dashboards.

        Startet Rich Live-Rendering und aktualisiert das Layout sekündlich.
        """
        try:
            from rich.console import Console
            from rich.live import Live
        except ImportError:
            logger.warning(
                "Rich-Bibliothek nicht installiert. Dashboard deaktiviert. "
                "Installation: pip install rich"
            )
            # Fallback: einfache Konsolenausgabe
            await self._simple_console_loop()
            return

        self._running = True
        console = Console()

        try:
            with Live(
                self._render(),
                console=console,
                refresh_per_second=2,
                screen=False,
            ) as live:
                while self._running:
                    live.update(self._render())
                    await asyncio.sleep(self.refresh_interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Dashboard-Fehler: %s", e)

    async def _simple_console_loop(self):
        """Einfacher Fallback ohne Rich."""
        self._running = True
        while self._running:
            stats = self.mm.get_statistics()
            btc = self.binance.current_price
            print(
                f"\r[MM-Bot] BTC=${btc:,.2f} | "
                f"PnL=${stats.get('session_realized_pnl', 0):.2f} | "
                f"Orders={stats.get('active_orders', 0)} | "
                f"SpreadEink=${stats.get('estimated_spread_earned', 0):.4f}",
                end="", flush=True
            )
            await asyncio.sleep(self.refresh_interval * 5)

    def _render(self):
        """Rendere das vollständige Dashboard-Layout."""
        try:
            return self._build_layout()
        except Exception as e:
            logger.error("Render-Fehler: %s", e)
            return f"Dashboard-Fehler: {e}"

    def _build_layout(self):
        """Baue das Rich-Layout auf."""
        from rich.layout import Layout
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from rich.columns import Columns

        layout = Layout()

        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="fills", size=8),
            Layout(name="footer", size=3),
        )

        layout["body"].split_row(
            Layout(name="market_data", ratio=1),
            Layout(name="performance", ratio=1),
        )

        # ── Header ────────────────────────────────────────────────────────────
        mode_colors = {
            "SIMULATION": "yellow",
            "PAPER": "cyan",
            "LIVE": "bold red",
        }
        mode_color = mode_colors.get(self._mode, "white")

        elapsed = time.time() - self._start_time
        elapsed_str = f"{int(elapsed // 3600):02d}:{int((elapsed % 3600) // 60):02d}:{int(elapsed % 60):02d}"

        header_text = Text()
        header_text.append("⚡ POLYMARKET MARKET MAKER BOT ⚡", style="bold white")
        header_text.append(f"  [{self._mode}]", style=f"bold {mode_color}")
        header_text.append(f"  Laufzeit: {elapsed_str}", style="dim")

        layout["header"].update(Panel(header_text, style="blue"))

        # ── Marktdaten (links) ────────────────────────────────────────────────
        market_table = Table(show_header=False, expand=True, box=None)
        market_table.add_column("Key", style="dim", width=18)
        market_table.add_column("Value", style="white")

        btc_price = self.binance.current_price
        btc_str = f"${btc_price:>12,.2f}" if btc_price > 0 else "Verbinde..."
        btc_color = "green" if btc_price > 0 else "red"
        market_table.add_row("BTC/USDT", Text(btc_str, style=btc_color))

        # Fair Value
        if self._last_fair_value:
            fv = self._last_fair_value
            fv_direction = "▲ UP" if fv.fair_value > 0.50 else "▼ DOWN"
            fv_color = "green" if fv.fair_value > 0.52 else ("red" if fv.fair_value < 0.48 else "yellow")
            market_table.add_row(
                "Fair Value",
                Text(f"{fv.fair_value:.4f}  {fv_direction}", style=fv_color)
            )
            market_table.add_row(
                "EMA5 / EMA15",
                Text(
                    f"{fv.ema5:,.0f}  /  {fv.ema15:,.0f}" if fv.ema5 and fv.ema15 else "Berechne...",
                    style="cyan"
                )
            )
            atr_str = f"${fv.atr:.2f}" if fv.atr else "N/A"
            market_table.add_row("ATR (Volatilität)", Text(atr_str, style="cyan"))
            market_table.add_row("Adj / Zeitfaktor", f"{fv.momentum_adj:+.4f} / {fv.time_factor:.2f}")
            market_table.add_row("Konfidenz", f"{fv.confidence:.0%}")
            market_table.add_row(
                "Preisänd. (Open)",
                Text(
                    f"{fv.price_change_pct:+.2%}",
                    style="green" if fv.price_change_pct >= 0 else "red"
                )
            )

        # Aktuelle Quotes
        if self._last_quotes:
            q = self._last_quotes
            market_table.add_row("─" * 18, "─" * 20)
            market_table.add_row(
                "BID",
                Text(f"{q.bid_price:.4f}  ({q.bid_skew:+.4f})  ${q.bid_size_usdc:.2f}", style="green")
            )
            market_table.add_row(
                "ASK",
                Text(f"{q.ask_price:.4f}  ({q.ask_skew:+.4f})  ${q.ask_size_usdc:.2f}", style="red")
            )
            spread_pct = q.spread * 100
            market_table.add_row(
                "Spread",
                Text(f"{q.spread:.4f}  ({spread_pct:.1f}%)", style="yellow")
            )
            imbalance_color = "red" if abs(q.imbalance_ratio) > 0.3 else "yellow" if abs(q.imbalance_ratio) > 0.1 else "green"
            market_table.add_row(
                "Inventar-Imbalance",
                Text(f"{q.imbalance_ratio:+.1%}", style=imbalance_color)
            )

        layout["market_data"].update(Panel(market_table, title="[bold cyan]Marktdaten & Quotes[/bold cyan]"))

        # ── Performance (rechts) ──────────────────────────────────────────────
        stats = self.mm.get_statistics()
        inv_summary = self.inv_mgr.get_summary()

        perf_table = Table(show_header=False, expand=True, box=None)
        perf_table.add_column("Metrik", style="dim", width=22)
        perf_table.add_column("Wert", style="white")

        # Gesamt-PnL
        realized_pnl = stats.get("session_realized_pnl", 0.0)
        pnl_color = "green" if realized_pnl >= 0 else "red"
        perf_table.add_row(
            "Session PnL (real.)",
            Text(f"${realized_pnl:+.4f}", style=pnl_color)
        )

        spread_earned = stats.get("estimated_spread_earned", 0.0)
        perf_table.add_row(
            "Spread-Einnahmen (est.)",
            Text(f"${spread_earned:.4f}", style="green")
        )

        # Order-Statistiken
        perf_table.add_row("─" * 22, "─" * 20)
        perf_table.add_row(
            "Aktive Orders",
            f"{stats.get('active_orders', 0)} / {stats.get('max_open_orders', 4)}"
        )
        perf_table.add_row("Aktive Märkte", str(stats.get("active_markets", 0)))

        bid_fills = stats.get("total_bid_fills", 0)
        ask_fills = stats.get("total_ask_fills", 0)
        roundtrips = min(bid_fills, ask_fills)
        perf_table.add_row(
            "Fills (B/A/Roundtrips)",
            f"{bid_fills} / {ask_fills} / {roundtrips}"
        )

        cancelled = stats.get("total_cancelled_stale", 0)
        perf_table.add_row("Stale Cancels", str(cancelled))

        # Inventar
        perf_table.add_row("─" * 22, "─" * 20)
        exposure = inv_summary.get("total_exposure_usdc", 0.0)
        capital = stats.get("capital", 100.0)
        used_pct = inv_summary.get("capital_used_pct", 0.0)
        perf_table.add_row(
            "Exposure / Kapital",
            f"${exposure:.2f} / ${capital:.0f}  ({used_pct:.0%})"
        )
        perf_table.add_row(
            "YES-Tokens",
            f"{inv_summary.get('total_yes_tokens', 0):.3f}"
        )
        perf_table.add_row(
            "NO-Tokens (Short)",
            f"{inv_summary.get('total_no_tokens', 0):.3f}"
        )

        # Konfiguration
        perf_table.add_row("─" * 22, "─" * 20)
        perf_table.add_row(
            "Basis-Spread",
            f"{stats.get('base_spread', 0.04):.1%}"
        )
        perf_table.add_row("Modus", stats.get("mode", "simulation").upper())

        layout["performance"].update(Panel(perf_table, title="[bold yellow]Performance & Inventar[/bold yellow]"))

        # ── Letzte Fills ──────────────────────────────────────────────────────
        fills_table = Table(
            show_header=True,
            expand=True,
            box=None,
        )
        fills_table.add_column("Zeit", style="dim", width=10)
        fills_table.add_column("Seite", width=5)
        fills_table.add_column("Tokens", width=8, justify="right")
        fills_table.add_column("Preis", width=8, justify="right")
        fills_table.add_column("Wert USDC", width=10, justify="right")
        fills_table.add_column("Markt", style="dim")

        # Fills aus DB aktualisieren (alle 5 Sekunden)
        now = time.time()
        if now - self._last_db_refresh > 5.0:
            try:
                self._recent_fills = self.db.get_recent_fills(limit=8)
                self._last_db_refresh = now
            except Exception:
                pass

        for fill in self._recent_fills[:8]:
            fill_time = time.strftime("%H:%M:%S", time.localtime(fill.get("filled_at", 0)))
            side = fill.get("mm_side", "?")
            side_text = Text("BID", style="green") if side == "bid" else Text("ASK", style="red")
            tokens = fill.get("filled_tokens", 0)
            price = fill.get("fill_price", 0)
            value = fill.get("fill_value_usdc", 0)
            market = fill.get("market_id", "")[-16:]

            fills_table.add_row(
                fill_time,
                side_text,
                f"{tokens:.3f}",
                f"{price:.4f}",
                f"${value:.3f}",
                market,
            )

        if not self._recent_fills:
            fills_table.add_row(
                "─", "─", "─", "─", "─", "Noch keine Fills in dieser Session"
            )

        layout["fills"].update(Panel(fills_table, title="[bold]Letzte Fills[/bold]"))

        # ── Footer / Status ───────────────────────────────────────────────────
        footer_text = Text()
        binance_connected = self.binance.is_connected
        footer_text.append("Binance: ", style="dim")
        footer_text.append(
            "Verbunden" if binance_connected else "Getrennt",
            style="green" if binance_connected else "red"
        )
        footer_text.append("  |  ", style="dim")
        footer_text.append("Status: ", style="dim")
        footer_text.append(self._status_message, style="white")

        layout["footer"].update(Panel(footer_text))

        return layout

    async def stop(self):
        """Stoppe das Dashboard."""
        self._running = False
