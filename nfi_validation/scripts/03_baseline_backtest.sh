#!/usr/bin/env bash
# Phase 2 - NAIVE baseline backtest. CONTAMINATED / IN-SAMPLE BY CONSTRUCTION.
#
# Runs the CURRENT NFI (HEAD) over the WHOLE 2023->now period on today's static
# pairlist. Because NFI is continuously re-tuned against recent data (see the
# git-history analysis: X7 is ~9 months old, 25.9k total commits), this backtest
# is NOT out-of-sample - it is the upper bound of what the strategy will ever show,
# not an expectation. The report labels every number from here "contaminated".
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
USER_DATA="${ROOT}/user_data"

FT="docker compose -f ${ROOT}/docker-compose.yml run --rm freqtrade"   # or: FT="freqtrade"
CONFIG="${ROOT}/config/config.json"
PAIRS="${ROOT}/config/pairlist-static-binance-spot-usdt.json"
STRAT_DIR="${ROOT}/vendor/NostalgiaForInfinity"   # cloned HEAD; class = NostalgiaForInfinityX7
STRAT="${1:-NostalgiaForInfinityX7}"

$FT backtesting \
  --config "${CONFIG}" \
  --config "${PAIRS}" \
  --strategy "${STRAT}" \
  --strategy-path "${STRAT_DIR}" \
  --datadir "${USER_DATA}/data" \
  --timerange 20230101- \
  --timeframe 5m \
  --fee 0.00075 \
  --export trades \
  --export-filename "${USER_DATA}/backtest_results/baseline_contaminated"

echo "Baseline (CONTAMINATED) exported. Summarize with:"
echo "  python3 ${HERE}/summarize_trades.py --results ${USER_DATA}/backtest_results --contaminated"
