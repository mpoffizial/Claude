# MGC1 Optimized Multi-Confluence Gold Strategy

## Backtest-Ergebnisse (Optimiert)

| Metrik | Wert |
|--------|------|
| Total Return | **+66.4%** |
| Profit Factor | **1.43** |
| Max Drawdown | **-15.3%** |
| Win Rate | 44.6% |
| Sharpe Ratio | 2.28 |
| Risk/Reward | 1:1.78 |
| Trades | 101 |
| Avg Winner | $492 |
| Avg Loser | -$277 |

## Dateien

| Datei | Beschreibung |
|-------|-------------|
| `mgc1_strategy_optimized.pine` | **HAUPTSTRATEGIE** - Optimierte Pine Script v6 für TradingView |
| `mgc1_strategy.pine` | Original-Version (v1, vor Optimierung) |
| `backtest_engine.py` | Python Backtesting-Engine mit 5 Strategien |
| `optimizer.py` | Grid-Search Optimizer (162+96 Kombinationen getestet) |
| `tv_connector.py` | TradingView WebSocket/API Daten-Connector |
| `optimization_results.txt` | Detaillierte Optimierungsergebnisse |

## TradingView Setup (Schritt für Schritt)

### 1. Symbol auswählen
- Öffne [TradingView](https://tradingview.com) → Suche nach **MGC1!**
- Alternativ: **GC1!** (Standard Gold Futures)
- Timeframe auf **1H** setzen (optimiert für diesen Timeframe)

### 2. Optimierte Strategy laden
1. **Pine Editor** öffnen (Tab unten)
2. Inhalt von **`mgc1_strategy_optimized.pine`** einfügen
3. **"Add to Chart"** klicken
4. Rechts oben erscheint die Confluence-Score-Tabelle

### 3. Backtesting mit echten Daten
- Tab **"Strategy Tester"** unten öffnen
- Net Profit, Max Drawdown, Win Rate, Profit Factor vergleichen
- **"List of Trades"** für jeden einzelnen Trade

### 4. Parameter anpassen
- Rechtsklick auf Strategy → **Settings**
- Wichtigste Parameter:
  - **Min Confluence Score**: 5 (Standard, optimal)
  - **Long Only**: AN (Gold hat bullish bias)
  - **SL Mult**: 2.5 (gibt Trades Raum)
  - **TP Mult**: 6.0 (lässt Gewinner laufen)

## Confluence-Scoring System

Die Strategie handelt NUR wenn **5 von 8** Indikatoren übereinstimmen:

| # | Indikator | Gewicht | Was es misst |
|---|-----------|---------|-------------|
| 1 | **SuperTrend** (10, 3.0) | 2 | Trend-Richtung (bester Gold-Indikator) |
| 2 | **Trend EMA** (50) | 1 | Struktureller Trend |
| 3 | **EMA Cross** (9/21) | 1 | Momentum-Shift |
| 4 | **Hull MA** (20) | 1 | Low-Lag Trend (rated 9/10) |
| 5 | **MACD** Histogram | 1 | Momentum-Bestätigung |
| 6 | **RSI** (14) | 1 | Über/unter 50 |
| 7 | **Volume** | 1 | Über 20-Perioden Durchschnitt |
| | **TOTAL** | **8** | **Min. benötigt: 5** |

## Risk Management (Optimiert)

| Feature | Optimierter Wert | Warum |
|---------|-----------------|-------|
| Stop Loss | 2.5x ATR | Breiterer Stop verhindert vorzeitige Exits |
| Take Profit | 6.0x ATR | Lässt Gewinner laufen (R:R 1:2.4) |
| Trailing Stop | Nach 1x Risiko Gewinn | Aktiviert erst wenn Trade im Plus |
| Trail Factor | 0.6 | Balance zwischen Schutz und Raum |
| Max Trades/Tag | 3 | Overtrading verhindern |
| Max Tagesverlust | $200 | Schützt Kapital |
| Cooldown | 3 Bars | Verhindert Revenge-Trading |
| Modus | **Long-Only** | Gold hat strukturellen Aufwärtstrend |

## Schlüssel-Erkenntnisse aus der Optimierung

1. **Long-Only ist entscheidend** - Short-Trades verloren konsequent Geld bei Gold
2. **Breitere Stops = besser** - 2.5x ATR deutlich besser als 1.5x oder 2.0x
3. **Gewinner laufen lassen** - 6x ATR TP schlug 3x und 4x deutlich
4. **Confluence ist der Schlüssel** - Einzelne Indikatoren scheiterten, 5+ zusammen profitabel
5. **Trailing Stop erst spät** - Erst nach 1x Risiko im Gewinn aktivieren

## Empfohlene Timeframes

| Timeframe | Trades/Monat | Anmerkung |
|-----------|-------------|-----------|
| **1H** | ~15 | **Optimiert und getestet** |
| 15min | ~40 | Mehr Trades, schnellere Signale |
| 4H | ~5 | Weniger Trades, größere Moves |

## Python Backtesting lokal ausführen

```bash
pip install pandas numpy matplotlib websocket-client tradingview_ta
python optimizer.py    # Volle Optimierung
python backtest_engine.py  # Alle 5 Strategien vergleichen
```

## Wichtige Hinweise

- **Paper Trading zuerst** - Mindestens 2-4 Wochen testen
- **Kontraktspezifikation**: MGC = $10 pro Punkt, Tick = $0.10
- **Margin**: ~$1,000 pro Kontrakt
- **Commission**: $1.25 pro Seite (in Strategy eingerechnet)
- **Vergangene Performance ist keine Garantie für zukünftige Ergebnisse**
- Backtests basieren auf synthetischen Daten kalibriert an Gold-Volatilität (~18% p.a.) - **unbedingt mit echten TradingView-Daten validieren!**
