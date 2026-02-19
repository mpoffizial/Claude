"""
Configuration for the Chainlink Lag Arbitrage strategy.

Parameters calibrated for Polymarket BTC Up/Down 15-minute markets
with <500 USD capital.
"""

from dataclasses import dataclass, field


@dataclass
class ChainlinkOnChainConfig:
    """Chainlink BTC/USD price feed on Polygon."""
    # Chainlink BTC/USD Aggregator on Polygon mainnet
    contract_address: str = "0xc907E116054Ad103354f2D350FD2514433D57F6F"
    # Polygon RPC endpoints (public, rate-limited)
    rpc_urls: list[str] = field(default_factory=lambda: [
        "https://polygon-rpc.com",
        "https://rpc.ankr.com/polygon",
        "https://polygon.llamarpc.com",
    ])
    poll_interval_seconds: float = 2.0  # Poll every 2s for fast lag detection
    decimals: int = 8  # Chainlink BTC/USD uses 8 decimals


@dataclass
class SpotConfig:
    """Binance spot price configuration."""
    ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
    reconnect_delay: float = 1.0
    max_reconnect_delay: float = 30.0


@dataclass
class LagThresholds:
    """Thresholds for lag detection and signal generation."""
    # Lag delta thresholds (USD difference between spot and chainlink)
    conservative_threshold: float = 5.0   # Fewer trades, higher win rate
    aggressive_threshold: float = 2.0     # More trades, more noise
    active_threshold: float = 5.0         # Currently active threshold

    # Timing windows (seconds before market resolution)
    entry_window_start: int = 60   # Start monitoring at T-60s
    entry_window_end: int = 10     # Stop entering at T-10s (execution buffer)
    signal_window: int = 45        # Generate signals starting at T-45s
    order_placement: int = 20      # Place orders at T-20s

    # Validation filters
    min_odds: float = 0.50         # Minimum Polymarket odds to consider
    max_odds: float = 0.85         # If odds > this, edge already priced in
    direction_lookback_seconds: int = 30  # Confirm direction over this window

    # Chainlink freshness
    max_chainlink_age_seconds: float = 120.0  # Reject if last update > 2 min ago
    chainlink_recent_update_seconds: float = 10.0  # Lag may be gone if updated recently


@dataclass
class RiskParams:
    """Risk parameters for <500 USD capital."""
    total_capital: float = 500.0
    min_position_usd: float = 1.0
    max_position_usd: float = 25.0          # 5% of capital
    max_position_pct: float = 0.05          # 5% per trade
    max_concurrent_positions: int = 1
    consecutive_loss_limit: int = 3          # Pause after 3 losses in a row
    pause_duration_seconds: int = 3600       # 1 hour pause
    daily_drawdown_limit_pct: float = 0.15  # 15% max daily loss
    min_expected_edge: float = 0.75         # Odds must be > 0.75 for clear signal


@dataclass
class LoggingConfig:
    """Configuration for the lag data logger."""
    output_dir: str = "lag_data"
    csv_filename: str = "lag_observations.csv"
    resolutions_filename: str = "market_resolutions.csv"
    sample_interval_seconds: float = 1.0    # Record price pair every 1s
    detailed_interval_seconds: float = 0.5  # In last 120s, record every 0.5s
    detailed_window_seconds: int = 120      # Detailed recording window


@dataclass
class LagArbitrageConfig:
    """Master configuration for the Chainlink Lag Arbitrage strategy."""
    chainlink: ChainlinkOnChainConfig = field(default_factory=ChainlinkOnChainConfig)
    spot: SpotConfig = field(default_factory=SpotConfig)
    thresholds: LagThresholds = field(default_factory=LagThresholds)
    risk: RiskParams = field(default_factory=RiskParams)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
