"""
Polymarket Prediction Market Analyzer
Fetches and analyzes market probabilities for trading signals
"""

import requests
import pandas as pd
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import time


class PolymarketAnalyzer:
    """Analyze Polymarket prediction markets for trading opportunities"""

    BASE_URL = "https://gamma-api.polymarket.com"
    ENDPOINTS = {
        "markets": "/markets",
        "market_detail": "/markets/{market_id}",
        "trades": "/trades",
        "prices": "/prices"
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "PolymarketAnalyzer/1.0"
        })

    def get_markets(self, limit: int = 100, offset: int = 0) -> Optional[List[Dict]]:
        """
        Fetch active markets from Polymarket

        Args:
            limit: Number of markets to fetch
            offset: Pagination offset

        Returns:
            List of market dictionaries
        """
        try:
            params = {
                "limit": limit,
                "offset": offset,
                "active": True
            }
            response = self.session.get(f"{self.BASE_URL}{self.ENDPOINTS['markets']}",
                                       params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching markets: {e}")
            return None

    def get_market_by_id(self, market_id: str) -> Optional[Dict]:
        """
        Get detailed market information

        Args:
            market_id: Market ID from Polymarket

        Returns:
            Market detail dictionary
        """
        try:
            url = f"{self.BASE_URL}{self.ENDPOINTS['market_detail'].format(market_id=market_id)}"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching market {market_id}: {e}")
            return None

    def get_market_prices(self, market_id: str) -> Optional[Dict]:
        """
        Get current prices for a market

        Args:
            market_id: Market ID

        Returns:
            Price data for all outcomes
        """
        try:
            params = {"market_id": market_id}
            response = self.session.get(f"{self.BASE_URL}{self.ENDPOINTS['prices']}",
                                       params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ Error fetching prices: {e}")
            return None

    def search_markets(self, keyword: str, limit: int = 50) -> Optional[List[Dict]]:
        """
        Search markets by keyword

        Args:
            keyword: Search term
            limit: Max results

        Returns:
            Matching markets
        """
        try:
            params = {
                "search_term": keyword,
                "limit": limit
            }
            response = self.session.get(f"{self.BASE_URL}{self.ENDPOINTS['markets']}",
                                       params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"❌ Error searching markets: {e}")
            return None

    def analyze_market_opportunity(self, market: Dict) -> Dict:
        """
        Analyze a single market for trading opportunities

        Args:
            market: Market dictionary from API

        Returns:
            Analysis with opportunity score
        """
        analysis = {
            "market_id": market.get("id"),
            "question": market.get("question"),
            "outcomes": [],
            "liquidity": market.get("volume24h", 0),
            "expiration": market.get("endDate"),
            "opportunity_score": 0,
            "signals": []
        }

        # Analyze each outcome
        outcomes = market.get("outcomes", [])
        for outcome in outcomes:
            price = float(outcome.get("price", 0))
            prob = price * 100  # Price is probability in decimal

            # Extreme probabilities are opportunities
            if prob > 90 or prob < 10:
                analysis["signals"].append({
                    "type": "EXTREME_PROBABILITY",
                    "outcome": outcome.get("label"),
                    "probability": prob,
                    "opportunity": "Mispricing possible"
                })

            analysis["outcomes"].append({
                "label": outcome.get("label"),
                "probability": prob,
                "price": price,
                "yes_shares": outcome.get("yes_shares", 0),
                "no_shares": outcome.get("no_shares", 0)
            })

        # Calculate opportunity score
        if analysis["signals"]:
            analysis["opportunity_score"] = 75  # Strong signal

        # Check volume
        if analysis["liquidity"] > 100000:
            analysis["opportunity_score"] += 10  # Good liquidity bonus

        analysis["opportunity_score"] = min(100, analysis["opportunity_score"])

        return analysis

    def detect_probability_arbitrage(self, markets: List[Dict]) -> List[Dict]:
        """
        Detect arbitrage opportunities between related markets

        Args:
            markets: List of markets to analyze

        Returns:
            List of arbitrage opportunities
        """
        arbitrage_opportunities = []

        # Look for related markets with probability inconsistencies
        for i, market1 in enumerate(markets):
            for market2 in markets[i+1:]:
                # Check if markets are related
                if self._markets_related(market1.get("question"), market2.get("question")):
                    # Calculate combined probabilities
                    probs1 = [float(o.get("price", 0)) for o in market1.get("outcomes", [])]
                    probs2 = [float(o.get("price", 0)) for o in market2.get("outcomes", [])]

                    # If probabilities don't sum to ~1.0, there's an opportunity
                    for p1 in probs1:
                        for p2 in probs2:
                            combined = p1 + p2
                            if combined > 1.05 or combined < 0.95:  # Deviation tolerance
                                arbitrage_opportunities.append({
                                    "market_1": market1.get("question"),
                                    "market_2": market2.get("question"),
                                    "combined_probability": combined,
                                    "edge": abs(1.0 - combined) * 100
                                })

        return arbitrage_opportunities

    @staticmethod
    def _markets_related(question1: str, question2: str) -> bool:
        """Check if two market questions are related"""
        # Simple keyword matching for now
        keywords1 = set(question1.lower().split())
        keywords2 = set(question2.lower().split())
        common = keywords1 & keywords2
        return len(common) > 3

    def get_trending_markets(self, limit: int = 20) -> Optional[List[Dict]]:
        """
        Get trending/most active markets

        Returns:
            Top trending markets
        """
        try:
            markets = self.get_markets(limit=500)
            if not markets:
                return None

            # Sort by volume
            sorted_markets = sorted(markets,
                                   key=lambda m: m.get("volume24h", 0),
                                   reverse=True)
            return sorted_markets[:limit]
        except Exception as e:
            print(f"Error getting trending markets: {e}")
            return None


def format_market_analysis(analysis: Dict) -> str:
    """Format market analysis for display"""
    report = f"""
┌─ POLYMARKET ANALYSIS ──────────────────────────────────────────────────────────┐
│
│  Question: {analysis['question']}
│  Market ID: {analysis['market_id']}
│  Opportunity Score: {analysis['opportunity_score']:.0f}/100
│
│  OUTCOMES:
"""

    for outcome in analysis["outcomes"]:
        prob_bar = "█" * int(outcome["probability"] / 5) + "░" * (20 - int(outcome["probability"] / 5))
        report += f"│    {outcome['label']:<20} {prob_bar} {outcome['probability']:>6.1f}%\n"

    report += f"""│
│  Liquidity (24h):  ${analysis['liquidity']:,.0f}
│  Expires: {analysis['expiration']}
│
"""

    if analysis["signals"]:
        report += "│  ⚡ SIGNALS DETECTED:\n"
        for signal in analysis["signals"]:
            report += f"│    - {signal['type']}: {signal['outcome']} @ {signal['probability']:.1f}%\n"

    report += "│\n└────────────────────────────────────────────────────────────────────────────────────┘\n"

    return report


if __name__ == "__main__":
    analyzer = PolymarketAnalyzer()

    print("\n" + "="*80)
    print("🎲 POLYMARKET ANALYZER INITIALIZED")
    print("="*80 + "\n")

    # Test: Fetch trending markets
    print("[1/3] Fetching trending markets...")
    trending = analyzer.get_trending_markets(limit=10)

    if trending:
        print(f"✅ Found {len(trending)} trending markets\n")

        for i, market in enumerate(trending[:3], 1):
            print(f"[Market {i}]")
            analysis = analyzer.analyze_market_opportunity(market)
            print(format_market_analysis(analysis))

    else:
        print("⚠️  Could not fetch markets")
