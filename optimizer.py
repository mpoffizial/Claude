"""
MGC1 Strategy Optimizer
Fixes identified issues and optimizes parameters through systematic grid search.

Key fixes from initial backtest:
1. Trailing stop was too aggressive → made optional / wider
2. Short trades losing heavily on bullish gold → add long-bias option
3. SuperTrend+MACD had no trades → relax entry conditions
4. Combined strategy too many trades → tighten confluence
"""

import pandas as pd
import numpy as np
import itertools
from backtest_engine import (
    BacktestEngine, BacktestConfig,
    ema, sma, hull_ma, atr, rsi, macd, supertrend,
    ichimoku, bollinger_bands, keltner_channels, vwap_session
)
from tv_connector import TradingViewData

import warnings
warnings.filterwarnings("ignore")


# ============================================================================
# FIXED BACKTEST ENGINE (improved trailing stop)
# ============================================================================

class OptimizedBacktestEngine(BacktestEngine):
    """Improved backtest engine with better trailing stop logic."""

    def run_strategy(self, signals: pd.DataFrame, use_trailing=True, trail_factor=0.5) -> dict:
        capital = self.config.initial_capital
        self.trades = []
        self.equity_curve = [capital]

        position = 0
        entry_price = 0.0
        entry_time = None
        entry_bar = 0
        stop_loss = 0.0
        take_profit = 0.0
        daily_pnl = 0.0
        daily_trades = 0
        current_day = None
        highest_since_entry = 0.0
        lowest_since_entry = float('inf')
        bars_since_loss = 999

        for i in range(1, len(self.df)):
            row = self.df.iloc[i]
            sig = signals.iloc[i]

            day = row.name.date() if hasattr(row.name, 'date') else None
            if day != current_day:
                daily_pnl = 0.0
                daily_trades = 0
                current_day = day

            if position != 0:
                exit_price = None
                exit_reason = ""

                if position == 1:
                    highest_since_entry = max(highest_since_entry, row["high"])

                    # Trailing stop: only activate after price moved 1x risk in our favor
                    initial_risk = entry_price - stop_loss
                    if use_trailing and highest_since_entry > entry_price + initial_risk:
                        trail_sl = highest_since_entry - initial_risk * trail_factor
                        effective_sl = max(stop_loss, trail_sl)
                    else:
                        effective_sl = stop_loss

                    if row["low"] <= effective_sl:
                        exit_price = max(effective_sl, row["open"]) - self.config.slippage_ticks * self.config.tick_size
                        exit_reason = "trailing_stop" if effective_sl > stop_loss else "stop_loss"
                    elif row["high"] >= take_profit:
                        exit_price = take_profit
                        exit_reason = "take_profit"
                    elif sig.get("long_exit", False):
                        exit_price = row["close"]
                        exit_reason = "signal_exit"

                elif position == -1:
                    lowest_since_entry = min(lowest_since_entry, row["low"])

                    initial_risk = stop_loss - entry_price
                    if use_trailing and lowest_since_entry < entry_price - initial_risk:
                        trail_sl = lowest_since_entry + initial_risk * trail_factor
                        effective_sl = min(stop_loss, trail_sl)
                    else:
                        effective_sl = stop_loss

                    if row["high"] >= effective_sl:
                        exit_price = min(effective_sl, row["open"]) + self.config.slippage_ticks * self.config.tick_size
                        exit_reason = "trailing_stop" if effective_sl < stop_loss else "stop_loss"
                    elif row["low"] <= take_profit:
                        exit_price = take_profit
                        exit_reason = "take_profit"
                    elif sig.get("short_exit", False):
                        exit_price = row["close"]
                        exit_reason = "signal_exit"

                if exit_price is not None:
                    if position == 1:
                        pnl_points = exit_price - entry_price
                    else:
                        pnl_points = entry_price - exit_price

                    pnl = pnl_points * self.config.contract_value - 2 * self.config.commission
                    pnl_pct = pnl / capital * 100

                    from backtest_engine import Trade
                    trade = Trade(
                        entry_time=entry_time, exit_time=row.name,
                        direction="long" if position == 1 else "short",
                        entry_price=entry_price, exit_price=exit_price,
                        pnl=pnl, pnl_pct=pnl_pct,
                        bars_held=i - entry_bar, exit_reason=exit_reason
                    )
                    self.trades.append(trade)
                    capital += pnl
                    daily_pnl += pnl
                    bars_since_loss = 0 if pnl < 0 else 999
                    position = 0

            if position == 0:
                bars_since_loss += 1
                can_trade = (daily_pnl > -self.config.max_daily_loss and
                           daily_trades < self.config.max_trades_per_day and
                           bars_since_loss >= 3)

                if can_trade:
                    if sig.get("long_entry", False):
                        entry_price = row["close"] + self.config.slippage_ticks * self.config.tick_size
                        stop_loss = sig.get("stop_loss", entry_price - 5)
                        take_profit = sig.get("take_profit", entry_price + 10)
                        highest_since_entry = row["high"]
                        lowest_since_entry = row["low"]
                        position = 1
                        entry_time = row.name
                        entry_bar = i
                        daily_trades += 1

                    elif sig.get("short_entry", False):
                        entry_price = row["close"] - self.config.slippage_ticks * self.config.tick_size
                        stop_loss = sig.get("stop_loss", entry_price + 5)
                        take_profit = sig.get("take_profit", entry_price - 10)
                        highest_since_entry = row["high"]
                        lowest_since_entry = row["low"]
                        position = -1
                        entry_time = row.name
                        entry_bar = i
                        daily_trades += 1

            self.equity_curve.append(capital)

        return self._calculate_metrics()


