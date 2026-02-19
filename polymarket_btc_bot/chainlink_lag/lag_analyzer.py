#!/usr/bin/env python3
"""
Lag Data Analyzer - Processes logged lag data to determine optimal thresholds.

Reads the CSV files produced by lag_logger.py and computes:
1. Lag distribution statistics
2. Optimal threshold for given win-rate targets
3. Simulated P&L at various thresholds
4. Time-of-day patterns
5. Volatility correlation

Usage:
    python -m polymarket_btc_bot.chainlink_lag.lag_analyzer
    python -m polymarket_btc_bot.chainlink_lag.lag_analyzer --data-dir lag_data
    python -m polymarket_btc_bot.chainlink_lag.lag_analyzer --threshold 5.0
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class ResolutionRow:
    market_slug: str
    start_timestamp: int
    end_timestamp: int
    opening_price: float
    spot_at_resolution: float
    spot_direction: str
    spot_delta_from_open: float
    chainlink_at_resolution: float
    chainlink_direction: str
    chainlink_delta_from_open: float
    chainlink_round_at_resolution: int
    chainlink_last_update_age: float
    lag_delta_at_resolution: float
    max_lag_delta_last_60s: float
    avg_lag_delta_last_60s: float
    direction_match: bool
    spot_predicted_correctly: bool
    num_observations: int
    resolution_datetime_utc: str


@dataclass
class ObservationRow:
    timestamp: float
    spot_price: float
    chainlink_price: float
    lag_delta: float
    lag_delta_pct: float
    on_chain_age_seconds: float
    time_to_resolution: float
    market_slug: str


def load_resolutions(data_dir: str) -> list[ResolutionRow]:
    """Load market_resolutions.csv."""
    path = os.path.join(data_dir, "market_resolutions.csv")
    if not os.path.exists(path):
        print(f"ERROR: {path} not found. Run lag_logger first.")
        return []

    rows = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append(ResolutionRow(
                    market_slug=row["market_slug"],
                    start_timestamp=int(row["start_timestamp"]),
                    end_timestamp=int(row["end_timestamp"]),
                    opening_price=float(row["opening_price"]),
                    spot_at_resolution=float(row["spot_at_resolution"]),
                    spot_direction=row["spot_direction"],
                    spot_delta_from_open=float(row["spot_delta_from_open"]),
                    chainlink_at_resolution=float(row["chainlink_at_resolution"]),
                    chainlink_direction=row["chainlink_direction"],
                    chainlink_delta_from_open=float(row["chainlink_delta_from_open"]),
                    chainlink_round_at_resolution=int(row["chainlink_round_at_resolution"]),
                    chainlink_last_update_age=float(row["chainlink_last_update_age"]),
                    lag_delta_at_resolution=float(row["lag_delta_at_resolution"]),
                    max_lag_delta_last_60s=float(row["max_lag_delta_last_60s"]),
                    avg_lag_delta_last_60s=float(row["avg_lag_delta_last_60s"]),
                    direction_match=row["direction_match"].lower() == "true",
                    spot_predicted_correctly=row["spot_predicted_correctly"].lower() == "true",
                    num_observations=int(row["num_observations"]),
                    resolution_datetime_utc=row["resolution_datetime_utc"],
                ))
            except (KeyError, ValueError) as e:
                print(f"Warning: Skipping malformed row: {e}")
                continue

    return rows


def load_observations(data_dir: str) -> list[ObservationRow]:
    """Load lag_observations.csv (can be large)."""
    path = os.path.join(data_dir, "lag_observations.csv")
    if not os.path.exists(path):
        return []

    rows = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append(ObservationRow(
                    timestamp=float(row["timestamp"]),
                    spot_price=float(row["spot_price"]),
                    chainlink_price=float(row["chainlink_price"]),
                    lag_delta=float(row["lag_delta"]),
                    lag_delta_pct=float(row["lag_delta_pct"]),
                    on_chain_age_seconds=float(row["on_chain_age_seconds"]),
                    time_to_resolution=float(row["time_to_resolution"]),
                    market_slug=row["market_slug"],
                ))
            except (KeyError, ValueError):
                continue

    return rows


def percentile(data: list[float], p: float) -> float:
    """Compute p-th percentile of sorted data."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def analyze_lag_distribution(resolutions: list[ResolutionRow]):
    """Analyze the distribution of lag values across all resolutions."""
    print("\n" + "=" * 70)
    print("LAG DISTRIBUTION ANALYSIS")
    print("=" * 70)
    print(f"\nTotal market resolutions: {len(resolutions)}")

    if not resolutions:
        print("No data to analyze.")
        return

    # Lag at resolution
    lag_abs = [abs(r.lag_delta_at_resolution) for r in resolutions]
    max_lags = [r.max_lag_delta_last_60s for r in resolutions]
    avg_lags = [r.avg_lag_delta_last_60s for r in resolutions]
    cl_ages = [r.chainlink_last_update_age for r in resolutions]

    print("\n--- Absolute Lag at Resolution (USD) ---")
    print(f"  Mean:       ${sum(lag_abs)/len(lag_abs):.2f}")
    print(f"  Median:     ${percentile(lag_abs, 50):.2f}")
    print(f"  P25:        ${percentile(lag_abs, 25):.2f}")
    print(f"  P75:        ${percentile(lag_abs, 75):.2f}")
    print(f"  P90:        ${percentile(lag_abs, 90):.2f}")
    print(f"  P95:        ${percentile(lag_abs, 95):.2f}")
    print(f"  Max:        ${max(lag_abs):.2f}")

    print("\n--- Max Lag in Last 60s (USD) ---")
    print(f"  Mean:       ${sum(max_lags)/len(max_lags):.2f}")
    print(f"  Median:     ${percentile(max_lags, 50):.2f}")
    print(f"  P90:        ${percentile(max_lags, 90):.2f}")
    print(f"  Max:        ${max(max_lags):.2f}")

    print("\n--- Chainlink Update Age at Resolution (seconds) ---")
    print(f"  Mean:       {sum(cl_ages)/len(cl_ages):.1f}s")
    print(f"  Median:     {percentile(cl_ages, 50):.1f}s")
    print(f"  P90:        {percentile(cl_ages, 90):.1f}s")
    print(f"  Max:        {max(cl_ages):.1f}s")

    # Direction match analysis
    matches = sum(1 for r in resolutions if r.direction_match)
    mismatches = len(resolutions) - matches
    print(f"\n--- Direction Match (Spot vs Chainlink) ---")
    print(f"  Match:      {matches}/{len(resolutions)} ({100*matches/len(resolutions):.1f}%)")
    print(f"  Mismatch:   {mismatches}/{len(resolutions)} ({100*mismatches/len(resolutions):.1f}%)")

    # Direction match by lag size
    print("\n--- Direction Match Rate by Lag Size ---")
    bins = [(0, 2), (2, 5), (5, 10), (10, 20), (20, 50), (50, float("inf"))]
    for lo, hi in bins:
        in_bin = [r for r in resolutions if lo <= abs(r.lag_delta_at_resolution) < hi]
        if in_bin:
            match_rate = sum(1 for r in in_bin if r.direction_match) / len(in_bin)
            label = f"${lo}-${hi}" if hi != float("inf") else f">${lo}"
            print(f"  {label:>10}: {len(in_bin):3d} markets, match rate {100*match_rate:.1f}%")


