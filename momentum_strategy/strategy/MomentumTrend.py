"""
MomentumTrend — a deliberately simple, transparent long-only trend/breakout
strategy for Freqtrade (Binance spot USDT).

Design philosophy — the anti-NFI
--------------------------------
The NostalgiaForInfinity analysis in ../nfi_validation showed what curve fitting
looks like: 75,000 lines, tens of thousands of hand-set thresholds, a rewrite every
~7 months. This strategy is the opposite on purpose:

  * ONE economic thesis: crypto trends persist (momentum / breakout is one of the
    most robust, cross-asset-documented anomalies). Nothing else.
  * ~7 parameters, all ROUND NUMBERS chosen a priori, NOT hyperopted. Fewer knobs =
    less surface to overfit. They are class constants so you can see every one.
  * Higher timeframe (1h) and a breakout trigger => infrequent trades => low
    sensitivity to fees/slippage (NFI's structural weakness was the opposite).
  * Risk is managed explicitly (hard stop + trailing + time-decaying ROI), not hidden
    in DCA/"grinding" that masks losers.

Honest status
-------------
A backtest that looks good proves NOTHING here — that is the entire lesson of the NFI
work. This strategy is UNVALIDATED until it survives the same gauntlet: out-of-sample
walk-forward, realistic costs (break-even > ~40 bps), a Buy&Hold-BTC Sortino benchmark,
and robustness (Monte Carlo / regime split). Run ../nfi_validation on it before
trusting a single number. No claim of edge is made.
"""
from __future__ import annotations

import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame
from technical import qtpylib


class MomentumTrend(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False  # spot

    # --- parameters: all a-priori, round, NOT hyperopted -------------------
    EMA_FAST = 50      # trend structure (fast)
    EMA_SLOW = 200     # regime filter (the classic long-term trend line)
    DONCHIAN = 20      # breakout lookback (20-bar high == "new local high")
    ADX_MIN = 20       # only trade when a trend actually exists
    RSI_LEN = 14       # standard
    RSI_EXIT = 80      # exhaustion exit
    ATR_LEN = 14       # for reference/sizing docs

    # --- risk management (explicit, not hidden) ----------------------------
    stoploss = -0.10                     # hard stop: -10%
    trailing_stop = True
    trailing_stop_positive = 0.03        # once in profit, trail 3%
    trailing_stop_positive_offset = 0.06 # ...but only after +6%
    trailing_only_offset_is_reached = True
    # time-decaying profit targets (keys are minutes): take more early, relax later
    minimal_roi = {"0": 0.20, "720": 0.10, "1440": 0.05, "2880": 0.0}

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    process_only_new_candles = True
    startup_candle_count = 200  # need EMA200 warm

    order_types = {
        "entry": "limit", "exit": "limit",
        "stoploss": "market", "stoploss_on_exchange": False,
    }

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=self.EMA_FAST)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=self.EMA_SLOW)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.RSI_LEN)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.ATR_LEN)
        # prior N-bar high (shifted so the current bar can break OUT of it)
        dataframe["donchian_high"] = dataframe["high"].rolling(self.DONCHIAN).max().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long = (
            (dataframe["ema_fast"] > dataframe["ema_slow"])          # uptrend structure
            & (dataframe["close"] > dataframe["ema_slow"])           # above the regime line
            & (dataframe["adx"] > self.ADX_MIN)                      # trend has strength
            & qtpylib.crossed_above(dataframe["close"], dataframe["donchian_high"])  # breakout
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[long, ["enter_long", "enter_tag"]] = (1, "breakout_uptrend")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        exit_long = (
            (dataframe["close"] < dataframe["ema_fast"])             # trend broke
            | (dataframe["rsi"] > self.RSI_EXIT)                     # blow-off / exhaustion
        ) & (dataframe["volume"] > 0)
        dataframe.loc[exit_long, ["exit_long", "exit_tag"]] = (1, "trend_break_or_exhaustion")
        return dataframe
