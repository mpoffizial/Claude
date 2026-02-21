"""
Maker Backtest Runner
=====================
Fetches real Binance BTC 1-minute klines, runs the maker backtest,
and prints a comprehensive report.

Usage
-----
    # 14 days, default config, simulation only
    python -m polymarket_btc_bot.maker.run_backtest

    # 30 days, wider spread
    python -m polymarket_btc_bot.maker.run_backtest --days 30 --spread 0.015

    # Parameter sweep: test multiple spread widths
    python -m polymarket_btc_bot.maker.run_backtest --days 14 --sweep-spread

    # Use cached data (skip Binance fetch if DB already populated)
    python -m polymarket_btc_bot.maker.run_backtest --use-cache

Output
------
  - Full P&L breakdown per market and in aggregate
  - Sharpe ratio, max drawdown, win rate
  - Income attribution: spread / rebate / arb / adverse selection / settlement
  - Sensitivity tables if --sweep-* flags are set
"""

import argparse
import asyncio
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

from polymarket_btc_bot.backtesting.historical_loader import (
    BacktestConfig, HistoricalKline, HistoricalLoader,
)
from polymarket_btc_bot.config import PolymarketConfig
from polymarket_btc_bot.maker.backtest import MakerBacktestConfig, MakerBacktester, MakerBacktestResult
from polymarket_btc_bot.maker.config import MakerBotConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Synthetic data generator (GBM) – used when Binance is not reachable
# ---------------------------------------------------------------------------

def generate_synthetic_klines(
    days: int,
    btc_start: float = 95_000.0,
    annual_vol: float = 0.65,
    annual_drift: float = 0.30,
    seed: int = 42,
) -> list[HistoricalKline]:
    """
    Generate realistic BTC 1-minute klines using Geometric Brownian Motion.

    Parameters are calibrated to historical BTC behaviour:
      annual_vol   = 65%  (realistic range 50–80%)
      annual_drift = 30%  (long-term upward trend)

    The GBM formula for each 1-minute step:
      S_{t+1} = S_t × exp((μ - σ²/2)·Δt + σ·√Δt·Z)
      where Z ~ N(0,1) and Δt = 1/(365×1440) years

    The OHLC for each candle is derived from the opening and closing prices
    plus two additional intra-minute Brownian steps for high/low.
    """
    rng = np.random.default_rng(seed)
    total_minutes = days * 24 * 60

    dt = 1.0 / (365.0 * 1440.0)          # 1 minute in years
    mu = annual_drift
    sigma = annual_vol
    drift = (mu - 0.5 * sigma ** 2) * dt
    diffusion = sigma * math.sqrt(dt)

    # Simulate close prices using GBM
    log_returns = drift + diffusion * rng.standard_normal(total_minutes)
    closes = btc_start * np.exp(np.cumsum(log_returns))
    opens  = np.concatenate([[btc_start], closes[:-1]])

    # Intra-minute high / low (two extra GBM steps)
    hi_noise = abs(diffusion * rng.standard_normal(total_minutes))
    lo_noise = abs(diffusion * rng.standard_normal(total_minutes))
    highs = np.maximum(opens, closes) * np.exp(hi_noise)
    lows  = np.minimum(opens, closes) * np.exp(-lo_noise)

    # Volume: lognormal around $500M/day → ~$350k per 1m bar
    volumes = rng.lognormal(mean=math.log(350_000), sigma=0.6, size=total_minutes)

    # Build klines starting from 14 days ago
    start_ts_ms = int((time.time() - days * 86400) * 1000)
    klines = [
        HistoricalKline(
            timestamp=start_ts_ms + i * 60_000,
            open=float(opens[i]),
            high=float(highs[i]),
            low=float(lows[i]),
            close=float(closes[i]),
            volume=float(volumes[i]),
        )
        for i in range(total_minutes)
    ]
    logger.info(
        "Generated %d synthetic 1m klines (%.1f days, BTC: $%.0f → $%.0f)",
        len(klines), days, btc_start, closes[-1],
    )
    return klines


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