def simulate_threshold(resolutions: list[ResolutionRow], threshold: float, odds_assumed: float = 0.65):
    """
    Simulate P&L at a given lag threshold.

    Assumption: If |lag_delta| > threshold at T-30s, we buy the direction
    indicated by spot price. We assume we can buy at `odds_assumed` price.
    Polymarket pays $1 on correct outcome, minus 2% fee.
    """
    trades = 0
    wins = 0
    losses = 0
    total_pnl = 0.0
    position_size = 5.0  # $5 per trade

    for r in resolutions:
        # Would we have entered?
        if r.max_lag_delta_last_60s < threshold:
            continue  # No signal

        trades += 1
        # Did spot direction match resolution (chainlink direction)?
        if r.spot_predicted_correctly:
            # Win: payout $1 per share minus 2% fee, minus cost
            payout_per_dollar = (1.0 * 0.98) - odds_assumed
            pnl = position_size * (payout_per_dollar / odds_assumed)
            wins += 1
        else:
            # Loss: lose entire position cost
            pnl = -position_size
            losses += 1

        total_pnl += pnl

    return trades, wins, losses, total_pnl


def threshold_optimization(resolutions: list[ResolutionRow]):
    """Find optimal threshold by simulating across a range."""
    print("\n" + "=" * 70)
    print("THRESHOLD OPTIMIZATION (Simulated)")
    print("=" * 70)
    print(f"\nAssumptions: $5 position, buy at 0.65 odds, 2% winner fee")
    print(f"{'Threshold':>10} {'Trades':>7} {'Wins':>5} {'Losses':>7} "
          f"{'Win%':>6} {'PnL':>10} {'PnL/Trade':>10}")
    print("-" * 70)

    thresholds = [1.0, 2.0, 3.0, 4.0, 5.0, 7.5, 10.0, 15.0, 20.0, 30.0, 50.0]

    best_pnl_per_trade = -999
    best_threshold = 0

    for t in thresholds:
        trades, wins, losses, pnl = simulate_threshold(resolutions, t)
        if trades == 0:
            print(f"  ${t:>7.1f}   {'--':>7}")
            continue

        win_rate = 100 * wins / trades
        pnl_per_trade = pnl / trades

        marker = ""
        if pnl_per_trade > best_pnl_per_trade and trades >= 5:
            best_pnl_per_trade = pnl_per_trade
            best_threshold = t
            marker = " <-- best"

        print(f"  ${t:>7.1f} {trades:>7} {wins:>5} {losses:>7} "
              f"{win_rate:>5.1f}% ${pnl:>9.2f} ${pnl_per_trade:>9.2f}{marker}")

    if best_threshold > 0:
        print(f"\nRECOMMENDED THRESHOLD: ${best_threshold:.1f}")
        print(f"  (Best PnL/trade with >= 5 trades)")

    # Test with different assumed odds
    print("\n--- Sensitivity to Entry Odds ---")
    if best_threshold > 0:
        for odds in [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]:
            trades, wins, losses, pnl = simulate_threshold(resolutions, best_threshold, odds)
            if trades > 0:
                win_rate = 100 * wins / trades
                print(f"  Odds {odds:.2f}: {trades} trades, "
                      f"{win_rate:.1f}% win, PnL ${pnl:.2f}")


