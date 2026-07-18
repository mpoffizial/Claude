"""Opening Range Breakout (ORB) strategy for Nasdaq (NQ/MNQ, 5-min RTH).

Rules
-----
Opening Range: high/low of the first `or_min` minutes of the session.
Entry:  close breaks OR-high + buffer (long) / OR-low - buffer (short),
        only before the entry cutoff, max `max_trades` per day.
Filter: daily trend EMA (close of prior days) for direction bias.
Stop:   `stop_frac` x OR-width beyond entry.
Target: `tgt_r` x initial risk (0 = no target, hold to EOD).
        Optional breakeven move after +1R.
EOD:    always flat on the last bar of the day - no overnight risk.
Sizing: `risk_pct` of equity per trade.
"""

from dataclasses import dataclass
import numpy as np
import pandas as pd

BARS_PER_DAY = 78


@dataclass
class Params:
    or_min: int = 30           # opening range length in minutes
    buffer_frac: float = 0.0   # entry buffer as fraction of OR width
    stop_frac: float = 1.0     # stop distance as fraction of OR width
    tgt_r: float = 2.0         # profit target in R (0 = EOD exit only)
    breakeven: bool = False    # move stop to entry after +1R
    cutoff_bar: int = 42       # no entries after this bar (42 = 13:00)
    ema_days: int = 20         # daily trend EMA (0 = filter off)
    allow_short: bool = True
    max_trades: int = 2        # per day
    risk_pct: float = 0.01
    fee: float = 0.00015       # per side (MNQ commission + 1 tick slippage)


def _daily_ema(day_close: np.ndarray, n: int) -> np.ndarray:
    alpha = 2.0 / (n + 1.0)
    out = np.empty_like(day_close)
    out[0] = day_close[0]
    for i in range(1, len(day_close)):
        out[i] = alpha * day_close[i] + (1 - alpha) * out[i - 1]
    return out


def backtest(df: pd.DataFrame, p: Params, equity0: float = 25_000.0) -> dict:
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    day = df["day"].to_numpy()
    bar = df["bar"].to_numpy()
    n_days = day[-1] + 1

    day_close = c[bar == BARS_PER_DAY - 1]
    ema_prev = np.empty(n_days)               # EMA as of *yesterday's* close
    if p.ema_days > 0:
        e = _daily_ema(day_close, p.ema_days)
        ema_prev[0], ema_prev[1:] = np.nan, e[:-1]
    or_bars = p.or_min // 5

    equity = equity0
    daily_pnl = np.zeros(n_days)
    trades = []
    warmup_days = p.ema_days

    i = 0
    n = len(c)
    while i < n:
        d = day[i]
        day_start = i
        day_end = day_start + BARS_PER_DAY - 1
        if d < warmup_days:
            i = day_end + 1
            continue

        or_hi = h[day_start:day_start + or_bars].max()
        or_lo = l[day_start:day_start + or_bars].min()
        width = or_hi - or_lo
        buf = p.buffer_frac * width
        long_ok = p.ema_days == 0 or (
            not np.isnan(ema_prev[d]) and day_close[d - 1] > ema_prev[d])
        short_ok = p.allow_short and (
            p.ema_days == 0 or (not np.isnan(ema_prev[d]) and day_close[d - 1] < ema_prev[d]))

        pos = 0
        entry_px = stop = tgt = qty = 0.0
        n_today = 0
        day_pnl = 0.0

        j = day_start + or_bars
        pending = 0
        while j <= day_end:
            # execute pending entry at this bar's open
            if pending != 0 and pos == 0:
                entry_px = o[j] * (1 + p.fee * pending)
                risk = p.stop_frac * width
                if risk > 0 and width > 0:
                    qty = equity * p.risk_pct / risk
                    pos = pending
                    stop = entry_px - pos * risk
                    tgt = entry_px + pos * p.tgt_r * risk if p.tgt_r > 0 else np.nan
                    n_today += 1
                pending = 0

            if pos != 0:
                exit_px = 0.0
                # conservative intrabar order: stop first, then target
                if pos == 1 and l[j] <= stop:
                    exit_px = stop
                elif pos == -1 and h[j] >= stop:
                    exit_px = stop
                elif p.tgt_r > 0 and pos == 1 and h[j] >= tgt:
                    exit_px = tgt
                elif p.tgt_r > 0 and pos == -1 and l[j] <= tgt:
                    exit_px = tgt
                elif j == day_end:
                    exit_px = c[j]                      # EOD flat
                if exit_px:
                    exit_px *= (1 - p.fee * pos)
                    pnl = (exit_px - entry_px) * qty * pos
                    equity += pnl
                    day_pnl += pnl
                    trades.append(pnl)
                    pos = 0
                elif p.breakeven:
                    # after price moves 1R in favor, stop -> entry
                    r1 = p.stop_frac * width
                    if pos == 1 and h[j] >= entry_px + r1:
                        stop = max(stop, entry_px)
                    elif pos == -1 and l[j] <= entry_px - r1:
                        stop = min(stop, entry_px)

            # new signals (close-based), executed next bar
            if (pos == 0 and pending == 0 and n_today < p.max_trades
                    and j < min(day_start + p.cutoff_bar, day_end)):
                if long_ok and c[j] > or_hi + buf:
                    pending = 1
                elif short_ok and c[j] < or_lo - buf:
                    pending = -1
            j += 1

        daily_pnl[d] = day_pnl
        i = day_end + 1

    return _metrics(np.array(trades), daily_pnl[warmup_days:], equity0)


