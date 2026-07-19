"""Multi-strategy quant portfolio: the honest route to "steady profit,
small drawdown".

Components (each with its previously validated, walk-forward-tested
parameters):
  1. NY ORB      - Nasdaq opening range breakout, intraday, long-only
  2. London ORB  - Asian-range breakout on a EU index, intraday, L+S
  3. AMB         - crypto 1h trend following, swing, long-only

Portfolio construction (standard institutional recipe):
  - inverse-volatility (risk parity) capital weights
  - daily returns combined, weights static (set from first 250 days)
  - volatility targeting: scale exposure so realized 60d portfolio vol
    tracks 10% p.a., exposure clamped to [0.3, 1.5]
  - drawdown brake: exposure halved while portfolio is >10% below its
    peak, restored above -5%

Evaluated on 6 unseen seed-sets (101-106). In the simulators the three
markets are statistically independent; real BTC/Nasdaq/DAX correlate
positively, so live diversification will be somewhat weaker - the
component-vs-portfolio comparison shows how much is at stake.
"""

import json
import sys
import numpy as np

sys.path.insert(0, "../amb_strategy")
sys.path.insert(0, "../nasdaq_orb")
sys.path.insert(0, "amb_strategy")
sys.path.insert(0, "nasdaq_orb")

from data import generate_ohlcv                      # amb_strategy
from strategy import backtest as amb_backtest, Params as AmbParams
from data_nq import generate_days
from strategy_orb import backtest as orb_backtest, Params as OrbParams
import london_backtest as lb

TARGET_VOL = 0.10
VOL_WIN = 60
BRAKE_DD, BRAKE_OFF = -0.10, -0.05
CLAMP = (0.3, 1.5)

AMB_P = AmbParams(entry_len=24, exit_len=72, trail_mult=5.0, sl_mult=3.0,
                  ema_len=100, vol_filter=True, vol_low=0.15, vol_high=0.95,
                  allow_short=False)
ORB_P = OrbParams(or_min=60, buffer_frac=0.1, stop_frac=0.5, tgt_r=0.0,
                  breakeven=True, cutoff_bar=66, ema_days=0,
                  allow_short=False, max_trades=2)
LON_P = dict(mode="asian", tgt_r=0.0, stop_mode="0.25", allow_short=True,
             cutoff_bar=600, max_trades=3)


def daily_returns(eq: np.ndarray) -> np.ndarray:
    return np.diff(eq) / eq[:-1]


def component_returns(seed: int, n_days: int = 750):
    amb_eq = amb_backtest(generate_ohlcv(30_000, "BTC", seed=seed),
                          AMB_P)["equity_curve"][::24]
    ny_eq = orb_backtest(generate_days(1000, seed=seed),
                         ORB_P)["equity_curve"]
    lon_eq = lb.backtest(lb.generate(750, seed=seed)[0], return_daily=True,
                         **LON_P)["_daily_equity"]
    rs = [daily_returns(e)[:n_days] for e in (amb_eq, ny_eq, lon_eq)]
    n = min(len(r) for r in rs)
    return np.column_stack([r[:n] for r in rs])


def metrics(rets: np.ndarray) -> dict:
    eq = np.cumprod(1 + rets)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
    dd = ((np.concatenate([[1.0], eq]) - peak) / peak).min()
    sharpe = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    yrs = len(rets) / 252
    cagr = eq[-1] ** (1 / yrs) - 1
    n_m = len(rets) // 21
    monthly = (1 + rets[:n_m * 21]).reshape(n_m, 21).prod(axis=1) - 1
    return dict(sharpe=sharpe, cagr=cagr, max_dd=dd,
                pct_win_months=(monthly > 0).mean(),
                worst_month=monthly.min(), best_month=monthly.max())


def build_portfolio(R: np.ndarray) -> np.ndarray:
    """Risk-parity weights + vol targeting + drawdown brake."""
    vols = R[:250].std(axis=0) * np.sqrt(252)
    w = (1 / vols) / (1 / vols).sum()
    raw = R @ w

    out = np.empty_like(raw)
    eq = 1.0
    peak = 1.0
    braked = False
    tgt_d = TARGET_VOL / np.sqrt(252)
    for t in range(len(raw)):
        est = raw[max(0, t - VOL_WIN):t].std() if t > 10 else tgt_d
        k = np.clip(tgt_d / (est + 1e-12), *CLAMP)
        dd = eq / peak - 1
        if braked and dd > BRAKE_OFF:
            braked = False
        elif not braked and dd < BRAKE_DD:
            braked = True
        if braked:
            k *= 0.5
        out[t] = k * raw[t]
        eq *= 1 + out[t]
        peak = max(peak, eq)
    return raw, out


def main():
    names = ["AMB (BTC 1h)", "NY ORB (NQ 5m)", "London ORB (1m)"]
    rows_c = {n: [] for n in names}
    rows_raw, rows_final = [], []
    for seed in range(101, 107):
        R = component_returns(seed)
        for j, n in enumerate(names):
            rows_c[n].append(metrics(R[:, j]))
        raw, fin = build_portfolio(R)
        rows_raw.append(metrics(raw))
        rows_final.append(metrics(fin))
        m = rows_final[-1]
        print(f"seed {seed}: sharpe={m['sharpe']:.2f} cagr={m['cagr']:+.1%} "
              f"dd={m['max_dd']:.1%} winM={m['pct_win_months']:.0%} "
              f"worstM={m['worst_month']:.1%}")

    def med(rows):
        return {k: float(np.median([r[k] for r in rows])) for k in rows[0]}

    print("\n=== MEDIAN over 6 unseen seed-sets ===")
    summary = {}
    for n in names:
        m = med(rows_c[n])
        summary[n] = m
        print(f"{n:<18} sharpe={m['sharpe']:5.2f} cagr={m['cagr']:+7.1%} "
              f"dd={m['max_dd']:6.1%} winM={m['pct_win_months']:.0%} "
              f"worstM={m['worst_month']:6.1%}")
    m = med(rows_raw)
    summary["Portfolio (risk parity)"] = m
    print(f"{'PORTFOLIO raw':<18} sharpe={m['sharpe']:5.2f} cagr={m['cagr']:+7.1%} "
          f"dd={m['max_dd']:6.1%} winM={m['pct_win_months']:.0%} "
          f"worstM={m['worst_month']:6.1%}")
    m = med(rows_final)
    summary["Portfolio (vol-target + brake)"] = m
    print(f"{'PORTFOLIO final':<18} sharpe={m['sharpe']:5.2f} cagr={m['cagr']:+7.1%} "
          f"dd={m['max_dd']:6.1%} winM={m['pct_win_months']:.0%} "
          f"worstM={m['worst_month']:6.1%}")

    with open("results_portfolio.json", "w") as f:
        json.dump({"median": summary,
                   "per_seed_final": [
                       {k: float(v) for k, v in r.items()} for r in rows_final]},
                  f, indent=2, default=float)
    print("wrote results_portfolio.json")


if __name__ == "__main__":
    main()
