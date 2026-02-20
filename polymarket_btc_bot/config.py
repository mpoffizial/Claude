"""
Configuration for the Polymarket BTC Trading Bot.
Supports both 5-minute and 15-minute markets.
All parameters, thresholds, API endpoints, and risk limits.
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TradingMode(Enum):
    SIMULATION = "simulation"
    PAPER = "paper"
    LIVE = "live"


@dataclass
class BinanceConfig:
    ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
    rest_url: str = "https://api.binance.com/api/v3"
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 60.0


@dataclass
class PolymarketConfig:
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    clob_rest_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137  # Polygon mainnet

    # Auth from env
    private_key: str = field(default_factory=lambda: os.getenv("POLY_PRIVATE_KEY", ""))
    api_key: str = field(default_factory=lambda: os.getenv("POLY_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("POLY_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("POLY_API_PASSPHRASE", ""))
    proxy_wallet: str = field(default_factory=lambda: os.getenv("POLY_PROXY_WALLET", ""))


@dataclass
class ChainlinkConfig:
    api_url: str = "https://data.chain.link/streams/btc-usd"
    poll_interval_seconds: float = 5.0


@dataclass
class StrategyConfig:
    # Market Duration
    market_duration_seconds: int = 300          # 300s = 5min, 900s = 15min

    # Lag Arbitrage (Layer 1)
    momentum_threshold: float = 0.0015     # 0.15% BTC move - lower for 5min markets
    momentum_lookback_seconds: int = 20    # Look back 20s (scaled for 5min market)
    max_ask_price_lag: float = 0.62        # Only buy if ask < this
    min_edge_ratio: float = 0.05           # (1-ask)/ask must exceed this

    # Intra-Market Arbitrage (Layer 2)
    arb_threshold: float = 0.975           # up_ask + down_ask < this triggers arb
    arb_min_profit: float = 0.005          # Min profit after fees for arb

    # Late-Period Momentum (Layer 3)
    late_period_activation_seconds: int = 90    # Last 90s of 5min market (last 30%)
    late_period_price_deviation: float = 0.001  # 0.1% deviation from opening
    late_period_max_ask: float = 0.80           # Only buy if ask < this

    # Early Scalping (Layer 4 - 5min specific)
    scalping_entry_window_seconds: int = 60     # Trade only in first 60s
    scalping_momentum_threshold: float = 0.0012 # 0.12% move triggers scalp entry
    scalping_max_ask: float = 0.60              # Buy only if ask <= 0.60
    scalping_acceleration_threshold: float = 0.0006  # Min additional momentum per 10s

    # Signal Aggregation Weights
    lag_weight: float = 0.40
    arb_weight: float = 0.30
    late_weight: float = 0.15
    scalping_weight: float = 0.15


@dataclass
class RiskConfig:
    # Position Limits
    max_position_per_market: float = 20.0       # Max $20 USDC per market
    max_open_positions: int = 3                  # Max 3 simultaneous markets
    max_daily_exposure: float = 200.0            # Max $200 USDC total exposure/day

    # Loss Limits
    max_daily_loss: float = -30.0                # Stop bot at -$30/day
    max_single_trade_loss: float = -10.0         # Max loss per trade

    # Edge Requirements
    min_edge_threshold: float = 0.03             # Minimum 3% expected edge
    min_momentum_threshold: float = 0.002        # Minimum 0.2% BTC movement

    # Timing (tuned for 5min markets)
    no_trade_first_seconds: int = 10             # Only 10s warmup for 5min market
    no_trade_last_seconds: int = 15              # Buffer before close
    max_hold_time_minutes: int = 4               # Max hold = market duration

    # Fee Management
    winner_fee: float = 0.02                     # Polymarket 2% winner fee
    min_profit_after_fee: float = 0.005          # Min $0.005 profit after fee


@dataclass
class ExecutionConfig:
    default_trade_size: float = 10.0             # Default USDC per trade
    max_orders_per_minute: int = 60              # API rate limit
    order_timeout_seconds: float = 2.0           # Timeout for order placement
    max_retries: int = 3                         # Max retries on failure
    slippage_tolerance: float = 0.005            # 0.5% slippage tolerance
    use_limit_orders: bool = True                # Prefer limit orders for rebates


@dataclass
class BacktestConfig:
    data_dir: str = "backtest_data"
    lookback_days: int = 30
    slippage_bps: float = 50.0                   # 0.5% = 50 basis points
    latency_ms: int = 200                        # Simulated latency
    partial_fill_rate: float = 0.85              # 85% of orders fully filled
    train_ratio: float = 0.67                    # 67% train, 33% test


@dataclass
class MonitoringConfig:
    log_dir: str = "logs"
    log_level: str = "INFO"
    dashboard_refresh_seconds: float = 1.0
    alert_on_disconnect_seconds: int = 300       # Alert if no API response for 5min


@dataclass
class BotConfig:
    mode: TradingMode = TradingMode.SIMULATION
    trade_size: float = 10.0

    binance: BinanceConfig = field(default_factory=BinanceConfig)
    polymarket: PolymarketConfig = field(default_factory=PolymarketConfig)
    chainlink: ChainlinkConfig = field(default_factory=ChainlinkConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)

    @classmethod
    def from_args(
        cls,
        mode: str = "simulation",
        size: float = 10.0,
        market_minutes: int = 5,
    ) -> "BotConfig":
        config = cls()
        config.mode = TradingMode(mode)
        config.trade_size = size
        config.execution.default_trade_size = size

        # Apply market-duration-specific tuning
        if market_minutes == 5:
            config.strategy.market_duration_seconds = 300
            config.strategy.momentum_lookback_seconds = 20
            config.strategy.late_period_activation_seconds = 90
            config.risk.no_trade_first_seconds = 10
            config.risk.no_trade_last_seconds = 15
            config.risk.max_hold_time_minutes = 4
        elif market_minutes == 15:
            config.strategy.market_duration_seconds = 900
            config.strategy.momentum_lookback_seconds = 60
            config.strategy.late_period_activation_seconds = 360
            config.risk.no_trade_first_seconds = 30
            config.risk.no_trade_last_seconds = 20
            config.risk.max_hold_time_minutes = 12
            # Restore 15min defaults
            config.strategy.momentum_threshold = 0.002
            config.strategy.scalping_entry_window_seconds = 0  # Disable scalping
        return config


# Optimization grid for 5-minute backtesting
OPTIMIZATION_GRID_5MIN = {
    "momentum_threshold": [0.0008, 0.001, 0.0012, 0.0015, 0.002],
    "momentum_lookback_seconds": [10, 15, 20, 30],
    "min_edge_threshold": [0.02, 0.03, 0.04, 0.05],
    "late_period_activation_seconds": [60, 75, 90, 120],
    "late_period_max_ask": [0.65, 0.70, 0.75, 0.80],
    "scalping_momentum_threshold": [0.0008, 0.001, 0.0012, 0.0015],
    "scalping_max_ask": [0.55, 0.58, 0.60, 0.62],
}

# Optimization grid for 15-minute backtesting (legacy)
OPTIMIZATION_GRID_15MIN = {
    "momentum_threshold": [0.001, 0.0015, 0.002, 0.003, 0.004],
    "momentum_lookback_seconds": [30, 45, 60, 90, 120],
    "min_edge_threshold": [0.02, 0.03, 0.04, 0.05],
    "late_period_activation_seconds": [240, 300, 360, 420],
    "late_period_max_ask": [0.65, 0.70, 0.75, 0.80],
}

# Default grid (5min)
OPTIMIZATION_GRID = OPTIMIZATION_GRID_5MIN
