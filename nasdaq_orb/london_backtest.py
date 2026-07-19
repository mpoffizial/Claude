"""London-session ORB: simulator + backtester + optimizer.

Simulates a European index (DAX-like) at 1-minute resolution for the
00:00-17:00 London window: quiet Asian session, volatility burst at the
08:00 London open, lunch lull, US-overlap afternoon activity, overnight
gap, daily regime switching + GARCH vol clustering, fat tails.

Edge mechanism ("trend mornings"): on ~20% of days the London open starts
a directional move through the morning, direction biased by regime -
the session-breakout analogue of NY trend days. A sensitivity stage
reports what happens when this assumption is weakened.

Strategy under test = london_orb.pine logic:
  range = Asian session (00:00-08:00) or opening range (5/15/30 min),
  1-min close breakout entry, stop = breakout candle extreme or a fraction
  of range width, target = R-multiple or hold to 16:25 flat.
"""

import itertools
import json
import time

import numpy as np

MIN_PER_DAY = 1020          # 00:00 - 17:00 London
OPEN_BAR = 480              # 08:00
FLAT_BAR = 985              # 16:25
TRADING_DAYS = 252

REGIMES = ["bull", "bear", "side"]
DRIFT = {"bull": 0.25, "bear": -0.30, "side": 0.03}
VOL_MULT = {"bull": 0.85, "bear": 1.45, "side": 0.75}
TRANSITION = np.array([
    [0.9917, 0.0042, 0.0041],
    [0.0083, 0.9833, 0.0084],
    [0.0075, 0.0050, 0.9875],
])
ANNUAL_VOL = 0.18
GAP_VAR_SHARE = 0.15
S0 = 24_000.0

TREND_AM_PROB = 0.20        # trend-morning probability
TREND_AM_SIGMA = 0.9        # extra move in daily sigmas, 08:00-12:00
TREND_DIR_BIAS = {"bull": 0.62, "bear": 0.38, "side": 0.50}


def _vol_profile() -> np.ndarray:
    w = np.full(MIN_PER_DAY, 0.40)            # Asian base
    w[OPEN_BAR:OPEN_BAR + 30] = 2.5            # London open burst
    w[OPEN_BAR + 30:OPEN_BAR + 90] = 1.6
    w[OPEN_BAR + 90:690] = 1.0                 # 09:30-11:30
    w[690:780] = 0.6                           # lunch
    w[780:960] = 1.35                          # US overlap 13:00-16:00
    w[960:] = 0.8
    return w / w.sum()


def generate(n_days: int = 750, seed: int = 42,
             am_prob: float = TREND_AM_PROB,
             am_sigma: float = TREND_AM_SIGMA):
    rng = np.random.default_rng(seed)
    w = _vol_profile()
    reg = np.empty(n_days, dtype=np.int64)
    state = rng.choice(3, p=[0.45, 0.20, 0.35])
    for d in range(n_days):
        state = rng.choice(3, p=TRANSITION[state])
        reg[d] = state

    base_dvar = ANNUAL_VOL ** 2 / TRADING_DAYS
    omega, alpha, beta = base_dvar * 0.05, 0.10, 0.85
    t_gap = rng.standard_t(4, n_days) / np.sqrt(2)
    t_min = rng.standard_t(5, (n_days, MIN_PER_DAY)) / np.sqrt(5 / 3)

    close = np.empty((n_days, MIN_PER_DAY))
    log_p = np.log(S0)
    dvar = base_dvar
    prev_eps = 0.0
    for d in range(n_days):
        r = REGIMES[reg[d]]
        dvar = omega + alpha * prev_eps ** 2 + beta * dvar
        day_var = dvar * VOL_MULT[r] ** 2
        day_start = log_p

        log_p += np.sqrt(day_var * GAP_VAR_SHARE) * t_gap[d]   # overnight gap
        sig = np.sqrt(day_var * (1 - GAP_VAR_SHARE) * w)
        rets = DRIFT[r] / TRADING_DAYS / MIN_PER_DAY + sig * t_min[d]

        if rng.random() < am_prob:                             # trend morning
            direction = 1 if rng.random() < TREND_DIR_BIAS[r] else -1
            extra = direction * am_sigma * np.sqrt(day_var)
            rets[OPEN_BAR:720] += extra / (720 - OPEN_BAR)

        path = log_p + np.cumsum(rets)
        close[d] = np.exp(path)
        log_p = path[-1]
        sd = np.sqrt(dvar)
        prev_eps = float(np.clip((log_p - day_start) / VOL_MULT[r], -3 * sd, 3 * sd))
    return close, reg


