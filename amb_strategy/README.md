# AMB — Adaptive Momentum Breakout (optimiert)

Trend-Following-Strategie für BTC/Krypto auf 1H-Basis, entwickelt und in
**4 Stufen optimiert** (Grobraster → Feinraster → Walk-Forward → Validierung
auf 10 ungesehenen Marktpfaden).

## Die Strategie

| Baustein | Regel |
|---|---|
| **Entry (Long)** | Close bricht über das 24-Bar-Donchian-Hoch UND Close > EMA(100) |
| **Volatilitätsfilter** | ATR-Perzentil-Rang (200 Bars) muss zwischen 0.15 und 0.95 liegen — handelt weder tote noch panische Märkte |
| **Initial Stop** | 3.0 × ATR(14) unter Entry |
| **Trailing Stop** | Chandelier: Höchstkurs seit Entry − 5.0 × ATR (nur ratschend) |
| **Channel Exit** | Close unter das 72-Bar-Donchian-Tief |
| **Position Sizing** | 1% des Kapitals riskiert pro Trade (Stop-Distanz-basiert) |
| **Shorts** | AUS — verschlechterten in jeder Optimierungsstufe die Ergebnisse |
| **Kosten** | 0.05% Fee + 0.02% Slippage pro Seite eingerechnet |

## Finale optimierte Settings

```
entry_len  = 24      (Donchian Breakout Lookback)
exit_len   = 72      (Exit-Channel Lookback)
trail_mult = 5.0     (Chandelier ATR-Multiplikator)
sl_mult    = 3.0     (Initial Stop ATR-Multiplikator)
ema_len    = 100     (Trendfilter)
vol_filter = an      (Band 0.15 – 0.95)
long_only  = ja
risk       = 1% pro Trade
```

## Ergebnisse der Validierung (10 ungesehene Marktpfade, je ~3.4 Jahre)

Diese Pfade wurden **nie zur Optimierung benutzt** — das sind echte
Out-of-Sample-Zahlen, keine In-Sample-Schönfärberei:

| Metrik (Median) | Strategie | Buy & Hold |
|---|---|---|
| Gesamtrendite | **+43.7%** | +25.8% |
| CAGR | **+11.2%** | — |
| Sharpe Ratio | **0.70** | ~0.4 |
| Max Drawdown | **−22.2%** | −50% bis −85% |
| Profit Factor | 1.20 | — |
| Win Rate | 32.9% | — |
| Schlechtester Pfad | **−22.5%** | **−73.9%** |
| Profitable Pfade | 8 / 10 | 5 / 10 |

Charakter: klassisches Trend Following — niedrige Trefferquote, große
Gewinner (Ø +$234 vs. Ø −$97), massiv besserer Kapitalschutz als Buy & Hold
in Bärenmärkten.

## Wie optimiert wurde (Anti-Overfitting-Design)

1. **Stufe 1 — Grobraster:** 540 Kombinationen × 5 unabhängige Marktpfade,
   Bewertung per Median (nicht Bestwert!) eines robusten Scores
   (Sharpe, bestraft für dünne Trade-Samples und tiefe Drawdowns).
2. **Stufe 2 — Feinraster:** 243 Kombinationen um den Gewinner
   (inkl. Stop-Multiplikator und Verlust-Cooldown).
3. **Stufe 3 — Walk-Forward:** 30 rollierende Train(8000)/Test(4000)-Folds
   über 6 Pfade. Finale Parameterwahl: bester **Median-Score über alle
   Test-Folds** — belohnt Konsistenz statt Glückstreffer.
4. **Stufe 4 — Validierung:** Finale Settings auf 10 komplett frischen,
   nie zuvor benutzten Marktpfaden.

**Warum das wichtig ist:** Iteration 1 fand in-sample ein "besseres" Setting
(Score 1.99), das out-of-sample auf Sharpe 0.44 kollabierte (Overfitting auf
kurze Lookbacks mit 500+ Trades). Das finale Setting liegt auf einem breiten
Parameter-Plateau — benachbarte Settings performen ähnlich, das Ergebnis
hängt nicht an einem Zufallstreffer.

Getestet, aber verworfen:
- **Shorts** — verloren auf allen Pfaden konsistent Geld (fette rechte Tails)
- **Kurze Breakouts (12–24 ohne Vol-Filter)** — Overfitting, Kosten fressen den Edge
- **3-Längen-Ensemble (24/96/336)** — senkt den Drawdown auf −17%, aber gleiche
  Sharpe bei weniger Rendite; als konservative Variante dokumentiert

## Dateien

| Datei | Inhalt |
|---|---|
| `amb_strategy.pine` | **TradingView Pine v6** mit den optimierten Defaults |
| `strategy.py` | Backtest-Engine (Event-basiert, Intrabar-Stops, Kosten) |
| `optimize.py` | 4-stufige Optimierungspipeline |
| `data.py` | Marktdaten-Simulator (Regime-Switching + GARCH + Fat Tails) |
| `results.json` | Vollständige Ergebnisse aller Stufen |

## Selbst ausführen

```bash
pip install pandas numpy
python3 optimize.py        # komplette Pipeline (~5 min)
python3 strategy.py        # Einzel-Backtest mit Default-Settings
```

## ⚠️ Ehrliche Einordnung — bitte lesen

1. **Die Backtests laufen auf simulierten Daten** (kalibriert auf
   BTC-Statistik: ~55–60% Jahresvolatilität, Volatilitäts-Clustering, fette
   Tails, Regime-Wechsel), weil diese Umgebung keine Marktdaten-APIs
   erreicht. Die Simulation enthält echte Trend-Persistenz — reale Märkte
   können sich anders verhalten. **Vor echtem Einsatz zwingend das Pine
   Script auf echten TradingView-Daten (BTCUSD 1H, mehrere Jahre)
   validieren.**
2. **"Hochprofitabel ohne Risiko" existiert nicht.** Realistisch erreichbar
   mit einer robusten Einzelstrategie: Sharpe ~0.7, zweistellige CAGR,
   Drawdowns um −20%. Backtests, die +500% ohne nennenswerten Drawdown
   zeigen, sind praktisch immer überangepasst — genau deshalb wurde hier
   auf ungesehenen Daten validiert und der In-Sample-Traumwert bewusst
   verworfen.
3. In 2 von 10 simulierten Marktverläufen (lange Bärenmärkte) verliert auch
   das optimierte Setting leicht (−4% bis −7% p.a.). Kein Trendfolger
   gewinnt in jedem Regime.
4. Erst **Paper Trading (4+ Wochen)**, dann kleine Größe.
5. Vergangene/simulierte Performance garantiert keine zukünftigen Ergebnisse.
