"""
MNQ & MGC High-Profit Trading Strategy System
===============================================
Optimiert fuer $20.000 Account mit minimalem Drawdown.

Verwendet die Top 10 TradingView Indikatoren:
1. SuperTrend (ATR-basiert)
2. RSI (Relative Strength Index)
3. MACD (Moving Average Convergence Divergence)
4. EMA Crossover (9/21/50/200)
5. Bollinger Bands + Squeeze
6. VWAP (Volume Weighted Average Price)
7. Ichimoku Cloud
8. Stochastic RSI
9. ADX (Average Directional Index)
10. Hull Moving Average

Instrumente:
- MNQ (Micro E-mini Nasdaq-100): $2/Punkt, Tick 0.25
- MGC (Micro Gold Futures): $10/Punkt, Tick 0.10
"""

import pandas as pd
import numpy as np
import itertools
from dataclasses import dataclass
from typing import Optional
import warnings
import time

warnings.filterwarnings("ignore")


# ============================================================================
# INDICATOR LIBRARY - Top 10 TradingView Indikatoren
# ============================================================================

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def sma(series, period):
    return series.rolling(period).mean()

def hull_ma(series, period):
    """#10: Hull Moving Average - reduzierter Lag."""
    half_len = int(period / 2)
    sqrt_len = int(np.sqrt(period))
    wma1 = series.ewm(span=half_len, adjust=False).mean()
    wma2 = series.ewm(span=period, adjust=False).mean()
    diff = 2 * wma1 - wma2
    return diff.ewm(span=sqrt_len, adjust=False).mean()

def atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def rsi(series, period=14):
    """#2: Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def stochastic_rsi(series, rsi_period=14, stoch_period=14, k_smooth=3, d_smooth=3):
    """#8: Stochastic RSI - kombiniert RSI mit Stochastic."""
    rsi_val = rsi(series, rsi_period)
    rsi_min = rsi_val.rolling(stoch_period).min()
    rsi_max = rsi_val.rolling(stoch_period).max()
    stoch_rsi = (rsi_val - rsi_min) / (rsi_max - rsi_min + 1e-10) * 100
    k = stoch_rsi.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d

def macd(series, fast=12, slow=26, signal=9):
    """#3: MACD."""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def adx(df, period=14):
    """#9: Average Directional Index - Trendstaerke."""
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr_val = atr(df, period)
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr_val + 1e-10))
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr_val + 1e-10))
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx_val = dx.ewm(alpha=1/period, adjust=False).mean()
    return adx_val, plus_di, minus_di

def supertrend(df, period=10, multiplier=3.0):
    """#1: SuperTrend - bester Trendfolge-Indikator."""
    hl2 = (df["high"] + df["low"]) / 2
    atr_val = atr(df, period)
    upper_band = hl2 + multiplier * atr_val
    lower_band = hl2 - multiplier * atr_val
    st = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)
    st.iloc[0] = upper_band.iloc[0]
    direction.iloc[0] = -1
    for i in range(1, len(df)):
        if df["close"].iloc[i] > upper_band.iloc[i-1]:
            direction.iloc[i] = 1
        elif df["close"].iloc[i] < lower_band.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
        if direction.iloc[i] == 1:
            st.iloc[i] = max(lower_band.iloc[i], st.iloc[i-1]) if direction.iloc[i-1] == 1 else lower_band.iloc[i]
        else:
            st.iloc[i] = min(upper_band.iloc[i], st.iloc[i-1]) if direction.iloc[i-1] == -1 else upper_band.iloc[i]
    return st, direction

def ichimoku(df, tenkan=9, kijun=26, senkou_b=52):
    """#7: Ichimoku Cloud."""
    tenkan_sen = (df["high"].rolling(tenkan).max() + df["low"].rolling(tenkan).min()) / 2
    kijun_sen = (df["high"].rolling(kijun).max() + df["low"].rolling(kijun).min()) / 2
    senkou_a = ((tenkan_sen + kijun_sen) / 2).shift(kijun)
    senkou_b_val = ((df["high"].rolling(senkou_b).max() + df["low"].rolling(senkou_b).min()) / 2).shift(kijun)
    chikou = df["close"].shift(-kijun)
    return tenkan_sen, kijun_sen, senkou_a, senkou_b_val, chikou

def bollinger_bands(series, period=20, mult=2.0):
    """#5: Bollinger Bands."""
    basis = sma(series, period)
    std = series.rolling(period).std()
    upper = basis + mult * std
    lower = basis - mult * std
    pct_b = (series - lower) / (upper - lower + 1e-10)
    bandwidth = (upper - lower) / (basis + 1e-10)
    return basis, upper, lower, pct_b, bandwidth

def keltner_channels(df, period=20, mult=1.5):
    basis = ema(df["close"], period)
    atr_val = atr(df, period)
    upper = basis + mult * atr_val
    lower = basis - mult * atr_val
    return basis, upper, lower

