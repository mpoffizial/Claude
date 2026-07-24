#!/usr/bin/env python3
"""
Phase 1 deliverable: data size (GB), pair count, and MISSING CANDLES per pair.

Reads the downloaded freqtrade feather files and, for each pair, counts how many
5m candles are missing versus a continuous grid from first to last timestamp.
Missing candles matter: NFI is a 5m strategy and gaps distort indicators/entries.

  python3 02b_report_gaps.py --data-dir ../user_data/data/binance --timeframe 5m
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--timeframe", default="5m")
    args = ap.parse_args()

    step = pd.Timedelta(minutes=TF_MINUTES[args.timeframe])
    files = sorted(args.data_dir.glob(f"*-{args.timeframe}.feather"))
    if not files:
        print(f"No {args.timeframe} feather files in {args.data_dir}")
        return 1

    total_bytes = 0
    rows = []
    for f in files:
        total_bytes += f.stat().st_size
        df = pd.read_feather(f)
        df["date"] = pd.to_datetime(df["date"], utc=True)
        n = len(df)
        if n < 2:
            rows.append((f.stem, n, 0, 0.0, None, None))
            continue
        span = df["date"].iloc[-1] - df["date"].iloc[0]
        expected = int(span / step) + 1
        missing = expected - n
        rows.append((f.stem.replace(f"-{args.timeframe}", ""), n, missing,
                     100.0 * missing / expected,
                     df["date"].iloc[0].date().isoformat(),
                     df["date"].iloc[-1].date().isoformat()))

    rep = pd.DataFrame(rows, columns=["pair", "candles", "missing", "missing_pct",
                                      "first", "last"]).sort_values("missing_pct", ascending=False)
    print(f"Timeframe {args.timeframe}: {len(files)} pairs, "
          f"data size {total_bytes/1e9:.2f} GB")
    print(f"Total missing candles: {rep['missing'].sum():,} "
          f"(mean {rep['missing_pct'].mean():.3f}% per pair)\n")
    print("Worst 15 pairs by missing %:")
    with pd.option_context("display.max_rows", 20, "display.width", 120):
        print(rep.head(15).to_string(index=False))
    rep.to_csv(args.data_dir.parent.parent / "outputs" / f"gaps_{args.timeframe}.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
