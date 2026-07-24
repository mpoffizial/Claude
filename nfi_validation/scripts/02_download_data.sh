#!/usr/bin/env bash
# Phase 1 - data download. Run on a machine/network where Binance is reachable
# (the managed web sandbox blocks all crypto exchange endpoints by policy).
#
# Downloads 5m + 15m + 1h OHLCV for the NFI static Binance-spot-USDT pairlist
# from 2023-01-01 to now, in feather format, into user_data/data/binance/.
#
# Then reports data size (GB), pair count, and missing-candle gaps per pair.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
USER_DATA="${ROOT}/user_data"
mkdir -p "${USER_DATA}/data/binance" "${USER_DATA}/strategies" "${USER_DATA}/logs"

# --- pick ONE runner ---------------------------------------------------------
# (a) Docker (matches the task's original request; image pinned):
FT="docker compose -f ${ROOT}/docker-compose.yml run --rm freqtrade"
# (b) or local binary (pip install freqtrade==2026.6):
# FT="freqtrade"

CONFIG="${ROOT}/config/config.json"
PAIRS="${ROOT}/config/pairlist-static-binance-spot-usdt.json"

# Merge base config + pairlist so download-data sees the whitelist.
# freqtrade reads pair_whitelist from the merged config.
$FT download-data \
  --config "${CONFIG}" \
  --config "${PAIRS}" \
  --exchange binance \
  --trading-mode spot \
  --timeframes 5m 15m 1h \
  --timerange 20230101- \
  --data-format-ohlcv feather \
  --datadir "${USER_DATA}/data"

echo "=============================================================="
echo "Data size:"; du -sh "${USER_DATA}/data/binance"
echo "Pairs (5m files):"; ls "${USER_DATA}/data/binance"/*-5m.feather 2>/dev/null | wc -l
echo "--------------------------------------------------------------"
echo "Gap / missing-candle report per pair:"
# freqtrade's own integrity check lists timeframe gaps:
$FT list-data --datadir "${USER_DATA}/data" --show-timerange || true
echo "For a per-pair missing-candle count, see 02b_report_gaps.py"
