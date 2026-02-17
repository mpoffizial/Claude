"""
MGC1 Ultra-High-Frequency Scalping Strategy Optimizer
Optimized for 5-minute and 15-minute timeframes on Micro Gold Futures

Scalping Principles:
- Tight entry conditions (2-3 confirmations)
- Very tight stops (2-4 pips)
- Quick profit targets (3-8 pips)
- High win rate > 55%
- Many trades per day (15-40)
- Average holding time < 15 minutes
- Profit Factor > 1.5
"""

import pandas as pd
import numpy as np
import itertools
from backtest_engine import (
    BacktestEngine, BacktestConfig,
    ema, sma, hull_ma, atr, rsi, macd, supertrend,
    bollinger_bands, keltner_channels
)
from tv_connector import TradingViewData
import warnings
warnings.filterwarnings("ignore")


# ============================================================================
# SCALPING-OPTIMIZED BACKTEST ENGINE
# ============================================================================

class ScalpingBacktestEngine(BacktestEngine):
    """Optimized for scalping with tighter stops and faster exits."""

    def run_strategy(self, signals: pd.DataFrame, use_trailing=False, trail_factor=0.3) -> dict:
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
        bars_since_entry = 0
        max_profit_since_entry = 0.0

        for i in range(1, len(self.df)):
            row = self.df.iloc[i]
            sig = signals.iloc[i]

            day = row.name.date() if hasattr(row.name, 'date') else None
            if day != current_day:
                daily_pnl = 0.0
                daily_trades = 0
                current_day = day

            if position != 0:
                bars_since_entry += 1
                exit_price = None
                exit_reason = ""

                if position == 1:  # Long position
                    max_profit_since_entry = max(max_profit_since_entry, row["high"] - entry_price)

                    # Quick profit trail (scalping style) - tighter
                    if use_trailing and max_profit_since_entry > 0:
                        trail_sl = row["high"] - max_profit_since_entry * trail_factor
                        effective_sl = max(stop_loss, trail_sl)
                    else:
                        effective_sl = stop_loss

                    # Exit conditions
                    if row["low"] <= effective_sl:
                        exit_price = effective_sl
                        exit_reason = "stop_loss"
                    elif row["high"] >= take_profit:
                        exit_price = take_profit
                        exit_reason = "take_profit"
                    elif sig.get("long_exit", False):
                        exit_price = row["close"]
                        exit_reason = "signal_exit"
                    elif bars_since_entry > sig.get("max_bars", 50):  # Max holding time
                        exit_price = row["close"]
                        exit_reason = "max_bars_reached"

                elif position == -1:  # Short position
                    max_profit_since_entry = max(max_profit_since_entry, entry_price - row["low"])

                    if use_trailing and max_profit_since_entry > 0:
                        trail_sl = row["low"] + max_profit_since_entry * trail_factor
                        effective_sl = min(stop_loss, trail_sl)
                    else:
                        effective_sl = stop_loss

                    if row["high"] >= effective_sl:
                        exit_price = effective_sl
                        exit_reason = "stop_loss"
                    elif row["low"] <= take_profit:
                        exit_price = take_profit
                        exit_reason = "take_profit"
                    elif sig.get("short_exit", False):
                        exit_price = row["close"]
                        exit_reason = "signal_exit"
                    elif bars_since_entry > sig.get("max_bars", 50):
                        exit_price = row["close"]
                        exit_reason = "max_bars_reached"

                if exit_price is not None:
                    if position == 1:
                        pnl_points = exit_price - entry_price
                    else:
                        pnl_points = entry_price - exit_price

                    pnl = pnl_points * self.config.contract_value - 2 * self.config.commission
                    pnl_pct = (pnl / capital * 100) if capital > 0 else 0

                    from backtest_engine import Trade
                    trade = Trade(
                        entry_time=entry_time,
                        exit_time=row.name,
                        direction="long" if position == 1 else "short",
                        entry_price=entry_price,
                        exit_price=exit_price,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        bars_held=bars_since_entry,
                        exit_reason=exit_reason
                    )
                    self.trades.append(trade)
                    capital += pnl
                    daily_pnl += pnl

                    position = 0
                    bars_since_entry = 0
                    max_profit_since_entry = 0.0

            # Check entries (only if flat)
            if position == 0:
                can_trade = (daily_pnl > -self.config.max_daily_loss and
                           daily_trades < self.config.max_trades_per_day)

                if can_trade:
                    if sig.get("long_entry", False):
                        entry_price = row["close"]
                        stop_loss = sig.get("stop_loss", entry_price - 3)
                        take_profit = sig.get("take_profit", entry_price + 5)
                        position = 1
                        entry_time = row.name
                        entry_bar = i
                        bars_since_entry = 0
                        max_profit_since_entry = 0.0
                        daily_trades += 1

                    elif sig.get("short_entry", False):
                        entry_price = row["close"]
                        stop_loss = sig.get("stop_loss", entry_price + 3)
                        take_profit = sig.get("take_profit", entry_price - 5)
                        position = -1
                        entry_time = row.name
                        entry_bar = i
                        bars_since_entry = 0
                        max_profit_since_entry = 0.0
                        daily_trades += 1

            self.equity_curve.append(capital)

        # Calculate metrics
        if not self.trades:
            return {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "total_pnl": 0,
                "avg_win": 0,
                "avg_loss": 0,
                "sharpe_ratio": 0,
                "max_drawdown": 0,
                "final_equity": capital,
                "trades": []
            }

        wins = [t.pnl for t in self.trades if t.pnl > 0]
        losses = [t.pnl for t in self.trades if t.pnl < 0]

        total_wins = sum(wins) if wins else 0
        total_losses = abs(sum(losses)) if losses else 0

        profit_factor = total_wins / total_losses if total_losses > 0 else 0
        win_rate = len(wins) / len(self.trades) * 100 if self.trades else 0
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0

        # Sharpe Ratio
        returns = np.diff(self.equity_curve) / np.array(self.equity_curve[:-1])
        sharpe = (np.mean(returns) / np.std(returns) * np.sqrt(252 * 6.5)) if np.std(returns) > 0 else 0

        # Max Drawdown
        cummax = np.maximum.accumulate(self.equity_curve)
        drawdown = (np.array(self.equity_curve) - cummax) / cummax
        max_dd = np.min(drawdown) if len(drawdown) > 0 else 0

        return {
            "total_trades": len(self.trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_pnl": sum(t.pnl for t in self.trades),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "sharpe_ratio": sharpe,
            "max_drawdown": max_dd,
            "final_equity": capital,
            "trades": self.trades
        }


# ============================================================================
# SCALPING STRATEGY GENERATORS
# ============================================================================

def strategy_rsi_ema_scalp(df, rsi_period=7, rsi_buy=30, rsi_sell=70,
                           ema_fast=5, ema_slow=13, sl_pips=3, tp_pips=6):
    """
    RSI + EMA Scalping Strategy
    - RSI for overbought/oversold (fast period for scalping)
    - EMA crossover for trend confirmation
    - Tight stops and quick profits
    """
    signals = pd.DataFrame(index=df.index)

    # Calculate indicators
    rsi_vals = rsi(df["close"], rsi_period)
    ema_fast = ema(df["close"], ema_fast)
    ema_slow = ema(df["close"], ema_slow)

    # Entry signals
    long_signal = (rsi_vals < rsi_buy) & (ema_fast > ema_slow)
    short_signal = (rsi_vals > rsi_sell) & (ema_fast < ema_slow)

    signals["long_entry"] = long_signal & ~long_signal.shift(1).fillna(False)
    signals["short_entry"] = short_signal & ~short_signal.shift(1).fillna(False)

    # Exit signals (when RSI crosses back)
    signals["long_exit"] = (rsi_vals > 50) & (rsi_vals.shift(1) <= 50)
    signals["short_exit"] = (rsi_vals < 50) & (rsi_vals.shift(1) >= 50)

    # Risk/Reward
    signals["stop_loss"] = df["close"] - sl_pips * 0.10
    signals["take_profit"] = df["close"] + tp_pips * 0.10
    signals["max_bars"] = 30  # Max 30 bars = 30 min on 1min chart

    return signals


def strategy_macd_scalp(df, fast=8, slow=17, signal=5, sl_pips=3, tp_pips=7):
    """
    MACD Scalping Strategy
    - Fast MACD for momentum
    - Quick entry on signal crossover
    """
    signals = pd.DataFrame(index=df.index)

    macd_line, signal_line, histogram = macd(df["close"], fast, slow, signal)

    # Histogram crossover signals (most responsive)
    hist_positive = histogram > 0
    hist_positive_prev = hist_positive.shift(1).fillna(False)

    signals["long_entry"] = hist_positive & ~hist_positive_prev & (macd_line > signal_line)
    signals["short_entry"] = ~hist_positive & hist_positive_prev & (macd_line < signal_line)

    # Exit on histogram reversal
    signals["long_exit"] = ~hist_positive & hist_positive_prev
    signals["short_exit"] = hist_positive & ~hist_positive_prev

    signals["stop_loss"] = df["close"] - sl_pips * 0.10
    signals["take_profit"] = df["close"] + tp_pips * 0.10
    signals["max_bars"] = 25

    return signals


def strategy_bollinger_breakout_scalp(df, period=12, mult=1.5, sl_pips=3, tp_pips=6):
    """
    Bollinger Bands Breakout Scalping
    - Entry on band breakout
    - Exit on reversal
    """
    signals = pd.DataFrame(index=df.index)

    basis, upper, lower = bollinger_bands(df["close"], period, mult)

    # Breakout signals
    long_breakout = (df["close"] > upper) & (df["close"].shift(1) <= upper.shift(1))
    short_breakout = (df["close"] < lower) & (df["close"].shift(1) >= lower.shift(1))

    signals["long_entry"] = long_breakout
    signals["short_entry"] = short_breakout

    # Exit when touching opposite band
    signals["long_exit"] = df["close"] <= basis
    signals["short_exit"] = df["close"] >= basis

    signals["stop_loss"] = df["close"] - sl_pips * 0.10
    signals["take_profit"] = df["close"] + tp_pips * 0.10
    signals["max_bars"] = 20

    return signals


def strategy_confluence_scalp(df, rsi_period=8, ema_fast=5, ema_slow=15,
                              sl_pips=2, tp_pips=5):
    """
    Confluence-based Scalping (RSI + EMA + Price Action)
    - Multiple confirmations for higher win rate
    """
    signals = pd.DataFrame(index=df.index)

    rsi_vals = rsi(df["close"], rsi_period)
    ema_fast_vals = ema(df["close"], ema_fast)
    ema_slow_vals = ema(df["close"], ema_slow)

    # Three confirmations needed
    rsi_long = rsi_vals < 40
    ema_long = ema_fast_vals > ema_slow_vals
    price_long = df["close"] > ema_fast_vals

    rsi_short = rsi_vals > 60
    ema_short = ema_fast_vals < ema_slow_vals
    price_short = df["close"] < ema_fast_vals

    signals["long_entry"] = rsi_long & ema_long & price_long
    signals["short_entry"] = rsi_short & ema_short & price_short

    # Exit when any confirmation fails
    signals["long_exit"] = ~ema_long | (rsi_vals > 70)
    signals["short_exit"] = ~ema_short | (rsi_vals < 30)

    signals["stop_loss"] = df["close"] - sl_pips * 0.10
    signals["take_profit"] = df["close"] + tp_pips * 0.10
    signals["max_bars"] = 35

    return signals


def strategy_ema_crossover_scalp(df, fast=3, slow=8, sl_pips=2, tp_pips=4):
    """
    Ultra-fast EMA Crossover Scalping
    - Very short periods for rapid entries
    """
    signals = pd.DataFrame(index=df.index)

    ema_fast = ema(df["close"], fast)
    ema_slow = ema(df["close"], slow)

    cross_above = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
    cross_below = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))

    signals["long_entry"] = cross_above
    signals["short_entry"] = cross_below

    signals["long_exit"] = cross_below
    signals["short_exit"] = cross_above

    signals["stop_loss"] = df["close"] - sl_pips * 0.10
    signals["take_profit"] = df["close"] + tp_pips * 0.10
    signals["max_bars"] = 15

    return signals