def _metrics(trades: np.ndarray, daily_pnl: np.ndarray, equity0: float) -> dict:
    eq = equity0 + np.cumsum(daily_pnl)
    years = len(daily_pnl) / 252
    total_ret = eq[-1] / equity0 - 1
    cagr = (eq[-1] / equity0) ** (1 / years) - 1 if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(np.concatenate([[equity0], eq]))
    max_dd = ((np.concatenate([[equity0], eq]) - peak) / peak).min()
    r = daily_pnl / np.concatenate([[equity0], eq[:-1]])
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0
    wins, losses = trades[trades > 0], trades[trades <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else np.inf

    active = daily_pnl != 0
    act_pnl = daily_pnl[active]
    # longest run of consecutive losing *active* days
    max_lose_run = run = 0
    for x in act_pnl:
        run = run + 1 if x < 0 else 0
        max_lose_run = max(max_lose_run, run)
    # monthly aggregation (21 trading days)
    n_m = len(daily_pnl) // 21
    monthly = daily_pnl[:n_m * 21].reshape(n_m, 21).sum(axis=1)

    return {
        "total_return": total_ret, "cagr": cagr, "max_dd": max_dd,
        "sharpe": sharpe, "profit_factor": pf,
        "n_trades": len(trades),
        "win_rate": len(wins) / len(trades) if len(trades) else 0.0,
        "trades_per_day": len(trades) / len(daily_pnl),
        "active_days": active.mean(),
        "pct_win_days": (act_pnl > 0).mean() if len(act_pnl) else 0.0,
        "avg_day_usd": act_pnl.mean() if len(act_pnl) else 0.0,
        "worst_day_usd": act_pnl.min() if len(act_pnl) else 0.0,
        "max_losing_days_run": max_lose_run,
        "pct_win_months": (monthly > 0).mean() if n_m else 0.0,
        "avg_month_usd": monthly.mean() if n_m else 0.0,
        "worst_month_usd": monthly.min() if n_m else 0.0,
        "equity_curve": eq,
    }


def robust_score(m: dict) -> float:
    s = m["sharpe"] * min(1.0, m["n_trades"] / 50.0)
    s -= 2.0 * max(0.0, -m["max_dd"] - 0.25)
    return s


if __name__ == "__main__":
    from data_nq import generate_days
    df = generate_days(1000, seed=1)
    m = backtest(df, Params())
    print({k: (round(v, 3) if isinstance(v, (float, np.floating)) else v)
           for k, v in m.items() if k != "equity_curve"})
