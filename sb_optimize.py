"""
ICT Silver Bullet FVG - Parameter Optimizer
===========================================
Coarse-to-fine grid search over the strategy inputs with a strict
in-sample / out-of-sample split to control overfitting.

  In-sample  (IS):  2023-02-07 .. 2023-06-30   (~100 trading days)
  Out-of-sample (OOS): 2023-07-01 .. 2023-09-11 (~50 trading days)

Ranking score (IS): net profit, but configs with < MIN_TRADES trades or
profit factor < 1 are discarded; drawdown is penalized so that
"one lucky trade" configs do not float to the top.

    score = net - 0.5 * max_dd      (requires trades >= MIN_TRADES)

The top IS configs are then re-run OOS; the recommended setting is the
one with the best combined (IS + OOS) behaviour, not the raw IS winner.
"""

import itertools
import time
import numpy as np
import pandas as pd

from sb_backtest import (load_data, precompute, run_backtest, metrics,
                         ENTRY_EDGE, ENTRY_CE, ENTRY_FULL, TP_LIQ, TP_FIX)

MIN_TRADES_IS = 20


def split_indices(pre, split_ts):
    idx = pre["index"]
    is_end = int(np.searchsorted(idx, split_ts))
    return is_end


def score(m):
    if m["trades"] < MIN_TRADES_IS or not np.isfinite(m["pf"]) or m["pf"] <= 1.0:
        return -np.inf
    return m["net"] - 0.5 * m["max_dd"]


def evaluate(pre, params, is_end):
    m_is = metrics(run_backtest(pre, params, 0, is_end))
    return m_is


def stage1_grid():
    grid = dict(
        entry_mode=[ENTRY_EDGE, ENTRY_CE, ENTRY_FULL],
        tp_mode=[TP_LIQ, TP_FIX],
        fix_rrr=[1.0, 1.5, 2.0, 3.0],
        min_rrr=[1.0, 1.5, 2.0],
        disp_mult=[0.6, 0.9, 1.2, 1.5],
        min_gap_tk=[2, 4, 8],
        sweep_lb=[15, 30, 60, 90],
        sl_lb=[5, 10, 15],
        sl_buf_tk=[4, 8, 16],
    )
    keys = list(grid)
    for combo in itertools.product(*grid.values()):
        p = dict(zip(keys, combo))
        # fix_rrr is only a fallback in liquidity mode -> skip redundant combos
        if p["tp_mode"] == TP_LIQ and p["fix_rrr"] != 2.0:
            continue
        yield p


def stage2_variants(base):
    """Session / bias / trade-management variants around a base config."""
    out = []
    for am, ldn, pm in [(1, 1, 1), (1, 0, 1), (1, 1, 0), (1, 0, 0), (0, 1, 1), (0, 0, 1), (0, 1, 0)]:
        for bias in (True, False):
            for max_day in (2, 4, 6):
                for grace in (0, 12, 24, 48):
                    p = dict(base)
                    p.update(use_am=bool(am), use_ldn=bool(ldn), use_pm=bool(pm),
                             use_bias=bias, max_day=max_day, grace_bars=grace)
                    out.append(p)
    return out


def fmt_params(p):
    em = {0: "Edge", 1: "CE", 2: "Full"}[p.get("entry_mode", 1)]
    tp = {0: "Liq", 1: "FixRRR"}[p.get("tp_mode", 0)]
    sess = "".join([s for s, on in (("AM", p.get("use_am", True)),
                                    ("LDN", p.get("use_ldn", True)),
                                    ("PM", p.get("use_pm", True))) if on]) or "-"
    return (f"entry={em} tp={tp} rrr={p.get('fix_rrr', 2.0)} minRRR={p.get('min_rrr', 2.0)} "
            f"disp={p.get('disp_mult', 1.2)} gap={p.get('min_gap_tk', 4)}t "
            f"sweep={p.get('sweep_lb', 30)} slLB={p.get('sl_lb', 10)} "
            f"slBuf={p.get('sl_buf_tk', 8)}t sess={sess} bias={p.get('use_bias', True)} "
            f"maxDay={p.get('max_day', 4)} grace={p.get('grace_bars', 24)}")