def time_of_day_analysis(resolutions: list[ResolutionRow]):
    """Analyze lag patterns by time of day (UTC)."""
    print("\n" + "=" * 70)
    print("TIME-OF-DAY ANALYSIS (UTC)")
    print("=" * 70)

    if not resolutions:
        return

    hourly = defaultdict(list)
    for r in resolutions:
        try:
            dt = datetime.strptime(r.resolution_datetime_utc, "%Y-%m-%d %H:%M:%S")
            hour = dt.hour
            hourly[hour].append(r)
        except (ValueError, AttributeError):
            continue

    print(f"\n{'Hour':>6} {'Markets':>8} {'Avg Lag':>9} {'Max Lag':>9} "
          f"{'Match%':>8} {'Avg CL Age':>11}")
    print("-" * 60)

    for hour in sorted(hourly.keys()):
        rows = hourly[hour]
        avg_lag = sum(abs(r.lag_delta_at_resolution) for r in rows) / len(rows)
        max_lag = max(abs(r.lag_delta_at_resolution) for r in rows)
        match_rate = 100 * sum(1 for r in rows if r.direction_match) / len(rows)
        avg_age = sum(r.chainlink_last_update_age for r in rows) / len(rows)

        print(f"  {hour:02d}:00 {len(rows):>8} ${avg_lag:>8.2f} ${max_lag:>8.2f} "
              f"{match_rate:>7.1f}% {avg_age:>10.1f}s")


def lag_timeline_analysis(observations: list[ObservationRow]):
    """Analyze how lag evolves approaching market resolution."""
    print("\n" + "=" * 70)
    print("LAG TIMELINE (Approaching Resolution)")
    print("=" * 70)

    if not observations:
        print("No observation data loaded.")
        return

    # Bucket by time-to-resolution
    buckets = defaultdict(list)
    for obs in observations:
        ttr = obs.time_to_resolution
        if ttr <= 10:
            bucket = "T-10s"
        elif ttr <= 20:
            bucket = "T-20s"
        elif ttr <= 30:
            bucket = "T-30s"
        elif ttr <= 45:
            bucket = "T-45s"
        elif ttr <= 60:
            bucket = "T-60s"
        elif ttr <= 120:
            bucket = "T-120s"
        elif ttr <= 300:
            bucket = "T-5min"
        elif ttr <= 600:
            bucket = "T-10min"
        else:
            bucket = "T->10min"

    order = ["T->10min", "T-10min", "T-5min", "T-120s", "T-60s", "T-45s", "T-30s", "T-20s", "T-10s"]

    print(f"\n{'Window':>12} {'Count':>8} {'Avg |Lag|':>11} {'P90 |Lag|':>11} "
          f"{'Avg CL Age':>11}")
    print("-" * 65)

    for bucket_name in order:
        if bucket_name not in buckets:
            continue
        obs_list = buckets[bucket_name]
        abs_lags = [abs(o.lag_delta) for o in obs_list]
        ages = [o.on_chain_age_seconds for o in obs_list]

        avg_lag = sum(abs_lags) / len(abs_lags)
        p90_lag = percentile(abs_lags, 90)
        avg_age = sum(ages) / len(ages)

        print(f"  {bucket_name:>10} {len(obs_list):>8} ${avg_lag:>10.2f} ${p90_lag:>10.2f} "
              f"{avg_age:>10.1f}s")


