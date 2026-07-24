#!/usr/bin/env bash
# Phase 1 - data download. Run on a machine/network where Binance is reachable
# (the managed web sandbox blocks all crypto exchange endpoints by policy).
#
# Downloads 5m + 15m + 1h OHLCV for the NFI static Binance-spot-USDT pairlist
# (160 pairs) from 2023-01-01 to now, in feather format, into
# user_data/data/binance/. Then package it and hand the tarball to the analyst.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
USER_DATA="${ROOT}/user_data"
mkdir -p "${USER_DATA}/data" "${USER_DATA}/logs"

# Docker bind-mounts ONLY ./user_data -> /freqtrade/user_data, so anything the
# container must read (config + pairlist) has to live under user_data/.
cp "${ROOT}/config/config.json"                              "${USER_DATA}/config.json"
cp "${ROOT}/config/pairlist-static-binance-spot-usdt.json"  "${USER_DATA}/pairlist.json"

# ============================================================================
# Pick ONE runner.
# ============================================================================

# (a) Docker - recommended, no local TA-Lib/build headaches. Uses CONTAINER paths.
docker compose -f "${ROOT}/docker-compose.yml" run --rm freqtrade download-data \
  --config /freqtrade/user_data/config.json \
  --config /freqtrade/user_data/pairlist.json \
  --exchange binance --trading-mode spot \
  --timeframes 5m 15m 1h --timerange 20230101- \
  --data-format-ohlcv feather \
  --datadir /freqtrade/user_data/data

# (b) OR local pip freqtrade (needs the TA-Lib C lib; a clean venv avoids the
#     Debian setuptools bug). Comment out block (a) and use HOST paths:
# python3 -m venv .venv && . .venv/bin/activate
# pip install --upgrade pip setuptools wheel && pip install "freqtrade==2026.6"
# freqtrade download-data \
#   --config "${ROOT}/config/config.json" \
#   --config "${ROOT}/config/pairlist-static-binance-spot-usdt.json" \
#   --exchange binance --trading-mode spot \
#   --timeframes 5m 15m 1h --timerange 20230101- \
#   --data-format-ohlcv feather --datadir "${USER_DATA}/data"

# ============================================================================
echo "=============================================================="
echo "Data size:"; du -sh "${USER_DATA}/data/binance" 2>/dev/null || true
echo "5m pair files:"; ls "${USER_DATA}/data/binance"/*-5m.feather 2>/dev/null | wc -l
echo "--------------------------------------------------------------"
echo "Now package and share the tarball with the analyst:"
echo "  ( cd ${USER_DATA}/data && tar czf ~/nfi_data.tgz binance/ )"
echo "  then upload ~/nfi_data.tgz to a PUBLIC bucket / GitHub release and send the URL."
