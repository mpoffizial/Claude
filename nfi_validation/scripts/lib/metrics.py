"""
Performance metrics for closed-trade backtest output (Freqtrade-shaped).

Every metric documents its assumptions in the docstring, because the task rule is:
"no number without the assumptions under which it was produced."

Conventions
-----------
* A "trades" DataFrame has at least: profit_abs (float, quote ccy), profit_ratio
  (float, e.g. 0.012 = +1.2%), open_date, close_date (datetime64). This is exactly
  Freqtrade's backtest-result trade schema.
* Equity is marked-to-market on CLOSED trades only (realized equity). Intra-trade
  (open-position) drawdown is therefore NOT captured here and will understate true
  drawdown - this is stated wherever drawdown is reported.
* Starting capital defaults to 10_000 (matches config dry_run_wallet).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 365.25  # crypto trades 24/7/365


def realized_equity_curve(trades: pd.DataFrame, starting_capital: float = 10_000.0) -> pd.Series:
    """Equity after each closed trade, indexed by close_date (sorted).

    Assumes trades are booked sequentially at close (realized PnL). With
    max_open_trades > 1 this is an approximation of true account equity, but it is
    the standard basis for trade-sequence Monte Carlo (Phase 5) and is stated as such.
    """
    if trades.empty:
        return pd.Series([starting_capital], index=pd.to_datetime(["1970-01-01"]))
    t = trades.sort_values("close_date")
    eq = starting_capital + t["profit_abs"].cumsum()
    eq.index = pd.to_datetime(t["close_date"].values)
    # prepend the starting point
    start = pd.Series([starting_capital], index=[eq.index.min() - pd.Timedelta(seconds=1)])
    return pd.concat([start, eq])


def cagr(equity: pd.Series) -> float:
    """Compound annual growth rate from first to last equity point.

    Assumption: uses actual calendar span of the equity curve. Returns NaN if the
    span is < 1 day or starting/'ending equity <= 0.
    """
    if len(equity) < 2:
        return float("nan")
    days = (equity.index[-1] - equity.index[0]).total_seconds() / 86400.0
    if days < 1 or equity.iloc[0] <= 0 or equity.iloc[-1] <= 0:
        return float("nan")
    return (equity.iloc[-1] / equity.iloc[0]) ** (TRADING_DAYS / days) - 1.0


def max_drawdown(equity: pd.Series) -> float:
    """Maximum peak-to-trough decline as a POSITIVE fraction (0.30 = -30%).

    Realized-equity basis (see module docstring) - understates true drawdown.
    """
    if len(equity) < 2:
        return float("nan")
    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    return float(-dd.min())


def _daily_returns(equity: pd.Series) -> pd.Series:
    """Daily simple returns from the realized-equity curve (resampled, ffilled)."""
    daily = equity.resample("1D").last().ffill()
    return daily.pct_change().dropna()


def sortino(equity: pd.Series, rf_annual: float = 0.0) -> float:
    """Annualized Sortino ratio from daily realized returns.

    Assumptions: daily returns; downside deviation uses returns below the daily
    risk-free target (rf_annual/365.25); annualized by sqrt(365.25). Returns NaN if
    there is no downside deviation (no losing days) or < 5 daily observations.
    """
    r = _daily_returns(equity)
    if len(r) < 5:
        return float("nan")
    rf_daily = rf_annual / TRADING_DAYS
    excess = r - rf_daily
    downside = excess[excess < 0]
    dd = np.sqrt((downside ** 2).mean()) if len(downside) else 0.0
    if dd == 0:
        return float("nan")
    return float(excess.mean() / dd * np.sqrt(TRADING_DAYS))


def sharpe(equity: pd.Series, rf_annual: float = 0.0) -> float:
    """Annualized Sharpe from daily realized returns (for completeness)."""
    r = _daily_returns(equity)
    if len(r) < 5 or r.std(ddof=1) == 0:
        return float("nan")
    rf_daily = rf_annual / TRADING_DAYS
    return float((r.mean() - rf_daily) / r.std(ddof=1) * np.sqrt(TRADING_DAYS))


def calmar(equity: pd.Series) -> float:
    """CAGR / MaxDD. Returns NaN if MaxDD == 0."""
    mdd = max_drawdown(equity)
    if not np.isfinite(mdd) or mdd == 0:
        return float("nan")
    return cagr(equity) / mdd


def sqn(trades: pd.DataFrame) -> float:
    """Van Tharp System Quality Number = sqrt(N) * mean(R) / std(R).

    Assumption: R is per-trade profit_ratio (return on the position), NOT a true
    R-multiple vs. initial risk (Freqtrade backtests don't expose per-trade initial
    risk). Stated so the number isn't mistaken for a risk-normalized SQN.
    Capped display range is the caller's job; NaN if < 2 trades or zero variance.
    """
    if len(trades) < 2:
        return float("nan")
    r = trades["profit_ratio"].to_numpy(dtype=float)
    s = r.std(ddof=1)
    if s == 0:
        return float("nan")
    return float(np.sqrt(len(r)) * r.mean() / s)


def profit_factor(trades: pd.DataFrame) -> float:
    """Gross profit / gross loss (absolute). NaN if no losses (undefined/infinite)."""
    if trades.empty:
        return float("nan")
    p = trades["profit_abs"].to_numpy(dtype=float)
    gross_win = p[p > 0].sum()
    gross_loss = -p[p < 0].sum()
    if gross_loss == 0:
        return float("nan")
    return float(gross_win / gross_loss)


def avg_hold_hours(trades: pd.DataFrame) -> float:
    """Mean holding time in hours."""
    if trades.empty:
        return float("nan")
    dur = (pd.to_datetime(trades["close_date"]) - pd.to_datetime(trades["open_date"]))
    return float(dur.dt.total_seconds().mean() / 3600.0)


def summarize(trades: pd.DataFrame, starting_capital: float = 10_000.0) -> dict:
    """All headline metrics for a trade set, as a flat dict.

    Every value carries the assumptions documented above. `contaminated` is left
    for the caller to stamp (True for the Phase-2 baseline, False for time-frozen).
    """
    eq = realized_equity_curve(trades, starting_capital)
    total_profit = float(trades["profit_abs"].sum()) if not trades.empty else 0.0
    wins = int((trades["profit_ratio"] > 0).sum()) if not trades.empty else 0
    return {
        "n_trades": int(len(trades)),
        "win_rate": wins / len(trades) if len(trades) else float("nan"),
        "total_profit_abs": total_profit,
        "total_profit_pct": total_profit / starting_capital,
        "cagr": cagr(eq),
        "max_drawdown": max_drawdown(eq),
        "sortino": sortino(eq),
        "sharpe": sharpe(eq),
        "calmar": calmar(eq),
        "sqn": sqn(trades),
        "profit_factor": profit_factor(trades),
        "avg_hold_hours": avg_hold_hours(trades),
    }
