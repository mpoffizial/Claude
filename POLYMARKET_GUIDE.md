# 🎲 Polymarket Trading Analyzer & Bot

Complete toolkit for analyzing Polymarket prediction markets and generating trading signals.

## 🚀 Quick Start

```bash
# Run live signal generator
python polymarket_live_demo.py

# Analyze specific markets
python polymarket_analyzer.py

# Run trading bot
python polymarket_trading_bot.py
```

---

## 📊 What Is Polymarket?

**Polymarket** is a decentralized prediction market platform where:
- 🎯 Users trade binary outcome contracts (YES/NO) on real-world events
- 💰 Market prices represent crowd-sourced probability estimates
- 📈 Price = Probability (e.g., 0.72 price = 72% probability)
- 🔗 Built on blockchain with real monetary stakes
- ⚖️ Markets settle when the event outcome is determined

**Examples:**
- "Will Bitcoin be above $50,000 by Dec 2026?" (Crypto)
- "Who will win the 2026 US Election?" (Politics)
- "Will Fed raise rates in 2026?" (Economics)
- "Will Tesla stock exceed $300 by Q3 2026?" (Tech)

---

## 🔍 How The Analyzer Works

### 1. Data Fetching

**Option A: Direct API (When you have internet access)**
```python
from polymarket_analyzer import PolymarketAnalyzer

analyzer = PolymarketAnalyzer()
markets = analyzer.get_markets(limit=100)  # Fetch top 100 markets
```

**Option B: Search Markets**
```python
# Search for specific markets
bitcoin_markets = analyzer.search_markets("Bitcoin", limit=20)
election_markets = analyzer.search_markets("election", limit=20)
```

**Option C: Get Trending Markets**
```python
trending = analyzer.get_trending_markets(limit=20)  # Most active markets
```

### 2. Signal Detection

The tool detects **4 types of trading opportunities:**

| Signal Type | Condition | Action | Edge |
|-------------|-----------|--------|------|
| **Overbought** | Probability > 75% | SHORT | High |
| **Oversold** | Probability < 25% | LONG | High |
| **Fair Value** | 45% < Prob < 55% | Monitor | Medium |
| **Arbitrage** | Outcome sum ≠ 100% | Spread | Very High |

### 3. Trade Execution

For each signal:
- ✅ Entry point (current market price)
- ✅ Stop loss (probability reversal point)
- ✅ Take profit (edge target)
- ✅ Risk/Reward ratio
- ✅ Confidence score (0-100)

---

## 💡 Trading Strategies

### Strategy 1: Probability Reversion
**When:** Probability becomes extreme (>75% or <25%)
**Why:** Markets often overcorrect and revert
**Action:** Trade against the extreme probability
```
Example: "Trump wins 2024" at 85% probability
→ SHORT at 0.85, TP at 0.72, SL at 0.92
```

### Strategy 2: Arbitrage
**When:** Probability sum ≠ 100% across related markets
**Why:** Risk-free profit if probabilities reconcile
**Action:** Spread trade across outcomes
```
Example: Market 1 YES = 0.58, NO = 0.43 (sum = 1.01)
→ SHORT YES, LONG NO → 1% guaranteed profit
```

### Strategy 3: Momentum Trading
**When:** Probability moving up/down with high volume
**Why:** Momentum persists in prediction markets
**Action:** Follow the probability shift
```
Example: Probability rises 0.55 → 0.68 on high volume
→ LONG, TP at further probability increase
```

### Strategy 4: Information Edge
**When:** You have information before market does
**Why:** Prediction markets price in known information
**Action:** Trade on informational advantage
```
Example: Breaking news favors one outcome
→ Fade the market if odds don't reflect news yet
```

---

## 📈 Signal Examples

### Example 1: Oversold Opportunity ✅

```
Market: "Will Fed raise rates in 2026?"
Current Probability: YES = 35% | NO = 65%
Signal: OVERSOLD (YES option undervalued)
Edge: 25 - 35 = 10%
Action: BUY YES at 0.35
Target: 0.45+ (10% move)
Risk/Reward: 1:2.0
```

### Example 2: Overbought Opportunity ✅

```
Market: "Will Bitcoin > $50k by 2026?"
Current Probability: YES = 82% | NO = 18%
Signal: OVERBOUGHT (YES option overvalued)
Edge: 82 - 75 = 7%
Action: SHORT YES at 0.82
Target: 0.72 (10% move)
Risk/Reward: 1:1.4
```

### Example 3: Arbitrage ✅

```
Market A: Outcome X = 0.58, Outcome Y = 0.42 (sum = 1.00)
Market B: Outcome X = 0.55, Outcome Y = 0.46 (sum = 1.01)

Arbitrage:
SHORT Market B YES (0.55)
LONG Market A YES (0.58)
→ 3% spread → guaranteed profit when reconciles
```

---

## 🔗 Connecting to Live Polymarket Data

### Method 1: Direct API (Recommended)

```bash
pip install requests pandas numpy
python polymarket_trading_bot.py
```

**When you have internet access:**
- Fetches real-time market data from `gamma-api.polymarket.com`
- Analyzes all active markets
- Generates live trading signals

### Method 2: Using py-clob-client (Advanced)

```bash
pip install py-clob-client
```

```python
from pyClob import ClobClient

client = ClobClient(host="https://clob.polymarket.com")
markets = client.get_markets()  # Get all markets
prices = client.get_prices(market_id)  # Get live prices
```

