"""
Individueller Indikator-Vergleichstest
========================================
Testet jeden der Top 10 TradingView Indikatoren einzeln,
um zu bestimmen welcher der profitabelste fuer MNQ und MGC ist.
"""

import pandas as pd
import numpy as np
from mnq_mgc_strategy import (
    ema, sma, hull_ma, atr, rsi, stochastic_rsi, macd, adx,
    supertrend, ichimoku, bollinger_bands, keltner_channels, vwap_session, obv,
    AdvancedBacktestEngine, MNQ_CONFIG, MGC_CONFIG,
    generate_mnq_data, generate_mgc_data
)
import warnings
warnings.filterwarnings("ignore")


def single_supertrend_strategy(df, period=10, mult=3.0, sl_mult=2.0, tp_mult=4.0):
    """Nur SuperTrend als Signal."""
    signals = pd.DataFrame(index=df.index)
    st, st_dir = supertrend(df, period, mult)
    atr_val = atr(df)

    signals["long_entry"] = (st_dir == 1) & (st_dir.shift(1) != 1)
    signals["short_entry"] = (st_dir == -1) & (st_dir.shift(1) != -1)
    signals["long_exit"] = (st_dir == -1) & (st_dir.shift(1) != -1)
    signals["short_exit"] = (st_dir == 1) & (st_dir.shift(1) != 1)
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def single_rsi_strategy(df, period=14, oversold=30, overbought=70, sl_mult=2.0, tp_mult=4.0):
    """Nur RSI als Signal."""
    signals = pd.DataFrame(index=df.index)
    rsi_val = rsi(df["close"], period)
    atr_val = atr(df)

    signals["long_entry"] = (rsi_val > oversold) & (rsi_val.shift(1) <= oversold)
    signals["short_entry"] = (rsi_val < overbought) & (rsi_val.shift(1) >= overbought)
    signals["long_exit"] = rsi_val > overbought
    signals["short_exit"] = rsi_val < oversold
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def single_macd_strategy(df, fast=12, slow=26, signal_p=9, sl_mult=2.0, tp_mult=4.0):
    """Nur MACD als Signal."""
    signals = pd.DataFrame(index=df.index)
    macd_line, signal_line, hist = macd(df["close"], fast, slow, signal_p)
    atr_val = atr(df)

    signals["long_entry"] = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    signals["short_entry"] = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))
    signals["long_exit"] = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))
    signals["short_exit"] = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def single_ema_crossover_strategy(df, fast=9, slow=21, sl_mult=2.0, tp_mult=4.0):
    """Nur EMA Crossover als Signal."""
    signals = pd.DataFrame(index=df.index)
    fast_ema = ema(df["close"], fast)
    slow_ema = ema(df["close"], slow)
    atr_val = atr(df)

    signals["long_entry"] = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
    signals["short_entry"] = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))
    signals["long_exit"] = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))
    signals["short_exit"] = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def single_bb_strategy(df, period=20, mult=2.0, sl_mult=2.0, tp_mult=4.0):
    """Nur Bollinger Bands als Signal (Mean Reversion)."""
    signals = pd.DataFrame(index=df.index)
    bb_basis, bb_upper, bb_lower, pct_b, bw = bollinger_bands(df["close"], period, mult)
    atr_val = atr(df)

    signals["long_entry"] = (df["close"] < bb_lower) & (df["close"].shift(1) >= bb_lower.shift(1))
    signals["short_entry"] = (df["close"] > bb_upper) & (df["close"].shift(1) <= bb_upper.shift(1))
    signals["long_exit"] = df["close"] > bb_basis
    signals["short_exit"] = df["close"] < bb_basis
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def single_vwap_strategy(df, sl_mult=2.0, tp_mult=4.0):
    """Nur VWAP als Signal."""
    signals = pd.DataFrame(index=df.index)
    vwap_val = vwap_session(df)
    atr_val = atr(df)
    rsi_val = rsi(df["close"])

    cross_above = (df["close"] > vwap_val) & (df["close"].shift(1) <= vwap_val.shift(1))
    cross_below = (df["close"] < vwap_val) & (df["close"].shift(1) >= vwap_val.shift(1))

    signals["long_entry"] = cross_above & (rsi_val > 40) & (rsi_val < 65)
    signals["short_entry"] = cross_below & (rsi_val > 35) & (rsi_val < 60)
    signals["long_exit"] = cross_below
    signals["short_exit"] = cross_above
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def single_ichimoku_strategy(df, sl_mult=2.5, tp_mult=5.0):
    """Nur Ichimoku Cloud als Signal."""
    signals = pd.DataFrame(index=df.index)
    tenkan, kijun, senkou_a, senkou_b, _ = ichimoku(df)
    atr_val = atr(df)

    cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)
    tk_cross_up = (tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1))
    tk_cross_down = (tenkan < kijun) & (tenkan.shift(1) >= kijun.shift(1))

    signals["long_entry"] = tk_cross_up & (df["close"] > cloud_top)
    signals["short_entry"] = tk_cross_down & (df["close"] < cloud_bot)
    signals["long_exit"] = tk_cross_down
    signals["short_exit"] = tk_cross_up
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def single_stoch_rsi_strategy(df, sl_mult=2.0, tp_mult=4.0):
    """Nur Stochastic RSI als Signal."""
    signals = pd.DataFrame(index=df.index)
    stoch_k, stoch_d = stochastic_rsi(df["close"])
    atr_val = atr(df)

    signals["long_entry"] = (stoch_k > stoch_d) & (stoch_k.shift(1) <= stoch_d.shift(1)) & (stoch_k < 80)
    signals["short_entry"] = (stoch_k < stoch_d) & (stoch_k.shift(1) >= stoch_d.shift(1)) & (stoch_k > 20)
    signals["long_exit"] = (stoch_k > 80) | ((stoch_k < stoch_d) & (stoch_k.shift(1) >= stoch_d.shift(1)))
    signals["short_exit"] = (stoch_k < 20) | ((stoch_k > stoch_d) & (stoch_k.shift(1) <= stoch_d.shift(1)))
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def single_adx_strategy(df, threshold=25, sl_mult=2.0, tp_mult=4.0):
    """Nur ADX als Signal (mit DI+/DI-)."""
    signals = pd.DataFrame(index=df.index)
    adx_val, plus_di, minus_di = adx(df)
    atr_val = atr(df)

    di_cross_up = (plus_di > minus_di) & (plus_di.shift(1) <= minus_di.shift(1))
    di_cross_down = (minus_di > plus_di) & (minus_di.shift(1) <= plus_di.shift(1))

    signals["long_entry"] = di_cross_up & (adx_val > threshold)
    signals["short_entry"] = di_cross_down & (adx_val > threshold)
    signals["long_exit"] = di_cross_down | (adx_val < 15)
    signals["short_exit"] = di_cross_up | (adx_val < 15)
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def single_hull_ma_strategy(df, period=20, sl_mult=2.0, tp_mult=4.0):
    """Nur Hull Moving Average als Signal."""
    signals = pd.DataFrame(index=df.index)
    hma = hull_ma(df["close"], period)
    atr_val = atr(df)

    hma_turn_up = (hma > hma.shift(1)) & (hma.shift(1) <= hma.shift(2))
    hma_turn_down = (hma < hma.shift(1)) & (hma.shift(1) >= hma.shift(2))

    signals["long_entry"] = hma_turn_up
    signals["short_entry"] = hma_turn_down
    signals["long_exit"] = hma_turn_down
    signals["short_exit"] = hma_turn_up
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def run_indicator_comparison(df, config, instrument_name):
    """Teste alle 10 Indikatoren einzeln und vergleiche."""
    print(f"\n{'='*90}")
    print(f"  INDIKATOR-VERGLEICH: {instrument_name}")
    print(f"  Jeder Indikator wird einzeln als Trading-Signal getestet")
    print(f"{'='*90}")

    indicators = {
        "1. SuperTrend":     single_supertrend_strategy,
        "2. RSI":            single_rsi_strategy,
        "3. MACD":           single_macd_strategy,
        "4. EMA Crossover":  single_ema_crossover_strategy,
        "5. Bollinger Bands": single_bb_strategy,
        "6. VWAP":           single_vwap_strategy,
        "7. Ichimoku Cloud": single_ichimoku_strategy,
        "8. Stochastic RSI": single_stoch_rsi_strategy,
        "9. ADX":            single_adx_strategy,
        "10. Hull MA":       single_hull_ma_strategy,
    }

    results = {}
    for name, strat_fn in indicators.items():
        try:
            signals = strat_fn(df)
            engine = AdvancedBacktestEngine(df, config)
            metrics = engine.run_strategy(signals)
            results[name] = metrics
        except Exception as e:
            results[name] = {"total_trades": 0, "error": str(e)}

    # Sortiere nach Profit Factor
    valid = {k: v for k, v in results.items() if v.get("total_trades", 0) >= 3}

    print(f"\n{'Indikator':<22} {'Trades':>7} {'Win%':>7} {'PnL($)':>10} "
          f"{'Return%':>8} {'PF':>6} {'DD%':>7} {'Sharpe':>7} {'Expect':>8}")
    print("-" * 95)

    ranked = sorted(valid.items(),
                    key=lambda x: x[1].get("profit_factor", 0) * 10 + x[1].get("sharpe_ratio", 0) * 5,
                    reverse=True)

    medals = [">>>", ">> ", ">  "]
    for i, (name, m) in enumerate(ranked):
        prefix = medals[i] if i < 3 else "   "
        print(f"{prefix}{name:<19} {m['total_trades']:>7} {m['win_rate']:>6.1f}% "
              f"${m['total_pnl']:>9.2f} {m['total_return_pct']:>7.1f}% "
              f"{m['profit_factor']:>6.2f} {m['max_drawdown_pct']:>6.1f}% "
              f"{m['sharpe_ratio']:>7.2f} ${m.get('expectancy', 0):>7.2f}")

    # Indikatoren ohne Trades
    no_trades = {k: v for k, v in results.items() if v.get("total_trades", 0) < 3}
    if no_trades:
        print(f"\n  Zu wenig Trades (<3): {', '.join(no_trades.keys())}")

    if ranked:
        winner_name, winner_metrics = ranked[0]
        print(f"\n{'='*90}")
        print(f"  BESTER EINZELINDIKATOR fuer {instrument_name}: {winner_name}")
        print(f"  PF={winner_metrics['profit_factor']:.2f}, "
              f"WR={winner_metrics['win_rate']:.1f}%, "
              f"PnL=${winner_metrics['total_pnl']:.2f}, "
              f"DD={winner_metrics['max_drawdown_pct']:.1f}%")

        # Top 3 Detail
        print(f"\n  TOP 3 DETAIL:")
        for i, (name, m) in enumerate(ranked[:3]):
            print(f"\n  #{i+1} {name}:")
            print(f"    Trades: {m['total_trades']} | Winners: {m['winning_trades']} | Losers: {m['losing_trades']}")
            print(f"    Avg Winner: ${m['avg_winner']:.2f} | Avg Loser: ${m['avg_loser']:.2f}")
            print(f"    Largest Win: ${m['largest_winner']:.2f} | Largest Loss: ${m['largest_loser']:.2f}")
            print(f"    Long PnL: ${m['long_pnl']:.2f} ({m['long_trades']}T) | Short PnL: ${m['short_pnl']:.2f} ({m['short_trades']}T)")
            print(f"    Max Consec Wins: {m['max_consec_wins']} | Max Consec Losses: {m['max_consec_losses']}")
            print(f"    Exit Reasons: {m['exit_reasons']}")

    print(f"{'='*90}")
    return ranked


