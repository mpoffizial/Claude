"""Deterministic checks for 06_robustness pure functions. No market data.

Run:  python3 test_robustness.py
"""
import importlib.util
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location("rob", Path(__file__).parent / "06_robustness.py")
rob = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rob)


def test_max_dd():
    # 10000 -> 10100 -> 10050 -> 10250 -> 10150 ; worst = 10150/10250 - 1
    path = np.array([10_000, 10_100, 10_050, 10_250, 10_150.0])
    assert abs(rob.max_dd_from_path(path) - (1 - 10_150 / 10_250)) < 1e-9


def test_mc_order_invariextremes():
    # sum is order-invariant -> every shuffled final capital equals start + sum
    p = np.array([100.0, -50.0, 200.0, -100.0])
    finals, dds = rob.monte_carlo_order(p, n=200, start=10_000)
    assert np.allclose(finals, 10_150.0)          # final only depends on sum
    assert (dds >= 0).all()


def test_concentration():
    p = np.array([1.0, 2.0, 3.0, 4.0, 100.0])     # one giant winner
    c = rob.concentration(p, top_frac=0.2)          # removes best 1
    assert c["n_removed"] == 1
    assert abs(c["total_without_best_5pct"] - 10.0) < 1e-9
    assert c["still_profitable_without_top5pct"] is True


def test_bootstrap_pf_positive_edge():
    # strong positive edge -> bootstrap PF distribution mostly > 1
    p = np.concatenate([np.full(80, 10.0), np.full(20, -5.0)])
    pfs = rob.bootstrap_profit_factor(p, n=500)
    assert np.nanmedian(pfs) > 1.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} robustness tests passed.")
