"""
Backtest — Polymarket BTC Up/Down 5min Märkte | Letzte 3 Tage | $2 Risk

Datenquellen (100% echt):
  Polymarket: backtest_data/polymarket_btc_markets_30d.json
              7464 aufgelöste Märkte mit echten bestBid/bestAsk/outcomePrices
  BTC Preis:  Kraken REST API (5min OHLC, live abgerufen)

Zwei Strategien im Vergleich:
─────────────────────────────────────────────────────────────────────────────
  A | MARKET MAKING (beide Seiten)
      Wir posten Bid und Ask symmetrisch um die Fair Value.
      Fill-Modell (konservativ / realistisch / optimistisch):
        Konservativ:  P(jede Seite füllt) = 50%  → P(beide) = 25%
        Realistisch:  P(jede Seite füllt) = 70%  → P(beide) = 49%
        Optimistisch: P(jede Seite füllt) = 90%  → P(beide) = 81%
      Basis: BTC 5min Median-HL-Range = 0.073%, Markt-Spread = 1%
             Mit 4% Spread sind unsere Quotes 2% von mid entfernt.

  B | DIRECTIONALER MOMENTUM TRADE
      Wenn BTC in den letzten 5min um >THRESH bewegte, kaufen wir die
      Richtung. $2 Einsatz, zahlen Market-Ask (~0.505).
      Win:  +$1.88  (2/0.505 × 0.98 - 2)
      Lose: -$2.00
─────────────────────────────────────────────────────────────────────────────
"""

import json
import datetime
import urllib.request
import os

os.chdir("/home/user/Claude")

# ═══════════════════════════ KONFIGURATION ═══════════════════════════════════
RISK_PER_SIDE    = 2.00   # $ USDC je Seite (Bid ODER Ask)
BASE_SPREAD      = 0.04   # 4% Gesamtspread (Minimum für positiven EV nach 2% Fee)
WINNER_FEE       = 0.02   # Polymarket 2% Gewinner-Fee
SLIPPAGE         = 0.005  # 0.5% Slippage auf Entry
DAYS_BACK        = 3

# Strategie B
MOM_THRESH       = 0.0003 # 0.03% BTC-Bewegung für Momentum-Signal
                          # (Median 5min BTC Move: 0.044% → ca. 30% der Märkte)
MOM_ENTRY_ASK    = 0.505  # Realistischer Ask beim Kauf (ein Tick über mid)

# Fill-Wahrscheinlichkeiten Strategie A
FILL_PROB = {
    "konservativ":   0.50,   # 50% je Seite
    "realistisch":   0.70,   # 70% je Seite (unser Basis-Szenario)
    "optimistisch":  0.90,   # 90% je Seite
}

# ═══════════════════════════ DATEN LADEN ═════════════════════════════════════

def load_kraken(days: int) -> dict[int, dict]:
    since = int((datetime.datetime.utcnow() - datetime.timedelta(days=days + 0.5)).timestamp())
    url   = f"https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=5&since={since}"
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            data = json.loads(r.read())
        raw = data["result"]["XXBTZUSD"]
    except Exception as e:
        print(f"  [WARN] Kraken live-Abruf fehlgeschlagen ({e}), nutze lokale Datei")
        with open("backtest_data/btc_5min_kraken.json") as fh:
            raw = json.load(fh)
    # {ts → {open, high, low, close}}
    return {int(k[0]): {"o": float(k[1]), "h": float(k[2]),
                        "l": float(k[3]), "c": float(k[4])} for k in raw}