def strategy_supertrend_scalp(df, period=10, mult=2.0, sl_pips=3, tp_pips=5):
    """
    SuperTrend Scalping - Best for Gold
    - Proven profitable indicator for gold
    - Tight stops with trend following
    """
    signals = pd.DataFrame(index=df.index)

    st, direction = supertrend(df, period, mult)

    # Entry on trend reversal
    trend_change_up = (direction > 0) & (direction.shift(1) <= 0)
    trend_change_down = (direction < 0) & (direction.shift(1) >= 0)

    signals["long_entry"] = trend_change_up
    signals["short_entry"] = trend_change_down

    # Exit on trend reversal
    signals["long_exit"] = direction < 0
    signals["short_exit"] = direction > 0

    signals["stop_loss"] = df["close"] - sl_pips * 0.10
    signals["take_profit"] = df["close"] + tp_pips * 0.10
    signals["max_bars"] = 40

    return signals


# ============================================================================
# OPTIMIZER
# ============================================================================

def optimize_scalping_strategies(df, verbose=True):
    """
    Comprehensive scalping strategy optimizer
    Tests multiple strategies and parameters
    """

    # Scalping-specific config
    config = BacktestConfig(
        initial_capital=5000.0,  # Smaller for scalping
        contract_value=10.0,
        commission=1.25,
        slippage_ticks=1,
        tick_size=0.10,
        max_trades_per_day=20,  # More for scalping
        max_daily_loss=150.0
    )

    results = []

    if verbose:
        print("\n" + "="*80)
        print("SCALPING STRATEGY OPTIMIZER - COMPREHENSIVE TEST")
        print("="*80)

    # ========== STRATEGY 1: RSI + EMA ==========
    if verbose:
        print("\n[1/5] Testing RSI + EMA Strategy...")

    rsi_periods = [7, 9]
    rsi_buys = [30, 35]
    rsi_sells = [65, 70]
    ema_fasts = [5, 7]
    ema_slows = [13, 15]
    sl_pips_list = [2, 3]
    tp_pips_list = [5, 7]

    for rsi_p, rsi_buy, rsi_sell, ema_f, ema_s, sl_p, tp_p in itertools.product(
        rsi_periods, rsi_buys, rsi_sells, ema_fasts, ema_slows, sl_pips_list, tp_pips_list
    ):
        try:
            signals = strategy_rsi_ema_scalp(df, rsi_p, rsi_buy, rsi_sell, ema_f, ema_s, sl_p, tp_p)
            engine = ScalpingBacktestEngine(df, config)
            result = engine.run_strategy(signals)

            result["strategy"] = "RSI_EMA"
            result["params"] = {
                "rsi_period": rsi_p,
                "rsi_buy": rsi_buy,
                "rsi_sell": rsi_sell,
                "ema_fast": ema_f,
                "ema_slow": ema_s,
                "sl_pips": sl_p,
                "tp_pips": tp_p
            }
            results.append(result)
        except:
            pass

    # ========== STRATEGY 2: MACD ==========
    if verbose:
        print("[2/5] Testing MACD Strategy...")

    macd_fasts = [8, 10]
    macd_slows = [17, 19]
    macd_signals = [5, 7]

    for fast, slow, sig, sl_p, tp_p in itertools.product(
        macd_fasts, macd_slows, macd_signals, sl_pips_list, tp_pips_list
    ):
        try:
            signals = strategy_macd_scalp(df, fast, slow, sig, sl_p, tp_p)
            engine = ScalpingBacktestEngine(df, config)
            result = engine.run_strategy(signals)

            result["strategy"] = "MACD"
            result["params"] = {
                "macd_fast": fast,
                "macd_slow": slow,
                "macd_signal": sig,
                "sl_pips": sl_p,
                "tp_pips": tp_p
            }
            results.append(result)
        except:
            pass

    # ========== STRATEGY 3: Bollinger Breakout ==========
    if verbose:
        print("[3/5] Testing Bollinger Bands Breakout...")

    bb_periods = [12, 14]
    bb_mults = [1.5, 1.8]

    for period, mult, sl_p, tp_p in itertools.product(
        bb_periods, bb_mults, sl_pips_list, tp_pips_list
    ):
        try:
            signals = strategy_bollinger_breakout_scalp(df, period, mult, sl_p, tp_p)
            engine = ScalpingBacktestEngine(df, config)
            result = engine.run_strategy(signals)

            result["strategy"] = "BB_BREAKOUT"
            result["params"] = {
                "bb_period": period,
                "bb_mult": mult,
                "sl_pips": sl_p,
                "tp_pips": tp_p
            }
            results.append(result)
        except:
            pass

    # ========== STRATEGY 4: Confluence ==========
    if verbose:
        print("[4/5] Testing Confluence Scalping...")

    for rsi_p, ema_f, ema_s, sl_p, tp_p in itertools.product(
        [8, 10], [5, 7], [13, 15], [2, 3], [5, 6]
    ):
        try:
            signals = strategy_confluence_scalp(df, rsi_p, ema_f, ema_s, sl_p, tp_p)
            engine = ScalpingBacktestEngine(df, config)
            result = engine.run_strategy(signals)

            result["strategy"] = "CONFLUENCE"
            result["params"] = {
                "rsi_period": rsi_p,
                "ema_fast": ema_f,
                "ema_slow": ema_s,
                "sl_pips": sl_p,
                "tp_pips": tp_p
            }
            results.append(result)
        except:
            pass

    # ========== STRATEGY 5: EMA Crossover ==========
    if verbose:
        print("[5/5] Testing Ultra-Fast EMA Crossover...")

    for fast, slow, sl_p, tp_p in itertools.product(
        [3, 4], [7, 10], [2, 3], [4, 5]
    ):
        try:
            signals = strategy_ema_crossover_scalp(df, fast, slow, sl_p, tp_p)
            engine = ScalpingBacktestEngine(df, config)
            result = engine.run_strategy(signals)

            result["strategy"] = "EMA_CROSSOVER"
            result["params"] = {
                "ema_fast": fast,
                "ema_slow": slow,
                "sl_pips": sl_p,
                "tp_pips": tp_p
            }
            results.append(result)
        except:
            pass

    # ========== STRATEGY 6: SuperTrend Scalping ==========
    if verbose:
        print("[BONUS] Testing SuperTrend Scalping (proven for gold)...")

    st_periods = [7, 10]
    st_mults = [1.5, 2.0, 2.5]

    for period, mult, sl_p, tp_p in itertools.product(
        st_periods, st_mults, [2, 3], [5, 6, 7]
    ):
        try:
            signals = strategy_supertrend_scalp(df, period, mult, sl_p, tp_p)
            engine = ScalpingBacktestEngine(df, config)
            result = engine.run_strategy(signals)

            result["strategy"] = "SUPERTREND"
            result["params"] = {
                "st_period": period,
                "st_mult": mult,
                "sl_pips": sl_p,
                "tp_pips": tp_p
            }
            results.append(result)
        except:
            pass

    return results


