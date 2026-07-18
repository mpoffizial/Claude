# Nasdaq ORB — Opening Range Breakout (täglich handelbar)

Intraday-Strategie für **NQ/MNQ-Futures** (oder QQQ) auf 5-Minuten-Basis,
gebaut für tägliches Trading: handelt an ~75% der Tage, ist **jeden Abend
flat** (kein Overnight-Risiko) und wurde mit derselben 4-stufigen
Anti-Overfitting-Pipeline optimiert wie `amb_strategy/`.

## Die Strategie

| Baustein | Regel (optimiert) |
|---|---|
| **Opening Range** | High/Low der ersten **60 Minuten** (9:30–10:30 ET) |
| **Entry (Long)** | 5-Min-Close bricht über OR-Hoch + **0.1× OR-Breite** Buffer |
| **Entry-Cutoff** | Keine neuen Entries nach **15:00 ET** |
| **Stop** | **0.5× OR-Breite** unter Entry |
| **Breakeven** | Nach +1R wird der Stop auf Entry gezogen |
| **Target** | Keins — Gewinner laufen bis **Handelsschluss** (EOD flat) |
| **Trades/Tag** | Max. 2 |
| **Shorts** | AUS — verschlechterten die Ergebnisse in allen Stufen |
| **Daily-EMA-Filter** | AUS — kostete mehr Rendite als er Schutz brachte |
| **Sizing** | 1% des Kapitals pro Trade (OR-Breiten-basiert) |
| **Kosten** | MNQ-Kommission + 1 Tick Slippage pro Seite |

Charakter: niedrige Trefferquote (~31%), viele kleine Breakeven-/Stop-Exits,
Gewinn kommt aus den **Trend-Tagen**, an denen die Position bis zum Schluss
durchläuft.

## Validierung (10 ungesehene Marktpfade à ~4 Jahre, 25.000 $ Startkapital)

| Metrik (Median) | Wert | Spanne über 10 Pfade |
|---|---|---|
| CAGR | **+65.6%** | +29% bis +133% |
| Sharpe Ratio | **1.92** | 1.13 – 2.99 |
| Max Drawdown | **−18.8%** | −12.5% bis −33.1% |
| Profit Factor | 1.49 | 1.22 – 1.84 |
| Profitable Monate | **68%** | 57% – 74% |
| Profitable Pfade | **10 / 10** | auch wenn Buy & Hold negativ war |
| Trades pro Tag | ~0.75 | handelt an ~54–75% der Tage |
| Längste Verlustserie | 10–17 Handelstage | |

## ⚠️ Der eine Befund, den du kennen MUSST

Ich habe getestet, wovon der Edge abhängt, indem ich die
Intraday-Momentum-Stärke im Simulator variiert habe:

| Trend-Tage-Annahme | Sharpe | CAGR | Profit Factor |
|---|---|---|---|
| Basis (18% der Tage, 1.2σ) | 2.08 | +72% | 1.52 |
| Abgeschwächt (10%, 0.8σ) | 0.27 | +4% | 1.03 |
| Ohne Trend-Tage | −0.02 | −3% | 0.97 |

**Der gesamte Edge kommt aus Intraday-Momentum** (Eröffnungsbewegungen, die
bis zum Schluss weiterlaufen). Dieses Phänomen ist auf echten Index-Daten
wissenschaftlich dokumentiert (Gao/Han/Li/Zhou 2018 "Intraday Momentum";
Zarattini/Aziz 2023 zu QQQ-ORB), aber **seine tatsächliche Stärke entscheidet
über Gewinn oder Breakeven** — und die kann nur ein Backtest auf echten
Daten zeigen. Deshalb: bevor auch nur ein Euro riskiert wird, das
mitgelieferte Pine Script auf **NQ1!/MNQ1! 5-Min, mehrere Jahre** in
TradingView laufen lassen und mit diesen Zahlen vergleichen.

## Als Einkommensquelle? Die ehrliche Rechnung

Bei 25.000 $ Konto und 1% Risiko pro Trade (Median-Pfad, **ohne** Entnahmen,
mit Zinseszins) lag der Durchschnittsmonat bei ~3.400 $. ABER:

1. **Es ist kein Gehalt.** Nur ~68% der Monate sind positiv — d.h. **jeder
   dritte Monat endet im Minus**, Verlustserien von 10–17 Handelstagen
   kamen auf jedem Pfad vor. Wer monatlich Geld entnehmen *muss*, wird
   in Drawdowns gezwungen, genau dann aufzuhören, wenn es am teuersten ist.
2. **Entnahmen töten den Zinseszins.** Die +65% CAGR entstehen durch
   Reinvestition. Wer den Gewinn monatlich abzieht, bekommt deutlich weniger.
3. **Simulierte Daten.** Siehe oben — die reale Momentum-Stärke ist die
   große Unbekannte. Plane mit dem abgeschwächten Szenario als
   Möglichkeit, nicht mit dem Median.
4. **Praktisch:** MNQ-Micro-Futures (2 $/Punkt, keine PDT-Regel, ~2.000 $
   Intraday-Margin) sind für diese Kontogröße das richtige Instrument —
   nicht NQ (20 $/Punkt, zu groß für 1%-Risiko auf 25k).
5. **Regel:** Erst 4+ Wochen Paper-Trading, dann 1 MNQ, erst nach 3
   profitablen Monaten skalieren. Niemals Lebenshaltungskosten vom
   Trading-Konto abhängig machen.

## Wie optimiert wurde

1. **Grobraster:** 216 Kombinationen (OR-Länge, Stop, Target, EMA-Filter,
   Shorts) × 5 unabhängige Marktpfade, Median-Score
2. **Feinraster:** 324 Kombinationen (+ Buffer, Breakeven, Cutoff)
3. **Walk-Forward:** 18 Train(400 Tage)/Test(200 Tage)-Folds über 6 Pfade,
   Finalwahl = bester Median über alle Test-Folds
4. **Validierung:** 10 nie zur Optimierung benutzte Pfade

Konsistentes Muster über alle Stufen: lange Opening Range (45–60 Min),
enger Stop (0.5× OR), **kein** Profit-Target (EOD laufen lassen), Breakeven
nach +1R, long-only. Kurze ORs (15 Min) und feste R-Targets verloren
durchweg.

## Dateien & Ausführen

| Datei | Inhalt |
|---|---|
| `orb_strategy.pine` | TradingView Pine v6, optimierte Defaults (NQ/MNQ 5-Min) |
| `strategy_orb.py` | Backtest-Engine (Intrabar-Stops, EOD-Flat, Kosten) |
| `optimize_orb.py` | 4-stufige Optimierungspipeline |
| `data_nq.py` | Nasdaq-RTH-Simulator (Gaps, U-Vol, Regime, Trend-Tage) |
| `results_orb.json` | Vollständige Ergebnisse |

```bash
pip install pandas numpy
python3 optimize_orb.py    # komplette Pipeline (~2 min)
python3 strategy_orb.py    # Einzel-Backtest
```

Simulierte/vergangene Performance ist keine Garantie für zukünftige
Ergebnisse. Futures-Trading kann zum Totalverlust führen.
