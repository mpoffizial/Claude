"""SQLite-Zustand: welche Clips gefunden, gerendert und wo gepostet wurden."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .models import Clip, iso, parse_time, utcnow

# Upload-Status, die als "erledigt" zählen (kein erneuter Upload, zählt fürs Tageslimit)
SUCCESS_STATUSES = ("posted", "inbox", "submitted")
FINAL_STATUSES = SUCCESS_STATUSES + ("dry_run",)

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    broadcaster_id TEXT,
    broadcaster_login TEXT,
    broadcaster_name TEXT,
    title TEXT,
    game_name TEXT,
    language TEXT,
    view_count INTEGER DEFAULT 0,
    duration REAL DEFAULT 0,
    created_at TEXT NOT NULL,
    video_id TEXT,
    vod_offset INTEGER,
    source TEXT,
    score REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'discovered',
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_after TEXT,
    context_json TEXT,
    video_path TEXT,
    rendered_path TEXT,
    meta_json TEXT,
    error TEXT,
    discovered_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status, score DESC);
CREATE TABLE IF NOT EXISTS uploads (
    clip_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,
    remote_id TEXT,
    url TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (clip_id, platform)
);
"""


class Database:
    """Dünne, threadsichere Hülle um SQLite (Live-Modus schreibt aus einem eigenen Thread)."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if str(path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Ergänzt Spalten, die in älteren Datenbank-Versionen fehlen."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(clips)")}
        for name, ddl in (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("retry_after", "TEXT"),
                          ("context_json", "TEXT")):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE clips ADD COLUMN {name} {ddl}")
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(uploads)")}
        for name, ddl in (("review_status", "TEXT"), ("review_reason", "TEXT"), ("review_at", "TEXT"),
                          ("review_attempts", "INTEGER NOT NULL DEFAULT 0")):
            if name not in columns:
                self._conn.execute(f"ALTER TABLE uploads ADD COLUMN {name} {ddl}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _query(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple | list = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    # ------------------------------------------------------------------ Clips
    def get_clip(self, clip_id: str) -> sqlite3.Row | None:
        rows = self._query("SELECT * FROM clips WHERE id = ?", (clip_id,))
        return rows[0] if rows else None

    def upsert_clip(self, clip: Clip, source: str, score: float, context: dict | None = None) -> bool:
        """Speichert einen neuen Clip. Bei bekannten, noch offenen Clips werden Views/Score aktualisiert.

        Gibt True zurück, wenn der Clip neu war.
        """
        now = iso(utcnow())
        with self._lock:
            row = self.get_clip(clip.id)
            if row is None:
                self._conn.execute(
                    """INSERT INTO clips (id, url, broadcaster_id, broadcaster_login, broadcaster_name, title,
                           game_name, language, view_count, duration, created_at, video_id, vod_offset, source,
                           score, status, context_json, discovered_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?, ?)""",
                    (clip.id, clip.url, clip.broadcaster_id, clip.broadcaster_login, clip.broadcaster_name,
                     clip.title, clip.game_name, clip.language, clip.view_count, clip.duration,
                     iso(clip.created_at), clip.video_id, clip.vod_offset, source, score,
                     json.dumps(context or {}, ensure_ascii=False), now, now),
                )
                return True
            if row["status"] == "discovered":
                # Hype-Clips behalten ihren Bonus; Top-Clips bekommen den aktuellen Score
                new_score = max(score, row["score"] or 0) if row["source"] == "chat_hype" else score
                self._conn.execute(
                    "UPDATE clips SET view_count = ?, score = ?, updated_at = ? WHERE id = ?",
                    (max(clip.view_count, row["view_count"] or 0), new_score, now, clip.id),
                )
            return False

    def has_overlapping(self, clip: Clip, window_seconds: float) -> bool:
        """Gibt es schon einen Clip vom selben Moment (anderer Zuschauer hat dasselbe geclippt)?"""
        if clip.video_id and clip.vod_offset is not None:
            rows = self._query(
                """SELECT 1 FROM clips WHERE id != ? AND broadcaster_id = ? AND video_id = ?
                   AND vod_offset IS NOT NULL AND ABS(vod_offset - ?) < ?
                   AND status NOT IN ('failed', 'skipped') LIMIT 1""",
                (clip.id, clip.broadcaster_id, clip.video_id, clip.vod_offset, window_seconds),
            )
        else:
            rows = self._query(
                """SELECT 1 FROM clips WHERE id != ? AND broadcaster_id = ?
                   AND ABS(julianday(created_at) - julianday(?)) * 86400.0 < ?
                   AND status NOT IN ('failed', 'skipped') LIMIT 1""",
                (clip.id, clip.broadcaster_id, iso(clip.created_at), window_seconds),
            )
        return bool(rows)

    def count_from_broadcaster_since(self, broadcaster_id: str, since: datetime) -> int:
        return self._query("SELECT COUNT(*) AS n FROM clips WHERE broadcaster_id = ? AND discovered_at >= ?",
                           (broadcaster_id, iso(since)))[0]["n"]

    def mark(self, clip_id: str, status: str, **fields) -> None:
        allowed = {"video_path", "rendered_path", "meta_json", "error", "score"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unbekannte Felder: {unknown}")
        sets = ["status = ?", "updated_at = ?"] + [f"{name} = ?" for name in fields]
        params = [status, iso(utcnow()), *fields.values(), clip_id]
        self._execute(f"UPDATE clips SET {', '.join(sets)} WHERE id = ?", params)

    def clips_to_prepare(self, limit: int, min_created_at: datetime) -> list[sqlite3.Row]:
        if limit <= 0:
            return []
        return self._query(
            """SELECT * FROM clips WHERE status = 'discovered' AND created_at >= ?
               AND (retry_after IS NULL OR retry_after <= ?) ORDER BY score DESC LIMIT ?""",
            (iso(min_created_at), iso(utcnow()), limit),
        )

    def prepare_failed(self, clip_id: str, error: str, max_attempts: int, backoff_minutes: float = 5) -> bool:
        """Fehler beim Vorbereiten: später erneut versuchen (mit wachsender Pause) oder aufgeben.

        Gibt True zurück, wenn der Clip endgültig aufgegeben wurde.
        """
        with self._lock:
            row = self.get_clip(clip_id)
            if row is None:
                return True
            attempts = (row["attempts"] or 0) + 1
            if attempts >= max_attempts:
                self._conn.execute("UPDATE clips SET status = 'failed', attempts = ?, error = ?, updated_at = ? "
                                   "WHERE id = ?", (attempts, error, iso(utcnow()), clip_id))
                return True
            retry = utcnow() + timedelta(minutes=backoff_minutes * attempts)
            self._conn.execute("UPDATE clips SET status = 'discovered', attempts = ?, error = ?, retry_after = ?, "
                               "updated_at = ? WHERE id = ?", (attempts, error, iso(retry), iso(utcnow()), clip_id))
            return False

    def clips_with_status(self, status: str) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM clips WHERE status = ? ORDER BY score DESC", (status,))

    def count_with_status(self, status: str) -> int:
        return self._query("SELECT COUNT(*) AS n FROM clips WHERE status = ?", (status,))[0]["n"]

    def expire_older_than(self, min_created_at: datetime) -> list[sqlite3.Row]:
        """Markiert offene Clips, die zu alt zum Posten sind, als 'expired' und gibt sie zurück."""
        with self._lock:
            rows = self._query(
                "SELECT * FROM clips WHERE status IN ('discovered', 'rendered') AND created_at < ?",
                (iso(min_created_at),),
            )
            for row in rows:
                self.mark(row["id"], "expired")
            return rows

    # ---------------------------------------------------------------- Uploads
    def next_for_platform(self, platform: str, max_attempts: int, min_created_at: datetime) -> sqlite3.Row | None:
        rows = self._query(
            """SELECT c.* FROM clips c
               LEFT JOIN uploads u ON u.clip_id = c.id AND u.platform = ?
               WHERE c.status = 'rendered' AND c.created_at >= ?
                 AND (u.clip_id IS NULL OR (u.status = 'failed' AND u.attempts < ?))
               ORDER BY c.score DESC LIMIT 1""",
            (platform, iso(min_created_at), max_attempts),
        )
        return rows[0] if rows else None

    def record_upload(self, clip_id: str, platform: str, status: str, remote_id: str = "", url: str = "",
                      error: str = "", count_attempt: bool = True) -> None:
        now = iso(utcnow())
        increment = 1 if count_attempt else 0
        self._execute(
            """INSERT INTO uploads (clip_id, platform, status, remote_id, url, error, attempts, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(clip_id, platform) DO UPDATE SET
                   status = excluded.status, remote_id = excluded.remote_id, url = excluded.url,
                   error = excluded.error, attempts = uploads.attempts + ?, updated_at = excluded.updated_at""",
            (clip_id, platform, status, remote_id, url, error, increment, now, now, increment),
        )

    def give_up_upload(self, clip_id: str, platform: str, attempts: int) -> None:
        """Keine weiteren Versuche für diese Plattform (nicht behebbarer Fehler)."""
        self._execute("UPDATE uploads SET attempts = MAX(attempts, ?) WHERE clip_id = ? AND platform = ?",
                      (attempts, clip_id, platform))

    # ------------------------------------------------- Weiterempfehlungs-Check
    def uploads_to_review(self, platform: str, posted_before: datetime, posted_after: datetime,
                          max_attempts: int = 3, limit: int = 10) -> list[sqlite3.Row]:
        """Gepostete Videos, deren Empfehlungs-Status noch nicht (abschließend) geprüft wurde."""
        return self._query(
            """SELECT u.clip_id, u.updated_at, c.meta_json, c.title FROM uploads u JOIN clips c ON c.id = u.clip_id
               WHERE u.platform = ? AND u.status IN ('posted', 'submitted')
                 AND (u.review_status IS NULL OR u.review_status IN ('pending', 'unknown'))
                 AND u.review_attempts < ? AND u.updated_at <= ? AND u.updated_at >= ?
               ORDER BY u.updated_at LIMIT ?""",
            (platform, max_attempts, iso(posted_before), iso(posted_after), limit),
        )

    def set_review(self, clip_id: str, platform: str, status: str, reason: str = "") -> None:
        # updated_at bleibt unberührt – es ist der Post-Zeitpunkt für Tageslimit und Mindestabstand
        self._execute(
            """UPDATE uploads SET review_status = ?, review_reason = ?, review_at = ?,
                   review_attempts = review_attempts + 1 WHERE clip_id = ? AND platform = ?""",
            (status, reason, iso(utcnow()), clip_id, platform),
        )

    def restricted_review_times(self, platform: str, since: datetime) -> list[datetime]:
        rows = self._query(
            """SELECT review_at FROM uploads WHERE platform = ? AND review_status = 'restricted'
               AND review_at >= ? ORDER BY review_at DESC""", (platform, iso(since)))
        return [parse_time(row["review_at"]) for row in rows]

    def upload_statuses(self, clip_id: str) -> dict[str, sqlite3.Row]:
        return {row["platform"]: row for row in self._query("SELECT * FROM uploads WHERE clip_id = ?", (clip_id,))}

    def posts_since(self, platform: str, since: datetime) -> int:
        placeholders = ",".join("?" * len(SUCCESS_STATUSES))
        return self._query(
            f"SELECT COUNT(*) AS n FROM uploads WHERE platform = ? AND status IN ({placeholders}) AND updated_at >= ?",
            (platform, *SUCCESS_STATUSES, iso(since)),
        )[0]["n"]

    def last_post_at(self, platform: str) -> datetime | None:
        placeholders = ",".join("?" * len(SUCCESS_STATUSES))
        rows = self._query(
            f"SELECT MAX(updated_at) AS t FROM uploads WHERE platform = ? AND status IN ({placeholders})",
            (platform, *SUCCESS_STATUSES),
        )
        value = rows[0]["t"] if rows else None
        return parse_time(value) if value else None

    def post_times_since(self, platform: str, since: datetime) -> list[datetime]:
        """Zeitpunkte erfolgreicher Posts seit `since`, neueste zuerst."""
        placeholders = ",".join("?" * len(SUCCESS_STATUSES))
        rows = self._query(
            f"""SELECT updated_at FROM uploads WHERE platform = ? AND status IN ({placeholders})
                AND updated_at >= ? ORDER BY updated_at DESC""",
            (platform, *SUCCESS_STATUSES, iso(since)),
        )
        return [parse_time(row["updated_at"]) for row in rows]

    # ------------------------------------------------------------- Übersicht
    def stats(self) -> dict:
        clips = {row["status"]: row["n"] for row in self._query("SELECT status, COUNT(*) AS n FROM clips GROUP BY status")}
        uploads: dict[str, dict[str, int]] = {}
        for row in self._query("SELECT platform, status, COUNT(*) AS n FROM uploads GROUP BY platform, status"):
            uploads.setdefault(row["platform"], {})[row["status"]] = row["n"]
        return {"clips": clips, "uploads": uploads}

    def recent_uploads(self, limit: int = 10) -> list[sqlite3.Row]:
        return self._query(
            """SELECT u.*, c.title, c.broadcaster_login FROM uploads u JOIN clips c ON c.id = u.clip_id
               ORDER BY u.updated_at DESC LIMIT ?""",
            (limit,),
        )

    def recent_clips(self, limit: int = 10) -> list[sqlite3.Row]:
        return self._query("SELECT * FROM clips ORDER BY updated_at DESC LIMIT ?", (limit,))


def hours_ago(now: datetime, hours: float) -> datetime:
    return now - timedelta(hours=hours)