### Method 3: GraphQL API

```bash
pip install requests
```

```python
import requests

query = """
{
  markets(first: 100) {
    id
    question
    outcomes {
      label
      price
    }
  }
}
"""

response = requests.post(
    "https://api.polymarket.com/graphql",
    json={"query": query}
)
```

### Method 4: Fallback - Manual Import

If API is blocked:
1. Visit https://polymarket.com
2. Export market data as CSV
3. Load in analyzer:

```python
import pandas as pd
from polymarket_trading_bot import PolymarketTradingBot

df = pd.read_csv("polymarket_data.csv")
bot = PolymarketTradingBot()
signals = bot.scan_all_markets()
```

---

## 🎯 Position Sizing

### Risk Management Rule

**Risk only 2-5% per market on your account**

```
Account: $10,000
Risk per trade: 2% = $200

Market odds: 70% vs 30% (0.70 to 0.30 = 40% move)
Position: $200 / 0.40 = $500 (5 contracts of $100 each)
```

### Kelly Criterion

```
Optimal Position Size = (Edge × Win% - Loss%) / Odds
Example:
Edge: 10%
Win Rate: 60%
Loss Rate: 40%
Odds: 1.5

Size = (0.10 × 0.60 - 0.40) / 1.5 = 3% of account
```

### Conservative Sizing
```
New traders: 1-2% per trade
Experienced: 2-5% per trade
Professional: 5-10% per trade
Maximum: Never >10% per trade
```

---

## 📊 Files Guide

| File | Purpose |
|------|---------|
| `polymarket_live_demo.py` | **START HERE** - Live signal demo |
| `polymarket_analyzer.py` | Core market analysis engine |
| `polymarket_trading_bot.py` | Auto trading bot with signals |
| `POLYMARKET_GUIDE.md` | Complete documentation (this file) |
| `polymarket_live_signals.json` | Latest signals output |

---

## ⚠️ Important Rules

1. ✅ **ALWAYS trade with stop losses** - Use probability reversals
2. ✅ **Only trade liquid markets** - Minimum $100k daily volume
3. ✅ **Check fundamentals** - Don't just follow probabilities
4. ✅ **Verify event definitions** - Market resolution rules matter
5. ✅ **Track results** - Keep a trading journal
6. ⚠️ **Don't over-leverage** - Prediction markets can be volatile
7. ⚠️ **Avoid extreme probabilities** - Binary outcomes can surprise
8. ⚠️ **Monitor news** - Event updates change probabilities fast

---

## 🔧 Troubleshooting

### "Connection refused" / "API timeout"
- Check internet connection
- Verify you're not behind restrictive proxy
- Try API fallback methods (GraphQL, CSV import)

### "No signals found"
- Check if markets have sufficient liquidity
- Verify probability data is recent
- Check if extreme conditions exist

### "Wrong prices/probabilities"
- Verify API endpoint is correct
- Check market hasn't closed
- Ensure price data is sorted by time

### "Rate limited"
- Add delay between API calls: `time.sleep(1)`
- Use pagination: `offset=100`, `offset=200`, etc.
- Reduce query frequency

---

## 📚 Learning Resources

**Understanding Prediction Markets:**
- https://docs.polymarket.com - Official docs
- https://polymarket.com/de - Main platform (German)
- Research papers on prediction market theory

**Trading Strategies:**
- Mean reversion: Classic strategy for overbought/oversold
- Momentum: Follow probability shifts with volume
- Arbitrage: Exploit cross-market inconsistencies
- Information edge: Trade on known but not-yet-priced info

**Tools & Libraries:**
- py-clob-client: Official Python client
- polymarket-apis: Pydantic-validated API wrappers
- Requests: HTTP library for API calls

---

## 💰 Real World Examples

### Bitcoin Prediction Market

```
Question: "Will Bitcoin > $100k by EOY 2026?"

Current Price: 0.65 (65% probability)
Daily Volume: $500k
Edge: Market seems underpricing with favorable macro

Action:
Entry: 0.65 (buy 5 contracts @ $100 each = $325 position)
Stop: 0.55 (if macro turns negative)
Take Profit: 0.75 (10% move)
Risk: $50 | Reward: $50 | R/R = 1:1
```

### Election Market

```
Question: "Will candidate X win 2026 election?"

Current Price: 0.72 (72% probability)
Daily Volume: $2M (very liquid)
Edge: Consensus seems overconfident

Action:
Entry: 0.72 (SHORT 10 contracts)
Stop: 0.82 (if momentum accelerates)
Take Profit: 0.62 (10% drop)
Risk: $100 | Reward: $100 | R/R = 1:1
```

---

## 🚀 Next Steps

1. **Run demo:** `python polymarket_live_demo.py`
2. **Analyze markets:** `python polymarket_analyzer.py`
3. **Generate signals:** `python polymarket_trading_bot.py`
4. **Paper trade:** Test on Polymarket with fake money
5. **Go live:** Start with small positions once profitable

---

## 📞 Support

- **Polymarket Docs:** https://docs.polymarket.com
- **Discord Community:** Polymarket Discord
- **X (Twitter):** @PolymarketUSD

---

**Disclaimer:** This is educational content. Prediction market trading carries risk. Only trade money you can afford to lose. Past performance ≠ future results. Not financial advice.

---

**Happy trading!** 🎲📈
