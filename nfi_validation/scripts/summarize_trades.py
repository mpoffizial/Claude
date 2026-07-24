#!/usr/bin/env python3
"""
Summarize a freqtrade backtest export into the task's Phase-2 metric set:
CAGR, Max Drawdown, Sortino, Calmar, SQN, Profit Factor, #trades, avg hold.

  python3 summarize_trades.py --results ../user_data/backtest_results --contaminated

--contaminated stamps the output so Phase-2 numbers are never confused with the
clean Phase-3 walk-forward numbers.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "lib"))
import metrics as M  # noqa: E402


def load_ft_trades(results_dir: Path) -> pd.DataFrame:
    cands = [c for c in (sorted(results_dir.glob("*.zip")) + sorted(results_dir.glob("*.json")))
             if ".last_result" not in c.name and "config" not in c.name]
    if not cands:
        raise FileNotFoundError(f"no export in {results_dir}")
    latest = max(cands, key=lambda p: p.stat().st_mtime)
    if latest.suffix == ".zip":
        with zipfile.ZipFile(latest) as z:
            name = [n for n in z.namelist() if n.endswith(".json") and "config" not in n][0]
            data = json.loads(z.read(name))
    else:
        data = json.loads(latest.read_text())
    strat = next(iter(data["strategy"].values()))
    tr = pd.DataFrame(strat["trades"])
    if not tr.empty:
        tr["open_date"] = pd.to_datetime(tr["open_date"])
        tr["close_date"] = pd.to_datetime(tr["close_date"])
    return tr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, required=True)
    ap.add_argument("--contaminated", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    tr = load_ft_trades(args.results)
    s = M.summarize(tr)
    s["contaminated"] = bool(args.contaminated)
    s["in_sample_warning"] = (
        "IN-SAMPLE / contaminated: current NFI re-tuned on this very period. "
        "Upper bound, not an expectation." if args.contaminated else "clean"
    )
    print(json.dumps(s, indent=2, default=str))
    if args.out:
        args.out.write_text(json.dumps(s, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
