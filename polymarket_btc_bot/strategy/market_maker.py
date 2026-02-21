"""
MarketMaker: Kernstrategie für Market Making auf Polymarket BTC Up/Down Märkten.

──────────────────────────────────────────────────────────────────────────────
Strategie-Übersicht:
──────────────────────────────────────────────────────────────────────────────
Market Making = kontinuierliches Quoten BEIDER Seiten (BID und ASK) mit
definiertem Spread. Ziel: Spread-Einnahmen, nicht Richtungshandel.

Ablauf pro Refresh-Zyklus (alle 30 Sekunden):
  1. Fair Value berechnen (FairValueCalc)
  2. Spread-Breite ermitteln (Konfidenz-basiert)
  3. Inventory Skewing anwenden (InventoryManager)
  4. Aktuelle Orders prüfen: stale Orders canceln
  5. Neue Orders platzieren (BID + ASK)
  6. Risiko prüfen (RiskManager)

BID-Order:  Wir kaufen YES-Tokens wenn Preis unseren Bid erreicht
ASK-Order:  Wir verkaufen YES-Tokens wenn Preis unseren Ask erreicht

Spread-Einnahme-Logik:
  Fair Value = 0.50
  Bid = 0.475, Ask = 0.525
  → Wenn beide Seiten gefüllt werden: Einnahme = 0.525 - 0.475 = $0.05 pro Token
  → ROI pro Roundtrip ≈ 5.26% (vor Gebühren)

──────────────────────────────────────────────────────────────────────────────
Stale Order Detection:
──────────────────────────────────────────────────────────────────────────────
Orders gelten als "stale" wenn:
  - Der Fair Value sich um mehr als (spread/2) verschoben hat
  - Die Order älter als max_order_age_seconds ist
  - Das Inventar eine starke Skew-Anpassung erfordert

──────────────────────────────────────────────────────────────────────────────
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from polymarket_btc_bot.execution.inventory_manager import InventoryManager, QuoteAdjustment
from polymarket_btc_bot.execution.order_manager import Order, OrderManager, OrderSide, OrderStatus, OrderType
from polymarket_btc_bot.strategy.fair_value import FairValueCalc, FairValueResult

logger = logging.getLogger(__name__)


class MMOrderSide(Enum):
    BID = "bid"   # Kauf-Order (wir kaufen YES-Tokens)
    ASK = "ask"   # Verkauf-Order (wir verkaufen YES-Tokens)


@dataclass
class ActiveMMOrder:
    """Aktive Market-Making-Order mit Tracking-Informationen."""

    order: Order
    mm_side: MMOrderSide       # Bid oder Ask
    market_id: str
    fair_value_at_placement: float
    half_spread_at_placement: float
    placed_at: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        """Alter der Order in Sekunden."""
        return time.time() - self.placed_at

    @property
    def is_active(self) -> bool:
        """True wenn die Order noch aktiv (offen) ist."""
        return self.order.is_active


@dataclass
class MarketMMState:
    """Zustand des Market Makers für einen einzelnen Markt."""

    market_id: str
    token_id: str
    up_won_side: bool          # True wenn wir YES-Tokens für "Up" tracken

    # Aktive Orders
    bid_order: Optional[ActiveMMOrder] = None
    ask_order: Optional[ActiveMMOrder] = None

    # Letzte bekannte Fair-Value-Berechnung
    last_fair_value: Optional[FairValueResult] = None
    last_refresh: float = 0.0

    # Statistiken
    total_bid_fills: int = 0
    total_ask_fills: int = 0
    total_spread_earned: float = 0.0
    refresh_count: int = 0

    @property
    def has_active_bid(self) -> bool:
        return self.bid_order is not None and self.bid_order.is_active

    @property
    def has_active_ask(self) -> bool:
        return self.ask_order is not None and self.ask_order.is_active

    @property
    def active_order_count(self) -> int:
        return int(self.has_active_bid) + int(self.has_active_ask)


@dataclass
class MMConfig:
    """Konfiguration für den Market Maker."""

    # ── Spread-Parameter ──────────────────────────────────────────────────────
    base_spread: float = 0.04          # Basis-Spread (z.B. 0.04 = 4%)
    min_spread: float = 0.025          # Minimaler Spread
    max_spread: float = 0.10           # Maximaler Spread

    # ── Position / Kapital ────────────────────────────────────────────────────
    capital: float = 100.0             # Gesamtkapital USDC
    max_position_pct: float = 0.10     # Max. Position pro Markt (10%)
    base_order_size_usdc: float = 5.0  # Basis-Ordergröße USDC

    # ── Refresh / Timing ──────────────────────────────────────────────────────
    refresh_seconds: float = 30.0      # Refresh-Intervall
    max_order_age_seconds: float = 60.0  # Max. Order-Alter vor Erneuerung
    stale_threshold: float = 0.5       # Ab dieser FV-Verschiebung: Order canceln
                                       # (als Anteil des halben Spreads)

    # ── Limits ────────────────────────────────────────────────────────────────
    max_open_orders: int = 4           # Gesamt max. offene Orders (2 pro Markt)
    max_markets: int = 2               # Max. Märkte gleichzeitig

    # ── Inventory Skewing ─────────────────────────────────────────────────────
    skew_factor: float = 0.50          # Skewing-Stärke [0.0–1.0]
    max_inventory_ratio: float = 0.60  # Max. Imbalance bevor Quoting gestoppt

    # ── Kelly Criterion ───────────────────────────────────────────────────────
    kelly_fraction: float = 0.25       # 25% Kelly (für Sicherheit)

    # ── Risiko ────────────────────────────────────────────────────────────────
    max_session_loss_pct: float = 0.20  # Max. Verlust pro Session (20%)
    polymarket_fee: float = 0.02       # Polymarket Gebühr auf Gewinne (2%)

    # ── Simulation ────────────────────────────────────────────────────────────
    simulation_mode: bool = True       # True = keine echten Orders


class MarketMaker:
    """
    Market-Making-Strategie für Polymarket BTC Up/Down Märkte.

    Verantwortlichkeiten:
    - Koordiniert FairValueCalc, InventoryManager, OrderManager
    - Entscheidet wann und zu welchen Preisen Orders platziert werden
    - Erkennt stale Orders und aktualisiert Quotes
    - Überwacht Inventar-Imbalance und skewed Quotes entsprechend

    Verwendung:
        mm = MarketMaker(
            config=MMConfig(base_spread=0.04),
            fair_value_calc=fv_calc,
            inventory_manager=inv_mgr,
            order_manager=order_mgr,
        )
        await mm.refresh_market(
            market_id="btc-up-15min-xyz",
            token_id="0xABC...",
            opening_price=42000.0,
            time_remaining=300,
            market_duration=900,
        )
    """

    def __init__(
        self,
        config: MMConfig,
        fair_value_calc: FairValueCalc,
        inventory_manager: InventoryManager,
        order_manager: OrderManager,
    ):
        self.config = config
        self.fv_calc = fair_value_calc
        self.inv_mgr = inventory_manager
        self.order_mgr = order_manager

        # Aktive Märkte: market_id → MarketMMState
        self._markets: dict[str, MarketMMState] = {}

        # Statistiken
        self._total_bid_fills: int = 0
        self._total_ask_fills: int = 0
        self._total_spread_earned: float = 0.0
        self._total_cancelled_stale: int = 0

    # ──────────────────────────────────────────────────────────────────────────
    # Markt-Verwaltung
    # ──────────────────────────────────────────────────────────────────────────

    def register_market(
        self,
        market_id: str,
        token_id: str,
        up_won_side: bool = True,
    ):
        """
        Registriere einen Markt für Market Making.

        Args:
            market_id:    Eindeutige Markt-ID
            token_id:     YES-Token Contract ID
            up_won_side:  True wenn YES-Token = "BTC Up gewinnt"
        """
        if market_id in self._markets:
            logger.debug("Markt bereits registriert: %s", market_id[:20])
            return

        self._markets[market_id] = MarketMMState(
            market_id=market_id,
            token_id=token_id,
            up_won_side=up_won_side,
        )

        logger.info("Markt registriert für MM: %s", market_id[:30])

    def unregister_market(self, market_id: str):
        """Entferne Markt aus dem Market-Making (z.B. nach Ablauf)."""
        if market_id in self._markets:
            self._markets.pop(market_id)
            logger.info("Markt abgemeldet: %s", market_id[:20])

    # ──────────────────────────────────────────────────────────────────────────
    # Kern-Logik: Refresh
    # ──────────────────────────────────────────────────────────────────────────

    async def refresh_market(
        self,
        market_id: str,
        opening_price: float,
        time_remaining_seconds: float,
        market_duration_seconds: float,
    ) -> Optional[QuoteAdjustment]:
        """
        Führe einen vollständigen Market-Making-Zyklus für einen Markt durch.

        Ablauf:
        1. Fair Value berechnen
        2. Spread-Breite festlegen
        3. Quotes mit Inventory Skewing berechnen
        4. Stale Orders identifizieren und canceln
        5. Neue Orders platzieren
        6. Zustand aktualisieren

        Args:
            market_id:                Markt-ID
            opening_price:            BTC-Preis bei Markteröffnung
            time_remaining_seconds:   Verbleibende Zeit bis Marktschluss
            market_duration_seconds:  Gesamtdauer des Marktes

        Returns:
            QuoteAdjustment mit platzierten Quotes, oder None wenn keine Orders
        """
        if market_id not in self._markets:
            logger.error("Markt nicht registriert: %s", market_id[:20])
            return None

        state = self._markets[market_id]

        # Kein Trading nahe Ablauf (letzte 30 Sekunden)
        if time_remaining_seconds < 30:
            logger.info(
                "[%s] Kein Quoting: Ablauf in %.0fs",
                market_id[:16], time_remaining_seconds
            )
            await self._cancel_all_orders(state)
            return None

        # ── Schritt 1: Fair Value berechnen ───────────────────────────────────
        fv_result = self.fv_calc.compute(
            opening_price=opening_price,
            time_remaining_seconds=time_remaining_seconds,
            market_duration_seconds=market_duration_seconds,
        )
        state.last_fair_value = fv_result

        logger.info(
            "[%s] FV: %.4f | Conf: %.2f | %s",
            market_id[:16],
            fv_result.fair_value,
            fv_result.confidence,
            str(fv_result),
        )

        # ── Schritt 2: Spread-Breite festlegen ────────────────────────────────
        spread = self.fv_calc.compute_spread_width(
            confidence=fv_result.confidence,
            base_spread=self.config.base_spread,
            min_spread=self.config.min_spread,
            max_spread=self.config.max_spread,
        )
        half_spread = spread / 2.0

        # ── Schritt 3: Quotes mit Inventory Skewing ───────────────────────────
        quotes = self.inv_mgr.compute_quotes(
            market_id=market_id,
            token_id=state.token_id,
            fair_value=fv_result.fair_value,
            half_spread=half_spread,
            base_order_size_usdc=self.config.base_order_size_usdc,
        )

        logger.info(
            "[%s] Quotes: %s",
            market_id[:16], str(quotes)
        )

        # ── Schritt 4: Stale Orders canceln ───────────────────────────────────
        await self._cancel_stale_orders(state, fv_result.fair_value, half_spread)

        # ── Schritt 5: Neue Orders platzieren ─────────────────────────────────
        await self._place_quotes(state, quotes)

        # ── Schritt 6: Zustand aktualisieren ──────────────────────────────────
        state.last_refresh = time.time()
        state.refresh_count += 1

        return quotes

    async def refresh_all(
        self,
        market_states: dict[str, dict],
    ):
        """
        Refreshe alle registrierten Märkte gleichzeitig.

        Args:
            market_states: Dictionary market_id → {opening_price, time_remaining, duration}
        """
        tasks = []
        for market_id, state_info in market_states.items():
            if market_id in self._markets:
                task = self.refresh_market(
                    market_id=market_id,
                    opening_price=state_info["opening_price"],
                    time_remaining_seconds=state_info["time_remaining"],
                    market_duration_seconds=state_info["duration"],
                )
                tasks.append(task)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error("Refresh-Fehler: %s", r)

    # ──────────────────────────────────────────────────────────────────────────
    # Order-Verwaltung
    # ──────────────────────────────────────────────────────────────────────────

    async def _cancel_stale_orders(
        self,
        state: MarketMMState,
        current_fair_value: float,
        half_spread: float,
    ):
        """
        Cancle stale Orders (zu alt oder zu weit vom Fair Value entfernt).

        Stale-Kriterien:
        1. Order-Alter > max_order_age_seconds
        2. Fair-Value-Verschiebung > stale_threshold × half_spread
        """
        for mm_order in [state.bid_order, state.ask_order]:
            if mm_order is None or not mm_order.is_active:
                continue

            should_cancel = False
            cancel_reason = ""

            # Kriterium 1: Zu alt
            if mm_order.age_seconds > self.config.max_order_age_seconds:
                should_cancel = True
                cancel_reason = f"Alter ({mm_order.age_seconds:.0f}s > {self.config.max_order_age_seconds}s)"

            # Kriterium 2: Fair Value hat sich verschoben
            fv_shift = abs(current_fair_value - mm_order.fair_value_at_placement)
            stale_threshold_price = self.config.stale_threshold * half_spread
            if fv_shift > stale_threshold_price:
                should_cancel = True
                cancel_reason = f"FV-Verschiebung ({fv_shift:.4f} > {stale_threshold_price:.4f})"

            if should_cancel:
                logger.info(
                    "[%s] Cancle stale %s-Order: %s",
                    state.market_id[:16],
                    mm_order.mm_side.value.upper(),
                    cancel_reason,
                )
                success = await self.order_mgr.cancel_order(mm_order.order.order_id)
                if success:
                    self._total_cancelled_stale += 1
                    if mm_order.mm_side == MMOrderSide.BID:
                        state.bid_order = None
                    else:
                        state.ask_order = None

    async def _cancel_all_orders(self, state: MarketMMState):
        """Cancle alle aktiven Orders für einen Markt."""
        for mm_order in [state.bid_order, state.ask_order]:
            if mm_order and mm_order.is_active:
                await self.order_mgr.cancel_order(mm_order.order.order_id)

        state.bid_order = None
        state.ask_order = None
        logger.info("[%s] Alle Orders gecancelt", state.market_id[:16])

    async def _place_quotes(
        self,
        state: MarketMMState,
        quotes: QuoteAdjustment,
    ):
        """
        Platziere BID und ASK Orders wenn nötig.

        Regeln:
        - Keine neue BID-Order wenn bereits aktive BID vorhanden
        - Keine neue ASK-Order wenn bereits aktive ASK vorhanden
        - Max. 2 Orders pro Markt (config.max_open_orders / 2)
        - Inventar-Checks: Nicht quoten wenn zu starke Imbalance
        """
        tasks = []

        # ── BID-Order ─────────────────────────────────────────────────────────
        if not state.has_active_bid:
            if not self.inv_mgr.should_stop_quoting(state.market_id, "buy"):
                if quotes.bid_size_usdc >= 0.50:
                    # Token-Größe = USDC-Größe / Preis
                    bid_tokens = quotes.bid_size_usdc / quotes.bid_price
                    tasks.append(("bid", quotes.bid_price, bid_tokens))

        # ── ASK-Order ─────────────────────────────────────────────────────────
        if not state.has_active_ask:
            if not self.inv_mgr.should_stop_quoting(state.market_id, "sell"):
                if quotes.ask_size_usdc >= 0.50:
                    ask_tokens = quotes.ask_size_usdc / quotes.ask_price
                    tasks.append(("ask", quotes.ask_price, ask_tokens))

        if not tasks:
            logger.debug("[%s] Keine neuen Orders nötig", state.market_id[:16])
            return

        # Orders parallel platzieren
        for mm_side, price, tokens in tasks:
            order_side = OrderSide.BUY if mm_side == "bid" else OrderSide.SELL
            try:
                order = await self.order_mgr.place_order(
                    token_id=state.token_id,
                    side=order_side,
                    price=round(price, 4),
                    size=round(tokens, 4),
                    order_type=OrderType.GTC,
                )

                if order.status not in (OrderStatus.FAILED,):
                    mm_order = ActiveMMOrder(
                        order=order,
                        mm_side=MMOrderSide.BID if mm_side == "bid" else MMOrderSide.ASK,
                        market_id=state.market_id,
                        fair_value_at_placement=quotes.fair_value,
                        half_spread_at_placement=quotes.half_spread,
                    )

                    if mm_side == "bid":
                        state.bid_order = mm_order
                    else:
                        state.ask_order = mm_order

                    logger.info(
                        "[%s] %s-Order platziert: %.4f @ %.4f USDC "
                        "(%.4f Token)",
                        state.market_id[:16],
                        mm_side.upper(),
                        price,
                        price * tokens,
                        tokens,
                    )

                    # Inventar über geplante Order informieren
                    self.inv_mgr.record_order_placed(
                        market_id=state.market_id,
                        token_id=state.token_id,
                        side="buy" if mm_side == "bid" else "sell",
                    )

                else:
                    logger.warning(
                        "[%s] %s-Order fehlgeschlagen: %s",
                        state.market_id[:16],
                        mm_side.upper(),
                        order.error,
                    )

            except Exception as e:
                logger.error(
                    "[%s] Fehler beim Platzieren von %s-Order: %s",
                    state.market_id[:16], mm_side.upper(), e,
                )

    # ──────────────────────────────────────────────────────────────────────────
    # Fill-Verarbeitung
    # ──────────────────────────────────────────────────────────────────────────

    def process_fill(
        self,
        market_id: str,
        order_id: str,
        filled_size: float,
        fill_price: float,
    ):
        """
        Verarbeite einen Order-Fill und aktualisiere Inventar + Statistiken.

        Sollte aufgerufen werden wenn eine Order (teil-)gefüllt wird.

        Args:
            market_id:    Markt-ID
            order_id:     Order-ID
            filled_size:  Gefüllte Token-Anzahl
            fill_price:   Ausführungspreis
        """
        if market_id not in self._markets:
            return

        state = self._markets[market_id]

        # Bestimme welche Order gefüllt wurde
        mm_order = None
        mm_side = None

        if state.bid_order and state.bid_order.order.order_id == order_id:
            mm_order = state.bid_order
            mm_side = "buy"
            state.total_bid_fills += 1
            self._total_bid_fills += 1
        elif state.ask_order and state.ask_order.order.order_id == order_id:
            mm_order = state.ask_order
            mm_side = "sell"
            state.total_ask_fills += 1
            self._total_ask_fills += 1

        if mm_order is None:
            logger.debug("Unbekannte Order gefüllt: %s", order_id[:16])
            return

        # Gegenseiten-Preis für Spread-Schätzung
        counterpart_price = None
        if mm_side == "buy" and state.ask_order:
            counterpart_price = state.ask_order.order.price
        elif mm_side == "sell" and state.bid_order:
            counterpart_price = state.bid_order.order.price

        # Inventar aktualisieren
        self.inv_mgr.record_fill(
            market_id=state.market_id,
            token_id=state.token_id,
            side=mm_side,
            size_tokens=filled_size,
            fill_price=fill_price,
            counterpart_price=counterpart_price,
        )

        # Geschätztes Spread-Einkommen berechnen
        if counterpart_price:
            if mm_side == "buy":
                est_spread = max(0, counterpart_price - fill_price) * filled_size
            else:
                est_spread = max(0, fill_price - counterpart_price) * filled_size
            state.total_spread_earned += est_spread
            self._total_spread_earned += est_spread

        logger.info(
            "[%s] Fill: %s %.4f @ %.4f | Geschätzter Spread: $%.4f",
            market_id[:16],
            mm_side.upper(),
            filled_size,
            fill_price,
            state.total_spread_earned,
        )

    def process_market_resolution(
        self,
        market_id: str,
        up_won: bool,
    ) -> float:
        """
        Verarbeite Marktauflösung.

        Args:
            market_id:  Markt-ID
            up_won:     True wenn BTC gestiegen ist (YES gewonnen)

        Returns:
            Realisiertes PnL
        """
        if market_id not in self._markets:
            return 0.0

        state = self._markets[market_id]

        # Alle aktiven Orders canceln
        asyncio.create_task(self._cancel_all_orders(state))

        # PnL berechnen
        pnl = self.inv_mgr.record_market_resolution(
            market_id=market_id,
            up_won=up_won,
            fee_rate=self.config.polymarket_fee,
        )

        # Markt abmelden
        self.unregister_market(market_id)

        return pnl

    # ──────────────────────────────────────────────────────────────────────────
    # Status & Statistiken
    # ──────────────────────────────────────────────────────────────────────────

    def get_active_order_count(self) -> int:
        """Gesamtzahl aktiver Orders über alle Märkte."""
        count = 0
        for state in self._markets.values():
            count += state.active_order_count
        return count

    def get_market_state(self, market_id: str) -> Optional[MarketMMState]:
        """Hole Zustand für einen Markt."""
        return self._markets.get(market_id)

    def get_all_states(self) -> list[MarketMMState]:
        """Alle aktiven Markt-Zustände."""
        return list(self._markets.values())

    def get_statistics(self) -> dict:
        """
        Gesamtstatistiken des Market Makers.

        Returns:
            Dictionary mit allen relevanten Kennzahlen
        """
        inv_summary = self.inv_mgr.get_summary()

        active_orders = self.get_active_order_count()
        active_markets = len(self._markets)

        # Per-Markt Statistiken aggregieren
        total_bid_fills = sum(s.total_bid_fills for s in self._markets.values())
        total_ask_fills = sum(s.total_ask_fills for s in self._markets.values())
        total_spread = sum(s.total_spread_earned for s in self._markets.values())

        return {
            # Order-Statistiken
            "active_markets": active_markets,
            "active_orders": active_orders,
            "max_open_orders": self.config.max_open_orders,
            "total_bid_fills": self._total_bid_fills,
            "total_ask_fills": self._total_ask_fills,
            "roundtrips": min(self._total_bid_fills, self._total_ask_fills),
            "total_cancelled_stale": self._total_cancelled_stale,

            # PnL / Spread
            "estimated_spread_earned": self._total_spread_earned,
            "session_realized_pnl": inv_summary["session_realized_pnl"],

            # Inventar
            "total_exposure_usdc": inv_summary["total_exposure_usdc"],
            "capital_used_pct": inv_summary["capital_used_pct"],

            # Konfiguration
            "base_spread": self.config.base_spread,
            "capital": self.config.capital,
            "mode": "simulation" if self.config.simulation_mode else "live",
        }
