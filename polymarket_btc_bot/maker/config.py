"""
Configuration for the Polymarket Maker-Rebate / Market-Making Bot.

Each dataclass covers one functional area:
  MakerQuoteConfig   – spread, size, requote logic, timing guards
  MakerInventoryConfig – inventory limits and skew parameters
  MakerRebateConfig  – Polymarket fee/rebate assumptions
  MakerRiskConfig    – hard daily/session limits
  MakerArbConfig     – micro-arb (Yes+No < 1) parameters
  MakerBotConfig     – top-level container with API credentials
"""

import os
from dataclasses import dataclass, field


@dataclass
class MakerQuoteConfig:
    """Quote generation parameters."""

    # --- Spread ---
    default_half_spread: float = 0.010   # $0.01 default half-spread on each side of fair value
    min_half_spread: float = 0.004       # Never quote tighter than this (edge floor)
    max_half_spread: float = 0.050       # Widen to this in volatile / illiquid markets

    # --- Quote sizing ---
    quote_size_usdc: float = 20.0        # USDC placed per order (bid or ask)
    min_quote_size_usdc: float = 5.0     # Orders below this size are skipped

    # --- Re-quote triggers ---
    requote_threshold: float = 0.005     # Re-quote if fair value moves >= 0.5 cents
    max_quote_age_seconds: float = 10.0  # Force re-quote after this age regardless

    # --- Market-filtering ---
    min_market_spread: float = 0.008     # Skip quoting when market spread is < 0.8%
    only_near_50: bool = True            # Restrict to near-50/50 markets
    near_50_tolerance: float = 0.15      # Quote if mid in [0.35, 0.65]

    # --- Timing guards (per market lifetime) ---
    no_quote_first_seconds: int = 15     # Sit out the first 15 s of each market
    no_quote_last_seconds: int = 45      # Pull quotes 45 s before market close

    # --- Skew cap (applied inside PricingEngine) ---
    max_skew: float = 0.040              # Maximum skew shift in probability units
    skew_factor: float = 0.080           # Skew per USDC of net inventory imbalance


@dataclass
class MakerInventoryConfig:
    """Inventory management parameters."""

    max_yes_inventory_usdc: float = 100.0   # Max USDC value of Yes tokens to hold
    max_no_inventory_usdc: float = 100.0    # Max USDC value of No tokens to hold
    max_net_exposure_usdc: float = 40.0     # Max |yes_usdc − no_usdc| at any time

    # --- Auto-hedge ---
    auto_hedge_enabled: bool = True
    hedge_trigger_usdc: float = 35.0        # Trigger hedge when net exposure exceeds this
    hedge_target_ratio: float = 0.5         # After hedge, target |net| = trigger * ratio


@dataclass
class MakerRebateConfig:
    """
    Polymarket rebate assumptions.

    Polymarket 15-minute markets: takers pay ~2 % fee.
    That fee pool is distributed to active makers proportionally
    to their share of maker volume.  The effective rebate rate varies
    but a conservative estimate is ~0.3–0.8 % of traded USDC.
    """

    taker_fee_rate: float = 0.020           # Fee takers pay (2%)
    estimated_rebate_rate: float = 0.005    # Conservative maker rebate estimate (0.5%)


@dataclass
class MakerRiskConfig:
    """Hard risk limits – reaching these halts or scales down the bot."""

    max_daily_loss_usdc: float = -50.0      # Halt at −$50 session loss
    max_total_inventory_usdc: float = 250.0 # Hard cap on total open inventory
    max_markets_simultaneously: int = 3     # Quote in at most N markets at once
    max_orders_per_minute: int = 120        # API rate limit
    min_fill_to_track_usdc: float = 0.50    # Ignore fills below this value


@dataclass
class MakerArbConfig:
    """Micro-arbitrage parameters (buy Yes + No for < $1)."""

    enabled: bool = True
    threshold: float = 0.980               # Trigger arb when yes_ask + no_ask < this
    min_profit_usdc: float = 0.005         # Minimum absolute profit to bother executing
    size_usdc: float = 15.0                # USDC per arb leg


@dataclass
class MakerBotConfig:
    """
    Top-level configuration for the Maker-Rebate Bot.

    Aggregates all sub-configs plus Polymarket API credentials
    (loaded from environment variables) and operational parameters.
    """

    quote: MakerQuoteConfig = field(default_factory=MakerQuoteConfig)
    inventory: MakerInventoryConfig = field(default_factory=MakerInventoryConfig)
    rebate: MakerRebateConfig = field(default_factory=MakerRebateConfig)
    risk: MakerRiskConfig = field(default_factory=MakerRiskConfig)
    arb: MakerArbConfig = field(default_factory=MakerArbConfig)

    # --- Polymarket endpoints ---
    clob_ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    clob_rest_url: str = "https://clob.polymarket.com"
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137  # Polygon mainnet

    # --- API credentials (from environment) ---
    private_key: str = field(default_factory=lambda: os.getenv("POLY_PRIVATE_KEY", ""))
    api_key: str = field(default_factory=lambda: os.getenv("POLY_API_KEY", ""))
    api_secret: str = field(default_factory=lambda: os.getenv("POLY_API_SECRET", ""))
    api_passphrase: str = field(default_factory=lambda: os.getenv("POLY_API_PASSPHRASE", ""))
    proxy_wallet: str = field(default_factory=lambda: os.getenv("POLY_PROXY_WALLET", ""))

    # --- Operational ---
    simulation_mode: bool = True           # True = no real orders placed
    loop_interval_seconds: float = 1.0     # Monitoring loop cadence

    @classmethod
    def from_env(cls) -> "MakerBotConfig":
        """
        Build a MakerBotConfig from environment variables.

        Recognised env vars (all optional, have defaults):
          MAKER_SIMULATION    – "true"/"false"   (default: true)
          MAKER_QUOTE_SIZE    – float USDC        (default: 20.0)
          MAKER_SPREAD        – float half-spread (default: 0.01)
          MAKER_MAX_EXPOSURE  – float USDC        (default: 40.0)
          MAKER_MAX_LOSS      – float USDC        (default: -50.0)
        """
        cfg = cls()
        cfg.simulation_mode = os.getenv("MAKER_SIMULATION", "true").lower() == "true"
        cfg.quote.quote_size_usdc = float(os.getenv("MAKER_QUOTE_SIZE", str(cfg.quote.quote_size_usdc)))
        cfg.quote.default_half_spread = float(os.getenv("MAKER_SPREAD", str(cfg.quote.default_half_spread)))
        cfg.inventory.max_net_exposure_usdc = float(
            os.getenv("MAKER_MAX_EXPOSURE", str(cfg.inventory.max_net_exposure_usdc))
        )
        cfg.risk.max_daily_loss_usdc = float(
            os.getenv("MAKER_MAX_LOSS", str(cfg.risk.max_daily_loss_usdc))
        )
        return cfg