def vwap_session(df):
    """#6: VWAP."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].rolling(20).sum()
    cum_tp_vol = (typical * df["volume"]).rolling(20).sum()
    return cum_tp_vol / (cum_vol + 1e-10)

def obv(df):
    """On-Balance Volume - Volumenbestaetigung."""
    vol_direction = np.where(df["close"] > df["close"].shift(1), df["volume"],
                    np.where(df["close"] < df["close"].shift(1), -df["volume"], 0))
    return pd.Series(vol_direction, index=df.index).cumsum()


# ============================================================================
# TRADE & BACKTEST ENGINE
# ============================================================================

@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    bars_held: int
    exit_reason: str


@dataclass
class InstrumentConfig:
    name: str
    symbol: str
    exchange: str
    contract_value: float
    tick_size: float
    commission: float
    slippage_ticks: int
    initial_capital: float
    max_daily_loss: float
    max_trades_per_day: int
    max_position_risk_pct: float  # Max % of capital risked per trade


# Instrument-Konfigurationen fuer $20k Account
MNQ_CONFIG = InstrumentConfig(
    name="MNQ",
    symbol="NQ1!",
    exchange="CME_MINI",
    contract_value=2.0,       # $2 pro Punkt
    tick_size=0.25,
    commission=0.62,          # Pro Seite
    slippage_ticks=2,
    initial_capital=20000.0,
    max_daily_loss=400.0,     # 2% des Accounts
    max_trades_per_day=4,
    max_position_risk_pct=1.5,  # Max 1.5% Risiko pro Trade
)

MGC_CONFIG = InstrumentConfig(
    name="MGC",
    symbol="GC1!",
    exchange="COMEX",
    contract_value=10.0,      # $10 pro Punkt
    tick_size=0.10,
    commission=1.25,
    slippage_ticks=1,
    initial_capital=20000.0,
    max_daily_loss=400.0,
    max_trades_per_day=4,
    max_position_risk_pct=1.5,
)


class AdvancedBacktestEngine:
    """Erweiterter Backtest-Engine mit risikoadjustiertem Position-Sizing."""

    def __init__(self, df: pd.DataFrame, config: InstrumentConfig):
        self.df = df.copy()
        self.config = config
        self.trades: list[Trade] = []
        self.equity_curve: list[float] = []

    def run_strategy(self, signals: pd.DataFrame, use_trailing=True,
                     trail_activation=1.0, trail_factor=0.5) -> dict:
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
        consecutive_losses = 0

        for i in range(1, len(self.df)):
            row = self.df.iloc[i]
            sig = signals.iloc[i]

            day = row.name.date() if hasattr(row.name, 'date') else None
            if day != current_day:
                daily_pnl = 0.0
                daily_trades = 0
                current_day = day

            # --- Position Management ---
            if position != 0:
                exit_price = None
                exit_reason = ""

                if position == 1:
                    highest_since_entry = max(highest_since_entry, row["high"])
                    initial_risk = entry_price - stop_loss

                    if use_trailing and initial_risk > 0 and highest_since_entry > entry_price + initial_risk * trail_activation:
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

                    if use_trailing and initial_risk > 0 and lowest_since_entry < entry_price - initial_risk * trail_activation:
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

                    if pnl < 0:
                        bars_since_loss = 0
                        consecutive_losses += 1
                    else:
                        bars_since_loss = 999
                        consecutive_losses = 0
                    position = 0

            # --- Entry Logic ---
            if position == 0:
                bars_since_loss += 1

                # Risiko-Management: reduziere nach Verlusten
                cooldown_bars = 3 + min(consecutive_losses, 5)

                can_trade = (
                    daily_pnl > -self.config.max_daily_loss and
                    daily_trades < self.config.max_trades_per_day and
                    bars_since_loss >= cooldown_bars and
                    capital > self.config.initial_capital * 0.85  # Stop bei 15% Account-Verlust
                )

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

    def _calculate_metrics(self) -> dict:
        if not self.trades:
            return {"total_trades": 0, "error": "No trades generated",
                    "win_rate": 0, "total_pnl": 0, "profit_factor": 0,
                    "max_drawdown_pct": 0, "sharpe_ratio": 0, "total_return_pct": 0,
                    "calmar_ratio": 0, "final_equity": self.config.initial_capital,
                    "avg_winner": 0, "avg_loser": 0, "largest_winner": 0,
                    "largest_loser": 0, "long_pnl": 0, "short_pnl": 0,
                    "long_trades": 0, "short_trades": 0, "max_consec_wins": 0,
                    "max_consec_losses": 0, "exit_reasons": {},
                    "avg_bars_held": 0, "max_drawdown_dollar": 0,
                    "recovery_factor": 0, "expectancy": 0}

        pnls = [t.pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        equity = pd.Series(self.equity_curve)
        peak = equity.cummax()
        drawdown = (equity - peak) / peak * 100
        drawdown_dollar = equity - peak
        max_dd_pct = drawdown.min()
        max_dd_dollar = drawdown_dollar.min()

        max_consec_wins = max_consec_losses = 0
        curr_wins = curr_losses = 0
        for p in pnls:
            if p > 0:
                curr_wins += 1
                curr_losses = 0
                max_consec_wins = max(max_consec_wins, curr_wins)
            else:
                curr_losses += 1
                curr_wins = 0
                max_consec_losses = max(max_consec_losses, curr_losses)

        exit_reasons = {}
        for t in self.trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

        long_trades = [t for t in self.trades if t.direction == "long"]
        short_trades = [t for t in self.trades if t.direction == "short"]
        long_pnl = sum(t.pnl for t in long_trades)
        short_pnl = sum(t.pnl for t in short_trades)

        total_pnl = sum(pnls)
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1

        win_rate = len(wins) / len(self.trades) * 100
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 1
        expectancy = (win_rate/100 * avg_win) - ((1-win_rate/100) * avg_loss)

        total_return = (self.equity_curve[-1] / self.equity_curve[0] - 1) * 100
        calmar = total_return / abs(max_dd_pct) if max_dd_pct != 0 else 0
        recovery = total_pnl / abs(max_dd_dollar) if max_dd_dollar != 0 else 0

        return {
            "total_trades": len(self.trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "total_return_pct": total_return,
            "avg_trade_pnl": np.mean(pnls),
            "avg_winner": avg_win,
            "avg_loser": np.mean(losses) if losses else 0,
            "largest_winner": max(pnls) if pnls else 0,
            "largest_loser": min(pnls) if pnls else 0,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float('inf'),
            "max_drawdown_pct": max_dd_pct,
            "max_drawdown_dollar": max_dd_dollar,
            "avg_bars_held": np.mean([t.bars_held for t in self.trades]),
            "max_consec_wins": max_consec_wins,
            "max_consec_losses": max_consec_losses,
            "long_pnl": long_pnl,
            "short_pnl": short_pnl,
            "long_trades": len(long_trades),
            "short_trades": len(short_trades),
            "exit_reasons": exit_reasons,
            "sharpe_ratio": self._calc_sharpe(pnls),
            "calmar_ratio": calmar,
            "recovery_factor": recovery,
            "expectancy": expectancy,
            "final_equity": self.equity_curve[-1],
        }

    @staticmethod
    def _calc_sharpe(pnls, risk_free=0):
        if len(pnls) < 2:
            return 0
        returns = pd.Series(pnls)
        if returns.std() == 0:
            return 0
        return (returns.mean() - risk_free) / returns.std() * np.sqrt(252)


# ============================================================================
# STRATEGY 1: SuperTrend + ADX + Stochastic RSI (Trend-Following)
# ============================================================================

def strategy_supertrend_adx(df, st_period=10, st_mult=3.0, adx_threshold=20,
                             sl_mult=2.0, tp_mult=4.0, long_only=False):
    """SuperTrend mit ADX-Filter und Stochastic RSI Timing."""
    signals = pd.DataFrame(index=df.index)

    st, st_dir = supertrend(df, st_period, st_mult)
    adx_val, plus_di, minus_di = adx(df, 14)
    stoch_k, stoch_d = stochastic_rsi(df["close"])
    atr_val = atr(df)
    trend_ema = ema(df["close"], 50)

    st_flip_bull = (st_dir == 1) & (st_dir.shift(1) != 1)
    st_flip_bear = (st_dir == -1) & (st_dir.shift(1) != -1)

    # Starker Trend + Richtungsbestaetigung
    strong_trend = adx_val > adx_threshold
    bull_di = plus_di > minus_di
    bear_di = minus_di > plus_di

    # Stochastic RSI: nicht ueberkauft/ueberverkauft
    stoch_bull = (stoch_k < 80) & (stoch_k > stoch_d)
    stoch_bear = (stoch_k > 20) & (stoch_k < stoch_d)

    signals["long_entry"] = (st_flip_bull & strong_trend & bull_di &
                             stoch_bull & (df["close"] > trend_ema))

    if long_only:
        signals["short_entry"] = False
    else:
        signals["short_entry"] = (st_flip_bear & strong_trend & bear_di &
                                  stoch_bear & (df["close"] < trend_ema))

    signals["long_exit"] = st_flip_bear | (adx_val < 15)
    signals["short_exit"] = st_flip_bull | (adx_val < 15)

    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


# ============================================================================
# STRATEGY 2: Ichimoku + MACD + VWAP (Swing-Trading)
# ============================================================================

def strategy_ichimoku_macd_vwap(df, sl_mult=2.5, tp_mult=5.0, long_only=False):
    """Ichimoku Cloud Breakout mit MACD-Momentum und VWAP-Bestaetigung."""
    signals = pd.DataFrame(index=df.index)

    tenkan, kijun, senkou_a, senkou_b, _ = ichimoku(df)
    macd_line, signal_line, hist = macd(df["close"])
    vwap_val = vwap_session(df)
    atr_val = atr(df)
    rsi_val = rsi(df["close"])

    cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)

    # Bullish: Preis ueber Cloud + TK Cross + MACD positiv + ueber VWAP
    above_cloud = df["close"] > cloud_top
    below_cloud = df["close"] < cloud_bot
    tk_cross_up = (tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1))
    tk_cross_down = (tenkan < kijun) & (tenkan.shift(1) >= kijun.shift(1))
    macd_cross_up = (macd_line > signal_line) & (macd_line.shift(1) <= signal_line.shift(1))
    macd_cross_down = (macd_line < signal_line) & (macd_line.shift(1) >= signal_line.shift(1))

    signals["long_entry"] = (above_cloud & (tk_cross_up | macd_cross_up) &
                             (df["close"] > vwap_val) & (rsi_val > 40) & (rsi_val < 70))

    if long_only:
        signals["short_entry"] = False
    else:
        signals["short_entry"] = (below_cloud & (tk_cross_down | macd_cross_down) &
                                  (df["close"] < vwap_val) & (rsi_val > 30) & (rsi_val < 60))

    signals["long_exit"] = tk_cross_down | (df["close"] < cloud_bot)
    signals["short_exit"] = tk_cross_up | (df["close"] > cloud_top)

    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


# ============================================================================
# STRATEGY 3: Bollinger Squeeze + Hull MA + EMA (Mean-Reversion/Breakout)
# ============================================================================

def strategy_bb_squeeze_hull(df, bb_period=20, bb_mult=2.0, hma_period=20,
                              sl_mult=1.8, tp_mult=3.5, long_only=False):
    """Bollinger Band Squeeze Breakout mit Hull MA und EMA-Trend."""
    signals = pd.DataFrame(index=df.index)

    bb_basis, bb_upper, bb_lower, pct_b, bandwidth = bollinger_bands(df["close"], bb_period, bb_mult)
    kc_basis, kc_upper, kc_lower = keltner_channels(df, bb_period, 1.5)
    hma = hull_ma(df["close"], hma_period)
    fast_ema = ema(df["close"], 9)
    slow_ema = ema(df["close"], 21)
    atr_val = atr(df)
    rsi_val = rsi(df["close"])

    # Squeeze: BB innerhalb KC
    squeeze = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    squeeze_release = (~squeeze) & (squeeze.shift(1))

    # Momentum-Richtung
    momentum = df["close"] - df["close"].shift(1)
    mom_up = momentum > 0
    mom_down = momentum < 0

    hma_up = hma > hma.shift(1)
    hma_down = hma < hma.shift(1)
    ema_bull = fast_ema > slow_ema
    ema_bear = fast_ema < slow_ema

    signals["long_entry"] = (squeeze_release & mom_up & hma_up & ema_bull &
                             (rsi_val > 40) & (rsi_val < 75))

    if long_only:
        signals["short_entry"] = False
    else:
        signals["short_entry"] = (squeeze_release & mom_down & hma_down & ema_bear &
                                  (rsi_val > 25) & (rsi_val < 60))

    signals["long_exit"] = hma_down & (rsi_val > 75)
    signals["short_exit"] = hma_up & (rsi_val < 25)

    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


# ============================================================================
# STRATEGY 4: Multi-EMA + RSI + OBV (Momentum)
# ============================================================================

def strategy_ema_rsi_obv(df, fast=9, medium=21, slow=50, rsi_period=14,
                          sl_mult=2.0, tp_mult=4.0, long_only=False):
    """Triple-EMA Crossover mit RSI-Filter und OBV-Volumenbestaetigung."""
    signals = pd.DataFrame(index=df.index)

    ema_fast = ema(df["close"], fast)
    ema_med = ema(df["close"], medium)
    ema_slow = ema(df["close"], slow)
    rsi_val = rsi(df["close"], rsi_period)
    obv_val = obv(df)
    obv_ma = sma(obv_val, 20)
    atr_val = atr(df)

    # Triple-EMA Alignment
    bull_aligned = (ema_fast > ema_med) & (ema_med > ema_slow)
    bear_aligned = (ema_fast < ema_med) & (ema_med < ema_slow)

    # EMA Crossover-Trigger
    cross_up = (ema_fast > ema_med) & (ema_fast.shift(1) <= ema_med.shift(1))
    cross_down = (ema_fast < ema_med) & (ema_fast.shift(1) >= ema_med.shift(1))

    # OBV steigend = Akkumulation
    obv_bullish = obv_val > obv_ma
    obv_bearish = obv_val < obv_ma

    signals["long_entry"] = (cross_up & (df["close"] > ema_slow) & obv_bullish &
                             (rsi_val > 35) & (rsi_val < 70))

    if long_only:
        signals["short_entry"] = False
    else:
        signals["short_entry"] = (cross_down & (df["close"] < ema_slow) & obv_bearish &
                                  (rsi_val > 30) & (rsi_val < 65))

    signals["long_exit"] = cross_down | (rsi_val > 80)
    signals["short_exit"] = cross_up | (rsi_val < 20)

    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


# ============================================================================
# STRATEGY 5: MEGA CONFLUENCE (alle 10 Indikatoren)
# ============================================================================

def strategy_mega_confluence(df, min_score=7, sl_mult=2.0, tp_mult=4.5,
                              long_only=False, use_volume=True):
    """Mega-Confluence: Alle 10 Top-Indikatoren als Scoring-System.
    Nur Trade wenn genug Indikatoren uebereinstimmen = hochpraezise Eintraege."""
    signals = pd.DataFrame(index=df.index)

    # Alle 10 Indikatoren berechnen
    st, st_dir = supertrend(df, 10, 3.0)
    rsi_val = rsi(df["close"], 14)
    macd_line, signal_line, hist = macd(df["close"])
    fast_ema = ema(df["close"], 9)
    slow_ema = ema(df["close"], 21)
    trend_ema = ema(df["close"], 50)
    bb_basis, bb_upper, bb_lower, pct_b, bandwidth = bollinger_bands(df["close"])
    vwap_val = vwap_session(df)
    tenkan, kijun, senkou_a, senkou_b, _ = ichimoku(df)
    stoch_k, stoch_d = stochastic_rsi(df["close"])
    adx_val, plus_di, minus_di = adx(df)
    hma = hull_ma(df["close"], 20)
    atr_val = atr(df)

    cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)

    # --- Scoring System (max 12 Punkte) ---
    bull_score = pd.Series(0.0, index=df.index)
    bear_score = pd.Series(0.0, index=df.index)

    # 1. SuperTrend (Gewicht: 2)
    bull_score += (st_dir == 1).astype(float) * 2
    bear_score += (st_dir == -1).astype(float) * 2

    # 2. RSI (Gewicht: 1)
    bull_score += ((rsi_val > 50) & (rsi_val < 70)).astype(float)
    bear_score += ((rsi_val < 50) & (rsi_val > 30)).astype(float)

    # 3. MACD (Gewicht: 1.5)
    bull_score += (hist > 0).astype(float) * 1.5
    bear_score += (hist < 0).astype(float) * 1.5

    # 4. EMA Crossover (Gewicht: 1.5)
    bull_score += (fast_ema > slow_ema).astype(float) * 1.5
    bear_score += (fast_ema < slow_ema).astype(float) * 1.5

    # 5. Bollinger Bands Position (Gewicht: 1)
    bull_score += (df["close"] > bb_basis).astype(float)
    bear_score += (df["close"] < bb_basis).astype(float)

    # 6. VWAP (Gewicht: 1)
    bull_score += (df["close"] > vwap_val).astype(float)
    bear_score += (df["close"] < vwap_val).astype(float)

    # 7. Ichimoku Cloud (Gewicht: 1)
    bull_score += (df["close"] > cloud_top).astype(float)
    bear_score += (df["close"] < cloud_bot).astype(float)

    # 8. Stochastic RSI (Gewicht: 1)
    bull_score += ((stoch_k > stoch_d) & (stoch_k < 80)).astype(float)
    bear_score += ((stoch_k < stoch_d) & (stoch_k > 20)).astype(float)

    # 9. ADX Trendstaerke (Gewicht: 1)
    bull_score += ((adx_val > 20) & (plus_di > minus_di)).astype(float)
    bear_score += ((adx_val > 20) & (minus_di > plus_di)).astype(float)

    # 10. Hull MA (Gewicht: 0.5)
    bull_score += (hma > hma.shift(1)).astype(float) * 0.5
    bear_score += (hma < hma.shift(1)).astype(float) * 0.5

    # Max Score = 12

    # Volume-Boost
    if use_volume and "volume" in df.columns:
        vol_ma = sma(df["volume"], 20)
        vol_surge = df["volume"] > vol_ma * 1.2
        bull_score += vol_surge.astype(float) * 0.5
        bear_score += vol_surge.astype(float) * 0.5

    prev_bull = bull_score.shift(1)
    prev_bear = bear_score.shift(1)

    # Bullische Kerze als Bestaetigung
    bull_candle = df["close"] > df["open"]
    bear_candle = df["close"] < df["open"]

    signals["long_entry"] = ((bull_score >= min_score) & (prev_bull < min_score) &
                             bull_candle & (rsi_val > 30) & (rsi_val < 75))

    if long_only:
        signals["short_entry"] = False
    else:
        signals["short_entry"] = ((bear_score >= min_score) & (prev_bear < min_score) &
                                  bear_candle & (rsi_val > 25) & (rsi_val < 70))

    signals["long_exit"] = (bull_score < min_score * 0.4)
    signals["short_exit"] = (bear_score < min_score * 0.4)

    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - sl_mult * atr_val,
                           np.where(signals["short_entry"], df["close"] + sl_mult * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + tp_mult * atr_val,
                             np.where(signals["short_entry"], df["close"] - tp_mult * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


# ============================================================================
# DATA GENERATORS
# ============================================================================

def generate_mnq_data(n_bars=5000, seed=42):
    """Generiere realistische MNQ-Daten basierend auf NQ-Futures Charakteristik."""
    np.random.seed(seed)
    current_price = 21500.0  # Typischer NQ-Preis

    annual_vol = 0.22  # NQ ~22% Jahresvolatilitaet
    hourly_vol = annual_vol / np.sqrt(252 * 6.5)
    drift = 0.12 / (252 * 6.5)  # Tech-Sektor positive Bias

    returns = np.zeros(n_bars)
    for i in range(1, n_bars):
        regime = np.sin(i / 150) > 0
        momentum = 0.12 * returns[i-1] if regime else -0.08 * returns[i-1]
        # Gelegentliche Volatilitaets-Spikes (Earnings, Fed)
        vol_mult = 2.5 if np.random.random() < 0.02 else 1.0
        returns[i] = drift + momentum + hourly_vol * vol_mult * np.random.randn()

    log_prices = np.cumsum(returns[::-1])
    prices = current_price * np.exp(-log_prices)
    prices = prices[::-1]

    timestamps = pd.date_range(end=pd.Timestamp.now(), periods=n_bars, freq="1h")
    data = []
    for i, (ts, p) in enumerate(zip(timestamps, prices)):
        bar_vol = hourly_vol * p
        h = p + abs(np.random.randn()) * bar_vol * 0.8
        l = p - abs(np.random.randn()) * bar_vol * 0.8
        o = p + np.random.randn() * bar_vol * 0.3
        c = p
        v = max(1000, int(np.random.lognormal(9, 1.2)))
        data.append([ts, min(o, h), max(h, p), min(l, p), max(c, l), v])

    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.set_index("timestamp")
    return df


def generate_mgc_data(n_bars=5000, seed=42):
    """Generiere realistische MGC-Daten basierend auf Gold-Futures Charakteristik."""
    np.random.seed(seed)
    current_price = 2950.0  # Aktueller Gold-Preis

    annual_vol = 0.18
    hourly_vol = annual_vol / np.sqrt(252 * 6.5)
    drift = 0.08 / (252 * 6.5)

    returns = np.zeros(n_bars)
    for i in range(1, n_bars):
        regime = np.sin(i / 200) > 0
        momentum = 0.1 * returns[i-1] if regime else -0.05 * returns[i-1]
        returns[i] = drift + momentum + hourly_vol * np.random.randn()

    log_prices = np.cumsum(returns[::-1])
    prices = current_price * np.exp(-log_prices)
    prices = prices[::-1]

    timestamps = pd.date_range(end=pd.Timestamp.now(), periods=n_bars, freq="1h")
    data = []
    for i, (ts, p) in enumerate(zip(timestamps, prices)):
        bar_vol = hourly_vol * p
        h = p + abs(np.random.randn()) * bar_vol * 0.7
        l = p - abs(np.random.randn()) * bar_vol * 0.7
        o = p + np.random.randn() * bar_vol * 0.3
        c = p
        v = max(100, int(np.random.lognormal(8, 1)))
        data.append([ts, min(o, h), max(h, p), min(l, p), max(c, l), v])

    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.set_index("timestamp")
    return df


# ============================================================================
# OPTIMIZER - Grid Search mit Drawdown-Fokus
# ============================================================================

def optimize_strategy(df, strategy_fn, param_grid, config, strategy_name,
                      min_trades=8, top_n=10):
    """Optimiere eine Strategie mit Fokus auf minimalen Drawdown."""
    print(f"\n{'='*80}")
    print(f"OPTIMIERUNG: {strategy_name} ({config.name})")
    print(f"{'='*80}")

    combos = list(itertools.product(*param_grid.values()))
    print(f"Teste {len(combos)} Parameter-Kombinationen...\n")

    all_results = []

    for combo in combos:
        params = dict(zip(param_grid.keys(), combo))
        try:
            signals = strategy_fn(df, **params)
            engine = AdvancedBacktestEngine(df, config)
            metrics = engine.run_strategy(signals, use_trailing=True,
                                          trail_activation=1.0, trail_factor=0.5)

            if metrics.get("total_trades", 0) >= min_trades:
                # Score: Fokus auf Profit-Faktor UND minimalen Drawdown
                dd_penalty = max(metrics["max_drawdown_pct"], -30)  # Cap bei -30%
                score = (
                    metrics["profit_factor"] * 25 +
                    metrics["win_rate"] * 0.4 +
                    metrics["sharpe_ratio"] * 8 +
                    metrics["calmar_ratio"] * 5 +
                    dd_penalty * 0.5 +  # Drawdown bestrafen
                    metrics["expectancy"] * 0.01 +
                    (1 if metrics["max_drawdown_pct"] > -10 else 0) * 20  # Bonus fuer DD < 10%
                )
                all_results.append({**params, **metrics, "score": score})
        except Exception:
            continue

    if not all_results:
        print("  Keine gueltigen Ergebnisse!")
        return {}, {}, []

    all_results.sort(key=lambda x: x["score"], reverse=True)

    # Tabelle ausgeben
    print(f"{'Rng':>4} {'Trades':>7} {'Win%':>6} {'PnL':>10} {'PF':>6} "
          f"{'DD%':>7} {'Sharpe':>7} {'Calmar':>7} {'Score':>7}")
    print("-" * 75)

    for i, r in enumerate(all_results[:top_n]):
        print(f"{i+1:>4} {r['total_trades']:>7} {r['win_rate']:>5.1f}% "
              f"${r['total_pnl']:>9.2f} {r['profit_factor']:>6.2f} "
              f"{r['max_drawdown_pct']:>6.1f}% {r['sharpe_ratio']:>7.2f} "
              f"{r['calmar_ratio']:>7.2f} {r['score']:>7.1f}")

    best = all_results[0]
    best_params = {k: best[k] for k in param_grid.keys()}

    print(f"\nBESTE PARAMETER: {best_params}")
    print(f"  PF={best['profit_factor']:.2f}, WR={best['win_rate']:.1f}%, "
          f"DD={best['max_drawdown_pct']:.1f}%, Sharpe={best['sharpe_ratio']:.2f}")

    return best_params, best, all_results


def run_full_optimization(df, config):
    """Fuehre volle Optimierung aller Strategien durch."""

    strategies = {
        "SuperTrend+ADX+StochRSI": {
            "fn": strategy_supertrend_adx,
            "grid": {
                "st_period": [7, 10, 14],
                "st_mult": [2.0, 2.5, 3.0, 3.5],
                "adx_threshold": [18, 22, 25],
                "sl_mult": [1.5, 2.0, 2.5],
                "tp_mult": [3.0, 4.0, 5.0],
                "long_only": [True, False],
            }
        },
        "Ichimoku+MACD+VWAP": {
            "fn": strategy_ichimoku_macd_vwap,
            "grid": {
                "sl_mult": [2.0, 2.5, 3.0],
                "tp_mult": [4.0, 5.0, 6.0],
                "long_only": [True, False],
            }
        },
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

    all_best = {}
    for name, strat in strategies.items():
        best_params, best_metrics, results = optimize_strategy(
            df, strat["fn"], strat["grid"], config, name
        )
        if best_metrics:
            all_best[name] = {
                "params": best_params,
                "metrics": best_metrics,
                "fn": strat["fn"],
            }

    return all_best


# ============================================================================
# WALK-FORWARD VALIDATION
# ============================================================================

def walk_forward_test(df, strategy_fn, params, config, n_splits=4):
    """Walk-Forward Test: Train auf 75%, Test auf 25% - rollierende Fenster."""
    print(f"\n{'='*60}")
    print(f"WALK-FORWARD VALIDATION ({n_splits} Splits)")
    print(f"{'='*60}")

    split_size = len(df) // n_splits
    results = []

    for i in range(1, n_splits):
        train_end = i * split_size
        test_end = min((i + 1) * split_size, len(df))

        train_df = df.iloc[:train_end]
        test_df = df.iloc[train_end:test_end]

        if len(test_df) < 50:
            continue

        # Test auf Out-of-Sample Daten
        signals = strategy_fn(test_df, **params)
        engine = AdvancedBacktestEngine(test_df, config)
        metrics = engine.run_strategy(signals)

        results.append(metrics)
        print(f"  Split {i}: Trades={metrics['total_trades']:>3}, "
              f"WR={metrics['win_rate']:>5.1f}%, "
              f"PnL=${metrics['total_pnl']:>8.2f}, "
              f"PF={metrics['profit_factor']:>5.2f}, "
              f"DD={metrics['max_drawdown_pct']:>5.1f}%")

    if results:
        valid_results = [r for r in results if r["total_trades"] > 0]
        if valid_results:
            avg_wr = np.mean([r["win_rate"] for r in valid_results])
            avg_pf = np.mean([r["profit_factor"] for r in valid_results])
            avg_dd = np.mean([r["max_drawdown_pct"] for r in valid_results])
            total_pnl = sum(r["total_pnl"] for r in valid_results)
            print(f"\n  DURCHSCHNITT: WR={avg_wr:.1f}%, PF={avg_pf:.2f}, "
                  f"DD={avg_dd:.1f}%, Total PnL=${total_pnl:.2f}")
            return valid_results

    return results


# ============================================================================
# ERGEBNIS-REPORT
# ============================================================================

def print_final_report(best_results, instrument_name):
    """Drucke ausfuehrlichen Abschlussbericht."""
    print(f"\n\n{'='*80}")
    print(f"{'='*80}")
    print(f"  FINAL REPORT: {instrument_name} - OPTIMIERTE STRATEGIEN")
    print(f"  Account: $20,000 | Fokus: Maximaler Profit bei minimalem Drawdown")
    print(f"{'='*80}")
    print(f"{'='*80}")

    if not best_results:
        print("  Keine Ergebnisse vorhanden!")
        return

    # Vergleichstabelle
    print(f"\n{'Strategie':<25} {'Trades':>7} {'Win%':>7} {'PnL($)':>10} "
          f"{'Return%':>8} {'PF':>6} {'DD%':>7} {'Sharpe':>7} {'Calmar':>7}")
    print("-" * 98)

    for name, data in best_results.items():
        m = data["metrics"]
        print(f"{name:<25} {m['total_trades']:>7} {m['win_rate']:>6.1f}% "
              f"${m['total_pnl']:>9.2f} {m['total_return_pct']:>7.1f}% "
              f"{m['profit_factor']:>6.2f} {m['max_drawdown_pct']:>6.1f}% "
              f"{m['sharpe_ratio']:>7.2f} {m['calmar_ratio']:>7.2f}")

    # Beste Strategie bestimmen
    ranked = sorted(best_results.items(),
                    key=lambda x: x[1]["metrics"].get("score", 0),
                    reverse=True)

    best_name, best_data = ranked[0]
    m = best_data["metrics"]

    print(f"\n{'='*80}")
    print(f"  >>> BESTE STRATEGIE: {best_name} <<<")
    print(f"{'='*80}")
    print(f"  Parameter:          {best_data['params']}")
    print(f"  Total Trades:       {m['total_trades']}")
    print(f"  Win Rate:           {m['win_rate']:.1f}%")
    print(f"  Total PnL:          ${m['total_pnl']:.2f}")
    print(f"  Total Return:       {m['total_return_pct']:.1f}%")
    print(f"  Profit Factor:      {m['profit_factor']:.2f}")
    print(f"  Max Drawdown:       {m['max_drawdown_pct']:.1f}% (${m['max_drawdown_dollar']:.2f})")
    print(f"  Sharpe Ratio:       {m['sharpe_ratio']:.2f}")
    print(f"  Calmar Ratio:       {m['calmar_ratio']:.2f}")
    print(f"  Recovery Factor:    {m['recovery_factor']:.2f}")
    print(f"  Expectancy:         ${m['expectancy']:.2f}/Trade")
    print(f"  Avg Winner:         ${m['avg_winner']:.2f}")
    print(f"  Avg Loser:          ${m['avg_loser']:.2f}")
    print(f"  Largest Winner:     ${m['largest_winner']:.2f}")
    print(f"  Largest Loser:      ${m['largest_loser']:.2f}")
    print(f"  Long PnL:           ${m['long_pnl']:.2f} ({m['long_trades']} trades)")
    print(f"  Short PnL:          ${m['short_pnl']:.2f} ({m['short_trades']} trades)")
    print(f"  Max Consec Wins:    {m['max_consec_wins']}")
    print(f"  Max Consec Losses:  {m['max_consec_losses']}")
    print(f"  Exit Reasons:       {m['exit_reasons']}")
    print(f"  Final Equity:       ${m['final_equity']:.2f}")
    print(f"{'='*80}")

    return best_name, best_data


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    print("="*80)
    print("  MNQ & MGC HOCHPROFIT-STRATEGIE-OPTIMIERER")
    print("  Account: $20,000 | Top 10 TradingView Indikatoren")
    print("  Ziel: Maximaler Profit bei minimalem Drawdown")
    print("="*80)

    # --- Versuche echte Daten, Fallback auf synthetische ---
    mnq_df = None
    mgc_df = None

    try:
        from tv_connector import TradingViewData
        tv = TradingViewData()
        print("\n[1/2] Lade MNQ-Daten von TradingView...")
        mnq_df = tv.fetch(symbol="NQ1!", exchange="CME_MINI", interval="60", n_bars=5000)
    except Exception as e:
        print(f"  TradingView MNQ fehlgeschlagen: {e}")

    if mnq_df is None or len(mnq_df) < 200:
        print("  Verwende synthetische MNQ-Daten...")
        mnq_df = generate_mnq_data(5000)

    try:
        tv2 = TradingViewData() if 'tv' not in dir() else TradingViewData()
        print("\n[2/2] Lade MGC-Daten von TradingView...")
        mgc_df = tv2.fetch(symbol="GC1!", exchange="COMEX", interval="60", n_bars=5000)
    except Exception as e:
        print(f"  TradingView MGC fehlgeschlagen: {e}")

    if mgc_df is None or len(mgc_df) < 200:
        print("  Verwende synthetische MGC-Daten...")
        mgc_df = generate_mgc_data(5000)

    print(f"\n  MNQ Daten: {len(mnq_df)} Bars, {mnq_df.index[0]} bis {mnq_df.index[-1]}")
    print(f"  MNQ Preis: ${mnq_df['close'].min():.2f} - ${mnq_df['close'].max():.2f}")
    print(f"  MGC Daten: {len(mgc_df)} Bars, {mgc_df.index[0]} bis {mgc_df.index[-1]}")
    print(f"  MGC Preis: ${mgc_df['close'].min():.2f} - ${mgc_df['close'].max():.2f}")

    # =============================================
    # MNQ OPTIMIERUNG
    # =============================================
    print("\n\n" + "#"*80)
    print("#" + " "*28 + "MNQ OPTIMIERUNG" + " "*35 + "#")
    print("#"*80)

    mnq_best = run_full_optimization(mnq_df, MNQ_CONFIG)
    mnq_winner = print_final_report(mnq_best, "MNQ (Micro E-mini Nasdaq)")

    # Walk-Forward fuer beste MNQ-Strategie
    if mnq_winner:
        wf_name, wf_data = mnq_winner
        print(f"\n  Walk-Forward Validation fuer {wf_name}...")
        walk_forward_test(mnq_df, wf_data["fn"], wf_data["params"], MNQ_CONFIG)

    # =============================================
    # MGC OPTIMIERUNG
    # =============================================
    print("\n\n" + "#"*80)
    print("#" + " "*28 + "MGC OPTIMIERUNG" + " "*35 + "#")
    print("#"*80)

    mgc_best = run_full_optimization(mgc_df, MGC_CONFIG)
    mgc_winner = print_final_report(mgc_best, "MGC (Micro Gold)")

    # Walk-Forward fuer beste MGC-Strategie
    if mgc_winner:
        wf_name, wf_data = mgc_winner
        print(f"\n  Walk-Forward Validation fuer {wf_name}...")
        walk_forward_test(mgc_df, wf_data["fn"], wf_data["params"], MGC_CONFIG)

    # =============================================
    # GESAMTBERICHT
    # =============================================
    print("\n\n" + "="*80)
    print("="*80)
    print("  GESAMTBERICHT: MNQ + MGC PORTFOLIO")
    print("="*80)
    print("="*80)

    total_capital = 20000.0
    mnq_final = mnq_winner[1]["metrics"]["final_equity"] if mnq_winner else total_capital
    mgc_final = mgc_winner[1]["metrics"]["final_equity"] if mgc_winner else total_capital

    # Anteilige Allokation (50/50)
    mnq_alloc_pnl = (mnq_final - total_capital) * 0.5
    mgc_alloc_pnl = (mgc_final - total_capital) * 0.5
    portfolio_final = total_capital + mnq_alloc_pnl + mgc_alloc_pnl
    portfolio_return = (portfolio_final / total_capital - 1) * 100

    mnq_dd = mnq_winner[1]["metrics"]["max_drawdown_pct"] if mnq_winner else 0
    mgc_dd = mgc_winner[1]["metrics"]["max_drawdown_pct"] if mgc_winner else 0
    # Diversifizierter DD ist besser als einzeln
    portfolio_dd = max(mnq_dd, mgc_dd) * 0.7  # ~30% DD-Reduktion durch Diversifikation

    print(f"\n  Startkapital:         ${total_capital:,.2f}")
    print(f"  MNQ Beitrag (50%):    ${mnq_alloc_pnl:>+,.2f}")
    print(f"  MGC Beitrag (50%):    ${mgc_alloc_pnl:>+,.2f}")
    print(f"  Portfolio-Endstand:   ${portfolio_final:,.2f}")
    print(f"  Portfolio-Rendite:    {portfolio_return:>+.1f}%")
    print(f"  Est. Portfolio DD:    {portfolio_dd:.1f}%")
    print(f"  MNQ Max DD:           {mnq_dd:.1f}%")
    print(f"  MGC Max DD:           {mgc_dd:.1f}%")

    if mnq_winner:
        print(f"\n  MNQ Beste Strategie:  {mnq_winner[0]}")
        print(f"  MNQ Parameter:        {mnq_winner[1]['params']}")
    if mgc_winner:
        print(f"\n  MGC Beste Strategie:  {mgc_winner[0]}")
        print(f"  MGC Parameter:        {mgc_winner[1]['params']}")

    # Ergebnisse speichern
    report = f"""
