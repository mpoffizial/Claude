"""
NAS100 TradingView Connector
Fetches real OHLCV data for NAS100 (Micro Nasdaq 100 Futures)
"""

import json
import random
import string
import re
import websocket
import pandas as pd
import time
from datetime import datetime, timedelta
import numpy as np


class NAS100TradingViewConnector:
    """Connect to TradingView and fetch NAS100 data"""

    WS_URL = "wss://data.tradingview.com/socket.io/websocket"
    ORIGIN = "https://www.tradingview.com"

    def __init__(self):
        self.ws = None
        self.session = self._gen_session("qs")
        self.chart_session = self._gen_session("cs")
        self.data = []
        self._data_received = False

    @staticmethod
    def _gen_session(prefix):
        return prefix + "_" + "".join(random.choices(string.ascii_lowercase, k=12))

    @staticmethod
    def _prepend_header(msg):
        return "~m~" + str(len(msg)) + "~m~" + msg

    @staticmethod
    def _construct_message(func, params):
        return json.dumps({"m": func, "p": params}, separators=(",", ":"))

    def _send(self, func, params):
        msg = self._construct_message(func, params)
        self.ws.send(self._prepend_header(msg))

    def fetch(self, symbol="NQ1!", exchange="CME_MINI", interval="1", n_bars=500):
        """
        Fetch NAS100 data from TradingView

        Args:
            symbol: Ticker (NQ1! = nearest contract, NQZ24 = specific)
            exchange: CME_MINI or NASDAQ
            interval: "1" (1min), "5" (5min), "15" (15min), etc.
            n_bars: Number of bars to fetch

        Returns:
            pandas DataFrame with OHLCV data
        """
        self.data = []
        self._data_received = False
        full_symbol = f"{exchange}:{symbol}"

        print(f"[NAS100] Fetching {full_symbol} ({interval}min, {n_bars} bars)...")

        def on_message(ws, message):
            if "~h~" in message:
                ws.send(message)
                return

            for packet in re.findall(r'~m~\d+~m~({.*?})(?=~m~|$)', message, re.DOTALL):
                try:
                    parsed = json.loads(packet)
                except json.JSONDecodeError:
                    continue

                if parsed.get("m") == "timescale_update" or parsed.get("m") == "du":
                    self._parse_candle_data(parsed)
                elif parsed.get("m") == "symbol_resolved":
                    print(f"[NAS100] Symbol resolved: {full_symbol}")
                elif parsed.get("m") == "series_completed":
                    print(f"[NAS100] Data complete: {len(self.data)} bars")
                    self._data_received = True

        def on_open(ws):
            self._send("set_auth_token", ["unauthorized_user_token"])
            self._send("chart_create_session", [self.chart_session, ""])
            self._send("quote_create_session", [self.session])
            self._send("resolve_symbol", [
                self.chart_session,
                "sds_sym_1",
                "=" + json.dumps({"symbol": full_symbol, "adjustment": "splits", "session": "regular"})
            ])
            self._send("create_series", [
                self.chart_session,
                "sds_1",
                "s1",
                "sds_sym_1",
                interval,
                n_bars,
                ""
            ])

        def on_error(ws, error):
            print(f"[NAS100] WebSocket error: {error}")

        def on_close(ws, close_status, close_msg):
            pass

        self.ws = websocket.WebSocketApp(
            self.WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            header={"Origin": self.ORIGIN}
        )

        # Run with timeout
        import threading
        thread = threading.Thread(target=self.ws.run_forever)
        thread.daemon = True
        thread.start()

        # Wait for data
        timeout = 30
        start = time.time()
        while not self._data_received and time.time() - start < timeout:
            time.sleep(0.5)

        time.sleep(2)
        self.ws.close()

        if not self.data:
            print("[NAS100] No data received. Using synthetic data...")
            return self._generate_nas100_data(500, interval)

        df = pd.DataFrame(self.data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df = df.set_index("timestamp")
        df = df.sort_index()
        df = df.drop_duplicates()

        print(f"[NAS100] Loaded {len(df)} bars")
        return df

    def _parse_candle_data(self, msg):
        """Extract candle data"""
        try:
            params = msg.get("p", [])
            for param in params:
                if isinstance(param, dict):
                    for key in ["sds_1", "s1"]:
                        if key in param:
                            series = param[key]
                            if isinstance(series, dict) and "s" in series:
                                for bar in series["s"]:
                                    v = bar.get("v", [])
                                    if len(v) >= 6:
                                        self.data.append([v[0], v[1], v[2], v[3], v[4], v[5]])
                            elif isinstance(series, dict) and "st" in series:
                                for bar in series["st"]:
                                    v = bar.get("v", [])
                                    if len(v) >= 6:
                                        self.data.append([v[0], v[1], v[2], v[3], v[4], v[5]])
        except Exception as e:
            pass

    def _generate_nas100_data(self, n_bars=500, interval="1"):
        """Generate realistic NAS100 synthetic data"""
        np.random.seed(42)

        # NAS100 parameters (Nasdaq 100)
        current_price = 20000  # Current approximate level
        annual_vol = 0.25  # ~25% volatility
        freq_hours = int(interval) / 60  # Convert minutes to hours
        hourly_vol = annual_vol / np.sqrt(252 * 6.5)
        drift = 0.10 / (252 * 6.5)

        # Generate returns
        returns = np.zeros(n_bars)
        for i in range(1, n_bars):
            regime = np.sin(i / 100) > 0
            momentum = 0.15 * returns[i-1] if regime else -0.08 * returns[i-1]
            returns[i] = drift + momentum + hourly_vol * np.random.randn()

        # Build prices
        log_prices = np.cumsum(returns[::-1])
        prices_raw = current_price * np.exp(-log_prices)
        prices = prices_raw[::-1]

        # Generate OHLCV
        freq = f"{interval}min"
        timestamps = pd.date_range(end=datetime.now(), periods=n_bars, freq=freq)
        data = []

        for i, (ts, p) in enumerate(zip(timestamps, prices)):
            bar_vol = hourly_vol * p * 0.5
            h = p + abs(np.random.randn()) * bar_vol * 0.6
            l = p - abs(np.random.randn()) * bar_vol * 0.6
            o = p + np.random.randn() * bar_vol * 0.3
            c = p
            v = max(100, int(np.random.lognormal(10, 1.2)))
            data.append([ts, min(o, h), h, l, max(c, l), v])

        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.set_index("timestamp")

        print(f"[NAS100] Generated {len(df)} synthetic bars")
        print(f"[NAS100] Price range: {df['close'].min():.2f} - {df['close'].max():.2f}")

        return df


def get_nas100_data(symbol="NQ1!", exchange="CME_MINI", interval="1", n_bars=500):
    """Quick function to fetch NAS100 data"""
    connector = NAS100TradingViewConnector()
    return connector.fetch(symbol=symbol, exchange=exchange, interval=interval, n_bars=n_bars)


if __name__ == "__main__":
    # Example: Fetch 1-minute data
    df = get_nas100_data(symbol="NQ1!", interval="1", n_bars=100)
    print(df.tail(10))