async def load_klines(
    days: int,
    data_dir: str,
    use_cache: bool,
    synthetic: bool = False,
    btc_vol: float = 0.65,
    seed: int = 42,
) -> list[HistoricalKline]:
    """
    Load 1-minute BTCUSDT klines for the last `days` days.

    Uses the existing HistoricalLoader which fetches from Binance and
    caches results in a local SQLite database.

    Args:
        days:       Number of calendar days to look back.
        data_dir:   Directory for the SQLite cache file.
        use_cache:  If True, try the cache first; skip Binance if enough data exists.

    Returns:
        Chronological list of HistoricalKline objects.
    """
    if synthetic:
        return generate_synthetic_klines(days, annual_vol=btc_vol, seed=seed)

    from polymarket_btc_bot.config import PolymarketConfig
    bt_cfg = BacktestConfig(data_dir=data_dir)
    poly_cfg = PolymarketConfig()
    loader = HistoricalLoader(bt_cfg, poly_cfg)

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(days * 24 * 3600 * 1000)

    # Try cache first
    if use_cache:
        cached = loader.load_klines(start_ms, now_ms)
        if len(cached) >= days * 24 * 60 * 0.90:  # 90% coverage threshold
            logger.info("Using cached data: %d klines (%.1f days)", len(cached), len(cached) / 1440)
            return cached
        logger.info("Cache has %d klines, fetching fresh data from Binance...", len(cached))

    await loader.start()
    try:
        logger.info("Fetching %d days of Binance 1m BTCUSDT klines...", days)
        try:
            klines = await loader.fetch_binance_klines(start_ms, now_ms, interval="1m")
        except Exception as exc:
            logger.warning("Binance fetch failed (%s). Falling back to synthetic data.", exc)
            return generate_synthetic_klines(days, annual_vol=btc_vol, seed=seed)
        if klines:
            loader.save_klines(klines)
            logger.info("Saved %d klines to cache", len(klines))
            return klines
        logger.warning("Binance returned 0 klines. Falling back to synthetic data.")
        return generate_synthetic_klines(days, annual_vol=btc_vol, seed=seed)
    finally:
        await loader.stop()


# ---------------------------------------------------------------------------
# Pretty printing
# ---------------------------------------------------------------------------

def print_monthly_breakdown(result: MakerBacktestResult) -> None:
    """Print per-calendar-month P&L summary."""
    if not result.windows:
        return

    monthly: dict[str, list[float]] = {}
    for w in result.windows:
        dt = datetime.fromtimestamp(w.window_start_ts / 1000, tz=timezone.utc)
        key = dt.strftime("%Y-%m")
        monthly.setdefault(key, []).append(w.net_pnl)

    print("\n  Monthly P&L breakdown:")
    print(f"  {'Month':<10} {'Windows':>8} {'Net PnL':>10} {'Per Day':>10} {'WinRate':>8}")
    print(f"  {'─'*10} {'─'*8} {'─'*10} {'─'*10} {'─'*8}")
    for month, pnls in sorted(monthly.items()):
        total = sum(pnls)
        n = len(pnls)
        win_rate = sum(1 for p in pnls if p > 0) / n
        # Approximate days = windows / 96
        per_day = total / (n / 96.0) if n > 0 else 0.0
        print(f"  {month:<10} {n:>8} ${total:>9.4f} ${per_day:>9.2f} {win_rate:>7.1%}")


