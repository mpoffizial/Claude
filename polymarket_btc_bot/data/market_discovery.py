"""
Auto-discovers the currently active BTC Up/Down 5-minute market on Polymarket.

Discovery-Strategie (drei Stufen):
1. Lokaler Index aus backtest_data/polymarket_btc_markets_30d.json
   → Blitzschnell, deckt heute und die nächsten Stunden ab
2. Gamma API ID-Scan (vorwärts von letzter bekannter ID)
   → Für Märkte jenseits des lokalen Index
3. CLOB API direkte Abfrage per conditionId
   → Zum Verifizieren und Abrufen aktueller Token-Preise

Markteigenschaften:
- Frage: "Bitcoin Up or Down - February 22, 9:50AM-9:55AM ET"
- feeType: crypto_15_min (Polymarket-interner Name)
- Tatsächliche Dauer: 5 Minuten
- eventStartTime → endDate = genau 5 Minuten Abstand
- restricted: True (daher nicht in allgemeinen API-Listings)
"""

import asyncio
import datetime
import json as _json
import logging
import os
import time
import urllib.request as _urllib_req
from dataclasses import dataclass
from typing import Optional

from polymarket_btc_bot.config import PolymarketConfig

logger = logging.getLogger(__name__)

# Pfad zur vorgeladenen Backtest-Datei
_BACKTEST_FILE = os.path.join("backtest_data", "polymarket_btc_markets_30d.json")

# Wie viele Gamma-IDs wir beim Scan nach vorne/hinten probieren
_GAMMA_SCAN_RANGE = 50


@dataclass
class MarketInfo:
    condition_id: str
    question: str
    market_slug: str
    up_token_id: str
    down_token_id: str
    start_timestamp: int        # Unix timestamp: eventStartTime
    end_timestamp: int          # Unix timestamp: endDate (start + 300s)
    gamma_id: Optional[int] = None
    opening_price: Optional[float] = None  # Chainlink BTC price at open

    @property
    def duration_seconds(self) -> int:
        return self.end_timestamp - self.start_timestamp

    @property
    def time_remaining(self) -> float:
        return max(0.0, self.end_timestamp - time.time())

    @property
    def time_elapsed(self) -> float:
        return max(0.0, time.time() - self.start_timestamp)

    @property
    def is_active(self) -> bool:
        now = time.time()
        return self.start_timestamp <= now <= self.end_timestamp

    @property
    def is_expired(self) -> bool:
        return time.time() > self.end_timestamp