def main(csv_path="USATECHIDXUSD_M1.csv", timeframe="1min"):
    df = load_data(csv_path, timeframe)
    pre = precompute(df)
    split_ts = pd.Timestamp("2023-07-01", tz="America/New_York")
    is_end = split_indices(pre, split_ts)
    n = pre["n"]
    print(f"Data: {n} bars {df.index[0]} .. {df.index[-1]} | IS bars: {is_end} OOS bars: {n - is_end}")

    # ---------------- stage 1 ----------------
    t0 = time.time()
    results = []
    grid = list(stage1_grid())
    print(f"Stage 1: {len(grid)} configs ...")
    for k, p in enumerate(grid):
        m = evaluate(pre, p, is_end)
        s = score(m)
        if np.isfinite(s):
            results.append((s, p, m))
        if (k + 1) % 2000 == 0:
            print(f"  {k + 1}/{len(grid)}  elapsed {time.time() - t0:.0f}s  kept {len(results)}")
    results.sort(key=lambda r: -r[0])
    print(f"Stage 1 done in {time.time() - t0:.0f}s, {len(results)} configs passed filters")

    print("\nTop 15 in-sample:")
    for s, p, m in results[:15]:
        print(f"  score={s:8.0f} net={m['net']:8.0f} tr={m['trades']:3d} wr={m['winrate']:4.1f}% "
              f"pf={m['pf']:4.2f} dd={m['max_dd']:7.0f} | {fmt_params(p)}")

    # ---------------- OOS check of top-N ----------------
    topn = results[:40]
    print("\nOOS validation of top 40:")
    combined = []
    for s, p, m in topn:
        mo = metrics(run_backtest(pre, p, is_end, n))
        c = (m["net"] + 2.0 * mo["net"] - 0.5 * (m["max_dd"] + mo["max_dd"]))
        combined.append((c, p, m, mo))
        print(f"  IS net={m['net']:8.0f} pf={m['pf']:4.2f} tr={m['trades']:3d} || "
              f"OOS net={mo['net']:8.0f} pf={mo['pf']:4.2f} tr={mo['trades']:3d} wr={mo['winrate']:4.1f}% | "
              f"{fmt_params(p)}")
    combined.sort(key=lambda r: -r[0])

    # ---------------- stage 2 on best combined base ----------------
    best_base = combined[0][1]
    print(f"\nStage 2 around: {fmt_params(best_base)}")
    stage2 = []
    for p in stage2_variants(best_base):
        m = evaluate(pre, p, is_end)
        s = score(m)
        if np.isfinite(s):
            mo = metrics(run_backtest(pre, p, is_end, n))
            c = m["net"] + 2.0 * mo["net"] - 0.5 * (m["max_dd"] + mo["max_dd"])
            stage2.append((c, p, m, mo))
    stage2.sort(key=lambda r: -r[0])
    print("Top 15 stage 2 (ranked by IS + 2*OOS - DD penalty):")
    for c, p, m, mo in stage2[:15]:
        print(f"  comb={c:8.0f} IS net={m['net']:8.0f} pf={m['pf']:4.2f} tr={m['trades']:3d} || "
              f"OOS net={mo['net']:8.0f} pf={mo['pf']:4.2f} tr={mo['trades']:3d} | {fmt_params(p)}")

    # ---------------- final full-period run of winner ----------------
    winner = stage2[0][1] if stage2 else best_base
    print("\n=== WINNER (full period) ===")
    print(fmt_params(winner))
    mfull = metrics(run_backtest(pre, winner))
    from sb_backtest import print_report
    print_report("Full period", mfull)
    m_is = metrics(run_backtest(pre, winner, 0, is_end))
    m_oos = metrics(run_backtest(pre, winner, is_end, n))
    print_report("In-sample", m_is)
    print_report("Out-of-sample", m_oos)
    return winner, results, combined, stage2


if __name__ == "__main__":
    import sys
    csv = sys.argv[1] if len(sys.argv) > 1 else "USATECHIDXUSD_M1.csv"
    tf = sys.argv[2] if len(sys.argv) > 2 else "1min"
    main(csv, tf)