def print_btc_volatility_breakdown(result: MakerBacktestResult) -> None:
    """Show P&L stratified by the BTC return magnitude in the window."""
    if not result.windows:
        return

    buckets = {
        "flat   (<0.1%)": [],
        "small (0.1-0.3%)": [],
        "medium(0.3-0.7%)": [],
        "large  (>0.7%)": [],
    }

    for w in result.windows:
        r = abs(w.btc_return_pct)
        if r < 0.10:
            buckets["flat   (<0.1%)"].append(w.net_pnl)
        elif r < 0.30:
            buckets["small (0.1-0.3%)"].append(w.net_pnl)
        elif r < 0.70:
            buckets["medium(0.3-0.7%)"].append(w.net_pnl)
        else:
            buckets["large  (>0.7%)"].append(w.net_pnl)

    print("\n  P&L by BTC move magnitude (adverse selection risk):")
    print(f"  {'Bucket':<22} {'Count':>6} {'Avg PnL':>10} {'Win%':>7}")
    print(f"  {'─'*22} {'─'*6} {'─'*10} {'─'*7}")
    for label, pnls in buckets.items():
        if not pnls:
            continue
        avg = sum(pnls) / len(pnls)
        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        print(f"  {label:<22} {len(pnls):>6} ${avg:>9.4f} {wr:>6.1%}")


def print_fill_distribution(result: MakerBacktestResult) -> None:
    """Histogram of fills per window."""
    fills_per_window = [w.total_fills for w in result.windows]
    if not fills_per_window:
        return
    arr = np.array(fills_per_window)
    print(f"\n  Fill distribution per market window:")
    print(f"    Min={int(arr.min())}  p25={int(np.percentile(arr, 25))}  "
          f"Median={int(np.median(arr))}  p75={int(np.percentile(arr, 75))}  "
          f"Max={int(arr.max())}  Mean={arr.mean():.1f}")


# ---------------------------------------------------------------------------
# Parameter sweep
# ---------------------------------------------------------------------------

def run_sweep_spread(
    klines: list[HistoricalKline],
    base_config: MakerBotConfig,
    bt_cfg: MakerBacktestConfig,
    spreads: list[float],
) -> None:
    """Test multiple half-spread values and compare results."""
    print("\n" + "=" * 70)
    print("  SPREAD SENSITIVITY SWEEP")
    print("=" * 70)
    print(f"  {'HalfSprd':>10} {'NetPnL':>10} {'Sharpe':>8} {'MaxDD':>8} "
          f"{'Fills/Mkt':>10} {'SpreadYld%':>12} {'AdvSel%':>10}")
    print(f"  {'─'*10} {'─'*10} {'─'*8} {'─'*8} {'─'*10} {'─'*12} {'─'*10}")

    for spread in spreads:
        cfg = MakerBotConfig()
        cfg.quote.default_half_spread = spread
        cfg.quote.quote_size_usdc = base_config.quote.quote_size_usdc
        cfg.inventory.max_net_exposure_usdc = base_config.inventory.max_net_exposure_usdc
        cfg.arb = base_config.arb

        backtester = MakerBacktester(cfg, bt_cfg)
        res = backtester.run(klines)

        print(
            f"  {spread:>10.4f} ${res.net_pnl:>9.2f} {res.sharpe_ratio:>8.2f} "
            f"${res.max_drawdown:>7.2f} {res.fills_per_window:>10.1f} "
            f"{res.spread_yield_pct:>11.4f}% {res.adverse_sel_pct:>9.4f}%"
        )


