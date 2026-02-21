"""
TradeDatabase: SQLite-basiertes Logging für alle Market-Making-Aktivitäten.

Tabellen:
──────────────────────────────────────────────────────────────────────────────
  orders       — Alle platzierten Orders (Bid + Ask)
  fills        — Alle ausgeführten Order-Fills
  market_pnl   — PnL pro aufgelöstem Markt
  sessions     — Session-Übersicht (Start, Ende, Gesamt-PnL)
  fair_values  — Historische Fair-Value-Berechnungen (für Analyse)
──────────────────────────────────────────────────────────────────────────────

Verwendung:
    db = TradeDatabase("trades.db")
    db.initialize()

    # Order loggen
    db.log_order(order_id="abc123", ...)

    # Fill loggen
    db.log_fill(order_id="abc123", ...)

    # PnL abfragen
    stats = db.get_session_stats()
──────────────────────────────────────────────────────────────────────────────
"""

import logging
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionStats:
    """Statistiken einer Trading-Session."""

    session_id: int
    start_time: float
    end_time: Optional[float]
    total_orders: int
    total_fills: int
    total_bid_fills: int
    total_ask_fills: int
    realized_pnl: float
    estimated_spread_income: float
    total_volume_usdc: float
    markets_traded: int

    @property
    def duration_minutes(self) -> float:
        end = self.end_time or time.time()
        return (end - self.start_time) / 60.0

    @property
    def win_rate(self) -> float:
        """Anteil der vollständigen Roundtrips (BID + ASK gefüllt)."""
        if self.total_fills == 0:
            return 0.0
        roundtrips = min(self.total_bid_fills, self.total_ask_fills)
        return roundtrips / max(self.total_fills / 2, 1)


