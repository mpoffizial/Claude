# BTC / Gold ATR-Breakout – Backtest

Python-Nachbau von `BTC_Gold_Breakout.pine`, Kerze für Kerze auf 1h-Daten,
mit der TradingView-Brokeremulation, die für dieses Skript relevant ist
(Fill zum nächsten Open, Slippage in Ticks, Provision je Ausführung,
Stop schon in der Einstiegsstunde, 2 % Margin mit Margin Call, Konto in CHF).

## Zwei Versionen

| Datei | Risiko je Trade | Hebel | Zweck |
|---|---|---|---|
| `BTC_Gold_Breakout.pine` | 60 % BTC / 40 % Gold | bis 40x / 16x | Original-Forschungsportierung |
| `BTC_Gold_Breakout_realistisch.pine` | 5 % | max. 5x | gleiche Regeln, realistisches Risiko und Kosten |

Portfolio 70/30 mit Quartals-Umverteilung, 18.10.2021 – 18.10.2026, 500 CHF,
echte Daten, realistische Kosten (inkl. BTC-Funding und 7 % Gold-Finanzierung):
5 % Risiko (`--realistic`) 1'620 CHF, Max. DD −10 %;
Original-Risiko (`--realistic-costs`) 313'376 CHF, Max. DD −72 %, tiefster Stand 144 CHF.

## Ausführen

```bash
pip install pandas numpy
# lädt BTCUSDT-Perpetual (Binance), XAUUSD und USD/CHF (Dukascopy) nach backtest_data/
python atr_breakout/backtest.py --download
```

Standard: alle drei Profile, je 500 CHF, Test 18.10.2021 – 18.10.2026,
Kurshistorie ab 01.01.2020 für das Warm-up von ATR/EMA/SMA.

Wichtige Optionen:

| Option | Bedeutung |
|---|---|
| `--profile btc\|btc-latest\|gold` | nur ein Profil |
| `--capital 350` | Startkapital CHF |
| `--start / --end` | Testfenster (Ende exklusiv) |
| `--realistic` | Einstellungen der realistischen Pine-Version (5 % Risiko, max. 5x, Kosten inkl. Slippage, Gold 0.01 Unzen, Funding) |
| `--realistic-costs` | nur realistische Kosten (Provision inkl. 0.02 % Slippage, BTC-Funding, Gold-Finanzierung, Gold 0.01 Unzen), Risiko bleibt |
| `--gold-financing 7` | Gold-Übernachtkosten in % pro Jahr auf den Positionswert (Standard bei `--realistic*`: 7) |
| `--portfolio` | zusätzlich Portfolio 70 % BTC / 30 % Gold mit Quartals-Umverteilung |
| `--risk 1` | 1 % Risiko je Trade statt Originalrisiko (60 % BTC / 40 % Gold) |
| `--no-margin-calls` | ohne TradingView-Margin-Call rechnen |
| `--funding` | historische Binance-Funding-Raten für BTC belasten (nicht im Pine-Skript) |
| `--btc-csv / --gold-csv` | eigene 1h-Daten, z. B. TradingView-Export (`time,open,high,low,close`) |
| `--fx-csv` / `--usdchf 0.80` | eigene USD/CHF-Tageskurse bzw. konstanter Kurs |

Ergebnisse: Kennzahlen und Jahrestabelle im Terminal, Trade-Listen,
Equity-Kurven und Monatsendstände (`monatlich.csv`) als CSV in `backtest_data/results/`.

## Benötigte Hosts

`data.binance.vision`, `datafeed.dukascopy.com`.

## Modellannahmen

- Margin Call wie TradingView: Equity am Kerzentief unter 2 % Margin führt zur
  Liquidation des 4-fachen der Deckungsmenge zum Kerzentief. Wird in derselben
  Stunde der Stop erreicht, schliesst die ganze Position zum Stop.
- Gewinne in CHF mit dem USD/CHF-Schlusskurs des Vortags; USDT = USD.
- Gold: Provision 0.005 %, Mengenschritt 1 Unze, Tick 0.01.
- Teil-Liquidationen zählen in der Statistik zur selben Position.
- Wie im Pine-Skript: ohne `--funding` kein Funding, keine Wechselgebühr, keine Börsen-Liquidation,
  kein 70/30-Rebalancing.