def rank_results(results):
    """Rank results by profitability and consistency."""
    valid_results = [r for r in results if r["total_trades"] >= 10]

    if not valid_results:
        return []

    # Scoring: Profit Factor is king, then win rate, then PnL consistency
    max_pnl = max([r["total_pnl"] for r in valid_results]) if valid_results else 1.0
    max_pnl = max(max_pnl, 1.0)  # Avoid division by zero

    for r in valid_results:
        # Profit Factor (critical - must be > 1.0)
        pf_score = max(min((r["profit_factor"] - 0.5) / 2.0, 1.0), 0)  # 0.5 to 2.5 range

        # Win Rate (must be > 50% for profitable scalping)
        wr_score = max((r["win_rate"] - 50.0) / 50.0, 0)  # 50% to 100% range

        # Total PnL (positive PnL is critical)
        pnl_score = max(r["total_pnl"] / max_pnl, 0)

        # Number of trades (enough samples)
        trades_score = min(r["total_trades"] / 100.0, 1.0)

        r["score"] = (pf_score * 0.40) + (wr_score * 0.35) + (pnl_score * 0.15) + (trades_score * 0.10)

    return sorted(valid_results, key=lambda x: x["score"], reverse=True)


def print_results(ranked_results, top_n=10):
    """Print top results in a table."""
    print("\n" + "="*140)
    print("TOP SCALPING STRATEGIES RANKED BY PROFITABILITY & CONSISTENCY")
    print("="*140)
    print(f"{'Rank':<5} {'Strategy':<15} {'Trades':<8} {'Win%':<7} {'PF':<6} {'Sharpe':<7} {'PnL':<10} {'Score':<7}")
    print("-"*140)

    for i, result in enumerate(ranked_results[:top_n], 1):
        strategy = result["strategy"]
        trades = result["total_trades"]
        win_rate = result["win_rate"]
        pf = result["profit_factor"]
        sharpe = result["sharpe_ratio"]
        pnl = result["total_pnl"]
        score = result["score"]

        print(f"{i:<5} {strategy:<15} {trades:<8} {win_rate:>6.1f}% {pf:>5.2f} {sharpe:>6.2f} ${pnl:>8.0f}  {score:>6.3f}")

    print("-"*140)