# ============================================================================
# OPTIMIZED STRATEGIES
# ============================================================================

def optimized_supertrend_strategy(df, st_period=10, st_mult=3.0, rsi_len=14,
                                   sl_mult=2.0, tp_mult=4.0, long_only=False):
    """Optimized SuperTrend strategy with parameter flexibility."""
    signals = pd.DataFrame(index=df.index)

    st, st_dir = supertrend(df, st_period, st_mult)
    rsi_val = rsi(df["close"], rsi_len)
    macd_line, signal_line, hist = macd(df["close"])
    atr_val = atr(df)
    trend_ema = ema(df["close"], 50)

    # Relaxed conditions: SuperTrend flip + trend confirmation
    st_flip_bull = (st_dir == 1) & (st_dir.shift(1) != 1)
    st_flip_bear = (st_dir == -1) & (st_dir.shift(1) != -1)

    signals["long_entry"] = (st_flip_bull &
                             (df["close"] > trend_ema) &
                             (rsi_val > 35) & (rsi_val < 75))

    if long_only:
        signals["short_entry"] = False
    else:
        signals["short_entry"] = (st_flip_bear &
                                  (df["close"] < trend_ema) &
                                  (rsi_val > 25) & (rsi_val < 65))

    signals["long_exit"] = st_flip_bear
    signals["short_exit"] = st_flip_bull

    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def optimized_confluence_strategy(df, min_score=5, sl_mult=2.0, tp_mult=4.0,
                                   long_only=False, use_volume=True):
    """Optimized confluence strategy with tunable score threshold."""
    signals = pd.DataFrame(index=df.index)

    st, st_dir = supertrend(df, 10, 3.0)
    hma = hull_ma(df["close"], 20)
    fast_ema = ema(df["close"], 9)
    slow_ema = ema(df["close"], 21)
    trend_ema = ema(df["close"], 50)
    rsi_val = rsi(df["close"], 14)
    macd_line, signal_line, hist = macd(df["close"])
    atr_val = atr(df)

    # Scoring system
    bull_score = pd.Series(0, index=df.index, dtype=float)
    bear_score = pd.Series(0, index=df.index, dtype=float)

    # SuperTrend direction (weight: 2)
    bull_score += (st_dir == 1).astype(int) * 2
    bear_score += (st_dir == -1).astype(int) * 2

    # Price vs trend EMA (weight: 1)
    bull_score += (df["close"] > trend_ema).astype(int)
    bear_score += (df["close"] < trend_ema).astype(int)

    # Fast > Slow EMA (weight: 1)
    bull_score += (fast_ema > slow_ema).astype(int)
    bear_score += (fast_ema < slow_ema).astype(int)

    # HMA direction (weight: 1)
    bull_score += (hma > hma.shift(1)).astype(int)
    bear_score += (hma < hma.shift(1)).astype(int)

    # MACD histogram (weight: 1)
    bull_score += (hist > 0).astype(int)
    bear_score += (hist < 0).astype(int)

    # RSI trend (weight: 1)
    bull_score += (rsi_val > 50).astype(int)
    bear_score += (rsi_val < 50).astype(int)

    # Volume surge (weight: 1)
    if use_volume and "volume" in df.columns:
        vol_ma = sma(df["volume"], 20)
        vol_ok = df["volume"] > vol_ma
        bull_score += vol_ok.astype(int)
        bear_score += vol_ok.astype(int)

    # Max score = 8

    rsi_ok = (rsi_val > 25) & (rsi_val < 75)
    prev_bull = bull_score.shift(1)
    prev_bear = bear_score.shift(1)

    signals["long_entry"] = ((bull_score >= min_score) & (prev_bull < min_score) & rsi_ok &
                             (df["close"] > df["open"]))  # Bullish candle confirmation

    if long_only:
        signals["short_entry"] = False
    else:
        signals["short_entry"] = ((bear_score >= min_score) & (prev_bear < min_score) & rsi_ok &
                                  (df["close"] < df["open"]))

    signals["long_exit"] = (bull_score < 3)
    signals["short_exit"] = (bear_score < 3)

    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


