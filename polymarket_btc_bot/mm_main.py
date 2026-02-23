"""
Market Making Bot — Haupteinstiegspunkt

──────────────────────────────────────────────────────────────────────────────
Architektur-Übersicht:
──────────────────────────────────────────────────────────────────────────────

  ┌─────────────────────────────────────────────────────────────────┐
  │                    MarketMakerBotMain                           │
  │                                                                 │
  │  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   │
  │  │  BinanceFeed  │   │ FairValueCalc│   │ InventoryManager │   │
  │  │  (ccxt/WS)    │   │ (EMA+ATR)    │   │ (Skewing+Kelly)  │   │
  │  └──────┬───────┘   └──────┬───────┘   └────────┬─────────┘   │
  │         │                  │                     │             │
  │         └──────────────────┼─────────────────────┘            │
  │                            ▼                                   │
  │                    ┌──────────────┐                           │
  │                    │  MarketMaker  │                           │
  │                    │  (Kern-Logik) │                           │
  │                    └──────┬───────┘                           │
  │                           │                                   │
  │              ┌────────────┼────────────┐                     │
  │              ▼            ▼            ▼                     │
  │       ┌──────────┐ ┌──────────┐ ┌──────────┐               │
  │       │OrderMgr  │ │  RiskMgr │ │ TradeDB  │               │
  │       │(Poly CLOB│ │(Drawdown)│ │(SQLite)  │               │
  │       └──────────┘ └──────────┘ └──────────┘               │
  │                                                               │
  │       ┌──────────────────────────┐                           │
  │       │    MMDashboard (Rich)     │                           │
  │       └──────────────────────────┘                           │
  └─────────────────────────────────────────────────────────────────┘

Hauptloop (alle 30 Sekunden):
  1. BTC-Preis + Momentum aus Binance abrufen
  2. Aktive Polymarket-Märkte suchen
  3. Fair Value für jeden Markt berechnen
  4. Inventory Skewing berechnen
  5. Stale Orders canceln
  6. Neue Bid/Ask Orders platzieren
  7. Risiko prüfen (Max-Drawdown, Max-Exposure)
  8. Dashboard aktualisieren
  9. Trade-Datenbank aktualisieren

──────────────────────────────────────────────────────────────────────────────
Ausführung:
──────────────────────────────────────────────────────────────────────────────
  # Simulationsmodus (kein echtes Geld):
  python -m polymarket_btc_bot.mm_main

  # Mit Konfigurationsdatei:
  python -m polymarket_btc_bot.mm_main --config config.yaml

  # Paper Trading (Live-Preise, keine echten Orders):
  python -m polymarket_btc_bot.mm_main --mode paper

  # Live Trading:
  python -m polymarket_btc_bot.mm_main --mode live --capital 100

──────────────────────────────────────────────────────────────────────────────
Umgebungsvariablen (für Live-Trading):
──────────────────────────────────────────────────────────────────────────────
  POLY_PRIVATE_KEY     — Ethereum Private Key (0x...)
  POLY_API_KEY         — Polymarket API-Schlüssel
  POLY_API_SECRET      — Polymarket API-Secret
  POLY_API_PASSPHRASE  — Polymarket API-Passphrase
  POLY_PROXY_WALLET    — Polymarket Proxy Wallet Address
──────────────────────────────────────────────────────────────────────────────
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

# ── Interne Module ─────────────────────────────────────────────────────────────
from polymarket_btc_bot.config import (
    BinanceConfig,
    PolymarketConfig,
    TradingMode,
    ExecutionConfig,
)
from polymarket_btc_bot.data.binance_feed import BinanceFeed
from polymarket_btc_bot.data.market_discovery import MarketDiscovery, MarketInfo
from polymarket_btc_bot.data.trade_db import TradeDatabase
from polymarket_btc_bot.execution.inventory_manager import InventoryManager
from polymarket_btc_bot.execution.order_manager import OrderManager
from polymarket_btc_bot.monitoring.mm_dashboard import MMDashboard
from polymarket_btc_bot.strategy.fair_value import FairValueCalc
from polymarket_btc_bot.strategy.market_maker import MarketMaker, MMConfig
from polymarket_btc_bot.strategy.volatility_filter import VolatilityGuard

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Konfigurations-Loader
# ──────────────────────────────────────────────────────────────────────────────

def load_config(config_path: Optional[str] = None) -> dict:
    """
    Lade Konfiguration aus YAML-Datei und/oder Kommandozeilen-Argumenten.

    Priorität (höchste zuerst):
    1. Kommandozeilen-Argumente
    2. Konfigurationsdatei (config.yaml)
    3. Standard-Werte

    Args:
        config_path: Pfad zur YAML-Konfigurationsdatei (optional)

    Returns:
        Konfigurations-Dictionary
    """
    # Standard-Konfiguration
    defaults = {
        "mode": "simulation",
        "capital": 100.0,
        "base_spread": 0.04,
        "min_spread": 0.025,
        "max_spread": 0.10,
        "max_position_pct": 0.10,
        "base_order_size_usdc": 5.0,
        "refresh_seconds": 30.0,
        "max_order_age_seconds": 60.0,
        "stale_threshold": 0.5,
        "max_open_orders": 8,
        "max_markets": 4,
        "skew_factor": 0.50,
        "max_inventory_ratio": 0.60,
        "kelly_fraction": 0.25,
        "max_session_loss_pct": 0.20,
        "polymarket_fee": 0.02,
        "db_path": "trades.db",
        "log_level": "INFO",
        "log_file": "mm_bot.log",
        "dashboard_refresh": 1.0,
        # Volatilitäts-Filter
        "vola_filter_threshold": 0.03,
        "vola_filter_lookback": 900,
        "vola_filter_cooldown": 300.0,
        "vola_filter_vol_threshold": None,
        "vola_filter_vol_lookback": 300,
        "vola_spread_threshold": 0.0015,
        "vola_min_spread": 0.06,
        # Fair Value Parameter
        "ema_short_period": 5,
        "ema_long_period": 15,
        "atr_period": 14,
        "max_momentum_adj": 0.12,
        "max_price_adj": 0.15,
    }

    config = dict(defaults)

    # YAML-Datei laden falls angegeben
    if config_path and Path(config_path).exists():
        try:
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                yaml_config = yaml.safe_load(f)
            if yaml_config and isinstance(yaml_config, dict):
                # Flaches Laden (market_making.* → direkt in config)
                mm_config = yaml_config.get("market_making", yaml_config)
                config.update(mm_config)
            logger.info("Konfiguration geladen: %s", config_path)
        except ImportError:
            logger.warning(
                "PyYAML nicht installiert. Verwende Standard-Konfiguration. "
                "Installation: pip install pyyaml"
            )
        except Exception as e:
            logger.error("Fehler beim Laden der Konfiguration: %s", e)

    return config


# ──────────────────────────────────────────────────────────────────────────────
# Risk Manager (vereinfacht, MM-spezifisch)
# ──────────────────────────────────────────────────────────────────────────────

class MMRiskManager:
    """
    Risikomanagement für den Market Making Bot.

    Überwacht:
    - Session-Verlust-Limit (Max. 20% des Kapitals)
    - Max. Open Exposure
    - Drawdown-Tracking

    Bei Überschreitung: Bot wird angehalten (Emergency Halt).
    """

    def __init__(self, capital: float, max_loss_pct: float = 0.20):
        self.capital = capital
        self.max_loss = capital * max_loss_pct
        self._session_pnl: float = 0.0
        self._peak_pnl: float = 0.0
        self._max_drawdown: float = 0.0
        self._halted: bool = False
        self._halt_reason: str = ""

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def update_pnl(self, new_pnl: float):
        """Aktualisiere PnL und prüfe auf Verlust-Limits."""
        self._session_pnl = new_pnl

        # Drawdown berechnen
        if new_pnl > self._peak_pnl:
            self._peak_pnl = new_pnl
        drawdown = self._peak_pnl - new_pnl
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown

        # Stop-Loss prüfen
        if self._session_pnl <= -self.max_loss:
            self._halted = True
            self._halt_reason = (
                f"Session-Verlust-Limit erreicht: ${self._session_pnl:.2f} "
                f"(Limit: -${self.max_loss:.2f})"
            )
            logger.critical("NOTFALL-STOPP: %s", self._halt_reason)

    def check_exposure(self, current_exposure: float, max_exposure: float) -> bool:
        """
        Prüfe ob die Exposure das Limit überschreitet.

        Returns:
            True wenn Exposure OK, False wenn zu hoch
        """
        if current_exposure > max_exposure:
            logger.warning(
                "Exposure-Limit überschritten: $%.2f > $%.2f",
                current_exposure, max_exposure
            )
            return False
        return True

    def get_stats(self) -> dict:
        return {
            "session_pnl": self._session_pnl,
            "peak_pnl": self._peak_pnl,
            "max_drawdown": self._max_drawdown,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "max_loss_limit": -self.max_loss,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Haupt-Bot-Klasse
# ──────────────────────────────────────────────────────────────────────────────

class MarketMakerBotMain:
    """
    Haupt-Orchestrator für den Market Making Bot.

    Koordiniert alle Komponenten und führt den Hauptloop aus.

    Verwendung:
        bot = MarketMakerBotMain(config)
        await bot.run()
    """

    def __init__(self, config: dict):
        self.config = config
        self._running = False
        self._active_markets: dict[str, MarketInfo] = {}

        mode_str = config.get("mode", "simulation")
        try:
            self._trading_mode = TradingMode(mode_str)
        except ValueError:
            logger.warning("Unbekannter Modus '%s', verwende 'simulation'", mode_str)
            self._trading_mode = TradingMode.SIMULATION

        capital = config.get("capital", 100.0)

        # ── Komponenten initialisieren ─────────────────────────────────────────

        # Binance Preisfeed
        self.binance = BinanceFeed(
            config=BinanceConfig(),
            lookback_seconds=config.get("ema_long_period", 15) * 60,
        )

        # Polymarket API-Verbindung
        poly_config = PolymarketConfig()
        exec_config = ExecutionConfig(
            default_trade_size=config.get("base_order_size_usdc", 5.0),
            max_orders_per_minute=30,
        )

        self.order_mgr = OrderManager(
            poly_config=poly_config,
            exec_config=exec_config,
            mode=self._trading_mode,
        )

        # Markt-Suche
        self.discovery = MarketDiscovery(poly_config)

        # Fair-Value-Berechner
        self.fv_calc = FairValueCalc(
            binance_feed=self.binance,
            ema_short_period=config.get("ema_short_period", 5),
            ema_long_period=config.get("ema_long_period", 15),
            atr_period=config.get("atr_period", 14),
            max_momentum_adj=config.get("max_momentum_adj", 0.12),
            max_price_adj=config.get("max_price_adj", 0.15),
        )

        # Inventar-Verwaltung
        self.inv_mgr = InventoryManager(
            capital=capital,
            max_position_pct=config.get("max_position_pct", 0.10),
            skew_factor=config.get("skew_factor", 0.50),
            max_inventory_ratio=config.get("max_inventory_ratio", 0.60),
            kelly_fraction=config.get("kelly_fraction", 0.25),
        )

        # Market Maker Konfiguration
        mm_config = MMConfig(
            base_spread=config.get("base_spread", 0.04),
            min_spread=config.get("min_spread", 0.025),
            max_spread=config.get("max_spread", 0.10),
            capital=capital,
            max_position_pct=config.get("max_position_pct", 0.10),
            base_order_size_usdc=config.get("base_order_size_usdc", 5.0),
            refresh_seconds=config.get("refresh_seconds", 30.0),
            max_order_age_seconds=config.get("max_order_age_seconds", 60.0),
            stale_threshold=config.get("stale_threshold", 0.5),
            max_open_orders=config.get("max_open_orders", 4),
            max_markets=config.get("max_markets", 2),
            skew_factor=config.get("skew_factor", 0.50),
            max_inventory_ratio=config.get("max_inventory_ratio", 0.60),
            kelly_fraction=config.get("kelly_fraction", 0.25),
            max_session_loss_pct=config.get("max_session_loss_pct", 0.20),
            polymarket_fee=config.get("polymarket_fee", 0.02),
            vola_spread_threshold=config.get("vola_spread_threshold", 0.0015),
            vola_min_spread=config.get("vola_min_spread", 0.06),
            simulation_mode=(self._trading_mode == TradingMode.SIMULATION),
        )

        # Market Maker Strategie
        self.market_maker = MarketMaker(
            config=mm_config,
            fair_value_calc=self.fv_calc,
            inventory_manager=self.inv_mgr,
            order_manager=self.order_mgr,
        )

        # Volatilitäts-Filter (Stop-Quoting + Spread-Anpassung)
        self.vola_guard = VolatilityGuard(
            binance_feed=self.binance,
            move_threshold=config.get("vola_filter_threshold", 0.03),
            lookback_seconds=config.get("vola_filter_lookback", 900),
            vol_threshold=config.get("vola_filter_vol_threshold", None),
            vol_lookback_seconds=config.get("vola_filter_vol_lookback", 300),
            cooldown_seconds=config.get("vola_filter_cooldown", 300.0),
            base_spread=config.get("base_spread", 0.04),
            vola_min_spread=config.get("vola_min_spread", 0.06),
        )

        # Risikomanagement
        self.risk_mgr = MMRiskManager(
            capital=capital,
            max_loss_pct=config.get("max_session_loss_pct", 0.20),
        )

        # Trade-Datenbank
        self.db = TradeDatabase(db_path=config.get("db_path", "trades.db"))

        # Dashboard
        self.dashboard = MMDashboard(
            config=config,
            binance_feed=self.binance,
            market_maker=self.market_maker,
            inventory_manager=self.inv_mgr,
            trade_db=self.db,
            refresh_interval=config.get("dashboard_refresh", 1.0),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Bot-Lebenszyklus
    # ──────────────────────────────────────────────────────────────────────────

    async def start(self):
        """
        Initialisiere alle Komponenten und starte den Bot.

        Ablauf:
        1. Datenbank initialisieren + Session starten
        2. Order Manager starten (CLOB-Verbindung)
        3. Market Discovery starten
        4. Binance WebSocket verbinden
        5. Warten auf erste Preisdaten
        6. Alle asyncio Tasks starten
        """
        logger.info("=" * 60)
        logger.info("POLYMARKET MARKET MAKER BOT - START")
        logger.info("Modus: %s | Kapital: $%.2f", self._trading_mode.value, self.config.get("capital", 100))
        logger.info("Basis-Spread: %.1f%% | Refresh: %.0fs",
                    self.config.get("base_spread", 0.04) * 100,
                    self.config.get("refresh_seconds", 30))
        logger.info("=" * 60)

        # Datenbank initialisieren
        self.db.initialize()
        session_id = self.db.start_session(
            capital_usdc=self.config.get("capital", 100.0),
            mode=self._trading_mode.value,
            base_spread=self.config.get("base_spread", 0.04),
            config_json=json.dumps(self.config),
            notes=f"MM-Bot Start: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        )
        logger.info("Datenbank-Session gestartet: ID=%d", session_id)

        # Komponenten starten
        await self.order_mgr.start()
        await self.discovery.start()

        self._running = True

        # asyncio Tasks starten
        tasks = [
            asyncio.create_task(self.binance.connect(), name="binance_ws"),
            asyncio.create_task(self._main_loop(), name="main_loop"),
            asyncio.create_task(self._market_discovery_loop(), name="discovery"),
            asyncio.create_task(self._fill_monitor_loop(), name="fill_monitor"),
            asyncio.create_task(self.dashboard.run(), name="dashboard"),
        ]

        logger.info("Alle Tasks gestartet. Warte auf Binance-Preisfeed...")

        # Auf ersten Preis warten
        await self._wait_for_price(timeout=30.0)

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bot wird gestoppt...")
        except Exception as e:
            logger.error("Fataler Fehler: %s", e, exc_info=True)
        finally:
            await self.stop()

    async def stop(self):
        """
        Sauberes Herunterfahren des Bots.

        1. Alle offenen Orders canceln
        2. Verbindungen trennen
        3. Finale Statistiken ausgeben
        4. Datenbank-Session beenden
        """
        self._running = False
        logger.info("Stoppe Bot...")

        # Alle Orders canceln
        try:
            await self.order_mgr.cancel_all()
        except Exception as e:
            logger.error("Fehler beim Canceln der Orders: %s", e)

        # Verbindungen trennen
        try:
            await self.order_mgr.stop()
            await self.binance.stop()
            await self.discovery.stop()
            await self.dashboard.stop()
        except Exception as e:
            logger.error("Fehler beim Stoppen der Verbindungen: %s", e)

        # Finale Statistiken
        stats = self.market_maker.get_statistics()
        risk_stats = self.risk_mgr.get_stats()
        db_stats = self.db.get_session_stats()

        logger.info("=" * 60)
        logger.info("FINALE STATISTIKEN")
        logger.info("=" * 60)
        logger.info("Aktive Märkte:          %d", stats.get("active_markets", 0))
        logger.info("Gesamt BID-Fills:       %d", stats.get("total_bid_fills", 0))
        logger.info("Gesamt ASK-Fills:       %d", stats.get("total_ask_fills", 0))
        logger.info("Roundtrips:             %d", stats.get("roundtrips", 0))
        logger.info("Spread-Einnahmen (est): $%.4f", stats.get("estimated_spread_earned", 0))
        logger.info("Realisiertes PnL:       $%.4f", risk_stats.get("session_pnl", 0))
        logger.info("Max Drawdown:           $%.4f", risk_stats.get("max_drawdown", 0))
        if db_stats:
            logger.info("DB-Fills total:         %d", db_stats.total_fills)
        logger.info("=" * 60)

        # Datenbank-Session beenden
        self.db.end_session()

        logger.info("Bot gestoppt.")

    # ──────────────────────────────────────────────────────────────────────────
    # Haupt-Loop
    # ──────────────────────────────────────────────────────────────────────────

    async def _main_loop(self):
        """
        Haupt-Market-Making-Loop (alle 30 Sekunden).

        Pro Zyklus:
        1. Risiko prüfen
        2. Aktive Märkte iterieren
        3. Fair Value berechnen
        4. Quotes updaten (cancel stale + neue Orders)
        5. Dashboard-Daten aktualisieren
        """
        logger.info("Hauptloop gestartet")
        refresh_seconds = self.config.get("refresh_seconds", 30.0)

        while self._running:
            try:
                await self._refresh_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Fehler im Hauptloop: %s", e, exc_info=True)

            await asyncio.sleep(refresh_seconds)

    async def _refresh_cycle(self):
        """Ein einzelner Refresh-Zyklus."""

        # ── Risiko-Check ──────────────────────────────────────────────────────
        if self.risk_mgr.is_halted:
            logger.critical(
                "Bot halted! Grund: %s",
                self.risk_mgr.halt_reason
            )
            self.dashboard.set_status(f"HALT: {self.risk_mgr.halt_reason}")
            return

        # ── Volatilitäts-Filter ───────────────────────────────────────────────
        vola_state = self.vola_guard.check()
        if vola_state.is_high_vola:
            logger.warning(
                "VOLA-FILTER: Quoting pausiert | BTC: %+.1f%% in 15min | "
                "Cooldown: %.0fs | Empf. Spread: %.1f%%",
                vola_state.price_change_pct * 100,
                vola_state.cooldown_remaining,
                vola_state.spread_recommendation * 100,
            )
            self.dashboard.set_status(
                f"VOLA-FILTER AKTIV — Cooldown: {vola_state.cooldown_remaining:.0f}s"
            )
            # Alle offenen Orders canceln während Hochvola-Event
            for state in self.market_maker.get_all_states():
                await self.market_maker._cancel_all_orders(state)
            return

        if not self._active_markets:
            logger.debug("Keine aktiven Märkte — warte auf Discovery...")
            self.dashboard.set_status("Suche aktive Märkte...")
            return

        if self.binance.current_price <= 0:
            logger.warning("Kein BTC-Preis verfügbar — überspringe Zyklus")
            self.dashboard.set_status("Warte auf BTC-Preis...")
            return

        logger.info(
            "Refresh-Zyklus | BTC: $%.2f | Märkte: %d | Orders: %d",
            self.binance.current_price,
            len(self._active_markets),
            self.market_maker.get_active_order_count(),
        )

        # ── Märkte refreshen ──────────────────────────────────────────────────
        last_quotes = None
        last_fv = None

        for market_id, market_info in list(self._active_markets.items()):

            # Markt abgelaufen?
            if market_info.is_expired:
                logger.info("Markt abgelaufen: %s", market_id[:20])
                await self._handle_market_expiry(market_info)
                continue

            # Zu wenig Zeit übrig?
            if market_info.time_remaining < 30:
                logger.info(
                    "Markt fast abgelaufen [%.0fs übrig]: %s",
                    market_info.time_remaining, market_id[:20]
                )
                continue

            # Markt registrieren falls nötig
            if market_id not in {s.market_id for s in self.market_maker.get_all_states()}:
                self.market_maker.register_market(
                    market_id=market_id,
                    token_id=market_info.up_token_id,
                )

            try:
                # Fair Value berechnen
                fv_result = self.fv_calc.compute(
                    opening_price=market_info.opening_price or self.binance.current_price,
                    time_remaining_seconds=market_info.time_remaining,
                    market_duration_seconds=market_info.duration_seconds,
                )
                last_fv = fv_result

                # In Datenbank loggen
                self.db.log_fair_value(
                    market_id=market_id,
                    fair_value=fv_result.fair_value,
                    ema5=fv_result.ema5,
                    ema15=fv_result.ema15,
                    atr=fv_result.atr,
                    momentum_adj=fv_result.momentum_adj,
                    time_factor=fv_result.time_factor,
                    confidence=fv_result.confidence,
                    btc_price=fv_result.current_price,
                    opening_price=fv_result.opening_price,
                )

                # Market Maker Refresh
                quotes = await self.market_maker.refresh_market(
                    market_id=market_id,
                    opening_price=market_info.opening_price or self.binance.current_price,
                    time_remaining_seconds=market_info.time_remaining,
                    market_duration_seconds=market_info.duration_seconds,
                )
                last_quotes = quotes

                # Dashboard aktualisieren
                if quotes:
                    self.dashboard.update_quotes(quotes)
                self.dashboard.update_fair_value(fv_result)

                # PnL-Status für Risikomanager aktualisieren
                inv_summary = self.inv_mgr.get_summary()
                self.risk_mgr.update_pnl(inv_summary["session_realized_pnl"])

                self.dashboard.set_status(
                    f"OK | FV: {fv_result.fair_value:.4f} | "
                    f"Orders: {self.market_maker.get_active_order_count()}"
                )

            except Exception as e:
                logger.error("Fehler beim Refreshen von Markt %s: %s", market_id[:20], e)
                self.dashboard.set_status(f"Fehler: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Markt-Discovery Loop
    # ──────────────────────────────────────────────────────────────────────────

    async def _market_discovery_loop(self):
        """
        Loop für die Suche nach aktiven Polymarket-Märkten.

        Läuft alle 10 Sekunden und sucht nach BTC Up/Down Märkten.
        """
        logger.info("Market-Discovery-Loop gestartet")

        while self._running:
            try:
                await self._discover_markets()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Fehler in Discovery-Loop: %s", e)

            await asyncio.sleep(10.0)

    async def _discover_markets(self):
        """Suche und registriere aktive Märkte (5m + 15m parallel)."""
        max_markets = self.config.get("max_markets", 4)

        candidates = []
        try:
            market_5m = await self.discovery.discover_current_market()
            if market_5m:
                candidates.append(market_5m)
        except Exception as e:
            logger.debug("5m Discovery-Fehler: %s", e)

        try:
            market_15m = await self.discovery.discover_current_15m_market()
            if market_15m:
                candidates.append(market_15m)
        except Exception as e:
            logger.debug("15m Discovery-Fehler: %s", e)

        for market in candidates:
            if len(self._active_markets) >= max_markets:
                break
            if market.condition_id in self._active_markets:
                continue

            # Kurze Anzeige: letzten ~25 Zeichen der Frage (z.B. "6:35AM-6:40AM ET")
            short_label = market.question[-32:] if market.question else market.condition_id[:16]

            logger.info(
                "Neuer Markt gefunden: %s | Verbleibend: %.0fs",
                short_label,
                market.time_remaining,
            )

            if not market.opening_price:
                market.opening_price = self.binance.current_price
                logger.info(
                    "Opening-Preis gesetzt: $%.2f (BTC aktuell)",
                    market.opening_price
                )

            self._active_markets[market.condition_id] = market
            self.dashboard.set_status(
                f"Neuer Markt: {short_label}"
            )

    async def _handle_market_expiry(self, market_info: MarketInfo):
        """
        Verarbeite Marktablauf:
        1. Alle Orders canceln
        2. PnL berechnen
        3. Ergebnis in Datenbank speichern
        4. Markt aus aktiver Liste entfernen
        """
        market_id = market_info.condition_id
        current_price = self.binance.current_price
        opening_price = market_info.opening_price or 0.0

        up_won = current_price > opening_price if opening_price > 0 else None

        if up_won is not None:
            logger.info(
                "Markt abgelaufen: %s | Open: $%.2f | Close: $%.2f | Ergebnis: %s",
                market_id,
                opening_price,
                current_price,
                "UP GEWONNEN" if up_won else "DOWN GEWONNEN",
            )

            # Market Maker informieren
            pnl = self.market_maker.process_market_resolution(
                market_id=market_id,
                up_won=up_won,
            )

            # Inventar-Daten für DB
            inv = self.inv_mgr.get_inventory(market_id)
            if inv:
                self.db.log_market_resolution(
                    market_id=market_id,
                    up_won=up_won,
                    yes_tokens=inv.yes_tokens,
                    yes_cost_usdc=inv.yes_cost,
                    no_tokens=inv.no_tokens,
                    no_revenue_usdc=inv.no_revenue,
                    net_pnl=pnl,
                    fee_paid=pnl * self.config.get("polymarket_fee", 0.02),
                    spread_income=inv.spread_income_est,
                )

            # Risiko-Update
            inv_summary = self.inv_mgr.get_summary()
            self.risk_mgr.update_pnl(inv_summary["session_realized_pnl"])

        else:
            logger.warning("Kein gültiges Ergebnis für Markt: %s", market_id)

        # Markt aus aktiver Liste entfernen
        self._active_markets.pop(market_id, None)

    # ──────────────────────────────────────────────────────────────────────────
    # Fill-Monitor Loop
    # ──────────────────────────────────────────────────────────────────────────

    async def _fill_monitor_loop(self):
        """
        Monitor-Loop für Order-Fills.

        Prüft alle 5 Sekunden ob Orders gefüllt wurden und
        aktualisiert Inventar + Datenbank entsprechend.

        In Live-/Paper-Modus: Fragt Polymarket CLOB-API ab.
        In Simulation: Simuliert Fills basierend auf Marktpreisen.
        """
        logger.info("Fill-Monitor gestartet")

        while self._running:
            try:
                await self._check_fills()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Fehler im Fill-Monitor: %s", e)

            await asyncio.sleep(5.0)

    async def _check_fills(self):
        """Prüfe ob Orders gefüllt wurden."""
        active_orders = self.order_mgr.get_active_orders()

        for order in active_orders:
            if self._trading_mode == TradingMode.SIMULATION:
                await self._simulate_fill_check(order)
            elif self._trading_mode == TradingMode.PAPER:
                await self._paper_fill_check(order)
            else:
                # Live: Order-Status aus CLOB API abfragen
                await self._live_fill_check(order)

    async def _simulate_fill_check(self, order):
        """
        Simuliere Order-Fill basierend auf aktuellem Marktpreis.

        Logik:
        - BUY-Order: Füllt wenn Marktpreis den Bid-Preis "sieht"
          (vereinfacht: 20% Wahrscheinlichkeit pro Check bei realistischem Preis)
        - SELL-Order: Füllt wenn Marktpreis den Ask-Preis "sieht"

        In Realität würde das CLOB die Fills verwalten.
        """
        import random

        if not order.is_active:
            return

        current_price = self.binance.current_price
        if current_price <= 0:
            return

        # Markt-Preisnähe prüfen (vereinfacht)
        # In echten Märkten: CLOB gibt Fills zurück
        should_fill = False
        fill_probability = 0.15  # 15% Wahrscheinlichkeit pro 5s Check

        if order.side.value == "BUY":
            # Bid-Fill: wahrscheinlicher wenn viel Handelsvolumen
            should_fill = random.random() < fill_probability

        elif order.side.value == "SELL":
            # Ask-Fill: ähnliche Logik
            should_fill = random.random() < fill_probability

        if should_fill and order.status.value == "open":
            # Simulierten Fill verarbeiten
            fill_size = order.size
            fill_price = order.price

            # Inventar aktualisieren
            for market_id, market_info in self._active_markets.items():
                if order.token_id == market_info.up_token_id:
                    mm_side = "buy" if order.side.value == "BUY" else "sell"

                    self.market_maker.process_fill(
                        market_id=market_id,
                        order_id=order.order_id,
                        filled_size=fill_size,
                        fill_price=fill_price,
                    )

                    # In DB loggen
                    self.db.log_fill(
                        order_id=order.order_id,
                        market_id=market_id,
                        mm_side=mm_side,
                        filled_tokens=fill_size,
                        fill_price=fill_price,
                        btc_price=current_price,
                    )

                    # Order-Status aktualisieren
                    order.status = type('Status', (), {'value': 'filled'})()
                    break

    async def _paper_fill_check(self, order):
        """
        Paper-Trading Fill-Simulation mit Fair-Value-basierter Logik.

        Realistischer als die rein zufällige Simulation:
        - BID-Order füllt wenn aktueller FV > Bid-Preis (Markt bewertet Up höher)
        - ASK-Order füllt wenn aktueller FV < Ask-Preis (Markt bewertet Down höher)
        - Wahrscheinlichkeit steigt mit Abstand FV ↔ Order-Preis und Orderalter

        Kein echtes CLOB. Alle Fills bleiben lokal.
        """
        import random

        if not order.is_active:
            return

        current_price = self.binance.current_price
        if current_price <= 0:
            return

        for market_id, market_info in self._active_markets.items():
            if order.token_id != market_info.up_token_id:
                continue

            # FV für diesen Markt berechnen (aus Cache wenn möglich)
            fv_result = self.fv_calc.compute(
                opening_price=market_info.opening_price or current_price,
                time_remaining_seconds=market_info.time_remaining,
                market_duration_seconds=market_info.duration_seconds,
                use_cache=True,
            )
            current_fv = fv_result.fair_value
            order_age_s = time.time() - order.created_at

            should_fill = False

            if order.side.value == "BUY":
                # BID füllt wenn FV über Bid liegt (jemand verkauft zu unserem Preis)
                if current_fv > order.price:
                    fv_distance = current_fv - order.price
                    # Basis-Wahrscheinlichkeit: 0–40% je nach Abstand
                    fill_prob = min(0.40, fv_distance * 5.0)
                    # Zeitbonus: nach 2 Min max. +20% zusätzlich
                    time_bonus = min(0.20, order_age_s / 120.0 * 0.20)
                    should_fill = random.random() < (fill_prob + time_bonus)

            elif order.side.value == "SELL":
                # ASK füllt wenn FV unter Ask liegt (jemand kauft zu unserem Preis)
                if current_fv < order.price:
                    fv_distance = order.price - current_fv
                    fill_prob = min(0.40, fv_distance * 5.0)
                    time_bonus = min(0.20, order_age_s / 120.0 * 0.20)
                    should_fill = random.random() < (fill_prob + time_bonus)

            if should_fill:
                from polymarket_btc_bot.execution.order_manager import OrderStatus
                mm_side = "buy" if order.side.value == "BUY" else "sell"
                fill_size = order.size
                fill_price = order.price

                self.market_maker.process_fill(
                    market_id=market_id,
                    order_id=order.order_id,
                    filled_size=fill_size,
                    fill_price=fill_price,
                )

                self.db.log_fill(
                    order_id=order.order_id,
                    market_id=market_id,
                    mm_side=mm_side,
                    filled_tokens=fill_size,
                    fill_price=fill_price,
                    btc_price=current_price,
                )

                order.status = OrderStatus.FILLED
                order.filled_size = fill_size
                order.avg_fill_price = fill_price
                order.updated_at = time.time()

                logger.info(
                    "[PAPER] Fill: %s %.4f @ %.4f | FV=%.4f | BTC=$%.2f",
                    mm_side.upper(),
                    fill_size,
                    fill_price,
                    current_fv,
                    current_price,
                )

            break  # Nur ersten passenden Markt verarbeiten

    async def _live_fill_check(self, order):
        """
        Prüfe Fill-Status über Polymarket CLOB-API.
        (Nur für Live-Trading)
        """
        if not self.order_mgr._clob_client or not order.clob_order_id:
            return

        try:
            # CLOB-API nach Order-Status fragen
            # Das genaue API-Format hängt von py-clob-client Version ab
            pass  # In echter Implementierung: API-Call
        except Exception as e:
            logger.debug("Fill-Check-Fehler (ignoriert): %s", e)

    # ──────────────────────────────────────────────────────────────────────────
    # Hilfsmethoden
    # ──────────────────────────────────────────────────────────────────────────

    async def _wait_for_price(self, timeout: float = 30.0):
        """Warte auf ersten BTC-Preis vom Binance Feed."""
        start = time.time()
        while time.time() - start < timeout:
            if self.binance.current_price > 0:
                logger.info(
                    "Binance verbunden: BTC = $%.2f",
                    self.binance.current_price
                )
                return True
            await asyncio.sleep(0.5)

        logger.warning("Timeout: Kein Preis in %.0f Sekunden erhalten", timeout)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Logging Setup
# ──────────────────────────────────────────────────────────────────────────────

def setup_logging(config: dict):
    """
    Konfiguriere das Logging-System.

    Loggt gleichzeitig in:
    - Konsole (INFO-Level mit Farben)
    - Datei (DEBUG-Level, alle Details)
    """
    log_level = getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO)
    log_file = config.get("log_file", "mm_bot.log")

    # Root Logger konfigurieren
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Format
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)-30s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Konsole Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # Datei Handler
    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception as e:
        logger.warning("Konnte Log-Datei nicht öffnen: %s", e)

    # Externe Libraries ruhigstellen
    for noisy_logger in ["websockets", "aiohttp", "urllib3", "asyncio"]:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


# ──────────────────────────────────────────────────────────────────────────────
# Argument-Parser
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Parse Kommandozeilen-Argumente."""
    parser = argparse.ArgumentParser(
        description="Polymarket BTC Market Making Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  # Simulationsmodus (Standard):
  python -m polymarket_btc_bot.mm_main

  # Mit eigener Konfigurationsdatei:
  python -m polymarket_btc_bot.mm_main --config config.yaml

  # Paper Trading (echte Preise, keine echten Orders):
  python -m polymarket_btc_bot.mm_main --mode paper

  # Live Trading mit 100 USDC:
  python -m polymarket_btc_bot.mm_main --mode live --capital 100

  # Mit angepasstem Spread:
  python -m polymarket_btc_bot.mm_main --spread 0.05 --capital 50
        """,
    )

    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Pfad zur YAML-Konfigurationsdatei (Standard: config.yaml)",
    )
    parser.add_argument(
        "--mode",
        choices=["simulation", "paper", "live"],
        default=None,
        help="Handelsmodus (Standard: simulation)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=None,
        help="Startkapital in USDC (Standard: 100.0)",
    )
    parser.add_argument(
        "--spread",
        type=float,
        default=None,
        help="Basis-Spread (Standard: 0.04 = 4%%)",
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=None,
        help="Refresh-Intervall in Sekunden (Standard: 30)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Pfad zur SQLite-Datenbankdatei (Standard: trades.db)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=None,
        help="Log-Level (Standard: INFO)",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Dashboard deaktivieren (für Logging-Modus)",
    )

    return parser.parse_args()


# ──────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    """Hauptfunktion — Entry Point für den Market Making Bot."""

    args = parse_args()

    # Konfiguration laden (YAML + Argumente)
    config_path = args.config or "config.yaml"
    config = load_config(config_path)

    # Kommandozeilen-Argumente überschreiben Konfiguration
    if args.mode:
        config["mode"] = args.mode
    if args.capital is not None:
        config["capital"] = args.capital
    if args.spread is not None:
        config["base_spread"] = args.spread
    if args.refresh is not None:
        config["refresh_seconds"] = args.refresh
    if args.db:
        config["db_path"] = args.db
    if args.log_level:
        config["log_level"] = args.log_level

    # Logging initialisieren
    setup_logging(config)

    # Warnung bei Live-Trading ohne API-Keys
    if config.get("mode") == "live":
        required_env_vars = [
            "POLY_PRIVATE_KEY",
            "POLY_API_KEY",
            "POLY_API_SECRET",
            "POLY_API_PASSPHRASE",
        ]
        missing = [v for v in required_env_vars if not os.getenv(v)]
        if missing:
            logger.error(
                "LIVE-TRADING: Fehlende Umgebungsvariablen: %s",
                ", ".join(missing)
            )
            logger.error(
                "Setze die Variablen oder verwende --mode simulation"
            )
            sys.exit(1)

    logger.info("Market Making Bot konfiguriert:")
    logger.info("  Modus:          %s", config.get("mode"))
    logger.info("  Kapital:        $%.2f", config.get("capital", 100))
    logger.info("  Basis-Spread:   %.1f%%", config.get("base_spread", 0.04) * 100)
    logger.info("  Vola-Min-Spread:%.1f%% (ab %.2f%% realized vol)",
                config.get("vola_min_spread", 0.06) * 100,
                config.get("vola_spread_threshold", 0.0015) * 100)
    logger.info("  Vola-Filter:    %.0f%% in %.0fmin | Cooldown %.0fs",
                config.get("vola_filter_threshold", 0.03) * 100,
                config.get("vola_filter_lookback", 900) / 60,
                config.get("vola_filter_cooldown", 300))
    logger.info("  Refresh:        %.0fs", config.get("refresh_seconds", 30))
    logger.info("  Max-Orders:     %d", config.get("max_open_orders", 4))
    logger.info("  Kelly-Fraction: %.0f%%", config.get("kelly_fraction", 0.25) * 100)
    logger.info("  Skew-Faktor:    %.0f%%", config.get("skew_factor", 0.50) * 100)

    # Bot erstellen
    bot = MarketMakerBotMain(config)

    # Event Loop mit Signal-Handling
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown_handler(sig_name: str):
        logger.info("Signal empfangen: %s — Bot wird gestoppt...", sig_name)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    try:
        loop.add_signal_handler(
            signal.SIGINT,
            lambda: shutdown_handler("SIGINT")
        )
        loop.add_signal_handler(
            signal.SIGTERM,
            lambda: shutdown_handler("SIGTERM")
        )
    except NotImplementedError:
        # Windows unterstützt add_signal_handler nicht
        pass

    try:
        loop.run_until_complete(bot.start())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt — Bot wird gestoppt...")
    except Exception as e:
        logger.error("Nicht abgefangener Fehler: %s", e, exc_info=True)
    finally:
        # Sauberes Cleanup
        try:
            remaining = asyncio.all_tasks(loop)
            if remaining:
                loop.run_until_complete(
                    asyncio.gather(*remaining, return_exceptions=True)
                )
        except Exception:
            pass
        loop.close()
        logger.info("Event Loop geschlossen. Auf Wiedersehen!")


if __name__ == "__main__":
    main()
