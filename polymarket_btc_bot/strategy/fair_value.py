"""
FairValueCalc: Berechnet die faire Wahrscheinlichkeit für BTC-Richtungsmärkte.

Methodischer Ansatz:
──────────────────────────────────────────────────────────────────────
1. Basis-Wahrscheinlichkeit: 50% (neutrale Ausgangslage, Münzwurf)
2. Momentum-Anpassung via EMA-Crossover (EMA5 vs EMA15)
   - EMA5 > EMA15 → Aufwärtstrend → Fair Value > 50%
   - EMA5 < EMA15 → Abwärtstrend → Fair Value < 50%
3. Preis-Momentum vs. Markteröffnung (Opening Price)
   - BTC +2% seit Eröffnung → höhere Up-Wahrscheinlichkeit
4. Volatilitätsdämpfung via ATR
   - Hohe Volatilität = hohe Unsicherheit → Anpassungen kleiner
5. Zeit-Faktor: Nahe Ablauf → Reversion zu Fair Value basierend
   auf aktuellem Preisstand vs. Opening
──────────────────────────────────────────────────────────────────────

Formel (vereinfacht):
    fair_value = 0.50 + momentum_adj * vol_dampener * time_factor
    momentum_adj = 0.6 * ema_adj + 0.4 * price_adj

Grenzen: [0.15, 0.85] — extreme Werte sind auf liquiden Märkten
selten und würden den Spread unattraktiv machen.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

from polymarket_btc_bot.data.binance_feed import BinanceFeed

logger = logging.getLogger(__name__)


@dataclass
class FairValueResult:
    """Ergebnis der Fair-Value-Berechnung mit Hilfsgrößen für Debugging."""

    fair_value: float           # 0.0–1.0, geschätzte P(BTC Up)
    ema5: Optional[float]       # Kurzfristiger EMA (5 Perioden)
    ema15: Optional[float]      # Langfristiger EMA (15 Perioden)
    atr: Optional[float]        # Average True Range (Volatilitätsmaß)
    momentum_adj: float         # Gesamt-Anpassung zum Basiswert
    time_factor: float          # Zeit-Faktor (1.0 = viel Zeit, 0.0 = Ablauf)
    confidence: float           # Konfidenz [0.0–1.0] — beeinflusst Spread-Breite
    current_price: float        # Aktueller BTC-Preis
    opening_price: float        # BTC-Preis bei Markteröffnung
    price_change_pct: float     # Preisänderung seit Eröffnung
    timestamp: float            # Berechnungszeitstempel

    def __str__(self) -> str:
        direction = "UP" if self.fair_value > 0.50 else "DOWN"
        ema5_str  = f"{self.ema5:.1f}"  if self.ema5  else "N/A"
        ema15_str = f"{self.ema15:.1f}" if self.ema15 else "N/A"
        atr_str   = f"{self.atr:.2f}"   if self.atr   else "N/A"
        return (
            f"FV={self.fair_value:.4f} ({direction}) | "
            f"EMA5={ema5_str} | "
            f"EMA15={ema15_str} | "
            f"ATR={atr_str} | "
            f"Adj={self.momentum_adj:+.4f} | "
            f"TimeFactor={self.time_factor:.2f} | "
            f"Conf={self.confidence:.2f}"
        )


class FairValueCalc:
    """
    Berechnet den fairen Wert (Wahrscheinlichkeit BTC Up) für Polymarket-Märkte.

    Verwendet den Binance-Preisfeed als Grundlage und kombiniert:
    - EMA-Crossover (Trend-Erkennung)
    - ATR-basierte Volatilitätsdämpfung
    - Opening-Price-Vergleich
    - Time-to-Expiry-Faktor

    Typische Nutzung:
        calc = FairValueCalc(binance_feed)
        result = calc.compute(
            opening_price=42000.0,
            time_remaining_seconds=300,
            market_duration_seconds=900,
        )
        fair_val = result.fair_value  # z.B. 0.53
    """

    # Clamp-Grenzen: extreme Werte werden auf diesen Bereich begrenzt
    MIN_FAIR_VALUE = 0.15
    MAX_FAIR_VALUE = 0.85

    def __init__(
        self,
        binance_feed: BinanceFeed,
        ema_short_period: int = 5,
        ema_long_period: int = 15,
        atr_period: int = 14,
        max_momentum_adj: float = 0.12,   # Max ±12% durch Momentum
        max_price_adj: float = 0.15,      # Max ±15% durch Preisbewegung
        momentum_weight: float = 0.60,    # Gewicht EMA-Momentum
        price_weight: float = 0.40,       # Gewicht Preis-Momentum
    ):
        """
        Args:
            binance_feed:       Binance WebSocket Preisfeed
            ema_short_period:   EMA-Periode für Kurzzeit-Trend (Standard: 5)
            ema_long_period:    EMA-Periode für Langzeit-Trend (Standard: 15)
            atr_period:         Perioden für ATR-Berechnung (Standard: 14)
            max_momentum_adj:   Maximale Anpassung durch EMA-Crossover
            max_price_adj:      Maximale Anpassung durch Preisbewegung
            momentum_weight:    Gewicht des EMA-Momentum-Signals
            price_weight:       Gewicht des Preis-Momentum-Signals
        """
        self.feed = binance_feed
        self.ema_short = ema_short_period
        self.ema_long = ema_long_period
        self.atr_period = atr_period
        self.max_momentum_adj = max_momentum_adj
        self.max_price_adj = max_price_adj
        self.momentum_weight = momentum_weight
        self.price_weight = price_weight

        # Cache: Letzte Berechnung für Performance
        self._last_result: Optional[FairValueResult] = None
        self._cache_ttl: float = 2.0  # Sekunden bis Cache ungültig

    # ──────────────────────────────────────────────────────────────────────────
    # Private Hilfsmethoden
    # ──────────────────────────────────────────────────────────────────────────

    def _get_recent_prices(self, n: int = 200) -> list[float]:
        """Extrahiere die letzten N Preise aus dem Binance-Preisbuffer."""
        buffer = list(self.feed._price_buffer)
        if not buffer:
            return []
        return [p.price for p in buffer[-n:]]

    def _compute_ema(self, prices: list[float], period: int) -> Optional[float]:
        """
        Berechne EMA (Exponentieller Gleitender Durchschnitt).

        Initialisierung: Einfacher Durchschnitt der ersten 'period' Werte.
        Folgeberechnung: EMA_t = price_t * k + EMA_(t-1) * (1 - k)
        wobei k = 2 / (period + 1)

        Args:
            prices: Preisliste (ältester Wert zuerst)
            period: EMA-Periode

        Returns:
            EMA-Wert oder None bei zu wenig Daten
        """
        if len(prices) < period:
            return None

        k = 2.0 / (period + 1)

        # Initialisierung: Einfacher Durchschnitt der ersten 'period' Preise
        ema = sum(prices[:period]) / period

        # Iterative EMA-Berechnung
        for price in prices[period:]:
            ema = price * k + ema * (1.0 - k)

        return ema

    def _compute_atr(self, prices: list[float]) -> Optional[float]:
        """
        Berechne ATR (Average True Range) als Volatilitätsmaß.

        Bei Tick-Daten (keine OHLC verfügbar):
            True Range = |Preis_t - Preis_(t-1)|

        ATR = Durchschnitt der letzten 'atr_period' True Ranges.

        Returns:
            ATR in USD oder None bei zu wenig Daten
        """
        if len(prices) < self.atr_period + 1:
            return None

        true_ranges = [
            abs(prices[i] - prices[i - 1])
            for i in range(1, len(prices))
        ]

        # Einfacher Durchschnitt der letzten 'atr_period' TR-Werte
        atr = sum(true_ranges[-self.atr_period:]) / self.atr_period
        return atr

    def _compute_ema_adjustment(
        self,
        ema5: Optional[float],
        ema15: Optional[float],
    ) -> float:
        """
        Berechne Anpassung basierend auf EMA-Crossover.

        EMA5 > EMA15 → Aufwärtstrend → positive Anpassung (Up wahrscheinlicher)
        EMA5 < EMA15 → Abwärtstrend → negative Anpassung (Down wahrscheinlicher)

        Verstärkung: 1% EMA-Spread → ~5% Wahrscheinlichkeitsanpassung

        Returns:
            float im Bereich [-max_momentum_adj, +max_momentum_adj]
        """
        if ema5 is None or ema15 is None or ema15 == 0:
            return 0.0

        # Relativer EMA-Spread (prozentual)
        ema_spread_pct = (ema5 - ema15) / ema15

        # Verstärken: 1% EMA-Spread → 5% Wahrscheinlichkeitsanpassung
        raw_adj = ema_spread_pct * 5.0

        return max(-self.max_momentum_adj, min(self.max_momentum_adj, raw_adj))

    def _compute_price_adjustment(
        self,
        current_price: float,
        opening_price: float,
    ) -> float:
        """
        Berechne Anpassung basierend auf Preisbewegung seit Markteröffnung.

        BTC +2% seit Eröffnung → höhere P(Up), da Momentum anhält.
        BTC -2% seit Eröffnung → niedrigere P(Up).

        Verstärkung: 1% Preisänderung → ~10% Wahrscheinlichkeitsanpassung

        Returns:
            float im Bereich [-max_price_adj, +max_price_adj]
        """
        if opening_price <= 0 or current_price <= 0:
            return 0.0

        price_change_pct = (current_price - opening_price) / opening_price

        # Verstärken: 1% Preisänderung → 10% Anpassung
        raw_adj = price_change_pct * 10.0

        return max(-self.max_price_adj, min(self.max_price_adj, raw_adj))

    def _apply_volatility_dampener(
        self,
        adjustment: float,
        atr: Optional[float],
        current_price: float,
    ) -> float:
        """
        Dämpfe Richtungsanpassung bei hoher Volatilität.

        Begründung: Hohe Volatilität = hohe Unsicherheit über Richtung.
        Bei ATR = 0.5% des Preises → volle Dämpfung (Faktor ≈ 0.5).

        Returns:
            Gedämpfte Anpassung
        """
        if atr is None or current_price <= 0 or atr <= 0:
            return adjustment

        # ATR als Prozentsatz des Preises
        atr_pct = atr / current_price

        # Linearer Dämpfungsfaktor: Bei hoher Vola → kleiner Faktor
        # atr_pct = 0.001 (0.1%) → dampener ≈ 0.95
        # atr_pct = 0.005 (0.5%) → dampener ≈ 0.75
        # atr_pct = 0.020 (2.0%) → dampener ≈ 0.20 (Minimum)
        dampener = max(0.20, 1.0 - atr_pct * 40.0)

        return adjustment * dampener

    def _compute_time_factor(
        self,
        time_remaining_seconds: float,
        market_duration_seconds: float,
    ) -> float:
        """
        Berechne Zeit-Faktor für Anpassungsgewichtung.

        Logik:
        - Bei viel Zeit: Momentum-Signale gelten stärker (time_factor ≈ 1.0)
        - Bei wenig Zeit: Konvergenz zu aktuellem Stand erzwungen
          (time_factor → 0.0 wenn < 10% der Zeit verbleibt)

        Beispiel (15-Minuten-Markt):
          - 15min verbleibend → time_factor = 1.0
          - 7.5min verbleibend → time_factor ≈ 1.0
          - 3min verbleibend → time_factor ≈ 0.4
          - 1min verbleibend → time_factor ≈ 0.13

        Returns:
            float im Bereich [0.0, 1.0]
        """
        if market_duration_seconds <= 0:
            return 0.5

        time_ratio = time_remaining_seconds / market_duration_seconds

        # Sigmoid-ähnliche Kurve: flacht nach oben ab, fällt gegen Ende schnell
        # Schwellenwert bei 20% verbleibender Zeit
        if time_ratio >= 0.20:
            return 1.0
        else:
            # Lineare Abnahme von 1.0 bei 20% bis 0.0 bei 0%
            return time_ratio / 0.20

    # ──────────────────────────────────────────────────────────────────────────
    # Öffentliche Methoden
    # ──────────────────────────────────────────────────────────────────────────

    def compute(
        self,
        opening_price: float,
        time_remaining_seconds: float,
        market_duration_seconds: float,
        use_cache: bool = True,
    ) -> FairValueResult:
        """
        Berechne den fairen Wert für den aktuellen Marktzustand.

        Algorithmus:
        1. Preishistorie aus Binance-Feed laden
        2. EMA5 und EMA15 berechnen
        3. ATR berechnen
        4. Momentum-Anpassung via EMA-Crossover
        5. Preis-Anpassung vs. Opening Price
        6. Beide Anpassungen gewichten und kombinieren
        7. Volatilitätsdämpfung anwenden
        8. Zeit-Faktor multiplizieren
        9. Fair Value = 0.50 + gedämpfte Anpassung

        Args:
            opening_price:            BTC-Preis bei Markteröffnung
            time_remaining_seconds:   Sekunden bis Marktschluss
            market_duration_seconds:  Gesamtdauer des Marktes

        Returns:
            FairValueResult mit Fair Value und allen Zwischengrößen
        """
        # Cache nutzen wenn aktuell genug
        if use_cache and self._last_result is not None:
            age = time.time() - self._last_result.timestamp
            if age < self._cache_ttl:
                return self._last_result

        current_price = self.feed.current_price

        # Fallback: Keine Daten verfügbar → neutraler Wert
        if current_price <= 0:
            result = FairValueResult(
                fair_value=0.50,
                ema5=None,
                ema15=None,
                atr=None,
                momentum_adj=0.0,
                time_factor=0.5,
                confidence=0.0,
                current_price=0.0,
                opening_price=opening_price,
                price_change_pct=0.0,
                timestamp=time.time(),
            )
            self._last_result = result
            return result

        # Preishistorie laden (letzte 200 Datenpunkte)
        prices = self._get_recent_prices(200)

        if len(prices) < 5:
            logger.warning("Zu wenig Preisdaten (%d < 5) für Fair-Value-Berechnung", len(prices))
            result = FairValueResult(
                fair_value=0.50,
                ema5=None,
                ema15=None,
                atr=None,
                momentum_adj=0.0,
                time_factor=1.0,
                confidence=0.0,
                current_price=current_price,
                opening_price=opening_price,
                price_change_pct=0.0,
                timestamp=time.time(),
            )
            self._last_result = result
            return result

        # ── Technische Indikatoren ────────────────────────────────────────────
        ema5  = self._compute_ema(prices, self.ema_short)
        ema15 = self._compute_ema(prices, self.ema_long)
        atr   = self._compute_atr(prices)

        # ── Einzelne Anpassungen ─────────────────────────────────────────────
        ema_adj   = self._compute_ema_adjustment(ema5, ema15)
        price_adj = self._compute_price_adjustment(current_price, opening_price)

        # ── Gewichtete Kombination ────────────────────────────────────────────
        raw_adj = (
            ema_adj   * self.momentum_weight +
            price_adj * self.price_weight
        )

        # ── Volatilitätsdämpfung ─────────────────────────────────────────────
        dampened_adj = self._apply_volatility_dampener(raw_adj, atr, current_price)

        # ── Zeit-Faktor ───────────────────────────────────────────────────────
        time_factor  = self._compute_time_factor(
            time_remaining_seconds, market_duration_seconds
        )
        final_adj = dampened_adj * time_factor

        # ── Fair Value ────────────────────────────────────────────────────────
        fair_value = 0.50 + final_adj
        fair_value = max(self.MIN_FAIR_VALUE, min(self.MAX_FAIR_VALUE, fair_value))

        # ── Konfidenzschätzung ────────────────────────────────────────────────
        # Mehr Datenpunkte = höhere Konfidenz
        data_confidence = min(1.0, len(prices) / 50.0)
        # Niedrige Volatilität = höhere Konfidenz
        if atr and current_price > 0:
            vol_confidence = max(0.1, 1.0 - (atr / current_price) * 50.0)
        else:
            vol_confidence = 0.5
        confidence = data_confidence * vol_confidence

        # Preisänderung seit Eröffnung
        price_change_pct = (
            (current_price - opening_price) / opening_price
            if opening_price > 0 else 0.0
        )

        result = FairValueResult(
            fair_value=fair_value,
            ema5=ema5,
            ema15=ema15,
            atr=atr,
            momentum_adj=final_adj,
            time_factor=time_factor,
            confidence=confidence,
            current_price=current_price,
            opening_price=opening_price,
            price_change_pct=price_change_pct,
            timestamp=time.time(),
        )

        self._last_result = result

        logger.debug(
            "FairValue: %.4f | EMA5/15: %.0f/%.0f | ATR: %.1f | "
            "Adj: %+.4f | TF: %.2f | Conf: %.2f",
            fair_value,
            ema5 or 0, ema15 or 0,
            atr or 0,
            final_adj,
            time_factor,
            confidence,
        )

        return result

    def compute_spread_width(
        self,
        confidence: float,
        base_spread: float = 0.04,
        min_spread: float = 0.02,
        max_spread: float = 0.10,
    ) -> float:
        """
        Berechne optimale Spread-Breite basierend auf Konfidenz.

        Logik:
        - Hohe Konfidenz (wenig Unsicherheit) → engerer Spread → mehr Fills
        - Niedrige Konfidenz (hohe Unsicherheit) → breiterer Spread → mehr Schutz

        Args:
            confidence:   Konfidenzwert aus compute() [0.0–1.0]
            base_spread:  Basis-Spread-Breite (Standard: 4%)
            min_spread:   Minimaler Spread (Standard: 2%)
            max_spread:   Maximaler Spread (Standard: 10%)

        Returns:
            Spread-Breite in Dezimal (z.B. 0.04 = 4%)
        """
        # Niedrige Konfidenz → breiter Spread (bis zu 2x base_spread)
        uncertainty_factor = 1.0 + (1.0 - confidence) * 1.5
        spread = base_spread * uncertainty_factor

        return max(min_spread, min(max_spread, spread))
