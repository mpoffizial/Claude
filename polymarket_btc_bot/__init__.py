"""
Polymarket BTC Trading Bot

Vollständige Trading-Bot-Suite für Polymarket BTC Up/Down Märkte.
Enthält zwei Haupt-Strategien:

1. DIREKTIONALE STRATEGIEN (main.py):
   - Chainlink Lag Arbitrage: Nutzt Orderbook-Verzögerung vs. Spot-Preis
   - Intra-Market Arbitrage: Risikofreie Arb wenn P(Up)+P(Down) < 1
   - Late-Period Momentum: Nutzt Fehlbewertung in den letzten Minuten

2. MARKET MAKING (mm_main.py):
   - Kontinuierliches Quoten beider Seiten (BID + ASK) mit definiertem Spread
   - Fair Value Berechnung via EMA5/15, ATR-Volatilität
   - Inventory Skewing bei einseitigen Positionen
   - Kelly Criterion für Positionsgrößen
   - SQLite Trade-Logging

Verwendung:
    # Direktionale Strategie:
    python -m polymarket_btc_bot.main --mode simulation

    # Market Making:
    python -m polymarket_btc_bot.mm_main --config config.yaml
    python -m polymarket_btc_bot.mm_main --mode simulation
    python -m polymarket_btc_bot.mm_main --mode live --capital 100
"""

__version__ = "0.2.0"
