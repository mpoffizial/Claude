"""
Polymarket Trading Bot
Generates trading signals based on probability shifts and opportunities
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from polymarket_analyzer import PolymarketAnalyzer
import json


class PolymarketTradingBot:
    """Generate trading signals from Polymarket probability markets"""

    def __init__(self):
        self.analyzer = PolymarketAnalyzer()
        self.price_history = {}  # Track price history
        self.signals = []

    def analyze_probability_shift(self, market: Dict, historical_data: Optional[List[Dict]] = None) -> Dict:
        """
        Detect trading opportunities from probability shifts

        Args:
            market: Current market data
            historical_data: Previous price data for comparison

        Returns:
            Trading signal with direction and strength
        """
        signal = {
            "market_id": market.get("id"),
            "question": market.get("question"),
            "timestamp": datetime.now().isoformat(),
            "opportunities": [],
            "recommendation": "HOLD",
            "confidence": 0
        }

        outcomes = market.get("outcomes", [])
        current_prices = {o.get("label"): float(o.get("price", 0)) for o in outcomes}

        # Analyze each outcome
        for outcome in outcomes:
            label = outcome.get("label")
            current_price = float(outcome.get("price", 0))
            current_prob = current_price * 100

            # Signal 1: Extreme moves (>80% or <20%)
            if current_prob > 80:
                signal["opportunities"].append({
                    "type": "EXTREME_HIGH_PROBABILITY",
                    "outcome": label,
                    "probability": current_prob,
                    "trade": "SHORT (Probability likely to decrease)",
                    "reasoning": "Market may be overconfident. Look for reversion.",
                    "edge": current_prob - 75  # Edge = deviation from 75%
                })

            elif current_prob < 20:
                signal["opportunities"].append({
                    "type": "EXTREME_LOW_PROBABILITY",
                    "outcome": label,
                    "probability": current_prob,
                    "trade": "LONG (Probability likely to increase)",
                    "reasoning": "Market may be underestimating possibility. Look for rally.",
                    "edge": 25 - current_prob  # Edge = deviation from 25%
                })

            # Signal 2: Mid-range with momentum (probability changes)
            if historical_data:
                old_price = next((o.get("price", current_price) for o in historical_data if o.get("label") == label), current_price)
                price_change = current_price - float(old_price)

                if abs(price_change) > 0.05:  # >5% change
                    direction = "UP" if price_change > 0 else "DOWN"
                    signal["opportunities"].append({
                        "type": "MOMENTUM_SHIFT",
                        "outcome": label,
                        "probability": current_prob,
                        "price_change": price_change * 100,
                        "direction": direction,
                        "trade": "LONG" if price_change > 0 else "SHORT",
                        "reasoning": f"Probability {direction} {abs(price_change)*100:.1f}% - momentum trade",
                        "edge": abs(price_change) * 100
                    })

            # Signal 3: Risk/Reward analysis
            if 30 < current_prob < 70:  # Good probability zones
                if current_prob > 50:
                    reward = current_prob - 50  # Distance from 50%
                    signal["opportunities"].append({
                        "type": "GOOD_RISK_REWARD",
                        "outcome": label,
                        "probability": current_prob,
                        "risk": 50,  # Risk to lose against 50%
                        "reward": reward,
                        "ratio": reward / 50 if reward > 0 else 0,
                        "trade": "LONG (Good odds)",
                        "edge": reward
                    })

        # Set recommendation
        if signal["opportunities"]:
            # Count bullish and bearish signals
            long_signals = [o for o in signal["opportunities"] if "LONG" in o.get("trade", "")]
            short_signals = [o for o in signal["opportunities"] if "SHORT" in o.get("trade", "")]

            if len(long_signals) > len(short_signals):
                signal["recommendation"] = "BUY"
                signal["confidence"] = min(100, len(long_signals) * 25)
            elif len(short_signals) > len(long_signals):
                signal["recommendation"] = "SELL"
                signal["confidence"] = min(100, len(short_signals) * 25)
            else:
                signal["recommendation"] = "MIXED"
                signal["confidence"] = 50

        return signal

    def scan_all_markets(self, limit: int = 100) -> List[Dict]:
        """
        Scan all markets for trading opportunities

        Args:
            limit: Number of markets to scan

        Returns:
            List of signals ranked by opportunity
        """
        print(f"[Scanning] Fetching top {limit} markets...")
        markets = self.analyzer.get_markets(limit=limit)

        if not markets:
            print("❌ Could not fetch markets")
            return []

        signals = []
        for market in markets:
            signal = self.analyze_probability_shift(market)
            if signal["opportunities"]:
                signals.append(signal)

        # Sort by highest edge opportunities
        signals.sort(key=lambda s: max([o.get("edge", 0) for o in s["opportunities"]], default=0),
                    reverse=True)

        return signals[:10]  # Return top 10

    def identify_edge_opportunities(self, markets: List[Dict]) -> List[Dict]:
        """
        Find markets with real trading edges

        Args:
            markets: List of markets to analyze

        Returns:
            High-edge opportunities
        """
        opportunities = []

        for market in markets:
            analysis = self.analyzer.analyze_market_opportunity(market)

            if analysis["opportunity_score"] > 60:
                # Check for arbitrage
                outcomes = market.get("outcomes", [])
                prices = [float(o.get("price", 0)) for o in outcomes]

                # Probability sum should equal 1.0 for binary markets
                prob_sum = sum(prices)
                edge = (1.0 - prob_sum) * 100  # Percentage edge

                if abs(edge) > 1:  # More than 1% edge
                    opportunities.append({
                        "market_id": market.get("id"),
                        "question": market.get("question"),
                        "edge_pct": edge,
                        "strategy": "ARBITRAGE" if edge < 0 else "CONTRARIAN",
                        "confidence": min(100, analysis["opportunity_score"] + abs(edge) * 10),
                        "liquidity": analysis["liquidity"]
                    })

        return sorted(opportunities, key=lambda o: o["confidence"], reverse=True)

    def generate_portfolio_signal(self, signals: List[Dict]) -> Dict:
        """
        Generate overall portfolio signal from multiple markets

        Args:
            signals: List of individual market signals

        Returns:
            Portfolio-level signal
        """
        if not signals:
            return {"status": "NO_SIGNALS", "recommendation": "HOLD"}

        # Aggregate signals
        long_count = len([s for s in signals if s["recommendation"] == "BUY"])
        short_count = len([s for s in signals if s["recommendation"] == "SELL"])
        mixed_count = len([s for s in signals if s["recommendation"] == "MIXED"])

        avg_confidence = np.mean([s["confidence"] for s in signals])

        if long_count > short_count + mixed_count:
            overall = "LONG_BIAS"
        elif short_count > long_count + mixed_count:
            overall = "SHORT_BIAS"
        else:
            overall = "NEUTRAL"

        return {
            "overall_signal": overall,
            "long_signals": long_count,
            "short_signals": short_count,
            "mixed_signals": mixed_count,
            "average_confidence": avg_confidence,
            "total_signals": len(signals),
            "recommendation": "BUY" if long_count >= 2 else "SELL" if short_count >= 2 else "HOLD"
        }


def print_trading_signal(signal: Dict):
    """Format and print trading signal"""
    print(f"\n┌─ TRADING SIGNAL ─────────────────────────────────────────────────────────┐")
    print(f"│  Market:      {signal['question'][:60]}...")
    print(f"│  Signal:      {signal['recommendation']} (Confidence: {signal['confidence']:.0f}%)")
    print(f"│  ─────────────────────────────────────────────────────────────────────────")

    for i, opp in enumerate(signal["opportunities"][:3], 1):  # Show top 3
        print(f"│  [{i}] {opp['type']}")
        print(f"│      Outcome:  {opp['outcome']}")
        print(f"│      Prob:     {opp.get('probability', 'N/A'):.1f}%")
        print(f"│      Trade:    {opp.get('trade', 'N/A')}")
        print(f"│      Edge:     {opp.get('edge', 0):.2f}%")

    print(f"└─────────────────────────────────────────────────────────────────────────┘")


def print_portfolio_signal(portfolio_signal: Dict):
    """Print portfolio-level analysis"""
    print(f"\n╔═══════════════════════════════════════════════════════════════════════════╗")
    print(f"║                   📊 POLYMARKET PORTFOLIO SIGNAL                          ║")
    print(f"╚═══════════════════════════════════════════════════════════════════════════╝")
    print(f"\nOverall Signal:       {portfolio_signal['overall_signal']}")
    print(f"Recommendation:       {portfolio_signal['recommendation']}")
    print(f"Average Confidence:   {portfolio_signal['average_confidence']:.1f}%")
    print(f"─────────────────────────────────────────────────────────────────────────")
    print(f"Long Signals:         {portfolio_signal['long_signals']}")
    print(f"Short Signals:        {portfolio_signal['short_signals']}")
    print(f"Mixed Signals:        {portfolio_signal['mixed_signals']}")
    print(f"Total Signals:        {portfolio_signal['total_signals']}")
    print()


if __name__ == "__main__":
    print("\n" + "="*80)
    print("🤖 POLYMARKET TRADING BOT")
    print("="*80)

    bot = PolymarketTradingBot()

    # Scan for opportunities
    print("\n[1/3] Scanning Polymarket for trading opportunities...")
    signals = bot.scan_all_markets(limit=100)

    if signals:
        print(f"✅ Found {len(signals)} trading signals\n")

        # Show top signals
        for i, signal in enumerate(signals[:5], 1):
            print(f"\n--- SIGNAL #{i} ---")
            print_trading_signal(signal)

        # Portfolio-level analysis
        print("\n[2/3] Aggregating signals...")
        portfolio_signal = bot.generate_portfolio_signal(signals)
        print_portfolio_signal(portfolio_signal)

        # Export to JSON
        export_data = {
            "timestamp": datetime.now().isoformat(),
            "portfolio_signal": portfolio_signal,
            "top_signals": []
        }

        for signal in signals[:5]:
            export_data["top_signals"].append({
                "market_id": signal["market_id"],
                "question": signal["question"],
                "recommendation": signal["recommendation"],
                "confidence": signal["confidence"],
                "opportunities_count": len(signal["opportunities"])
            })

        with open("polymarket_signals.json", "w") as f:
            json.dump(export_data, f, indent=2)

        print("[3/3] ✅ Signals exported to polymarket_signals.json")

    else:
        print("⚠️  No trading signals found at this moment")