def print_best_strategy(best):
    """Print detailed info about best strategy."""
    print("\n" + "="*80)
    print("🏆 BEST SCALPING STRATEGY")
    print("="*80)
    print(f"Strategy:         {best['strategy']}")
    print(f"Parameters:       {best['params']}")
    print(f"\nPerformance Metrics:")
    print(f"  Total Trades:   {best['total_trades']}")
    print(f"  Winning Trades: {best['winning_trades']} ({best['win_rate']:.1f}%)")
    print(f"  Losing Trades:  {best['losing_trades']}")
    print(f"  Profit Factor:  {best['profit_factor']:.2f}")
    print(f"  Total PnL:      ${best['total_pnl']:.2f}")
    print(f"  Avg Winner:     ${best['avg_win']:.2f}")
    print(f"  Avg Loser:      ${best['avg_loss']:.2f}")
    print(f"  Sharpe Ratio:   {best['sharpe_ratio']:.2f}")
    print(f"  Max Drawdown:   {best['max_drawdown']:.2%}")
    print(f"  Final Equity:   ${best['final_equity']:.2f}")
    print(f"  Total Return:   {(best['final_equity'] - 5000) / 5000 * 100:.1f}%")
    print("="*80)


if __name__ == "__main__":
    print("\n🚀 Fetching real TradingView data for MGC1 (Micro Gold Futures)...")

    try:
        tv = TradingViewData()
        df = tv.fetch(symbol="MGCM2025", exchange="COMEX", interval="60", n_bars=5000)

        if len(df) < 500:
            print("⚠️  Not enough data. Using synthetic data...")
            df = tv._generate_realistic_gold_data(2900.0, n_bars=5000)
    except Exception as e:
        print(f"⚠️  Could not fetch live data: {e}")
        print("Using synthetic gold data for backtesting...")
        tv = TradingViewData()
        df = tv._generate_realistic_gold_data(2900.0, n_bars=5000)

    # Run optimization
    results = optimize_scalping_strategies(df, verbose=True)

    # Rank and display results
    ranked = rank_results(results)

    if ranked:
        print_results(ranked, top_n=15)
        print_best_strategy(ranked[0])

        # Save results
        results_df = pd.DataFrame([
            {
                "rank": i+1,
                "strategy": r["strategy"],
                "params": str(r["params"]),
                "trades": r["total_trades"],
                "win_rate": r["win_rate"],
                "profit_factor": r["profit_factor"],
                "sharpe_ratio": r["sharpe_ratio"],
                "total_pnl": r["total_pnl"],
                "final_equity": r["final_equity"],
                "score": r["score"]
            }
            for i, r in enumerate(ranked)
        ])

        results_df.to_csv("scalping_optimization_results.csv", index=False)
        print("\n✅ Results saved to scalping_optimization_results.csv")
    else:
        print("❌ No valid results found. Please check your data.")
