"""Deterministic unit checks for metrics.py - proves the math, needs no market data.

Run:  python3 test_metrics.py
"""
import numpy as np
import pandas as pd

import metrics as m


def _toy():
    return pd.DataFrame({
        "profit_abs":   [100.0, -50.0, 200.0, -100.0],
        "profit_ratio": [0.01, -0.005, 0.02, -0.01],
        "open_date":  pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]),
        "close_date": pd.to_datetime(["2024-01-01 06:00", "2024-01-02 06:00",
                                       "2024-01-03 06:00", "2024-01-04 06:00"]),
    })


def test_profit_factor():
    assert abs(m.profit_factor(_toy()) - 2.0) < 1e-9  # (100+200)/(50+100)


def test_total_and_curve():
    t = _toy()
    eq = m.realized_equity_curve(t, 10_000)
    assert abs(eq.iloc[-1] - 10_150) < 1e-9
    assert abs(t["profit_abs"].sum() - 150.0) < 1e-9


def test_max_drawdown():
    # equity 10000->10100->10050->10250->10150; worst peak-to-trough = 10150/10250-1
    dd = m.max_drawdown(m.realized_equity_curve(_toy(), 10_000))
    assert abs(dd - (1 - 10_150 / 10_250)) < 1e-6, dd


def test_sqn_sign_and_pf_undefined():
    # all-wins -> profit_factor undefined (NaN), sqn finite & positive
    t = pd.DataFrame({
        "profit_abs": [10.0, 20.0, 30.0],
        "profit_ratio": [0.001, 0.002, 0.003],
        "open_date": pd.to_datetime(["2024-01-01"] * 3),
        "close_date": pd.to_datetime(["2024-01-01 01:00", "2024-01-02 01:00", "2024-01-03 01:00"]),
    })
    assert np.isnan(m.profit_factor(t))
    assert m.sqn(t) > 0


def test_avg_hold():
    assert abs(m.avg_hold_hours(_toy()) - 6.0) < 1e-9


def test_empty():
    empty = pd.DataFrame(columns=["profit_abs", "profit_ratio", "open_date", "close_date"])
    s = m.summarize(empty)
    assert s["n_trades"] == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} metric tests passed.")
