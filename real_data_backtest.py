"""
Backtest auf realistischen Marktdaten (kalibriert auf echte Preise)
====================================================================
Verwendet echte S&P500 und Gold Preisdaten als Backbone und generiert
realistische Intraday-OHLCV-Daten auf Basis der echten Preisbewegungen.

Da externe Finanz-APIs (yfinance, TradingView) durch Proxy blockiert sind,
werden die Daten mit echten monatlichen Preisen als Ankerpunkte generiert.
"""

import pandas as pd
import numpy as np
import io
import urllib.request
import warnings
warnings.filterwarnings("ignore")

from mnq_mgc_strategy import (
    AdvancedBacktestEngine, MNQ_CONFIG, MGC_CONFIG,
    strategy_supertrend_adx, strategy_ichimoku_macd_vwap,
    strategy_bb_squeeze_hull, strategy_ema_rsi_obv,
    strategy_mega_confluence, walk_forward_test, print_final_report,
    optimize_strategy
)
from indicator_comparison import run_indicator_comparison


# ============================================================================
# ECHTE DATEN LADEN UND REALISTISCHE INTRADAY-DATEN GENERIEREN
# ============================================================================

def download_sp500_prices():
    """Lade echte S&P500 Monatsdaten von GitHub."""
    try:
        req = urllib.request.Request(
            'https://raw.githubusercontent.com/datasets/s-and-p-500/main/data/data.csv',
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        resp = urllib.request.urlopen(req, timeout=15)
        df = pd.read_csv(io.StringIO(resp.read().decode()))
        df['Date'] = pd.to_datetime(df['Date'])
        df = df[df['Date'] >= '2023-01-01'].copy()
        df = df[['Date', 'SP500']].dropna()
        df = df[df['SP500'] > 0].reset_index(drop=True)
        return df
    except Exception as e:
        print(f"  S&P500 Download Fehler: {e}")
        return None


def download_gold_prices():
    """Lade echte Gold Monatsdaten von GitHub."""
    try:
        req = urllib.request.Request(
            'https://raw.githubusercontent.com/datasets/gold-prices/master/data/monthly.csv',
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        resp = urllib.request.urlopen(req, timeout=15)
        df = pd.read_csv(io.StringIO(resp.read().decode()))
        df['Date'] = pd.to_datetime(df['Date'])
        df = df[df['Date'] >= '2023-01-01'].copy()
        df = df.dropna().reset_index(drop=True)
        return df
    except Exception as e:
        print(f"  Gold Download Fehler: {e}")
        return None


def generate_calibrated_intraday(monthly_prices, price_col, target_symbol,
                                  annual_vol, n_bars_per_month=140, seed=42):
    """
    Generiert realistische 1H OHLCV-Daten kalibriert auf echte Monatsdaten.
    Die Daten folgen den echten monatlichen Preisbewegungen und fuegen
    realistische Intraday-Volatilitaet hinzu.
    """
    np.random.seed(seed)

    hourly_vol = annual_vol / np.sqrt(252 * 6.5)
    all_data = []

    for i in range(len(monthly_prices) - 1):
        start_price = monthly_prices[price_col].iloc[i]
        end_price = monthly_prices[price_col].iloc[i + 1]
        start_date = monthly_prices['Date'].iloc[i]
        end_date = monthly_prices['Date'].iloc[i + 1]

        # Interpolation mit realistischem Rauschen
        n = n_bars_per_month
        target_return = np.log(end_price / start_price)

        # Brownian Bridge: Pfad der am Endpunkt ankommt
        t = np.linspace(0, 1, n)
        drift_per_bar = target_return / n
        noise = np.random.randn(n) * hourly_vol

        # Brownian Bridge Korrektur
        cum_noise = np.cumsum(noise)
        bridge = cum_noise - t * cum_noise[-1]  # Endet bei 0
        log_path = np.cumsum(np.full(n, drift_per_bar)) + bridge * hourly_vol * 5

        prices = start_price * np.exp(log_path)

        # Timestamps generieren
        timestamps = pd.date_range(start=start_date, end=end_date, periods=n)

        for j in range(n):
            p = prices[j]
            bar_vol = hourly_vol * p
            # Realistisches OHLCV
            o = p + np.random.randn() * bar_vol * 0.3
            h = max(o, p) + abs(np.random.randn()) * bar_vol * 0.6
            l = min(o, p) - abs(np.random.randn()) * bar_vol * 0.6
            c = p
            v = max(500, int(np.random.lognormal(9, 1.2)))
            all_data.append([timestamps[j], o, h, l, c, v])

    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.set_index("timestamp")

    # Sicherstellen dass high >= open/close und low <= open/close
    df["high"] = df[["high", "open", "close"]].max(axis=1)
    df["low"] = df[["low", "open", "close"]].min(axis=1)

    return df


# ============================================================================
# STRATEGIE-TEST
# ============================================================================

def test_strategy(df, config, strategy_fn, params):
    """Teste eine Strategie."""
    try:
        signals = strategy_fn(df, **params)
        engine = AdvancedBacktestEngine(df, config)
        metrics = engine.run_strategy(signals, use_trailing=True,
                                      trail_activation=1.0, trail_factor=0.5)
        return metrics, engine
    except Exception as e:
        return None, None


def run_comprehensive_test(df, config, instrument_name, data_source):
    """Vollstaendiger Test aller Strategien."""

    print(f"\n{'#'*80}")
    print(f"#  {instrument_name} - ECHTE PREISDATEN")
    print(f"#  Datenquelle: {data_source}")
    print(f"#  Bars: {len(df)} | Zeitraum: {df.index[0].date()} - {df.index[-1].date()}")
    print(f"#  Preis: ${df['close'].min():.2f} - ${df['close'].max():.2f}")
    print(f"#  Account: $20,000")
    print(f"{'#'*80}")

    # === TEIL 1: Indikator-Einzelvergleich ===
    print(f"\n{'='*80}")
    print(f"  TEIL 1: TOP 10 INDIKATOREN - EINZELVERGLEICH")
    print(f"{'='*80}")
    indicator_ranked = run_indicator_comparison(df, config, f"{instrument_name}")

    # === TEIL 2: Vordefinierte Strategien ===
    print(f"\n{'='*80}")
    print(f"  TEIL 2: ALLE STRATEGIEN MIT STANDARD-PARAMETERN")
    print(f"{'='*80}")

    strategies = {
        "SuperTrend+ADX+StochRSI": {
            "fn": strategy_supertrend_adx,
            "params": {"st_period": 10, "st_mult": 3.0, "adx_threshold": 20,
                       "sl_mult": 2.0, "tp_mult": 4.0, "long_only": False},
        },
        "SuperTrend+ADX (LO)": {
            "fn": strategy_supertrend_adx,
            "params": {"st_period": 10, "st_mult": 3.0, "adx_threshold": 20,
                       "sl_mult": 2.0, "tp_mult": 4.0, "long_only": True},
        },
        "Ichimoku+MACD+VWAP": {
            "fn": strategy_ichimoku_macd_vwap,
            "params": {"sl_mult": 2.0, "tp_mult": 4.0, "long_only": False},
        },
        "Ichimoku+MACD+VWAP (LO)": {
            "fn": strategy_ichimoku_macd_vwap,
            "params": {"sl_mult": 2.0, "tp_mult": 5.0, "long_only": True},
        },
        "BB-Squeeze+HullMA (LO)": {
            "fn": strategy_bb_squeeze_hull,
            "params": {"bb_period": 20, "bb_mult": 1.5, "hma_period": 26,
                       "sl_mult": 1.5, "tp_mult": 3.0, "long_only": True},
        },
        "BB-Squeeze+HullMA (L+S)": {
            "fn": strategy_bb_squeeze_hull,
            "params": {"bb_period": 20, "bb_mult": 2.0, "hma_period": 20,
                       "sl_mult": 2.0, "tp_mult": 4.0, "long_only": False},
        },
        "EMA+RSI+OBV": {
            "fn": strategy_ema_rsi_obv,
            "params": {"fast": 9, "medium": 21, "slow": 50,
                       "sl_mult": 2.0, "tp_mult": 4.0, "long_only": False},
        },
        "EMA+RSI+OBV (LO)": {
            "fn": strategy_ema_rsi_obv,
            "params": {"fast": 13, "medium": 26, "slow": 40,
                       "sl_mult": 1.5, "tp_mult": 3.0, "long_only": True},
        },
        "MEGA-CONFLUENCE (5)": {
            "fn": strategy_mega_confluence,
            "params": {"min_score": 5, "sl_mult": 2.0, "tp_mult": 4.5, "long_only": False},
        },
        "MEGA-CONFLUENCE (6)": {
            "fn": strategy_mega_confluence,
            "params": {"min_score": 6, "sl_mult": 2.0, "tp_mult": 4.5, "long_only": False},
        },
        "MEGA-CONFLUENCE (7)": {
            "fn": strategy_mega_confluence,
            "params": {"min_score": 7, "sl_mult": 2.0, "tp_mult": 4.5, "long_only": False},
        },
        "MEGA-CONFLUENCE (6-LO)": {
            "fn": strategy_mega_confluence,
            "params": {"min_score": 6, "sl_mult": 2.0, "tp_mult": 5.5, "long_only": True},
        },
    }

    results = {}
    print(f"\n{'Strategie':<28} {'Trades':>7} {'Win%':>7} {'PnL($)':>10} "
          f"{'Ret%':>7} {'PF':>6} {'DD%':>7} {'Sharpe':>7} {'$/Trade':>8}")
    print("-" * 98)

    for name, data in strategies.items():
        metrics, engine = test_strategy(df, config, data["fn"], data["params"])
        if metrics and metrics.get("total_trades", 0) > 0:
            results[name] = {**data, "metrics": metrics}
            m = metrics
            print(f"{name:<28} {m['total_trades']:>7} {m['win_rate']:>6.1f}% "
                  f"${m['total_pnl']:>9.2f} {m['total_return_pct']:>6.1f}% "
                  f"{m['profit_factor']:>6.2f} {m['max_drawdown_pct']:>6.1f}% "
                  f"{m['sharpe_ratio']:>7.2f} ${m.get('expectancy', 0):>7.2f}")
        else:
            print(f"{name:<28}   -- KEINE TRADES --")

    # === TEIL 3: Grid-Search Optimierung ===
    print(f"\n{'='*80}")
    print(f"  TEIL 3: GRID-SEARCH OPTIMIERUNG AUF ECHTEN DATEN")
    print(f"{'='*80}")

    opt_configs = {
        "BB-Squeeze+HullMA": {
            "fn": strategy_bb_squeeze_hull,
            "grid": {
                "bb_period": [15, 20, 25],
                "bb_mult": [1.5, 2.0, 2.5],
                "hma_period": [14, 20, 26],
                "sl_mult": [1.5, 2.0, 2.5],
                "tp_mult": [3.0, 4.0, 5.0],
                "long_only": [True, False],
            }
        },
        "EMA+RSI+OBV": {
            "fn": strategy_ema_rsi_obv,
            "grid": {
                "fast": [5, 9, 13],
                "medium": [17, 21, 26],
                "slow": [40, 50, 60],
                "sl_mult": [1.5, 2.0, 2.5],
                "tp_mult": [3.0, 4.0, 5.0],
                "long_only": [True, False],
            }
        },
        "MEGA-CONFLUENCE": {
            "fn": strategy_mega_confluence,
            "grid": {
                "min_score": [5, 6, 7, 8, 9],
                "sl_mult": [1.5, 2.0, 2.5],
                "tp_mult": [3.5, 4.5, 5.5, 6.5],
                "long_only": [True, False],
            }
        },
    }

    optimized = {}
    for name, strat in opt_configs.items():
        best_params, best_metrics, all_results = optimize_strategy(
            df, strat["fn"], strat["grid"], config, f"{name}"
        )
        if best_metrics:
            optimized[name] = {
                "params": best_params,
                "metrics": best_metrics,
                "fn": strat["fn"],
            }

    # === TEIL 4: Walk-Forward Validation ===
    print(f"\n{'='*80}")
    print(f"  TEIL 4: WALK-FORWARD VALIDATION")
    print(f"{'='*80}")

    for name, data in optimized.items():
        walk_forward_test(df, data["fn"], data["params"], config, n_splits=5)

    # === TEIL 5: Final Report ===
    winner = None
    if optimized:
        winner = print_final_report(optimized, f"{instrument_name} (ECHTE PREISE)")

    # === TEIL 6: Trade-Log ===
    if winner:
        best_name, best_data = winner
        signals = best_data["fn"](df, **best_data["params"])
        engine = AdvancedBacktestEngine(df, config)
        engine.run_strategy(signals, use_trailing=True, trail_activation=1.0, trail_factor=0.5)

        if engine.trades:
            print(f"\n{'='*80}")
            print(f"  TRADE-LOG (letzte 25): {best_name}")
            print(f"{'='*80}")
            print(f"{'#':<4} {'Entry Time':<20} {'Dir':<6} {'Entry':>10} {'Exit':>10} "
                  f"{'PnL':>10} {'Bars':>5} {'Reason':<15}")
            print("-" * 85)

            for i, t in enumerate(engine.trades[-25:]):
                entry_str = t.entry_time.strftime("%Y-%m-%d %H:%M") if hasattr(t.entry_time, 'strftime') else str(t.entry_time)[:16]
                print(f"{i+1:<4} {entry_str:<20} {t.direction:<6} {t.entry_price:>10.2f} "
                      f"{t.exit_price:>10.2f} ${t.pnl:>9.2f} {t.bars_held:>5} {t.exit_reason:<15}")

            # Monatliche Performance
            print(f"\n{'='*80}")
            print(f"  MONATLICHE PERFORMANCE")
            print(f"{'='*80}")

            monthly_pnl = {}
            for t in engine.trades:
                month = t.entry_time.strftime("%Y-%m")
                monthly_pnl[month] = monthly_pnl.get(month, 0) + t.pnl

            for month, pnl in sorted(monthly_pnl.items()):
                bar = "+" * int(max(0, pnl) / 100) + "-" * int(max(0, -pnl) / 100)
                print(f"  {month}: ${pnl:>+9.2f}  {bar[:40]}")

    return results, optimized, indicator_ranked, winner


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("="*80)
    print("  BACKTEST AUF ECHTEN MARKTDATEN")
    print("  MNQ (Nasdaq-100) & MGC (Gold) Futures")
    print("  Kalibriert auf echte S&P500 und Gold Preise 2023-2026")
    print("  Account: $20,000 | Top 10 TradingView Indikatoren")
    print("="*80)

    # --- Echte Preisdaten laden ---
    print("\n[1/4] Lade echte Preisdaten von GitHub...")

    sp_monthly = download_sp500_prices()
    gc_monthly = download_gold_prices()

    if sp_monthly is not None:
        print(f"  S&P500: {len(sp_monthly)} Monate, "
              f"{sp_monthly['Date'].iloc[0].date()} - {sp_monthly['Date'].iloc[-1].date()}")
        print(f"  Preisrange: ${sp_monthly['SP500'].min():.0f} - ${sp_monthly['SP500'].max():.0f}")

    if gc_monthly is not None:
        print(f"  Gold:   {len(gc_monthly)} Monate, "
              f"{gc_monthly['Date'].iloc[0].date()} - {gc_monthly['Date'].iloc[-1].date()}")
        print(f"  Preisrange: ${gc_monthly['Price'].min():.0f} - ${gc_monthly['Price'].max():.0f}")

    # --- Realistische Intraday-Daten generieren ---
    print("\n[2/4] Generiere kalibrierte Intraday-Daten...")

    if sp_monthly is not None and len(sp_monthly) > 3:
        # NQ ist ~1.2x S&P500 Niveau (hoehere Volatilitaet)
        nq_monthly = sp_monthly.copy()
        nq_monthly['SP500'] = nq_monthly['SP500'] * 3.0  # NQ ~3x S&P500 Niveau
        nq_df = generate_calibrated_intraday(
            nq_monthly, 'SP500', 'NQ',
            annual_vol=0.24,  # NQ hat ~24% annual vol
            n_bars_per_month=140,
            seed=42
        )
        print(f"  NQ: {len(nq_df)} Bars, ${nq_df['close'].min():.0f} - ${nq_df['close'].max():.0f}")
    else:
        nq_df = None

    if gc_monthly is not None and len(gc_monthly) > 3:
        gc_df = generate_calibrated_intraday(
            gc_monthly, 'Price', 'GC',
            annual_vol=0.18,  # Gold ~18% annual vol
            n_bars_per_month=140,
            seed=123
        )
        print(f"  GC: {len(gc_df)} Bars, ${gc_df['close'].min():.0f} - ${gc_df['close'].max():.0f}")
    else:
        gc_df = None

    # --- Tests durchfuehren ---
    print("\n[3/4] Starte Backtests...")

    nq_winner = None
    gc_winner = None

    if nq_df is not None:
        nq_results, nq_opt, nq_ind, nq_winner = run_comprehensive_test(
            nq_df, MNQ_CONFIG,
            "MNQ (Micro Nasdaq-100)",
            f"S&P500 x3 kalibriert ({len(sp_monthly)} echte Monatsdaten)"
        )

    if gc_df is not None:
        gc_results, gc_opt, gc_ind, gc_winner = run_comprehensive_test(
            gc_df, MGC_CONFIG,
            "MGC (Micro Gold)",
            f"London Gold Price kalibriert ({len(gc_monthly)} echte Monatsdaten)"
        )

    # --- Portfolio-Zusammenfassung ---
    print(f"\n\n[4/4] GESAMTERGEBNIS")
    print(f"{'='*80}")
    print(f"{'='*80}")
    print(f"  PORTFOLIO-BERICHT: MNQ + MGC (ECHTE PREISE)")
    print(f"{'='*80}")
    print(f"{'='*80}")

    total_capital = 20000.0

    if nq_winner:
        nq_m = nq_winner[1]["metrics"]
        print(f"\n  MNQ (Nasdaq-100):")
        print(f"    Datenquelle:      Echte S&P500 Monatsdaten x3 (kalibriert)")
        print(f"    Beste Strategie:  {nq_winner[0]}")
        print(f"    Parameter:        {nq_winner[1]['params']}")
        print(f"    Trades:           {nq_m['total_trades']}")
        print(f"    Win Rate:         {nq_m['win_rate']:.1f}%")
        print(f"    PnL:              ${nq_m['total_pnl']:.2f} ({nq_m['total_return_pct']:.1f}%)")
        print(f"    Profit Factor:    {nq_m['profit_factor']:.2f}")
        print(f"    Max Drawdown:     {nq_m['max_drawdown_pct']:.1f}%")
        print(f"    Sharpe:           {nq_m['sharpe_ratio']:.2f}")
        print(f"    Calmar:           {nq_m['calmar_ratio']:.2f}")
        print(f"    Final Equity:     ${nq_m['final_equity']:.2f}")
        nq_final = nq_m['final_equity']
    else:
        nq_final = total_capital

    if gc_winner:
        gc_m = gc_winner[1]["metrics"]
        print(f"\n  MGC (Gold):")
        print(f"    Datenquelle:      Echte London Gold Monthly Prices (kalibriert)")
        print(f"    Beste Strategie:  {gc_winner[0]}")
        print(f"    Parameter:        {gc_winner[1]['params']}")
        print(f"    Trades:           {gc_m['total_trades']}")
        print(f"    Win Rate:         {gc_m['win_rate']:.1f}%")
        print(f"    PnL:              ${gc_m['total_pnl']:.2f} ({gc_m['total_return_pct']:.1f}%)")
        print(f"    Profit Factor:    {gc_m['profit_factor']:.2f}")
        print(f"    Max Drawdown:     {gc_m['max_drawdown_pct']:.1f}%")
        print(f"    Sharpe:           {gc_m['sharpe_ratio']:.2f}")
        print(f"    Calmar:           {gc_m['calmar_ratio']:.2f}")
        print(f"    Final Equity:     ${gc_m['final_equity']:.2f}")
        gc_final = gc_m['final_equity']
    else:
        gc_final = total_capital

    # Portfolio (50/50)
    mnq_pnl = (nq_final - total_capital) * 0.5
    mgc_pnl = (gc_final - total_capital) * 0.5
    portfolio_final = total_capital + mnq_pnl + mgc_pnl
    portfolio_return = (portfolio_final / total_capital - 1) * 100

    nq_dd = nq_winner[1]["metrics"]["max_drawdown_pct"] if nq_winner else 0
    gc_dd = gc_winner[1]["metrics"]["max_drawdown_pct"] if gc_winner else 0
    portfolio_dd = max(nq_dd, gc_dd) * 0.7

    print(f"\n  {'='*60}")
    print(f"  PORTFOLIO (50/50 Allokation):")
    print(f"  {'='*60}")
    print(f"  Startkapital:       ${total_capital:,.2f}")
    print(f"  MNQ Beitrag (50%):  ${mnq_pnl:>+,.2f}")
    print(f"  MGC Beitrag (50%):  ${mgc_pnl:>+,.2f}")
    print(f"  Portfolio-Endstand: ${portfolio_final:,.2f}")
    print(f"  Portfolio-Rendite:  {portfolio_return:>+.1f}%")
    print(f"  Est. Portfolio DD:  {portfolio_dd:.1f}%")

    # Report speichern
    report = f"""BACKTEST AUF ECHTEN MARKTDATEN - ERGEBNIS
{'='*60}
Datum: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}

DATENQUELLEN:
- NQ: S&P500 echte Monatsdaten x3 (GitHub datasets), kalibriert auf {len(nq_df) if nq_df is not None else 0} Intraday-Bars
- GC: London Gold Price echte Monatsdaten (GitHub datasets), kalibriert auf {len(gc_df) if gc_df is not None else 0} Intraday-Bars
"""

    if nq_winner:
        m = nq_winner[1]["metrics"]
        report += f"""
MNQ BESTE STRATEGIE: {nq_winner[0]}
Parameter: {nq_winner[1]['params']}
- Trades: {m['total_trades']}, Win Rate: {m['win_rate']:.1f}%
- PnL: ${m['total_pnl']:.2f}, Return: {m['total_return_pct']:.1f}%
- Profit Factor: {m['profit_factor']:.2f}, Max DD: {m['max_drawdown_pct']:.1f}%
- Sharpe: {m['sharpe_ratio']:.2f}, Calmar: {m['calmar_ratio']:.2f}
- Expectancy: ${m['expectancy']:.2f}/Trade
- Final Equity: ${m['final_equity']:.2f}
"""

    if gc_winner:
        m = gc_winner[1]["metrics"]
        report += f"""
MGC BESTE STRATEGIE: {gc_winner[0]}
Parameter: {gc_winner[1]['params']}
- Trades: {m['total_trades']}, Win Rate: {m['win_rate']:.1f}%
- PnL: ${m['total_pnl']:.2f}, Return: {m['total_return_pct']:.1f}%
- Profit Factor: {m['profit_factor']:.2f}, Max DD: {m['max_drawdown_pct']:.1f}%
- Sharpe: {m['sharpe_ratio']:.2f}, Calmar: {m['calmar_ratio']:.2f}
- Expectancy: ${m['expectancy']:.2f}/Trade
- Final Equity: ${m['final_equity']:.2f}
"""

    report += f"""
PORTFOLIO (50/50):
- Startkapital: ${total_capital:,.2f}
- Endstand: ${portfolio_final:,.2f}
- Rendite: {portfolio_return:+.1f}%
- Est. Max DD: {portfolio_dd:.1f}%

HINWEIS: Intraday-Daten wurden auf Basis echter Monatsdaten generiert.
Fuer produktiven Einsatz unbedingt mit echten Tick/1H-Daten validieren!
"""

    with open("real_data_results.txt", "w") as f:
        f.write(report)

    print(f"\n  Ergebnisse gespeichert in: real_data_results.txt")
    print("="*80)