if __name__ == "__main__":
    print("="*90)
    print("  TOP 10 TRADINGVIEW INDIKATOREN - EINZELVERGLEICH")
    print("  Welcher Indikator ist der beste fuer MNQ und MGC?")
    print("="*90)

    mnq_df = generate_mnq_data(5000)
    mgc_df = generate_mgc_data(5000)

    print(f"\n  MNQ: {len(mnq_df)} Bars, ${mnq_df['close'].min():.0f}-${mnq_df['close'].max():.0f}")
    print(f"  MGC: {len(mgc_df)} Bars, ${mgc_df['close'].min():.0f}-${mgc_df['close'].max():.0f}")

    mnq_ranked = run_indicator_comparison(mnq_df, MNQ_CONFIG, "MNQ (Micro Nasdaq)")
    mgc_ranked = run_indicator_comparison(mgc_df, MGC_CONFIG, "MGC (Micro Gold)")

    print(f"\n\n{'='*90}")
    print("  ZUSAMMENFASSUNG: BESTE INDIKATOREN")
    print(f"{'='*90}")
    if mnq_ranked:
        print(f"  MNQ Platz 1: {mnq_ranked[0][0]} (PF={mnq_ranked[0][1]['profit_factor']:.2f})")
        if len(mnq_ranked) > 1:
            print(f"  MNQ Platz 2: {mnq_ranked[1][0]} (PF={mnq_ranked[1][1]['profit_factor']:.2f})")
        if len(mnq_ranked) > 2:
            print(f"  MNQ Platz 3: {mnq_ranked[2][0]} (PF={mnq_ranked[2][1]['profit_factor']:.2f})")
    if mgc_ranked:
        print(f"  MGC Platz 1: {mgc_ranked[0][0]} (PF={mgc_ranked[0][1]['profit_factor']:.2f})")
        if len(mgc_ranked) > 1:
            print(f"  MGC Platz 2: {mgc_ranked[1][0]} (PF={mgc_ranked[1][1]['profit_factor']:.2f})")
        if len(mgc_ranked) > 2:
            print(f"  MGC Platz 3: {mgc_ranked[2][0]} (PF={mgc_ranked[2][1]['profit_factor']:.2f})")
    print(f"{'='*90}")
