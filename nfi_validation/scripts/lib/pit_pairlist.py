"""
Point-in-time (PIT) StaticPairList reconstruction - the Phase-3 survivorship fix.

Why this exists
---------------
Freqtrade's VolumePairList ranks pairs by *current* quote volume. Applying it to a
2023 backtest window is look-ahead on two axes:
  1. It includes pairs that only became liquid LATER (they wouldn't have been traded
     in 2023).
  2. It silently excludes pairs that were liquid in 2023 but have since been delisted
     or died - the classic survivorship bias, which inflates returns because the
     dead pairs (often -90%+) never enter the sample.

The correct construction for a window starting at date D is: rank pairs by their
average quote volume over a lookback window ENDING at D, using only pairs that had
data before D. This function does exactly that, from downloaded OHLCV.

Honest limitation (report this, per the rules)
----------------------------------------------
This can only be done cleanly for pairs whose historical candles you actually have.
If you seed the universe from NFI's *static* backtest pairlist (authored ~2024+),
you have already filtered to pairs that survived to ~2024 - so pairs that traded in
early 2023 and died before ~2024 are absent from the universe entirely, and no PIT
ranking can bring them back. `estimate_survivorship_bias()` quantifies the size of
that residual leak so it is never ignored silently.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd


def _ft_filename(pair: str, timeframe: str) -> str:
    """Freqtrade feather filename, e.g. 'BTC/USDT' 1h -> 'BTC_USDT-1h.feather'."""
    return f"{pair.replace('/', '_')}-{timeframe}.feather"


def pit_static_pairlist(
    data_dir: Path,
    universe: list[str],
    checkpoint: date,
    lookback_days: int = 30,
    top_n: int = 80,
    timeframe: str = "1h",
    min_history_days: int = 14,
) -> list[str]:
    """Return the top_n pairs by mean quote volume over [checkpoint-lookback, checkpoint).

    * universe        : candidate pairs (e.g. NFI static list) - see limitation above.
    * data_dir        : freqtrade data dir, e.g. user_data/data/binance
    * A pair is eligible only if it has >= min_history_days of candles ENDING before
      the checkpoint (i.e. it was actually trading and liquid enough back then).
    * quote volume proxy = sum(close * volume) over the lookback, per pair, /days.

    Deterministic given the same data. Emits the ranked list; the caller writes it as
    a StaticPairList config for that window.
    """
    cp = datetime(checkpoint.year, checkpoint.month, checkpoint.day)
    lo = cp - timedelta(days=lookback_days)
    rows = []
    for pair in universe:
        f = data_dir / _ft_filename(pair, timeframe)
        if not f.exists():
            continue
        df = pd.read_feather(f)
        if "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
        win = df[(df["date"] >= lo) & (df["date"] < cp)]
        # require enough real history strictly before the checkpoint
        hist = df[df["date"] < cp]
        if hist.empty:
            continue
        span_days = (cp - hist["date"].min()).days
        if span_days < min_history_days or win.empty:
            continue
        quote_vol = float((win["close"] * win["volume"]).sum())
        days = max((win["date"].max() - win["date"].min()).days, 1)
        rows.append((pair, quote_vol / days))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [p for p, _ in rows[:top_n]]


def estimate_survivorship_bias(
    data_dir: Path,
    universe: list[str],
    checkpoint: date,
    timeframe: str = "1h",
) -> dict:
    """Quantify how much of the intended universe is unobservable at `checkpoint`.

    Returns counts of pairs in the universe that have NO candles before the checkpoint
    (i.e. not-yet-listed -> would be wrongly included by a naive today's-list backtest)
    plus the coverage ratio. This is the measurable part of the leak; the unmeasurable
    part (pairs that died before the static list was authored) is bounded below by it
    and is discussed in the report.
    """
    cp = datetime(checkpoint.year, checkpoint.month, checkpoint.day)
    total = len(universe)
    have_data = 0
    listed_before_cp = 0
    for pair in universe:
        f = data_dir / _ft_filename(pair, timeframe)
        if not f.exists():
            continue
        have_data += 1
        df = pd.read_feather(f)
        df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None)
        if (df["date"] < cp).any():
            listed_before_cp += 1
    return {
        "checkpoint": checkpoint.isoformat(),
        "universe_size": total,
        "pairs_with_any_data": have_data,
        "pairs_listed_before_checkpoint": listed_before_cp,
        "not_yet_listed_at_checkpoint": have_data - listed_before_cp,
        "coverage_ratio": listed_before_cp / total if total else float("nan"),
        "note": (
            "not_yet_listed pairs would be look-ahead if traded in this window; "
            "pairs that died before the static list was authored are NOT in the "
            "universe at all and cannot be measured here - true survivorship bias is "
            ">= what this reports."
        ),
    }
