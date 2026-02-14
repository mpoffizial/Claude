"""
MGC1 Multi-Strategy Backtesting Engine
Tests multiple strategy variants using real TradingView data and finds the optimal combination.

Based on research of the most profitable TradingView indicators for gold:
- SuperTrend (ATR-based trend following)
- VWAP (institutional flow)
- Ichimoku Cloud (multi-factor trend)
- Hull Moving Average (low-lag trend)
- EMA Crossover with ATR trailing stop
- Bollinger Band Squeeze + Keltner Channel breakout
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
import warnings
warnings.filterwarnings("ignore")


# ============================================================================
# INDICATOR LIBRARY
# ============================================================================

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def sma(series, period):
    return series.rolling(period).mean()

def hull_ma(series, period):
    """Hull Moving Average - reduces lag significantly."""
    half_len = int(period / 2)
    sqrt_len = int(np.sqrt(period))
    wma1 = series.ewm(span=half_len, adjust=False).mean()
    wma2 = series.ewm(span=period, adjust=False).mean()
    diff = 2 * wma1 - wma2
    return diff.ewm(span=sqrt_len, adjust=False).mean()

def atr(df, period=14):
    """Average True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def rsi(series, period=14):
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    """MACD indicator."""
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def supertrend(df, period=10, multiplier=3.0):
    """SuperTrend indicator - best trend following indicator for gold."""
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
    """Ichimoku Cloud indicator."""
    tenkan_sen = (df["high"].rolling(tenkan).max() + df["low"].rolling(tenkan).min()) / 2
    kijun_sen = (df["high"].rolling(kijun).max() + df["low"].rolling(kijun).min()) / 2
    senkou_a = ((tenkan_sen + kijun_sen) / 2).shift(kijun)
    senkou_b_val = ((df["high"].rolling(senkou_b).max() + df["low"].rolling(senkou_b).min()) / 2).shift(kijun)
    chikou = df["close"].shift(-kijun)
    return tenkan_sen, kijun_sen, senkou_a, senkou_b_val, chikou

def bollinger_bands(series, period=20, mult=2.0):
    """Bollinger Bands."""
    basis = sma(series, period)
    std = series.rolling(period).std()
    upper = basis + mult * std
    lower = basis - mult * std
    return basis, upper, lower

def keltner_channels(df, period=20, mult=1.5):
    """Keltner Channels."""
    basis = ema(df["close"], period)
    atr_val = atr(df, period)
    upper = basis + mult * atr_val
    lower = basis - mult * atr_val
    return basis, upper, lower

