"""
VolatilityGuard: Erkennt explosive BTC-Preisbewegungen und stoppt Quoting.

──────────────────────────────────────────────────────────────────────────────
Zwei Trigger-Bedingungen:
──────────────────────────────────────────────────────────────────────────────
1. Preis-Move-Filter:
   |BTC-Änderung in lookback_seconds| > move_threshold (Standard: 3%)
   → typisch: 3% in 15 Minuten = starker Move

2. Realized-Vola-Filter (optional):
   std(returns, vol_lookback_seconds) > vol_threshold
   → optional, kann deaktiviert werden (vol_threshold=None)

Cooldown:
   Nach Trigger: Quoting pausiert für cooldown_seconds (Standard: 5 Min),
   auch wenn sich der Markt danach beruhigt hat.

Spread-Empfehlung:
   Bei erhöhter Vola: Spread linear von base_spread auf vola_min_spread skaliert.
   - Normal (realized_vol < 0.15%):  base_spread (z.B. 4%)
   - Erhöhte Vola (0.15% – 0.30%):  linear bis vola_min_spread (z.B. 6%)
   - Hohe Vola (> 0.30%):           vola_min_spread oder mehr

──────────────────────────────────────────────────────────────────────────────
Verwendung:
──────────────────────────────────────────────────────────────────────────────
    guard = VolatilityGuard(
        binance_feed=feed,
        move_threshold=0.03,      # 3% in 15 Min
        lookback_seconds=900,     # 15 Minuten
        cooldown_seconds=300,     # 5 Min Pause nach Trigger
        base_spread=0.04,
        vola_min_spread=0.06,     # Mind. 6% Spread bei hoher Vola
    )

    state = guard.check()
    if state.is_high_vola:
        logger.warning("Quoting pausiert! Cooldown: %.0fs", state.cooldown_remaining)
    else:
        spread = state.spread_recommendation  # z.B. 0.05 = 5% bei erhöhter Vola
──────────────────────────────────────────────────────────────────────────────
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

from polymarket_btc_bot.data.binance_feed import BinanceFeed

logger = logging.getLogger(__name__)


@dataclass
class VolatilityState:
    """Aktueller Volatilitätszustand — Ergebnis von VolatilityGuard.check()."""

    is_high_vola: bool             # True → Quoting soll gestoppt werden
    price_change_pct: float        # BTC-Änderung über lookback (z.B. 0.035 = +3.5%)
    realized_vol: Optional[float]  # Realisierte Vola (std der Returns), oder None
    cooldown_remaining: float      # Sekunden bis Quoting wieder erlaubt
    spread_recommendation: float   # Empfohlener Spread (z.B. 0.06 = 6%)


class VolatilityGuard:
    """
    Überwacht BTC-Volatilität und entscheidet ob Quoting sicher ist.

    Verantwortlichkeiten:
    - Preis-Move-Filter: stoppt Quoting bei explosiven BTC-Moves
    - Realized-Vola-Filter: optionaler zweiter Trigger
    - Cooldown-Management: verhindert sofortigen Neustart nach Hochvola
    - Spread-Empfehlung: skaliert Spread mit aktueller Vola
    """

    # Schwellenwerte für Spread-Skalierung
    _NORMAL_VOL_THRESHOLD: float = 0.0015   # 0.15% = normaler Bereich
    _HIGH_VOL_THRESHOLD: float = 0.003      # 0.30% = volle Spread-Erhöhung

    def __init__(
        self,
        binance_feed: BinanceFeed,
        move_threshold: float = 0.03,
        lookback_seconds: int = 900,
        vol_threshold: Optional[float] = None,
        vol_lookback_seconds: int = 300,
        cooldown_seconds: float = 300.0,
        base_spread: float = 0.04,
        vola_min_spread: float = 0.06,
    ):
        """
        Args:
            binance_feed:         Binance WebSocket Preisfeed
            move_threshold:       Preis-Move-Schwelle (Standard: 0.03 = 3%)
            lookback_seconds:     Zeitfenster für Preis-Move-Check (Standard: 900 = 15min)
            vol_threshold:        Realized-Vola-Schwelle, oder None zum Deaktivieren
            vol_lookback_seconds: Zeitfenster für Vola-Berechnung (Standard: 300 = 5min)
            cooldown_seconds:     Pause nach Trigger in Sekunden (Standard: 300 = 5min)
            base_spread:          Normaler Spread für Empfehlung (Standard: 0.04 = 4%)
            vola_min_spread:      Mindest-Spread bei hoher Vola (Standard: 0.06 = 6%)
        """
        self.feed = binance_feed
        self.move_threshold = move_threshold
        self.lookback_seconds = lookback_seconds
        self.vol_threshold = vol_threshold
        self.vol_lookback_seconds = vol_lookback_seconds
        self.cooldown_seconds = cooldown_seconds
        self.base_spread = base_spread
        self.vola_min_spread = vola_min_spread

        self._triggered_at: Optional[float] = None
        self._trigger_count: int = 0
        self._last_state: Optional[VolatilityState] = None

    # ──────────────────────────────────────────────────────────────────────────
    # Kern-Methode
    # ──────────────────────────────────────────────────────────────────────────

    def check(self) -> VolatilityState:
        """
        Prüfe ob aktuell ein Hochvola-Event vorliegt.

        Ablauf:
        1. Preis-Move-Check: |Δ%| > move_threshold?
        2. Realized-Vola-Check: std > vol_threshold? (falls aktiviert)
        3. Cooldown-Verwaltung
        4. Spread-Empfehlung berechnen

        Returns:
            VolatilityState mit is_high_vola=True wenn Quoting gestoppt werden soll.
        """
        current_price = self.feed.current_price
        price_change_pct = 0.0
        is_triggered = False

        # ── Trigger 1: Preis-Move-Filter ──────────────────────────────────────
        price_ago = self.feed.get_price_at(self.lookback_seconds)
        if price_ago and price_ago > 0 and current_price > 0:
            price_change_pct = (current_price - price_ago) / price_ago

            if abs(price_change_pct) > self.move_threshold:
                is_triggered = True
                if self._triggered_at is None:
                    self._triggered_at = time.time()
                    self._trigger_count += 1
                    logger.warning(
                        "VOLA-FILTER AKTIV: BTC %+.1f%% in %.0f Min "
                        "— Quoting gestoppt (Trigger #%d)",
                        price_change_pct * 100,
                        self.lookback_seconds / 60,
                        self._trigger_count,
                    )

        # ── Trigger 2: Realized-Vola-Filter (optional) ────────────────────────
        realized_vol = self.feed.get_volatility(seconds=self.vol_lookback_seconds)
        if (
            not is_triggered
            and self.vol_threshold is not None
            and realized_vol is not None
            and realized_vol > self.vol_threshold
        ):
            is_triggered = True
            if self._triggered_at is None:
                self._triggered_at = time.time()
                self._trigger_count += 1
                logger.warning(
                    "VOLA-FILTER AKTIV: Realized-Vola %.4f%% > %.4f%% "
                    "— Quoting gestoppt (Trigger #%d)",
                    realized_vol * 100,
                    self.vol_threshold * 100,
                    self._trigger_count,
                )

        # ── Cooldown-Logik ─────────────────────────────────────────────────────
        cooldown_remaining = 0.0
        if self._triggered_at is not None:
            elapsed = time.time() - self._triggered_at
            cooldown_remaining = max(0.0, self.cooldown_seconds - elapsed)

            if is_triggered:
                # Trigger noch aktiv → Cooldown-Timer neu starten
                self._triggered_at = time.time()
                cooldown_remaining = self.cooldown_seconds
            elif cooldown_remaining <= 0:
                # Beruhigt und Cooldown abgelaufen → zurücksetzen
                logger.info(
                    "VOLA-FILTER: Hochvola-Event vorbei — Quoting wird fortgesetzt"
                )
                self._triggered_at = None

        final_high_vola = is_triggered or cooldown_remaining > 0

        # ── Spread-Empfehlung ──────────────────────────────────────────────────
        spread_rec = self._compute_spread_recommendation(realized_vol)

        state = VolatilityState(
            is_high_vola=final_high_vola,
            price_change_pct=price_change_pct,
            realized_vol=realized_vol,
            cooldown_remaining=cooldown_remaining,
            spread_recommendation=spread_rec,
        )
        self._last_state = state
        return state

    # ──────────────────────────────────────────────────────────────────────────
    # Spread-Empfehlung
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_spread_recommendation(
        self, realized_vol: Optional[float]
    ) -> float:
        """
        Berechne empfohlenen Spread basierend auf aktueller Volatilität.

        Skalierung:
          realized_vol ≤ _NORMAL_VOL_THRESHOLD (0.15%): → base_spread
          linear bis _HIGH_VOL_THRESHOLD (0.30%):       → vola_min_spread
          darüber:                                       → vola_min_spread (geclampt)

        Beispiele (base_spread=4%, vola_min_spread=6%):
          0.10% Vola → 4.0% Spread
          0.15% Vola → 4.0% Spread
          0.22% Vola → 5.0% Spread
          0.30% Vola → 6.0% Spread
          0.50% Vola → 6.0% Spread (Max)
        """
        if realized_vol is None or realized_vol <= self._NORMAL_VOL_THRESHOLD:
            return self.base_spread

        ratio = min(
            1.0,
            (realized_vol - self._NORMAL_VOL_THRESHOLD)
            / (self._HIGH_VOL_THRESHOLD - self._NORMAL_VOL_THRESHOLD),
        )
        return self.base_spread + ratio * (self.vola_min_spread - self.base_spread)

    # ──────────────────────────────────────────────────────────────────────────
    # Status & Statistiken
    # ──────────────────────────────────────────────────────────────────────────

    @property
    def trigger_count(self) -> int:
        """Gesamtzahl der ausgelösten Triggers seit Bot-Start."""
        return self._trigger_count

    @property
    def is_active(self) -> bool:
        """True wenn aktuell ein Hochvola-Event aktiv ist."""
        return self._last_state.is_high_vola if self._last_state else False

    def get_status(self) -> dict:
        """Status-Dictionary für Dashboard und Logging."""
        state = self._last_state
        if state is None:
            return {
                "is_high_vola": False,
                "price_change_pct": 0.0,
                "realized_vol": None,
                "cooldown_remaining": 0.0,
                "spread_recommendation": self.base_spread,
                "trigger_count": 0,
            }
        return {
            "is_high_vola": state.is_high_vola,
            "price_change_pct": state.price_change_pct,
            "realized_vol": state.realized_vol,
            "cooldown_remaining": state.cooldown_remaining,
            "spread_recommendation": state.spread_recommendation,
            "trigger_count": self._trigger_count,
        }