# ============================================================================
# OPTIMIZER
# ============================================================================

def grid_search_supertrend(df):
    """Grid search for optimal SuperTrend parameters."""
    print("\n" + "="*80)
    print("GRID SEARCH: SuperTrend Strategy")
    print("="*80)

    best_score = -float('inf')
    best_params = {}
    best_metrics = {}
    all_results = []

    param_grid = {
        "st_period": [7, 10, 14],
        "st_mult": [2.0, 2.5, 3.0],
        "sl_mult": [1.5, 2.0, 2.5],
        "tp_mult": [3.0, 4.0, 5.0],
        "long_only": [True, False],
    }

    combos = list(itertools.product(*param_grid.values()))
    print(f"Testing {len(combos)} parameter combinations...\n")

    for combo in combos:
        params = dict(zip(param_grid.keys(), combo))
        signals = optimized_supertrend_strategy(df, **params)
        engine = OptimizedBacktestEngine(df)
        metrics = engine.run_strategy(signals, use_trailing=True, trail_factor=0.6)

        if metrics.get("total_trades", 0) >= 10:
            # Composite score: profit factor * win_rate - drawdown_penalty
            score = (metrics["profit_factor"] * 20 +
                    metrics["win_rate"] * 0.3 +
                    metrics["sharpe_ratio"] * 5 +
                    max(metrics["max_drawdown_pct"], -50) * 0.3)

            all_results.append({**params, **metrics, "score": score})

            if score > best_score:
                best_score = score
                best_params = params
                best_metrics = metrics

    # Sort and show top 10
    all_results.sort(key=lambda x: x["score"], reverse=True)

    print(f"{'Rank':<5} {'ST_P':>5} {'ST_M':>5} {'SL':>5} {'TP':>5} {'Long?':>6} "
          f"{'Trades':>7} {'Win%':>6} {'PnL':>10} {'PF':>6} {'DD%':>7} {'Score':>7}")
    print("-" * 85)

    for i, r in enumerate(all_results[:15]):
        print(f"{i+1:<5} {r['st_period']:>5} {r['st_mult']:>5.1f} {r['sl_mult']:>5.1f} "
              f"{r['tp_mult']:>5.1f} {str(r['long_only']):>6} "
              f"{r['total_trades']:>7} {r['win_rate']:>5.1f}% "
              f"${r['total_pnl']:>9.2f} {r['profit_factor']:>6.2f} "
              f"{r['max_drawdown_pct']:>6.1f}% {r['score']:>7.1f}")

    print(f"\nBEST PARAMETERS: {best_params}")
    print(f"BEST METRICS: PF={best_metrics.get('profit_factor', 0):.2f}, "
          f"WR={best_metrics.get('win_rate', 0):.1f}%, "
          f"PnL=${best_metrics.get('total_pnl', 0):.2f}, "
          f"DD={best_metrics.get('max_drawdown_pct', 0):.1f}%")

    return best_params, best_metrics, all_results


