"""
NAS100 Scalp Trade Analysis Tool
Real-time chart analysis for scalping setups with TradingView data
"""

import pandas as pd
import numpy as np
from datetime import datetime
import json
from typing import Dict, List, Tuple

# ============================================================================
# FAST SCALP INDICATORS
# ============================================================================

def ema(series, period):
    """Exponential Moving Average"""
    return series.ewm(span=period, adjust=False).mean()

def sma(series, period):
    """Simple Moving Average"""
    return series.rolling(period).mean()

def rsi(series, period=14):
    """Relative Strength Index - key for scalping"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    """Average True Range - for dynamic stops"""
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def macd(series, fast=12, slow=26, signal=9):
    """MACD - momentum confirmation"""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def stochastic(df, k_period=14, d_period=3):
    """Stochastic Oscillator - fast momentum"""
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k_percent = 100 * (df["close"] - low_min) / (high_max - low_min)
    d_percent = k_percent.rolling(d_period).mean()
    return k_percent, d_percent

def bollinger_bands(series, period=20, mult=2.0):
    """Bollinger Bands for breakout setups"""
    basis = sma(series, period)
    std = series.rolling(period).std()
    upper = basis + mult * std
    lower = basis - mult * std
    return basis, upper, lower, (upper - lower)

def support_resistance(df, lookback=20):
    """Find local support and resistance levels"""
    high = df["high"].rolling(lookback, center=True).max()
    low = df["low"].rolling(lookback, center=True).min()

    # Identify pivot points
    is_support = (df["low"] == low).astype(int)
    is_resistance = (df["high"] == high).astype(int)

    return is_support, is_resistance, low, high

def detect_breakout(df, basis, upper_band, lower_band, last_n=5):
    """Detect breakout trading opportunities"""
    close = df["close"]

    # Breakout up (close above upper band)
    breakout_up = (close > upper_band) & (close.shift(1) <= upper_band)

    # Breakout down (close below lower band)
    breakout_down = (close < lower_band) & (close.shift(1) >= lower_band)

    return breakout_up, breakout_down

def detect_pullback(df, fast_ema, slow_ema):
    """Detect pullback trading opportunities"""
    close = df["close"]

    # Pullback to fast EMA during uptrend
    pullback_buy = (close < fast_ema) & (fast_ema > slow_ema) & (close.shift(1) >= fast_ema.shift(1))

    # Pullback to fast EMA during downtrend
    pullback_sell = (close > fast_ema) & (fast_ema < slow_ema) & (close.shift(1) <= fast_ema.shift(1))

    return pullback_buy, pullback_sell

# ============================================================================
# SCALP SETUP ANALYZER
# ============================================================================

class NAS100ScalpAnalyzer:
    """Analyze NAS100 chart for scalp trading setups"""

    def __init__(self, df: pd.DataFrame, timeframe="1min"):
        """
        Initialize analyzer

        Args:
            df: DataFrame with OHLCV data (timestamp, open, high, low, close, volume)
            timeframe: "1min" or "5min"
        """
        self.df = df.copy()
        self.timeframe = timeframe
        self.current_price = df["close"].iloc[-1]
        self.current_time = df.index[-1] if hasattr(df.index, '__iter__') else datetime.now()

    def analyze(self) -> Dict:
        """Run complete analysis and return setups"""
        df = self.df

        # Calculate indicators
        fast_ema = ema(df["close"], 9)
        slow_ema = ema(df["close"], 21)
        rsi_val = rsi(df["close"], 14)
        atr_val = atr(df, 14)
        macd_line, signal_line, macd_hist = macd(df["close"])
        k_percent, d_percent = stochastic(df, 14, 3)
        bb_basis, bb_upper, bb_lower, bb_width = bollinger_bands(df["close"], 20, 2.0)

        # Current values
        current_close = df["close"].iloc[-1]
        current_high = df["high"].iloc[-1]
        current_low = df["low"].iloc[-1]
        current_atr = atr_val.iloc[-1]
        current_rsi = rsi_val.iloc[-1]
        current_k = k_percent.iloc[-1]
        current_d = d_percent.iloc[-1]
        current_macd_hist = macd_hist.iloc[-1]

        # Trend analysis
        trend = self._analyze_trend(fast_ema, slow_ema, current_close)

        # Detect setups
        setups = []

        # 1. BREAKOUT SETUP
        breakout_up, breakout_down = detect_breakout(df, bb_basis, bb_upper, bb_lower)
        if breakout_up.iloc[-1]:
            setup = self._create_breakout_setup(
                "LONG", "Breakout", current_close, current_atr,
                bb_upper.iloc[-1], current_rsi, current_k
            )
            setups.append(setup)

        if breakout_down.iloc[-1]:
            setup = self._create_breakout_setup(
                "SHORT", "Breakout", current_close, current_atr,
                bb_lower.iloc[-1], current_rsi, current_k
            )
            setups.append(setup)

        # 2. PULLBACK SETUP
        pullback_buy, pullback_sell = detect_pullback(df, fast_ema, slow_ema)
        if pullback_buy.iloc[-1]:
            setup = self._create_pullback_setup(
                "LONG", "Pullback", current_close, current_atr,
                fast_ema.iloc[-1], slow_ema.iloc[-1], current_rsi
            )
            setups.append(setup)

        if pullback_sell.iloc[-1]:
            setup = self._create_pullback_setup(
                "SHORT", "Pullback", current_close, current_atr,
                fast_ema.iloc[-1], slow_ema.iloc[-1], current_rsi
            )
            setups.append(setup)

        # 3. MEAN REVERSION SETUP (RSI extremes)
        mr_setup = self._detect_mean_reversion(
            current_close, current_atr, current_rsi, current_k,
            bb_lower.iloc[-1], bb_upper.iloc[-1]
        )
        if mr_setup:
            setups.append(mr_setup)

        # 4. MOMENTUM SETUP (MACD + Stoch confirmation)
        mom_setup = self._detect_momentum(
            current_close, current_atr, current_macd_hist, current_k, current_d
        )
        if mom_setup:
            setups.append(mom_setup)

        # Analyze each setup
        analyzed_setups = []
        for setup in setups:
            setup["quality_score"] = self._score_setup(setup, current_rsi, current_k, trend)
            analyzed_setups.append(setup)

        # Sort by quality score
        analyzed_setups.sort(key=lambda x: x["quality_score"], reverse=True)

        return {
            "timestamp": str(self.current_time),
            "price": current_close,
            "atr": current_atr,
            "trend": trend,
            "rsi": current_rsi,
            "stoch_k": current_k,
            "macd_hist": current_macd_hist,
            "setups": analyzed_setups,
            "indicators": {
                "ema_9": fast_ema.iloc[-1],
                "ema_21": slow_ema.iloc[-1],
                "bb_upper": bb_upper.iloc[-1],
                "bb_lower": bb_lower.iloc[-1],
                "bb_width": bb_width.iloc[-1]
            }
        }

    def _analyze_trend(self, fast_ema, slow_ema, current_close):
        """Determine current trend"""
        if current_close > fast_ema.iloc[-1] > slow_ema.iloc[-1]:
            return "STRONG UPTREND"
        elif current_close > slow_ema.iloc[-1] > fast_ema.iloc[-1]:
            return "WEAK UPTREND"
        elif current_close < fast_ema.iloc[-1] < slow_ema.iloc[-1]:
            return "STRONG DOWNTREND"
        elif current_close < slow_ema.iloc[-1] < fast_ema.iloc[-1]:
            return "WEAK DOWNTREND"
        else:
            return "CONSOLIDATION"

    def _create_breakout_setup(self, direction, setup_type, price, atr, breakout_level, rsi, stoch_k):
        """Create breakout setup"""
        if direction == "LONG":
            entry = breakout_level + 0.5
            stop_loss = breakout_level - atr * 0.8
            take_profit = price + atr * 2.0
        else:
            entry = breakout_level - 0.5
            stop_loss = breakout_level + atr * 0.8
            take_profit = price - atr * 2.0

        return {
            "direction": direction,
            "setup_type": setup_type,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": abs(entry - stop_loss),
            "reward": abs(take_profit - entry),
            "rr_ratio": abs(take_profit - entry) / abs(entry - stop_loss) if abs(entry - stop_loss) > 0 else 0,
            "rsi": rsi,
            "stoch_k": stoch_k
        }

    def _create_pullback_setup(self, direction, setup_type, price, atr, fast_ema, slow_ema, rsi):
        """Create pullback setup"""
        if direction == "LONG":
            entry = fast_ema
            stop_loss = fast_ema - atr * 0.6
            take_profit = fast_ema + atr * 2.5
        else:
            entry = fast_ema
            stop_loss = fast_ema + atr * 0.6
            take_profit = fast_ema - atr * 2.5

        return {
            "direction": direction,
            "setup_type": setup_type,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk": abs(entry - stop_loss),
            "reward": abs(take_profit - entry),
            "rr_ratio": abs(take_profit - entry) / abs(entry - stop_loss) if abs(entry - stop_loss) > 0 else 0,
            "rsi": rsi,
            "pullback_zone": (fast_ema - atr * 0.3, fast_ema + atr * 0.3)
        }

    def _detect_mean_reversion(self, price, atr, rsi, stoch_k, bb_lower, bb_upper):
        """Detect mean reversion opportunities"""
        setup = None

        # Oversold condition
        if rsi < 30 and stoch_k < 20:
            setup = {
                "direction": "LONG",
                "setup_type": "Mean Reversion",
                "entry": price,
                "stop_loss": price - atr * 1.0,
                "take_profit": price + atr * 1.5,
                "risk": atr * 1.0,
                "reward": atr * 1.5,
                "rr_ratio": 1.5,
                "rsi": rsi,
                "stoch_k": stoch_k,
                "condition": "Oversold (RSI<30, K<20)"
            }

        # Overbought condition
        elif rsi > 70 and stoch_k > 80:
            setup = {
                "direction": "SHORT",
                "setup_type": "Mean Reversion",
                "entry": price,
                "stop_loss": price + atr * 1.0,
                "take_profit": price - atr * 1.5,
                "risk": atr * 1.0,
                "reward": atr * 1.5,
                "rr_ratio": 1.5,
                "rsi": rsi,
                "stoch_k": stoch_k,
                "condition": "Overbought (RSI>70, K>80)"
            }

        return setup

    def _detect_momentum(self, price, atr, macd_hist, stoch_k, stoch_d):
        """Detect momentum setups"""
        setup = None

        # Bullish momentum
        if macd_hist > 0 and stoch_k > stoch_d and stoch_k < 80:
            setup = {
                "direction": "LONG",
                "setup_type": "Momentum",
                "entry": price,
                "stop_loss": price - atr * 0.9,
                "take_profit": price + atr * 2.0,
                "risk": atr * 0.9,
                "reward": atr * 2.0,
                "rr_ratio": 2.0 / 0.9,
                "stoch_k": stoch_k,
                "condition": "MACD>0, K>D crossover"
            }

        # Bearish momentum
        elif macd_hist < 0 and stoch_k < stoch_d and stoch_k > 20:
            setup = {
                "direction": "SHORT",
                "setup_type": "Momentum",
                "entry": price,
                "stop_loss": price + atr * 0.9,
                "take_profit": price - atr * 2.0,
                "risk": atr * 0.9,
                "reward": atr * 2.0,
                "rr_ratio": 2.0 / 0.9,
                "stoch_k": stoch_k,
                "condition": "MACD<0, K<D crossover"
            }

        return setup

    def _score_setup(self, setup, rsi, stoch_k, trend):
        """Score setup quality (0-100)"""
        score = 50  # Base score

        # Trend alignment (+25 points)
        if setup["direction"] == "LONG":
            if "UPTREND" in trend:
                score += 25
            elif "DOWNTREND" in trend:
                score -= 15
        else:
            if "DOWNTREND" in trend:
                score += 25
            elif "UPTREND" in trend:
                score -= 15

        # R:R ratio (+15 points if > 1.5)
        if setup["rr_ratio"] > 1.5:
            score += 15
        elif setup["rr_ratio"] > 1.0:
            score += 8
        else:
            score -= 10

        # RSI not in extreme (-10 points if extreme)
        if rsi > 70 or rsi < 30:
            score -= 10
        else:
            score += 5

        # Setup type bonus
        setup_bonuses = {
            "Momentum": 10,
            "Mean Reversion": 5,
            "Pullback": 8,
            "Breakout": 12
        }
        score += setup_bonuses.get(setup.get("setup_type"), 0)

        return max(0, min(100, score))


# ============================================================================
# FORMATTING & DISPLAY
# ============================================================================

def format_setup_report(analysis: Dict) -> str:
    """Format analysis results for display"""
    report = f"""
