#!/usr/bin/env python3
"""
Convert Binance-vision kline ZIPs (from 02_fetch_binance_vision.py) into the
freqtrade feather format the rest of the pipeline consumes.

Run by the ANALYST after receiving/unpacking nfi_data.tgz. Needs pandas.

  python3 02c_bvision_to_feather.py --src ../binance_vision --out ../user_data/data/binance

Handles the two real-world quirks of Binance public data:
  * newer files carry a header row, older ones don't  -> coerced away
  * open_time precision changed from milliseconds to MICROSECONDS in 2025
    -> auto-detected per pair and normalized to UTC datetimes
Output: <BASE>_USDT-<tf>.feather with columns [date, open, high, low, close, volume],
UTC-aware, de-duplicated, ascending - exactly what freqtrade expects.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "count", "taker_base", "taker_quote", "ignore"]


def read_zip_csv(zip_path: Path) -> pd.DataFrame:
    """Read the single CSV inside a Binance kline zip -> frame with a UTC `date`.

    Unit detection is done PER FILE (not once for a whole pair): a pair's history
    spans the 2023 (ms) -> 2025+ (microsecond) precision change, so each file must
    be decoded on its own or early rows land in 1970.
    """
    with zipfile.ZipFile(zip_path) as z:
        raw = z.read(z.namelist()[0])
    df = pd.read_csv(io.BytesIO(raw), header=None, names=KLINE_COLS)
    # a header row (non-numeric open_time) becomes NaN and is dropped
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    df = df.dropna(subset=["open_time"])
    ts = df["open_time"].astype("int64")
    # ms ~1.7e12 for 2023-26; microseconds ~1.7e15.
    unit = "us" if int(ts.iloc[0]) > 1_000_000_000_000_000 else "ms"
    df["date"] = pd.to_datetime(ts, unit=unit, utc=True).astype("datetime64[ns, UTC]")
    return df


def to_freqtrade_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({
        "date": df["date"],
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "volume": df["volume"].astype(float),
    })
    return out.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def pair_filename(sym: str, tf: str) -> str:
    """'BTCUSDT' -> 'BTC_USDT-5m.feather' (all pairs are *USDT)."""
    base = sym[:-4] if sym.endswith("USDT") else sym
    return f"{base}_USDT-{tf}.feather"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True, help="binance_vision dir")
    ap.add_argument("--out", type=Path, required=True, help="freqtrade datadir/binance")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    # collect zips grouped by (SYM, tf) across monthly + daily
    groups: dict[tuple[str, str], list[Path]] = {}
    for zp in args.src.rglob("*.zip"):
        m = re.match(r"([A-Z0-9]+)-(\w+)-", zp.name)
        if not m:
            continue
        groups.setdefault((m.group(1), m.group(2)), []).append(zp)

    if not groups:
        print(f"No zips under {args.src}. Nothing to convert.")
        return 1

    n_pairs = len({s for s, _ in groups})
    print(f"Converting {n_pairs} pairs / {len(groups)} (pair,tf) groups...")
    written = 0
    for (sym, tf), zips in sorted(groups.items()):
        frames = []
        for zp in sorted(zips):
            try:
                frames.append(read_zip_csv(zp))
            except Exception as e:  # noqa: BLE001
                print(f"  skip {zp.name}: {e}")
        if not frames:
            continue
        merged = to_freqtrade_frame(pd.concat(frames, ignore_index=True))
        dest = args.out / pair_filename(sym, tf)
        merged.to_feather(dest)
        written += 1
        if written % 25 == 0:
            print(f"  wrote {written} files (last: {dest.name}, {len(merged):,} candles)")
    print(f"Done. Wrote {written} feather files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
