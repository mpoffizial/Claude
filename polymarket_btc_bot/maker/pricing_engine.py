"""
Pricing Engine: Calculates fair value and generates bid/ask quotes
for the Maker-Rebate strategy.

Quote generation flow:
  1. Compute microprice (size-weighted mid) from live orderbook
  2. Update rolling 60-second volatility estimate
  3. Set half-spread = clamp(vol-adjusted spread, min, max)
  4. Compute inventory skew (lean away from excess inventory)
  5. bid = (fair_value + skew) - half_spread
     ask = (fair_value + skew) + half_spread
  6. Clamp to [0.01, 0.99] and ensure bid < ask
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from polymarket_btc_bot.data.polymarket_clob import MarketOrderbook, OrderbookSnapshot
from polymarket_btc_bot.maker.config import MakerArbConfig, MakerQuoteConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class Quote:
    """A two-sided quote for a single outcome token."""

    token_id: str
    side_name: str           # "yes" or "no"
    bid_price: float
    ask_price: float
    bid_size_usdc: float     # USDC value placed at bid
    ask_size_usdc: float     # USDC value placed at ask
    fair_value: float        # Microprice at quote time
    half_spread: float       # Applied half-spread (before skew)
    skew_applied: float      # Net skew shift (positive = prices shifted up)
    generated_at: float      # Unix timestamp

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2.0

    @property
    def effective_half_spread(self) -> float:
        """Actual distance from fair value to bid/ask."""
        return (self.ask_price - self.bid_price) / 2.0


@dataclass
class MarketQuotes:
    """Quotes for both outcomes of a binary market (Yes and No)."""

    yes_quote: Optional[Quote] = None
    no_quote: Optional[Quote] = None
    timestamp: float = field(default_factory=time.time)

    @property
    def is_valid(self) -> bool:
        return self.yes_quote is not None and self.no_quote is not None

    @property
    def combined_ask(self) -> Optional[float]:
        """
        yes_ask + no_ask.  When < 1.0 there is a micro-arb opportunity:
        buy both sides for guaranteed $1 payout.
        """
        if not self.is_valid:
            return None
        return self.yes_quote.ask_price + self.no_quote.ask_price  # type: ignore[union-attr]

    @property
    def combined_bid(self) -> Optional[float]:
        """yes_bid + no_bid.  When > 1.0 we can sell both sides for guaranteed profit."""
        if not self.is_valid:
            return None
        return self.yes_quote.bid_price + self.no_quote.bid_price  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Pricing Engine
# ---------------------------------------------------------------------------

class PricingEngine:
    """
    Computes fair values and generates bid/ask quotes.

    Shared by all concurrent market strategies.
    Thread-safety note: all methods are synchronous; the caller (async strategy
    loop) ensures sequential access.
    """

    def __init__(self, quote_cfg: MakerQuoteConfig, arb_cfg: MakerArbConfig):
        self._qcfg = quote_cfg
        self._arb_cfg = arb_cfg
        # Rolling microprice history for volatility estimation
        self._vol_history: list[tuple[float, float]] = []   # (timestamp, microprice)
        self._volatility: float = 0.0                        # Avg absolute mid change/s

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def current_volatility(self) -> float:
        """Current 60-second rolling volatility estimate."""
        return self._volatility

    # ------------------------------------------------------------------
    # Fair value
    # ------------------------------------------------------------------

    def compute_microprice(self, book: OrderbookSnapshot) -> Optional[float]:
        """
        Size-weighted mid-price (microprice).

        Biases the mid toward whichever side has more top-of-book liquidity,
        giving a better estimate of where the 'true' price is.

        Returns None if the book is empty or crossed.
        """
        if not book.bids or not book.asks:
            return None

        best_bid = book.best_bid
        best_ask = book.best_ask
        bid_size = book.best_bid_size
        ask_size = book.best_ask_size

        if best_bid is None or best_ask is None or bid_size is None or ask_size is None:
            return None

        if best_bid >= best_ask:
            # Crossed book – fall back to simple mid
            logger.debug("Crossed book: bid=%.4f ask=%.4f", best_bid, best_ask)
            return round((best_bid + best_ask) / 2.0, 4)

        total = bid_size + ask_size
        if total <= 0:
            return round((best_bid + best_ask) / 2.0, 4)

        # Microprice: weight mid toward the side with more size
        microprice = (best_bid * ask_size + best_ask * bid_size) / total
        return round(microprice, 4)

    # ------------------------------------------------------------------
    # Spread
    # ------------------------------------------------------------------

    def compute_half_spread(self, book: OrderbookSnapshot) -> float:
        """
        Determine the half-spread for quoting.

        Base: half of the current market spread.
        Volatility add-on: 1.5× the rolling mean absolute change.
        Clamped to [min_half_spread, max_half_spread].
        """
        market_spread = book.spread
        if market_spread is None or market_spread <= 0:
            market_spread = self._qcfg.default_half_spread * 2.0

        half = market_spread / 2.0
        half += self._volatility * 1.5          # Widen in volatile conditions
        half = max(self._qcfg.min_half_spread, min(self._qcfg.max_half_spread, half))
        return round(half, 4)

    # ------------------------------------------------------------------
    # Inventory skew
    # ------------------------------------------------------------------

    def compute_skew(
        self,
        yes_inventory_usdc: float,
        no_inventory_usdc: float,
        side: str,
    ) -> float:
        """
        Compute a price skew based on current inventory imbalance.

        When we are net-long Yes (yes > no):
          - Yes quotes shift DOWN → we bid less aggressively and offer at lower ask
            (encourages takers to lift our Yes ask, reducing Yes inventory).
          - No quotes shift UP → we bid more aggressively for No
            (encourages takers to sell No to us, balancing inventory).

        Skew is clamped to ±max_skew.

        Args:
            yes_inventory_usdc: current USDC value of Yes tokens held
            no_inventory_usdc:  current USDC value of No tokens held
            side: "yes" or "no"

        Returns:
            Skew in probability units (positive = shift prices up).
        """
        net = yes_inventory_usdc - no_inventory_usdc   # positive = net long Yes
        factor = self._qcfg.skew_factor
        max_sk = self._qcfg.max_skew

        if side == "yes":
            skew = -net * factor   # Long Yes → push Yes prices down
        else:
            skew = net * factor    # Long Yes → push No prices up

        return max(-max_sk, min(max_sk, skew))

    # ------------------------------------------------------------------
    # Quote generation
    # ------------------------------------------------------------------

    def generate_quote(
        self,
        book: OrderbookSnapshot,
        token_id: str,
        side_name: str,
        yes_inventory_usdc: float,
        no_inventory_usdc: float,
    ) -> Optional[Quote]:
        """
        Generate a bid/ask quote for a single outcome token.

        Returns None if:
          - The book is empty / undetermined
          - Fair value is outside the safe quotable range [0.05, 0.95]
          - only_near_50 is set and the fair value is outside [0.35, 0.65]
          - Market spread is too tight (< min_market_spread) – risk of over-quoting
        """
        fv = self.compute_microprice(book)
        if fv is None:
            return None

        # Sanity range check
        if not (0.05 <= fv <= 0.95):
            logger.debug("Fair value %.4f outside [0.05, 0.95] for %s", fv, side_name)
            return None

        # Near-50 filter
        if self._qcfg.only_near_50:
            tol = self._qcfg.near_50_tolerance
            if not (0.50 - tol <= fv <= 0.50 + tol):
                logger.debug("Skipping %s: fv=%.4f outside near-50 band", side_name, fv)
                return None

        # Skip extremely tight markets (risk of adverse selection dominating)
        spread = book.spread
        if spread is not None and spread < self._qcfg.min_market_spread:
            logger.debug("Market spread %.4f too tight for %s", spread, side_name)
            return None

        half_spread = self.compute_half_spread(book)
        skew = self.compute_skew(yes_inventory_usdc, no_inventory_usdc, side_name)
        adjusted_fv = fv + skew

        bid = round(adjusted_fv - half_spread, 3)
        ask = round(adjusted_fv + half_spread, 3)

        # Clamp to valid probability range
        bid = max(0.01, min(0.97, bid))
        ask = max(0.02, min(0.99, ask))

        # Guarantee bid < ask with at least min_half_spread between them
        if bid >= ask:
            mid = (bid + ask) / 2.0
            bid = round(mid - self._qcfg.min_half_spread, 3)
            ask = round(mid + self._qcfg.min_half_spread, 3)
            bid = max(0.01, bid)
            ask = min(0.99, ask)

        return Quote(
            token_id=token_id,
            side_name=side_name,
            bid_price=bid,
            ask_price=ask,
            bid_size_usdc=self._qcfg.quote_size_usdc,
            ask_size_usdc=self._qcfg.quote_size_usdc,
            fair_value=fv,
            half_spread=half_spread,
            skew_applied=skew,
            generated_at=time.time(),
        )

    def generate_market_quotes(
        self,
        market_ob: MarketOrderbook,
        yes_token_id: str,
        no_token_id: str,
        yes_inventory_usdc: float,
        no_inventory_usdc: float,
    ) -> MarketQuotes:
        """Generate quotes for both outcome tokens of a binary market."""
        yes_q = self.generate_quote(
            market_ob.up_book, yes_token_id, "yes",
            yes_inventory_usdc, no_inventory_usdc,
        )
        no_q = self.generate_quote(
            market_ob.down_book, no_token_id, "no",
            yes_inventory_usdc, no_inventory_usdc,
        )
        return MarketQuotes(yes_quote=yes_q, no_quote=no_q, timestamp=time.time())

    # ------------------------------------------------------------------
    # Micro-arbitrage detection
    # ------------------------------------------------------------------

    def check_micro_arb(self, market_ob: MarketOrderbook) -> tuple[bool, float]:
        """
        Detect micro-arbitrage: buy Yes + buy No for < $1.00.

        In a binary market one outcome pays $1.  Buying both guarantees a $1
        payout.  If the combined ask < $1, the difference is risk-free profit
        (before execution costs).

        Returns:
            (is_opportunity, profit_per_unit)
        """
        yes_ask = market_ob.up_ask
        no_ask = market_ob.down_ask

        if yes_ask is None or no_ask is None:
            return False, 0.0

        combined = yes_ask + no_ask
        if combined < self._arb_cfg.threshold:
            return True, round(1.0 - combined, 5)

        return False, 0.0

    # ------------------------------------------------------------------
    # Re-quote decision
    # ------------------------------------------------------------------

    def quotes_need_refresh(
        self, old_quote: Optional[Quote], current_fv: float
    ) -> bool:
        """
        Decide whether existing orders should be cancelled and re-quoted.

        Triggers:
          - No existing quote → always refresh
          - Quote age >= max_quote_age_seconds
          - Fair value has moved >= requote_threshold
        """
        if old_quote is None:
            return True

        age = time.time() - old_quote.generated_at
        if age >= self._qcfg.max_quote_age_seconds:
            return True

        if abs(current_fv - old_quote.fair_value) >= self._qcfg.requote_threshold:
            return True

        return False

    # ------------------------------------------------------------------
    # Volatility estimation
    # ------------------------------------------------------------------

    def update_volatility(self, microprice: float) -> None:
        """
        Update the rolling 60-second volatility estimate from a new microprice.

        Volatility = mean absolute change between consecutive microprices
        in the trailing 60-second window.
        """
        now = time.time()
        self._vol_history.append((now, microprice))

        # Prune entries older than 60 seconds
        cutoff = now - 60.0
        self._vol_history = [(t, m) for t, m in self._vol_history if t >= cutoff]

        if len(self._vol_history) < 3:
            self._volatility = 0.0
            return

        mids = [m for _, m in self._vol_history]
        changes = [abs(mids[i] - mids[i - 1]) for i in range(1, len(mids))]
        self._volatility = sum(changes) / len(changes)
