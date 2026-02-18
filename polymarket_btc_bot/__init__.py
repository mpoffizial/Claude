"""
Polymarket BTC 15-Minute Trading Bot

A fully automated trading bot for Polymarket's BTC Up/Down 15-minute
prediction markets. Implements three strategy layers:

1. Chainlink Lag Arbitrage - Exploits orderbook delay vs spot price
2. Intra-Market Arbitrage - Risk-free arb when P(Up)+P(Down) < 1
3. Late-Period Momentum - Capitalizes on mispricing in final minutes

Usage:
    python -m polymarket_btc_bot.main --mode simulation
    python -m polymarket_btc_bot.main --mode paper --size 10
    python -m polymarket_btc_bot.main --mode live --size 50
"""

__version__ = "0.1.0"