def vwap_session(df):
    """Simple cumulative VWAP (resets not implemented for simplicity - uses rolling)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].rolling(20).sum()
    cum_tp_vol = (typical * df["volume"]).rolling(20).sum()
    return cum_tp_vol / cum_vol


# ============================================================================
# TRADE RESULT
# ============================================================================

@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str  # "long" or "short"
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    bars_held: int
    exit_reason: str


# ============================================================================
# BACKTEST ENGINE
# ============================================================================

@dataclass
class BacktestConfig:
    initial_capital: float = 10000.0
    contract_value: float = 10.0       # MGC: $10 per point
    commission: float = 1.25           # Per side
    slippage_ticks: int = 1
    tick_size: float = 0.10
    max_trades_per_day: int = 3
    max_daily_loss: float = 200.0


class BacktestEngine:
    def __init__(self, df: pd.DataFrame, config: BacktestConfig = None):
        self.df = df.copy()
        self.config = config or BacktestConfig()
        self.trades: list[Trade] = []
        self.equity_curve: list[float] = []

    def run_strategy(self, signals: pd.DataFrame) -> dict:
        """
        Run backtest on signal DataFrame.
        signals must have columns: 'long_entry', 'short_entry', 'long_exit', 'short_exit',
                                    'stop_loss', 'take_profit'
        """
        capital = self.config.initial_capital
        self.trades = []
        self.equity_curve = [capital]

        position = 0  # 0=flat, 1=long, -1=short
        entry_price = 0.0
        entry_time = None
        entry_bar = 0
        stop_loss = 0.0
        take_profit = 0.0
        daily_pnl = 0.0
        daily_trades = 0
        current_day = None
        trailing_stop = 0.0
        bars_since_loss = 999

        for i in range(1, len(self.df)):
            row = self.df.iloc[i]
            sig = signals.iloc[i]
            prev = self.df.iloc[i-1]

            # Reset daily counters
            day = row.name.date() if hasattr(row.name, 'date') else None
            if day != current_day:
                daily_pnl = 0.0
                daily_trades = 0
                current_day = day

            # Check exits first
            if position != 0:
                exit_price = None
                exit_reason = ""

                if position == 1:  # Long position
                    # Update trailing stop
                    if row["high"] > entry_price:
                        new_trail = row["high"] - (entry_price - stop_loss) * 0.75
                        trailing_stop = max(trailing_stop, new_trail)

                    if row["low"] <= stop_loss:
                        exit_price = stop_loss - self.config.slippage_ticks * self.config.tick_size
                        exit_reason = "stop_loss"
                    elif row["low"] <= trailing_stop and trailing_stop > stop_loss:
                        exit_price = trailing_stop - self.config.slippage_ticks * self.config.tick_size
                        exit_reason = "trailing_stop"
                    elif row["high"] >= take_profit:
                        exit_price = take_profit - self.config.slippage_ticks * self.config.tick_size
                        exit_reason = "take_profit"
                    elif sig.get("long_exit", False):
                        exit_price = row["close"] - self.config.slippage_ticks * self.config.tick_size
                        exit_reason = "signal_exit"

                elif position == -1:  # Short position
                    if row["high"] > entry_price:
                        new_trail = row["low"] + (stop_loss - entry_price) * 0.75
                        trailing_stop = min(trailing_stop, new_trail)

                    if row["high"] >= stop_loss:
                        exit_price = stop_loss + self.config.slippage_ticks * self.config.tick_size
                        exit_reason = "stop_loss"
                    elif row["high"] >= trailing_stop and trailing_stop < stop_loss:
                        exit_price = trailing_stop + self.config.slippage_ticks * self.config.tick_size
                        exit_reason = "trailing_stop"
                    elif row["low"] <= take_profit:
                        exit_price = take_profit + self.config.slippage_ticks * self.config.tick_size
                        exit_reason = "take_profit"
                    elif sig.get("short_exit", False):
                        exit_price = row["close"] + self.config.slippage_ticks * self.config.tick_size
                        exit_reason = "signal_exit"

                if exit_price is not None:
                    # Calculate PnL
                    if position == 1:
                        pnl_points = exit_price - entry_price
                    else:
                        pnl_points = entry_price - exit_price

                    pnl = pnl_points * self.config.contract_value - 2 * self.config.commission
                    pnl_pct = pnl / capital * 100

                    trade = Trade(
                        entry_time=entry_time,
                        exit_time=row.name,
                        direction="long" if position == 1 else "short",
                        entry_price=entry_price,
                        exit_price=exit_price,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        bars_held=i - entry_bar,
                        exit_reason=exit_reason
                    )
                    self.trades.append(trade)
                    capital += pnl
                    daily_pnl += pnl

                    if pnl < 0:
                        bars_since_loss = 0
                    else:
                        bars_since_loss = 999

                    position = 0

            # Check entries (only if flat)
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
                        trailing_stop = stop_loss
                        position = 1
                        entry_time = row.name
                        entry_bar = i
                        daily_trades += 1

                    elif sig.get("short_entry", False):
                        entry_price = row["close"] - self.config.slippage_ticks * self.config.tick_size
                        stop_loss = sig.get("stop_loss", entry_price + 5)
                        take_profit = sig.get("take_profit", entry_price - 10)
                        trailing_stop = stop_loss
                        position = -1
                        entry_time = row.name
                        entry_bar = i
                        daily_trades += 1

            self.equity_curve.append(capital)

        return self._calculate_metrics()

    def _calculate_metrics(self) -> dict:
        """Calculate comprehensive backtest metrics."""
        if not self.trades:
            return {"total_trades": 0, "error": "No trades generated"}

        pnls = [t.pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        equity = pd.Series(self.equity_curve)
        peak = equity.cummax()
        drawdown = (equity - peak) / peak * 100
        max_dd = drawdown.min()

        # Consecutive wins/losses
        max_consec_wins = 0
        max_consec_losses = 0
        curr_wins = 0
        curr_losses = 0
        for p in pnls:
            if p > 0:
                curr_wins += 1
                curr_losses = 0
                max_consec_wins = max(max_consec_wins, curr_wins)
            else:
                curr_losses += 1
                curr_wins = 0
                max_consec_losses = max(max_consec_losses, curr_losses)

        # Exit reason breakdown
        exit_reasons = {}
        for t in self.trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

        # Long vs Short performance
        long_trades = [t for t in self.trades if t.direction == "long"]
        short_trades = [t for t in self.trades if t.direction == "short"]
        long_pnl = sum(t.pnl for t in long_trades)
        short_pnl = sum(t.pnl for t in short_trades)

        total_pnl = sum(pnls)
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1

        return {
            "total_trades": len(self.trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": len(wins) / len(self.trades) * 100,
            "total_pnl": total_pnl,
            "total_return_pct": (self.equity_curve[-1] / self.equity_curve[0] - 1) * 100,
            "avg_trade_pnl": np.mean(pnls),
            "avg_winner": np.mean(wins) if wins else 0,
            "avg_loser": np.mean(losses) if losses else 0,
            "largest_winner": max(pnls),
            "largest_loser": min(pnls),
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float('inf'),
            "max_drawdown_pct": max_dd,
            "avg_bars_held": np.mean([t.bars_held for t in self.trades]),
            "max_consec_wins": max_consec_wins,
            "max_consec_losses": max_consec_losses,
            "long_pnl": long_pnl,
            "short_pnl": short_pnl,
            "long_trades": len(long_trades),
            "short_trades": len(short_trades),
            "exit_reasons": exit_reasons,
            "sharpe_ratio": self._calc_sharpe(pnls),
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
# STRATEGY IMPLEMENTATIONS
# ============================================================================

def strategy_supertrend_macd(df, st_period=10, st_mult=3.0, rsi_period=14):
    """Strategy 1: SuperTrend + MACD + RSI Filter
    Based on TradingView's highest-rated gold indicator combination."""
    signals = pd.DataFrame(index=df.index)

    st, st_dir = supertrend(df, st_period, st_mult)
    macd_line, signal_line, hist = macd(df["close"])
    rsi_val = rsi(df["close"], rsi_period)
    atr_val = atr(df)

    # Long: SuperTrend bullish + MACD crossover + RSI not overbought
    signals["long_entry"] = ((st_dir == 1) & (st_dir.shift(1) == -1) &
                             (hist > 0) & (rsi_val < 70) & (rsi_val > 30))
    signals["short_entry"] = ((st_dir == -1) & (st_dir.shift(1) == 1) &
                              (hist < 0) & (rsi_val > 30) & (rsi_val < 70))
    signals["long_exit"] = (st_dir == -1) & (st_dir.shift(1) == 1)
    signals["short_exit"] = (st_dir == 1) & (st_dir.shift(1) == -1)
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - 2.0 * atr_val,
                           np.where(signals["short_entry"], df["close"] + 2.0 * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + 3.5 * atr_val,
                             np.where(signals["short_entry"], df["close"] - 3.5 * atr_val, np.nan))

    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def strategy_ichimoku_supertrend(df):
    """Strategy 2: Ichimoku Cloud + SuperTrend
    Top-rated swing trading combination for gold."""
    signals = pd.DataFrame(index=df.index)

    tenkan, kijun, senkou_a, senkou_b, _ = ichimoku(df)
    st, st_dir = supertrend(df, 10, 3.0)
    atr_val = atr(df)

    # Price above cloud + SuperTrend bullish + Tenkan cross Kijun
    cloud_bull = (df["close"] > senkou_a) & (df["close"] > senkou_b)
    cloud_bear = (df["close"] < senkou_a) & (df["close"] < senkou_b)
    tenkan_cross_up = (tenkan > kijun) & (tenkan.shift(1) <= kijun.shift(1))
    tenkan_cross_down = (tenkan < kijun) & (tenkan.shift(1) >= kijun.shift(1))

    signals["long_entry"] = cloud_bull & tenkan_cross_up & (st_dir == 1)
    signals["short_entry"] = cloud_bear & tenkan_cross_down & (st_dir == -1)
    signals["long_exit"] = tenkan_cross_down | (st_dir == -1)
    signals["short_exit"] = tenkan_cross_up | (st_dir == 1)
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - 2.5 * atr_val,
                           np.where(signals["short_entry"], df["close"] + 2.5 * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + 4.0 * atr_val,
                             np.where(signals["short_entry"], df["close"] - 4.0 * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def strategy_hull_vwap_bb(df):
    """Strategy 3: Hull MA + VWAP + Bollinger Band Squeeze
    Best day trading combination."""
    signals = pd.DataFrame(index=df.index)

    hma = hull_ma(df["close"], 20)
    vwap_val = vwap_session(df)
    bb_basis, bb_upper, bb_lower = bollinger_bands(df["close"], 20, 2.0)
    kc_basis, kc_upper, kc_lower = keltner_channels(df, 20, 1.5)
    rsi_val = rsi(df["close"], 14)
    atr_val = atr(df)

    # Squeeze detection: BB inside KC
    squeeze = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    squeeze_release = (~squeeze) & (squeeze.shift(1))

    # HMA direction
    hma_up = hma > hma.shift(1)
    hma_down = hma < hma.shift(1)

    # Long: Squeeze release + HMA up + Price above VWAP + RSI confirms
    signals["long_entry"] = (squeeze_release & hma_up &
                             (df["close"] > vwap_val) & (rsi_val > 50) & (rsi_val < 75))
    signals["short_entry"] = (squeeze_release & hma_down &
                              (df["close"] < vwap_val) & (rsi_val < 50) & (rsi_val > 25))
    signals["long_exit"] = hma_down | (rsi_val > 80)
    signals["short_exit"] = hma_up | (rsi_val < 20)
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - 1.5 * atr_val,
                           np.where(signals["short_entry"], df["close"] + 1.5 * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + 3.0 * atr_val,
                             np.where(signals["short_entry"], df["close"] - 3.0 * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def strategy_ema_atr_trailing(df, fast=9, slow=21, trend=100):
    """Strategy 4: EMA Crossover + ATR Trailing Stop
    Classic gold strategy that captured 3100+ pips in 2025."""
    signals = pd.DataFrame(index=df.index)

    fast_ema = ema(df["close"], fast)
    slow_ema = ema(df["close"], slow)
    trend_ema = ema(df["close"], trend)
    rsi_val = rsi(df["close"], 14)
    atr_val = atr(df)
    macd_line, _, hist = macd(df["close"])

    ema_cross_up = (fast_ema > slow_ema) & (fast_ema.shift(1) <= slow_ema.shift(1))
    ema_cross_down = (fast_ema < slow_ema) & (fast_ema.shift(1) >= slow_ema.shift(1))

    signals["long_entry"] = (ema_cross_up & (df["close"] > trend_ema) &
                             (rsi_val > 30) & (rsi_val < 70) & (hist > 0))
    signals["short_entry"] = (ema_cross_down & (df["close"] < trend_ema) &
                              (rsi_val > 30) & (rsi_val < 70) & (hist < 0))
    signals["long_exit"] = ema_cross_down
    signals["short_exit"] = ema_cross_up
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - 2.0 * atr_val,
                           np.where(signals["short_entry"], df["close"] + 2.0 * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + 3.0 * atr_val,
                             np.where(signals["short_entry"], df["close"] - 3.0 * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


def strategy_combined_best(df):
    """Strategy 5: COMBINED BEST - Uses confluence of all top indicators.
    Only enters when multiple strategies agree."""
    signals = pd.DataFrame(index=df.index)

    # Calculate all indicators
    st, st_dir = supertrend(df, 10, 3.0)
    hma = hull_ma(df["close"], 20)
    fast_ema = ema(df["close"], 9)
    slow_ema = ema(df["close"], 21)
    trend_ema = ema(df["close"], 100)
    rsi_val = rsi(df["close"], 14)
    macd_line, signal_line, hist = macd(df["close"])
    atr_val = atr(df)
    vwap_val = vwap_session(df)
    bb_basis, bb_upper, bb_lower = bollinger_bands(df["close"], 20, 2.0)
    tenkan, kijun, senkou_a, senkou_b, _ = ichimoku(df)

    # Confluence scoring
    bull_score = pd.Series(0, index=df.index, dtype=float)
    bear_score = pd.Series(0, index=df.index, dtype=float)

    # SuperTrend (weight: 2)
    bull_score += (st_dir == 1).astype(int) * 2
    bear_score += (st_dir == -1).astype(int) * 2

    # EMA trend (weight: 1)
    bull_score += (df["close"] > trend_ema).astype(int)
    bear_score += (df["close"] < trend_ema).astype(int)

    # EMA crossover (weight: 2)
    bull_score += (fast_ema > slow_ema).astype(int) * 2
    bear_score += (fast_ema < slow_ema).astype(int) * 2

    # HMA direction (weight: 1)
    bull_score += (hma > hma.shift(1)).astype(int)
    bear_score += (hma < hma.shift(1)).astype(int)

    # MACD (weight: 1)
    bull_score += (hist > 0).astype(int)
    bear_score += (hist < 0).astype(int)

    # VWAP (weight: 1)
    bull_score += (df["close"] > vwap_val).astype(int)
    bear_score += (df["close"] < vwap_val).astype(int)

    # Ichimoku cloud (weight: 1)
    cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)
    bull_score += (df["close"] > cloud_top).astype(int)
    bear_score += (df["close"] < cloud_bot).astype(int)

    # RSI filter (not a score, just a filter)
    rsi_ok = (rsi_val > 25) & (rsi_val < 75)

    # Need 7+ out of 9 points for entry (high confluence)
    min_score = 7
    prev_bull = bull_score.shift(1)
    prev_bear = bear_score.shift(1)

    signals["long_entry"] = ((bull_score >= min_score) & (prev_bull < min_score) & rsi_ok)
    signals["short_entry"] = ((bear_score >= min_score) & (prev_bear < min_score) & rsi_ok)
    signals["long_exit"] = (bull_score < 4)
    signals["short_exit"] = (bear_score < 4)

    # Tighter stops for higher quality trades
    signals["stop_loss"] = np.where(signals["long_entry"], df["close"] - 1.8 * atr_val,
                           np.where(signals["short_entry"], df["close"] + 1.8 * atr_val, np.nan))
    signals["take_profit"] = np.where(signals["long_entry"], df["close"] + 4.0 * atr_val,
                             np.where(signals["short_entry"], df["close"] - 4.0 * atr_val, np.nan))
    signals["stop_loss"] = signals["stop_loss"].ffill()
    signals["take_profit"] = signals["take_profit"].ffill()
    return signals


# ============================================================================
# MAIN: Run all strategies and compare
# ============================================================================

def run_all_strategies(df):
    """Run all strategies and return comparison."""
    strategies = {
        "SuperTrend + MACD + RSI": strategy_supertrend_macd,
        "Ichimoku + SuperTrend": strategy_ichimoku_supertrend,
        "Hull MA + VWAP + BB Squeeze": strategy_hull_vwap_bb,
        "EMA Crossover + ATR Trail": strategy_ema_atr_trailing,
        "COMBINED CONFLUENCE": strategy_combined_best,
    }

    results = {}
    engine_cache = {}
    for name, strategy_fn in strategies.items():
        print(f"\n{'='*60}")
        print(f"Testing: {name}")
        print(f"{'='*60}")

        signals = strategy_fn(df)
        engine = BacktestEngine(df)
        metrics = engine.run_strategy(signals)
        results[name] = metrics
        engine_cache[name] = engine

        if metrics.get("total_trades", 0) > 0:
            print(f"  Trades:        {metrics['total_trades']}")
            print(f"  Win Rate:      {metrics['win_rate']:.1f}%")
            print(f"  Total PnL:     ${metrics['total_pnl']:.2f}")
            print(f"  Total Return:  {metrics['total_return_pct']:.1f}%")
            print(f"  Profit Factor: {metrics['profit_factor']:.2f}")
            print(f"  Max Drawdown:  {metrics['max_drawdown_pct']:.1f}%")
            print(f"  Sharpe Ratio:  {metrics['sharpe_ratio']:.2f}")
            print(f"  Avg Winner:    ${metrics['avg_winner']:.2f}")
            print(f"  Avg Loser:     ${metrics['avg_loser']:.2f}")
            print(f"  Long PnL:      ${metrics['long_pnl']:.2f} ({metrics['long_trades']} trades)")
            print(f"  Short PnL:     ${metrics['short_pnl']:.2f} ({metrics['short_trades']} trades)")
            print(f"  Exit Reasons:  {metrics['exit_reasons']}")
        else:
            print(f"  No trades generated!")

    return results, engine_cache


def print_comparison_table(results):
    """Print a formatted comparison table."""
    print("\n\n" + "="*100)
    print("STRATEGY COMPARISON TABLE")
    print("="*100)

    header = f"{'Strategy':<30} {'Trades':>7} {'Win%':>7} {'PnL($)':>10} {'Return%':>9} {'PF':>6} {'MaxDD%':>8} {'Sharpe':>7}"
    print(header)
    print("-"*100)

    for name, m in results.items():
        if m.get("total_trades", 0) > 0:
            line = (f"{name:<30} {m['total_trades']:>7} {m['win_rate']:>6.1f}% "
                   f"{m['total_pnl']:>10.2f} {m['total_return_pct']:>8.1f}% "
                   f"{m['profit_factor']:>6.2f} {m['max_drawdown_pct']:>7.1f}% "
                   f"{m['sharpe_ratio']:>7.2f}")
        else:
            line = f"{name:<30}     NO TRADES GENERATED"
        print(line)

    # Find best strategy
    valid = {k: v for k, v in results.items() if v.get("total_trades", 0) > 0}
    if valid:
        best_pf = max(valid, key=lambda k: valid[k].get("profit_factor", 0))
        best_dd = max(valid, key=lambda k: valid[k].get("max_drawdown_pct", -999))
        best_wr = max(valid, key=lambda k: valid[k].get("win_rate", 0))
        best_ret = max(valid, key=lambda k: valid[k].get("total_return_pct", 0))

        print("\n" + "="*100)
        print("BEST PERFORMERS:")
        print(f"  Best Profit Factor:  {best_pf}")
        print(f"  Lowest Drawdown:     {best_dd}")
        print(f"  Highest Win Rate:    {best_wr}")
        print(f"  Best Total Return:   {best_ret}")

        # Composite score (weighted)
        scores = {}
        for name, m in valid.items():
            score = (m["profit_factor"] * 25 +
                    m["win_rate"] * 0.5 +
                    m["total_return_pct"] * 0.2 +
                    m["max_drawdown_pct"] * -0.5 +   # Lower DD = better
                    m["sharpe_ratio"] * 10)
            scores[name] = score

        best_overall = max(scores, key=scores.get)
        print(f"\n  >>> BEST OVERALL (composite score): {best_overall} <<<")
        print(f"      Score: {scores[best_overall]:.1f}")

    print("="*100)


if __name__ == "__main__":
    from tv_connector import get_gold_data

    print("Fetching gold futures data from TradingView...")
    df = get_gold_data(symbol="GC1!", exchange="COMEX", interval="60", n_bars=5000)

    if df is not None and len(df) > 100:
        results, engines = run_all_strategies(df)
        print_comparison_table(results)
    else:
        print("ERROR: Could not fetch sufficient data!")
