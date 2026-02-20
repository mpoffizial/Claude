"""
Polymarket Fee Calculator

Vollstaendige Berechnung aller Kosten und Gewinne fuer Polymarket-Trades.

Polymarket Fee-Struktur (Stand 2025):
--------------------------------------
1. WINNER FEE:  2% wird vom Gewinn-Payout abgezogen (nur bei Gewinn-Token)
2. MAKER FEE:   0%  (Limit-Orders = Maker → keine Gebuehr)
3. TAKER FEE:   0%  (Market-Orders direkt auf dem CLOB = keine Gebuehr)
4. GAS (Polygon): ~0.001–0.01 USDC pro Transaktion (vernachlaessigbar)

Das bedeutet: Die einzige relevante Gebuehr ist die 2% Winner-Fee.
Aber: Der implizite Spread-Kosten (Kauf an Ask statt Mid) muss separat gerechnet werden.

Break-Even-Analyse:
-------------------
  Kauf von N Token zu Preis P (P = Ask-Preis, 0 < P < 1)
  Kosten: N * P  (=  Trade-Groesse in USDC)

  Falls GEWINN:
    Brutto-Auszahlung:  N * 1.0
    Winner-Fee:         N * 0.02
    Gas:                ~0.005 USDC (Polygon, pauschal)
    Netto-Auszahlung:   N * 0.98 - gas
    Gewinn:             N * 0.98 - gas - N * P = N * (0.98 - P) - gas

  Falls VERLUST:
    Auszahlung:  0
    Gas:         ~0.005 USDC
    Verlust:     -N * P - gas

  Break-Even Win-Wahrscheinlichkeit P_min:
    P_min * N * (0.98 - P) = (1 - P_min) * N * P + gas_total * 2
    P_min = (P + gas_per_token) / (0.98 + gas_per_token - P_taker_fee)
    Vereinfacht (gas≈0):  P_min = P / 0.98

  Beispiele:
    Ask=0.50 → P_min = 51.0%  (sehr attraktiv, nahe 50:50)
    Ask=0.55 → P_min = 56.1%
    Ask=0.60 → P_min = 61.2%
    Ask=0.65 → P_min = 66.3%
    Ask=0.70 → P_min = 71.4%
    Ask=0.75 → P_min = 76.5%
    Ask=0.80 → P_min = 81.6%  (teuer – kaum Upside)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeEconomics:
    """Vollstaendige Oekonomie eines einzelnen Polymarket-Trades."""

    # Input
    ask_price: float            # Preis pro Token (0 < P < 1)
    usdc_size: float            # Eingesetztes Kapital in USDC
    win_probability: float      # Geschaetzte Gewinn-Wahrscheinlichkeit (0–1)

    # Abgeleitete Werte
    tokens: float = 0.0            # Anzahl gekaufter Token
    gross_payout_win: float = 0.0  # Brutto-Gewinn wenn Win ($1 * tokens)
    winner_fee_amount: float = 0.0 # 2%-Gebuehr bei Gewinn (in USDC)
    gas_cost: float = 0.0          # Polygon-Gas (pauschal)
    net_payout_win: float = 0.0    # Netto-Gewinn wenn Win
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
            f"Tokens={self.tokens:.2f} | P_win={self.win_probability:.1%} | "
            f"Break-Even={self.break_even_probability:.1%} | "
            f"Edge={self.edge:+.3f} | "
            f"EV={sign}{self.expected_value:.4f} USDC | "
            f"ROI(win)={self.roi_win:+.1%} ROI(lose)={self.roi_lose:+.1%} | "
            f"{'PROFITABLE' if self.is_profitable else 'NOT PROFITABLE'}"
        )


class FeeCalculator:
    """
    Zentraler Polymarket Fee-Rechner.

    Berechnet exakt:
    - Winner-Fee (2% auf Payout beim Gewinner)
    - Maker-Fee (0%)
    - Taker-Fee (0%)
    - Polygon Gas (~0.005 USDC/Tx, pauschal)
    - Break-Even Win-Wahrscheinlichkeit
    - Erwarteten Wert (EV)
    - Edge = (geschaetzte Win-Prob) - (Break-Even Win-Prob)
    """

    # Polymarket Fee-Konstanten (Stand 2025)
    WINNER_FEE_RATE: float = 0.02      # 2% Gebuehr auf Payout bei Gewinn
    MAKER_FEE_RATE: float = 0.0        # 0% fuer Limit-Orders (Maker)
    TAKER_FEE_RATE: float = 0.0        # 0% fuer Market-Orders (Taker)
    GAS_COST_USDC: float = 0.005       # ~$0.005 pro Transaktion (Polygon)

    def __init__(
        self,
        winner_fee: float = WINNER_FEE_RATE,
        maker_fee: float = MAKER_FEE_RATE,
        taker_fee: float = TAKER_FEE_RATE,
        gas_cost: float = GAS_COST_USDC,
    ):
        self.winner_fee = winner_fee
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.gas_cost = gas_cost

    # ------------------------------------------------------------------
    # Kern-Berechnungen
    # ------------------------------------------------------------------

    def break_even_probability(self, ask_price: float, usdc_size: float = 10.0) -> float:
        """
        Minimale Win-Wahrscheinlichkeit, ab der ein Trade profitabel ist.

        Herleitung:
          P_min * profit_win + (1 - P_min) * profit_lose = 0
          P_min * (net_payout_win - cost) = (1 - P_min) * cost + gas_total

          Mit gas_total ≈ 2 * gas_cost (Entry + Exit via Resolution):
          P_min = (cost + gas_total) / (net_payout_win + gas_total)
        """
        if ask_price <= 0 or ask_price >= 1:
            return 1.0

        tokens = usdc_size / ask_price
        net_payout_win = tokens * (1.0 - self.winner_fee) - self.gas_cost
        cost_with_gas = usdc_size + self.gas_cost

        if net_payout_win <= 0:
            return 1.0

        return cost_with_gas / (net_payout_win + cost_with_gas)

    def net_payout_if_win(self, ask_price: float, usdc_size: float) -> float:
        """Netto-Auszahlung bei Gewinn (nach Winner-Fee und Gas)."""
        tokens = usdc_size / ask_price
        return tokens * (1.0 - self.winner_fee) - self.gas_cost

    def profit_if_win(self, ask_price: float, usdc_size: float) -> float:
        """Gewinn nach allen Kosten wenn Trade gewonnen wird."""
        return self.net_payout_if_win(ask_price, usdc_size) - usdc_size

    def profit_if_lose(self, ask_price: float, usdc_size: float) -> float:
        """Verlust nach allen Kosten wenn Trade verloren wird."""
        return -(usdc_size + self.gas_cost)

    def expected_value(
        self,
        ask_price: float,
        usdc_size: float,
        win_probability: float,
    ) -> float:
        """
        Erwarteter Wert des Trades in USDC.
        EV = P(win) * profit_win + P(lose) * profit_lose
        """
        win_profit = self.profit_if_win(ask_price, usdc_size)
        lose_profit = self.profit_if_lose(ask_price, usdc_size)
        return win_probability * win_profit + (1 - win_probability) * lose_profit

    def edge(self, ask_price: float, win_probability: float, usdc_size: float = 10.0) -> float:
        """
        Edge = (geschaetzte Win-Prob) - (Break-Even Win-Prob).
        Positiver Edge bedeutet profitabel.
        """
        be_prob = self.break_even_probability(ask_price, usdc_size)
        return win_probability - be_prob

    def analyze(
        self,
        ask_price: float,
        usdc_size: float,
        win_probability: float,
    ) -> TradeEconomics:
        """
        Vollstaendige Analyse eines geplanten Trades.

        Args:
            ask_price:       Token-Kaufpreis (0–1)
            usdc_size:       Einzusetzendes Kapital in USDC
            win_probability: Geschaetzte Gewinnwahrscheinlichkeit (0–1)

        Returns:
            TradeEconomics mit allen berechneten Werten
        """
        tokens = usdc_size / ask_price
        gross_payout_win = tokens * 1.0
        winner_fee_amount = gross_payout_win * self.winner_fee
        net_payout_win = gross_payout_win - winner_fee_amount - self.gas_cost

        net_profit_win = net_payout_win - usdc_size
        net_profit_lose = -(usdc_size + self.gas_cost)

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
            gross_payout_win=gross_payout_win,
            winner_fee_amount=winner_fee_amount,
            gas_cost=self.gas_cost,
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
    # Hilfsmethoden fuer Arbitrage
    # ------------------------------------------------------------------

    def arb_profit(self, up_ask: float, down_ask: float, usdc_size: float) -> float:
        """
        Garantierter Gewinn bei Intra-Market-Arbitrage.

        Beide Seiten kaufen: Kosten = up_ask + down_ask (pro Token-Paar).
        Einer gewinnt immer → Payout = 1.0 * tokens_win * (1 - winner_fee).

        Vereinfacht (beide Seiten gleich gross):
          Kosten pro Paar: combined_ask = up_ask + down_ask
          Payout pro Paar: 1.0 * (1 - 0.02) = 0.98
          Gewinn pro Paar: 0.98 - combined_ask
        """
        combined_ask = up_ask + down_ask
        payout = 1.0 - self.winner_fee
        profit_per_token = payout - combined_ask
        tokens = usdc_size / combined_ask
        return tokens * profit_per_token

    def arb_break_even_combined(self) -> float:
        """
        Maximaler Combined-Ask fuer profitable Arbitrage.
        Break-Even: combined_ask = 1 - winner_fee = 0.98
        Mit Gas: etwas darunter.
        """
        return 1.0 - self.winner_fee

    # ------------------------------------------------------------------
    # Hilfsmethoden: PnL-Berechnung bei Positionsschliessen
    # ------------------------------------------------------------------

    def close_position_pnl(
        self,
        tokens: float,
        cost: float,
        won: bool,
    ) -> tuple[float, float]:
        """
        Berechnet PnL und PnL-nach-Fee beim Schliessen einer Position.

        Args:
            tokens: Anzahl Token in der Position
            cost:   Urspruengliche Kosten (USDC) beim Oeffnen
            won:    Hat die Position gewonnen?

        Returns:
            (pnl_gross, pnl_after_fee) als Tuple
        """
        if won:
            gross_payout = tokens * 1.0
            fee = gross_payout * self.winner_fee
            net_payout = gross_payout - fee - self.gas_cost
            pnl_gross = gross_payout - cost
            pnl_after_fee = net_payout - cost
        else:
            pnl_gross = -cost
            pnl_after_fee = -(cost + self.gas_cost)

        return pnl_gross, pnl_after_fee

    # ------------------------------------------------------------------
    # Tabelle: Break-Even-Wahrscheinlichkeiten fuer gaengige Ask-Preise
    # ------------------------------------------------------------------

    def break_even_table(self, usdc_size: float = 10.0) -> str:
        """
        Gibt eine Tabelle der Break-Even-Wahrscheinlichkeiten aus.
        Hilfreich zum schnellen Einschaetzen ob ein Preis attraktiv ist.
        """
        lines = [
            f"Polymarket Fee-Kalkulation (Winner-Fee={self.winner_fee:.0%}, "
            f"Gas={self.gas_cost:.3f} USDC, Trade-Groesse=${usdc_size:.0f})\n",
            f"{'Ask-Preis':>12} {'Break-Even Win%':>16} {'ROI bei Win':>12} "
            f"{'ROI bei Lose':>13} {'EV bei 55%':>12} {'EV bei 60%':>12}",
            "-" * 80,
        ]
        for ask in [0.45, 0.50, 0.52, 0.55, 0.58, 0.60, 0.62, 0.65, 0.70, 0.75, 0.80]:
            be = self.break_even_probability(ask, usdc_size)
            roi_w = (self.profit_if_win(ask, usdc_size) / usdc_size) * 100
            roi_l = (self.profit_if_lose(ask, usdc_size) / usdc_size) * 100
            ev55 = self.expected_value(ask, usdc_size, 0.55)
            ev60 = self.expected_value(ask, usdc_size, 0.60)
            lines.append(
                f"{ask:>12.3f} {be:>15.2%}  {roi_w:>+10.1f}%  {roi_l:>+10.1f}%  "
                f"{ev55:>+10.4f}  {ev60:>+10.4f}"
            )
        return "\n".join(lines)


# Globale Singleton-Instanz mit Standard-Polymarket-Fees
POLYMARKET_FEES = FeeCalculator(
    winner_fee=0.02,
    maker_fee=0.0,
    taker_fee=0.0,
    gas_cost=0.005,
)
