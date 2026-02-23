"""
Live Backtest: 1 Stunde, $100 virtuelles Kapital.

Startet den Bot in SIMULATION-Modus mit echten Live-Preisen
(Binance BTC, Polymarket Orderbook) — aber ohne echte Orders.
Stoppt automatisch nach 3600 Sekunden und druckt ein
detailliertes Abschlussbericht.

Starten:
    python live_backtest.py
"""

import asyncio
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from polymarket_btc_bot.config import (
    BotConfig, TradingMode,
    RiskConfig, MarketMakerConfig, ExecutionConfig, MonitoringConfig,
)
from polymarket_btc_bot.main import TradingBot

# ──────────────────────────────────────────────────────────
# Konfiguration
# ──────────────────────────────────────────────────────────
BANKROLL       = 100.0          # Virtuelles Startkapital
DURATION_S     = 3600           # 60 Minuten
TRADE_SIZE     = 10.0           # USDC pro Einzeltrade (10 % des Bankrolls)
STATUS_EVERY_S = 60             # Status-Print alle 60 Sekunden


def build_config() -> BotConfig:
    """BotConfig für $100 Backtest."""
    cfg = BotConfig()
    cfg.mode       = TradingMode.SIMULATION
    cfg.trade_size = TRADE_SIZE

    # Risk skaliert auf $100
    cfg.risk = RiskConfig(
        max_position_per_market = 20.0,   # max $20 pro Markt
        max_open_positions      = 3,
        max_daily_exposure      = BANKROLL,
        max_daily_loss          = -BANKROLL * 0.30,   # -$30 Stop
        max_single_trade_loss   = -TRADE_SIZE,
        min_edge_threshold      = 0.03,
        min_momentum_threshold  = 0.002,
        no_trade_first_seconds  = 30,
        no_trade_last_seconds   = 20,
        max_hold_time_minutes   = 12,
        winner_fee              = 0.02,
        min_profit_after_fee    = 0.005,
    )

    # Market-Maker auf $100 skaliert
    cfg.market_maker = MarketMakerConfig(
        enabled               = True,
        num_levels            = 3,
        level_spacing         = 0.02,
        size_per_level        = 3.0,      # $3 pro Level (9 $ total pro Seite)
        first_level_offset    = 0.01,
        refresh_interval_seconds = 30.0,
        max_order_age_seconds    = 60.0,
        min_time_remaining       = 90.0,
        start_after_seconds      = 30.0,
        reprice_threshold        = 0.01,
        max_active_orders        = 12,
        max_position_per_side    = 25.0,
        min_ask_price            = 0.10,
        max_ask_price            = 0.90,
    )

    cfg.execution = ExecutionConfig(
        default_trade_size      = TRADE_SIZE,
        max_orders_per_minute   = 60,
        order_timeout_seconds   = 2.0,
        max_retries             = 3,
        slippage_tolerance      = 0.005,
        use_limit_orders        = True,
        # Realistic MM fill model: 60 % base rate, decays with distance from 0.50.
        # Level 1 (1 ct below ask, ~0.49): ~57 %  Level 2 (~0.47): ~49 %
        # Level 3 (~0.45): ~42 %  Deep orders (0.30): ~6 %
        sim_base_fill_rate      = 0.60,
    )

    cfg.monitoring = MonitoringConfig(
        log_dir                   = "backtest_logs",
        log_level                 = "INFO",
        dashboard_refresh_seconds = 1.0,
    )

    return cfg


# ──────────────────────────────────────────────────────────
# Live-Backtest-Wrapper
# ──────────────────────────────────────────────────────────

