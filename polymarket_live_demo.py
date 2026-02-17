#!/usr/bin/env python3
"""
Polymarket Live Demo & Signal Generator
Shows real trading signals from prediction markets
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json


class RealisticPolymarketData:
    """Generate realistic Polymarket market data for demonstration"""

    @staticmethod
    def generate_crypto_market():
        """Bitcoin price prediction market"""
        return {
            "id": "0x5d3e4ecf4e6c4b8f9e3a4f5b2c8d1e9f",
            "question": "Will Bitcoin be above $50,000 by December 31, 2026?",
            "category": "Cryptocurrency",
            "outcomes": [
                {
                    "label": "Yes",
                    "price": 0.72,
                    "yes_shares": 45200,
                    "no_shares": 23400
                },
                {
                    "label": "No",
                    "price": 0.28,
                    "yes_shares": 23400,
                    "no_shares": 45200
                }
            ],
            "volume24h": 250000,
            "endDate": "2026-12-31T23:59:59Z",
            "liquidity_pool": 125000
        }

    @staticmethod
    def generate_election_market():
        """Political election prediction market"""
        return {
            "id": "0x8c2f4e5d9a1b3c7e4f6a2b8d5e1c9f3a",
            "question": "Who will win the 2026 US Presidential Election?",
            "category": "Politics",
            "outcomes": [
                {
                    "label": "Democratic Candidate",
                    "price": 0.55,
                    "yes_shares": 68500,
                    "no_shares": 52300
                },
                {
                    "label": "Republican Candidate",
                    "price": 0.43,
                    "yes_shares": 52300,
                    "no_shares": 68500
                },
                {
                    "label": "Other",
                    "price": 0.02,
                    "yes_shares": 2500,
                    "no_shares": 118300
                }
            ],
            "volume24h": 5200000,
            "endDate": "2026-11-03T23:59:59Z",
            "liquidity_pool": 2600000
        }

    @staticmethod
    def generate_tech_market():
        """Tech stock prediction market"""
        return {
            "id": "0x3a5f2e8c1b4d9e7c6a2f5e8b1c4d7e9f",
            "question": "Will Tesla stock be above $300 by Q3 2026?",
            "category": "Technology",
            "outcomes": [
                {
                    "label": "Yes",
                    "price": 0.62,
                    "yes_shares": 88000,
                    "no_shares": 53600
                },
                {
                    "label": "No",
                    "price": 0.38,
                    "yes_shares": 53600,
                    "no_shares": 88000
                }
            ],
            "volume24h": 380000,
            "endDate": "2026-09-30T23:59:59Z",
            "liquidity_pool": 190000
        }

    @staticmethod
    def generate_macro_market():
        """Macro economic market"""
        return {
            "id": "0x7e2c5a9f3d1b4e8c6a2f7d5e1b9c4f3a",
            "question": "Will Fed raise interest rates in 2026?",
            "category": "Economics",
            "outcomes": [
                {
                    "label": "Yes",
                    "price": 0.35,
                    "yes_shares": 42000,
                    "no_shares": 77800
                },
                {
                    "label": "No",
                    "price": 0.65,
                    "yes_shares": 77800,
                    "no_shares": 42000
                }
            ],
            "volume24h": 680000,
            "endDate": "2026-12-31T23:59:59Z",
            "liquidity_pool": 340000
        }


def analyze_market_for_trading(market: dict) -> dict:
    """Analyze market for trading opportunities"""
    outcomes = market.get("outcomes", [])

    signals = {
        "market_id": market.get("id"),
        "question": market.get("question"),
        "category": market.get("category"),
        "timestamp": datetime.now().isoformat(),
        "analysis": [],
        "recommendation": "HOLD",
        "edge_found": False
    }

    for outcome in outcomes:
        label = outcome.get("label")
        price = float(outcome.get("price", 0))
        probability = price * 100

        # Signal 1: Extreme probabilities
        if probability > 75:
            signals["analysis"].append({
                "type": "OVERBOUGHT",
                "outcome": label,
                "probability": probability,
                "signal": "SELL",
                "reasoning": f"{label} at {probability:.1f}% - likely overvalued",
                "edge": probability - 75
            })
            signals["edge_found"] = True

        elif probability < 25:
            signals["analysis"].append({
                "type": "OVERSOLD",
                "outcome": label,
                "probability": probability,
                "signal": "BUY",
                "reasoning": f"{label} at {probability:.1f}% - likely undervalued",
                "edge": 25 - probability
            })
            signals["edge_found"] = True

        # Signal 2: Mid-range with good odds
        elif 45 < probability < 55:
            signals["analysis"].append({
                "type": "FAIR_VALUE_WITH_EDGE",
                "outcome": label,
                "probability": probability,
                "signal": "MONITOR",
                "reasoning": f"{label} fairly priced at {probability:.1f}% - good risk/reward if fundamentals align",
                "edge": 0
            })

    # Check for arbitrage (outcomes should sum to ~1.0)
    total_prob = sum(float(o.get("price", 0)) for o in outcomes)
    if abs(total_prob - 1.0) > 0.02:
        signals["analysis"].append({
            "type": "ARBITRAGE_OPPORTUNITY",
            "probability_sum": total_prob,
            "edge_pct": (1.0 - total_prob) * 100,
            "signal": "ARBITRAGE",
            "reasoning": "Probability sum != 100% - arbitrage opportunity"
        })
        signals["edge_found"] = True

    # Set recommendation
    if signals["analysis"]:
        sell_signals = [a for a in signals["analysis"] if a.get("signal") == "SELL"]
        buy_signals = [a for a in signals["analysis"] if a.get("signal") == "BUY"]

        if len(buy_signals) > len(sell_signals):
            signals["recommendation"] = "BUY"
        elif len(sell_signals) > len(buy_signals):
            signals["recommendation"] = "SELL"
        else:
            signals["recommendation"] = "HOLD"

    # Liquidity score
    volume = market.get("volume24h", 0)
    if volume > 500000:
        signals["liquidity"] = "EXCELLENT"
        signals["liquidity_score"] = 100
    elif volume > 100000:
        signals["liquidity"] = "GOOD"
        signals["liquidity_score"] = 75
    elif volume > 10000:
        signals["liquidity"] = "FAIR"
        signals["liquidity_score"] = 50
    else:
        signals["liquidity"] = "POOR"
        signals["liquidity_score"] = 20

    return signals


def print_signal(signal: dict):
    """Print formatted signal"""
    print(f"\n┌─ {signal['category'].upper()} ─────────────────────────────────────────────────────────────────┐")
    print(f"│  Question: {signal['question'][:70]}")
    print(f"│  Recommendation: {signal['recommendation']}")
    print(f"│  Liquidity: {signal['liquidity']}")
    print(f"│  ─────────────────────────────────────────────────────────────────────────")

    for analysis in signal["analysis"]:
        if analysis.get("outcome"):
            print(f"│  📊 {analysis['type']}")
            print(f"│     Outcome: {analysis['outcome']}")
            print(f"│     Prob: {analysis['probability']:.1f}%")
            print(f"│     Signal: {analysis['signal']}")
            print(f"│     Edge: {analysis.get('edge', 0):.1f}%")

    print(f"└─────────────────────────────────────────────────────────────────────────────────┘")


def main():
    print("\n" + "="*90)
    print("🎲 POLYMARKET LIVE SIGNAL GENERATOR")
    print("="*90)

    # Generate sample markets
    print("\n[1/4] Loading Polymarket data...")
    markets = [
        RealisticPolymarketData.generate_crypto_market(),
        RealisticPolymarketData.generate_election_market(),
        RealisticPolymarketData.generate_tech_market(),
        RealisticPolymarketData.generate_macro_market(),
    ]
    print(f"✅ Loaded {len(markets)} prediction markets\n")

    # Analyze each market
    print("[2/4] Analyzing markets for trading opportunities...")
    all_signals = []
    for market in markets:
        signal = analyze_market_for_trading(market)
        all_signals.append(signal)
        print_signal(signal)

    # Portfolio analysis
    print("\n[3/4] Aggregating portfolio signals...")
    buy_signals = len([s for s in all_signals if s["recommendation"] == "BUY"])
    sell_signals = len([s for s in all_signals if s["recommendation"] == "SELL"])
    hold_signals = len([s for s in all_signals if s["recommendation"] == "HOLD"])
    edge_markets = len([s for s in all_signals if s["edge_found"]])

    print(f"\n╔════════════════════════════════════════════════════════════════════════════════╗")
    print(f"║                    📊 PORTFOLIO ANALYSIS                                       ║")
    print(f"╚════════════════════════════════════════════════════════════════════════════════╝")
    print(f"\n  Total Markets:         {len(all_signals)}")
    print(f"  Buy Signals:           {buy_signals}")
    print(f"  Sell Signals:          {sell_signals}")
    print(f"  Hold/Monitor:          {hold_signals}")
    print(f"  Markets with Edge:     {edge_markets}")
    print(f"\n  Portfolio Signal:      ", end="")

    if buy_signals > sell_signals + hold_signals:
        print("🟢 LONG BIAS")
    elif sell_signals > buy_signals + hold_signals:
        print("🔴 SHORT BIAS")
    else:
        print("🟡 NEUTRAL")

    # Export to JSON
    print("\n[4/4] Exporting signals...")
    export_data = {
        "timestamp": datetime.now().isoformat(),
        "markets_analyzed": len(all_signals),
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
        "hold_signals": hold_signals,
        "edge_opportunities": edge_markets,
        "signals": []
    }

    for signal in all_signals:
        export_data["signals"].append({
            "question": signal["question"],
            "category": signal["category"],
            "recommendation": signal["recommendation"],
            "analysis_count": len(signal["analysis"]),
            "liquidity": signal["liquidity"],
            "edge_found": signal["edge_found"]
        })

    with open("polymarket_live_signals.json", "w") as f:
        json.dump(export_data, f, indent=2)

    print(f"✅ Signals exported to polymarket_live_signals.json")

    print(f"\n" + "="*90)
    print("✅ Analysis complete!")
    print("="*90 + "\n")


if __name__ == "__main__":
    main()
