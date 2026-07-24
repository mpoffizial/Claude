# NFI Validation Pipeline

Reproducible test of whether **NostalgiaForInfinity** has a real edge or is curve
fitting. Method and results: [`report.md`](report.md). This README is how to run it.

The design principle: **never present an in-sample number as an out-of-sample one,
and never fabricate a number that needs data we don't have.** Where data is missing
the scripts stop and say so.

## What runs without market data (already done here)
- `scripts/01_analyze_git_history.py` — the structural curve-fitting evidence from
  NFI's own git history. Outputs in `outputs/`: `git_history.json`,
  `git_history_summary.md`, `complexity_growth.png`. **This is the headline finding.**
- `scripts/lib/test_metrics.py` (6/6) and `scripts/test_robustness.py` (4/4) — prove
  the metric and Monte-Carlo math without any market data.

## What needs market data (scripts written, pinned, tested; run when unblocked)
Phases 2, 4, 5, 6 and the *execution* of Phase 3 need 5m/15m/1h Binance klines for
the 160-pair NFI list, 2023→now (~2.5–3.5 GB).

### Unblock — get the data
The managed web sandbox blocks every crypto data host by policy (see `report.md` §0),
and has no Docker daemon. Two clean options:

1. **Provide data via an allowed host (works from this sandbox).** On any machine with
   Binance access, run `scripts/02_download_data.sh`, tar `user_data/data/binance/`,
   upload to a **public GCS or S3 bucket** or a GitHub release (all reachable from the
   sandbox), and hand over the URL. Pull it here and run the analysis scripts.
2. **Change the environment's egress policy** to allow `data.binance.vision`
   (+ optionally `huggingface.co`) and run natively — likely needs a fresh session.
   Docs: https://code.claude.com/docs/en/claude-code-on-the-web

## Full run order (on a machine with data + Docker, or after unblocking)
```bash
# 0. deps
pip install -r requirements.txt            # analysis env
git clone https://github.com/iterativv/NostalgiaForInfinity vendor/NostalgiaForInfinity
cp vendor/NostalgiaForInfinity/NostalgiaForInfinity*.py user_data/strategies/   # for baseline

# 1. data (Phase 1)
bash scripts/02_download_data.sh
python3 scripts/02b_report_gaps.py --data-dir user_data/data/binance --timeframe 5m

# 2. baseline — CONTAMINATED (Phase 2)
bash scripts/03_baseline_backtest.sh NostalgiaForInfinityX7
python3 scripts/summarize_trades.py --results user_data/backtest_results --contaminated

# 3. time-frozen walk-forward — the real test (Phase 3)
cd scripts && python3 04_walk_forward.py \
    --nfi-repo ../vendor/NostalgiaForInfinity \
    --data-dir ../user_data/data/binance \
    --base-config ../config/config.json \
    --universe ../config/pairlist-static-binance-spot-usdt.json \
    --out ../outputs --fee 0.00075 --start 2023-01-01

# 4-6. on the CLEAN aggregated trades
python3 05_costs.py      --trades ../outputs/clean_trades_aggregated.parquet
python3 06_robustness.py --trades ../outputs/clean_trades_aggregated.parquet \
                         --btc ../user_data/data/binance/BTC_USDT-1d.feather
python3 07_benchmark.py  --data-dir ../user_data/data/binance \
                         --walk-forward ../outputs/walk_forward.json
```

## Rules honored
No hyperopt, no parameter tuning — the strategy is tested as-is, frozen by git SHA.
Every reported number carries its assumptions. Negative results are the expected
outcome and are reported without softening.

## Layout
```
config/       config.json (mandatory NFI flags) + 160-pair static list
scripts/      01..07 pipeline + lib/ (metrics, pit_pairlist) + tests
outputs/      git-history results + figure (done); backtest outputs land here
docker-compose.yml, requirements.txt   pinned toolchain
```
