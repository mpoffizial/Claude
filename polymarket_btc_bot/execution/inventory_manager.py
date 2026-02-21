"""
InventoryManager: Verwaltet das Token-Inventar und berechnet Spread-Skewing.

──────────────────────────────────────────────────────────────────────────────
Market Making Inventar-Konzept:
──────────────────────────────────────────────────────────────────────────────
Als Market Maker platzieren wir gleichzeitig BID (Kauf) und ASK (Verkauf).
Wenn nur eine Seite gefüllt wird, entsteht eine einseitige Position:

  Fall A: BID gefüllt (wir haben YES-Token gekauft):
    → Wir sind LONG (profitieren wenn BTC steigt)
    → Risiko: BTC fällt → Token werden wertlos
    → Skewing: Bid senken (weniger kaufen), Ask halten

  Fall B: ASK gefüllt (wir haben YES-Token verkauft/geshorted):
    → Wir sind SHORT (profitieren wenn BTC fällt)
    → Risiko: BTC steigt → wir müssen $1 pro Token zahlen
    → Skewing: Ask senken (mehr verkaufen um auszugleichen), Bid erhöhen

──────────────────────────────────────────────────────────────────────────────
Inventory Skewing Formel:
──────────────────────────────────────────────────────────────────────────────
    imbalance_ratio = (yes_tokens - no_tokens) / total_tokens
    skew_amount = imbalance_ratio * max_skew

    adjusted_bid = fair_value - half_spread - skew_amount   (Long-Heavy: senken)
    adjusted_ask = fair_value + half_spread + skew_amount   (Long-Heavy: erhöhen)

Ergebnis bei Long-Heavy (imbalance > 0):
    - Bid liegt tiefer → weniger Käufe → Position baut sich langsamer auf
    - Ask liegt höher → Verkäufe werden attraktiver → Position wird abgebaut

Kelly Criterion für Positionsgrößen:
    Kelly% = (p - q) / (1 - q)
    wobei p = Fair Value, q = Quote-Preis

    Fractional Kelly (25%) für Risikoreduktion empfohlen.
──────────────────────────────────────────────────────────────────────────────
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MarketInventory:
    """Inventar-Zustand für einen einzelnen Markt."""

    market_id: str
    token_id: str             # YES-Token Contract ID

    # ── Token-Bestände ────────────────────────────────────────────────────────
    yes_tokens: float = 0.0   # Gekaufte YES-Token (Long-Position)
    no_tokens: float = 0.0    # Verkaufte YES-Token ohne Besitz (Short-Position)

    # ── Kosten / Einnahmen ────────────────────────────────────────────────────
    yes_cost: float = 0.0     # Bezahltes USDC für YES-Token (Kauf)
    no_revenue: float = 0.0   # Erhaltenes USDC für verkaufte YES-Token (Leerverkauf)

    # ── Order-Statistiken ─────────────────────────────────────────────────────
    total_buy_orders: int = 0
    total_sell_orders: int = 0
    total_filled_buy: int = 0
    total_filled_sell: int = 0
    spread_income_est: float = 0.0  # Geschätztes Spread-Einkommen

    # ── PnL ───────────────────────────────────────────────────────────────────
    realized_pnl: float = 0.0

    # ── Zeitstempel ───────────────────────────────────────────────────────────
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)

    # ──────────────────────────────────────────────────────────────────────────
    # Berechnete Eigenschaften
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def net_position(self) -> float:
        """
        Netto-Position in Token:
        Positiv = Long (mehr YES als NO), Negativ = Short.
        """
        return self.yes_tokens - self.no_tokens

    @property
    def total_tokens(self) -> float:
        """Gesamtzahl aller Tokens (YES + NO)."""
        return self.yes_tokens + self.no_tokens

    @property
    def net_cost(self) -> float:
        """Netto-Kosten der aktuellen Position (positiv = bezahlt)."""
        return self.yes_cost - self.no_revenue

    @property
    def imbalance_ratio(self) -> float:
        """
        Imbalance-Ratio:
          > 0: Long-Heavy (mehr YES als NO)
          < 0: Short-Heavy (mehr NO als YES)
            0: Ausgeglichen

        Bereich: [-1.0, +1.0]
        """
        total = self.total_tokens
        if total < 0.001:
            return 0.0
        return (self.yes_tokens - self.no_tokens) / total

    @property
    def is_balanced(self) -> bool:
        """True wenn die Position annähernd ausgeglichen ist (±10%)."""
        return abs(self.imbalance_ratio) < 0.10

    @property
    def long_exposure_usdc(self) -> float:
        """Aktuelles Risiko der Long-Position in USDC."""
        return self.yes_cost  # Was wir bezahlt haben

    @property
    def short_exposure_usdc(self) -> float:
        """
        Maximales Risiko der Short-Position in USDC.
        (Wenn YES gewinnt, müssen wir $1 × no_tokens zahlen)
        """
        return self.no_tokens * 1.0  # Was wir im Verlustfall zahlen müssen

    def __str__(self) -> str:
        return (
            f"[{self.market_id[:12]}] "
            f"YES={self.yes_tokens:.2f} NO={self.no_tokens:.2f} "
            f"Net={self.net_position:+.2f} "
            f"Imbalance={self.imbalance_ratio:+.2%} "
            f"Cost=${self.net_cost:.2f}"
        )


@dataclass
class QuoteAdjustment:
    """Angepasste Market-Making-Quotes nach Inventory Skewing."""

    # ── Preise ────────────────────────────────────────────────────────────────
    bid_price: float          # Angepasster Bid-Preis
    ask_price: float          # Angepasster Ask-Preis

    # ── Skewing-Komponenten ───────────────────────────────────────────────────
    bid_skew: float           # Anpassung des Bids (positiv = höher)
    ask_skew: float           # Anpassung des Asks (positiv = höher)
    half_spread: float        # Halber Base-Spread (vor Skewing)

    # ── Positionsgrößen ───────────────────────────────────────────────────────
    bid_size_usdc: float      # Ordergröße Bid in USDC
    ask_size_usdc: float      # Ordergröße Ask in USDC

    # ── Metadaten ─────────────────────────────────────────────────────────────
    imbalance_ratio: float    # Aktuelle Imbalance
    fair_value: float         # Zugrundeliegender Fair Value
    kelly_size_usdc: float    # Kelly-empfohlene Größe

    @property
    def spread(self) -> float:
        """Tatsächlicher Spread (Ask - Bid)."""
        return self.ask_price - self.bid_price

    @property
    def mid_price(self) -> float:
        """Mittelpunkt zwischen Bid und Ask."""
        return (self.bid_price + self.ask_price) / 2.0

    def __str__(self) -> str:
        return (
            f"Bid={self.bid_price:.4f}({self.bid_skew:+.4f}) "
            f"Ask={self.ask_price:.4f}({self.ask_skew:+.4f}) "
            f"Spread={self.spread:.4f} "
            f"Imbalance={self.imbalance_ratio:+.2%} "
            f"BidSz=${self.bid_size_usdc:.2f} AskSz=${self.ask_size_usdc:.2f}"
        )


class InventoryManager:
    """
    Verwaltet das Inventar aller offenen Märkte und berechnet
    skew-angepasste Market-Making-Quotes.

    Kernfunktionen:
    1. Inventar-Tracking: Verfolgt YES/NO-Token-Bestände je Markt
    2. Inventory Skewing: Passt Spread asymmetrisch bei Imbalance an
    3. Kelly-Positionierung: Berechnet optimale Ordergrößen
    4. Risiko-Überwachung: Max-Exposure, Imbalance-Limits

    Verwendung:
        inv_mgr = InventoryManager(capital=100.0)
        quotes = inv_mgr.compute_quotes(
            market_id="btc-up-15min-12345",
            token_id="0xABC...",
            fair_value=0.52,
            half_spread=0.02,
            base_order_size=5.0,
        )
    """

    def __init__(
        self,
        capital: float = 100.0,
        max_position_pct: float = 0.10,
        skew_factor: float = 0.50,
        max_inventory_ratio: float = 0.60,
        kelly_fraction: float = 0.25,
    ):
        """
        Args:
            capital:              Gesamtkapital in USDC
            max_position_pct:     Max. Position pro Markt (10% von Kapital = $10)
            skew_factor:          Stärke des Inventory Skewings [0.0–1.0]
            max_inventory_ratio:  Ab diesem Ratio: keine neuen Orders auf der
                                  übergewichteten Seite
            kelly_fraction:       Bruchteil des vollen Kelly (0.25 = 25% Kelly)
        """
        self.capital = capital
        self.max_position_pct = max_position_pct
        self.skew_factor = skew_factor
        self.max_inventory_ratio = max_inventory_ratio
        self.kelly_fraction = kelly_fraction

        self._inventories: dict[str, MarketInventory] = {}
        self._session_realized_pnl: float = 0.0
        self._session_start: float = time.time()

    # ──────────────────────────────────────────────────────────────────────────
    # Eigenschaften
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def max_position_usdc(self) -> float:
        """Maximale Position pro Markt in USDC."""
        return self.capital * self.max_position_pct

    @property
    def total_long_exposure(self) -> float:
        """Gesamte Long-Exposure aller Märkte in USDC."""
        return sum(inv.long_exposure_usdc for inv in self._inventories.values())

    @property
    def total_short_exposure(self) -> float:
        """Gesamte Short-Exposure aller Märkte in USDC."""
        return sum(inv.short_exposure_usdc for inv in self._inventories.values())

    @property
    def total_exposure(self) -> float:
        """Gesamte Exposure (Long + Short) in USDC."""
        return self.total_long_exposure + self.total_short_exposure

    @property
    def session_realized_pnl(self) -> float:
        """Realisiertes PnL der aktuellen Session."""
        return self._session_realized_pnl

    # ──────────────────────────────────────────────────────────────────────────
    # Inventar-Verwaltung
    # ──────────────────────────────────────────────────────────────────────────

    def get_or_create_inventory(
        self, market_id: str, token_id: str
    ) -> MarketInventory:
        """Hole oder erstelle Inventar-Eintrag für einen Markt."""
        if market_id not in self._inventories:
            self._inventories[market_id] = MarketInventory(
                market_id=market_id,
                token_id=token_id,
            )
            logger.debug("Neues Inventar erstellt für Markt: %s", market_id[:20])
        return self._inventories[market_id]

    def record_order_placed(self, market_id: str, token_id: str, side: str):
        """Registriere eine platzierte Order."""
        inv = self.get_or_create_inventory(market_id, token_id)
        if side == "buy":
            inv.total_buy_orders += 1
        else:
            inv.total_sell_orders += 1
        inv.last_updated = time.time()

    def record_fill(
        self,
        market_id: str,
        token_id: str,
        side: str,       # "buy" = BID gefüllt, "sell" = ASK gefüllt
        size_tokens: float,
        fill_price: float,
        counterpart_price: Optional[float] = None,
    ):
        """
        Registriere eine ausgeführte Order im Inventar.

        Args:
            market_id:          Markt-ID
            token_id:           YES-Token ID
            side:               "buy" (BID-Fill) oder "sell" (ASK-Fill)
            size_tokens:        Anzahl Token
            fill_price:         Ausführungspreis
            counterpart_price:  Preis der Gegenseite (für Spread-Schätzung)
        """
        inv = self.get_or_create_inventory(market_id, token_id)

        if side == "buy":
            # BID-Fill: Wir haben YES-Token gekauft → Long-Position aufgebaut
            inv.yes_tokens += size_tokens
            inv.yes_cost += size_tokens * fill_price
            inv.total_filled_buy += 1

            # Geschätztes Spread-Einkommen (wenn ASK bereits gefüllt war)
            if counterpart_price and counterpart_price > fill_price:
                inv.spread_income_est += (counterpart_price - fill_price) * size_tokens

        else:  # "sell"
            # ASK-Fill: Wir haben YES-Token leer verkauft → Short-Position aufgebaut
            inv.no_tokens += size_tokens
            inv.no_revenue += size_tokens * fill_price
            inv.total_filled_sell += 1

            # Geschätztes Spread-Einkommen (wenn BID bereits gefüllt war)
            if counterpart_price and fill_price > counterpart_price:
                inv.spread_income_est += (fill_price - counterpart_price) * size_tokens

        inv.last_updated = time.time()

        logger.info(
            "Fill registriert [%s]: %s %.3f @ %.4f | "
            "YES=%.3f NO=%.3f Imbalance=%+.2%",
            market_id[:16],
            side.upper(),
            size_tokens,
            fill_price,
            inv.yes_tokens,
            inv.no_tokens,
            inv.imbalance_ratio,
        )

    def record_market_resolution(
        self,
        market_id: str,
        up_won: bool,
        fee_rate: float = 0.02,
    ) -> float:
        """
        Verarbeite Marktauflösung und berechne realisiertes PnL.

        PnL-Berechnung:
          Wenn UP gewonnen:
            YES-Token: Payout = tokens × $1 - Gebühren - Kaufkosten
            NO-Token:  Verlust = tokens × $1 - erhaltene Prämie (short-Einnahmen)
          Wenn DOWN gewonnen:
            YES-Token: Verlust = -Kaufkosten (wertlos)
            NO-Token:  Gewinn = erhaltene Prämie (müssen nichts zahlen)

        Args:
            market_id:  Markt-ID
            up_won:     True wenn BTC gestiegen ist
            fee_rate:   Polymarket Gewinner-Gebühr (Standard: 2%)

        Returns:
            Realisiertes PnL (nach Gebühren)
        """
        if market_id not in self._inventories:
            logger.debug("Kein Inventar für Markt %s gefunden", market_id)
            return 0.0

        inv = self._inventories[market_id]
        pnl = 0.0

        if up_won:
            # ── YES-Tokens GEWINNEN ────────────────────────────────────────
            # Payout: $1 pro Token, aber Gebühr auf Gewinne
            yes_payout = inv.yes_tokens * 1.0
            yes_fee = yes_payout * fee_rate
            yes_net = yes_payout - yes_fee - inv.yes_cost

            # ── NO-Tokens (Short) VERLIEREN ───────────────────────────────
            # Wir müssen $1 pro geshorteten Token zahlen
            no_obligation = inv.no_tokens * 1.0
            no_net = inv.no_revenue - no_obligation  # Einnahmen - Verpflichtung

            pnl = yes_net + no_net

        else:  # down_won
            # ── YES-Tokens VERLIEREN ──────────────────────────────────────
            # YES-Tokens werden wertlos → Kaufkosten verloren
            yes_net = -inv.yes_cost

            # ── NO-Tokens (Short) GEWINNEN ────────────────────────────────
            # Wir brauchen nichts zu zahlen → Einnahmen behalten
            no_net = inv.no_revenue

            pnl = yes_net + no_net

        inv.realized_pnl = pnl
        self._session_realized_pnl += pnl

        logger.info(
            "Markt aufgelöst [%s]: %s | "
            "YES=%.3f (Kosten=$%.2f) | NO=%.3f (Einnahmen=$%.2f) | "
            "PnL=$%.4f",
            market_id[:20],
            "UP GEWONNEN" if up_won else "DOWN GEWONNEN",
            inv.yes_tokens, inv.yes_cost,
            inv.no_tokens, inv.no_revenue,
            pnl,
        )

        # Inventar bereinigen
        self._inventories.pop(market_id, None)

        return pnl

    # ──────────────────────────────────────────────────────────────────────────
    # Quote-Berechnung mit Skewing
    # ──────────────────────────────────────────────────────────────────────────

    def compute_quotes(
        self,
        market_id: str,
        token_id: str,
        fair_value: float,
        half_spread: float,
        base_order_size_usdc: float,
    ) -> QuoteAdjustment:
        """
        Berechne angepasste Market-Making-Quotes mit Inventory Skewing.

        Skewing-Logik:
        ─────────────────────────────────────────────────────────────
        imbalance_ratio > 0 (Long-Heavy: zu viele YES-Token):
          → Bid SENKEN:  Fair Value - half_spread - |skew| (weniger kaufen)
          → Ask ERHÖHEN: Fair Value + half_spread + |skew| (Käufer zahlen mehr)

        imbalance_ratio < 0 (Short-Heavy: zu viele leerverkaufte Token):
          → Bid ERHÖHEN: Fair Value - half_spread + |skew| (mehr kaufen)
          → Ask SENKEN:  Fair Value + half_spread - |skew| (attraktiverer Verkauf)
        ─────────────────────────────────────────────────────────────

        Args:
            market_id:           Markt-ID
            token_id:            YES-Token ID
            fair_value:          Geschätzter fairer Wert [0.0–1.0]
            half_spread:         Halber Spread (z.B. 0.02 bei 4% Spread)
            base_order_size_usdc: Basis-Ordergröße in USDC

        Returns:
            QuoteAdjustment mit allen angepassten Werten
        """
        inv = self.get_or_create_inventory(market_id, token_id)
        imbalance = inv.imbalance_ratio

        # ── Basis-Quotes (ohne Skewing) ───────────────────────────────────────
        base_bid = fair_value - half_spread
        base_ask = fair_value + half_spread

        # ── Skewing berechnen ─────────────────────────────────────────────────
        # Maximale Skew: proportional zum halben Spread × Skew-Faktor
        max_skew = half_spread * self.skew_factor

        # Skew-Betrag proportional zur Imbalance
        # Positiver Imbalance (Long) → negativer Bid-Skew, positiver Ask-Skew
        skew_amount = imbalance * max_skew

        bid_skew = -skew_amount    # Long-Heavy: Bid wird gesenkt
        ask_skew = +skew_amount    # Long-Heavy: Ask wird erhöht

        adjusted_bid = base_bid + bid_skew
        adjusted_ask = base_ask + ask_skew

        # ── Preise auf gültige Grenzen begrenzen ──────────────────────────────
        adjusted_bid = max(0.02, min(0.97, adjusted_bid))
        adjusted_ask = max(0.03, min(0.98, adjusted_ask))

        # Sicherstellen: Bid < Ask (Mindest-Spread von 1 Cent)
        if adjusted_bid >= adjusted_ask - 0.005:
            mid = fair_value
            adjusted_bid = mid - max(half_spread, 0.005)
            adjusted_ask = mid + max(half_spread, 0.005)

        # ── Positionsgrößen anpassen ──────────────────────────────────────────
        bid_size_usdc, ask_size_usdc = self._compute_order_sizes(
            inv=inv,
            imbalance=imbalance,
            base_order_size=base_order_size_usdc,
        )

        # ── Kelly-Positionsgröße für Referenz ─────────────────────────────────
        kelly_size = self.kelly_position_size(
            fair_value=fair_value,
            quote_price=adjusted_bid,
        )

        return QuoteAdjustment(
            bid_price=round(adjusted_bid, 4),
            ask_price=round(adjusted_ask, 4),
            bid_skew=round(bid_skew, 4),
            ask_skew=round(ask_skew, 4),
            half_spread=half_spread,
            bid_size_usdc=round(bid_size_usdc, 2),
            ask_size_usdc=round(ask_size_usdc, 2),
            imbalance_ratio=imbalance,
            fair_value=fair_value,
            kelly_size_usdc=kelly_size,
        )

    def _compute_order_sizes(
        self,
        inv: MarketInventory,
        imbalance: float,
        base_order_size: float,
    ) -> tuple[float, float]:
        """
        Berechne angepasste Ordergrößen basierend auf Inventar-Imbalance.

        Bei starker Imbalance:
        - Long-Heavy: Bid-Größe reduzieren (weniger kaufen), Ask-Größe erhöhen
        - Short-Heavy: Ask-Größe reduzieren (weniger verkaufen), Bid-Größe erhöhen

        Returns:
            (bid_size_usdc, ask_size_usdc)
        """
        abs_imbalance = abs(imbalance)

        if imbalance > 0.20:
            # Long-Heavy: Bid-Größe reduzieren, Ask-Größe erhöhen
            bid_factor = max(0.10, 1.0 - abs_imbalance * 0.80)
            ask_factor = min(1.50, 1.0 + abs_imbalance * 0.50)
        elif imbalance < -0.20:
            # Short-Heavy: Ask-Größe reduzieren, Bid-Größe erhöhen
            bid_factor = min(1.50, 1.0 + abs_imbalance * 0.50)
            ask_factor = max(0.10, 1.0 - abs_imbalance * 0.80)
        else:
            # Ausgeglichen → normale Größen
            bid_factor = 1.0
            ask_factor = 1.0

        bid_size = base_order_size * bid_factor
        ask_size = base_order_size * ask_factor

        # Max-Position begrenzen (wie viel verbleibt bis zum Limit)
        remaining_long_capacity = max(
            0.0, self.max_position_usdc - inv.long_exposure_usdc
        )
        remaining_short_capacity = max(
            0.0, self.max_position_usdc - inv.short_exposure_usdc
        )

        bid_size = min(bid_size, remaining_long_capacity)
        ask_size = min(ask_size, remaining_short_capacity * 1.5)  # Short etwas mehr erlaubt

        # Mindestgröße sicherstellen (0.50 USDC)
        bid_size = max(0.50, bid_size)
        ask_size = max(0.50, ask_size)

        # Maximale Single-Order-Größe begrenzen
        max_single = self.max_position_usdc
        bid_size = min(bid_size, max_single)
        ask_size = min(ask_size, max_single)

        return bid_size, ask_size

    # ──────────────────────────────────────────────────────────────────────────
    # Kelly Criterion
    # ──────────────────────────────────────────────────────────────────────────

    def kelly_position_size(
        self,
        fair_value: float,
        quote_price: float,
    ) -> float:
        """
        Berechne optimale Positionsgröße nach Kelly Criterion.

        Formel für binäre Märkte:
            Kelly% = (p - q) / (1 - q)

        wobei:
            p = geschätzte Gewinnwahrscheinlichkeit (Fair Value)
            q = Kaufpreis des Tokens (Quote-Preis)

        Beispiel:
            Fair Value = 0.55, Bid-Preis = 0.50
            Kelly% = (0.55 - 0.50) / (1 - 0.50) = 0.10 = 10%
            Fractional Kelly (25%): 2.5% des Kapitals

        Args:
            fair_value:   Geschätzte Gewinnwahrscheinlichkeit [0.0–1.0]
            quote_price:  Kaufpreis des Tokens [0.0–1.0]

        Returns:
            Empfohlene Positionsgröße in USDC (0.0 bei negativer Edge)
        """
        # Ungültige Eingaben abfangen
        if quote_price >= 1.0 or quote_price <= 0.0:
            return 0.0
        if fair_value <= 0.0 or fair_value >= 1.0:
            return 0.0

        # Kelly Formel
        kelly_pct = (fair_value - quote_price) / (1.0 - quote_price)

        # Negative Edge → keine Position
        if kelly_pct <= 0.0:
            return 0.0

        # Fractional Kelly anwenden
        fractional_kelly = kelly_pct * self.kelly_fraction

        # Positionsgröße in USDC
        position_usdc = self.capital * fractional_kelly

        # Begrenzen auf Maximum
        position_usdc = min(position_usdc, self.max_position_usdc)

        return round(position_usdc, 2)

    # ──────────────────────────────────────────────────────────────────────────
    # Übersicht & Diagnostik
    # ──────────────────────────────────────────────────────────────────────────

    def should_stop_quoting(self, market_id: str, side: str) -> bool:
        """
        Prüfe ob auf einer Seite keine weiteren Orders platziert werden sollen.

        Stopp-Bedingungen:
        - Long-Heavy (imbalance > max_inventory_ratio) → kein weiteres Bieten
        - Short-Heavy (imbalance < -max_inventory_ratio) → kein weiteres Anbieten

        Args:
            market_id:  Markt-ID
            side:       "buy" oder "sell"

        Returns:
            True wenn keine Orders auf dieser Seite platziert werden sollen
        """
        if market_id not in self._inventories:
            return False

        inv = self._inventories[market_id]
        imbalance = inv.imbalance_ratio

        if side == "buy" and imbalance > self.max_inventory_ratio:
            logger.warning(
                "Kein Bieten [%s]: Long-Heavy (%.1f%%)",
                market_id[:16], imbalance * 100
            )
            return True

        if side == "sell" and imbalance < -self.max_inventory_ratio:
            logger.warning(
                "Kein Anbieten [%s]: Short-Heavy (%.1f%%)",
                market_id[:16], imbalance * 100
            )
            return True

        return False

    def get_inventory(self, market_id: str) -> Optional[MarketInventory]:
        """Hole Inventar für einen Markt (None wenn nicht vorhanden)."""
        return self._inventories.get(market_id)

    def get_all_inventories(self) -> list[MarketInventory]:
        """Alle aktiven Inventare."""
        return list(self._inventories.values())

    def get_summary(self) -> dict:
        """
        Gesamtübersicht aller offenen Positionen und Session-Statistiken.

        Returns:
            Dictionary mit allen relevanten Kennzahlen
        """
        inventories = list(self._inventories.values())

        total_yes = sum(inv.yes_tokens for inv in inventories)
        total_no = sum(inv.no_tokens for inv in inventories)
        total_long_exposure = self.total_long_exposure
        total_short_exposure = self.total_short_exposure
        total_spread_est = sum(inv.spread_income_est for inv in inventories)

        return {
            "active_markets": len(inventories),
            "total_yes_tokens": total_yes,
            "total_no_tokens": total_no,
            "total_long_exposure_usdc": total_long_exposure,
            "total_short_exposure_usdc": total_short_exposure,
            "total_exposure_usdc": self.total_exposure,
            "capital_used_pct": self.total_exposure / self.capital if self.capital > 0 else 0.0,
            "session_realized_pnl": self._session_realized_pnl,
            "estimated_spread_income": total_spread_est,
            "session_duration_minutes": (time.time() - self._session_start) / 60.0,
        }
