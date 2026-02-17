#!/usr/bin/env python3
"""
NAS100 Scalp Trade Setup Analyzer
Main script to analyze charts and generate trading setups
"""

import sys
import json
from datetime import datetime

from nas100_connector import get_nas100_data
from nas100_scalp_analyzer import NAS100ScalpAnalyzer, format_setup_report


def analyze_nas100_live(interval="1", n_bars=100):
    """
    Fetch and analyze NAS100 chart for scalp setups

    Args:
        interval: "1" (1min), "5" (5min), "15" (15min)
        n_bars: Number of bars to fetch (100-500)
    """
    print("=" * 80)
    print(f"NAS100 SCALP ANALYSIS - {interval}min Chart")
    print("=" * 80)
    print()

    # 1. Fetch data from TradingView
    print("[1/3] Fetching NAS100 data from TradingView...")
    df = get_nas100_data(symbol="NQ1!", interval=interval, n_bars=n_bars)

    if df is None or len(df) < 20:
        print("❌ Error: Could not fetch sufficient data")
        return

    print(f"✓ Fetched {len(df)} bars")
    print(f"  Price range: {df['close'].min():.2f} - {df['close'].max():.2f}")
    print()

    # 2. Analyze chart
    print("[2/3] Analyzing chart for scalp setups...")
    analyzer = NAS100ScalpAnalyzer(df, timeframe=f"{interval}min")
    analysis = analyzer.analyze()
    print(f"✓ Found {len(analysis['setups'])} active setups")
    print()

    # 3. Print report
    print("[3/3] Generating report...")
    print(format_setup_report(analysis))

    return analysis


def export_setups_json(analysis, filename="nas100_setups.json"):
    """Export analysis to JSON file"""
    export_data = {
        "timestamp": analysis["timestamp"],
        "price": analysis["price"],
        "atr": analysis["atr"],
        "trend": analysis["trend"],
        "indicators": analysis["indicators"],
        "setups": []
    }

    for setup in analysis["setups"]:
        setup_export = {
            "direction": setup["direction"],
            "type": setup["setup_type"],
            "quality_score": setup["quality_score"],
            "entry": setup["entry"],
            "stop_loss": setup["stop_loss"],
            "take_profit": setup["take_profit"],
            "risk": setup["risk"],
            "reward": setup["reward"],
            "rr_ratio": setup["rr_ratio"]
        }
        export_data["setups"].append(setup_export)

    with open(filename, "w") as f:
        json.dump(export_data, f, indent=2)

    print(f"✓ Exported {len(analysis['setups'])} setups to {filename}")


if __name__ == "__main__":
    # Default: 1-minute chart with 100 bars
    interval = sys.argv[1] if len(sys.argv) > 1 else "1"
    n_bars = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    print()
    analysis = analyze_nas100_live(interval=interval, n_bars=n_bars)

    if analysis:
        # Optionally export to JSON
        export_setups_json(analysis)
        print()
        print("✓ Analysis complete!")

        # Summary
        if analysis["setups"]:
            best_setup = analysis["setups"][0]
            print(f"\n🎯 TOP SETUP: {best_setup['direction']} {best_setup['setup_type']} (Score: {best_setup['quality_score']:.0f}/100)")
            print(f"   Entry: {best_setup['entry']:.2f} | SL: {best_setup['stop_loss']:.2f} | TP: {best_setup['take_profit']:.2f}")
        else:
            print("\n⏸️  No active setups at this moment")
