"""Nasdaq-100 intraday (RTH) data simulator.

Simulates 1-minute prices for the 9:30-16:00 session (390 min/day) and
aggregates to 5-minute OHLC bars (78 bars/day). Features:
  - overnight gaps (~25% of daily variance)
  - U-shaped intraday volatility (high at open/close, low midday)
  - daily Markov regime switching (bull/bear/sideways)
  - GARCH(1,1) daily volatility clustering, Student-t fat tails

Calibration target: NDX ~22% annualized vol, positive long-run drift.
"""

import numpy as np
import pandas as pd

MIN_PER_DAY = 390
BARS_PER_DAY = 78          # 5-min bars
TRADING_DAYS = 252

REGIMES = ["bull", "bear", "side"]
DRIFT = {"bull": 0.30, "bear": -0.35, "side": 0.05}          # annualized
VOL_MULT = {"bull": 0.85, "bear": 1.50, "side": 0.70}
TRANSITION = np.array([                                       # per day
    [0.9917, 0.0042, 0.0041],   # bull: avg ~120d
    [0.0083, 0.9833, 0.0084],   # bear: avg ~60d
    [0.0075, 0.0050, 0.9875],   # side: avg ~80d
])

ANNUAL_VOL = 0.19
GAP_VAR_SHARE = 0.25
S0 = 20_000.0

# Trend days: documented intraday momentum on index futures (opening move
# tends to continue). ~18% of days get an extra directional drift applied
# after the opening phase, direction biased by the prevailing regime.
TREND_DAY_PROB = 0.18
TREND_DAY_SIGMA = 1.2       # extra move in units of daily sigma
TREND_DIR_BIAS = {"bull": 0.65, "bear": 0.35, "side": 0.50}  # P(up-day)


def _u_shape(n_min: int) -> np.ndarray:
    """Intraday variance weights: high open, low midday, elevated close."""
    x = np.linspace(0, 1, n_min)
    w = 1.8 * np.exp(-x / 0.12) + 0.65 + 0.75 * np.exp((x - 1) / 0.10)
    return w / w.sum()


def generate_days(n_days: int = 1000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    u_w = _u_shape(MIN_PER_DAY)

    # daily regime path
    reg = np.empty(n_days, dtype=np.int64)
    state = rng.choice(3, p=[0.45, 0.20, 0.35])   # ~stationary distribution
    for d in range(n_days):
        state = rng.choice(3, p=TRANSITION[state])
        reg[d] = state

    base_dvar = ANNUAL_VOL ** 2 / TRADING_DAYS
    omega, alpha, beta = base_dvar * 0.05, 0.10, 0.85

    t_gap = rng.standard_t(4, n_days) / np.sqrt(4 / 2)
    t_intra = rng.standard_t(5, (n_days, MIN_PER_DAY)) / np.sqrt(5 / 3)

    log_p = np.log(S0)
    minutes = np.empty((n_days, MIN_PER_DAY))
    opens = np.empty(n_days)
    dvar = base_dvar
    prev_eps = 0.0
    for d in range(n_days):
        r = REGIMES[reg[d]]
        dvar = omega + alpha * prev_eps ** 2 + beta * dvar
        day_var = dvar * VOL_MULT[r] ** 2
        drift_min = DRIFT[r] * 0.7 / TRADING_DAYS / MIN_PER_DAY  # 70% intraday

        # overnight gap
        gap = np.sqrt(day_var * GAP_VAR_SHARE) * t_gap[d] \
            + DRIFT[r] / TRADING_DAYS * 0.3
        log_p += gap
        opens[d] = np.exp(log_p)

        # intraday path with U-shaped variance allocation
        day_start = log_p - gap
        sig_min = np.sqrt(day_var * (1 - GAP_VAR_SHARE) * u_w)
        rets = drift_min + sig_min * t_intra[d]

        # trend day: extra directional drift after the opening 15 minutes
        if rng.random() < TREND_DAY_PROB:
            direction = 1 if rng.random() < TREND_DIR_BIAS[r] else -1
            extra = direction * TREND_DAY_SIGMA * np.sqrt(day_var)
            rets[15:] += extra / (MIN_PER_DAY - 15)
        path = log_p + np.cumsum(rets)
        minutes[d] = np.exp(path)
        log_p = path[-1]
        # realized close-to-close return feeds the GARCH recursion,
        # normalized by the regime multiplier (keeps persistence < 1 in
        # high-vol regimes) and winsorized at 3 sigma against fat tails
        sd = np.sqrt(dvar)
        eps = (log_p - day_start) / VOL_MULT[r]
        prev_eps = float(np.clip(eps, -3 * sd, 3 * sd))

    # aggregate 1-min -> 5-min OHLC
    m = minutes.reshape(n_days, BARS_PER_DAY, 5)
    close = m[:, :, -1]
    high = m.max(axis=2)
    low = m.min(axis=2)
    open_ = np.empty_like(close)
    open_[:, 0] = opens
    open_[:, 1:] = close[:, :-1]
    high = np.maximum(high, np.maximum(open_, close))
    low = np.minimum(low, np.minimum(open_, close))

    n = n_days * BARS_PER_DAY
    day_idx = np.repeat(np.arange(n_days), BARS_PER_DAY)
    bar_idx = np.tile(np.arange(BARS_PER_DAY), n_days)
    return pd.DataFrame({
        "open": open_.ravel(), "high": high.ravel(),
        "low": low.ravel(), "close": close.ravel(),
        "day": day_idx, "bar": bar_idx,
        "regime": reg[day_idx],
    })


if __name__ == "__main__":
    df = generate_days(1000, seed=1)
    dc = df.groupby("day").close.last()
    r = np.log(dc / dc.shift(1)).dropna()
    do = df.groupby("day").open.first()
    gap = np.log(do.values[1:] / dc.values[:-1])
    print(f"annualized vol : {r.std() * np.sqrt(252):.1%}")
    print(f"total return   : {dc.iloc[-1] / dc.iloc[0] - 1:+.1%} over {len(dc)/252:.1f}y")
    print(f"kurtosis(daily): {r.kurtosis():.1f}")
    print(f"gap var share  : {gap.var() / r.var():.1%}")
    b0 = df[df.bar < 6].groupby("day").apply(lambda x: np.log(x.close.iloc[-1]/x.open.iloc[0]).__abs__(), include_groups=False)
    print(f"first-30min avg abs move: {b0.mean():.3%}")
