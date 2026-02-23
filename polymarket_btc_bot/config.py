"""
Configuration for the Polymarket BTC 15-Minute Trading Bot.
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
    # CoinGecko simple price API (returns JSON, no auth required)
    api_url: str = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    poll_interval_seconds: float = 10.0


@dataclass
class StrategyConfig:
    # Lag Arbitrage (Layer 1)
    momentum_threshold: float = 0.002       # 0.2% BTC move in lookback window
    momentum_lookback_seconds: int = 60     # Look back 60s for momentum calc
    max_ask_price_lag: float = 0.62         # Only buy if ask < this
    min_edge_ratio: float = 0.05            # (1-ask)/ask must exceed this

    # Intra-Market Arbitrage (Layer 2)
    arb_threshold: float = 0.975            # up_ask + down_ask < this triggers arb
    arb_min_profit: float = 0.005           # Min profit after fees for arb

    # Late-Period Momentum (Layer 3)
    late_period_activation_seconds: int = 360   # Activate in last 6 minutes
    late_period_price_deviation: float = 0.001  # 0.1% deviation from opening
    late_period_max_ask: float = 0.80           # Only buy if ask < this

    # Signal Aggregation Weights
    lag_weight: float = 0.5
    arb_weight: float = 0.3
    late_weight: float = 0.2


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

    # Timing
    no_trade_first_seconds: int = 30             # No trades in first 30s
    no_trade_last_seconds: int = 20              # No new trades in last 20s
    max_hold_time_minutes: int = 12              # Exit if no profit after 12min

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
    # Simulation fill model: probability that a MM limit order actually fills.
    # Uses price-distance decay: fill_prob = sim_base_fill_rate * exp(-distance/0.05)
    # where distance = max(0, fair_value - limit_price).
    # Set to 1.0 for instant 100 % fill (original behaviour).
    sim_base_fill_rate: float = 1.0


@dataclass
class MarketMakerConfig:
    """
    Configuration for the limit-order ladder market-making strategy.

    The market maker continuously maintains a staircase of buy limit orders
    on both sides (UP / DOWN) throughout the full 5- or 15-minute window.
    It cancels stale orders and replaces them whenever the mid-price drifts
    beyond the reprice threshold.
    """
    enabled: bool = True

    # --- Order ladder ---
    num_levels: int = 3                          # Levels per side (UP + DOWN)
    level_spacing: float = 0.02                  # Price gap between levels (e.g. 0.02 = 2 cents)
    size_per_level: float = 5.0                  # USDC per limit order level
    # First level is placed this far below the current best ask
    first_level_offset: float = 0.01             # 1 cent inside the spread

    # --- Timing ---
    refresh_interval_seconds: float = 30.0       # Re-evaluate orders every 30 s
    max_order_age_seconds: float = 60.0          # Force-cancel orders older than this
    min_time_remaining: float = 90.0             # Stop placing in last 90 s of market
    start_after_seconds: float = 30.0            # Wait before placing first orders

    # --- Repricing ---
    reprice_threshold: float = 0.01              # Reprice if mid moved >= 1 cent since last quote
    max_active_orders: int = 12                  # Hard cap on total open MM orders

    # --- Risk ---
    max_position_per_side: float = 30.0          # Max total USDC exposure per side
    min_ask_price: float = 0.10                  # Never buy above 90 % probability
    max_ask_price: float = 0.90                  # Never buy below 10 % probability


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
    market_maker: MarketMakerConfig = field(default_factory=MarketMakerConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)

    @classmethod
    def from_args(cls, mode: str = "simulation", size: float = 10.0) -> "BotConfig":
        config = cls()
        config.mode = TradingMode(mode)
        config.trade_size = size
        config.execution.default_trade_size = size
        return config


# Optimization grid for backtesting
OPTIMIZATION_GRID = {
    "momentum_threshold": [0.001, 0.0015, 0.002, 0.003, 0.004],
    "momentum_lookback_seconds": [30, 45, 60, 90, 120],
    "min_edge_threshold": [0.02, 0.03, 0.04, 0.05],
    "late_period_activation_seconds": [240, 300, 360, 420],
    "late_period_max_ask": [0.65, 0.70, 0.75, 0.80],
}
