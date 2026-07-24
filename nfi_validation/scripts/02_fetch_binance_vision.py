#!/usr/bin/env python3
"""
Docker-free, dependency-FREE data download for the NFI validation.

Runs on ANY machine where Binance is reachable, with nothing but python3
(standard library only - no pip install, no freqtrade, no TA-Lib, no Docker).
It pulls the official Binance public data ZIPs from data.binance.vision for the
160-pair NFI static list, timeframes 5m/15m/1h, from 2023-01 to now. The analyst
converts these to freqtrade format on their side (02c_bvision_to_feather.py).

Usage
-----
    git clone -b claude/nfi-strategy-validation-4v007i https://github.com/mpoffizial/Claude
    cd Claude/nfi_validation
    python3 scripts/02_fetch_binance_vision.py           # ~1-1.5 GB, takes a while
    ( tar czf ~/nfi_data.tgz binance_vision/ )           # then upload + send URL

404s are normal and skipped: a pair simply wasn't listed yet in that month.
Re-running is safe - already-downloaded files are skipped (resumable).
"""
from __future__ import annotations

import concurrent.futures
import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

BASE = "https://data.binance.vision/data/spot"
TIMEFRAMES = ["5m", "15m", "1h"]
START = date(2023, 1, 1)
WORKERS = 12


def months(start: date, end: date) -> list[str]:
    y, m, out = start.year, start.month, []
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def download(url: str, dest: Path) -> tuple[str, str]:
    if dest.exists() and dest.stat().st_size > 0:
        return ("skip", url)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "nfi-fetch/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return ("ok", url)
    except urllib.error.HTTPError as e:
        return ("404", url) if e.code == 404 else ("err", f"{url}  [{e.code}]")
    except Exception as e:  # noqa: BLE001
        return ("err", f"{url}  [{e}]")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    pl = json.loads((root / "config" / "pairlist-static-binance-spot-usdt.json").read_text())
    pairs = [p.replace("/", "") for p in pl["exchange"]["pair_whitelist"]]
    out = root / "binance_vision"

    today = date.today()
    last_complete_month_end = today.replace(day=1) - timedelta(days=1)
    complete_months = months(START, last_complete_month_end)
    current_month_days = []
    d = today.replace(day=1)
    while d < today:
        current_month_days.append(d.isoformat())
        d += timedelta(days=1)

    tasks: list[tuple[str, Path]] = []
    for sym in pairs:
        for tf in TIMEFRAMES:
            for mm in complete_months:
                tasks.append((f"{BASE}/monthly/klines/{sym}/{tf}/{sym}-{tf}-{mm}.zip",
                              out / "monthly" / sym / tf / f"{sym}-{tf}-{mm}.zip"))
            for dd in current_month_days:
                tasks.append((f"{BASE}/daily/klines/{sym}/{tf}/{sym}-{tf}-{dd}.zip",
                              out / "daily" / sym / tf / f"{sym}-{tf}-{dd}.zip"))

    print(f"{len(pairs)} pairs x {len(TIMEFRAMES)} timeframes -> {len(tasks)} files to try")
    print(f"target dir: {out}\n")
    ok = skip = missing = err = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(download, u, d) for u, d in tasks]
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            st, info = fut.result()
            ok += st == "ok"; skip += st == "skip"; missing += st == "404"; err += st == "err"
            if st == "err":
                print("  ERR", info)
            if i % 500 == 0:
                print(f"  {i}/{len(tasks)}  downloaded={ok} skip={skip} "
                      f"not-listed(404)={missing} err={err}")

    print(f"\nDONE: downloaded={ok} skipped={skip} not-listed(404)={missing} errors={err}")
    if err:
        print("Some errors occurred - safe to re-run (already-downloaded files are skipped).")
    print("\nNext:")
    print(f"  ( cd {root} && tar czf ~/nfi_data.tgz binance_vision/ )")
    print("  then upload ~/nfi_data.tgz to a public bucket / GitHub release and send the URL.")
    return 1 if err and ok == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
