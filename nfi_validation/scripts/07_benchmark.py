#!/usr/bin/env python3
"""
Phase 6 - benchmark the CLEAN walk-forward result against buy & hold, risk-adjusted.

For each Phase-3 window [D, D+90d] compares NFI's realized return AND Sortino against:
  * Buy & Hold BTC over the same window
  * Buy & Hold an equal-weight basket of that window's traded pairs

The point (task rule): if NFI after costs doesn't beat B&H BTC on Sortino, the whole
apparatus isn't paying for its complexity. Nominal return alone is meaningless in a
market where BTC itself can do +100%.

Needs price data (BTC + basket) and ../outputs/walk_forward.json. Without them it
stops and says so - nothing fabricated.

  python3 07_benchmark.py --data-dir ../user_data/data/binance \
                          --walk-forward ../outputs/walk_forward.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS = 365.25


def load_close(data_dir: Path, pair: str, tf: str = "1h") -> pd.Series | None:
    f = data_dir / f"{pair.replace('/', '_')}-{tf}.feather"
    if not f.exists():
        return None
    df = pd.read_feather(f)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date")["close"].sort_index()


def buy_hold_return(close: pd.Series, start: date, end: date) -> float | None:
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    win = close[(close.index >= s) & (close.index <= e)]
    if len(win) < 2:
        return None
    return float(win.iloc[-1] / win.iloc[0] - 1.0)


def sortino_of_series(close: pd.Series, start: date, end: date) -> float | None:
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC")
    win = close[(close.index >= s) & (close.index <= e)]
    if len(win) < 10:
        return None
    daily = win.resample("1D").last().ffill().pct_change().dropna()
    downside = daily[daily < 0]
    dd = np.sqrt((downside ** 2).mean()) if len(downside) else 0.0
    if dd == 0:
        return None
    return float(daily.mean() / dd * np.sqrt(TRADING_DAYS))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--walk-forward", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("../outputs/benchmark.json"))
    args = ap.parse_args()

    if not args.walk_forward.exists():
        print(f"[benchmark] {args.walk_forward} not found. Run Phase 3 first. Nothing fabricated.")
        return 1

    windows = [w for w in json.loads(args.walk_forward.read_text()) if w.get("status") == "ok"]
    if not windows:
        print("[benchmark] no successful walk-forward windows. Nothing to benchmark.")
        return 1

    btc = load_close(args.data_dir, "BTC/USDT")
    rows = []
    nfi_wins = bh_wins = 0
    for w in windows:
        d = date.fromisoformat(w["checkpoint"])
        end = d + timedelta(days=90)
        wf_dir = args.walk_forward.parent / f"wf_{w['checkpoint']}"
        tr = pd.read_parquet(wf_dir / "trades.parquet") if (wf_dir / "trades.parquet").exists() else None
        nfi_ret = w.get("total_profit_pct")
        nfi_sortino = w.get("sortino")

        bh_btc = buy_hold_return(btc, d, end) if btc is not None else None
        btc_sortino = sortino_of_series(btc, d, end) if btc is not None else None

        # equal-weight basket of this window's traded pairs
        basket_rets = []
        if tr is not None and not tr.empty:
            for p in tr["pair"].unique():
                c = load_close(args.data_dir, p)
                if c is not None:
                    r = buy_hold_return(c, d, end)
                    if r is not None:
                        basket_rets.append(r)
        basket_ret = float(np.mean(basket_rets)) if basket_rets else None

        if nfi_sortino is not None and btc_sortino is not None:
            if nfi_sortino > btc_sortino:
                nfi_wins += 1
            else:
                bh_wins += 1
        rows.append({
            "window": w["checkpoint"], "strategy": w.get("strategy"),
            "nfi_return_pct": None if nfi_ret is None else round(nfi_ret * 100, 2),
            "nfi_sortino": None if nfi_sortino is None else round(nfi_sortino, 2),
            "bh_btc_return_pct": None if bh_btc is None else round(bh_btc * 100, 2),
            "bh_btc_sortino": None if btc_sortino is None else round(btc_sortino, 2),
            "basket_return_pct": None if basket_ret is None else round(basket_ret * 100, 2),
        })

    result = {
        "n_windows": len(windows),
        "per_window": rows,
        "nfi_beats_btc_sortino_in_windows": nfi_wins,
        "btc_beats_or_ties_nfi_sortino_in_windows": bh_wins,
        "verdict": (
            f"NFI beat B&H BTC on Sortino in {nfi_wins}/{nfi_wins+bh_wins} comparable "
            f"windows. " + ("If this is <= half, the complexity is not paying for itself."
                            if nfi_wins <= bh_wins else
                            "NFI adds risk-adjusted value vs simply holding BTC.")
        ),
    }
    args.out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
