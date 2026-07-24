#!/usr/bin/env python3
"""
Phase 4 - cost sensitivity on the CLEAN Phase-3 walk-forward trades.

Re-costs each out-of-sample trade under a grid of fee + slippage assumptions and
finds the break-even roundtrip cost at which the edge disappears.

Re-costing model (first-order, STATED because the rules demand it)
-----------------------------------------------------------------
Freqtrade's exported profit_ratio is NET of the fee used in Phase 3 (--applied-fee).
We recover an approximate GROSS per-trade return:

    gross_ratio_i = net_ratio_i + fills_i * applied_fee

and re-apply a new scenario cost:

    net'_ratio_i = gross_ratio_i - fills_i * (fee + slippage)

* fills_i defaults to 2 (one entry + one exit). NFI uses position adjustment
  ("grinding"/DCA), so real fills >= 2  =>  this UNDERSTATES cost  =>  break-even
  reported here is an UPPER bound (optimistic). Flagged in the output.
* slippage is per fill, in bps. The "tiered" scenario applies a higher slippage to
  pairs outside the top-20 by volume (illiquid long tail), which is where NFI's
  altcoin entries concentrate.

The GOLD-STANDARD alternative (re-running freqtrade per fee) is noted in the report;
this analytic pass is the fast, transparent approximation and says so.

  python3 05_costs.py --trades ../outputs/clean_trades_aggregated.parquet \
                      --applied-fee 0.00075 --top20 ../config/top20_by_volume.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent / "lib"))

FEES = [0.0010, 0.00075, 0.0005]           # per side
SLIPPAGES_BPS = [0, 5, 15, 30]             # per fill


def gross_ratio(trades: pd.DataFrame, applied_fee: float, fills: pd.Series) -> pd.Series:
    return trades["profit_ratio"].to_numpy() + fills.to_numpy() * applied_fee


def net_after(gross: np.ndarray, fills: np.ndarray, fee: float, slip_bps: np.ndarray) -> np.ndarray:
    slip = slip_bps / 1e4
    return gross - fills * (fee + slip)


def pf_from_ratio(net: np.ndarray) -> float:
    gw = net[net > 0].sum()
    gl = -net[net < 0].sum()
    return float(gw / gl) if gl > 0 else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=Path, required=True)
    ap.add_argument("--applied-fee", type=float, default=0.00075)
    ap.add_argument("--fills-per-trade", type=float, default=2.0)
    ap.add_argument("--top20", type=Path, default=None,
                    help="json list of top-20 pairs; others get tiered slippage")
    ap.add_argument("--tier-extra-bps", type=float, default=20.0)
    ap.add_argument("--out", type=Path, default=Path("../outputs/costs.json"))
    args = ap.parse_args()

    if not args.trades.exists():
        print(f"[costs] {args.trades} not found. Run Phase 3 first (needs data). "
              f"Nothing fabricated.")
        return 1

    tr = pd.read_parquet(args.trades)
    n = len(tr)
    fills = pd.Series(np.full(n, args.fills_per_trade))
    gross = gross_ratio(tr, args.applied_fee, fills)
    fills_np = fills.to_numpy()

    grid = []
    for fee in FEES:
        for slip in SLIPPAGES_BPS:
            net = net_after(gross, fills_np, fee, np.full(n, slip))
            roundtrip_bps = args.fills_per_trade * (fee + slip / 1e4) * 1e4
            grid.append({
                "fee_per_side_pct": fee * 100,
                "slippage_bps_per_fill": slip,
                "roundtrip_cost_bps": round(roundtrip_bps, 1),
                "total_net_pct": round(float(net.sum()) * 100, 2),
                "mean_net_per_trade_bps": round(float(net.mean()) * 1e4, 2),
                "profit_factor": round(pf_from_ratio(net), 3),
                "edge_survives": bool(net.sum() > 0),
            })

    # tiered scenario: extra slippage on non-top-20 pairs
    tiered = None
    if args.top20 and args.top20.exists():
        top20 = set(json.loads(args.top20.read_text()))
        slip_bps = np.where(tr["pair"].isin(top20), 5.0, 5.0 + args.tier_extra_bps)
        net = net_after(gross, fills_np, 0.00075, slip_bps)
        tiered = {
            "desc": f"fee 0.075%/side, 5bps top-20 / {5+args.tier_extra_bps:.0f}bps tail",
            "total_net_pct": round(float(net.sum()) * 100, 2),
            "profit_factor": round(pf_from_ratio(net), 3),
            "edge_survives": bool(net.sum() > 0),
            "share_trades_in_tail": round(float((~tr['pair'].isin(top20)).mean()), 3),
        }

    # Break-even: roundtrip cost c* (bps) where total net == 0.
    # total_net(c) = sum(gross) - N * fills * c/1e4  = 0  ->  c* = sum(gross)/(N*fills)*1e4
    breakeven_bps = float(gross.sum() / (n * args.fills_per_trade) * 1e4) if n else float("nan")

    verdict = (
        f"BREAK-EVEN roundtrip cost = {breakeven_bps:.1f} bps. "
        + ("BELOW ~40 bps -> dead for retail execution (fees+slippage alone kill it)."
           if breakeven_bps < 40 else
           "Above 40 bps -> survives typical retail costs; check the grid for the "
           "realistic fee+slippage cell.")
    )

    result = {
        "n_clean_trades": n,
        "applied_fee_in_phase3": args.applied_fee,
        "fills_per_trade_assumed": args.fills_per_trade,
        "model_note": "first-order re-costing; NFI DCA -> real fills >=2 -> break-even "
                      "here is an UPPER bound (optimistic).",
        "grid": grid,
        "tiered_illiquidity_scenario": tiered,
        "breakeven_roundtrip_bps": round(breakeven_bps, 1),
        "verdict": verdict,
    }
    args.out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
