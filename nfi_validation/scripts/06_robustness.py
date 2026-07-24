#!/usr/bin/env python3
"""
Phase 5 - robustness on the aggregated CLEAN (out-of-sample) trades.

  * Monte Carlo (n=10_000): shuffle trade ORDER -> distribution of final capital and
    max drawdown. Reports 5th pct final capital and 95th pct max drawdown.
  * Bootstrap  (n=10_000): resample trades WITH replacement -> 95% CI for profit
    factor. Flags whether the CI contains 1.0 (i.e. "no edge" not excluded).
  * Regime split: bull / bear / sideways by BTC trend at each trade's open. Shows
    whether all the profit comes from one regime.
  * Concentration: recompute totals after removing the best 5% of trades.

Produces montecarlo.png. Operates ONLY on real trades; with no trades file it stops
and says so (nothing fabricated). Pure functions are unit-tested in test_robustness.py.

  python3 06_robustness.py --trades ../outputs/clean_trades_aggregated.parquet \
                           --btc ../user_data/data/binance/BTC_USDT-1d.feather
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

RNG = np.random.default_rng(20260724)  # pinned seed for reproducibility
N_MC = 10_000


def equity_path(profit_abs: np.ndarray, start: float = 10_000.0) -> np.ndarray:
    return start + np.cumsum(profit_abs)


def max_dd_from_path(path: np.ndarray) -> float:
    peak = np.maximum.accumulate(path)
    return float(-(path / peak - 1.0).min())


def monte_carlo_order(profit_abs: np.ndarray, n: int = N_MC, start: float = 10_000.0):
    """Shuffle trade order n times; return arrays of final capital and max DD."""
    finals = np.empty(n)
    dds = np.empty(n)
    for i in range(n):
        perm = RNG.permutation(profit_abs)
        path = equity_path(perm, start)
        finals[i] = path[-1]
        dds[i] = max_dd_from_path(np.concatenate([[start], path]))
    return finals, dds


def bootstrap_profit_factor(profit_abs: np.ndarray, n: int = N_MC):
    """Resample trades with replacement; return distribution of profit factor."""
    m = len(profit_abs)
    pfs = np.empty(n)
    for i in range(n):
        s = profit_abs[RNG.integers(0, m, m)]
        gl = -s[s < 0].sum()
        pfs[i] = (s[s > 0].sum() / gl) if gl > 0 else np.nan
    return pfs


def btc_regime_series(btc_path: Path) -> pd.DataFrame | None:
    """Daily BTC with a bull/bear/sideways label from 50d vs 200d SMA + slope."""
    if not btc_path or not btc_path.exists():
        return None
    df = pd.read_feather(btc_path)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.sort_values("date").set_index("date")
    df["sma50"] = df["close"].rolling(50).mean()
    df["sma200"] = df["close"].rolling(200).mean()
    df["ret30"] = df["close"].pct_change(30)
    def label(r):
        if r["close"] > r["sma200"] and r["ret30"] > 0.05:
            return "bull"
        if r["close"] < r["sma200"] and r["ret30"] < -0.05:
            return "bear"
        return "sideways"
    df["regime"] = df.apply(label, axis=1)
    return df[["regime"]]


def regime_split(trades: pd.DataFrame, regimes: pd.DataFrame | None) -> dict:
    if regimes is None:
        return {"status": "no_btc_data - regime split skipped (needs BTC daily data)"}
    t = trades.copy()
    t["day"] = pd.to_datetime(t["open_date"], utc=True).dt.floor("D")
    reg = regimes.reset_index()
    reg["day"] = reg["date"].dt.floor("D")
    t = t.merge(reg[["day", "regime"]], on="day", how="left")
    out = {}
    total = t["profit_abs"].sum()
    for r, g in t.groupby("regime"):
        out[str(r)] = {
            "n_trades": int(len(g)),
            "total_profit_abs": round(float(g["profit_abs"].sum()), 2),
            "share_of_total_profit": (round(float(g["profit_abs"].sum() / total), 3)
                                      if total else None),
        }
    return out


def concentration(profit_abs: np.ndarray, top_frac: float = 0.05) -> dict:
    s = np.sort(profit_abs)[::-1]
    k = max(1, int(len(s) * top_frac))
    without = s[k:]
    return {
        "n_removed": k,
        "total_all": round(float(s.sum()), 2),
        "total_without_best_5pct": round(float(without.sum()), 2),
        "profit_from_best_5pct_share": (round(float(s[:k].sum() / s.sum()), 3)
                                        if s.sum() else None),
        "still_profitable_without_top5pct": bool(without.sum() > 0),
    }


def make_montecarlo_png(finals, dds, pfs, out_png: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(14, 4))
    axes[0].hist(finals, bins=60, color="#4c72b0")
    axes[0].axvline(np.percentile(finals, 5), color="#c44e52", ls="--",
                    label=f"5th pct = {np.percentile(finals,5):,.0f}")
    axes[0].axvline(10_000, color="gray", ls=":", label="start 10,000")
    axes[0].set_title("MC: final capital (shuffled order)")
    axes[0].legend(fontsize=8)
    axes[1].hist(dds * 100, bins=60, color="#dd8452")
    axes[1].axvline(np.percentile(dds, 95) * 100, color="#c44e52", ls="--",
                    label=f"95th pct = {np.percentile(dds,95)*100:.0f}%")
    axes[1].set_title("MC: max drawdown")
    axes[1].legend(fontsize=8)
    pf_clean = pfs[np.isfinite(pfs)]
    axes[2].hist(pf_clean, bins=60, color="#55a868")
    lo, hi = np.percentile(pf_clean, [2.5, 97.5])
    axes[2].axvline(1.0, color="#c44e52", ls="-", label="PF = 1.0 (no edge)")
    axes[2].axvspan(lo, hi, alpha=0.2, color="#55a868", label=f"95% CI [{lo:.2f},{hi:.2f}]")
    axes[2].set_title("Bootstrap: profit factor")
    axes[2].legend(fontsize=8)
    fig.suptitle("Phase 5 - robustness on CLEAN out-of-sample trades", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_png, dpi=130)
    print(f"[plot] wrote {out_png}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=Path, required=True)
    ap.add_argument("--btc", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("../outputs"))
    args = ap.parse_args()

    if not args.trades.exists():
        print(f"[robustness] {args.trades} not found. Run Phase 3 first (needs data). "
              f"Nothing fabricated.")
        return 1

    tr = pd.read_parquet(args.trades)
    profit_abs = tr["profit_abs"].to_numpy(dtype=float)
    if len(profit_abs) < 10:
        print(f"[robustness] only {len(profit_abs)} trades - too few for stable MC/bootstrap.")

    finals, dds = monte_carlo_order(profit_abs)
    pfs = bootstrap_profit_factor(profit_abs)
    pf_ci = [float(np.nanpercentile(pfs, 2.5)), float(np.nanpercentile(pfs, 97.5))]

    result = {
        "n_clean_trades": int(len(profit_abs)),
        "monte_carlo": {
            "n_runs": N_MC,
            "final_capital_5th_pct": round(float(np.percentile(finals, 5)), 2),
            "final_capital_median": round(float(np.median(finals)), 2),
            "max_drawdown_95th_pct": round(float(np.percentile(dds, 95)), 4),
        },
        "bootstrap_profit_factor": {
            "n_runs": N_MC,
            "ci95": [round(pf_ci[0], 3), round(pf_ci[1], 3)],
            "ci_contains_1.0": bool(pf_ci[0] <= 1.0 <= pf_ci[1]),
            "interpretation": ("CI includes 1.0 -> 'no edge' NOT statistically excluded"
                               if pf_ci[0] <= 1.0 <= pf_ci[1] else
                               "CI entirely above 1.0 -> edge robust to resampling"),
        },
        "regime_split": regime_split(tr, btc_regime_series(args.btc)),
        "trade_concentration": concentration(profit_abs),
    }
    (args.out / "robustness.json").write_text(json.dumps(result, indent=2))
    make_montecarlo_png(finals, dds, pfs, args.out / "montecarlo.png")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