def print_trading_recommendation(resolutions: list[ResolutionRow]):
    """Print actionable recommendation based on data."""
    print("\n" + "=" * 70)
    print("TRADING RECOMMENDATIONS")
    print("=" * 70)

    if len(resolutions) < 20:
        print(f"\nINSUFFICIENT DATA: Only {len(resolutions)} resolutions.")
        print("Need at least 20 (ideally 100+) for reliable recommendations.")
        print("Keep the logger running!")
        return

    # Find thresholds where win rate > 70%
    lag_abs = sorted([abs(r.lag_delta_at_resolution) for r in resolutions])
    good_thresholds = []

    for t in [1, 2, 3, 4, 5, 7, 10, 15, 20]:
        trades, wins, losses, pnl = simulate_threshold(resolutions, float(t))
        if trades >= 5:
            win_rate = wins / trades
            if win_rate >= 0.70:
                good_thresholds.append((t, trades, win_rate, pnl))

    if good_thresholds:
        # Pick the one with most trades above 70%
        best = max(good_thresholds, key=lambda x: x[1])
        t, trades, wr, pnl = best
        print(f"\n  RECOMMENDED THRESHOLD: ${t} USD")
        print(f"  Expected trades: ~{trades} per {len(resolutions)} markets")
        print(f"  Historical win rate: {100*wr:.1f}%")
        print(f"  Simulated PnL: ${pnl:.2f}")
    else:
        print("\n  WARNING: No threshold found with >70% win rate and >=5 trades.")
        print("  The lag may not be consistently exploitable in your environment.")
        print("  Possible causes:")
        print("    - Your RPC latency is too high")
        print("    - Chainlink updates are faster than expected")
        print("    - BTC volatility is too low for meaningful lag")

    # Infrastructure assessment
    if resolutions:
        avg_age = sum(r.chainlink_last_update_age for r in resolutions) / len(resolutions)
        print(f"\n  INFRASTRUCTURE ASSESSMENT:")
        print(f"  Average Chainlink age at resolution: {avg_age:.1f}s")
        if avg_age < 10:
            print("  -> Chainlink updates are very fresh. Lag opportunity may be limited.")
        elif avg_age < 30:
            print("  -> Moderate lag window. Strategy is viable with tight thresholds.")
        else:
            print("  -> Significant lag window. Good conditions for the strategy.")


def main():
    parser = argparse.ArgumentParser(description="Analyze Chainlink lag data")
    parser.add_argument("--data-dir", type=str, default="lag_data",
                        help="Directory with lag CSV files")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Simulate specific threshold")
    parser.add_argument("--load-observations", action="store_true",
                        help="Also load per-second observations (slower)")
    args = parser.parse_args()

    print("=" * 70)
    print("CHAINLINK LAG ANALYZER")
    print("=" * 70)
    print(f"Data directory: {args.data_dir}")

    # Load data
    resolutions = load_resolutions(args.data_dir)
    if not resolutions:
        print("\nNo resolution data found. Run the lag logger first:")
        print("  python -m polymarket_btc_bot.chainlink_lag.lag_logger --markets 100")
        sys.exit(1)

    print(f"Loaded {len(resolutions)} market resolutions")

    # Run analyses
    analyze_lag_distribution(resolutions)
    threshold_optimization(resolutions)
    time_of_day_analysis(resolutions)

    if args.load_observations:
        observations = load_observations(args.data_dir)
        print(f"\nLoaded {len(observations)} per-second observations")
        lag_timeline_analysis(observations)

    print_trading_recommendation(resolutions)

    # Specific threshold simulation
    if args.threshold:
        print(f"\n--- Custom Threshold: ${args.threshold} ---")
        trades, wins, losses, pnl = simulate_threshold(resolutions, args.threshold)
        if trades > 0:
            print(f"  Trades: {trades}, Wins: {wins}, Losses: {losses}")
            print(f"  Win Rate: {100*wins/trades:.1f}%")
            print(f"  PnL: ${pnl:.2f}")
        else:
            print("  No trades would have triggered at this threshold.")


if __name__ == "__main__":
    main()
