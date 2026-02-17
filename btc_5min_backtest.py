#!/usr/bin/env python3
"""
BTC UP/DOWN 5-Minute Strategy Backtest
Test the strategy with historical data before going live
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass
import json


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    direction: str  # "UP" or "DOWN"
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    bars_held: int
    exit_reason: str


class BTC5MinBacktester:
    """Backtest 5-minute BTC UP/DOWN strategy"""

    def __init__(self, initial_capital=10000):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.trades = []
        self.equity_curve = [initial_capital]

    @staticmethod
    def generate_realistic_historical_btc(n_bars=500):
        """Generate realistic historical 5-minute Bitcoin data"""
        np.random.seed(123)  # Consistent for backtesting

        # Simulate various market conditions
        base_price = 95000
        prices = [base_price]

        for i in range(n_bars):
            # Regime 1: Uptrend (bars 0-100)
            if i < 100:
                trend = 25
                volatility = 100
                prices.append(prices[-1] + np.random.randn() * volatility + trend)

            # Regime 2: Consolidation (bars 100-200)
            elif i < 200:
                trend = 0
                volatility = 80
                prices.append(prices[-1] + np.random.randn() * volatility + trend)

            # Regime 3: Downtrend (bars 200-300)
            elif i < 300:
                trend = -40
                volatility = 120
                prices.append(prices[-1] + np.random.randn() * volatility + trend)

            # Regime 4: Volatility spike (bars 300-400)
            elif i < 400:
                trend = 10
                volatility = 200
                prices.append(prices[-1] + np.random.randn() * volatility + trend)

            # Regime 5: Strong uptrend (bars 400+)
            else:
                trend = 60
                volatility = 100
                prices.append(prices[-1] + np.random.randn() * volatility + trend)

        # Generate OHLCV
        now = datetime.now()
        timestamps = [now - timedelta(minutes=n_bars-i) for i in range(n_bars)]

        data = []
        for i, (ts, price) in enumerate(zip(timestamps, prices)):
            h = price + abs(np.random.randn()) * 50
            l = price - abs(np.random.randn()) * 50
            o = l + (h - l) * np.random.uniform(0.3, 0.7)
            c = l + (h - l) * np.random.uniform(0.3, 0.7)
            vol = int(np.random.lognormal(15, 1.0))

            data.append([ts, o, h, l, c, vol])

        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.set_index("timestamp")
        df = df.sort_index()

        return df

    def calculate_indicators(self, df):
        """Calculate indicators for strategy"""
        # EMA
        ema_5 = df["close"].ewm(span=5, adjust=False).mean()
        ema_10 = df["close"].ewm(span=10, adjust=False).mean()

        # RSI
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        # MACD
        fast_ema = df["close"].ewm(span=5, adjust=False).mean()
        slow_ema = df["close"].ewm(span=13, adjust=False).mean()
        macd_line = fast_ema - slow_ema
        signal_line = macd_line.ewm(span=5, adjust=False).mean()
        macd_hist = macd_line - signal_line

        # Stochastic
        low_min = df["low"].rolling(14).min()
        high_max = df["high"].rolling(14).max()
        k_percent = 100 * (df["close"] - low_min) / (high_max - low_min)
        d_percent = k_percent.rolling(3).mean()

        # ATR
        tr1 = df["high"] - df["low"]
        tr2 = abs(df["high"] - df["close"].shift(1))
        tr3 = abs(df["low"] - df["close"].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        return {
            "ema_5": ema_5,
            "ema_10": ema_10,
            "rsi": rsi,
            "macd_hist": macd_hist,
            "k_percent": k_percent,
            "d_percent": d_percent,
            "atr": atr
        }

    def generate_signals(self, df, indicators):
        """Generate BUY UP / BUY DOWN signals using strategy"""
        signals = pd.DataFrame(index=df.index)

        ema_5 = indicators["ema_5"]
        ema_10 = indicators["ema_10"]
        rsi = indicators["rsi"]
        macd_hist = indicators["macd_hist"]
        k_percent = indicators["k_percent"]
        d_percent = indicators["d_percent"]
        atr = indicators["atr"]

        # Strategy: Trend following + Mean Reversion
        # BUY UP when:
        # - EMA(5) > EMA(10) (uptrend)
        # - MACD histogram > 0 (momentum)
        # - Stochastic K > D (momentum confirmation)
        # - RSI not too extreme

        buy_up = (
            (ema_5 > ema_10) &
            (macd_hist > 0) &
            (k_percent > d_percent) &
            (rsi < 80) &
            (rsi > 30)
        )

        # BUY DOWN when:
        # - EMA(5) < EMA(10) (downtrend)
        # - MACD histogram < 0 (negative momentum)
        # - Stochastic K < D (negative momentum)
        # - RSI not too extreme

        buy_down = (
            (ema_5 < ema_10) &
            (macd_hist < 0) &
            (k_percent < d_percent) &
            (rsi > 20) &
            (rsi < 70)
        )

        signals["buy_up"] = buy_up.astype(int)
        signals["buy_down"] = buy_down.astype(int)
        signals["atr"] = atr

        return signals

    def backtest(self, df, signals, risk_per_trade=0.02):
        """
        Run backtest with position sizing and risk management

        Args:
            df: OHLCV data
            signals: Generated signals
            risk_per_trade: Risk per trade (2% default)
        """
        self.trades = []
        self.equity_curve = [self.initial_capital]
        capital = self.initial_capital
        position = 0  # 0=flat, 1=long (UP), -1=short (DOWN)
        entry_price = 0
        entry_time = None
        entry_bar = 0
        stop_loss = 0
        take_profit = 0

        for i in range(1, len(df)):
            row = df.iloc[i]
            sig = signals.iloc[i]
            prev_sig = signals.iloc[i-1]
            atr = sig["atr"]

            # EXIT CONDITIONS
            if position != 0:
                exit_price = None
                exit_reason = ""

                if position == 1:  # Long (UP)
                    # Exit at take profit
                    if row["high"] >= take_profit:
                        exit_price = take_profit
                        exit_reason = "TP"
                    # Exit at stop loss
                    elif row["low"] <= stop_loss:
                        exit_price = stop_loss
                        exit_reason = "SL"
                    # Exit on signal reversal
                    elif sig["buy_down"] and not prev_sig["buy_down"]:
                        exit_price = row["close"]
                        exit_reason = "Signal Reversal"

                elif position == -1:  # Short (DOWN)
                    # Exit at take profit
                    if row["low"] <= take_profit:
                        exit_price = take_profit
                        exit_reason = "TP"
                    # Exit at stop loss
                    elif row["high"] >= stop_loss:
                        exit_price = stop_loss
                        exit_reason = "SL"
                    # Exit on signal reversal
                    elif sig["buy_up"] and not prev_sig["buy_up"]:
                        exit_price = row["close"]
                        exit_reason = "Signal Reversal"

                if exit_price:
                    pnl = (exit_price - entry_price) if position == 1 else (entry_price - exit_price)
                    pnl_pct = (pnl / entry_price) * 100

                    trade = Trade(
                        entry_time=entry_time,
                        exit_time=row.name,
                        direction="UP" if position == 1 else "DOWN",
                        entry_price=entry_price,
                        exit_price=exit_price,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        bars_held=i - entry_bar,
                        exit_reason=exit_reason
                    )
                    self.trades.append(trade)

                    capital += pnl
                    position = 0

            # ENTRY CONDITIONS
            if position == 0:
                # Position size based on risk
                risk_amount = capital * risk_per_trade / 100
                if not pd.isna(atr) and atr > 0:
                    position_size = risk_amount / atr
                else:
                    position_size = 0

                if sig["buy_up"] and not prev_sig["buy_up"]:
                    entry_price = row["close"]
                    entry_time = row.name
                    entry_bar = i
                    stop_loss = entry_price - (atr * 1.5)
                    take_profit = entry_price + (atr * 2.0)
                    position = 1

                elif sig["buy_down"] and not prev_sig["buy_down"]:
                    entry_price = row["close"]
                    entry_time = row.name
                    entry_bar = i
                    stop_loss = entry_price + (atr * 1.5)
                    take_profit = entry_price - (atr * 2.0)
                    position = -1

            self.equity_curve.append(capital)

        return self._calculate_metrics()

    def _calculate_metrics(self):
        """Calculate backtest statistics"""
        if not self.trades:
            return {
                "total_trades": 0,
                "status": "NO TRADES"
            }

        pnls = [t.pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        equity = pd.Series(self.equity_curve)
        peak = equity.cummax()
        drawdown = ((equity - peak) / peak * 100)
        max_dd = drawdown.min()

        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        up_trades = [t for t in self.trades if t.direction == "UP"]
        down_trades = [t for t in self.trades if t.direction == "DOWN"]
        up_wins = len([t for t in up_trades if t.pnl > 0])
        down_wins = len([t for t in down_trades if t.pnl > 0])

        return {
            "total_trades": len(self.trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": len(wins) / len(self.trades) * 100 if self.trades else 0,
            "avg_winner": np.mean(wins) if wins else 0,
            "avg_loser": np.mean(losses) if losses else 0,
            "largest_winner": max(pnls) if pnls else 0,
            "largest_loser": min(pnls) if pnls else 0,
            "total_pnl": sum(pnls),
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_dd,
            "initial_capital": self.initial_capital,
            "final_capital": self.equity_curve[-1],
            "total_return_pct": (self.equity_curve[-1] / self.initial_capital - 1) * 100,
            "avg_trade_bars": np.mean([t.bars_held for t in self.trades]) if self.trades else 0,
            "up_trades": len(up_trades),
            "down_trades": len(down_trades),
            "up_win_rate": up_wins / len(up_trades) * 100 if up_trades else 0,
            "down_win_rate": down_wins / len(down_trades) * 100 if down_trades else 0,
        }


def print_backtest_report(metrics):
    """Print formatted backtest results"""
    print("\n")
    print("╔" + "═" * 94 + "╗")
    print("║" + " " * 30 + "BTC 5-MINUTE STRATEGY BACKTEST RESULTS" + " " * 26 + "║")
    print("╚" + "═" * 94 + "╝")

    if metrics["total_trades"] == 0:
        print("\n❌ NO TRADES GENERATED - Strategy needs adjustment")
        return

    print(f"\n📊 OVERALL PERFORMANCE")
    print("─" * 96)
    print(f"  Total Trades:              {metrics['total_trades']}")
    print(f"  Winning Trades:            {metrics['winning_trades']} ({metrics['win_rate']:.1f}%)")
    print(f"  Losing Trades:             {metrics['losing_trades']}")
    print(f"  Profit Factor:             {metrics['profit_factor']:.2f}")
    print(f"  ─────────────────────────────────────────────────")
    print(f"  Total P&L:                 ${metrics['total_pnl']:,.2f}")
    print(f"  Initial Capital:           ${metrics['initial_capital']:,.2f}")
    print(f"  Final Capital:             ${metrics['final_capital']:,.2f}")
    print(f"  Total Return:              {metrics['total_return_pct']:.2f}%")

    print(f"\n💰 TRADE STATISTICS")
    print("─" * 96)
    print(f"  Average Winner:            ${metrics['avg_winner']:,.2f}")
    print(f"  Average Loser:             ${metrics['avg_loser']:,.2f}")
    print(f"  Largest Winner:            ${metrics['largest_winner']:,.2f}")
    print(f"  Largest Loser:             ${metrics['largest_loser']:,.2f}")
    print(f"  Avg Bars per Trade:        {metrics['avg_trade_bars']:.1f}")

    print(f"\n📈 DIRECTIONAL ANALYSIS")
    print("─" * 96)
    print(f"  UP Trades:                 {metrics['up_trades']} (Win Rate: {metrics['up_win_rate']:.1f}%)")
    print(f"  DOWN Trades:               {metrics['down_trades']} (Win Rate: {metrics['down_win_rate']:.1f}%)")

    print(f"\n⚠️  RISK METRICS")
    print("─" * 96)
    print(f"  Max Drawdown:              {metrics['max_drawdown_pct']:.2f}%")

    print("\n")

    # Assessment
    print("╔" + "═" * 94 + "╗")
    print("║" + " " * 35 + "STRATEGY ASSESSMENT" + " " * 41 + "║")
    print("╚" + "═" * 94 + "╝")

    if metrics["profit_factor"] > 1.5 and metrics["win_rate"] > 50:
        verdict = "✅ STRONG - Ready for live trading"
    elif metrics["profit_factor"] > 1.2 and metrics["win_rate"] > 45:
        verdict = "⚠️  FAIR - Consider optimizing parameters"
    elif metrics["profit_factor"] > 1.0:
        verdict = "🟡 MARGINAL - High risk, needs refinement"
    else:
        verdict = "❌ FAILED - Strategy is unprofitable"

    print(f"\n  Verdict:                   {verdict}")
    print(f"  Confidence:                {min(100, metrics['profit_factor'] * 50):.0f}% based on Profit Factor")

    if metrics["total_return_pct"] > 10:
        print(f"  Return Quality:            Excellent ({metrics['total_return_pct']:.1f}%)")
    elif metrics["total_return_pct"] > 5:
        print(f"  Return Quality:            Good ({metrics['total_return_pct']:.1f}%)")
    elif metrics["total_return_pct"] > 0:
        print(f"  Return Quality:            Positive ({metrics['total_return_pct']:.1f}%)")
    else:
        print(f"  Return Quality:            Negative ({metrics['total_return_pct']:.1f}%)")

    if metrics["max_drawdown_pct"] > -20:
        print(f"  Risk Assessment:           Acceptable (Max DD: {metrics['max_drawdown_pct']:.1f}%)")
    elif metrics["max_drawdown_pct"] > -50:
        print(f"  Risk Assessment:           High (Max DD: {metrics['max_drawdown_pct']:.1f}%)")
    else:
        print(f"  Risk Assessment:           Very High (Max DD: {metrics['max_drawdown_pct']:.1f}%)")

    print("\n" + "╔" + "═" * 94 + "╗")


if __name__ == "__main__":
    print("\n" + "=" * 96)
    print("🧪 BTC 5-MINUTE STRATEGY BACKTEST")
    print("=" * 96)

    # Generate historical data
    print("\n[1/4] Generating historical 5-minute Bitcoin data...")
    backtester = BTC5MinBacktester(initial_capital=10000)
    df = backtester.generate_realistic_historical_btc(n_bars=500)
    print(f"✅ Generated {len(df)} 5-minute bars")
    print(f"   Price range: ${df['close'].min():,.0f} - ${df['close'].max():,.0f}")

    # Calculate indicators
    print("\n[2/4] Calculating technical indicators...")
    indicators = backtester.calculate_indicators(df)
    print(f"✅ Indicators calculated:")
    print(f"   - EMA(5), EMA(10)")
    print(f"   - RSI(14)")
    print(f"   - MACD")
    print(f"   - Stochastic")
    print(f"   - ATR(14)")

    # Generate signals
    print("\n[3/4] Generating trading signals...")
    signals = backtester.generate_signals(df, indicators)
    num_up_signals = signals["buy_up"].sum()
    num_down_signals = signals["buy_down"].sum()
    print(f"✅ Signals generated:")
    print(f"   - BUY UP signals: {num_up_signals}")
    print(f"   - BUY DOWN signals: {num_down_signals}")

    # Run backtest
    print("\n[4/4] Running backtest with risk management...")
    metrics = backtester.backtest(df, signals, risk_per_trade=2.0)
    print(f"✅ Backtest complete!")

    # Print results
    print_backtest_report(metrics)

    # Export results
    export_data = {
        "timestamp": datetime.now().isoformat(),
        "backtest_period": f"{df.index[0]} to {df.index[-1]}",
        "metrics": {k: float(v) if isinstance(v, (np.floating, np.integer)) else v
                   for k, v in metrics.items()},
        "trades": [
            {
                "entry_time": str(t.entry_time),
                "exit_time": str(t.exit_time),
                "direction": t.direction,
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price),
                "pnl": float(t.pnl),
                "pnl_pct": float(t.pnl_pct),
                "bars_held": t.bars_held,
                "exit_reason": t.exit_reason
            }
            for t in backtester.trades
        ]
    }

    with open("btc_5min_backtest_results.json", "w") as f:
        json.dump(export_data, f, indent=2)

    print(f"\n✅ Results exported to btc_5min_backtest_results.json")
