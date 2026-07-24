"""Deterministic checks for 02c_bvision_to_feather.py on synthetic Binance ZIPs.

Covers the two real quirks: header vs no-header, and the ms->microsecond precision
change (a pair's history spans both, so each file must decode on its own).

Run:  python3 test_bvision.py
"""
import importlib.util
import tempfile
import zipfile
from pathlib import Path

import pandas as pd

spec = importlib.util.spec_from_file_location("conv", Path(__file__).parent / "02c_bvision_to_feather.py")
conv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(conv)


def _zip(path: Path, text: str):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(path.stem + ".csv", text)


def test_mixed_precision_and_header():
    d = Path(tempfile.mkdtemp())
    # NEW: header row + MICROSECOND open_time (1735689600000000us == 2025-01-01)
    newf = d / "BTCUSDT-5m-2025-01.zip"
    _zip(newf,
         "open_time,open,high,low,close,volume,close_time,quote_volume,count,tb,tq,ig\n"
         "1735689600000000,42000,42100,41950,42050,10.5,1,1,1,1,1,0\n"
         "1735689900000000,42050,42200,42000,42150,8.3,1,1,1,1,1,0\n")
    # OLD: no header + MILLISECOND open_time (1672531200000ms == 2023-01-01)
    oldf = d / "BTCUSDT-5m-2023-01.zip"
    _zip(oldf,
         "1672531200000,16500,16550,16490,16520,20,1,1,1,1,1,0\n"
         "1672531500000,16520,16560,16510,16540,15,1,1,1,1,1,0\n")

    merged = conv.to_freqtrade_frame(
        pd.concat([conv.read_zip_csv(newf), conv.read_zip_csv(oldf)], ignore_index=True))

    assert list(merged.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert str(merged["date"].dtype) == "datetime64[ns, UTC]"
    # regression: ms rows must NOT be mis-decoded as microseconds (would be 1970)
    assert merged["date"].iloc[0].year == 2023
    assert merged["date"].iloc[-1].year == 2025
    assert merged["date"].is_monotonic_increasing


def test_pair_filename():
    assert conv.pair_filename("BTCUSDT", "5m") == "BTC_USDT-5m.feather"
    assert conv.pair_filename("1INCHUSDT", "15m") == "1INCH_USDT-15m.feather"


if __name__ == "__main__":
    for k, fn in sorted(globals().items()):
        if k.startswith("test_"):
            fn()
            print(f"  ok  {k}")
    print("\nconverter tests passed.")
