"""
Kursdaten für den ATR-Breakout-Backtest laden (1h-Kerzen + USD/CHF).

Quellen:
- BTC:  Binance USDⓈ-M Perpetual BTCUSDT, 1h-Klines von data.binance.vision
        (gleicher Feed wie BINANCE:BTCUSDT.P auf TradingView).
- Gold: Dukascopy XAUUSD 1h-Bid-Kerzen (datafeed.dukascopy.com).
- FX:   EZB-Referenzkurse USD/CHF über api.frankfurter.app (Tageswerte).

Alternativ können CSV-Dateien geladen werden, z. B. ein TradingView-Export
("Chartdaten exportieren") mit Spalten time, open, high, low, close.
"""

import io
import json
import lzma
import struct
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

CACHE_DIR = Path(__file__).resolve().parent.parent / "backtest_data"
USER_AGENT = "Mozilla/5.0 (atr-breakout-backtest)"


def _get(url, timeout=60):
    """HTTP GET; gibt None bei 404 zurück (Datei existiert noch nicht)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _finish(df):
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[["open", "high", "low", "close"]].astype(float)


# ----------------------------------------------------------------------------
# BTC – Binance Perpetual
# ----------------------------------------------------------------------------

def _parse_binance_csv(raw):
    df = pd.read_csv(io.BytesIO(raw), header=None)
    if not str(df.iloc[0, 0]).strip().isdigit():  # neuere Dateien haben Kopfzeile
        df = df.iloc[1:]
    t = df[0].astype("int64")
    t = np.where(t > 10**14, t // 1000, t)  # µs -> ms
    out = pd.DataFrame({"open": df[1].values, "high": df[2].values,
                        "low": df[3].values, "close": df[4].values},
                       index=pd.to_datetime(t, unit="ms", utc=True))
    return out


def download_binance(symbol="BTCUSDT", start=date(2020, 1, 1), end=None, market="um"):
    """Lädt 1h-Klines monatsweise, für unvollständige Monate tageweise."""
    end = end or datetime.now(timezone.utc).date()
    base = f"https://data.binance.vision/data/futures/{market}"
    frames = []
    for y, m in _months(start, end):
        name = f"{symbol}-1h-{y}-{m:02d}"
        raw = _get(f"{base}/monthly/klines/{symbol}/1h/{name}.zip")
        if raw is not None:
            frames.append(_parse_binance_csv(zipfile.ZipFile(io.BytesIO(raw)).read(f"{name}.csv")))
            continue
        d = date(y, m, 1)
        while d.month == m and d <= end:
            dname = f"{symbol}-1h-{d.isoformat()}"
            raw = _get(f"{base}/daily/klines/{symbol}/1h/{dname}.zip")
            if raw is not None:
                frames.append(_parse_binance_csv(zipfile.ZipFile(io.BytesIO(raw)).read(f"{dname}.csv")))
            d += timedelta(days=1)
    if not frames:
        raise RuntimeError("Keine Binance-Daten erhalten.")
    return _finish(pd.concat(frames))


# ----------------------------------------------------------------------------
# Gold – Dukascopy XAUUSD
# ----------------------------------------------------------------------------

def _parse_bi5_candles(raw, t0, scale):
    if not raw:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    data = lzma.decompress(raw)
    rows = [struct.unpack(">5if", data[k:k + 24]) for k in range(0, len(data) - 23, 24)]
    a = np.array(rows, dtype=float)
    idx = pd.to_datetime(t0, utc=True) + pd.to_timedelta(a[:, 0], unit="s")
    # Dukascopy-Reihenfolge: time, open, close, low, high, volume
    return pd.DataFrame({"open": a[:, 1] / scale, "high": a[:, 4] / scale,
                         "low": a[:, 3] / scale, "close": a[:, 2] / scale,
                         "volume": a[:, 5]}, index=idx)


def download_dukascopy(instrument="XAUUSD", start=date(2020, 1, 1), end=None, scale=1000.0):
    """1h-Bid-Kerzen; aktueller Monat aus Minutenkerzen zusammengesetzt.
    Kerzen ohne Volumen (Markt geschlossen) werden entfernt."""
    end = end or datetime.now(timezone.utc).date()
    base = f"https://datafeed.dukascopy.com/datafeed/{instrument}"
    frames = []
    for y, m in _months(start, end):
        raw = _get(f"{base}/{y}/{m - 1:02d}/BID_candles_hour_1.bi5")
        if raw is not None:
            frames.append(_parse_bi5_candles(raw, datetime(y, m, 1), scale))
            continue
        d = date(y, m, 1)
        while d.month == m and d <= end:
            raw = _get(f"{base}/{y}/{m - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5")
            if raw:
                mins = _parse_bi5_candles(raw, datetime(d.year, d.month, d.day), scale)
                mins = mins[mins["volume"] > 0]
                if len(mins):
                    hrs = mins.resample("1h").agg({"open": "first", "high": "max", "low": "min",
                                                   "close": "last", "volume": "sum"}).dropna()
                    frames.append(hrs)
            d += timedelta(days=1)
    if not frames:
        raise RuntimeError("Keine Dukascopy-Daten erhalten.")
    df = pd.concat(frames)
    return _finish(df[df["volume"] > 0])


# ----------------------------------------------------------------------------
# USD/CHF – EZB-Referenzkurse
# ----------------------------------------------------------------------------

def download_usdchf(start=date(2020, 1, 1), end=None):
    end = end or datetime.now(timezone.utc).date()
    raw = _get(f"https://api.frankfurter.app/{start.isoformat()}..{end.isoformat()}?from=USD&to=CHF")
    rates = json.loads(raw)["rates"]
    s = pd.Series({pd.Timestamp(k, tz="UTC"): v["CHF"] for k, v in rates.items()}).sort_index()
    s.name = "usdchf"
    return s


# ----------------------------------------------------------------------------
# CSV-Import / Cache
# ----------------------------------------------------------------------------

def load_ohlc_csv(path):
    """CSV mit Zeitspalte (Unix-Sekunden/-Millisekunden oder ISO-Datum) und OHLC.
    Funktioniert mit TradingView-Exporten und den Cache-Dateien dieses Moduls."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next(c for c in df.columns if c in ("time", "timestamp", "date", "datetime", "open_time"))
    t = df[tcol]
    if np.issubdtype(t.dtype, np.number):
        unit = "ms" if t.iloc[0] > 10**11 else "s"
        idx = pd.to_datetime(t, unit=unit, utc=True)
    else:
        idx = pd.to_datetime(t, utc=True)
    out = df[["open", "high", "low", "close"]].copy()
    out.index = idx
    return _finish(out)


def load_fx_csv(path):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next(c for c in df.columns if c in ("date", "time", "timestamp"))
    vcol = next(c for c in df.columns if c in ("usdchf", "close", "rate", "chf"))
    s = pd.Series(df[vcol].astype(float).values, index=pd.to_datetime(df[tcol], utc=True)).sort_index()
    s.name = "usdchf"
    return s


def save_csv(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out.index = out.index.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.index.name = "time" if isinstance(df, pd.DataFrame) else "date"
    out.to_csv(path)