def grid_search_confluence(df):
    """Grid search for optimal confluence parameters."""
    print("\n" + "="*80)
    print("GRID SEARCH: Confluence Strategy")
    print("="*80)

    best_score = -float('inf')
    best_params = {}
    best_metrics = {}
    all_results = []

    param_grid = {
        "min_score": [4, 5, 6, 7],
        "sl_mult": [1.5, 2.0, 2.5],
        "tp_mult": [3.0, 4.0, 5.0, 6.0],
        "long_only": [True, False],
    }

    combos = list(itertools.product(*param_grid.values()))
    print(f"Testing {len(combos)} parameter combinations...\n")

    for combo in combos:
        params = dict(zip(param_grid.keys(), combo))
        signals = optimized_confluence_strategy(df, **params)
        engine = OptimizedBacktestEngine(df)
        metrics = engine.run_strategy(signals, use_trailing=True, trail_factor=0.6)

        if metrics.get("total_trades", 0) >= 5:
            score = (metrics["profit_factor"] * 20 +
                    metrics["win_rate"] * 0.3 +
                    metrics["sharpe_ratio"] * 5 +
                    max(metrics["max_drawdown_pct"], -50) * 0.3)

            all_results.append({**params, **metrics, "score": score})

            if score > best_score:
                best_score = score
                best_params = params
                best_metrics = metrics

    all_results.sort(key=lambda x: x["score"], reverse=True)

    print(f"{'Rank':<5} {'MinS':>5} {'SL':>5} {'TP':>5} {'Long?':>6} "
          f"{'Trades':>7} {'Win%':>6} {'PnL':>10} {'PF':>6} {'DD%':>7} {'Score':>7}")
    print("-" * 80)

    for i, r in enumerate(all_results[:15]):
        print(f"{i+1:<5} {r['min_score']:>5} {r['sl_mult']:>5.1f} "
              f"{r['tp_mult']:>5.1f} {str(r['long_only']):>6} "
              f"{r['total_trades']:>7} {r['win_rate']:>5.1f}% "
              f"${r['total_pnl']:>9.2f} {r['profit_factor']:>6.2f} "
              f"{r['max_drawdown_pct']:>6.1f}% {r['score']:>7.1f}")

    print(f"\nBEST PARAMETERS: {best_params}")
    print(f"BEST METRICS: PF={best_metrics.get('profit_factor', 0):.2f}, "
          f"WR={best_metrics.get('win_rate', 0):.1f}%, "
          f"PnL=${best_metrics.get('total_pnl', 0):.2f}, "
          f"DD={best_metrics.get('max_drawdown_pct', 0):.1f}%")

    return best_params, best_metrics, all_results


# ============================================================================
# DETAILED TRADE LOG
# ============================================================================

