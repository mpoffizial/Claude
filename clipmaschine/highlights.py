"""Top-Clips finden und bewerten: welche Highlights gehen gerade viral?

Zwei Quellen:
  • Twitch-API (mit TWITCH_CLIENT_ID/SECRET) – genau, inkl. Spiel und VOD-Position
  • öffentliche Twitch-Clip-Seite (ohne Zugangsdaten, über yt-dlp) – keine Entwickler-App nötig
Fällt die API aus, wird automatisch die öffentliche Seite genutzt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .config import AppConfig, StreamerConfig
from .db import Database
from .downloader import DownloadError, QuietYtDlpLogger, clip_id_from_ref, short_error
from .models import Clip, utcnow
from .twitch_api import TwitchAPI, TwitchAPIError

log = logging.getLogger(__name__)


def velocity_score(view_count: int, created_at: datetime, now: datetime, gravity: float) -> float:
    """Views pro Zeit mit "Schwerkraft" (wie bei Hacker News): frische, schnell wachsende Clips gewinnen."""
    age_hours = max((now - created_at).total_seconds() / 3600.0, 0.0)
    return view_count / ((age_hours + 2.0) ** gravity)


def same_moment(a: Clip, b: Clip, window_seconds: float) -> bool:
    """Zeigen zwei Clips vermutlich denselben Moment? (Viele Zuschauer clippen dasselbe.)"""
    if a.broadcaster_id != b.broadcaster_id:
        return False
    if a.video_id and a.video_id == b.video_id and a.vod_offset is not None and b.vod_offset is not None:
        return abs(a.vod_offset - b.vod_offset) < window_seconds
    return abs((a.created_at - b.created_at).total_seconds()) < window_seconds


def dedupe_moments(scored: list[tuple[Clip, float]], window_seconds: float) -> list[tuple[Clip, float]]:
    kept: list[tuple[Clip, float]] = []
    for clip, score in sorted(scored, key=lambda item: item[1], reverse=True):
        if any(same_moment(clip, other, window_seconds) for other, _ in kept):
            continue
        kept.append((clip, score))
    return kept


def passes_filters(clip: Clip, streamer: StreamerConfig, cfg: AppConfig) -> bool:
    d = cfg.discovery
    min_views = streamer.min_views if streamer.min_views is not None else d.min_views
    if clip.view_count < min_views:
        return False
    if not d.min_duration <= clip.duration <= d.max_duration:
        return False
    if d.languages and clip.language and clip.language.lower() not in {l.lower() for l in d.languages}:
        return False
    title = clip.title.lower()
    if any(word.lower() in title for word in d.exclude_keywords):
        return False
    return True


# ------------------------------------------------------------ öffentliche Seite
def _list_public_clips(url: str, limit: int) -> list[dict]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise DownloadError("yt-dlp ist nicht installiert: pip install yt-dlp") from exc
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "playlistend": limit,
            "socket_timeout": 30, "logger": QuietYtDlpLogger()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:  # yt-dlp wirft eigene Fehlertypen
        raise DownloadError(f"Twitch-Clip-Seite nicht lesbar: {short_error(exc)}") from exc
    return [entry for entry in (info.get("entries") or []) if entry]


def public_period(lookback_hours: float) -> str:
    if lookback_hours <= 24:
        return "24hr"
    if lookback_hours <= 24 * 7:
        return "7d"
    if lookback_hours <= 24 * 30:
        return "30d"
    return "all"


def clip_from_public_entry(entry: dict, login: str) -> Clip | None:
    url = entry.get("url") or ""
    try:
        slug = clip_id_from_ref(url)
    except ValueError:
        return None
    timestamp = entry.get("timestamp")
    if not timestamp:
        return None
    return Clip(
        id=slug, url=url, broadcaster_id=f"login:{login}", broadcaster_login=login,
        broadcaster_name=entry.get("channel") or entry.get("uploader") or login,
        title=(entry.get("title") or "").strip(), view_count=int(entry.get("view_count") or 0),
        created_at=datetime.fromtimestamp(float(timestamp), timezone.utc),
        duration=float(entry.get("duration") or 0), language=entry.get("language") or "",
    )


def fetch_public_clips(login: str, lookback_hours: float, limit: int, now: datetime) -> list[Clip]:
    """Top-Clips eines Kanals von der öffentlichen Twitch-Clip-Seite (ohne Zugangsdaten)."""
    url = f"https://www.twitch.tv/{login}/clips?filter=clips&range={public_period(lookback_hours)}"
    since = now - timedelta(hours=lookback_hours)
    clips = (clip_from_public_entry(entry, login) for entry in _list_public_clips(url, limit))
    return [clip for clip in clips if clip and clip.created_at >= since]


class ClipDiscovery:
    def __init__(self, cfg: AppConfig, api: TwitchAPI | None, db: Database):
        self.cfg = cfg
        self.api = api
        self.db = db
        self._users: dict[str, dict] = {}

    def resolve_streamers(self) -> dict[str, dict]:
        missing = [s.login for s in self.cfg.streamers if s.login not in self._users]
        if missing and self.api is not None:
            found = self.api.get_users(logins=missing)
            self._users.update(found)
            for login in missing:
                if login not in found:
                    log.warning("Twitch-Kanal '%s' nicht gefunden – Tippfehler in config.yaml?", login)
        return self._users

    def _candidates(self, streamer: StreamerConfig, now: datetime) -> list[Clip]:
        d = self.cfg.discovery
        if self.api is not None:
            try:
                user = self.resolve_streamers().get(streamer.login)
                if not user:
                    return []
                raw = self.api.get_clips(user["id"], now - timedelta(hours=d.lookback_hours), now,
                                         limit=d.fetch_limit, featured_only=d.featured_only)
                games = self.api.game_names([c.get("game_id", "") for c in raw])
                return [Clip.from_helix(data, streamer.login, games.get(str(data.get("game_id") or ""), ""))
                        for data in raw]
            except TwitchAPIError as exc:
                log.warning("Twitch-API für %s nicht nutzbar (%s) – nutze die öffentliche Clip-Seite",
                            streamer.login, exc)
        return fetch_public_clips(streamer.login, d.lookback_hours, d.fetch_limit, now)

    def discover(self, now: datetime | None = None) -> list[tuple[Clip, float]]:
        """Holt die aktuellen Top-Clips aller Streamer und speichert neue Kandidaten."""
        now = now or utcnow()
        d = self.cfg.discovery
        stored: list[tuple[Clip, float]] = []
        for streamer in self.cfg.streamers:
            try:
                clips = self._candidates(streamer, now)
            except Exception as exc:
                log.error("Clips von %s konnten nicht geladen werden: %s", streamer.login, exc)
                continue

            candidates = [(clip, velocity_score(clip.view_count, clip.created_at, now, d.gravity))
                          for clip in clips if passes_filters(clip, streamer, self.cfg)]
            new = 0
            for clip, score in dedupe_moments(candidates, d.dedupe_window_seconds):
                if self.db.get_clip(clip.id):
                    self.db.upsert_clip(clip, "top_clips", score)   # Views/Score aktualisieren
                    continue
                if new >= d.max_new_per_streamer or self.db.has_overlapping(clip, d.dedupe_window_seconds):
                    continue
                self.db.upsert_clip(clip, "top_clips", score)
                stored.append((clip, score))
                new += 1
            log.info("%s: %d Clips geprüft, %d neue Highlights", streamer.login, len(clips), new)
        return stored
