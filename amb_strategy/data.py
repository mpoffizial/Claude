"""Synthetic OHLCV data generator with realistic market microstructure.

Simulates prices at 5-minute resolution and aggregates to 1H bars.
Features: Markov regime switching (bull/bear/sideways), GARCH(1,1)
volatility clustering, Student-t fat tails and occasional jumps.

Presets are calibrated to real-world annualized volatility:
  BTC  ~ 55% p.a., slight positive drift
  GOLD ~ 18% p.a., slight positive drift
"""

import numpy as np
import pandas as pd

STEPS_PER_HOUR = 12          # 5-minute sub-steps
HOURS_PER_YEAR = 24 * 365

PRESETS = {
    "BTC": dict(
        s0=60_000.0,
        annual_vol=0.55,
        regime_drift={"bull": 1.20, "bear": -0.90, "side": 0.00},  # annualized
        regime_vol_mult={"bull": 0.9, "bear": 1.4, "side": 0.7},
        jump_prob=0.0008, jump_scale=0.025,
    ),
    "GOLD": dict(
        s0=2_400.0,
        annual_vol=0.18,
        regime_drift={"bull": 0.35, "bear": -0.25, "side": 0.00},
        regime_vol_mult={"bull": 0.9, "bear": 1.3, "side": 0.75},
        jump_prob=0.0004, jump_scale=0.012,
    ),
}

# Markov transition matrix (rows: from bull/bear/side), per hour
TRANSITION = np.array([
    [0.9990, 0.0004, 0.0006],
    [0.0006, 0.9988, 0.0006],
    [0.0007, 0.0005, 0.9988],
])
REGIMES = ["bull", "bear", "side"]


def generate_ohlcv(n_hours: int = 30_000, preset: str = "BTC",
                   seed: int = 42) -> pd.DataFrame:
    """Return a DataFrame with open/high/low/close/volume 1H bars."""
    cfg = PRESETS[preset]
    rng = np.random.default_rng(seed)
    n_steps = n_hours * STEPS_PER_HOUR
    dt = 1.0 / (HOURS_PER_YEAR * STEPS_PER_HOUR)  # in years

    # --- regime path (changes at hourly boundaries) ---
    regimes = np.empty(n_hours, dtype=np.int64)
    state = rng.integers(0, 3)
    for h in range(n_hours):
        state = rng.choice(3, p=TRANSITION[state])
        regimes[h] = state
    regime_steps = np.repeat(regimes, STEPS_PER_HOUR)

    drift = np.array([cfg["regime_drift"][r] for r in REGIMES])[regime_steps]
    vol_mult = np.array([cfg["regime_vol_mult"][r] for r in REGIMES])[regime_steps]

    # --- GARCH(1,1)-style volatility clustering on top of regime vol ---
    base_var = (cfg["annual_vol"] ** 2) * dt
    omega, alpha, beta = base_var * 0.05, 0.08, 0.87
    t_innov = rng.standard_t(df=4, size=n_steps) / np.sqrt(4 / 2)  # unit variance

    # --- jumps ---
    jumps = (rng.random(n_steps) < cfg["jump_prob"]) * \
        rng.normal(0, cfg["jump_scale"], n_steps)

    # recursion uses only the pure GARCH shock (eps), so regime multipliers,
    # drift and jumps cannot push effective persistence above alpha+beta
    log_ret = np.empty(n_steps)
    var = base_var
    for i in range(n_steps):
        eps = np.sqrt(var) * t_innov[i]
        log_ret[i] = drift[i] * dt + eps * vol_mult[i] + jumps[i]
        var = omega + alpha * eps * eps + beta * var

    prices = cfg["s0"] * np.exp(np.cumsum(log_ret))

    # --- aggregate 5-min path into 1H OHLC ---
    p = prices.reshape(n_hours, STEPS_PER_HOUR)
    close = p[:, -1]
    high = p.max(axis=1)
    low = p.min(axis=1)
    open_ = np.concatenate([[cfg["s0"]], close[:-1]])
    high = np.maximum(high, np.maximum(open_, close))
    low = np.minimum(low, np.minimum(open_, close))

    # volume: correlated with absolute hourly return + noise
    hourly_ret = np.abs(np.diff(np.log(prices[::STEPS_PER_HOUR]), prepend=0))
    volume = 1000 * (1 + 8 * hourly_ret / (hourly_ret.mean() + 1e-12)
                     ) * rng.lognormal(0, 0.35, n_hours)

    idx = pd.date_range("2021-01-01", periods=n_hours, freq="h")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close,
         "volume": volume, "regime": regimes}, index=idx)


if __name__ == "__main__":
    df = generate_ohlcv(30_000, "BTC", seed=1)
    ret = np.log(df.close / df.close.shift(1)).dropna()
    print(df.head())
    print(f"annualized vol: {ret.std() * np.sqrt(HOURS_PER_YEAR):.2%}")
    print(f"total return:   {df.close.iloc[-1] / df.close.iloc[0] - 1:+.1%}")
    print(f"kurtosis:       {ret.kurtosis():.1f}")