class TradeDatabase:
    """
    SQLite-Datenbank für Trade-Logging und Analyse.

    Thread-sicher durch connection-per-context-manager Ansatz.

    Alle Zeitstempel werden als Unix-Timestamp (float) gespeichert.
    """

    # Schema-Version für Migrationskontrolle
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str = "trades.db"):
        """
        Args:
            db_path: Pfad zur SQLite-Datenbankdatei
        """
        self.db_path = db_path
        self._current_session_id: Optional[int] = None

        # Sicherstellen dass der Verzeichnispfad existiert
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Context Manager für sichere Datenbankverbindungen."""
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")   # Write-Ahead Logging für Performance
        conn.execute("PRAGMA synchronous=NORMAL") # Balance zwischen Sicherheit und Speed
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error("Datenbankfehler: %s", e)
            raise
        finally:
            conn.close()

    def initialize(self):
        """
        Initialisiere Datenbankschema (erstelle Tabellen wenn nötig).

        Idempotent — kann mehrfach aufgerufen werden.
        """
        with self._conn() as conn:
            conn.executescript("""
                -- Schema-Version
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL
                );

                -- Sessions: Eine pro Bot-Start
                CREATE TABLE IF NOT EXISTS sessions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at      REAL NOT NULL,
                    ended_at        REAL,
                    capital_usdc    REAL NOT NULL,
                    mode            TEXT NOT NULL,       -- 'simulation', 'paper', 'live'
                    base_spread     REAL NOT NULL,
                    config_json     TEXT,
                    notes           TEXT
                );

                -- Orders: Alle platzierten Orders (auch unerfüllte)
                CREATE TABLE IF NOT EXISTS orders (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      INTEGER NOT NULL,
                    order_id        TEXT NOT NULL UNIQUE,
                    clob_order_id   TEXT,
                    market_id       TEXT NOT NULL,
                    token_id        TEXT NOT NULL,
                    mm_side         TEXT NOT NULL,       -- 'bid' oder 'ask'
                    order_side      TEXT NOT NULL,       -- 'BUY' oder 'SELL'
                    price           REAL NOT NULL,
                    size_tokens     REAL NOT NULL,
                    size_usdc       REAL NOT NULL,
                    fair_value      REAL NOT NULL,
                    half_spread     REAL NOT NULL,
                    inventory_skew  REAL DEFAULT 0.0,
                    status          TEXT NOT NULL,       -- 'open', 'filled', 'cancelled', 'failed'
                    placed_at       REAL NOT NULL,
                    updated_at      REAL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                -- Fills: Ausgeführte Order-Fills
                CREATE TABLE IF NOT EXISTS fills (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      INTEGER NOT NULL,
                    order_id        TEXT NOT NULL,
                    market_id       TEXT NOT NULL,
                    mm_side         TEXT NOT NULL,
                    filled_tokens   REAL NOT NULL,
                    fill_price      REAL NOT NULL,
                    fill_value_usdc REAL NOT NULL,
                    btc_price_at_fill REAL,
                    est_spread_income REAL DEFAULT 0.0,
                    filled_at       REAL NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                -- Markt-PnL: Ergebnis nach Marktauflösung
                CREATE TABLE IF NOT EXISTS market_pnl (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      INTEGER NOT NULL,
                    market_id       TEXT NOT NULL,
                    resolved_at     REAL NOT NULL,
                    up_won          INTEGER NOT NULL,   -- 1=Up, 0=Down
                    yes_tokens      REAL DEFAULT 0.0,
                    yes_cost_usdc   REAL DEFAULT 0.0,
                    no_tokens       REAL DEFAULT 0.0,
                    no_revenue_usdc REAL DEFAULT 0.0,
                    gross_pnl       REAL NOT NULL,
                    net_pnl         REAL NOT NULL,      -- Nach Gebühren
                    fee_paid        REAL DEFAULT 0.0,
                    spread_income   REAL DEFAULT 0.0,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                -- Fair Values: Historische FV-Berechnungen (für Backtesting-Analyse)
                CREATE TABLE IF NOT EXISTS fair_values (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      INTEGER NOT NULL,
                    market_id       TEXT NOT NULL,
                    timestamp       REAL NOT NULL,
                    fair_value      REAL NOT NULL,
                    ema5            REAL,
                    ema15           REAL,
                    atr             REAL,
                    momentum_adj    REAL,
                    time_factor     REAL,
                    confidence      REAL,
                    btc_price       REAL,
                    opening_price   REAL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                -- Indizes für schnelle Abfragen
                CREATE INDEX IF NOT EXISTS idx_orders_session   ON orders(session_id);
                CREATE INDEX IF NOT EXISTS idx_orders_market    ON orders(market_id);
                CREATE INDEX IF NOT EXISTS idx_fills_session    ON fills(session_id);
                CREATE INDEX IF NOT EXISTS idx_fills_market     ON fills(market_id);
                CREATE INDEX IF NOT EXISTS idx_pnl_session      ON market_pnl(session_id);
                CREATE INDEX IF NOT EXISTS idx_fv_session       ON fair_values(session_id);
                CREATE INDEX IF NOT EXISTS idx_fv_market        ON fair_values(market_id);
            """)

            # Schema-Version eintragen wenn noch nicht vorhanden
            conn.execute(
                "INSERT OR IGNORE INTO schema_version VALUES (?, ?)",
                (self.SCHEMA_VERSION, time.time())
            )

        logger.info("Datenbank initialisiert: %s", self.db_path)

    # ──────────────────────────────────────────────────────────────────────────
    # Session-Verwaltung
    # ──────────────────────────────────────────────────────────────────────────

    def start_session(
        self,
        capital_usdc: float,
        mode: str,
        base_spread: float,
        config_json: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        """
        Starte eine neue Trading-Session.

        Args:
            capital_usdc:  Startkapital
            mode:          'simulation', 'paper', oder 'live'
            base_spread:   Konfigurierter Basis-Spread
            config_json:   Optionale Konfiguration als JSON-String
            notes:         Notizen zur Session

        Returns:
            Session-ID
        """
        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO sessions
                   (started_at, capital_usdc, mode, base_spread, config_json, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (time.time(), capital_usdc, mode, base_spread, config_json, notes)
            )
            session_id = cursor.lastrowid

        self._current_session_id = session_id
        logger.info(
            "Session gestartet: ID=%d | Kapital=$%.2f | Modus=%s",
            session_id, capital_usdc, mode
        )
        return session_id

    def end_session(self, session_id: Optional[int] = None):
        """Beende die aktuelle Session."""
        sid = session_id or self._current_session_id
        if not sid:
            return

        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (time.time(), sid)
            )

        logger.info("Session beendet: ID=%d", sid)

        if sid == self._current_session_id:
            self._current_session_id = None

    # ──────────────────────────────────────────────────────────────────────────
    # Order-Logging
    # ──────────────────────────────────────────────────────────────────────────

    def log_order(
        self,
        order_id: str,
        market_id: str,
        token_id: str,
        mm_side: str,
        order_side: str,
        price: float,
        size_tokens: float,
        fair_value: float,
        half_spread: float,
        inventory_skew: float = 0.0,
        clob_order_id: Optional[str] = None,
        session_id: Optional[int] = None,
    ):
        """
        Logge eine neu platzierte Order.

        Args:
            order_id:       Interne Order-ID (UUID)
            market_id:      Polymarket Markt-ID
            token_id:       YES-Token ID
            mm_side:        'bid' oder 'ask'
            order_side:     'BUY' oder 'SELL'
            price:          Limit-Preis [0.0–1.0]
            size_tokens:    Anzahl Token
            fair_value:     Berechneter Fair Value zum Platzierungszeitpunkt
            half_spread:    Halber Spread zum Platzierungszeitpunkt
            inventory_skew: Angewendetes Inventory Skewing
            clob_order_id:  Polymarket CLOB Order ID (nach Bestätigung)
            session_id:     Session-ID (Standard: aktuelle Session)
        """
        sid = session_id or self._current_session_id
        if not sid:
            logger.warning("Kein Session-ID für Order-Logging: %s", order_id[:16])
            return

        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO orders
                   (session_id, order_id, clob_order_id, market_id, token_id,
                    mm_side, order_side, price, size_tokens, size_usdc,
                    fair_value, half_spread, inventory_skew, status, placed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid, order_id, clob_order_id, market_id, token_id,
                    mm_side, order_side, price, size_tokens, price * size_tokens,
                    fair_value, half_spread, inventory_skew, "open", time.time()
                )
            )

    def update_order_status(self, order_id: str, status: str, clob_order_id: Optional[str] = None):
        """Aktualisiere den Status einer Order."""
        with self._conn() as conn:
            if clob_order_id:
                conn.execute(
                    "UPDATE orders SET status = ?, clob_order_id = ?, updated_at = ? WHERE order_id = ?",
                    (status, clob_order_id, time.time(), order_id)
                )
            else:
                conn.execute(
                    "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?",
                    (status, time.time(), order_id)
                )

    # ──────────────────────────────────────────────────────────────────────────
    # Fill-Logging
    # ──────────────────────────────────────────────────────────────────────────

    def log_fill(
        self,
        order_id: str,
        market_id: str,
        mm_side: str,
        filled_tokens: float,
        fill_price: float,
        btc_price: Optional[float] = None,
        est_spread_income: float = 0.0,
        session_id: Optional[int] = None,
    ):
        """
        Logge einen Order-Fill.

        Args:
            order_id:          Order-ID
            market_id:         Markt-ID
            mm_side:           'bid' oder 'ask'
            filled_tokens:     Ausgeführte Token-Anzahl
            fill_price:        Ausführungspreis
            btc_price:         Aktueller BTC-Preis zum Fill-Zeitpunkt
            est_spread_income: Geschätztes Spread-Einkommen
            session_id:        Session-ID
        """
        sid = session_id or self._current_session_id
        if not sid:
            return

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO fills
                   (session_id, order_id, market_id, mm_side, filled_tokens,
                    fill_price, fill_value_usdc, btc_price_at_fill,
                    est_spread_income, filled_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid, order_id, market_id, mm_side, filled_tokens,
                    fill_price, fill_price * filled_tokens, btc_price,
                    est_spread_income, time.time()
                )
            )

        # Order-Status aktualisieren
        self.update_order_status(order_id, "filled")

    # ──────────────────────────────────────────────────────────────────────────
    # Markt-PnL-Logging
    # ──────────────────────────────────────────────────────────────────────────

    def log_market_resolution(
        self,
        market_id: str,
        up_won: bool,
        yes_tokens: float,
        yes_cost_usdc: float,
        no_tokens: float,
        no_revenue_usdc: float,
        net_pnl: float,
        fee_paid: float = 0.0,
        spread_income: float = 0.0,
        session_id: Optional[int] = None,
    ):
        """
        Logge das Ergebnis eines aufgelösten Marktes.

        Args:
            market_id:      Markt-ID
            up_won:         True wenn BTC gestiegen (YES gewonnen)
            yes_tokens:     Gehaltene YES-Token
            yes_cost_usdc:  Bezahltes USDC für YES-Token
            no_tokens:      Leerverkaufte YES-Token
            no_revenue_usdc: Einnahmen aus Leerverkäufen
            net_pnl:        Netto-PnL nach Gebühren
            fee_paid:       Bezahlte Gebühren
            spread_income:  Geschätztes Spread-Einkommen aus Roundtrips
            session_id:     Session-ID
        """
        sid = session_id or self._current_session_id
        if not sid:
            return

        gross_pnl = net_pnl + fee_paid

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO market_pnl
                   (session_id, market_id, resolved_at, up_won,
                    yes_tokens, yes_cost_usdc, no_tokens, no_revenue_usdc,
                    gross_pnl, net_pnl, fee_paid, spread_income)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid, market_id, time.time(), int(up_won),
                    yes_tokens, yes_cost_usdc, no_tokens, no_revenue_usdc,
                    gross_pnl, net_pnl, fee_paid, spread_income
                )
            )

        logger.info(
            "Markt-PnL geloggt [%s]: %s | PnL=$%.4f | SpreadEink=$%.4f",
            market_id[:20],
            "UP" if up_won else "DOWN",
            net_pnl,
            spread_income,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Fair-Value-Logging
    # ──────────────────────────────────────────────────────────────────────────

    def log_fair_value(
        self,
        market_id: str,
        fair_value: float,
        ema5: Optional[float],
        ema15: Optional[float],
        atr: Optional[float],
        momentum_adj: float,
        time_factor: float,
        confidence: float,
        btc_price: float,
        opening_price: float,
        session_id: Optional[int] = None,
    ):
        """Logge eine Fair-Value-Berechnung (für spätere Analyse)."""
        sid = session_id or self._current_session_id
        if not sid:
            return

        with self._conn() as conn:
            conn.execute(
                """INSERT INTO fair_values
                   (session_id, market_id, timestamp, fair_value, ema5, ema15,
                    atr, momentum_adj, time_factor, confidence, btc_price, opening_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid, market_id, time.time(), fair_value, ema5, ema15,
                    atr, momentum_adj, time_factor, confidence, btc_price, opening_price
                )
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Abfragen / Analytics
    # ──────────────────────────────────────────────────────────────────────────

    def get_session_stats(self, session_id: Optional[int] = None) -> Optional[SessionStats]:
        """
        Hole aggregierte Statistiken für eine Session.

        Args:
            session_id: Session-ID (Standard: aktuelle Session)

        Returns:
            SessionStats oder None wenn Session nicht gefunden
        """
        sid = session_id or self._current_session_id
        if not sid:
            return None

        with self._conn() as conn:
            # Session-Basis
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (sid,)
            ).fetchone()

            if not row:
                return None

            # Aggregierte Order-Statistiken
            order_stats = conn.execute(
                """SELECT
                    COUNT(*) as total_orders,
                    SUM(CASE WHEN mm_side = 'bid' THEN 1 ELSE 0 END) as bid_orders,
                    SUM(CASE WHEN mm_side = 'ask' THEN 1 ELSE 0 END) as ask_orders
                   FROM orders WHERE session_id = ?""",
                (sid,)
            ).fetchone()

            # Aggregierte Fill-Statistiken
            fill_stats = conn.execute(
                """SELECT
                    COUNT(*) as total_fills,
                    SUM(CASE WHEN mm_side = 'bid' THEN 1 ELSE 0 END) as bid_fills,
                    SUM(CASE WHEN mm_side = 'ask' THEN 1 ELSE 0 END) as ask_fills,
                    SUM(fill_value_usdc) as total_volume,
                    SUM(est_spread_income) as spread_income
                   FROM fills WHERE session_id = ?""",
                (sid,)
            ).fetchone()

            # Realisiertes PnL
            pnl_stats = conn.execute(
                """SELECT
                    SUM(net_pnl) as realized_pnl,
                    COUNT(DISTINCT market_id) as markets_traded
                   FROM market_pnl WHERE session_id = ?""",
                (sid,)
            ).fetchone()

        return SessionStats(
            session_id=sid,
            start_time=row["started_at"],
            end_time=row["ended_at"],
            total_orders=order_stats["total_orders"] or 0,
            total_fills=fill_stats["total_fills"] or 0,
            total_bid_fills=fill_stats["bid_fills"] or 0,
            total_ask_fills=fill_stats["ask_fills"] or 0,
            realized_pnl=pnl_stats["realized_pnl"] or 0.0,
            estimated_spread_income=fill_stats["spread_income"] or 0.0,
            total_volume_usdc=fill_stats["total_volume"] or 0.0,
            markets_traded=pnl_stats["markets_traded"] or 0,
        )

    def get_recent_fills(self, limit: int = 20, session_id: Optional[int] = None) -> list[dict]:
        """
        Hole die letzten N Fills.

        Returns:
            Liste von Fill-Dictionaries (neueste zuerst)
        """
        sid = session_id or self._current_session_id

        with self._conn() as conn:
            if sid:
                rows = conn.execute(
                    """SELECT * FROM fills WHERE session_id = ?
                       ORDER BY filled_at DESC LIMIT ?""",
                    (sid, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM fills ORDER BY filled_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()

        return [dict(row) for row in rows]

    def get_market_pnl_history(self, session_id: Optional[int] = None) -> list[dict]:
        """
        Hole PnL-Geschichte aller aufgelösten Märkte.

        Returns:
            Liste von PnL-Einträgen (neueste zuerst)
        """
        sid = session_id or self._current_session_id

        with self._conn() as conn:
            if sid:
                rows = conn.execute(
                    "SELECT * FROM market_pnl WHERE session_id = ? ORDER BY resolved_at DESC",
                    (sid,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM market_pnl ORDER BY resolved_at DESC"
                ).fetchall()

        return [dict(row) for row in rows]

    def get_all_time_pnl(self) -> dict:
        """Hole Gesamt-PnL über alle Sessions."""
        with self._conn() as conn:
            result = conn.execute(
                """SELECT
                    COUNT(DISTINCT session_id) as total_sessions,
                    COUNT(*) as total_markets,
                    SUM(net_pnl) as total_pnl,
                    SUM(spread_income) as total_spread_income,
                    AVG(net_pnl) as avg_pnl_per_market,
                    SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as profitable_markets,
                    SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) as losing_markets
                FROM market_pnl"""
            ).fetchone()

        if result:
            return dict(result)
        return {}

    def export_csv(self, output_path: str, table: str = "fills"):
        """
        Exportiere eine Tabelle als CSV-Datei.

        Args:
            output_path:  Ausgabepfad (z.B. 'fills_export.csv')
            table:        Tabellenname ('fills', 'orders', 'market_pnl')
        """
        import csv

        valid_tables = {"fills", "orders", "market_pnl", "fair_values", "sessions"}
        if table not in valid_tables:
            raise ValueError(f"Ungültige Tabelle: {table}. Erlaubt: {valid_tables}")

        with self._conn() as conn:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()

        if not rows:
            logger.info("Keine Daten in Tabelle '%s' für CSV-Export", table)
            return

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(rows[0].keys())  # Header
            writer.writerows([tuple(r) for r in rows])

        logger.info("CSV-Export: %d Zeilen aus '%s' → %s", len(rows), table, output_path)