╔════════════════════════════════════════════════════════════════════════════════╗
║                    NAS100 SCALP ANALYSIS REPORT                               ║
╚════════════════════════════════════════════════════════════════════════════════╝

📊 CHART STATUS ({analysis['timestamp']})
  Current Price:    {analysis['price']:.2f}
  ATR (14):         {analysis['atr']:.2f}
  Trend:            {analysis['trend']}
  RSI (14):         {analysis['rsi']:.1f}
  Stoch K:          {analysis['stoch_k']:.1f}
  MACD Histogram:   {analysis['macd_hist']:.6f}

┌────────────────────────────────────────────────────────────────────────────────┐
│ ACTIVE SETUPS                                                                  │
└────────────────────────────────────────────────────────────────────────────────┘
"""

    if not analysis["setups"]:
        report += "\n  ⏸️  No active setups at this moment\n"
    else:
        for i, setup in enumerate(analysis["setups"], 1):
            report += f"""
  [{i}] {setup['direction']} - {setup['setup_type']} (Score: {setup['quality_score']:.0f}/100)
  ─────────────────────────────────────────────────────
      Entry:           {setup['entry']:.2f}
      Stop Loss:       {setup['stop_loss']:.2f}
      Take Profit:     {setup['take_profit']:.2f}
      Risk:            {setup['risk']:.2f}
      Reward:          {setup['reward']:.2f}
      Risk/Reward:     1:{setup['rr_ratio']:.2f}
"""
            if "condition" in setup:
                report += f"      Condition:      {setup['condition']}\n"
            if "pullback_zone" in setup:
                low, high = setup["pullback_zone"]
                report += f"      Pullback Zone:  {low:.2f} - {high:.2f}\n"

    report += f"""
┌────────────────────────────────────────────────────────────────────────────────┐
│ KEY LEVELS                                                                     │
└────────────────────────────────────────────────────────────────────────────────┘

  EMA 9:            {analysis['indicators']['ema_9']:.2f}
  EMA 21:           {analysis['indicators']['ema_21']:.2f}
  BB Upper:         {analysis['indicators']['bb_upper']:.2f}
  BB Lower:         {analysis['indicators']['bb_lower']:.2f}
  BB Width:         {analysis['indicators']['bb_width']:.2f}

╔════════════════════════════════════════════════════════════════════════════════╗
"""

    return report


def print_quick_summary(analysis: Dict):
    """Print quick summary for terminal"""
    print(format_setup_report(analysis))


if __name__ == "__main__":
    print("NAS100 Scalp Analyzer Tool loaded successfully!")
    print("Use: analyzer = NAS100ScalpAnalyzer(df)")
    print("     analysis = analyzer.analyze()")
    print("     print_quick_summary(analysis)")
