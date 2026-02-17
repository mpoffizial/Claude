"""
NAS100 TradingView HTTP Connector
Uses tradingview_ta library for real-time analysis (no WebSocket)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

try:
    from tradingview_ta import TA_Handler, Interval
    TRADINGVIEW_TA_AVAILABLE = True
except ImportError:
    TRADINGVIEW_TA_AVAILABLE = False
    print("⚠️  tradingview_ta not installed. Install with: pip install tradingview-ta")


class NAS100HTTPConnector:
    """Real-time NAS100 data via HTTP (TradingView TA)"""

    def __init__(self):
        if not TRADINGVIEW_TA_AVAILABLE:
            raise ImportError("tradingview_ta required. Install: pip install tradingview-ta")

    def fetch_live_analysis(self, symbol="NQ1!", exchange="CME_MINI", interval=Interval.INTERVAL_1_MINUTE):
        """
        Fetch LIVE NAS100 technical analysis from TradingView

        Returns: dict with current price, indicators, and recommendation
        """
        try:
            handler = TA_Handler(
                symbol=symbol,
                screener="cfd",
                exchange=exchange,
                interval=interval
            )
            analysis = handler.get_analysis()
            return analysis
        except Exception as e:
            print(f"❌ Error fetching analysis: {e}")
            return None

    def get_current_nas100_price(self):
        """Get current NAS100 price from TradingView"""
        try:
            analysis = self.fetch_live_analysis()
            if analysis:
                price = analysis.indicators.get("close")
                return price
            return None
        except Exception as e:
            print(f"Error: {e}")
            return None

    def get_full_indicators(self, symbol="NQ1!", interval=Interval.INTERVAL_1_MINUTE):
        """Get all TradingView indicators for NAS100"""
        try:
            handler = TA_Handler(
                symbol=symbol,
                screener="cfd",
                exchange="CME_MINI",
                interval=interval
            )
            analysis = handler.get_analysis()

            indicators = {
                "timestamp": datetime.now().isoformat(),
                "symbol": symbol,
                "price": analysis.indicators.get("close"),
                "recommendation": analysis.summary.get("RECOMMENDATION"),
                "buy_signals": analysis.summary.get("BUY"),
                "sell_signals": analysis.summary.get("SELL"),
                "neutral_signals": analysis.summary.get("NEUTRAL"),
                "indicators": {}
            }

            # Extract key indicators
            key_indicators = [
                "RSI", "STOCH.K", "STOCH.D",
                "MACD.macd", "MACD.signal",
                "EMA10", "EMA20", "EMA50", "EMA100", "EMA200",
                "BB.upper", "BB.lower", "BB.basis",
                "ADX", "CCI20",
                "Mom", "AO",
                "VWAP", "HMA", "KAMA"
            ]

            for ind in key_indicators:
                value = analysis.indicators.get(ind)
                if value is not None:
                    indicators["indicators"][ind] = value

            return indicators

        except Exception as e:
            print(f"Error fetching indicators: {e}")
            return None

    def get_recommendation_strength(self, symbol="NQ1!", interval=Interval.INTERVAL_1_MINUTE):
        """Get TradingView recommendation strength (BUY, SELL, NEUTRAL)"""
        try:
            analysis = self.fetch_live_analysis(symbol, interval=interval)
            if analysis:
                return {
                    "recommendation": analysis.summary.get("RECOMMENDATION"),
                    "buy_signals": analysis.summary.get("BUY"),
                    "sell_signals": analysis.summary.get("SELL"),
                    "neutral_signals": analysis.summary.get("NEUTRAL"),
                    "total_signals": (analysis.summary.get("BUY", 0) +
                                    analysis.summary.get("SELL", 0) +
                                    analysis.summary.get("NEUTRAL", 0))
                }
            return None
        except Exception as e:
            print(f"Error: {e}")
            return None


def print_live_analysis(symbol="NQ1!"):
    """Print live TradingView analysis for NAS100"""
    print("\n" + "="*80)
    print("🔴 LIVE NAS100 ANALYSIS (TradingView Data)")
    print("="*80 + "\n")

    connector = NAS100HTTPConnector()

    # Get current price
    price = connector.get_current_nas100_price()
    if price:
        print(f"💰 Current Price: {price:.2f}")
    else:
        print("❌ Could not fetch price")
        return

    # Get full indicators
    data = connector.get_full_indicators(symbol)

    if data:
        print(f"\n📊 LIVE INDICATORS ({data['timestamp']})")
        print("─" * 80)

        # TradingView Recommendation
        rec = data["recommendation"]
        rec_emoji = "🟢" if rec == "BUY" else "🔴" if rec == "SELL" else "🟡"
        print(f"\n{rec_emoji} TradingView Recommendation: {rec}")
        print(f"   Buy Signals:    {data['buy_signals']}")
        print(f"   Sell Signals:   {data['sell_signals']}")
        print(f"   Neutral:        {data['neutral_signals']}")

        # Key Oscillators
        print(f"\n📈 OSCILLATORS:")
        indicators = data["indicators"]
        if "RSI" in indicators:
            rsi = indicators["RSI"]
            rsi_status = "🔥 Overbought" if rsi > 70 else "❄️  Oversold" if rsi < 30 else "✅ Neutral"
            print(f"   RSI:           {rsi:.1f} {rsi_status}")
        if "STOCH.K" in indicators:
            print(f"   Stoch K:       {indicators['STOCH.K']:.1f}")
            print(f"   Stoch D:       {indicators['STOCH.D']:.1f}")

        # Moving Averages
        print(f"\n📍 MOVING AVERAGES:")
        for ema in ["EMA10", "EMA20", "EMA50", "EMA100"]:
            if ema in indicators:
                print(f"   {ema}:        {indicators[ema]:.2f}")

        # MACD
        print(f"\n📊 MACD:")
        if "MACD.macd" in indicators:
            macd = indicators["MACD.macd"]
            signal = indicators.get("MACD.signal", 0)
            macd_status = "🟢 Bullish" if macd > signal else "🔴 Bearish"
            print(f"   MACD Line:     {macd:.6f}")
            print(f"   Signal Line:   {signal:.6f}")
            print(f"   Status:        {macd_status}")

        # Bollinger Bands
        print(f"\n🎯 BOLLINGER BANDS:")
        if "BB.upper" in indicators:
            print(f"   Upper:         {indicators['BB.upper']:.2f}")
            print(f"   Middle:        {indicators['BB.basis']:.2f}")
            print(f"   Lower:         {indicators['BB.lower']:.2f}")

        # Other important indicators
        print(f"\n🔧 OTHER INDICATORS:")
        for ind in ["ADX", "CCI20", "Mom", "AO"]:
            if ind in indicators:
                print(f"   {ind}:        {indicators[ind]:.2f}")

    print("\n" + "="*80)


if __name__ == "__main__":
    # Try to print live analysis
    try:
        print_live_analysis()
    except Exception as e:
        print(f"Error: {e}")
        print("\nℹ️  Make sure websocket-client is installed:")
        print("   pip install tradingview-ta websocket-client requests")