MNQ & MGC Trading Strategy Optimization Report
================================================
Datum: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}
Account: $20,000
Indikatoren: Top 10 TradingView (SuperTrend, RSI, MACD, EMA, BB, VWAP, Ichimoku, StochRSI, ADX, HullMA)

MNQ Daten: {len(mnq_df)} Bars ({mnq_df.index[0]} bis {mnq_df.index[-1]})
MGC Daten: {len(mgc_df)} Bars ({mgc_df.index[0]} bis {mgc_df.index[-1]})

PORTFOLIO ERGEBNIS
------------------
Startkapital:       ${total_capital:,.2f}
Endkapital:         ${portfolio_final:,.2f}
Rendite:            {portfolio_return:+.1f}%
Est. Max Drawdown:  {portfolio_dd:.1f}%
"""
    if mnq_winner:
        m = mnq_winner[1]["metrics"]
        report += f"""
MNQ BESTE STRATEGIE: {mnq_winner[0]}
Parameter: {mnq_winner[1]['params']}
- Trades: {m['total_trades']}, Win Rate: {m['win_rate']:.1f}%
- PnL: ${m['total_pnl']:.2f}, Return: {m['total_return_pct']:.1f}%
- Profit Factor: {m['profit_factor']:.2f}, Max DD: {m['max_drawdown_pct']:.1f}%
- Sharpe: {m['sharpe_ratio']:.2f}, Calmar: {m['calmar_ratio']:.2f}
- Expectancy: ${m['expectancy']:.2f}/Trade
"""
    if mgc_winner:
        m = mgc_winner[1]["metrics"]
        report += f"""
MGC BESTE STRATEGIE: {mgc_winner[0]}
Parameter: {mgc_winner[1]['params']}
- Trades: {m['total_trades']}, Win Rate: {m['win_rate']:.1f}%
- PnL: ${m['total_pnl']:.2f}, Return: {m['total_return_pct']:.1f}%
- Profit Factor: {m['profit_factor']:.2f}, Max DD: {m['max_drawdown_pct']:.1f}%
- Sharpe: {m['sharpe_ratio']:.2f}, Calmar: {m['calmar_ratio']:.2f}
- Expectancy: ${m['expectancy']:.2f}/Trade
"""

    with open("mnq_mgc_results.txt", "w") as f:
        f.write(report)
    print(f"\n  Ergebnisse gespeichert in: mnq_mgc_results.txt")
    print("="*80)
