# MGC1 Multi-Factor Gold Trading Strategy

## TradingView Setup

### 1. Symbol auswählen
- Öffne TradingView und suche nach **MGC1!** (Micro Gold Futures Continuous Contract)
- Alternativ: **MGCM2025** (spezifischer Kontrakt)
- Empfohlener Timeframe: **15min** oder **1H**

### 2. Strategy laden
1. TradingView öffnen → **Pine Editor** (unten)
2. Inhalt von `mgc1_strategy.pine` komplett einfügen
3. **"Add to Chart"** klicken
4. Die Strategie erscheint auf dem Chart mit Signalen und Info-Tabelle

### 3. Backtesting
- Tab **"Strategy Tester"** unten öffnen
- Dort siehst du: Net Profit, Max Drawdown, Win Rate, Profit Factor
- Unter **"List of Trades"** jeden einzelnen Trade analysieren

### 4. Parameter optimieren
- Rechtsklick auf Strategy Name → **Settings**
- Alle Parameter sind gruppiert und einstellbar
- TradingView "Optimize" Feature nutzen für automatische Optimierung

## Strategie-Übersicht

### Entry-Bedingungen (Long)
| Filter | Bedingung |
|--------|-----------|
| Trend | Preis über 100 EMA |
| Momentum | Fast EMA (9) kreuzt über Slow EMA (21) |
| RSI | Zwischen 30-70 (nicht überkauft/überverkauft) |
| MACD | Histogram positiv oder steigend |
| Volume | Über Durchschnitt |
| Session | Nur während aktiver Handelszeit (8-17 ET) |
| Candle | Schlusskurs über Eröffnung (Bestätigung) |

Short-Entries sind gespiegelt.

### Risk Management
| Feature | Standard-Wert |
|---------|---------------|
| Stop Loss | 2.0x ATR |
| Take Profit | 3.0x ATR (R:R = 1:1.5) |
| Trailing Stop | 1.5x ATR |
| Max Trades/Tag | 3 |
| Max Tagesverlust | $200 |
| Cooldown nach Verlust | 5 Bars |
| Session-Ende Exit | Automatisch |
| Pyramiding | Deaktiviert |

### Drawdown-Kontrolle
Die Strategie minimiert Drawdown durch:
1. **Multi-Filter-Ansatz** - Nur Trades mit mehrfacher Bestätigung
2. **Session-Filter** - Kein Trading in illiquiden Zeiten
3. **Tägliches Verlustlimit** - Stoppt nach $200 Tagesverlust
4. **Cooldown** - Wartezeit nach Verlust-Trades
5. **Trailing Stop** - Sichert laufende Gewinne
6. **Keine Pyramidierung** - Maximal 1 Position gleichzeitig

## Empfohlene Timeframes

| Timeframe | Stil | Anmerkung |
|-----------|------|-----------|
| 5min | Scalping | Mehr Trades, kleinere Moves |
| 15min | Intraday | **Empfohlen** - gute Balance |
| 1H | Swing-Intraday | Weniger Trades, größere Moves |
| 4H | Swing | Session-Filter ggf. deaktivieren |

## Wichtige Hinweise

- **Paper Trading zuerst** - Teste die Strategie mindestens 2-4 Wochen im Paper-Modus
- **Kontraktspezifikation**: MGC = $10 pro Punkt, Tick Size = 0.10 ($1.00/Tick)
- **Margin**: ~$1,000 pro Kontrakt (variiert je nach Broker)
- **Commission**: Strategie rechnet mit $1.25 pro Seite (anpassbar in Settings)
- **Vergangene Performance ist keine Garantie für zukünftige Ergebnisse**
