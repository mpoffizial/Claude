#!/usr/bin/env python3
"""
Live NAS100 Signal Generator with realistic scenarios
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from nas100_scalp_analyzer import NAS100ScalpAnalyzer, print_quick_summary


def generate_bullish_breakout_scenario():
    """Generate a chart with CLEAR BULLISH BREAKOUT setup"""
    np.random.seed(123)

    # Base price
    base_price = 20000
    n_bars = 100

    # Consolidation then breakout
    prices = [base_price]
    for i in range(n_bars):
        if i < 40:
            # Consolidation phase
            noise = np.random.randn() * 20
            prices.append(prices[-1] + noise)
        elif i < 60:
            # Squeeze (Bollinger Band contraction)
            noise = np.random.randn() * 10
            prices.append(prices[-1] + noise)
        else:
            # BREAKOUT UP
            noise = np.random.randn() * 15
            prices.append(prices[-1] + 50 + noise)  # Strong upward move

    # Generate OHLCV
    timestamps = pd.date_range(end=datetime.now(), periods=n_bars, freq="1min")
    data = []

    for i, (ts, price) in enumerate(zip(timestamps, prices)):
        vol = np.random.uniform(0.8, 1.2) * 50  # Breakout has volume
        h = price + abs(np.random.randn()) * vol
        l = price - abs(np.random.randn()) * vol
        o = price + np.random.randn() * vol * 0.5
        c = price
        data.append([ts, o, h, l, c, max(100, int(h - l) * 1000)])

    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.set_index("timestamp")
    return df


def generate_mean_reversion_scenario():
    """Generate chart with MEAN REVERSION (oversold) setup"""
    np.random.seed(456)

    base_price = 20000
    n_bars = 100

    prices = [base_price]
    for i in range(n_bars):
        if i < 50:
            # Uptrend
            prices.append(prices[-1] + np.random.randn() * 10 + 15)
        else:
            # Sharp selloff (RSI becomes < 30)
            prices.append(prices[-1] + np.random.randn() * 20 - 80)

    timestamps = pd.date_range(end=datetime.now(), periods=n_bars, freq="1min")
    data = []

    for i, (ts, price) in enumerate(zip(timestamps, prices)):
        vol = np.random.uniform(0.5, 1.5) * 40
        h = price + abs(np.random.randn()) * vol
        l = price - abs(np.random.randn()) * vol
        o = price + np.random.randn() * vol * 0.5
        c = price
        data.append([ts, o, h, l, c, max(100, int(h - l) * 1000)])

    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.set_index("timestamp")
    return df


def generate_momentum_scenario():
    """Generate chart with MOMENTUM setup (MACD + Stoch)"""
    np.random.seed(789)

    base_price = 20000
    n_bars = 100

    prices = [base_price]
    for i in range(n_bars):
        if i < 30:
            # Accumulation
            prices.append(prices[-1] + np.random.randn() * 10)
        elif i < 60:
            # Trend start
            prices.append(prices[-1] + np.random.randn() * 15 + 35)
        else:
            # Strong momentum
            prices.append(prices[-1] + np.random.randn() * 20 + 50)

    timestamps = pd.date_range(end=datetime.now(), periods=n_bars, freq="1min")
    data = []

    for i, (ts, price) in enumerate(zip(timestamps, prices)):
        vol = np.random.uniform(0.5, 1.3) * 45
        h = price + abs(np.random.randn()) * vol
        l = price - abs(np.random.randn()) * vol
        o = price + np.random.randn() * vol * 0.5
        c = price
        data.append([ts, o, h, l, c, max(100, int(h - l) * 1000)])

    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.set_index("timestamp")
    return df


def print_signal_summary(scenario_name, df, analysis):
    """Print trading signal summary"""
    print(f"\n{'='*80}")
    print(f"📈 SCENARIO: {scenario_name}")
    print(f"{'='*80}\n")

    print(f"Current Price: {df['close'].iloc[-1]:.2f}")
    print(f"Trend: {analysis['trend']}")
    print(f"RSI(14): {analysis['rsi']:.1f}")
    print(f"Stoch K: {analysis['stoch_k']:.1f}")
    print(f"ATR(14): {analysis['atr']:.2f}\n")

    if analysis["setups"]:
        print(f"🎯 ACTIVE SIGNALS FOUND: {len(analysis['setups'])}\n")

        for i, setup in enumerate(analysis["setups"], 1):
            status = "✅ STRONG" if setup["quality_score"] > 70 else "⚠️  MODERATE"
            print(f"[SIGNAL #{i}] {status}")
            print(f"  Type:         {setup['setup_type']} - {setup['direction']}")
            print(f"  Score:        {setup['quality_score']:.0f}/100")
            print(f"  ─────────────────────────────────")
            print(f"  Entry:        {setup['entry']:.2f}")
            print(f"  Stop Loss:    {setup['stop_loss']:.2f} (Risk: {setup['risk']:.2f}pts)")
            print(f"  Take Profit:  {setup['take_profit']:.2f} (Reward: {setup['reward']:.2f}pts)")
            print(f"  Risk/Reward:  1:{setup['rr_ratio']:.2f}")
            if "condition" in setup:
                print(f"  Condition:    {setup['condition']}")
            print()
    else:
        print("⏸️  No active signals at this moment\n")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("NAS100 LIVE SIGNAL GENERATOR - Testing Real Scenarios")
    print("="*80)

    # SCENARIO 1: Bullish Breakout
    print("\n[Generating Scenario 1: BULLISH BREAKOUT]")
    df1 = generate_bullish_breakout_scenario()
    analyzer1 = NAS100ScalpAnalyzer(df1, timeframe="1min")
    analysis1 = analyzer1.analyze()
    print_signal_summary("BULLISH BREAKOUT (Price breaking above consolidation)", df1, analysis1)

    # SCENARIO 2: Mean Reversion (Oversold)
    print("\n[Generating Scenario 2: MEAN REVERSION]")
    df2 = generate_mean_reversion_scenario()
    analyzer2 = NAS100ScalpAnalyzer(df2, timeframe="1min")
    analysis2 = analyzer2.analyze()
    print_signal_summary("MEAN REVERSION (Sharp selloff creates oversold bounce)", df2, analysis2)

    # SCENARIO 3: Momentum
    print("\n[Generating Scenario 3: MOMENTUM]")
    df3 = generate_momentum_scenario()
    analyzer3 = NAS100ScalpAnalyzer(df3, timeframe="1min")
    analysis3 = analyzer3.analyze()
    print_signal_summary("MOMENTUM (Strong trending move with acceleration)", df3, analysis3)

    print("\n" + "="*80)
    print("✅ All scenarios analyzed!")
    print("="*80 + "\n")
