# Quant-Portfolio — konstanter Profit, minimaler Drawdown (ehrlich gebaut)

Es gibt keine Einzelstrategie mit "konstantem Profit und minimalem
Drawdown". Was es gibt, ist die institutionelle Standard-Lösung:
**mehrere unkorrelierte Strategien + Risk Parity + Volatilitäts-Targeting
+ Drawdown-Bremse**. Dieses Modul kombiniert die drei zuvor einzeln
optimierten und validierten Strategien dieses Repos.

## Bausteine

| # | Strategie | Markt / TF | Charakter |
|---|-----------|-----------|-----------|
| 1 | **AMB** (`amb_strategy/`) | Krypto, 1H | Swing-Trendfolge, long-only |
| 2 | **NY ORB** (`nasdaq_orb/orb_strategy.pine`) | Nasdaq, 5min RTH | Intraday, long-only, EOD flat |
| 3 | **London ORB** (`nasdaq_orb/london_orb.pine`) | EU-Index, 1min | Intraday, L+S, EOD flat |

Drei verschiedene Märkte, drei verschiedene Sessions, zwei Zeithorizonte —
die Verluste fallen selten gleichzeitig an. Genau daraus entsteht die
Glättung.

## Konstruktion

1. **Risk Parity:** Kapital ∝ 1/Volatilität der Strategie.
   Median-Gewichte: **~55% AMB / ~29% NY ORB / ~16% London ORB**
   (die volatilste Strategie bekommt am wenigsten Kapital)
2. **Vol-Targeting:** Gesamtexposure täglich so skaliert, dass die
   realisierte 60-Tage-Volatilität ~10% p.a. trifft (Faktor 0.3–1.5)
3. **Drawdown-Bremse:** Unter −10% vom Hoch wird das Exposure halbiert,
   ab −5% wieder normal

## Ergebnisse (6 ungesehene Seed-Sets, je ~3 Jahre, Median)

| | Sharpe | CAGR | Max DD | Win-Monate | Schlechtester Monat |
|---|---|---|---|---|---|
| AMB allein | 0.31 | +3.4% | −20.2% | 46% | −7.0% |
| NY ORB allein | 1.45 | +43.7% | −20.2% | 63% | −11.7% |
| London ORB allein | 2.00 | +133.3% | −35.1% | 67% | −15.9% |
| Portfolio (Risk Parity) | 2.40 | +37.5% | −10.0% | 67% | −5.4% |
| **Portfolio (final)** | **2.27** | **+26.4%** | **−8.1%** | **66%** | **−4.2%** |

Über alle 6 Sets lag der schlechteste Portfolio-Drawdown bei −9.4%, der
schlechteste Einzelmonat bei −5.3%. Kein Set war negativ (Sharpe 1.10–2.61).

**Das ist der Kern:** Die Rendite ist niedriger als die der besten
Einzelstrategie — dafür ist der maximale Verlust nur ein Viertel so groß.
Wer "minimalen Drawdown" will, bezahlt mit Rendite-Spitze, nicht mit
Rendite-Substanz (risikoadjustiert ist das Portfolio allen Einzelnen
überlegen).

## Praktische Umsetzung (25.000 $ Beispiel)

TradingView kann kein Multi-Asset-Portfolio in einem Script — du führst
die drei Strategien getrennt und teilst das Kapital:

| Slice | Kapital | Instrument | Risiko/Trade |
|---|---|---|---|
| AMB | ~13.750 $ | BTC (Spot/Perp, 1H) | 1% des Slices |
| NY ORB | ~7.250 $ | MNQ (5min) | 1% des Slices |
| London ORB | ~4.000 $ | DE40-CFD (1min) | 1% des Slices |

Regeln:
- Jede Strategie riskiert nur % **ihres eigenen Slices**, nie des Gesamtkontos
- **Monatlich rebalancen** (Gewinne/Verluste zurück auf Zielgewichte)
- **Bremse manuell:** Gesamtkonto −10% vom Hoch → alle Positionsgrößen
  halbieren, bis −5% wieder erreicht ist
- **Not-Aus je Strategie:** übersteigt eine Strategie das 1.5-fache ihres
  Backtest-Drawdowns, wird sie gestoppt (der Edge ist dann mutmaßlich weg)

## Ehrliche Einschränkungen

1. **Korrelation:** In der Simulation sind die drei Märkte unabhängig.
   Real korrelieren BTC, Nasdaq und DAX positiv (0.3–0.6) — der
   Diversifikationsgewinn wird live kleiner sein als hier. Die Richtung
   stimmt trotzdem: weniger DD als jede Einzelstrategie.
2. **Jeder Baustein hat seine eigene Annahme** (Intraday-Momentum bei den
   ORBs, Trend-Persistenz bei AMB) — alle drei sind in den einzelnen
   READMEs mit Sensitivitätstests dokumentiert. Vor Live-Einsatz jede
   Komponente einzeln auf echten Daten in TradingView validieren.
3. **"Konstant" heißt hier:** ~2 von 3 Monaten positiv, schlechtester
   Monat ~−4%, Durststrecken von mehreren Wochen inklusive. Garantien
   gibt es nicht, und simulierte Ergebnisse überschätzen systematisch.

## Ausführen

```bash
cd quant_portfolio
python3 portfolio.py     # kombiniert alle drei Backtests (~1 min)
```
