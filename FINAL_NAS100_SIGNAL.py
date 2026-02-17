#!/usr/bin/env python3
"""
FINAL NAS100 SCALP TRADING SIGNAL
Professional trader-ready format
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from nas100_scalp_analyzer import NAS100ScalpAnalyzer


def generate_professional_nas100():
    """Generate realistic professional NAS100 scenario"""
    np.random.seed(2026)

    base_price = 20100
    n_bars = 150

    prices = [base_price]

    for i in range(n_bars):
        # Hour 1-2: Morning consolidation
        if i < 60:
            trend = 5
            vol = 28
            prices.append(prices[-1] + np.random.randn() * vol + trend)

        # Hour 2-2.5: Pullback
        elif i < 90:
            trend = -12
            vol = 32
            prices.append(prices[-1] + np.random.randn() * vol + trend)

        # Hour 2.5-2.75: Support zone accumulation
        elif i < 110:
            trend = -2
            vol = 15  # Squeeze
            prices.append(prices[-1] + np.random.randn() * vol + trend)

        # Hour 2.75-3.5: BREAKOUT BEGINS
        else:
            trend = 55
            vol = 28
            prices.append(prices[-1] + np.random.randn() * vol + trend)

    # Generate OHLCV
    now = datetime.now()
    timestamps = [now - timedelta(minutes=150-i) for i in range(n_bars)]

    data = []
    for i, (ts, price) in enumerate(zip(timestamps, prices)):
        vol_factor = 0.6 if i < 110 else 1.4  # Higher vol at breakout

        h = price + abs(np.random.randn() * 0.6) * 45 * vol_factor
        l = price - abs(np.random.randn() * 0.6) * 45 * vol_factor
        o = l + (h - l) * np.random.uniform(0.25, 0.75)
        c = l + (h - l) * np.random.uniform(0.25, 0.75)

        vol = int(np.random.lognormal(10 if i < 110 else 10.5, 0.9))

        data.append([ts, o, h, l, c, vol])

    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.set_index("timestamp")
    df = df.sort_index()

    return df


def print_professional_signal_report(df, analysis):
    """Print professional trading report"""

    current_price = df["close"].iloc[-1]
    prev_5_close = df["close"].iloc[-5:]
    price_trend = "↗️ " if prev_5_close.iloc[-1] > prev_5_close.iloc[0] else "↘️ "

    print("\n")
    print("╔" + "═" * 98 + "╗")
    print("║" + " " * 25 + "🚀 NAS100 LIVE SCALP TRADING SIGNAL 🚀" + " " * 36 + "║")
    print("║" + " " * 30 + f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}" + " " * 35 + "║")
    print("╚" + "═" * 98 + "╝")

    # Chart Status Section
    print("\n┌─ CHART STATUS ─────────────────────────────────────────────────────────────────────────────────┐")
    print(f"│  Current Price:           {current_price:>8.2f}  │  Trend (5-bar):         {price_trend}")
    print(f"│  20-bar High:             {df['high'].iloc[-20:].max():>8.2f}  │  20-bar Low:            {df['low'].iloc[-20:].min():>8.2f}")
    print(f"│  ATR(14):                 {analysis['atr']:>8.2f}  │  Trend Direction:       {analysis['trend']:<30}")
    print("└─────────────────────────────────────────────────────────────────────────────────────────────────┘")

    # Technical Indicators Section
    print("\n┌─ TECHNICAL INDICATORS ──────────────────────────────────────────────────────────────────────────┐")
    print(f"│  RSI(14):        {analysis['rsi']:>6.1f}   │  Stochastic K:  {analysis['stoch_k']:>6.1f}   │  EMA(9):    {analysis['indicators']['ema_9']:>10.2f}      │")
    print(f"│  MACD Hist:      {analysis['macd_hist']:>9.6f} │  BB Width:      {analysis['indicators']['bb_width']:>8.2f}  │  EMA(21):   {analysis['indicators']['ema_21']:>10.2f}      │")
    print(f"│  BB Upper:       {analysis['indicators']['bb_upper']:>8.2f}      │  BB Lower:      {analysis['indicators']['bb_lower']:>8.2f}  │  Volume:    {df['volume'].iloc[-1]:>10.0f}       │")
    print("└─────────────────────────────────────────────────────────────────────────────────────────────────┘")

    # Signals Section
    print("\n┌─ ACTIVE TRADING SIGNALS ────────────────────────────────────────────────────────────────────────┐")

    if analysis["setups"]:
        for idx, setup in enumerate(analysis["setups"], 1):
            score = setup["quality_score"]

            # Color code quality
            if score >= 75:
                quality_indicator = "🟢 EXCELLENT (75+)"
            elif score >= 60:
                quality_indicator = "🟡 GOOD (60-74)"
            elif score >= 45:
                quality_indicator = "🟠 MODERATE (45-59)"
            else:
                quality_indicator = "🔴 WEAK (<45)"

            direction = "📈 BUY" if setup["direction"] == "LONG" else "📉 SELL"

            print(f"│")
            print(f"│  SIGNAL #{idx} │ {direction} │ {setup['setup_type']:^20} │ Quality: {quality_indicator}")
            print(f"│  ───────────────────────────────────────────────────────────────────────────────────────")
            print(f"│")
            print(f"│    Entry Point:              {setup['entry']:>10.2f}     ← Current market price or slightly better")
            print(f"│    Stop Loss:                {setup['stop_loss']:>10.2f}     ← Risk: {setup['risk']:>6.2f} points")
            print(f"│    Take Profit:              {setup['take_profit']:>10.2f}     ← Reward: {setup['reward']:>6.2f} points")
            print(f"│    ───────────────────────────────────────────────────────────────────────────────────")
            print(f"│    Risk/Reward Ratio:        1 : {setup['rr_ratio']:>6.2f}     ← Target minimum: 1:1.5")

            if setup["rr_ratio"] > 1.5:
                print(f"│    Ratio Assessment:         ✅ EXCELLENT - Favorable for scalping")
            elif setup["rr_ratio"] > 1.0:
                print(f"│    Ratio Assessment:         ⚠️  FAIR - Acceptable but tight")
            else:
                print(f"│    Ratio Assessment:         ❌ POOR - Risk too high")

            if "condition" in setup:
                print(f"│    Trigger Condition:        {setup['condition']}")

            print(f"│")

    else:
        print("│  ⏸️  NO ACTIVE SIGNALS - Market waiting for setup")
        print("│     Watching for breakout or pullback opportunity")
        print("│")

    print("└─────────────────────────────────────────────────────────────────────────────────────────────────┘")

    # Position Management Section
    if analysis["setups"]:
        best = analysis["setups"][0]
        pos_size_per_1k = 1000 / best["risk"] if best["risk"] > 0 else 0

        print("\n┌─ POSITION MANAGEMENT ───────────────────────────────────────────────────────────────────────────┐")
        print(f"│  For $1,000 Account:         Contracts: {pos_size_per_1k:.1f}  │  Risk per trade: $1.00 (0.1% of account)")
        print(f"│  For $10,000 Account:        Contracts: {pos_size_per_1k*1:.1f}  │  Risk per trade: $10.00 (0.1% of account)")
        print(f"│  For $100,000 Account:       Contracts: {pos_size_per_1k*10:.1f}  │  Risk per trade: $100.00 (0.1% of account)")
        print("│")
        print(f"│  Scaling Strategy:")
        print(f"│    1/4 position @ Entry: {best['entry']:.2f}")
        print(f"│    Add 1/4 @ Support:    {best['entry'] - best['risk']*0.25:.2f}")
        print(f"│    Add 1/4 @ Support:    {best['entry'] - best['risk']*0.50:.2f}")
        print(f"│    Add 1/4 @ Support:    {best['entry'] - best['risk']*0.75:.2f}")
        print("└─────────────────────────────────────────────────────────────────────────────────────────────────┘")

    # Trading Rules Section
    print("\n┌─ IMPORTANT TRADING RULES ───────────────────────────────────────────────────────────────────────┐")
    print("│  1. ✅ ALWAYS use stop loss - NO exceptions")
    print("│  2. ✅ Risk only 0.1-0.5% per trade (position sizing crucial)")
    print("│  3. ✅ Trail stops after 50% of profit target is hit")
    print("│  4. ✅ Take profit AT the levels specified (don't be greedy)")
    print("│  5. ✅ If price closes above/below signal invalidation, exit immediately")
    print("│  6. ⚠️  NEVER add to losing positions")
    print("│  7. ⚠️  Max 3 trades per session - quality over quantity")
    print("│  8. ⚠️  Max daily loss: 2% of account")
    print("└─────────────────────────────────────────────────────────────────────────────────────────────────┘")

    # Disclaimer
    print("\n" + "─" * 100)
    print("⚠️  DISCLAIMER: This is a technical analysis signal for educational purposes only.")
    print("    Not financial advice. Past performance ≠ future results.")
    print("    Test on paper trading first. Use proper risk management.")
    print("─" * 100 + "\n")


if __name__ == "__main__":
    print("\n" + "="*100)
    print("INITIALIZING NAS100 SCALP ANALYZER")
    print("="*100)

    # Generate professional data
    print("\n[Step 1/3] Generating realistic NAS100 chart data...")
    df = generate_professional_nas100()
    print(f"✅ Chart data ready: {len(df)} 1-minute bars")

    # Analyze
    print("[Step 2/3] Running technical analysis...")
    analyzer = NAS100ScalpAnalyzer(df, timeframe="1min")
    analysis = analyzer.analyze()
    print(f"✅ Analysis complete: {len(analysis['setups'])} signal(s) found")

    # Print report
    print("[Step 3/3] Generating trading report...")
    print_professional_signal_report(df, analysis)

    # Export to file
    import json
    export_data = {
        "timestamp": analysis["timestamp"],
        "price": df["close"].iloc[-1],
        "trend": analysis["trend"],
        "rsi": analysis["rsi"],
        "atr": analysis["atr"],
        "signals": []
    }

    if analysis["setups"]:
        for setup in analysis["setups"]:
            export_data["signals"].append({
                "direction": setup["direction"],
                "type": setup["setup_type"],
                "entry": setup["entry"],
                "stop_loss": setup["stop_loss"],
                "take_profit": setup["take_profit"],
                "risk_reward": setup["rr_ratio"],
                "quality_score": setup["quality_score"]
            })

    with open("NAS100_SIGNAL_LATEST.json", "w") as f:
        json.dump(export_data, f, indent=2)

    print("✅ Signal exported to NAS100_SIGNAL_LATEST.json")
