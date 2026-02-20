"""
Polymarket Fee Calculator

Vollstaendige Berechnung aller Kosten und Gewinne fuer Polymarket-Trades.

Polymarket Fee-Struktur (Stand Februar 2026 / aktuelle Dokumentation):
=======================================================================

WICHTIG: Polymarket hat das Fee-Modell fuer Krypto-Kurzmarkte geaendert!

ALTES Modell (bis ~Mitte 2025):
  - Winner-Fee: 2% auf den Gewinn-Payout (nur bei Gewinn)
  - Taker-Fee:  0%
  - Gas:        ~$0.005 pro Tx (Polygon)

NEUES Modell (ab ~Mitte 2025, aktiv fuer 5min/15min Krypto-Markte):
  - Winner-Fee: 0% (Gewinner erhalten $1 ohne Abzug)
  - Taker-Fee:  DYNAMISCH, beim Kauf berechnet (nicht bei Resolution)
  - Gas:        ~$0.005 pro Tx (Polygon, unveraendert)

Dynamische Taker-Fee Formel:
  fee_rate(p) = max_taker_fee * 4 * p * (1 - p)

  Diese Parabel hat ihr Maximum bei p=0.50 und sinkt gegen 0 bei p→0 und p→1.

  Maximale Taker-Fees je Markttyp:
    5-Minuten-Krypto-Markte:   max = 0.44% = 0.0044  (bei p=0.50: 0.44%)
    15-Minuten-Krypto-Markte:  max = 1.56% = 0.0156  (bei p=0.50: 1.56%)
    Andere Markte (politisch etc.): 0% (nach wie vor gebuehrenfrei)

  Beispiele fuer 15-min Markte:
    p=0.50 → fee = 0.0156 * 4 * 0.5 * 0.5 = 1.56%
    p=0.55 → fee = 0.0156 * 4 * 0.55 * 0.45 = 1.54%
    p=0.60 → fee = 0.0156 * 4 * 0.60 * 0.40 = 1.50%
    p=0.70 → fee = 0.0156 * 4 * 0.70 * 0.30 = 1.31%
    p=0.80 → fee = 0.0156 * 4 * 0.80 * 0.20 = 1.00%

Maker-Fee / Maker-Rebate:
  - Maker-Fee: 0%
  - Maker-Rebate: Ja, aktiv (genaue Hoehe variiert)
    → Limit-Orders sind kostenguenstiger als Market-Orders

Break-Even-Analyse (neues Modell):
  Kosten pro Token: P + fee_rate(P) * P = P * (1 + f)
  Win-Profit: 1.0 - P * (1 + f)
  Lose-Profit: -P * (1 + f)
  Break-Even Win-Prob: P * (1 + f)

  Vergleich bei p=0.55:
    Alt (2% Winner-Fee): Break-Even = 0.55 / 0.98 = 56.1%
    Neu 5min (0.44%):    Break-Even = 0.55 * 1.0043 = 55.2%  ← guenstiger!
    Neu 15min (1.56%):   Break-Even = 0.55 * 1.0154 = 55.9%  ← aehnlich
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Markttyp-Konstanten
# ---------------------------------------------------------------------------

class MarketType:
    """Bekannte Markttypen mit ihren Max-Taker-Fees."""
    CRYPTO_5MIN  = "crypto_5min"   # max 0.44% Taker-Fee
    CRYPTO_15MIN = "crypto_15min"  # max 1.56% Taker-Fee
    OTHER        = "other"         # 0% (gebuehrenfrei)

# Max-Taker-Fee pro Markttyp (bei p=0.50)
MAX_TAKER_FEE: dict[str, float] = {
    MarketType.CRYPTO_5MIN:  0.0044,   # 0.44%
    MarketType.CRYPTO_15MIN: 0.0156,   # 1.56%
    MarketType.OTHER:        0.0,
}


@dataclass
class TradeEconomics:
    """Vollstaendige Oekonomie eines einzelnen Polymarket-Trades."""

    # Input
    ask_price: float            # Preis pro Token (0 < P < 1)
    usdc_size: float            # Eingesetztes Kapital in USDC
    win_probability: float      # Geschaetzte Gewinn-Wahrscheinlichkeit (0–1)

    # Abgeleitete Werte
    tokens: float = 0.0            # Anzahl gekaufter Token
    taker_fee_rate: float = 0.0    # Effektive Taker-Fee-Rate (%)
    taker_fee_amount: float = 0.0  # Absolute Taker-Fee in USDC
    gas_cost: float = 0.0          # Polygon-Gas (pauschal)
    total_cost: float = 0.0        # Gesamtkosten (Kauf + Fee + Gas)
    gross_payout_win: float = 0.0  # Brutto-Gewinn wenn Win ($1 * tokens)
    winner_fee_amount: float = 0.0 # Winner-Fee (meist 0 im neuen Modell)
    net_payout_win: float = 0.0    # Netto-Auszahlung wenn Win
    net_profit_win: float = 0.0    # Gewinn nach allen Kosten (Win)
    net_profit_lose: float = 0.0   # Verlust nach allen Kosten (Lose)
    expected_value: float = 0.0    # EV = P_win * profit_win + P_lose * profit_lose
    roi_win: float = 0.0           # Return on Investment bei Gewinn (%)
    roi_lose: float = 0.0          # Return on Investment bei Verlust (%)
    expected_roi: float = 0.0      # Erwarteter ROI (%)
    break_even_probability: float = 0.0  # Mindest-Win-Rate um profitabel zu sein
    edge: float = 0.0              # edge = win_prob - break_even_prob (>0 = profitabel)
    is_profitable: bool = False    # Ist der Trade mit dieser Win-Prob profitabel?

    def summary(self) -> str:
        sign = "+" if self.expected_value >= 0 else ""
        return (
            f"Ask={self.ask_price:.3f} | Size=${self.usdc_size:.2f} | "
            f"Taker-Fee={self.taker_fee_rate:.3%} (${self.taker_fee_amount:.4f}) | "
            f"Tokens={self.tokens:.2f} | P_win={self.win_probability:.1%} | "
            f"Break-Even={self.break_even_probability:.1%} | "
            f"Edge={self.edge:+.3f} | "
            f"EV={sign}{self.expected_value:.4f} USDC | "
            f"ROI(win)={self.roi_win:+.1%} ROI(lose)={self.roi_lose:+.1%} | "
            f"{'PROFITABLE' if self.is_profitable else 'NOT PROFITABLE'}"
        )


class FeeCalculator:
    """
    Zentraler Polymarket Fee-Rechner (aktuelles Modell, Feb 2026).

    Unterstuetzt beide Modelle:
    - Neues Modell (Standard): Dynamische Taker-Fee beim Kauf, keine Winner-Fee
    - Altes Modell (Fallback): 2% Winner-Fee bei Gewinn

    Dynamische Taker-Fee: fee_rate(p) = max_taker_fee * 4 * p * (1 - p)
    - 5min:  max_taker_fee = 0.0044  (max 0.44% bei p=0.50)
    - 15min: max_taker_fee = 0.0156  (max 1.56% bei p=0.50)
    """

    # Standard-Polygon-Gas (pauschal pro Transaktion)
    GAS_COST_USDC: float = 0.005

    def __init__(
        self,
        market_type: str = MarketType.CRYPTO_5MIN,
        gas_cost: float = GAS_COST_USDC,
        # Altes Modell (Fallback):
        use_legacy_winner_fee: bool = False,
        winner_fee: float = 0.0,
        # Manuelle Ueberschreibung der max Taker-Fee:
        max_taker_fee_override: Optional[float] = None,
    ):
        self.market_type = market_type
        self.gas_cost = gas_cost
        self.use_legacy_winner_fee = use_legacy_winner_fee
        self.winner_fee = winner_fee

        if max_taker_fee_override is not None:
            self._max_taker_fee = max_taker_fee_override
        else:
            self._max_taker_fee = MAX_TAKER_FEE.get(market_type, 0.0)

    # ------------------------------------------------------------------
    # Taker-Fee (dynamisch, parabolisch)
    # ------------------------------------------------------------------

    def taker_fee_rate(self, ask_price: float) -> float:
        """
        Effektive Taker-Fee-Rate fuer einen gegebenen Ask-Preis.

        fee_rate = max_taker_fee * 4 * p * (1 - p)

        Maximum bei p=0.50, sinkt gegen 0 bei p→0 oder p→1.
        """
        if ask_price <= 0 or ask_price >= 1:
            return 0.0
        return self._max_taker_fee * 4.0 * ask_price * (1.0 - ask_price)

    def taker_fee_amount(self, ask_price: float, usdc_size: float) -> float:
        """Absolute Taker-Fee in USDC fuer diesen Trade."""
        return usdc_size * self.taker_fee_rate(ask_price)

    # ------------------------------------------------------------------
    # Gesamtkosten
    # ------------------------------------------------------------------

    def total_cost(self, ask_price: float, usdc_size: float) -> float:
        """
        Gesamtkosten des Trades (Kauf + Taker-Fee + Gas).
        Beim alten Modell: nur Kauf + Gas (Winner-Fee kommt erst bei Aufloesung).
        """
        if self.use_legacy_winner_fee:
            return usdc_size + self.gas_cost
        return usdc_size + self.taker_fee_amount(ask_price, usdc_size) + self.gas_cost

    # ------------------------------------------------------------------
    # Break-Even-Analyse
    # ------------------------------------------------------------------

    def break_even_probability(self, ask_price: float, usdc_size: float = 10.0) -> float:
        """
        Minimale Win-Wahrscheinlichkeit, ab der ein Trade profitabel ist.

        Neues Modell:
          p_min = total_cost / tokens
          = (P * (1 + f) + gas/N) / 1.0
          ≈ P * (1 + f)  (fuer gas ≈ 0)

        Altes Modell (Winner-Fee):
          p_min = total_cost / net_payout_if_win
          = (P + gas/N) / (1 - winner_fee)
        """
        if ask_price <= 0 or ask_price >= 1:
            return 1.0

        tokens = usdc_size / ask_price

        if self.use_legacy_winner_fee:
            # Alt: Winner-Fee wird vom Payout abgezogen
            # Herleitung: P_min * profit_win = (1-P_min) * |profit_lose|
            #   => P_min = cost / net_payout_win
            # (Vereinfacht: P_min ≈ ask / (1 - winner_fee))
            net_payout_win = tokens * (1.0 - self.winner_fee) - self.gas_cost
            cost = usdc_size + self.gas_cost
            return cost / net_payout_win if net_payout_win > 0 else 1.0
        else:
            # Neu: Taker-Fee wird aufgeschlagen, Win = $1 pro Token ohne Abzug
            f = self.taker_fee_rate(ask_price)
            cost_per_token = ask_price * (1.0 + f) + self.gas_cost / tokens
            # break_even = cost_per_token / 1.0 = cost_per_token
            return min(1.0, cost_per_token)

    # ------------------------------------------------------------------
    # Gewinn/Verlust
    # ------------------------------------------------------------------

    def profit_if_win(self, ask_price: float, usdc_size: float) -> float:
        """Gewinn (netto) wenn der Trade gewonnen wird."""
        tokens = usdc_size / ask_price
        if self.use_legacy_winner_fee:
            return tokens * (1.0 - self.winner_fee) - self.gas_cost - usdc_size
        else:
            fee = self.taker_fee_amount(ask_price, usdc_size)
            return tokens * 1.0 - usdc_size - fee - self.gas_cost

    def profit_if_lose(self, ask_price: float, usdc_size: float) -> float:
        """Verlust (netto) wenn der Trade verloren wird."""
        if self.use_legacy_winner_fee:
            return -(usdc_size + self.gas_cost)
        else:
            fee = self.taker_fee_amount(ask_price, usdc_size)
            return -(usdc_size + fee + self.gas_cost)

    def expected_value(
        self,
        ask_price: float,
        usdc_size: float,
        win_probability: float,
    ) -> float:
        """EV = P(win) * profit_win + P(lose) * profit_lose"""
        return (
            win_probability * self.profit_if_win(ask_price, usdc_size)
            + (1 - win_probability) * self.profit_if_lose(ask_price, usdc_size)
        )

    def edge(self, ask_price: float, win_probability: float, usdc_size: float = 10.0) -> float:
        """Edge = (geschaetzte Win-Prob) - (Break-Even Win-Prob)."""
        return win_probability - self.break_even_probability(ask_price, usdc_size)

    # ------------------------------------------------------------------
    # Vollstaendige Analyse
    # ------------------------------------------------------------------

    def analyze(
        self,
        ask_price: float,
        usdc_size: float,
        win_probability: float,
    ) -> TradeEconomics:
        """Vollstaendige Analyse eines geplanten Trades."""
        tokens = usdc_size / ask_price
        f_rate = self.taker_fee_rate(ask_price)
        f_amount = self.taker_fee_amount(ask_price, usdc_size)

        if self.use_legacy_winner_fee:
            winner_fee_amount = tokens * self.winner_fee
            net_payout_win = tokens * 1.0 - winner_fee_amount - self.gas_cost
            total_cost_val = usdc_size + self.gas_cost
        else:
            winner_fee_amount = 0.0
            net_payout_win = tokens * 1.0 - self.gas_cost
            total_cost_val = usdc_size + f_amount + self.gas_cost

        net_profit_win = net_payout_win - total_cost_val + self.gas_cost  # gas bereits in cost
        net_profit_win = self.profit_if_win(ask_price, usdc_size)
        net_profit_lose = self.profit_if_lose(ask_price, usdc_size)

        ev = win_probability * net_profit_win + (1 - win_probability) * net_profit_lose
        roi_win = net_profit_win / usdc_size if usdc_size > 0 else 0.0
        roi_lose = net_profit_lose / usdc_size if usdc_size > 0 else -1.0
        expected_roi = ev / usdc_size if usdc_size > 0 else 0.0

        be_prob = self.break_even_probability(ask_price, usdc_size)
        edge_val = win_probability - be_prob

        return TradeEconomics(
            ask_price=ask_price,
            usdc_size=usdc_size,
            win_probability=win_probability,
            tokens=tokens,
            taker_fee_rate=f_rate,
            taker_fee_amount=f_amount,
            gas_cost=self.gas_cost,
            total_cost=total_cost_val,
            gross_payout_win=tokens * 1.0,
            winner_fee_amount=winner_fee_amount,
            net_payout_win=net_payout_win,
            net_profit_win=net_profit_win,
            net_profit_lose=net_profit_lose,
            expected_value=ev,
            roi_win=roi_win,
            roi_lose=roi_lose,
            expected_roi=expected_roi,
            break_even_probability=be_prob,
            edge=edge_val,
            is_profitable=ev > 0,
        )

    # ------------------------------------------------------------------
    # Arbitrage
    # ------------------------------------------------------------------

    def arb_profit(self, up_ask: float, down_ask: float, usdc_size: float) -> float:
        """
        Garantierter Gewinn bei Intra-Market-Arbitrage (beide Seiten kaufen).

        Kosten: usdc_size gesamt (auf beide Seiten verteilt)
        Einer gewinnt immer -> Payout = tokens_win * 1.0
        Taker-Fee wird fuer beide Seiten berechnet.
        """
        size_each = usdc_size / 2.0
        combined_ask = up_ask + down_ask

        # Kosten fuer up-Seite
        fee_up = self.taker_fee_amount(up_ask, size_each)
        tokens_up = size_each / up_ask

        # Kosten fuer down-Seite
        fee_down = self.taker_fee_amount(down_ask, size_each)
        tokens_down = size_each / down_ask

        # Annahme: beide Seiten gleich gross (vereinfacht)
        # Gewinner bekommt tokens_win * $1
        # Vereinfachung: profit = avg_tokens * 1.0 - total_cost
        avg_tokens = (tokens_up + tokens_down) / 2  # einer von beiden gewinnt
        total_fees = fee_up + fee_down + 2 * self.gas_cost
        total_cost = usdc_size + total_fees

        if self.use_legacy_winner_fee:
            payout = avg_tokens * (1.0 - self.winner_fee)
        else:
            payout = avg_tokens * 1.0

        return payout - total_cost + (usdc_size - size_each * 2)  # size_each * 2 = usdc_size

    def arb_profit_simple(self, up_ask: float, down_ask: float) -> float:
        """
        Vereinfachter Arb-Profit pro 1 USDC Gesamteinsatz.
        Payout pro combined_ask = 1.0 - taker_fees(both sides)
        """
        combined = up_ask + down_ask
        # fee auf die gewinnende Seite (vereinfacht: up-Seite)
        fee = self.taker_fee_rate(up_ask) * up_ask + self.taker_fee_rate(down_ask) * down_ask
        payout = (1.0 - self.winner_fee) if self.use_legacy_winner_fee else 1.0
        return payout - combined - fee

    # ------------------------------------------------------------------
    # PnL bei Positionsschliessen
    # ------------------------------------------------------------------

    def close_position_pnl(
        self,
        tokens: float,
        cost: float,
        won: bool,
        ask_price: Optional[float] = None,
    ) -> tuple[float, float]:
        """
        Berechnet (pnl_gross, pnl_after_fee) beim Schliessen einer Position.

        Args:
            tokens:    Anzahl Token in der Position
            cost:      Urspruengliche USDC-Kosten (exkl. Taker-Fee)
            won:       Hat die Position gewonnen?
            ask_price: Originaler Kaufpreis (fuer Taker-Fee-Rekonstruktion, optional)

        Returns:
            (pnl_gross, pnl_after_fee) als Tuple
        """
        if won:
            if self.use_legacy_winner_fee:
                gross = tokens * 1.0
                fee = gross * self.winner_fee
                net = gross - fee - self.gas_cost
            else:
                # Im neuen Modell wurde die Taker-Fee bereits beim Kauf bezahlt.
                # Hier nur noch Gas abziehen.
                gross = tokens * 1.0
                net = gross - self.gas_cost
            pnl_gross = gross - cost
            pnl_after_fee = net - cost
        else:
            pnl_gross = -cost
            pnl_after_fee = -(cost + self.gas_cost)

        return pnl_gross, pnl_after_fee

    # ------------------------------------------------------------------
    # Vergleichstabelle
    # ------------------------------------------------------------------

    def break_even_table(self, usdc_size: float = 10.0) -> str:
        """Tabelle der Break-Even-Wahrscheinlichkeiten fuer gaengige Ask-Preise."""
        mtype = {
            MarketType.CRYPTO_5MIN: "5-min",
            MarketType.CRYPTO_15MIN: "15-min",
        }.get(self.market_type, "andere")

        lines = [
            f"Polymarket Fee-Kalkulation [{mtype}] "
            f"(max_taker={self._max_taker_fee:.2%}, Gas={self.gas_cost:.3f} USDC, "
            f"Size=${usdc_size:.0f})\n",
            f"{'Ask':>8} {'Taker-Fee%':>11} {'Break-Even':>12} {'ROI Win':>10} "
            f"{'ROI Lose':>10} {'EV@55%':>10} {'EV@60%':>10}",
            "-" * 80,
        ]
        for ask in [0.45, 0.50, 0.52, 0.55, 0.58, 0.60, 0.62, 0.65, 0.70, 0.75, 0.80]:
            f_rate = self.taker_fee_rate(ask)
            be = self.break_even_probability(ask, usdc_size)
            roi_w = (self.profit_if_win(ask, usdc_size) / usdc_size) * 100
            roi_l = (self.profit_if_lose(ask, usdc_size) / usdc_size) * 100
            ev55 = self.expected_value(ask, usdc_size, 0.55)
            ev60 = self.expected_value(ask, usdc_size, 0.60)
            lines.append(
                f"{ask:>8.3f} {f_rate:>10.3%}  {be:>11.2%}  {roi_w:>+8.1f}%  "
                f"{roi_l:>+8.1f}%  {ev55:>+8.4f}  {ev60:>+8.4f}"
            )
        return "\n".join(lines)

    @staticmethod
    def compare_market_types(ask_price: float = 0.55, usdc_size: float = 10.0) -> str:
        """Vergleich der Fees zwischen 5min, 15min und altem Modell."""
        fc5  = FeeCalculator(MarketType.CRYPTO_5MIN)
        fc15 = FeeCalculator(MarketType.CRYPTO_15MIN)
        fcOld = FeeCalculator(use_legacy_winner_fee=True, winner_fee=0.02)

        lines = [
            f"Fee-Vergleich bei Ask={ask_price:.2f}, Size=${usdc_size:.0f}",
            f"{'Modell':>20} {'Taker-Fee':>12} {'Break-Even':>12} "
            f"{'Win-Profit':>12} {'EV@60%':>10}",
            "-" * 75,
        ]
        for label, fc in [("5min (neu, 0.44%)", fc5), ("15min (neu, 1.56%)", fc15), ("Alt (2% Winner)", fcOld)]:
            f_rate = fc.taker_fee_rate(ask_price) if not fc.use_legacy_winner_fee else 0.0
            be = fc.break_even_probability(ask_price, usdc_size)
            wp = fc.profit_if_win(ask_price, usdc_size)
            ev = fc.expected_value(ask_price, usdc_size, 0.60)
            lines.append(
                f"{label:>20} {f_rate:>11.3%}  {be:>11.2%}  "
                f"{wp:>+10.4f}$  {ev:>+8.4f}$"
            )
        return "\n".join(lines)


# ------------------------------------------------------------------
# Globale Singletons
# ------------------------------------------------------------------

FEES_5MIN  = FeeCalculator(MarketType.CRYPTO_5MIN)
FEES_15MIN = FeeCalculator(MarketType.CRYPTO_15MIN)

# Kompatibilitaets-Alias (altes Modell)
POLYMARKET_FEES = FeeCalculator(
    use_legacy_winner_fee=True,
    winner_fee=0.02,
)
