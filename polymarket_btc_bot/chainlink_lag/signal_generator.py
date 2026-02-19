"""
Chainlink Lag Signal Generator.

Implements the multi-filter signal validation logic:
1. Lag-Bedingung:   abs(spot - chainlink) > threshold
2. Richtungs-Konsistenz: Spot-Bewegung der letzten 30s bestätigt Signal
3. Odds-Filter:     Polymarket-Preis ist NICHT bereits eingepreist (Odds < 0.85)

Only generates signals within the T-60s to T-10s window before market resolution.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from polymarket_btc_bot.chainlink_lag.config import LagThresholds, RiskParams

logger = logging.getLogger(__name__)


class SignalAction(Enum):
    BUY_UP = "BUY_UP"
    BUY_DOWN = "BUY_DOWN"
    NO_TRADE = "NO_TRADE"


class Confidence(Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class NoTradeReason(Enum):
    OUTSIDE_WINDOW = "Outside T-60s entry window"
    FEEDS_NOT_READY = "Data feeds not ready"
    LAG_BELOW_THRESHOLD = "Lag below threshold"
    DIRECTION_INCONSISTENT = "Spot direction inconsistent over 30s"
    ODDS_ALREADY_PRICED = "Odds already priced in (>0.85)"
    ODDS_TOO_LOW = "Odds too low (<0.50)"
    CHAINLINK_TOO_FRESH = "Chainlink updated within 10s"
    CHAINLINK_TOO_STALE = "Chainlink data too stale (>120s)"
    HIGH_VOLATILITY = "Spot volatility too high (>$50 in 30s)"
    CONSECUTIVE_LOSSES = "Paused after consecutive losses"
    DAILY_DRAWDOWN = "Daily drawdown limit reached"


@dataclass
class TradeSignal:
    """Output format for a trade signal."""
    action: SignalAction
    time_to_resolution: float
    spot_price: float
    chainlink_price: float
    lag_delta: float
    polymarket_odds: Optional[float]
    recommended_position_usd: float
    confidence: Confidence
    reason: str
    no_trade_reason: Optional[NoTradeReason] = None

    # Internal metadata
    opening_price: Optional[float] = None
    spot_30s_ago: Optional[float] = None
    chainlink_age_seconds: Optional[float] = None
    timestamp: float = 0.0

    def __str__(self) -> str:
        lines = [
            f"SIGNAL: {self.action.value}",
            f"Zeit bis Auflösung: {self.time_to_resolution:.0f}s",
            f"Spot-Preis: ${self.spot_price:,.2f}",
            f"Chainlink-Preis: ${self.chainlink_price:,.2f}",
            f"Lag-Delta: ${self.lag_delta:.2f}",
            f"Polymarket-Odds: {self.polymarket_odds:.2f}" if self.polymarket_odds else "Polymarket-Odds: N/A",
            f"Empfohlene Position: ${self.recommended_position_usd:.2f}",
            f"Konfidenz: {self.confidence.value}",
            f"Begründung: {self.reason}",
        ]
        if self.no_trade_reason:
            lines.append(f"Grund (kein Trade): {self.no_trade_reason.value}")
        return "\n".join(lines)


class LagSignalGenerator:
    """
    Generates trade signals based on Chainlink lag analysis.

    Execution timeline:
        T-60s: Spot vs. Open prüfen - vorläufige Richtung bestimmen
        T-45s: Chainlink-Verzögerung messen (lag_delta berechnen)
        T-30s: Signal validieren - Odds auf Polymarket prüfen
        T-20s: Order platzieren (falls Signal stark genug)
        T-0s:  Market schließt, Chainlink löst auf
    """

    def __init__(self, thresholds: LagThresholds, risk: RiskParams):
        self.thresholds = thresholds
        self.risk = risk

        # Track consecutive losses
        self._consecutive_losses = 0
        self._pause_until: float = 0.0
        self._daily_pnl: float = 0.0
        self._daily_reset_date: str = ""

    def evaluate(
        self,
        spot_price: float,
        chainlink_price: float,
        chainlink_updated_at: int,
        opening_price: float,
        time_to_resolution: float,
        polymarket_odds_up: Optional[float] = None,
        polymarket_odds_down: Optional[float] = None,
        spot_price_30s_ago: Optional[float] = None,
        spot_price_60s_ago: Optional[float] = None,
    ) -> TradeSignal:
        """
        Evaluate all conditions and return a trade signal.

        Args:
            spot_price: Current Binance BTC/USDT
            chainlink_price: Latest Chainlink on-chain BTC/USD
            chainlink_updated_at: Unix timestamp of last on-chain update
            opening_price: BTC price at market open (Chainlink)
            time_to_resolution: Seconds until market closes
            polymarket_odds_up: Current ask price for UP token
            polymarket_odds_down: Current ask price for DOWN token
            spot_price_30s_ago: Spot price 30 seconds ago
            spot_price_60s_ago: Spot price 60 seconds ago

        Returns:
            TradeSignal with action, confidence, and reasoning
        """
        now = time.time()
        lag_delta = spot_price - chainlink_price
        chainlink_age = now - chainlink_updated_at

        # Base signal template
        base = dict(
            time_to_resolution=time_to_resolution,
            spot_price=spot_price,
            chainlink_price=chainlink_price,
            lag_delta=lag_delta,
            opening_price=opening_price,
            chainlink_age_seconds=chainlink_age,
            spot_30s_ago=spot_price_30s_ago,
            timestamp=now,
        )

        # --- Pre-checks ---

        # Check consecutive loss pause
        if self._pause_until > now:
            return self._no_trade(
                NoTradeReason.CONSECUTIVE_LOSSES,
                f"Paused until {self._pause_until - now:.0f}s remaining",
                **base,
            )

        # Check daily drawdown
        self._check_daily_reset()
        if self._daily_pnl <= -(self.risk.total_capital * self.risk.daily_drawdown_limit_pct):
            return self._no_trade(
                NoTradeReason.DAILY_DRAWDOWN,
                f"Daily PnL: ${self._daily_pnl:.2f}",
                **base,
            )

        # --- TIMING CHECK: Only enter at T-60s to T-10s ---
        if time_to_resolution > self.thresholds.entry_window_start:
            return self._no_trade(
                NoTradeReason.OUTSIDE_WINDOW,
                f"T-{time_to_resolution:.0f}s, waiting for T-{self.thresholds.entry_window_start}s",
                **base,
            )
        if time_to_resolution < self.thresholds.entry_window_end:
            return self._no_trade(
                NoTradeReason.OUTSIDE_WINDOW,
                f"T-{time_to_resolution:.0f}s, too late to enter",
                **base,
            )

        # --- FILTER 1: Lag-Bedingung ---
        abs_lag = abs(lag_delta)
        if abs_lag < self.thresholds.active_threshold:
            return self._no_trade(
                NoTradeReason.LAG_BELOW_THRESHOLD,
                f"|${lag_delta:.2f}| < threshold ${self.thresholds.active_threshold:.2f}",
                **base,
            )

        # --- Chainlink freshness checks ---
        if chainlink_age > self.thresholds.max_chainlink_age_seconds:
            return self._no_trade(
                NoTradeReason.CHAINLINK_TOO_STALE,
                f"Chainlink age: {chainlink_age:.0f}s > {self.thresholds.max_chainlink_age_seconds:.0f}s",
                **base,
            )

        if chainlink_age < self.thresholds.chainlink_recent_update_seconds:
            return self._no_trade(
                NoTradeReason.CHAINLINK_TOO_FRESH,
                f"Chainlink updated {chainlink_age:.0f}s ago, lag may be closing",
                **base,
            )

        # --- Determine direction from spot ---
        if spot_price > opening_price:
            direction = SignalAction.BUY_UP
            relevant_odds = polymarket_odds_up
        elif spot_price < opening_price:
            direction = SignalAction.BUY_DOWN
            relevant_odds = polymarket_odds_down
        else:
            return self._no_trade(
                NoTradeReason.LAG_BELOW_THRESHOLD,
                "Spot price equals opening price exactly",
                **base,
            )

        # --- FILTER 2: Richtungs-Konsistenz (30s lookback) ---
        if spot_price_30s_ago is not None:
            spot_movement_30s = abs(spot_price - spot_price_30s_ago)

            # Check for excessive volatility (>$50 swing in 30s)
            if spot_movement_30s > 50:
                return self._no_trade(
                    NoTradeReason.HIGH_VOLATILITY,
                    f"${spot_movement_30s:.2f} movement in 30s",
                    **base,
                )

            # Direction consistency: spot 30s ago should point same direction
            if direction == SignalAction.BUY_UP and spot_price_30s_ago > spot_price:
                return self._no_trade(
                    NoTradeReason.DIRECTION_INCONSISTENT,
                    "Spot was higher 30s ago but now signaling UP",
                    **base,
                )
            if direction == SignalAction.BUY_DOWN and spot_price_30s_ago < spot_price:
                return self._no_trade(
                    NoTradeReason.DIRECTION_INCONSISTENT,
                    "Spot was lower 30s ago but now signaling DOWN",
                    **base,
                )

        # --- FILTER 3: Odds-Filter ---
        if relevant_odds is not None:
            if relevant_odds > self.thresholds.max_odds:
                return self._no_trade(
                    NoTradeReason.ODDS_ALREADY_PRICED,
                    f"Odds {relevant_odds:.2f} > {self.thresholds.max_odds:.2f}",
                    polymarket_odds=relevant_odds,
                    **base,
                )
            if relevant_odds < self.thresholds.min_odds:
                return self._no_trade(
                    NoTradeReason.ODDS_TOO_LOW,
                    f"Odds {relevant_odds:.2f} < {self.thresholds.min_odds:.2f}",
                    polymarket_odds=relevant_odds,
                    **base,
                )

        # --- ALL FILTERS PASSED: Generate signal ---
        confidence = self._compute_confidence(
            abs_lag, chainlink_age, relevant_odds, time_to_resolution
        )
        position_size = self._compute_position_size(confidence, relevant_odds)

        reason = self._build_reason(direction, lag_delta, chainlink_age, relevant_odds)

        signal = TradeSignal(
            action=direction,
            time_to_resolution=time_to_resolution,
            spot_price=spot_price,
            chainlink_price=chainlink_price,
            lag_delta=lag_delta,
            polymarket_odds=relevant_odds,
            recommended_position_usd=position_size,
            confidence=confidence,
            reason=reason,
            opening_price=opening_price,
            spot_30s_ago=spot_price_30s_ago,
            chainlink_age_seconds=chainlink_age,
            timestamp=now,
        )

        logger.info("SIGNAL GENERATED:\n%s", signal)
        return signal

    def record_result(self, won: bool, pnl: float):
        """Record a trade result for risk management."""
        self._daily_pnl += pnl

        if won:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.risk.consecutive_loss_limit:
                self._pause_until = time.time() + self.risk.pause_duration_seconds
                logger.warning(
                    "PAUSE: %d consecutive losses. Pausing for %ds.",
                    self._consecutive_losses,
                    self.risk.pause_duration_seconds,
                )

    def _compute_confidence(
        self,
        abs_lag: float,
        chainlink_age: float,
        odds: Optional[float],
        time_to_resolution: float,
    ) -> Confidence:
        """Determine confidence level based on signal strength."""
        score = 0.0

        # Lag strength (0-40 points)
        if abs_lag > 20:
            score += 40
        elif abs_lag > 10:
            score += 30
        elif abs_lag > 5:
            score += 20
        else:
            score += 10

        # Chainlink staleness (0-30 points) - older = more confident lag persists
        if chainlink_age > 45:
            score += 30
        elif chainlink_age > 30:
            score += 20
        elif chainlink_age > 15:
            score += 10

        # Odds value (0-20 points) - lower odds = more edge
        if odds is not None:
            if odds < 0.60:
                score += 20
            elif odds < 0.70:
                score += 15
            elif odds < 0.80:
                score += 10

        # Timing (0-10 points) - T-20 to T-30 is ideal
        if 20 <= time_to_resolution <= 35:
            score += 10
        elif 15 <= time_to_resolution <= 45:
            score += 5

        if score >= 70:
            return Confidence.HIGH
        elif score >= 45:
            return Confidence.MEDIUM
        return Confidence.LOW

    def _compute_position_size(
        self, confidence: Confidence, odds: Optional[float]
    ) -> float:
        """Calculate position size based on confidence and Kelly criterion."""
        base_pct = self.risk.max_position_pct

        if confidence == Confidence.HIGH:
            multiplier = 1.0
        elif confidence == Confidence.MEDIUM:
            multiplier = 0.6
        else:
            multiplier = 0.3

        size = self.risk.total_capital * base_pct * multiplier

        # Clamp to risk limits
        size = max(self.risk.min_position_usd, min(size, self.risk.max_position_usd))
        return round(size, 2)

    def _build_reason(
        self,
        direction: SignalAction,
        lag_delta: float,
        chainlink_age: float,
        odds: Optional[float],
    ) -> str:
        dir_str = "UP" if direction == SignalAction.BUY_UP else "DOWN"
        odds_str = f", Odds {odds:.2f}" if odds else ""
        return (
            f"Spot signals {dir_str} with ${abs(lag_delta):.2f} lag, "
            f"Chainlink {chainlink_age:.0f}s stale{odds_str}"
        )

    def _no_trade(self, reason: NoTradeReason, detail: str, **kwargs) -> TradeSignal:
        """Create a NO_TRADE signal."""
        return TradeSignal(
            action=SignalAction.NO_TRADE,
            time_to_resolution=kwargs.get("time_to_resolution", 0),
            spot_price=kwargs.get("spot_price", 0),
            chainlink_price=kwargs.get("chainlink_price", 0),
            lag_delta=kwargs.get("lag_delta", 0),
            polymarket_odds=kwargs.get("polymarket_odds"),
            recommended_position_usd=0,
            confidence=Confidence.LOW,
            reason=detail,
            no_trade_reason=reason,
            opening_price=kwargs.get("opening_price"),
            chainlink_age_seconds=kwargs.get("chainlink_age_seconds"),
            timestamp=kwargs.get("timestamp", time.time()),
        )

    def _check_daily_reset(self):
        """Reset daily PnL at midnight UTC."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_reset_date = today
            self._daily_pnl = 0.0
            logger.info("Daily PnL reset for %s", today)