class MarketDiscovery:
    """
    Discovers and tracks active BTC 5-minute Up/Down markets on Polymarket.

    Diese Märkte sind `restricted: True` und tauchen nicht in den allgemeinen
    API-Listings auf. Wir nutzen einen lokalen Index + Gamma-ID-Scan.
    """

    # Intervall der BTC Up/Down Märkte in Sekunden
    MARKET_INTERVAL = 300  # 5 Minuten

    def __init__(self, config: PolymarketConfig):
        self.config = config
        self._current_market: Optional[MarketInfo] = None
        self._next_market: Optional[MarketInfo] = None

        # Lokaler Index: start_timestamp → MarketInfo
        self._local_index: dict[int, MarketInfo] = {}
        # Letzter bekannter Gamma-ID-Bereich (für Scan)
        self._last_known_gamma_id: Optional[int] = None

    async def start(self):
        """Lade lokalen Index aus Backtest-Datei."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._build_local_index)
        logger.info(
            "MarketDiscovery started | Lokaler Index: %d Märkte",
            len(self._local_index),
        )

    async def stop(self):
        logger.info("MarketDiscovery stopped")

    @property
    def current_market(self) -> Optional[MarketInfo]:
        return self._current_market

    @property
    def next_market(self) -> Optional[MarketInfo]:
        return self._next_market

    # ------------------------------------------------------------------ #
    # Lokaler Index                                                        #
    # ------------------------------------------------------------------ #

    def _build_local_index(self):
        """
        Liest polymarket_btc_markets_30d.json und baut einen Lookup
        start_timestamp → MarketInfo auf.
        """
        if not os.path.exists(_BACKTEST_FILE):
            logger.warning(
                "Backtest-Datei nicht gefunden: %s. Überspringe lokalen Index.", _BACKTEST_FILE
            )
            return

        try:
            with open(_BACKTEST_FILE, encoding="utf-8") as fh:
                raw_markets = _json.load(fh)
        except Exception as e:
            logger.error("Fehler beim Laden der Backtest-Datei: %s", e)
            return

        loaded = 0
        for raw in raw_markets:
            info = self._parse_gamma_market(raw)
            if info:
                self._local_index[info.start_timestamp] = info
                if self._last_known_gamma_id is None or (
                    info.gamma_id and info.gamma_id > self._last_known_gamma_id
                ):
                    self._last_known_gamma_id = info.gamma_id
                loaded += 1

        logger.debug("Lokaler Index aufgebaut: %d / %d Einträge geladen", loaded, len(raw_markets))

    def _parse_gamma_market(self, raw: dict) -> Optional["MarketInfo"]:
        """Parst einen Eintrag aus der Gamma-API-Antwort oder dem Backtest-File."""
        try:
            question = raw.get("question", "")
            if "Bitcoin Up or Down" not in question:
                return None

            condition_id = raw.get("conditionId", "")
            if not condition_id:
                return None

            # Token-IDs
            clob_ids_raw = raw.get("clobTokenIds", "[]")
            if isinstance(clob_ids_raw, str):
                try:
                    clob_ids = _json.loads(clob_ids_raw)
                except Exception:
                    import ast
                    clob_ids = ast.literal_eval(clob_ids_raw)
            else:
                clob_ids = clob_ids_raw

            if not clob_ids or len(clob_ids) < 2:
                return None

            # Up/Down Token — Outcomes prüfen
            outcomes_raw = raw.get("outcomes", '["Up", "Down"]')
            if isinstance(outcomes_raw, str):
                outcomes = _json.loads(outcomes_raw)
            else:
                outcomes = outcomes_raw

            up_token, down_token = clob_ids[0], clob_ids[1]
            for i, o in enumerate(outcomes):
                if "up" in o.lower():
                    up_token = clob_ids[i]
                    down_token = clob_ids[1 - i]
                    break

            # Zeitstempel aus eventStartTime und endDate
            event_start_str = raw.get("eventStartTime", "")
            end_date_str = raw.get("endDate", "")

            if not event_start_str or not end_date_str:
                return None

            start_ts = int(
                datetime.datetime.fromisoformat(
                    event_start_str.replace("Z", "+00:00")
                ).timestamp()
            )
            end_ts = int(
                datetime.datetime.fromisoformat(
                    end_date_str.replace("Z", "+00:00")
                ).timestamp()
            )

            # Slug aus events-Feld holen
            slug = ""
            events_raw = raw.get("events", [])
            if isinstance(events_raw, str):
                try:
                    events_list = _json.loads(events_raw.replace("'", '"'))
                except Exception:
                    events_list = []
            else:
                events_list = events_raw

            if events_list:
                slug = events_list[0].get("slug", "") or events_list[0].get("ticker", "")

            gamma_id_raw = raw.get("id")
            gamma_id = int(gamma_id_raw) if gamma_id_raw else None

            return MarketInfo(
                condition_id=condition_id,
                question=question,
                market_slug=slug,
                up_token_id=str(up_token),
                down_token_id=str(down_token),
                start_timestamp=start_ts,
                end_timestamp=end_ts,
                gamma_id=gamma_id,
            )

        except Exception as e:
            logger.debug("Fehler beim Parsen eines Markt-Eintrags: %s", e)
            return None

    # ------------------------------------------------------------------ #
    # HTTP-Hilfsfunktion (synchron, urllib)                               #
    # ------------------------------------------------------------------ #

    def _http_get_json(self, url: str, timeout: int = 10):
        """Synchroner HTTP GET via urllib (funktioniert ohne asyncio DNS)."""
        try:
            req = _urllib_req.Request(
                url,
                headers={
                    "User-Agent": "polymarket-mm-bot/1.0",
                    "Accept": "application/json",
                },
            )
            with _urllib_req.urlopen(req, timeout=timeout) as resp:
                return _json.loads(resp.read())
        except Exception as e:
            logger.debug("HTTP GET fehlgeschlagen (%s): %s", url[:70], e)
            return None

    # ------------------------------------------------------------------ #
    # Gamma API ID-Scan                                                    #
    # ------------------------------------------------------------------ #

    def _scan_gamma_for_btc_market(
        self, target_start_ts: int, scan_range: int = _GAMMA_SCAN_RANGE
    ) -> Optional[MarketInfo]:
        """
        Scannt Gamma-API-IDs rund um _last_known_gamma_id, um den Markt
        mit passendem eventStartTime zu finden.
        """
        if self._last_known_gamma_id is None:
            logger.warning("Kein Gamma-ID-Ankerpunkt bekannt — überspringe Scan")
            return None

        base_id = self._last_known_gamma_id
        logger.debug(
            "Gamma-Scan: Suche Markt für ts=%d, Basis-ID=%d, Range=±%d",
            target_start_ts,
            base_id,
            scan_range,
        )

        # Zuerst vorwärts (neuere Märkte), dann rückwärts
        ids_to_check = list(range(base_id + 1, base_id + scan_range + 1)) + list(
            range(base_id, base_id - scan_range, -1)
        )

        found = None
        for gid in ids_to_check:
            url = f"{self.config.gamma_api_url}/markets/{gid}"
            data = self._http_get_json(url, timeout=5)
            if not data or "Bitcoin Up or Down" not in data.get("question", ""):
                continue

            info = self._parse_gamma_market(data)
            if info is None:
                continue

            # Update letzter bekannter ID
            if info.gamma_id and info.gamma_id > (self._last_known_gamma_id or 0):
                self._last_known_gamma_id = info.gamma_id

            # Zum Index hinzufügen
            self._local_index[info.start_timestamp] = info

            if info.start_timestamp == target_start_ts:
                found = info
                logger.info(
                    "Gamma-Scan: Markt gefunden (ID=%d): %s",
                    gid,
                    info.question,
                )
                break

        return found

    # ------------------------------------------------------------------ #
    # CLOB-Verifizierung                                                  #
    # ------------------------------------------------------------------ #

    def _verify_via_clob(self, info: MarketInfo) -> Optional[MarketInfo]:
        """
        Prüft ob der Markt im CLOB noch aktiv und orders-accepting ist.
        Aktualisiert Token-IDs aus dem CLOB falls nötig.
        """
        url = f"{self.config.clob_rest_url}/markets/{info.condition_id}"
        data = self._http_get_json(url, timeout=8)
        if not data:
            return info  # Behalte vorhandene Info

        if not data.get("active") or not data.get("accepting_orders"):
            logger.warning(
                "CLOB: Markt %s ist nicht (mehr) aktiv/accepting",
                info.condition_id[:20],
            )
            return None

        # Token-IDs aus CLOB aktualisieren (zuverlässiger als Gamma)
        tokens = data.get("tokens", [])
        for t in tokens:
            outcome = t.get("outcome", "").lower()
            token_id = str(t.get("token_id", ""))
            if "up" in outcome:
                info.up_token_id = token_id
            elif "down" in outcome:
                info.down_token_id = token_id

        return info

    # ------------------------------------------------------------------ #
    # Haupt-Discovery-Logik                                               #
    # ------------------------------------------------------------------ #

    def _current_slot_ts(self) -> int:
        """Gibt den Unix-Timestamp des aktuellen 5-Minuten-Slots zurück."""
        now = int(time.time())
        return (now // self.MARKET_INTERVAL) * self.MARKET_INTERVAL

    async def discover_current_market(self) -> Optional[MarketInfo]:
        """Findet den aktuell aktiven BTC 5min Up/Down Markt."""
        slot_ts = self._current_slot_ts()

        # Stufe 1: Lokaler Index
        info = self._local_index.get(slot_ts)
        if info:
            logger.debug("Lokaler Index: Markt für ts=%d gefunden", slot_ts)
        else:
            # Stufe 2: Gamma-Scan
            logger.info(
                "Lokaler Index: ts=%d nicht gefunden — starte Gamma-Scan", slot_ts
            )
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(
                None, self._scan_gamma_for_btc_market, slot_ts
            )

        if info is None:
            logger.warning(
                "Kein BTC Up/Down Markt für ts=%d (%s UTC) gefunden",
                slot_ts,
                datetime.datetime.utcfromtimestamp(slot_ts).strftime("%H:%M"),
            )
            return None

        # Stufe 3: CLOB-Verifizierung (async via Executor)
        loop = asyncio.get_event_loop()
        verified = await loop.run_in_executor(None, self._verify_via_clob, info)

        if verified is None:
            return None

        self._current_market = verified
        logger.info(
            "Aktiver Markt: %s | %.0fs verbleibend",
            verified.question,
            verified.time_remaining,
        )
        return self._current_market

    async def discover_next_market(self) -> Optional[MarketInfo]:
        """Lädt den nächsten (noch nicht begonnenen) Markt vor."""
        if self._current_market is None:
            return None

        next_slot_ts = self._current_market.end_timestamp
        info = self._local_index.get(next_slot_ts)

        if info is None:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(
                None, self._scan_gamma_for_btc_market, next_slot_ts
            )

        if info:
            self._next_market = info
            logger.info(
                "Nächster Markt: %s (startet in %.0fs)",
                info.question,
                info.start_timestamp - time.time(),
            )

        return self._next_market

    async def fetch_market_details(self, condition_id: str):
        """Fetch detailed market info from CLOB API."""
        url = f"{self.config.clob_rest_url}/markets/{condition_id}"
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._http_get_json, url)

    async def run_discovery_loop(self, callback=None):
        """Continuously discover and track markets."""
        logger.info("Starting market discovery loop")

        while True:
            try:
                current = await self.discover_current_market()

                if current and callback:
                    await callback(current)

                # Pre-fetch next market 60s before current ends
                if current and current.time_remaining < 60:
                    await self.discover_next_market()

                # If current market expired, swap to next
                if current and current.is_expired:
                    if self._next_market and self._next_market.is_active:
                        self._current_market = self._next_market
                        self._next_market = None
                        if callback:
                            await callback(self._current_market)

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Discovery loop error: %s", e)
                await asyncio.sleep(10)