def run_sweep_quote_size(
    klines: list[HistoricalKline],
    base_config: MakerBotConfig,
    bt_cfg: MakerBacktestConfig,
    sizes: list[float],
) -> None:
    """Test multiple quote sizes."""
    print("\n" + "=" * 70)
    print("  QUOTE SIZE SENSITIVITY SWEEP")
    print("=" * 70)
    print(f"  {'QuoteSize':>10} {'NetPnL':>10} {'Sharpe':>8} {'MaxDD':>8} {'Volume':>12}")
    print(f"  {'─'*10} {'─'*10} {'─'*8} {'─'*8} {'─'*12}")

    for size in sizes:
        cfg = MakerBotConfig()
        cfg.quote.default_half_spread = base_config.quote.default_half_spread
        cfg.quote.quote_size_usdc = size
        cfg.inventory.max_net_exposure_usdc = max(size * 3, 40.0)

        backtester = MakerBacktester(cfg, bt_cfg)
        res = backtester.run(klines)

        print(
            f"  ${size:>9.0f} ${res.net_pnl:>9.2f} {res.sharpe_ratio:>8.2f} "
            f"${res.max_drawdown:>7.2f} ${res.total_volume_usdc:>11.0f}"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Polymarket Maker-Rebate Backtest",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--days", type=int, default=14, help="Days of history to backtest")
    p.add_argument("--spread", type=float, default=0.010,
                   help="Half-spread in probability units")
    p.add_argument("--quote-size", type=float, default=20.0,
                   help="USDC per quote side")
    p.add_argument("--max-exposure", type=float, default=40.0,
                   help="Max net inventory exposure (USDC)")
    p.add_argument("--btc-vol", type=float, default=0.65,
                   help="Annualised BTC volatility for BSM pricing (e.g. 0.65 = 65%%)")
    p.add_argument("--fill-prob", type=float, default=0.12,
                   help="Base fill probability per minute per side")
    p.add_argument("--use-cache", action="store_true",
                   help="Use cached Binance data if available")
    p.add_argument("--synthetic", action="store_true",
                   help="Use synthetic GBM data instead of fetching from Binance")
    p.add_argument("--data-dir", default="backtest_data",
                   help="Directory for SQLite data cache")
    p.add_argument("--sweep-spread", action="store_true",
                   help="Run spread sensitivity sweep")
    p.add_argument("--sweep-size", action="store_true",
                   help="Run quote-size sensitivity sweep")
    p.add_argument("--seed", type=int, default=42,
                   help="Random seed for reproducibility (0 = random)")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING"])
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # --- Load data ---
    klines = await load_klines(
        args.days, args.data_dir, args.use_cache,
        synthetic=args.synthetic,
        btc_vol=args.btc_vol,
        seed=args.seed if args.seed != 0 else 42,
    )

    if len(klines) < 15:
        logger.error("Insufficient kline data. Need at least 15 data points.")
        sys.exit(1)

    actual_days = len(klines) / 1440.0
    logger.info("Loaded %d klines (%.1f days)", len(klines), actual_days)

    # --- Build configs ---
    config = MakerBotConfig()
    config.quote.default_half_spread = args.spread
    config.quote.quote_size_usdc = args.quote_size
    config.inventory.max_net_exposure_usdc = args.max_exposure

    bt_cfg = MakerBacktestConfig(
        btc_annual_vol=args.btc_vol,
        base_fill_prob_per_min=args.fill_prob,
        random_seed=args.seed if args.seed != 0 else None,
    )

    # --- Main backtest run ---
    data_source = "synthetic GBM" if args.synthetic else "real Binance BTCUSDT 1m"
    print(f"\n{'=' * 60}")
    print("  POLYMARKET MAKER-REBATE BACKTEST")
    print(f"  Data   : {actual_days:.1f} days of {data_source} data")
    print(f"  Config : spread={args.spread:.4f}  size=${args.quote_size:.0f}  "
          f"max_exp=${args.max_exposure:.0f}")
    print(f"  BTC vol: {args.btc_vol:.0%}  fill_prob={args.fill_prob:.0%}/min")
    print(f"{'=' * 60}")

    backtester = MakerBacktester(config, bt_cfg)
    result = backtester.run(klines)

    # --- Detailed analysis ---
    print_monthly_breakdown(result)
    print_btc_volatility_breakdown(result)
    print_fill_distribution(result)

    # --- Parameter sweeps ---
    if args.sweep_spread:
        spreads = [0.004, 0.006, 0.008, 0.010, 0.012, 0.015, 0.020, 0.030]
        run_sweep_spread(klines, config, bt_cfg, spreads)

    if args.sweep_size:
        sizes = [5.0, 10.0, 20.0, 30.0, 50.0, 100.0]
        run_sweep_quote_size(klines, config, bt_cfg, sizes)

    print()
    print("Backtest complete.")


if __name__ == "__main__":
    asyncio.run(_main())
