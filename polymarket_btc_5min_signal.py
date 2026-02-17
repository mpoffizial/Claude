#!/usr/bin/env python3
"""
Polymarket BTC UP/DOWN 5-Minute Signal Generator
Real-time Bitcoin direction prediction for Polymarket binary markets
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json


class BTC5MinSignalGenerator:
    """Generate BTC UP/DOWN signals for 5-minute Polymarket markets"""

    def __init__(self):
        self.signal_confidence = 0
        self.direction = None

    @staticmethod
    def generate_realistic_btc_5min(n_bars=200):
        """Generate realistic 5-minute Bitcoin price data"""
        np.random.seed(2026)

        # Current BTC price ~$95,000
        base_price = 95000
        prices = [base_price]

        for i in range(n_bars):
            # Phase 1: Consolidation (bars 0-80)
            if i < 80:
                trend = 20
                volatility = 150
                prices.append(prices[-1] + np.random.randn() * volatility + trend)

            # Phase 2: Pullback (bars 80-140)
            elif i < 140:
                trend = -35
                volatility = 200
                prices.append(prices[-1] + np.random.randn() * volatility + trend)

            # Phase 3: Support bounce (bars 140-170)
            elif i < 170:
                trend = 50
                volatility = 100
                prices.append(prices[-1] + np.random.randn() * volatility + trend)

            # Phase 4: BREAKOUT UP (bars 170-200) ← CURRENT
            else:
                trend = 150
                volatility = 180
                prices.append(prices[-1] + np.random.randn() * volatility + trend)

        # Generate OHLCV
        now = datetime.now()
        timestamps = [now - timedelta(minutes=200-i) for i in range(n_bars)]

        data = []
        for i, (ts, price) in enumerate(zip(timestamps, prices)):
            vol_factor = 0.8 if i < 170 else 1.5  # Higher vol at breakout

            h = price + abs(np.random.randn() * 0.6) * 60 * vol_factor
            l = price - abs(np.random.randn() * 0.6) * 60 * vol_factor
            o = l + (h - l) * np.random.uniform(0.25, 0.75)
            c = l + (h - l) * np.random.uniform(0.25, 0.75)

            vol = int(np.random.lognormal(15, 1.2))

            data.append([ts, o, h, l, c, vol])

        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.set_index("timestamp")
        df = df.sort_index()

        return df

    def calculate_indicators(self, df):
        """Calculate technical indicators for 5-minute timeframe"""

        # Fast EMAs for 5-minute
        ema_5 = df["close"].ewm(span=5, adjust=False).mean()
        ema_10 = df["close"].ewm(span=10, adjust=False).mean()
        ema_20 = df["close"].ewm(span=20, adjust=False).mean()

        # RSI (14)
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        # MACD (fast settings for 5min: 5,13,5)
        fast_ema = df["close"].ewm(span=5, adjust=False).mean()
        slow_ema = df["close"].ewm(span=13, adjust=False).mean()
        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=5, adjust=False).mean()
        macd_hist = macd_line - signal_line

        # ATR (14)
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # Stochastic (14,3)
        low_min = df["low"].rolling(14).min()
        high_max = df["high"].rolling(14).max()
        k_percent = 100 * (df["close"] - low_min) / (high_max - low_min)
        d_percent = k_percent.rolling(3).mean()

        # Bollinger Bands (20, 2)
        bb_mid = df["close"].rolling(20).mean()
        bb_std = df["close"].rolling(20).std()
        bb_upper = bb_mid + (2 * bb_std)
        bb_lower = bb_mid - (2 * bb_std)

        return {
            "ema_5": ema_5,
            "ema_10": ema_10,
            "ema_20": ema_20,
            "rsi": rsi,
            "macd_hist": macd_hist,
            "atr": atr,
            "k_percent": k_percent,
            "d_percent": d_percent,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_mid": bb_mid
        }

    def generate_signal(self, df):
        """Generate UP/DOWN signal for next 5 minutes"""

        ind = self.calculate_indicators(df)

        # Current values
        current_close = df["close"].iloc[-1]
        current_rsi = ind["rsi"].iloc[-1]
        current_k = ind["k_percent"].iloc[-1]
        current_d = ind["d_percent"].iloc[-1]
        macd_hist = ind["macd_hist"].iloc[-1]
        atr = ind["atr"].iloc[-1]

        ema_5 = ind["ema_5"].iloc[-1]
        ema_10 = ind["ema_10"].iloc[-1]
        ema_20 = ind["ema_20"].iloc[-1]

        bb_upper = ind["bb_upper"].iloc[-1]
        bb_lower = ind["bb_lower"].iloc[-1]
        bb_mid = ind["bb_mid"].iloc[-1]

        # Price momentum (last 5 bars)
        price_change_5 = df["close"].iloc[-1] - df["close"].iloc[-5]
        price_change_pct_5 = (price_change_5 / df["close"].iloc[-5]) * 100

        # Volume confirmation
        avg_vol = df["volume"].iloc[-20:].mean()
        current_vol = df["volume"].iloc[-1]
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1

        signal = {
            "timestamp": datetime.now().isoformat(),
            "current_price": current_close,
            "atr": atr,
            "indicators": {
                "rsi": current_rsi,
                "stoch_k": current_k,
                "stoch_d": current_d,
                "macd_hist": macd_hist,
                "ema_5": ema_5,
                "ema_10": ema_10,
                "ema_20": ema_20,
                "bb_upper": bb_upper,
                "bb_lower": bb_lower
            },
            "analysis": []
        }

        # Signal 1: EMA alignment
        if ema_5 > ema_10 > ema_20:
            signal["analysis"].append({
                "indicator": "EMA Trend",
                "signal": "BULLISH",
                "strength": "STRONG",
                "value": f"EMA(5)={ema_5:.0f} > EMA(10)={ema_10:.0f} > EMA(20)={ema_20:.0f}"
            })
        elif ema_5 < ema_10 < ema_20:
            signal["analysis"].append({
                "indicator": "EMA Trend",
                "signal": "BEARISH",
                "strength": "STRONG",
                "value": f"EMA(5)={ema_5:.0f} < EMA(10)={ema_10:.0f} < EMA(20)={ema_20:.0f}"
            })

        # Signal 2: RSI
        if current_rsi > 70:
            signal["analysis"].append({
                "indicator": "RSI(14)",
                "signal": "BEARISH",
                "strength": "MEDIUM",
                "value": f"Overbought at {current_rsi:.1f}"
            })
        elif current_rsi < 30:
            signal["analysis"].append({
                "indicator": "RSI(14)",
                "signal": "BULLISH",
                "strength": "MEDIUM",
                "value": f"Oversold at {current_rsi:.1f}"
            })
        else:
            signal["analysis"].append({
                "indicator": "RSI(14)",
                "signal": "NEUTRAL",
                "strength": "LOW",
                "value": f"Mid-range at {current_rsi:.1f}"
            })

        # Signal 3: Stochastic
        if current_k > current_d and current_k < 80:
            signal["analysis"].append({
                "indicator": "Stochastic",
                "signal": "BULLISH",
                "strength": "MEDIUM",
                "value": f"K({current_k:.1f}) > D({current_d:.1f}), not overbought"
            })
        elif current_k < current_d and current_k > 20:
            signal["analysis"].append({
                "indicator": "Stochastic",
                "signal": "BEARISH",
                "strength": "MEDIUM",
                "value": f"K({current_k:.1f}) < D({current_d:.1f}), not oversold"
            })

        # Signal 4: MACD
        if macd_hist > 0:
            signal["analysis"].append({
                "indicator": "MACD",
                "signal": "BULLISH",
                "strength": "MEDIUM",
                "value": f"Histogram positive at {macd_hist:.6f}"
            })
        else:
            signal["analysis"].append({
                "indicator": "MACD",
                "signal": "BEARISH",
                "strength": "MEDIUM",
                "value": f"Histogram negative at {macd_hist:.6f}"
            })

        # Signal 5: Price location
        if current_close > bb_upper:
            signal["analysis"].append({
                "indicator": "BB Position",
                "signal": "BEARISH",
                "strength": "MEDIUM",
                "value": f"Price above upper band - potential pullback"
            })
        elif current_close < bb_lower:
            signal["analysis"].append({
                "indicator": "BB Position",
                "signal": "BULLISH",
                "strength": "MEDIUM",
                "value": f"Price below lower band - potential bounce"
            })

        # Signal 6: Momentum
        if price_change_pct_5 > 0.5:
            signal["analysis"].append({
                "indicator": "5-bar Momentum",
                "signal": "BULLISH",
                "strength": "STRONG",
                "value": f"+{price_change_pct_5:.2f}% in last 5 bars"
            })
        elif price_change_pct_5 < -0.5:
            signal["analysis"].append({
                "indicator": "5-bar Momentum",
                "signal": "BEARISH",
                "strength": "STRONG",
                "value": f"{price_change_pct_5:.2f}% in last 5 bars"
            })

        # Signal 7: Volume
        if vol_ratio > 1.3:
            signal["analysis"].append({
                "indicator": "Volume",
                "signal": "CONFIRMATION",
                "strength": "HIGH",
                "value": f"{vol_ratio:.2f}x average volume - strong move"
            })

        # Calculate direction and confidence
        bull_signals = len([a for a in signal["analysis"] if a["signal"] == "BULLISH"])
        bear_signals = len([a for a in signal["analysis"] if a["signal"] == "BEARISH"])
        neutral_signals = len([a for a in signal["analysis"] if a["signal"] == "NEUTRAL"])

        if bull_signals > bear_signals:
            signal["direction"] = "UP"
            signal["confidence"] = min(95, 50 + (bull_signals * 10) + (vol_ratio - 1) * 50)
        elif bear_signals > bull_signals:
            signal["direction"] = "DOWN"
            signal["confidence"] = min(95, 50 + (bear_signals * 10) + (vol_ratio - 1) * 50)
        else:
            signal["direction"] = "NEUTRAL"
            signal["confidence"] = 45

        # Trading levels
        signal["trading_levels"] = {
            "current_price": current_close,
            "entry": current_close,
            "stop_loss": current_close - (atr * 1.5) if signal["direction"] == "UP" else current_close + (atr * 1.5),
            "take_profit_1": current_close + (atr * 1.0) if signal["direction"] == "UP" else current_close - (atr * 1.0),
            "take_profit_2": current_close + (atr * 2.0) if signal["direction"] == "UP" else current_close - (atr * 2.0),
            "risk": atr * 1.5,
            "reward_1": atr * 1.0,
            "reward_2": atr * 2.0
        }

        signal["bullish_signals"] = bull_signals
        signal["bearish_signals"] = bear_signals

        return signal


def print_btc_signal(signal):
    """Print formatted BTC UP/DOWN signal"""

    direction_emoji = "🟢" if signal["direction"] == "UP" else "🔴" if signal["direction"] == "DOWN" else "🟡"
    confidence_bar = "█" * int(signal["confidence"] / 5) + "░" * (20 - int(signal["confidence"] / 5))

    print(f"\n")
    print("╔" + "═" * 94 + "╗")
    print("║" + " " * 30 + "🚀 BTC UP/DOWN 5-MINUTE SIGNAL 🚀" + " " * 31 + "║")
    print("║" + " " * 35 + f"Polymarket Binary Market" + " " * 36 + "║")
    print("╚" + "═" * 94 + "╝")

    print(f"\n📊 MARKET STATUS ({signal['timestamp']})")
    print("─" * 96)
    print(f"  Current Price:     ${signal['current_price']:,.2f}")
    print(f"  ATR(14):           {signal['atr']:.2f}")
    print()

    print(f"🎯 SIGNAL")
    print("─" * 96)
    print(f"  Direction:         {direction_emoji} {signal['direction']}")
    print(f"  Confidence:        {confidence_bar} {signal['confidence']:.0f}%")
    print(f"  Bull Signals:      {signal['bullish_signals']}")
    print(f"  Bear Signals:      {signal['bearish_signals']}")
    print()

    print(f"📈 TECHNICAL INDICATORS")
    print("─" * 96)
    ind = signal["indicators"]
    print(f"  RSI(14):           {ind['rsi']:>6.1f}   │  EMA(5):  {ind['ema_5']:>10.0f}   │  BB Upper:  {ind['bb_upper']:>10.0f}")
    print(f"  Stoch K:           {ind['stoch_k']:>6.1f}   │  EMA(10): {ind['ema_10']:>10.0f}   │  BB Lower:  {ind['bb_lower']:>10.0f}")
    print(f"  Stoch D:           {ind['stoch_d']:>6.1f}   │  EMA(20): {ind['ema_20']:>10.0f}   │  MACD:      {ind['macd_hist']:>10.6f}")
    print()

    print(f"⚡ SIGNAL ANALYSIS")
    print("─" * 96)
    for i, analysis in enumerate(signal["analysis"], 1):
        signal_emoji = "🟢" if analysis["signal"] == "BULLISH" else "🔴" if analysis["signal"] == "BEARISH" else "🟡"
        print(f"  [{i}] {signal_emoji} {analysis['indicator']:<20} {analysis['signal']:<10} ({analysis['strength']})")
        print(f"      {analysis['value']}")

    print()
    print(f"💰 TRADING LEVELS (For next 5 minutes)")
    print("─" * 96)
    levels = signal["trading_levels"]
    print(f"  Entry:             ${levels['entry']:,.2f}")
    print(f"  Stop Loss:         ${levels['stop_loss']:,.2f}  (Risk: {levels['risk']:.2f})")
    print(f"  TP Level 1:        ${levels['take_profit_1']:,.2f}  (Reward: {levels['reward_1']:.2f})")
    print(f"  TP Level 2:        ${levels['take_profit_2']:,.2f}  (Reward: {levels['reward_2']:.2f})")
    print(f"  R/R Ratio (TP1):   1:{(levels['reward_1']/levels['risk']):.2f}")
    print(f"  R/R Ratio (TP2):   1:{(levels['reward_2']/levels['risk']):.2f}")
    print()

    print(f"📋 RECOMMENDATION FOR POLYMARKET")
    print("─" * 96)
    if signal["direction"] == "UP":
        print(f"  🟢 BUY 'YES' on Polymarket Bitcoin UP contract")
        print(f"     Entry: Current market price")
        print(f"     Position Size: {(100 - signal['confidence']) / 5:.1f}% of bankroll")
        print(f"     Target: Price continues UP over next 5 minutes")
        print(f"     Exit: At TP1 (take 50%) or TP2 (take remaining) or SL")
    elif signal["direction"] == "DOWN":
        print(f"  🔴 BUY 'NO' on Polymarket Bitcoin DOWN contract")
        print(f"     Entry: Current market price")
        print(f"     Position Size: {(100 - signal['confidence']) / 5:.1f}% of bankroll")
        print(f"     Target: Price continues DOWN over next 5 minutes")
        print(f"     Exit: At TP1 (take 50%) or TP2 (take remaining) or SL")
    else:
        print(f"  🟡 NEUTRAL - NO CLEAR SIGNAL")
        print(f"     Better opportunities likely coming soon")
        print(f"     Monitor for clearer setup")

    print()
    print("⚠️  RISK DISCLAIMER")
    print("─" * 96)
    print("  This is a 5-minute prediction. Bitcoin can be highly volatile.")
    print("  Only risk 1-2% of your bankroll per trade.")
    print("  Use stop losses ALWAYS.")
    print("  This is NOT financial advice.")
    print()


if __name__ == "__main__":
    print("\n" + "=" * 96)
    print("🤖 BTC UP/DOWN 5-MINUTE SIGNAL GENERATOR")
    print("=" * 96)

    # Generate BTC data
    print("\n[1/3] Generating realistic BTC 5-minute chart data...")
    generator = BTC5MinSignalGenerator()
    df = generator.generate_realistic_btc_5min(n_bars=200)
    print(f"✅ Generated {len(df)} 5-minute bars")
    print(f"   Price range: ${df['close'].min():,.0f} - ${df['close'].max():,.0f}")

    # Generate signal
    print("\n[2/3] Analyzing chart and generating signal...")
    signal = generator.generate_signal(df)
    print(f"✅ Signal generated: {signal['direction']} with {signal['confidence']:.0f}% confidence")

    # Print signal
    print("\n[3/3] Formatting signal report...")
    print_btc_signal(signal)

    # Export to JSON
    export_data = {
        "timestamp": signal["timestamp"],
        "direction": signal["direction"],
        "confidence": signal["confidence"],
        "current_price": signal["current_price"],
        "entry": signal["trading_levels"]["entry"],
        "stop_loss": signal["trading_levels"]["stop_loss"],
        "take_profit_1": signal["trading_levels"]["take_profit_1"],
        "take_profit_2": signal["trading_levels"]["take_profit_2"],
        "polymarket_action": f"BUY {'YES (UP)' if signal['direction'] == 'UP' else 'NO (DOWN)'}"
    }

    with open("btc_5min_signal.json", "w") as f:
        json.dump(export_data, f, indent=2)

    print("✅ Signal exported to btc_5min_signal.json\n")
