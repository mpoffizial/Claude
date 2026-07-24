#!/usr/bin/env python3
"""
Generate SYNTHETIC OHLCV feather data to SMOKE-TEST the strategy end-to-end in
freqtrade. This is NOT market data and NOT a performance test — a geometric random
walk with occasional trend bursts. Its only job: prove the strategy executes, wires
up indicators, and produces entries/exits. Any P&L on this data is meaningless.

  python3 gen_synthetic_ohlcv.py --out ../user_data/data/binance --pairs BTC ETH SOL

Real evaluation uses real data through ../../nfi_validation (same validation gauntlet).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

RNG = np.random.default_rng(7)


def synth_pair(n_hours: int, start_price: float) -> pd.DataFrame:
    # drift regimes: alternate calm / trending / choppy so breakouts can trigger
    rets = np.zeros(n_hours)
    i = 0
    while i < n_hours:
        span = int(RNG.integers(200, 800))
        mode = RNG.choice(["calm", "trend_up", "trend_down"])
        mu = {"calm": 0.0, "trend_up": 0.0008, "trend_down": -0.0007}[mode]
        sig = {"calm": 0.004, "trend_up": 0.006, "trend_down": 0.007}[mode]
        rets[i:i + span] = RNG.normal(mu, sig, size=min(span, n_hours - i))
        i += span
    close = start_price * np.exp(np.cumsum(rets))
    # build OHLC around close
    high = close * (1 + np.abs(RNG.normal(0, 0.003, n_hours)))
    low = close * (1 - np.abs(RNG.normal(0, 0.003, n_hours)))
    open_ = np.concatenate([[start_price], close[:-1]])
    vol = RNG.uniform(50, 500, n_hours)
    dates = pd.date_range("2023-01-01", periods=n_hours, freq="1h", tz="UTC")
    return pd.DataFrame({"date": dates, "open": open_, "high": np.maximum.reduce([open_, high, close]),
                         "low": np.minimum.reduce([open_, low, close]), "close": close, "volume": vol})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pairs", nargs="+", default=["BTC", "ETH", "BNB", "SOL", "XRP",
                                                   "ADA", "AVAX", "LINK", "DOT", "LTC"])
    ap.add_argument("--hours", type=int, default=24 * 365 * 2)  # ~2 years hourly
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    starts = {"BTC": 16500, "ETH": 1200, "BNB": 250, "SOL": 10, "XRP": 0.35,
              "ADA": 0.25, "AVAX": 11, "LINK": 6, "DOT": 4.5, "LTC": 70}
    for p in args.pairs:
        df = synth_pair(args.hours, starts.get(p, 10.0))
        dest = args.out / f"{p}_USDT-1h.feather"
        df.to_feather(dest)
        print(f"  wrote {dest.name}  ({len(df):,} candles)")
    print("SYNTHETIC data only — for smoke-testing execution, not performance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