def print_trade_log(engine, name, limit=30):
    """Print detailed trade log."""
    print(f"\n{'='*80}")
    print(f"TRADE LOG: {name} (last {limit} trades)")
    print(f"{'='*80}")
    print(f"{'#':<4} {'Entry Time':<20} {'Dir':<6} {'Entry':>10} {'Exit':>10} "
          f"{'PnL':>10} {'Bars':>5} {'Reason':<15}")
    print("-" * 85)

    trades = engine.trades[-limit:]
    for i, t in enumerate(trades):
        entry_str = t.entry_time.strftime("%Y-%m-%d %H:%M") if hasattr(t.entry_time, 'strftime') else str(t.entry_time)[:16]
        print(f"{i+1:<4} {entry_str:<20} {t.direction:<6} {t.entry_price:>10.2f} "
              f"{t.exit_price:>10.2f} ${t.pnl:>9.2f} {t.bars_held:>5} {t.exit_reason:<15}")

    # Summary
    winners = [t for t in engine.trades if t.pnl > 0]
    losers = [t for t in engine.trades if t.pnl <= 0]
    avg_win = f"${np.mean([t.pnl for t in winners]):.2f}" if winners else "$0.00"
    avg_loss = f"${np.mean([t.pnl for t in losers]):.2f}" if losers else "$0.00"
    print(f"\nTotal: {len(engine.trades)} trades | "
          f"Winners: {len(winners)} (avg {avg_win}) | "
          f"Losers: {len(losers)} (avg {avg_loss})")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("="*80)
    print("MGC1 STRATEGY OPTIMIZER")
    print("="*80)

    # Get data
    tv = TradingViewData()
    df = tv.fetch(symbol="GC1!", exchange="COMEX", interval="60", n_bars=5000)

    if df is None or len(df) < 100:
        print("ERROR: Not enough data!")
        exit(1)

    print(f"\nData: {len(df)} bars, {df.index[0]} to {df.index[-1]}")
    print(f"Price range: ${df['close'].min():.2f} - ${df['close'].max():.2f}\n")

    # Run grid searches
    st_best_params, st_best_metrics, st_results = grid_search_supertrend(df)
    conf_best_params, conf_best_metrics, conf_results = grid_search_confluence(df)

    # Run the best strategy with detailed output
    print("\n\n" + "="*80)
    print("RUNNING BEST STRATEGY WITH DETAILED OUTPUT")
    print("="*80)

    # Determine overall best
    st_score = st_results[0]["score"] if st_results else 0
    conf_score = conf_results[0]["score"] if conf_results else 0

    if st_score >= conf_score and st_results:
        print(f"\nBest: SuperTrend Strategy (score: {st_score:.1f})")
        best_signals = optimized_supertrend_strategy(df, **st_best_params)
        best_name = "SuperTrend (Optimized)"
        best_params_final = st_best_params
    else:
        print(f"\nBest: Confluence Strategy (score: {conf_score:.1f})")
        best_signals = optimized_confluence_strategy(df, **conf_best_params)
        best_name = "Confluence (Optimized)"
        best_params_final = conf_best_params

    engine = OptimizedBacktestEngine(df)
    final_metrics = engine.run_strategy(best_signals, use_trailing=True, trail_factor=0.6)

    print(f"\n{'='*60}")
    print(f"FINAL OPTIMIZED RESULTS: {best_name}")
    print(f"{'='*60}")
    print(f"  Parameters:      {best_params_final}")
    print(f"  Total Trades:    {final_metrics['total_trades']}")
    print(f"  Win Rate:        {final_metrics['win_rate']:.1f}%")
    print(f"  Total PnL:       ${final_metrics['total_pnl']:.2f}")
    print(f"  Total Return:    {final_metrics['total_return_pct']:.1f}%")
    print(f"  Profit Factor:   {final_metrics['profit_factor']:.2f}")
    print(f"  Max Drawdown:    {final_metrics['max_drawdown_pct']:.1f}%")
    print(f"  Sharpe Ratio:    {final_metrics['sharpe_ratio']:.2f}")
    print(f"  Avg Winner:      ${final_metrics['avg_winner']:.2f}")
    print(f"  Avg Loser:       ${final_metrics['avg_loser']:.2f}")
    print(f"  Largest Winner:  ${final_metrics['largest_winner']:.2f}")
    print(f"  Largest Loser:   ${final_metrics['largest_loser']:.2f}")
    print(f"  Long PnL:        ${final_metrics['long_pnl']:.2f} ({final_metrics['long_trades']} trades)")
    print(f"  Short PnL:       ${final_metrics['short_pnl']:.2f} ({final_metrics['short_trades']} trades)")
    print(f"  Consec. Wins:    {final_metrics['max_consec_wins']}")
    print(f"  Consec. Losses:  {final_metrics['max_consec_losses']}")
    print(f"  Exit Reasons:    {final_metrics['exit_reasons']}")
    print(f"  Final Equity:    ${final_metrics['final_equity']:.2f}")

    print_trade_log(engine, best_name)

    # Save results
    results_text = f"""
MGC1 Strategy Optimization Results
===================================
Date: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}
Data: {len(df)} bars ({df.index[0]} to {df.index[-1]})

Best Strategy: {best_name}
Parameters: {best_params_final}

Performance:
- Total Trades: {final_metrics['total_trades']}
- Win Rate: {final_metrics['win_rate']:.1f}%
- Total PnL: ${final_metrics['total_pnl']:.2f}
- Total Return: {final_metrics['total_return_pct']:.1f}%
- Profit Factor: {final_metrics['profit_factor']:.2f}
- Max Drawdown: {final_metrics['max_drawdown_pct']:.1f}%
- Sharpe Ratio: {final_metrics['sharpe_ratio']:.2f}
- Final Equity: ${final_metrics['final_equity']:.2f}
"""
    with open("optimization_results.txt", "w") as f:
        f.write(results_text)

    print(f"\nResults saved to optimization_results.txt")