class LiveBacktest:
    """Läuft den TradingBot für DURATION_S Sekunden und druckt
    regelmäßig einen Status sowie einen Abschlussbericht."""

    def __init__(self):
        self.cfg       = build_config()
        self.bot       = TradingBot(self.cfg)
        self.start_ts  = 0.0
        self._done     = asyncio.Event()

    # ── public ──────────────────────────────────────────

    async def run(self):
        self.start_ts = time.time()
        self._print_header()

        # Bot starten (non-blocking via Task)
        bot_task    = asyncio.create_task(self.bot.start(), name="bot")
        timer_task  = asyncio.create_task(self._timer_loop(),  name="timer")
        status_task = asyncio.create_task(self._status_loop(), name="status")

        await asyncio.wait(
            [bot_task, timer_task, status_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Alle Tasks sauber beenden
        for t in [bot_task, timer_task, status_task]:
            if not t.done():
                t.cancel()
        await asyncio.gather(bot_task, timer_task, status_task, return_exceptions=True)

        self._print_final_report()

    # ── private helpers ─────────────────────────────────

    async def _timer_loop(self):
        """Wacht DURATION_S und bricht dann den Bot ab."""
        await asyncio.sleep(DURATION_S)
        elapsed = time.time() - self.start_ts
        print(f"\n⏱  Zeit abgelaufen ({elapsed:.0f}s) — stoppe Bot...")
        # Alle Tasks canceln → bot_task bricht aus gather() aus
        for task in asyncio.all_tasks():
            if task.get_name() not in ("timer", "status"):
                task.cancel()

    async def _status_loop(self):
        """Druckt alle STATUS_EVERY_S einen Zwischenstand."""
        next_print = time.time() + STATUS_EVERY_S
        while True:
            await asyncio.sleep(5)
            if time.time() >= next_print:
                self._print_status()
                next_print = time.time() + STATUS_EVERY_S

    # ── output ──────────────────────────────────────────

    def _elapsed_str(self) -> str:
        e = int(time.time() - self.start_ts)
        return f"{e//60:02d}:{e%60:02d}"

    def _remaining_str(self) -> str:
        rem = max(0, DURATION_S - int(time.time() - self.start_ts))
        return f"{rem//60:02d}:{rem%60:02d}"

    def _print_header(self):
        print()
        print("=" * 62)
        print("  POLYMARKET BTC 15m BOT — LIVE BACKTEST")
        print(f"  Kapital: ${BANKROLL:.0f}  |  Laufzeit: {DURATION_S//60} min")
        print(f"  Gestartet: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print("=" * 62)
        print()

    def _print_status(self):
        pt  = self.bot.position_tracker
        btc = self.bot.binance.current_price
        mkt = self.bot._current_market

        stats       = pt.get_all_stats()
        open_pos    = pt.open_positions
        closed_pos  = pt.closed_positions

        open_exposure = sum(p.cost for p in open_pos)
        virtual_bal   = BANKROLL + stats.get("total_pnl", 0.0)

        print()
        print(f"─── [{self._elapsed_str()} / {DURATION_S//60:02d}:00 | verbleibend {self._remaining_str()}] ─────")
        print(f"  BTC Preis  : ${btc:>10,.2f}")
        if mkt:
            rem = max(0, mkt.time_remaining)
            print(f"  Markt      : {mkt.market_slug}")
            print(f"  Verbleibend: {rem:.0f}s in diesem 15m-Fenster")
        else:
            print("  Markt      : (warte auf Markt...)")
        print(f"  Virtuelles Kapital: ${virtual_bal:>8.2f}  (Start: ${BANKROLL:.0f})")
        print(f"  Offene Positionen : {len(open_pos)}  (Exposure: ${open_exposure:.2f})")
        print(f"  Trades abgeschlossen: {stats.get('total_trades', 0)}")
        total_t = stats.get("total_trades", 0)
        if total_t > 0:
            wins = stats.get("wins", 0)
            wr   = stats.get("win_rate", 0.0)
            pnl  = stats.get("total_pnl", 0.0)
            print(f"  Win-Rate   : {wins}/{total_t} = {wr:.1%}")
            pnl_sign = "+" if pnl >= 0 else ""
            print(f"  P&L netto  : {pnl_sign}${pnl:.4f}")
        print()

    def _print_final_report(self):
        pt    = self.bot.position_tracker
        stats = pt.get_all_stats()
        btc   = self.bot.binance.current_price
        elapsed = time.time() - self.start_ts

        total_t = stats.get("total_trades", 0)
        wins    = stats.get("wins", 0)
        losses  = stats.get("losses", 0)
        wr      = stats.get("win_rate", 0.0)
        pnl     = stats.get("total_pnl", 0.0)
        avg_pnl = stats.get("avg_pnl_per_trade", 0.0)
        max_dd  = stats.get("max_drawdown", 0.0)
        sharpe  = stats.get("sharpe_ratio", 0.0)
        volume  = stats.get("total_volume", 0.0)
        roi     = (pnl / BANKROLL) * 100

        final_bal = BANKROLL + pnl

        # Open positions (unresolved) — zählen als Verlust
        open_pos = pt.open_positions
        open_cost = sum(p.cost for p in open_pos)

        print()
        print("=" * 62)
        print("  LIVE-BACKTEST ABSCHLUSSBERICHT")
        print("=" * 62)
        print(f"  Laufzeit       : {elapsed/60:.1f} min ({elapsed:.0f}s)")
        print(f"  BTC Schlusskurs: ${btc:,.2f}")
        print()
        print(f"  ── Kapital ──────────────────────────────────")
        pnl_sign = "+" if pnl >= 0 else ""
        print(f"  Start          : ${BANKROLL:.2f}")
        print(f"  P&L            : {pnl_sign}${pnl:.4f}")
        print(f"  ROI            : {pnl_sign}{roi:.2f}%")
        print(f"  Endkapital     : ${final_bal:.4f}")
        if open_cost > 0:
            print(f"  Noch offen     : ${open_cost:.2f} (nicht aufgelöst)")
        print()
        print(f"  ── Trades ───────────────────────────────────")
        print(f"  Gesamt         : {total_t}")
        print(f"  Gewonnen       : {wins}")
        print(f"  Verloren       : {losses}")
        print(f"  Win-Rate       : {wr:.1%}")
        print(f"  Avg P&L/Trade  : {'+' if avg_pnl>=0 else ''}${avg_pnl:.4f}")
        print(f"  Gesamtvolumen  : ${volume:.2f}")
        print()
        print(f"  ── Risiko ───────────────────────────────────")
        print(f"  Max Drawdown   : -${max_dd:.4f}")
        print(f"  Sharpe Ratio   : {sharpe:.2f}")
        print()

        # Trade-by-trade Liste
        closed = pt.closed_positions
        if closed:
            print(f"  ── Einzelne Trades ({len(closed)}) ──────────────────")
            print(f"  {'#':<3} {'Markt':<30} {'Seite':<5} {'Einstieg':>8} "
                  f"{'Größe':>7} {'P&L':>9} {'Ergebnis'}")
            print(f"  {'-'*3} {'-'*30} {'-'*5} {'-'*8} {'-'*7} {'-'*9} {'-'*8}")
            for i, p in enumerate(closed, 1):
                slug_short = p.market_slug[-28:] if len(p.market_slug) > 28 else p.market_slug
                pnl_str = f"{'+' if p.pnl_after_fee>=0 else ''}${p.pnl_after_fee:.4f}"
                result  = "WIN" if "win" in p.status.value else "LOSS"
                print(f"  {i:<3} {slug_short:<30} {p.side:<5} "
                      f"${p.entry_price:.4f} ${p.cost:>6.2f} {pnl_str:>9} {result}")

        print()
        print("=" * 62)
        print(f"  Backtest beendet: "
              f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print("=" * 62)
        print()


# ──────────────────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────────────────

def main():
    # Logging
    logging.basicConfig(
        level   = logging.WARNING,      # Bot-intern leise halten
        format  = "%(asctime)s [%(levelname)-7s] %(name)-25s | %(message)s",
        datefmt = "%H:%M:%S",
    )
    # Nur eigene Backtest-Logs auf INFO
    logging.getLogger("__main__").setLevel(logging.INFO)
    logging.getLogger("polymarket_btc_bot.main").setLevel(logging.INFO)
    logging.getLogger("polymarket_btc_bot.data.market_discovery").setLevel(logging.INFO)
    logging.getLogger("polymarket_btc_bot.execution.position_tracker").setLevel(logging.INFO)

    backtest = LiveBacktest()
    loop = asyncio.new_event_loop()

    def _shutdown(sig):
        print(f"\nSignal {sig.name} empfangen — stoppe Backtest...")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, _shutdown, s)

    try:
        loop.run_until_complete(backtest.run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
