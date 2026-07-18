"""Multi-stage optimizer for the AMB strategy.

Stage 1  Coarse grid search, in-sample, averaged over 3 market paths.
Stage 2  Fine grid around the stage-1 winner.
Stage 3  Walk-forward analysis (rolling train/test) on 6 paths using the
         top candidates — measures true out-of-sample performance.
Stage 4  Final validation of the chosen parameter set on 10 completely
         fresh, never-optimized-on market paths + buy & hold comparison.

The objective is `robust_score` (Sharpe penalized for thin samples and
deep drawdowns), and the final pick is the parameter set most often
chosen across walk-forward folds — not the single best backtest.
"""

import itertools
import json
import time
from collections import Counter
from dataclasses import replace

import numpy as np

from data import generate_ohlcv
from strategy import Params, backtest, robust_score

N_HOURS = 30_000
IS_BARS = 20_000            # in-sample portion for stages 1-2
GRID_SEEDS = [1, 2, 3]
WF_SEEDS = [1, 2, 3, 4, 5, 6]
VAL_SEEDS = list(range(101, 111))

_data_cache = {}


def get_data(seed):
    if seed not in _data_cache:
        _data_cache[seed] = generate_ohlcv(N_HOURS, "BTC", seed=seed)
    return _data_cache[seed]


def eval_params(p: Params, seeds, sl=slice(None)) -> dict:
    """Median metrics + robust score across seeds."""
    scores, mets = [], []
    for s in seeds:
        m = backtest(get_data(s).iloc[sl], p)
        scores.append(robust_score(m))
        mets.append(m)
    med = {k: float(np.median([m[k] for m in mets]))
           for k in ("sharpe", "cagr", "max_dd", "profit_factor", "n_trades",
                     "win_rate", "total_return")}
    med["score"] = float(np.median(scores))
    return med


def grid(space: dict):
    keys = list(space)
    for combo in itertools.product(*space.values()):
        yield Params(**dict(zip(keys, combo)))


def run_grid(space, seeds, sl, label):
    t0, results = time.time(), []
    combos = list(grid(space))
    for i, p in enumerate(combos):
        med = eval_params(p, seeds, sl)
        results.append((med["score"], p, med))
        if (i + 1) % 50 == 0:
            print(f"  [{label}] {i+1}/{len(combos)} ({time.time()-t0:.0f}s)")
    results.sort(key=lambda r: -r[0])
    return results


def pstr(p: Params) -> str:
    return (f"entry={p.entry_len} exit={p.exit_len} trail={p.trail_mult} "
            f"sl={p.sl_mult} ema={p.ema_len} vol=({p.vol_low},{p.vol_high})"
            f"{' +short' if p.allow_short else ''}"
            if p.vol_filter else
            f"entry={p.entry_len} exit={p.exit_len} trail={p.trail_mult} "
            f"sl={p.sl_mult} ema={p.ema_len} vol=off"
            f"{' +short' if p.allow_short else ''}")


def main():
    is_slice = slice(0, IS_BARS)

    # ---------------- Stage 1: coarse grid ----------------
    print("Stage 1: coarse grid search (in-sample, 3 paths)")
    coarse = {
        "entry_len": [24, 48, 72, 96, 120],
        "exit_len": [12, 24, 48],
        "trail_mult": [2.0, 3.0, 4.0],
        "ema_len": [100, 200, 400],
        "allow_short": [False, True],
        "vol_filter": [True, False],
    }
    r1 = run_grid(coarse, GRID_SEEDS, is_slice, "coarse")
    best = r1[0][1]
    print(f"  best coarse: {pstr(best)}  score={r1[0][0]:.2f}")

    # ---------------- Stage 2: fine grid ----------------
    print("Stage 2: fine grid around winner")
    fine = {
        "entry_len": sorted({max(12, best.entry_len - 24), best.entry_len,
                             best.entry_len + 24}),
        "exit_len": sorted({max(6, best.exit_len - 12), best.exit_len,
                            best.exit_len + 12}),
        "trail_mult": [best.trail_mult - 0.5, best.trail_mult,
                       best.trail_mult + 0.5],
        "sl_mult": [1.5, 2.0, 2.5, 3.0],
        "ema_len": [best.ema_len],
        "allow_short": [best.allow_short],
        "vol_filter": [best.vol_filter],
        "vol_low": [0.0, 0.15, 0.30] if best.vol_filter else [0.15],
    }
    r2 = run_grid(fine, GRID_SEEDS, is_slice, "fine")
    print(f"  best fine: {pstr(r2[0][1])}  score={r2[0][0]:.2f}")

    # candidate pool for walk-forward: top 8 unique param sets
    candidates = []
    for _, p, _ in (r2 + r1):
        if all(pstr(p) != pstr(q) for q in candidates):
            candidates.append(p)
        if len(candidates) == 8:
            break

    # ---------------- Stage 3: walk-forward ----------------
    print("Stage 3: walk-forward (train 8000 / test 4000)")
    train, test = 8_000, 4_000
    fold_starts = range(0, N_HOURS - train - test + 1, test)
    winners, oos_rets = Counter(), {s: [] for s in WF_SEEDS}
    for seed in WF_SEEDS:
        df = get_data(seed)
        for fs in fold_starts:
            tr_sl = slice(fs, fs + train)
            te_sl = slice(fs + train, fs + train + test)
            best_p = max(candidates,
                         key=lambda p: robust_score(backtest(df.iloc[tr_sl], p)))
            winners[pstr(best_p)] += 1
            m = backtest(df.iloc[te_sl], best_p)
            oos_rets[seed].append(m["total_return"])
    n_folds = len(list(fold_starts)) * len(WF_SEEDS)
    print("  fold winners:")
    for k, v in winners.most_common():
        print(f"    {v:2d}/{n_folds}  {k}")

    # final pick: params chosen most often across folds
    final_key = winners.most_common(1)[0][0]
    final_p = next(p for p in candidates if pstr(p) == final_key)

    # stitched OOS return per seed (compound fold returns)
    oos_total = {s: float(np.prod([1 + r for r in rs]) - 1)
                 for s, rs in oos_rets.items()}

    # ---------------- Stage 4: fresh-path validation ----------------
    print("Stage 4: validation on 10 unseen paths")
    val = []
    for seed in VAL_SEEDS:
        df = get_data(seed)
        m = backtest(df, final_p)
        bh = df.close.iloc[-1] / df.close.iloc[0] - 1
        m["buy_hold"] = float(bh)
        val.append({k: float(v) for k, v in m.items() if k != "equity_curve"})

    med = {k: float(np.median([v[k] for v in val])) for k in val[0]}
    print(f"  final params: {pstr(final_p)}")
    print(f"  median validation: sharpe={med['sharpe']:.2f} "
          f"cagr={med['cagr']:+.1%} dd={med['max_dd']:.1%} "
          f"pf={med['profit_factor']:.2f}")

    out = {
        "final_params": {k: getattr(final_p, k) for k in Params.__dataclass_fields__},
        "stage1_top5": [{"score": s, "params": pstr(p), **{k: v for k, v in m.items()}}
                        for s, p, m in r1[:5]],
        "stage2_top5": [{"score": s, "params": pstr(p), **{k: v for k, v in m.items()}}
                        for s, p, m in r2[:5]],
        "walkforward": {"fold_winners": dict(winners),
                        "oos_return_per_seed": oos_total,
                        "oos_fold_returns": {s: rs for s, rs in oos_rets.items()}},
        "validation_runs": val,
        "validation_median": med,
    }
    with open("results.json", "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("wrote results.json")


if __name__ == "__main__":
    main()
