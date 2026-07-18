"""Multi-stage optimizer for the Nasdaq ORB strategy.

Same anti-overfit design as amb_strategy/optimize.py:
  Stage 1  coarse grid, median robust score across 5 market paths
  Stage 2  fine grid around the winner
  Stage 3  walk-forward (train 400 days / test 200 days, 6 paths),
           final pick = best median score across all test folds
  Stage 4  validation on 10 never-optimized-on paths + income stats
"""

import itertools
import json
import time
from collections import Counter

import numpy as np

from data_nq import generate_days
from strategy_orb import Params, backtest, robust_score

N_DAYS = 1000
BARS = 78
IS_DAYS = 700
GRID_SEEDS = [1, 2, 3, 4, 5]
WF_SEEDS = [1, 2, 3, 4, 5, 6]
VAL_SEEDS = list(range(101, 111))

_cache = {}


def get_data(seed):
    if seed not in _cache:
        _cache[seed] = generate_days(N_DAYS, seed=seed)
    return _cache[seed]


def day_slice(df, d0, d1):
    out = df.iloc[d0 * BARS:d1 * BARS].copy()
    out["day"] = out["day"] - d0
    return out


def eval_params(p, seeds, d0, d1):
    scores, mets = [], []
    for s in seeds:
        m = backtest(day_slice(get_data(s), d0, d1), p)
        scores.append(robust_score(m))
        mets.append(m)
    med = {k: float(np.median([m[k] for m in mets]))
           for k in ("sharpe", "cagr", "max_dd", "profit_factor", "n_trades",
                     "win_rate", "pct_win_days", "pct_win_months")}
    med["score"] = float(np.median(scores))
    return med


def grid(space):
    keys = list(space)
    for combo in itertools.product(*space.values()):
        yield Params(**dict(zip(keys, combo)))


def run_grid(space, seeds, d0, d1, label):
    t0, results = time.time(), []
    combos = list(grid(space))
    for i, p in enumerate(combos):
        med = eval_params(p, seeds, d0, d1)
        results.append((med["score"], p, med))
        if (i + 1) % 50 == 0:
            print(f"  [{label}] {i+1}/{len(combos)} ({time.time()-t0:.0f}s)")
    results.sort(key=lambda r: -r[0])
    return results


def pstr(p):
    return (f"or={p.or_min} stop={p.stop_frac} tgt={p.tgt_r} "
            f"buf={p.buffer_frac} be={int(p.breakeven)} cut={p.cutoff_bar} "
            f"ema={p.ema_days} mt={p.max_trades}"
            f"{' +short' if p.allow_short else ' longonly'}")


def main():
    print("Stage 1: coarse grid (in-sample, 5 paths)")
    coarse = {
        "or_min": [15, 30, 60],
        "stop_frac": [0.5, 0.75, 1.0],
        "tgt_r": [0.0, 1.5, 2.5, 4.0],
        "ema_days": [0, 20, 50],
        "allow_short": [True, False],
    }
    r1 = run_grid(coarse, GRID_SEEDS, 0, IS_DAYS, "coarse")
    best = r1[0][1]
    print(f"  best coarse: {pstr(best)}  score={r1[0][0]:.2f}")

    print("Stage 2: fine grid around winner")
    fine = {
        "or_min": sorted({max(10, best.or_min - 15), best.or_min,
                          best.or_min + 15}),
        "stop_frac": sorted({max(0.25, best.stop_frac - 0.25), best.stop_frac,
                             best.stop_frac + 0.25}),
        "tgt_r": sorted({max(0.0, best.tgt_r - 1.0), best.tgt_r,
                         best.tgt_r + 1.0}),
        "buffer_frac": [0.0, 0.1, 0.2],
        "breakeven": [False, True],
        "cutoff_bar": [24, 42, 66],
        "ema_days": [best.ema_days],
        "allow_short": [best.allow_short],
    }
    r2 = run_grid(fine, GRID_SEEDS, 0, IS_DAYS, "fine")
    print(f"  best fine: {pstr(r2[0][1])}  score={r2[0][0]:.2f}")

    candidates = []
    for _, p, _ in (r2 + r1):
        if all(pstr(p) != pstr(q) for q in candidates):
            candidates.append(p)
        if len(candidates) == 8:
            break

    print("Stage 3: walk-forward (train 400d / test 200d)")
    train, test = 400, 200
    fold_starts = range(0, N_DAYS - train - test + 1, test)
    winners = Counter()
    cand_fold_scores = {pstr(p): [] for p in candidates}
    for seed in WF_SEEDS:
        df = get_data(seed)
        for fs in fold_starts:
            tr = day_slice(df, fs, fs + train)
            te = day_slice(df, fs + train, fs + train + test)
            best_p = max(candidates, key=lambda p: robust_score(backtest(tr, p)))
            winners[pstr(best_p)] += 1
            for p in candidates:
                cand_fold_scores[pstr(p)].append(robust_score(backtest(te, p)))
    n_folds = len(list(fold_starts)) * len(WF_SEEDS)
    print("  fold winners:")
    for k, v in winners.most_common():
        print(f"    {v:2d}/{n_folds}  {k}")
    cand_med = {k: float(np.median(v)) for k, v in cand_fold_scores.items()}
    print("  median test-fold score per candidate:")
    for k, v in sorted(cand_med.items(), key=lambda kv: -kv[1]):
        print(f"    {v:5.2f}  {k}")

    final_key = max(cand_med, key=cand_med.get)
    final_p = next(p for p in candidates if pstr(p) == final_key)

    print("Stage 4: validation on 10 unseen paths (25k account, 1% risk)")
    val = []
    for seed in VAL_SEEDS:
        df = get_data(seed)
        m = backtest(df, final_p)
        dc = df.groupby("day").close.last()
        m["buy_hold"] = float(dc.iloc[-1] / dc.iloc[0] - 1)
        val.append({k: float(v) for k, v in m.items() if k != "equity_curve"})

    med = {k: float(np.median([v[k] for v in val])) for k in val[0]}
    print(f"  final params: {pstr(final_p)}")
    print(f"  median: sharpe={med['sharpe']:.2f} cagr={med['cagr']:+.1%} "
          f"dd={med['max_dd']:.1%} pf={med['profit_factor']:.2f} "
          f"win_days={med['pct_win_days']:.0%} win_months={med['pct_win_months']:.0%} "
          f"avg_month=${med['avg_month_usd']:,.0f}")

    out = {
        "final_params": {k: getattr(final_p, k) for k in Params.__dataclass_fields__},
        "stage1_top5": [{"score": s, "params": pstr(p), **m} for s, p, m in r1[:5]],
        "stage2_top5": [{"score": s, "params": pstr(p), **m} for s, p, m in r2[:5]],
        "walkforward": {"fold_winners": dict(winners),
                        "candidate_median_scores": cand_med},
        "validation_runs": val,
        "validation_median": med,
    }
    with open("results_orb.json", "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("wrote results_orb.json")


if __name__ == "__main__":
    main()
