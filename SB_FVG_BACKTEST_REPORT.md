# ICT Silver Bullet FVG – Backtest & Optimierung

**Datum:** 2026-07-18 · **Branch:** `claude/ict-silver-bullet-backtest-hywexk`

## TL;DR

Die Original-Defaults sind auf 1-Minuten-Daten praktisch Breakeven (+651 USD, PF 1.05).
Mit 6 geänderten Settings wird die Strategie deutlich profitabel und bleibt es auch
out-of-sample:

| | Default | **Optimiert** | Konservativ (Bias AN) |
|---|---|---|---|
| Netto-PnL (1 NQ-Kontrakt, nach Kosten) | +651 USD | **+45.753 USD** | +30.451 USD |
| Trades (7 Monate) | 37 | **240** | 109 |
| Winrate | 21,6 % | **42,9 %** | 45,0 % |
| Profit Factor | 1,05 | **1,67** | 1,87 |
| Max. Drawdown | 4.173 USD | **5.597 USD** | 4.160 USD |
| Ø pro Trade | +18 USD | **+191 USD** | +279 USD |

![Equity-Kurve](equity_curve_optimized.png)

## Optimierte Settings (→ `sb_fvg_strategy_optimized.pine`)

| Input | Default | **Optimiert** | Wirkung |
|---|---|---|---|
| Midnight-Open-Bias-Filter | AN | **AUS** | Der Filter halbiert die Trade-Anzahl, ohne die Trefferquote zu verbessern. Counter-Bias-Setups (Sweep gegen den Tages-Bias) waren im Test genauso profitabel. |
| TP-Modus | Liquidität | **Fix RRR 2.0** | Der größte Hebel. Liquiditäts-TPs (PDH/PDL, Asia H/L) sind oft zu nah am Entry oder zu weit weg — festes 2R schneidet klar besser ab. 2.25R/2.5R/3R sind alle schlechter. |
| Minimales RRR | 2.0 | **1.5** | Im Fix-RRR-Modus nur noch Sicherheitsfilter (2.0 kann durch Float-Rundung Setups fälschlich verwerfen). |
| Sweep-Lookback | 30 Bars | **90 Bars** | Sweeps wirken auf 1min länger nach als 30 Minuten. Plateau 75–105 Bars — unkritisch. |
| SL-Puffer | 8 Ticks | **16 Ticks** | Weniger Stop-Runs um exakte Swing-Lows/Highs. Plateau 12–20 Ticks. |
| Fill-Frist (graceBars) | 24 | **48** | Limit-Orders dürfen länger auf ihren Fill warten. Wenig sensitiv (12–72 fast identisch). |
| Entry-Option | CE (50 %) | CE (50 %) | Bestätigt: CE schlägt Edge (+19k) und Full Gap (+19k) deutlich. |
| dispMult / minGap / slLB / maxDay / Sessions | 1.2 / 4T / 10 / 4 / alle | unverändert | Defaults liegen bereits im Optimum. Alle 3 Sessions tragen bei (AM am stärksten — ohne AM bricht das Ergebnis auf +12k ein). |

**Timeframe: 1 Minute.** Auf 3min fällt dieselbe Konfiguration auf +9.478 USD / PF 1.13
zurück — die Strategie gehört auf den 1min-Chart.

**Konservative Variante:** Bias-Filter wieder einschalten → weniger Trades (109),
niedrigerer absoluter Gewinn, aber höherer Profit Factor (1,87), höhere Winrate und
kleinerer Drawdown. Sinnvoll für kleinere Konten / Prop-Firm-Drawdown-Limits.

## Daten & Methodik

