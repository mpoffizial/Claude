#!/usr/bin/env python3
"""
Smoke-test the MomentumTrend STRATEGY LOGIC directly on synthetic data.

Why not `freqtrade backtesting`? That engine insists on loading live exchange
markets (api.binance.com), which the sandbox blocks - a network limitation, not a
strategy defect. On a machine with Binance access `freqtrade backtesting` runs the
same strategy unchanged. Here we exercise exactly the code this repo owns - the
populate_* methods - and confirm the strategy computes its indicators and emits
entries/exits. It is a correctness check, NOT a performance test (data is synthetic).

  <ftenv>/bin/python tools/smoke_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "strategy"))
from MomentumTrend import MomentumTrend  # noqa: E402


def run_pipeline(df: pd.DataFrame, pair: str) -> pd.DataFrame:
    # __new__ bypasses IStrategy.__init__ (needs a full config); populate_* only use
    # class-constant params + talib/qtpylib, so this is a faithful logic run.
    s = MomentumTrend.__new__(MomentumTrend)
    meta = {"pair": pair}
    df = s.populate_indicators(df.copy(), meta)
    df = s.populate_entry_trend(df, meta)
    df = s.populate_exit_trend(df, meta)
    return df


def main() -> int:
    data_dir = ROOT / "user_data" / "data" / "binance"
    files = sorted(data_dir.glob("*_USDT-1h.feather"))
    if not files:
        print("No synthetic data. Run gen_synthetic_ohlcv.py first.")
        return 1

    total_entries = total_exits = 0
    for f in files:
        pair = f.stem.replace("-1h", "").replace("_", "/")
        df = pd.read_feather(f)
        out = run_pipeline(df, pair)
        # indicators must exist and be populated after warmup
        for col in ("ema_fast", "ema_slow", "adx", "rsi", "donchian_high"):
            assert col in out.columns, f"missing indicator {col}"
        assert out["ema_slow"].notna().sum() > 0, "EMA200 never computed"
        entries = int(out.get("enter_long", pd.Series(dtype=float)).fillna(0).sum())
        exits = int(out.get("exit_long", pd.Series(dtype=float)).fillna(0).sum())
        total_entries += entries
        total_exits += exits
        print(f"  {pair:10s}  candles={len(out):>6,}  entries={entries:>4}  exits={exits:>4}")

    print(f"\nTOTAL  entries={total_entries}  exits={total_exits}")
    assert total_entries > 0, "strategy produced NO entries - logic is broken"
    assert total_exits > 0, "strategy produced NO exits - logic is broken"
    print("SMOKE TEST PASSED: MomentumTrend computes indicators and emits entries/exits.")
    print("(synthetic data => signal COUNTS are meaningful, P&L is not.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
