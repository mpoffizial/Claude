"""
CLI entry point for strategy optimization and backtesting.

Usage:
    python run.py optimize --symbol BTC/USDT --timeframe 15m --days 540
    python run.py backtest  --symbol BTC/USDT --timeframe 1h  --days 180
"""

import argparse
import sys
import time
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Crypto data fetcher
# ─────────────────────────────────────────────────────────────────────────────

TIMEFRAME_BARS_PER_DAY = {
    "1m": 1440, "3m": 480, "5m": 288, "15m": 96,
    "30m": 48, "1h": 24, "2h": 12, "4h": 6, "1d": 1,
}


def _generate_btc_data(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """
    Synthetic BTC/USDT OHLCV data via geometric Brownian motion.
    Calibrated to BTC's historical characteristics:
      - ~65 % annual volatility
      - ~100 % annual drift (bull-market assumption, conservative)
      - regime switching between trending and mean-reverting phases
    """
    bars_per_day = TIMEFRAME_BARS_PER_DAY[timeframe]
    n_bars = days * bars_per_day
    minutes_per_bar = 1440 // bars_per_day

    annual_vol  = 0.65
    annual_drift = 0.60
    bars_per_year = 365 * bars_per_day
    bar_vol   = annual_vol   / np.sqrt(bars_per_year)
    bar_drift = annual_drift / bars_per_year

    np.random.seed(42)
    base_price = 95_000.0   # approximate BTC/USDT as of mid-2025

    returns = np.zeros(n_bars)
    for i in range(1, n_bars):
        # Slow regime oscillation (trending ↔ mean-reverting)
        regime_bull = np.sin(i / (bars_per_day * 30)) > 0
        momentum = 0.08 * returns[i-1] if regime_bull else -0.04 * returns[i-1]
        # Occasional volatility spikes (crypto-specific)
        spike = np.random.randn() * bar_vol * 3 if np.random.rand() < 0.002 else 0
        returns[i] = bar_drift + momentum + bar_vol * np.random.randn() + spike

    # Build close prices backward from base_price so the series ends near it
    log_prices = np.cumsum(returns[::-1])
    prices = base_price * np.exp(-log_prices[::-1])

    freq = f"{minutes_per_bar}min"
    timestamps = pd.date_range(end=pd.Timestamp.now(tz="UTC"),
                               periods=n_bars, freq=freq)

    data = []
    for ts, p in zip(timestamps, prices):
        bar_range = abs(np.random.randn()) * bar_vol * p * 1.2
        o = p * (1 + np.random.randn() * bar_vol * 0.4)
        h = max(o, p) + abs(np.random.randn()) * bar_range * 0.6
        l = min(o, p) - abs(np.random.randn()) * bar_range * 0.6
        c = p
        vol = max(1, float(np.random.lognormal(10, 1)))
        data.append([ts, o, h, l, c, vol])

    df = pd.DataFrame(data,
                      columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.set_index("timestamp")
    return df


def fetch_ohlcv(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """
    Fetch OHLCV data.  Tries ccxt/Binance first; falls back to synthetic data
    when the network is unavailable (e.g. in sandboxed environments).
    """
    bars_per_day = TIMEFRAME_BARS_PER_DAY.get(timeframe)
    if bars_per_day is None:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. "
                         f"Choose from: {list(TIMEFRAME_BARS_PER_DAY)}")

    total_bars = days * bars_per_day

    # ── Try live data ────────────────────────────────────────────────────────
    try:
        import ccxt
        exchange = ccxt.binance({"enableRateLimit": True})
        print(f"[ccxt] Fetching {symbol} {timeframe} — {days} days "
              f"≈ {total_bars:,} bars from Binance...")

        all_candles = []
        since_ms = exchange.milliseconds() - days * 24 * 3600 * 1000
        limit = 1000

        while True:
            candles = exchange.fetch_ohlcv(symbol, timeframe,
                                           since=since_ms, limit=limit)
            if not candles:
                break
            all_candles.extend(candles)
            if len(candles) < limit:
                break
            since_ms = candles[-1][0] + 1
            time.sleep(exchange.rateLimit / 1000)

        if all_candles:
            df = pd.DataFrame(all_candles,
                              columns=["timestamp", "open", "high",
                                       "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.set_index("timestamp").sort_index().drop_duplicates()
            print(f"[ccxt] Loaded {len(df):,} bars  "
                  f"{df.index[0].date()} → {df.index[-1].date()}")
            return df

    except Exception as exc:
        print(f"[ccxt] Live data unavailable: {exc.__class__.__name__} — "
              "falling back to synthetic data.")

    # ── Synthetic fallback ───────────────────────────────────────────────────
    print(f"[synthetic] Generating {total_bars:,} bars of realistic "
          f"{symbol} {timeframe} data (GBM, σ_annual≈65%)...")
    df = _generate_btc_data(symbol, timeframe, days)
    print(f"[synthetic] {len(df):,} bars  "
          f"{df.index[0].date()} → {df.index[-1].date()}  "
          f"price ${df['close'].min():,.0f}–${df['close'].max():,.0f}")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Crypto-aware backtest config
# ─────────────────────────────────────────────────────────────────────────────

def make_crypto_config(initial_capital: float = 10_000.0,
                       position_pct: float = 0.10):
    """
    Build a BacktestConfig scaled for crypto spot/perp markets.

    position_pct: fraction of capital to risk per trade (default 10 %).
    contract_value is set to 1.0 — PnL is computed in quote-currency points.
    We later scale by position_pct inside the wrapper.
    """
    from backtest_engine import BacktestConfig
    return BacktestConfig(
        initial_capital=initial_capital,
        contract_value=position_pct,   # each $1 move = position_pct dollars
        commission=initial_capital * position_pct * 0.001,  # ~0.1 % taker fee
        slippage_ticks=1,
        tick_size=0.01,
        max_trades_per_day=6,
        max_daily_loss=initial_capital * 0.05,  # 5 % daily stop
    )


# ─────────────────────────────────────────────────────────────────────────────
# Optimize command
# ─────────────────────────────────────────────────────────────────────────────

def cmd_optimize(args):
    df = fetch_ohlcv(args.symbol, args.timeframe, args.days)

    if len(df) < 200:
        print("ERROR: Not enough data for meaningful optimization (need ≥ 200 bars).")
        sys.exit(1)

    cfg = make_crypto_config(args.capital, args.position_pct)
    print(f"\n[Config] Capital=${cfg.initial_capital:,.0f}  "
          f"Position={args.position_pct*100:.0f}%  "
          f"Commission=${cfg.commission:.2f}/side\n")

    from optimizer import (
        OptimizedBacktestEngine,
        grid_search_supertrend,
        grid_search_confluence,
        optimized_supertrend_strategy,
        optimized_confluence_strategy,
        print_trade_log,
    )

    # Monkey-patch the engine to use our crypto config
    _orig_init = OptimizedBacktestEngine.__init__

    def _patched_init(self, df, config=None):
        _orig_init(self, df, config or cfg)

    OptimizedBacktestEngine.__init__ = _patched_init

    st_best, st_metrics, st_results = grid_search_supertrend(df)
    cf_best, cf_metrics, cf_results = grid_search_confluence(df)

    st_score = st_results[0]["score"] if st_results else -1e9
    cf_score = cf_results[0]["score"] if cf_results else -1e9

    print("\n" + "=" * 70)
    print("FINAL OPTIMIZED RESULTS")
    print("=" * 70)

    if st_score >= cf_score and st_results:
        winner_name = "SuperTrend (Optimized)"
        winner_params = st_best
        best_signals = optimized_supertrend_strategy(df, **st_best)
        score = st_score
    else:
        winner_name = "Confluence (Optimized)"
        winner_params = cf_best
        best_signals = optimized_confluence_strategy(df, **cf_best)
        score = cf_score

    engine = OptimizedBacktestEngine(df)
    m = engine.run_strategy(best_signals, use_trailing=True, trail_factor=0.6)

    print(f"\nBest Strategy : {winner_name}  (score {score:.1f})")
    print(f"Parameters    : {winner_params}")
    print(f"Symbol        : {args.symbol}  |  Timeframe: {args.timeframe}  "
          f"|  Days: {args.days}")
    print()
    print(f"  Total Trades   : {m['total_trades']}")
    print(f"  Win Rate       : {m['win_rate']:.1f}%")
    print(f"  Total PnL      : ${m['total_pnl']:,.2f}")
    print(f"  Total Return   : {m['total_return_pct']:.1f}%")
    print(f"  Profit Factor  : {m['profit_factor']:.2f}")
    print(f"  Max Drawdown   : {m['max_drawdown_pct']:.1f}%")
    print(f"  Sharpe Ratio   : {m['sharpe_ratio']:.2f}")
    print(f"  Avg Winner     : ${m['avg_winner']:.2f}")
    print(f"  Avg Loser      : ${m['avg_loser']:.2f}")
    print(f"  Long PnL       : ${m['long_pnl']:.2f} ({m['long_trades']} trades)")
    print(f"  Short PnL      : ${m['short_pnl']:.2f} ({m['short_trades']} trades)")
    print(f"  Exit Reasons   : {m['exit_reasons']}")
    print(f"  Final Equity   : ${m['final_equity']:,.2f}")

    print_trade_log(engine, winner_name)

    # Persist results
    out_path = "optimization_results.txt"
    with open(out_path, "w") as f:
        f.write(f"Optimization Results\n{'='*40}\n")
        f.write(f"Symbol: {args.symbol}\n")
        f.write(f"Timeframe: {args.timeframe}\n")
        f.write(f"Days: {args.days}\n")
        f.write(f"Bars: {len(df)}\n")
        f.write(f"Date range: {df.index[0].date()} to {df.index[-1].date()}\n\n")
        f.write(f"Best Strategy: {winner_name}\n")
        f.write(f"Parameters: {winner_params}\n\n")
        f.write(f"Win Rate:      {m['win_rate']:.1f}%\n")
        f.write(f"Total Return:  {m['total_return_pct']:.1f}%\n")
        f.write(f"Profit Factor: {m['profit_factor']:.2f}\n")
        f.write(f"Max Drawdown:  {m['max_drawdown_pct']:.1f}%\n")
        f.write(f"Sharpe Ratio:  {m['sharpe_ratio']:.2f}\n")
        f.write(f"Final Equity:  ${m['final_equity']:,.2f}\n")

    print(f"\nResults saved to {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Backtest command (run all strategies, compare)
# ─────────────────────────────────────────────────────────────────────────────

def cmd_backtest(args):
    df = fetch_ohlcv(args.symbol, args.timeframe, args.days)

    if len(df) < 200:
        print("ERROR: Not enough data.")
        sys.exit(1)

    from backtest_engine import run_all_strategies, print_comparison_table
    results, _ = run_all_strategies(df)
    print_comparison_table(results)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="MGC1 / Crypto strategy optimizer & backtester",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--symbol",       default="BTC/USDT",
                        help="Trading pair, e.g. BTC/USDT (default: BTC/USDT)")
    common.add_argument("--timeframe",    default="15m",
                        help="OHLCV timeframe: 1m 5m 15m 30m 1h 4h 1d (default: 15m)")
    common.add_argument("--days",         type=int, default=180,
                        help="History length in days (default: 180)")
    common.add_argument("--capital",      type=float, default=10_000.0,
                        help="Starting capital in quote currency (default: 10000)")
    common.add_argument("--position-pct", type=float, default=0.10,
                        dest="position_pct",
                        help="Fraction of capital per trade (default: 0.10)")

    sub.add_parser("optimize",  parents=[common],
                   help="Grid-search optimal parameters for SuperTrend & Confluence")
    sub.add_parser("backtest",  parents=[common],
                   help="Run all five built-in strategies and compare")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "optimize":
        cmd_optimize(args)
    elif args.command == "backtest":
        cmd_backtest(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
