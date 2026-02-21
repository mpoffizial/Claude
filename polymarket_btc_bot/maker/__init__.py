"""
polymarket_btc_bot.maker
========================
Maker-Rebate / Market-Making strategy for Polymarket 5m/15m BTC markets.

Public API
----------
MakerBot       – top-level async orchestrator
MakerBotConfig – configuration container (load via MakerBotConfig.from_env())

Quick start::

    from polymarket_btc_bot.maker import MakerBot, MakerBotConfig
    import asyncio

    config = MakerBotConfig.from_env()
    asyncio.run(MakerBot(config).run())
"""

from polymarket_btc_bot.maker.bot import MakerBot
from polymarket_btc_bot.maker.config import MakerBotConfig

__all__ = ["MakerBot", "MakerBotConfig"]
