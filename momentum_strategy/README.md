# MomentumTrend — a simple, honest trend/breakout strategy

A long-only Freqtrade strategy for Binance spot USDT. Built as the **deliberate
opposite of NostalgiaForInfinity**: instead of 75,000 lines and ~14,000 tuned
constants, it is one economic thesis and ~7 round-number parameters.

## The thesis (the only one)
Crypto trends persist. Buy strength in an established uptrend, ride it, cut it when
the trend breaks. Momentum/breakout is among the most robust, cross-asset-documented
anomalies — so it is a defensible thing to try, and simple enough to be hard to overfit.

## The rules (all in `strategy/MomentumTrend.py`)
**Enter long** when *all* hold:
- `EMA50 > EMA200` and `close > EMA200` — established uptrend / regime filter
- `ADX(14) > 20` — a trend actually exists (not chop)
- `close` breaks above the prior **20-bar high** (Donchian breakout)

**Exit** when the trend breaks (`close < EMA50`) or momentum is exhausted (`RSI > 80`).
**Risk:** hard stop −10%, trailing stop (3% trail after +6%), time-decaying ROI.

Every parameter is a round number chosen a priori (50, 200, 20, 14, 20, 80). **Nothing
is hyperopted.** Fewer knobs = less to overfit. Higher timeframe (1h) + a breakout
trigger = infrequent trades = low sensitivity to fees/slippage (NFI's structural weak spot).

## Honest status — this is UNVALIDATED
A good-looking backtest proves nothing; that is the entire lesson of the NFI analysis
next door. **No edge is claimed.** Before trusting it, it must survive the same gauntlet:
1. **Out-of-sample walk-forward** (train on a window, test on the next, never overlap).
2. **Costs**: break-even roundtrip cost > ~40 bps (else dead for retail).
3. **Benchmark**: beat Buy&Hold BTC on **Sortino**, not just nominal return.
4. **Robustness**: Monte Carlo / bootstrap / regime split / drop best-5% of trades.

The scripts in `../nfi_validation/scripts` (`05_costs.py`, `06_robustness.py`,
`07_benchmark.py`) run directly on this strategy's exported trades — reuse them.

## What has actually been verified here
- The strategy **executes and emits entries/exits** — `tools/smoke_test.py` runs the
  populate_* logic on synthetic data (10 pairs × 2y hourly). ✅ passed.
- It has **not** been run through `freqtrade backtesting` in this sandbox: that engine
  loads live exchange markets from `api.binance.com`, which the sandbox blocks. On any
  machine with Binance access it runs unchanged.

## Run it (machine with Binance access, or after data is provisioned)
```bash
# real data
freqtrade download-data --exchange binance --pairs BTC/USDT ETH/USDT ... \
    --timeframes 1h --timerange 20200101- --data-format-ohlcv feather

# backtest
freqtrade backtesting --config config.json --strategy MomentumTrend \
    --strategy-path strategy --timerange 20200101-20240101 \
    --export trades --export-filename outputs/mt

# then validate honestly (out-of-sample split, costs, robustness, benchmark)
freqtrade backtesting ... --timerange 20240101-        # holdout, never tuned on
python3 ../nfi_validation/scripts/05_costs.py --trades outputs/mt*.zip  # etc.
```

## Smoke-test locally (no market data, no Binance)
```bash
python tools/gen_synthetic_ohlcv.py --out user_data/data/binance   # synthetic only
python tools/smoke_test.py                                         # logic check
```

## Layout
```
strategy/MomentumTrend.py   the strategy (single file, ~90 lines)
config.json                 freqtrade config (no keys)
tools/gen_synthetic_ohlcv.py  synthetic OHLCV for the smoke test
tools/smoke_test.py         runs populate_* and asserts signals are produced
```
