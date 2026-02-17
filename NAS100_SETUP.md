# NAS100 Scalp Trading Signal Analyzer

## 🚀 Quick Start

```bash
# Run live analysis
python FINAL_NAS100_SIGNAL.py

# Run with custom timeframe
python analyze_nas100.py 1 150    # 1-minute chart, 150 bars
python analyze_nas100.py 5 200    # 5-minute chart, 200 bars
```

## 📊 What This Tool Does

### Real-Time Signal Detection
Automatically detects 4 types of scalp trading setups:
1. **Breakout** - Price breaks above/below Bollinger Bands
2. **Pullback** - Price retraces to EMA during trend
3. **Mean Reversion** - Oversold (RSI<30) / Overbought (RSI>70)
4. **Momentum** - MACD + Stochastic crossover

### Output Includes
- ✅ Entry price
- ✅ Stop loss (ATR-based)
- ✅ Take profit level
- ✅ Risk/Reward ratio (target: >1.5)
- ✅ Quality score (0-100)
- ✅ Position sizing for different account sizes

---

## 🔌 Connecting to TradingView

### Option 1: Using TradingView WebSocket (Recommended for production)

Currently blocked by proxy in your environment, but here's how it works:

```bash
pip install websocket-client pandas numpy
python nas100_connector.py
```

**When you have internet access outside proxy:**
```python
from nas100_connector import get_nas100_data
df = get_nas100_data(symbol="NQ1!", interval="1", n_bars=500)
```

### Option 2: Using TradingView TA Library (HTTP)

```bash
pip install tradingview-ta requests
python nas100_http_connector.py
```

**When proxy is accessible:**
```python
from nas100_http_connector import NAS100HTTPConnector
connector = NAS100HTTPConnector()
data = connector.get_full_indicators("NQ1!")
```

### Option 3: Direct Integration (If TradingView Blocks)

Use alternative data sources:
```bash
pip install yfinance pandas
```

```python
import yfinance as yf
df = yf.download("NQ=F", interval="1m", period="1d")  # Nasdaq 100 Futures
```

### Option 4: Manual Chart Import

1. Open TradingView → NAS100 chart (1min)
2. Export data as CSV
3. Load in analyzer:

```python
import pandas as pd
from nas100_scalp_analyzer import NAS100ScalpAnalyzer

df = pd.read_csv("nas100_data.csv", index_col="timestamp", parse_dates=True)
analyzer = NAS100ScalpAnalyzer(df)
analysis = analyzer.analyze()
```

---

## 🛠️ Fix Proxy Issues

### If Behind Corporate Proxy

```bash
# Set proxy for pip
pip install --proxy [user:passwd@]proxy.server:port package_name

# Or configure Python
export http_proxy=http://proxy.server:8080
export https_proxy=https://proxy.server:8080
python script.py
```

### If Behind Firewall with SSL Inspection

```bash
# Disable SSL verification (last resort)
import os
os.environ['REQUESTS_CA_BUNDLE'] = ''
```

### Using VPN/Tunnel

```bash
# If available, use VPN to bypass local proxy
# Then run the connector scripts
python nas100_connector.py
```

---

## 📈 Trading Rules

**Always Follow These Rules:**

1. ✅ ALWAYS use stop loss - NO exceptions
2. ✅ Risk only 0.1-0.5% per trade
3. ✅ Target Risk/Reward minimum of 1:1.5
4. ✅ Take profit at specified levels
5. ✅ Trail stops after 50% profit target
6. ⚠️ Never add to losing positions
7. ⚠️ Maximum 3 trades per session
8. ⚠️ Daily loss limit: 2% of account

---

## 📊 File Guide

| File | Purpose |
|------|---------|
| `FINAL_NAS100_SIGNAL.py` | Main signal generator (run this!) |
| `analyze_nas100.py` | Analysis with custom parameters |
| `nas100_scalp_analyzer.py` | Core analysis engine |
| `nas100_connector.py` | TradingView WebSocket connector |
| `nas100_http_connector.py` | TradingView HTTP connector |
| `current_nas100_signal.py` | Realistic scenario example |
| `live_signal_example.py` | Multiple scenario testing |
| `NAS100_SIGNAL_LATEST.json` | Last signal in JSON format |

---

## 🎯 Position Sizing Example

**Risk: $1 per 100 points of risk**

```
Setup Risk: 92.45 points
Account: $10,000
Risk per trade: 0.1% = $10

Position size = $10 / 92.45 = 0.108 contracts
= ~1 micro contract (MNQ)
```

---

## 🔄 Indicator Definitions

| Indicator | What It Measures | Scalp Use |
|-----------|-----------------|-----------|
| **RSI(14)** | Momentum, overbought/oversold | Entry confirmation, mean reversion |
| **Stochastic** | Price relative to range | Momentum shifts, exits |
| **EMA(9/21)** | Fast trend | Entry zone, stop placement |
| **MACD** | Momentum + trend | Signal confirmation |
| **ATR(14)** | Volatility | Stop loss & profit sizing |
| **Bollinger Bands** | Volatility bands | Breakout detection |

---

## 💡 Example Trades

### SETUP 1: Breakout Trade
```
Chart: 1-minute
Condition: Price breaks above BB upper
Entry: 22,490
Stop: 22,405
Target: 22,575
Risk: 85 pts | Reward: 85 pts
R:R = 1:1.0 ❌ (Too tight)
```

### SETUP 2: Mean Reversion (Better)
```
Chart: 1-minute
Condition: RSI < 30 + Stochastic K < 20
Entry: 20,850
Stop: 20,750
Target: 20,950
Risk: 100 pts | Reward: 100 pts
R:R = 1:1.0 ⚠️ (Minimum acceptable)
```

### SETUP 3: Momentum Trade (Ideal)
```
Chart: 1-minute
Condition: MACD crossover + Price > EMA21
Entry: 21,200
Stop: 21,080
Target: 21,500
Risk: 120 pts | Reward: 300 pts
R:R = 1:2.5 ✅ (EXCELLENT)
```

---

## ⚡ Quick Scalping Tips

1. **Use 1-minute charts** - Most responsive for scalps
2. **Hold 1-5 minutes** - In and out quickly
3. **Move stop to BE** - Once 50% profit reached
4. **Trail hard** - Use 0.5x ATR trailing stop
5. **Skip low volume** - Need at least 50-100 contracts
6. **Avoid 9:30-10:00am EST** - High slippage, volatile
7. **Best times:** 10am-2pm EST - Steady volume, defined moves

---

## ⚠️ Disclaimer

This tool is for **educational purposes only**. Not financial advice.

**Important:**
- Past performance does NOT guarantee future results
- Test on paper trading first
- Use proper risk management
- Only risk money you can afford to lose
- Consult a financial advisor before trading

---

## 🆘 Troubleshooting

### "Module not found"
```bash
pip install websocket-client pandas numpy tradingview-ta requests
```

### "Connection timeout"
- Check internet connection
- Check proxy settings
- Try HTTP connector instead of WebSocket
- Use manual CSV import

### "No signals detected"
- Check chart has enough data (50+ bars)
- Check indicators are calculating properly
- Adjust signal detection thresholds in `nas100_scalp_analyzer.py`

### "Wrong prices/data"
- Verify timezone settings
- Check if data is sorted by time
- Ensure no missing bars

---

## 📞 Support

For issues or questions:
1. Check the README (you're reading it!)
2. Review example scripts (`live_signal_example.py`)
3. Check signal JSON file for debugging

---

**Happy trading!** 🚀📈