def backtest(close: np.ndarray, mode="asian", tgt_r=2.0, stop_mode="candle",
             allow_short=True, cutoff_bar=660, max_trades=2,
             risk=0.01, fee=0.0001, eq0=25_000.0) -> dict:
    n_days = close.shape[0]
    equity = eq0
    daily = np.zeros(n_days)
    trades = []
    or_bars = {"or5": 5, "or15": 15, "or30": 30}.get(mode, 0)

    for d in range(n_days):
        c = close[d]
        o = np.empty_like(c)
        o[0] = c[0]
        o[1:] = c[:-1]
        hi = np.maximum(o, c)
        lo = np.minimum(o, c)
        if mode == "asian":
            r0, r1 = 0, OPEN_BAR
        else:
            r0, r1 = OPEN_BAR, OPEN_BAR + or_bars
        rhi = hi[r0:r1].max()
        rlo = lo[r0:r1].min()
        width = rhi - rlo
        if width <= 0:
            continue
        pos = 0
        n_t = 0
        dpnl = 0.0
        entry = stop = tgt = qty = 0.0
        i = r1
        while i <= FLAT_BAR:
            if pos == 0 and n_t < max_trades and i < cutoff_bar:
                sig = 0
                if c[i] > rhi:
                    sig = 1
                elif allow_short and c[i] < rlo:
                    sig = -1
                if sig:
                    if stop_mode == "candle":
                        s = lo[i] if sig == 1 else hi[i]
                    else:                       # fraction of range width
                        f = float(stop_mode)
                        s = (rhi - f * width) if sig == 1 else (rlo + f * width)
                    e = c[i] * (1 + fee * sig)
                    rd = (e - s) * sig
                    if rd > 0:
                        pos, entry, stop = sig, e, s
                        tgt = e + sig * tgt_r * rd if tgt_r > 0 else np.nan
                        qty = equity * risk / rd
                        n_t += 1
            elif pos != 0:
                ex = 0.0
                if pos == 1 and lo[i] <= stop:
                    ex = stop
                elif pos == -1 and hi[i] >= stop:
                    ex = stop
                elif tgt_r > 0 and pos == 1 and hi[i] >= tgt:
                    ex = tgt
                elif tgt_r > 0 and pos == -1 and lo[i] <= tgt:
                    ex = tgt
                elif i == FLAT_BAR:
                    ex = c[i]
                if ex:
                    ex *= (1 - fee * pos)
                    pnl = (ex - entry) * qty * pos
                    equity += pnl
                    dpnl += pnl
                    trades.append(pnl)
                    pos = 0
            i += 1
        daily[d] = dpnl

    t = np.array(trades)
    eq = eq0 + np.cumsum(daily)
    full = np.concatenate([[eq0], eq])
    peak = np.maximum.accumulate(full)
    max_dd = ((full - peak) / peak).min()
    r = daily / full[:-1]
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0
    yrs = n_days / 252
    cagr = (eq[-1] / eq0) ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0
    wins, losses = t[t > 0], t[t <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() else np.inf
    act = daily[daily != 0]
    n_m = len(daily) // 21
    monthly = daily[:n_m * 21].reshape(n_m, 21).sum(axis=1)
    return {
        "sharpe": sharpe, "cagr": cagr, "max_dd": max_dd, "profit_factor": pf,
        "n_trades": len(t), "win_rate": len(wins) / len(t) if len(t) else 0.0,
        "pct_win_months": (monthly > 0).mean() if n_m else 0.0,
        "avg_month_usd": monthly.mean() if n_m else 0.0,
        "trades_per_day": len(t) / n_days,
    }


def score(m: dict) -> float:
    s = m["sharpe"] * min(1.0, m["n_trades"] / 50.0)
    s -= 2.0 * max(0.0, -m["max_dd"] - 0.25)
    return s


_cache = {}


def data(seed, **kw):
    key = (seed, tuple(sorted(kw.items())))
    if key not in _cache:
        _cache[key] = generate(750, seed=seed, **kw)[0]
    return _cache[key]


def eval_p(p: dict, seeds) -> dict:
    ms = [backtest(data(s), **p) for s in seeds]
    med = {k: float(np.median([m[k] for m in ms])) for k in ms[0]}
    med["score"] = float(np.median([score(m) for m in ms]))
    return med


def pstr(p):
    return (f"mode={p['mode']} tgt={p['tgt_r']} stop={p['stop_mode']} "
            f"cut={p.get('cutoff_bar', 660)} mt={p.get('max_trades', 2)}"
            f"{' +short' if p['allow_short'] else ' longonly'}")


def main():
    t0 = time.time()
    print("Stage 1: coarse grid (3 seeds)")
    grid1 = [dict(mode=m, tgt_r=t, stop_mode=s, allow_short=sh)
             for m, t, s, sh in itertools.product(
                 ["asian", "or5", "or15", "or30"],
                 [0.0, 1.5, 2.0, 3.0],
                 ["candle", "0.25", "0.5"],
                 [True, False])]
    res1 = []
    for i, p in enumerate(grid1):
        res1.append((eval_p(p, [1, 2, 3]), p))
        if (i + 1) % 24 == 0:
            print(f"  {i+1}/{len(grid1)} ({time.time()-t0:.0f}s)")
    res1.sort(key=lambda r: -r[0]["score"])
    for m, p in res1[:5]:
        print(f"  {m['score']:5.2f}  {pstr(p)}  sh={m['sharpe']:.2f} "
              f"pf={m['profit_factor']:.2f} tr={m['n_trades']:.0f}")
    best = res1[0][1]

    print("Stage 2: fine grid around winner")
    grid2 = [dict(best, cutoff_bar=cb, max_trades=mt, tgt_r=t)
             for cb, mt, t in itertools.product(
                 [600, 660, 780, 985],
                 [1, 2, 3],
                 sorted({max(0.0, best["tgt_r"] - 0.5), best["tgt_r"],
                         best["tgt_r"] + 0.5}))]
    res2 = [(eval_p(p, [1, 2, 3]), p) for p in grid2]
    res2.sort(key=lambda r: -r[0]["score"])
    for m, p in res2[:5]:
        print(f"  {m['score']:5.2f}  {pstr(p)}  sh={m['sharpe']:.2f} "
              f"pf={m['profit_factor']:.2f} tr={m['n_trades']:.0f}")
    final = res2[0][1]

    print("Stage 3: validation on 6 unseen seeds")
    val = [backtest(data(s), **final) for s in range(101, 107)]
    vmed = {k: float(np.median([m[k] for m in val])) for k in val[0]}
    print(f"  final: {pstr(final)}")
    print(f"  median: sharpe={vmed['sharpe']:.2f} cagr={vmed['cagr']:+.1%} "
          f"dd={vmed['max_dd']:.1%} pf={vmed['profit_factor']:.2f} "
          f"winM={vmed['pct_win_months']:.0%} avgM=${vmed['avg_month_usd']:,.0f}")

    print("Stage 4: sensitivity to trend-morning assumption")
    sens = {}
    for name, prob, sig in [("baseline(20%,0.9s)", 0.20, 0.9),
                            ("weak(10%,0.6s)", 0.10, 0.6),
                            ("none(0%)", 0.0, 0.0)]:
        ms = [backtest(generate(750, seed=s, am_prob=prob, am_sigma=sig)[0],
                       **final) for s in [201, 202, 203]]
        med = {k: float(np.median([m[k] for m in ms]))
               for k in ("sharpe", "cagr", "profit_factor", "max_dd")}
        sens[name] = med
        print(f"  {name}: sh={med['sharpe']:5.2f} cagr={med['cagr']:+7.1%} "
              f"pf={med['profit_factor']:.2f} dd={med['max_dd']:.1%}")

    out = {
        "final_params": final,
        "stage1_top5": [{"params": pstr(p), **m} for m, p in res1[:5]],
        "stage2_top5": [{"params": pstr(p), **m} for m, p in res2[:5]],
        "validation_runs": [{k: float(v) for k, v in m.items()} for m in val],
        "validation_median": vmed,
        "sensitivity": sens,
    }
    with open("results_london.json", "w") as f:
        json.dump(out, f, indent=2, default=float)
    print(f"wrote results_london.json ({time.time()-t0:.0f}s total)")


if __name__ == "__main__":
    main()