def btc_price(klines: dict, ts: datetime.datetime, field: str = "c") -> float:
    unix = int(ts.timestamp())
    base = (unix // 300) * 300
    for d in (0, 300, -300, 600, -600, 900, -900):
        entry = klines.get(base + d)
        if entry:
            return entry[field]
    return 0.0


def load_polymarket(days: int) -> list[dict]:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    with open("backtest_data/polymarket_btc_markets_30d.json", encoding="utf-8") as fh:
        raw = json.load(fh)
    result = []
    for m in raw:
        if "Bitcoin Up or Down" not in m.get("question", ""):
            continue
        if not m.get("closed"):
            continue

        est_s = m.get("eventStartTime", "")
        end_s = m.get("endDate", "")
        if not est_s or not end_s:
            continue
        try:
            est = datetime.datetime.fromisoformat(est_s.replace("Z", "+00:00")).replace(tzinfo=None)
            end = datetime.datetime.fromisoformat(end_s.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            continue
        if (end - est).total_seconds() != 300:
            continue
        if est < cutoff:
            continue

        op = [float(x) for x in json.loads(m.get("outcomePrices", '["0.5","0.5"]'))]
        if op[0] not in (0.0, 1.0):
            continue

        vol = float(m.get("volume") or 0)
        if vol < 1000:
            continue

        outcomes = json.loads(m.get("outcomes", '["Up","Down"]'))
        up_idx   = next((i for i, o in enumerate(outcomes) if "up" in o.lower()), 0)
        up_won   = op[up_idx] == 1.0

        result.append({
            "q":      m.get("question", ""),
            "start":  est,
            "end":    end,
            "up_won": up_won,
            "volume": vol,
        })
    result.sort(key=lambda x: x["start"])
    return result


# ═══════════════════════════ STRATEGIE A: MARKET MAKING ══════════════════════

def run_strategy_a(markets, klines, fill_prob: float) -> list[dict]:
    """
    Simuliert MM auf allen Märkten mit gegebener Fill-Wahrscheinlichkeit.
    Beide Seiten sind stochastisch unabhängig.
    """
    import random
    random.seed(42)  # Reproduzierbar

    results = []
    for m in markets:
        # Fair Value aus BTC-Momentum (5min vor Start)
        btc_now  = btc_price(klines, m["start"], "o")
        btc_prev = btc_price(klines, m["start"] - datetime.timedelta(minutes=5), "c")
        if btc_now <= 0 or btc_prev <= 0:
            continue

        momentum = (btc_now - btc_prev) / btc_prev
        skew     = max(-0.06, min(0.06, momentum * 20))
        fv       = max(0.10, min(0.90, 0.50 + skew))

        bid = max(0.02, fv - BASE_SPREAD / 2)
        ask = min(0.98, fv + BASE_SPREAD / 2)

        bid_fill = random.random() < fill_prob
        ask_fill = random.random() < fill_prob

        if not bid_fill and not ask_fill:
            continue

        up_won = m["up_won"]
        pnl    = 0.0
        risked = 0.0

        # BID-Seite: Wir kaufen Up-Tokens zu bid_price
        if bid_fill:
            entry  = bid * (1 + SLIPPAGE)
            tokens = RISK_PER_SIDE / entry
            if up_won:
                gross = tokens
                pnl  += gross * (1 - WINNER_FEE) - RISK_PER_SIDE
            else:
                pnl  -= RISK_PER_SIDE
            risked += RISK_PER_SIDE

        # ASK-Seite: Wir kaufen Down-Tokens zu (1 - ask_price)
        if ask_fill:
            down_entry = (1.0 - ask) * (1 + SLIPPAGE)
            tokens     = RISK_PER_SIDE / down_entry
            down_won   = not up_won
            if down_won:
                gross = tokens
                pnl  += gross * (1 - WINNER_FEE) - RISK_PER_SIDE
            else:
                pnl  -= RISK_PER_SIDE
            risked += RISK_PER_SIDE

        results.append({
            "start":      m["start"],
            "up_won":     up_won,
            "fv":         fv,
            "momentum":   momentum,
            "bid_fill":   bid_fill,
            "ask_fill":   ask_fill,
            "both":       bid_fill and ask_fill,
            "pnl":        pnl,
            "risked":     risked,
        })
    return results


# ═══════════════════════════ STRATEGIE B: MOMENTUM DIRECTIONSL ═══════════════

def run_strategy_b(markets, klines) -> list[dict]:
    """
    Directionaler Momentum-Trade: Kaufe die Richtung wenn BTC > THRESH bewegt.
    """
    results = []
    no_signal = 0

    for m in markets:
        # Nur BTC-Preis VOR dem Markt benötigt (Outcome kommt aus Polymarket-Daten)
        btc_start = btc_price(klines, m["start"], "o")
        btc_prev  = btc_price(klines, m["start"] - datetime.timedelta(minutes=5), "c")

        if btc_start <= 0 or btc_prev <= 0:
            no_signal += 1
            continue

        momentum = (btc_start - btc_prev) / btc_prev

        if abs(momentum) < MOM_THRESH:
            no_signal += 1
            continue

        direction = "up" if momentum > 0 else "down"
        up_won    = m["up_won"]
        won       = (direction == "up" and up_won) or (direction == "down" and not up_won)

        entry  = MOM_ENTRY_ASK * (1 + SLIPPAGE)
        tokens = RISK_PER_SIDE / entry
        if won:
            pnl = tokens * (1 - WINNER_FEE) - RISK_PER_SIDE
        else:
            pnl = -RISK_PER_SIDE

        results.append({
            "start":     m["start"],
            "direction": direction,
            "up_won":    up_won,
            "won":       won,
            "momentum":  momentum * 100,   # In Prozent
            "pnl":       pnl,
            "risked":    RISK_PER_SIDE,
        })

    return results, no_signal


# ═══════════════════════════ AUSWERTUNG ══════════════════════════════════════

def print_metrics(label: str, results: list[dict], total_markets: int):
    if not results:
        print(f"  {label}: keine Trades")
        return

    n         = len(results)
    total_pnl = sum(r["pnl"] for r in results)
    total_ris = sum(r["risked"] for r in results)
    wins      = sum(1 for r in results if r["pnl"] > 0)
    losses    = sum(1 for r in results if r["pnl"] < 0)
    wr        = wins / n

    # Drawdown
    equity    = 0.0; peak = 0.0; max_dd = 0.0
    for r in sorted(results, key=lambda x: x["start"]):
        equity += r["pnl"]
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    # Tages-PnL
    daily = {}
    for r in results:
        day = r["start"].strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0.0) + r["pnl"]

    days_span      = max(1, len(daily))
    trades_per_day = n / days_span
    pnl_per_day    = total_pnl / days_span

    print(f"  ┌─ {label}")
    print(f"  │  Trades:        {n:4d} / {total_markets} Märkte ({n/total_markets*100:.0f}%)")
    print(f"  │  Win Rate:      {wr:.1%}  ({wins}W / {losses}L)")
    print(f"  │  Gesamt-PnL:   ${total_pnl:+.4f}  (Risk: ${total_ris:.0f})")
    print(f"  │  ROI:           {total_pnl/total_ris*100:+.2f}%")
    print(f"  │  Ø/Trade:      ${total_pnl/n:+.5f}")
    print(f"  │  Max Drawdown: ${max_dd:.4f}")
    print(f"  │  Trades/Tag:   {trades_per_day:.0f}")
    print(f"  │  PnL/Tag (ø): ${pnl_per_day:+.4f}")

    print(f"  │")
    print(f"  │  PnL nach Tag:")
    for day in sorted(daily):
        cnt = sum(1 for r in results if r["start"].strftime("%Y-%m-%d") == day)
        bar = ("▲" if daily[day] > 0 else "▼") * min(int(abs(daily[day]) * 5), 30)
        print(f"  │    {day}  ${daily[day]:+.4f}  ({cnt:3d}T)  {bar}")

    print(f"  └──────────────────────────────────────────────────────")
    print()


# ═══════════════════════════ MAIN ════════════════════════════════════════════

def main():
    bar = "═" * 65
    print(bar)
    print("  POLYMARKET BTC UP/DOWN — BACKTEST (letzte 3 Tage, $2 Risk)")
    print(bar)
    print()

    print("  Lade Daten...")
    klines  = load_kraken(DAYS_BACK)
    markets = load_polymarket(DAYS_BACK)
    print(f"  BTC 5min Klines:  {len(klines)} Bars")
    print(f"  Polymarket-Märkte: {len(markets)} aufgelöst (5min, vol>$1k)")
    if markets:
        print(f"  Zeitraum: {markets[0]['start'].strftime('%Y-%m-%d %H:%M')} – "
              f"{markets[-1]['start'].strftime('%Y-%m-%d %H:%M')} UTC")

    # Vorab: BTC-Markt-Statistik
    kraken_covered = 0
    for m in markets:
        if btc_price(klines, m["start"], "o") > 0:
            kraken_covered += 1
    print(f"  Mit Kraken-Daten: {kraken_covered}/{len(markets)} Märkte")

    up_wins = sum(1 for m in markets if m["up_won"])
    print(f"  Up gewann:        {up_wins}/{len(markets)} = {up_wins/len(markets)*100:.1f}%")
    print()

    print(bar)
    print("  STRATEGIE A: MARKET MAKING (beidseitig)")
    print(f"  Spread: {BASE_SPREAD*100:.0f}%  |  Bid={0.50-BASE_SPREAD/2:.2f}  Ask={0.50+BASE_SPREAD/2:.2f}")
    print(f"  (Mindest-Spread für pos. EV nach 2% Gewinner-Fee: 4%)")
    print(bar)
    print()

    for scenario, fp in FILL_PROB.items():
        both_prob = fp ** 2
        one_prob  = 2 * fp * (1 - fp)
        print(f"  Szenario: {scenario.upper():12s}  (P_fill={fp:.0%}/Seite → "
              f"P_beide={both_prob:.0%}, P_einseitig={one_prob:.0%})")
        res_a = run_strategy_a(markets, klines, fp)
        print_metrics(f"MM {scenario}", res_a, len(markets))

    print(bar)
    print("  STRATEGIE B: DIRECTIONALER MOMENTUM TRADE")
    print(f"  Signal-Schwelle: |BTC 5min Return| > {MOM_THRESH*100:.2f}%")
    print(f"  Entry: Ask = ${MOM_ENTRY_ASK}  |  Risk: ${RISK_PER_SIDE}/Trade")
    print(bar)
    print()

    res_b, no_signal = run_strategy_b(markets, klines)
    if isinstance(res_b, tuple):
        res_b, no_signal = res_b

    print(f"  Märkte ohne Signal (übersprungen): {no_signal}")
    print()
    print_metrics("Momentum Direktional", res_b, len(markets))

    # Win Rate & Expected Value ohne Momentum (Baseline)
    breakeven_wr = RISK_PER_SIDE / (RISK_PER_SIDE + (2/MOM_ENTRY_ASK*(1-WINNER_FEE) - RISK_PER_SIDE))
    print(f"  ── Baseline (50% WR): Ø/Trade = "
          f"${0.5*(2/MOM_ENTRY_ASK*(1-WINNER_FEE)-RISK_PER_SIDE) - 0.5*RISK_PER_SIDE:.5f}")
    print(f"  ── Breakeven Win Rate: {breakeven_wr:.1%}")

    if res_b:
        won_mom   = sum(1 for r in res_b if r["won"])
        wr_actual = won_mom / len(res_b)
        print(f"  ── Tatsächliche WR: {wr_actual:.1%}  (benötigt: >52% für pos. EV)")

    print()
    print(bar)
    print("  ANMERKUNGEN / EINSCHRÄNKUNGEN")
    print(bar)
    print("  ✓ Polymarket-Ergebnisse (up_won): echte aufgelöste Märkte")
    print("  ✓ BTC-Preise: Kraken 5min OHLC (live abgerufen)")
    print("  ✓ Gebühren: 2% Gewinner-Fee + 0.5% Slippage eingerechnet")
    print("  ! Strategie A Fill-Rate: modelliert (kein Live-Orderbuch)")
    print("  ! Strategie B: Signal aus 5min-Kerzen, kein Tick-Level")
    print("  ! Markt-Spread bei Öffnung: ~1% (unsere 4% = außerhalb best B/A)")
    print(bar)


if __name__ == "__main__":
    main()