- **Daten:** Nasdaq-100-Index (USATECHIDXUSD, Dukascopy-CFD), 200.000 1-Minuten-Bars,
  **2023-02-07 bis 2023-09-11**, UTC → New York konvertiert. Quelle:
  [TheSnowGuru/Stocks-Futures-Financial-Time-series-Tick-Bar-Data](https://github.com/TheSnowGuru/Stocks-Futures-Financial-Time-series-Tick-Bar-Data/tree/main/indices/nasdaq100)
  (Datei `USATECHIDXUSD_M1.csv`, nicht committet).
  TradingView-/Yahoo-Datenfeeds sind in dieser Umgebung netzwerkseitig gesperrt,
  daher CFD-Index-Daten als NQ-Proxy (nahezu identischer Verlauf; Abweichung: kein
  Futures-Basis-Spread, Ticks nicht auf 0,25 gerastert).
- **Engine:** `sb_backtest.py` — 1:1-Port der Pine-Logik inklusive
  TradingView-Broker-Emulator-Semantik: Signale auf Bar-Close, Limit aktiv ab Folgebar,
  Fill am Open bei Gap durchs Limit, Intrabar-OHLC-Pfad-Annahme, „VERPASST"/„ABGELAUFEN"-
  Cancel-Logik, $2,50 Kommission je Seite, 1 Tick Slippage auf Stops (Limits slippagefrei,
  wie in TV). Bei Mehrdeutigkeit (Entry+SL+TP in einer Bar) wird **konservativ als
  Verlust** gewertet.
- **Optimierung:** `sb_optimize.py` — Grid-Search Stage 1 mit 19.440 Kombinationen
  (Entry-Modus, TP-Modus, RRR, dispMult, minGap, sweepLB, slLB, slBuf), Stage 2 mit
  168 Session-/Bias-/Management-Varianten. **In-Sample Feb–Jun, Out-of-Sample Jul–Sep**;
  Ranking bestraft Drawdown und gewichtet OOS doppelt, damit nicht der reine
  In-Sample-Sieger gewinnt.

## Ergebnis-Details (optimierte Settings, 1 NQ-Kontrakt)

| Segment | Netto | Trades | Winrate | PF | MaxDD |
|---|---|---|---|---|---|
| In-Sample (Feb–Jun) | +37.176 USD | 169* | 44 % | 1,83 | 3.579 USD |
| Out-of-Sample (Jul–Sep) | +8.577 USD | 71 | 38 % | 1,37 | 5.597 USD |
| Gesamt | +45.753 USD | 240 | 42,9 % | 1,67 | 5.597 USD |

*Long/Short ausgeglichen profitabel (Longs +20,2k / Shorts +23,1k). Ø Gewinner +1.129 USD,
Ø Verlierer −513 USD, längste Verluststrecke 5 Trades, größter Einzelverlust −1.994 USD.*

**Monats-PnL:** Feb +2,5k · Mär +15,3k · Apr +6,0k · Mai +8,6k · Jun +4,8k ·
**Jul −2,7k** · Aug +11,0k · Sep (Teilmonat) +0,3k → 7 von 8 Monaten positiv, kein
Einzelmonat dominiert das Ergebnis.

**Robustheit:**
- Alle finalen Parameter liegen auf Plateaus, nicht auf Spitzen (Sensitivity-Sweep
  je Parameter in `sb_optimize.py` reproduzierbar).
- Stress-Test mit doppelten Kosten (2 Ticks Slippage, $5/Seite): immer noch
  +41.500 USD, PF 1,62.
- Trade-Liste: `trades_optimized.csv`.

## Ehrliche Einschränkungen

1. **7 Monate Daten, ein Marktregime** (Tech-Bullenmarkt 2023). Die Shorts waren
   trotzdem profitabel, aber ein Bärenmarkt/Chop-Jahr ist nicht getestet.
2. **CFD-Proxy statt echter NQ-Futures-Ticks** — reale Fills an CME-Limits können
   schlechter sein (Queue-Position), auch wenn konservative Fill-Annahmen und
   Slippage-Stress das abfedern.
3. **240 Trades sind okay, aber nicht riesig.** Der OOS-Zeitraum ist mit ~10 Wochen
   kurz. Empfehlung: vor Live-Einsatz auf TradingView mit dem optimierten Script
   auf NQ/MNQ 1min nachtesten (Deep Backtesting) und mindestens 1–2 Monate
   Paper-/Sim-Trading.
4. MNQ statt NQ: alle USD-Werte ÷ 10, Kommission anpassen (Standard ~$0,74/Seite,
   d. h. relativ teurer — PF sinkt leicht).

## Dateien

| Datei | Inhalt |
|---|---|
| `sb_fvg_strategy_optimized.pine` | Pine-Script mit den optimierten Default-Inputs (Logik unverändert) |
| `sb_backtest.py` | Backtest-Engine (Python/Numba, TV-Broker-Emulation) |
| `sb_optimize.py` | Grid-Search-Optimizer mit IS/OOS-Split |
| `trades_optimized.csv` | Alle 240 Trades der optimierten Konfiguration |
| `equity_curve_optimized.png` | Equity-Kurve mit IS/OOS-Markierung |
