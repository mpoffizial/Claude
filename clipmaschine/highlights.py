"""Top-Clips finden und bewerten: welche Highlights gehen gerade viral?

Zwei Quellen:
  • Twitch-API (mit TWITCH_CLIENT_ID/SECRET) – genau, inkl. Spiel und VOD-Position
  • öffentliche Twitch-Clip-Seite (ohne Zugangsdaten, über yt-dlp) – keine Entwickler-App nötig
Fällt die API aus, wird automatisch die öffentliche Seite genutzt.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from .config import AppConfig, StreamerConfig
from .db import Database
from .downloader import DownloadError, QuietYtDlpLogger, clip_id_from_ref, short_error
from .models import Clip, utcnow
from .scheduler import local_timezone
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


# ------------------------------------------------------------ Spiele-Namen
_ROMAN = {"1": "i", "2": "ii", "3": "iii", "4": "iv", "5": "v", "6": "vi", "7": "vii", "8": "viii",
          "9": "ix", "10": "x"}


def normalize_game_name(name: str) -> str:
    """'GTA 6' und 'Grand Theft Auto VI' → 'grandtheftautovi' (zum Vergleichen von Kategorien)."""
    words = re.findall(r"[a-z]+|[0-9]+", name.lower())
    words = ["grandtheftauto" if w == "gta" else _ROMAN.get(w, w) for w in words]
    joined = "".join(words)
    return re.sub(r"^gta(?=[ivx]+$)", "grandtheftauto", joined)   # "gtavi" → "grandtheftautovi"


def best_category_match(name: str, candidates: list[dict]) -> dict | None:
    wanted = normalize_game_name(name)
    for category in candidates:
        if normalize_game_name(category.get("name", "")) == wanted:
            return category
    return None


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
        self._games: dict[str, dict] = {}            # konfigurierter Name → Twitch-Kategorie
        self._logins_by_id: dict[str, str] = {}
        self._games_missing_logged: set[str] = set()
        self._start_logged = False

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

    # ------------------------------------------------------------- Spiele
    def resolve_games(self) -> dict[str, dict]:
        """Sucht die Twitch-Kategorien der konfigurierten Spiele (auch vor dem Release: wartet geduldig)."""
        for name in self.cfg.discovery.games:
            if name in self._games:
                continue
            game = self.api.get_games([name]).get(name.lower())
            if game is None:
                game = best_category_match(name, self.api.search_categories(name))
            if game:
                self._games[name] = game
                log.info("Spiel gefunden: %s (Twitch-Kategorie %s)", game["name"], game["id"])
            elif name not in self._games_missing_logged:
                self._games_missing_logged.add(name)
                log.info("Twitch-Kategorie „%s“ gibt es noch nicht – die Maschine prüft regelmäßig weiter", name)
        return self._games

    def _logins_for(self, broadcaster_ids: list[str]) -> dict[str, str]:
        missing = [bid for bid in set(broadcaster_ids) if bid and bid not in self._logins_by_id]
        if missing:
            for login, user in self.api.get_users(ids=missing).items():
                self._logins_by_id[str(user["id"])] = login
        return self._logins_by_id

    def _discover_games(self, now: datetime) -> list[tuple[Clip, float]]:
        d = self.cfg.discovery
        stored: list[tuple[Clip, float]] = []
        excluded = {b.lower() for b in d.exclude_broadcasters}
        listed = {s.login for s in self.cfg.streamers}
        since_day = now - timedelta(hours=24)
        since = now - timedelta(hours=d.lookback_hours)
        if d.games_start_at:
            start = datetime.combine(date.fromisoformat(str(d.games_start_at)), datetime.min.time(),
                                     local_timezone(self.cfg.posting.timezone)).astimezone(timezone.utc)
            if now < start:
                if not self._start_logged:
                    self._start_logged = True
                    log.info("Spiele-Clips starten am %s – bis dahin wartet die Maschine", d.games_start_at)
                return stored
            since = max(since, start)
        for name, game in self.resolve_games().items():
            raw = self.api.get_clips(None, since, now, limit=d.fetch_limit, game_id=game["id"])
            logins = self._logins_for([str(c.get("broadcaster_id") or "") for c in raw])
            candidates = []
            for data in raw:
                login = logins.get(str(data.get("broadcaster_id") or "")) or \
                    re.sub(r"\W", "", (data.get("broadcaster_name") or "")).lower()
                clip = Clip.from_helix(data, login, game["name"])
                if login in excluded or (d.only_listed_streamers and login not in listed):
                    continue
                streamer = self.cfg.streamer(login) or StreamerConfig(login=login)
                if passes_filters(clip, streamer, self.cfg):
                    candidates.append((clip, velocity_score(clip.view_count, clip.created_at, now, d.gravity)))
            if not raw:
                log.info("%s: noch keine Clips im Zeitraum – sobald gestreamt wird, geht es los", game["name"])
            new = 0
            per_broadcaster: dict[str, int] = {}
            for clip, score in dedupe_moments(candidates, d.dedupe_window_seconds):
                if self.db.get_clip(clip.id):
                    self.db.upsert_clip(clip, "top_clips", score)
                    continue
                if new >= d.max_new_per_game or self.db.has_overlapping(clip, d.dedupe_window_seconds):
                    continue
                used = per_broadcaster.get(clip.broadcaster_id)
                if used is None:
                    used = self.db.count_from_broadcaster_since(clip.broadcaster_id, since_day)
                if used >= d.max_per_broadcaster_per_day:
                    continue
                per_broadcaster[clip.broadcaster_id] = used + 1
                self.db.upsert_clip(clip, "top_clips", score)
                stored.append((clip, score))
                new += 1
            log.info("%s: %d Clips geprüft, %d neue Highlights", game["name"], len(raw), new)
        return stored

    def discover(self, now: datetime | None = None) -> list[tuple[Clip, float]]:
        """Holt die aktuellen Top-Clips (pro Streamer und/oder pro Spiel) und speichert neue Kandidaten."""
        now = now or utcnow()
        d = self.cfg.discovery
        stored: list[tuple[Clip, float]] = []
        if d.games:
            if self.api is None:
                log.error("Clips nach Spiel brauchen TWITCH_CLIENT_ID/SECRET in der .env")
            else:
                try:
                    stored.extend(self._discover_games(now))
                except TwitchAPIError as exc:
                    log.error("Spiele-Clips konnten nicht geladen werden: %s", exc)
        streamers = [] if d.games and d.only_listed_streamers else self.cfg.streamers
        for streamer in streamers:
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
