"""Adaptive Momentum Breakout (AMB) strategy + event-driven backtester.

Rules
-----
Entry (long):  close breaks above the N-bar Donchian high, price above the
               trend EMA, and ATR-percentile inside the tradeable band.
Entry (short): symmetric (optional, off by default).
Exit:          Chandelier ATR trailing stop (ratchets only), initial ATR
               stop, or close crossing the opposite fast Donchian channel.
Sizing:        fixed-fractional risk per trade based on stop distance.
Costs:         fee + slippage charged on each side.
"""

from dataclasses import dataclass
import numpy as np
import pandas as pd

HOURS_PER_YEAR = 24 * 365


@dataclass
class Params:
    entry_len: int = 48        # Donchian breakout lookback
    exit_len: int = 24         # opposite-channel fast exit lookback
    trail_mult: float = 3.0    # chandelier ATR multiple
    sl_mult: float = 2.0       # initial stop ATR multiple
    ema_len: int = 200         # trend filter EMA
    atr_len: int = 14
    vol_filter: bool = True    # require ATR%-rank in [low, high]
    vol_low: float = 0.15
    vol_high: float = 0.95
    allow_short: bool = False
    cooldown: int = 0          # bars to wait after a losing trade
    risk_pct: float = 0.01     # equity fraction risked per trade
    fee: float = 0.0007        # 0.05% fee + 0.02% slippage per side


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    alpha = 2.0 / (n + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def _atr(h, l, c, n):
    tr = np.maximum(h[1:] - l[1:],
                    np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    tr = np.concatenate([[h[0] - l[0]], tr])
    return _ema(tr, 2 * n - 1)  # Wilder smoothing


def _rolling_extreme(x: np.ndarray, n: int, is_max: bool) -> np.ndarray:
    s = pd.Series(x)
    r = s.rolling(n, min_periods=1)
    return (r.max() if is_max else r.min()).to_numpy()


def _pct_rank(x: np.ndarray, n: int) -> np.ndarray:
    s = pd.Series(x)
    return s.rolling(n, min_periods=n // 4).rank(pct=True).fillna(0.5).to_numpy()


def backtest(df: pd.DataFrame, p: Params, equity0: float = 10_000.0) -> dict:
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    n = len(c)

    atr = _atr(h, l, c, p.atr_len)
    ema = _ema(c, p.ema_len)
    # channels computed on data up to the *previous* bar
    don_hi = np.concatenate([[np.inf], _rolling_extreme(c, p.entry_len, True)[:-1]])
    don_lo = np.concatenate([[-np.inf], _rolling_extreme(c, p.entry_len, False)[:-1]])
    exit_lo = np.concatenate([[-np.inf], _rolling_extreme(c, p.exit_len, False)[:-1]])
    exit_hi = np.concatenate([[np.inf], _rolling_extreme(c, p.exit_len, True)[:-1]])
    vol_rank = _pct_rank(atr / c, 200)

    equity = equity0
    eq_curve = np.empty(n)
    pos = 0            # +1 long, -1 short, 0 flat
    qty = stop = entry_px = 0.0
    pending = 0        # signal generated on close, executed next bar open
    trades = []
    last_loss_bar = -10**9
    warmup = max(p.entry_len, p.ema_len, p.atr_len) + 1

    for i in range(n):
        # --- execute pending entry at this bar's open ---
        if pending != 0 and pos == 0:
            entry_px = o[i] * (1 + p.fee * pending)
            stop_dist = p.sl_mult * atr[i - 1]
            if stop_dist > 0:
                qty = (equity * p.risk_pct) / stop_dist
                pos = pending
                stop = entry_px - pos * stop_dist
            pending = 0

        if pos != 0:
            # --- intrabar stop check (conservative: stop price fill) ---
            hit = (pos == 1 and l[i] <= stop) or (pos == -1 and h[i] >= stop)
            if hit:
                exit_px = stop * (1 - p.fee * pos)
                pnl = (exit_px - entry_px) * qty * pos
                equity += pnl
                trades.append(pnl)
                if pnl < 0:
                    last_loss_bar = i
                pos = 0
            else:
                # chandelier ratchet
                if pos == 1:
                    stop = max(stop, h[i] - p.trail_mult * atr[i])
                    channel_exit = c[i] < exit_lo[i]
                else:
                    stop = min(stop, l[i] + p.trail_mult * atr[i])
                    channel_exit = c[i] > exit_hi[i]
                if channel_exit:
                    exit_px = c[i] * (1 - p.fee * pos)
                    pnl = (exit_px - entry_px) * qty * pos
                    equity += pnl
                    trades.append(pnl)
                    if pnl < 0:
                        last_loss_bar = i
                    pos = 0

        # --- entry signals on close ---
        if (pos == 0 and pending == 0 and i >= warmup
                and i - last_loss_bar > p.cooldown):
            vol_ok = (not p.vol_filter) or (p.vol_low <= vol_rank[i] <= p.vol_high)
            if vol_ok:
                if c[i] > don_hi[i] and c[i] > ema[i]:
                    pending = 1
                elif p.allow_short and c[i] < don_lo[i] and c[i] < ema[i]:
                    pending = -1

        eq_curve[i] = equity + (c[i] - entry_px) * qty * pos if pos else equity

    return _metrics(np.array(trades), eq_curve, equity0, n)


def _metrics(trades: np.ndarray, eq: np.ndarray, equity0: float, n_bars: int) -> dict:
    years = n_bars / HOURS_PER_YEAR
    total_ret = eq[-1] / equity0 - 1
    cagr = (eq[-1] / equity0) ** (1 / years) - 1 if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    max_dd = ((eq - peak) / peak).min()
    day_eq = eq[::24]
    r = np.diff(day_eq) / day_eq[:-1]
    sharpe = r.mean() / r.std() * np.sqrt(365) if len(r) > 2 and r.std() > 0 else 0.0
    wins, losses = trades[trades > 0], trades[trades <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else np.inf
    return {
        "total_return": total_ret, "cagr": cagr, "max_dd": max_dd,
        "sharpe": sharpe, "profit_factor": pf,
        "n_trades": len(trades),
        "win_rate": len(wins) / len(trades) if len(trades) else 0.0,
        "avg_win": wins.mean() if len(wins) else 0.0,
        "avg_loss": losses.mean() if len(losses) else 0.0,
        "equity_curve": eq,
    }


def robust_score(m: dict) -> float:
    """Anti-overfit objective: Sharpe, penalized for thin trade samples
    and deep drawdowns."""
    s = m["sharpe"] * min(1.0, m["n_trades"] / 30.0)
    s -= 2.0 * max(0.0, -m["max_dd"] - 0.35)
    return s


if __name__ == "__main__":
    from data import generate_ohlcv
    df = generate_ohlcv(30_000, "BTC", seed=1)
    m = backtest(df, Params())
    print({k: (round(v, 3) if isinstance(v, float) else v)
           for k, v in m.items() if k != "equity_curve"})
