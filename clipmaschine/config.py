"""Konfiguration: config.yaml (Einstellungen) + .env (Zugangsdaten)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent

LAYOUTS = ("blur", "split", "crop")
TIKTOK_METHODS = ("auto", "api", "browser")
TIKTOK_MODES = ("direct", "inbox")
TIKTOK_PRIVACY = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")
YOUTUBE_PRIVACY = ("public", "unlisted", "private")

DEFAULT_HYPE_KEYWORDS = [
    # Lachen / Reaktionen (inkl. BTTV/7TV-Emotes, die im Chat als Text ankommen)
    "kekw", "lul", "lulw", "omegalul", "lmao", "lmfao", "xd", "haha", "hahaha", "😂", "🤣", "💀",
    # Hype
    "pog", "pogchamp", "poggers", "pogu", "w", "ww", "www", "letsgo", "lets go", "sheesh",
    "holy", "wtf", "omg", "insane", "😱", "🔥", "monkas", "ez", "+2",
    # Clip-Aufrufe
    "clip", "clipit", "clip it", "!clip", "clip that",
    # Deutsch
    "krass", "digga", "alter", "ehrenlos", "wild",
]


class ConfigError(Exception):
    """Fehlerhafte oder fehlende Konfiguration."""


@dataclass
class StreamerConfig:
    login: str
    layout: str | None = None            # überschreibt editing.layout
    facecam: dict | None = None          # {x, y, w, h} relativ (0..1) – für layout "split"
    crop_x: float = 0.5                  # horizontale Bildmitte für Crop (0 = links, 1 = rechts)
    hashtags: list[str] = field(default_factory=list)
    min_views: int | None = None         # überschreibt discovery.min_views

    def __post_init__(self) -> None:
        self.login = str(self.login).strip().lower()
        if self.facecam is not None:
            missing = {"x", "y", "w", "h"} - set(self.facecam)
            if missing:
                raise ConfigError(f"facecam von '{self.login}' braucht x, y, w, h (fehlt: {sorted(missing)})")
            self.facecam = {k: float(self.facecam[k]) for k in ("x", "y", "w", "h")}


@dataclass
class TwitchConfig:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:17563/twitch/callback"


@dataclass
class DiscoveryConfig:
    enabled: bool = True
    interval_minutes: float = 20
    lookback_hours: float = 24
    fetch_limit: int = 100               # wie viele Top-Clips pro Streamer abgefragt werden
    min_views: int = 50
    min_duration: float = 6
    max_duration: float = 60
    gravity: float = 1.3                 # höher = frische Clips werden stärker bevorzugt
    max_new_per_streamer: int = 3        # neue Clips pro Streamer und Suchlauf
    dedupe_window_seconds: float = 30    # Clips desselben Moments zusammenfassen
    featured_only: bool = False          # nur vom Streamer "gefeaturete" Clips
    languages: list[str] = field(default_factory=list)       # leer = alle Sprachen
    exclude_keywords: list[str] = field(default_factory=list)  # Clips mit diesen Wörtern im Titel überspringen


@dataclass
class LiveConfig:
    enabled: bool = False
    window_seconds: float = 10           # Kurzfenster für den Hype
    baseline_seconds: float = 180        # Vergleichsfenster ("normales" Chat-Tempo)
    warmup_seconds: float = 60           # erst nach so vielen Sekunden Beobachtung auslösen
    spike_factor: float = 3.0            # Kurzfenster muss X-mal aktiver sein als die Baseline
    min_messages_per_second: float = 1.5
    min_unique_chatters: int = 8
    min_baseline_rate: float = 0.3       # Untergrenze, damit stille Chats nicht ständig auslösen
    keyword_weight: float = 0.5          # Zusatzgewicht pro Hype-Wort/Emote (max. 3 pro Nachricht)
    hype_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_HYPE_KEYWORDS))
    cooldown_seconds: float = 120        # Mindestabstand zwischen zwei Clips pro Kanal
    clip_delay_seconds: float = 4        # kurz warten, damit die Reaktion mit im Clip ist
    live_check_interval_seconds: float = 120
    hype_score_bonus: float = 1000.0     # Hype-Clips werden vor Top-Clips gepostet


@dataclass
class EditingConfig:
    layout: str = "blur"                 # blur | split | crop
    width: int = 1080
    height: int = 1920
    fps: int = 30
    max_duration: float = 59
    crf: int = 20
    preset: str = "medium"
    video_maxrate: str = "8M"
    audio_bitrate: str = "160k"
    loudnorm: bool = True
    target_lufs: float = -14
    blur_strength: int = 12
    split_top_ratio: float = 0.35        # Anteil der Höhe für die Facecam im split-Layout
    font: str = "Arial"
    fonts_dir: str | None = None         # Ordner mit eigenen Schriftarten (.ttf/.otf)
    hook_text: bool = True               # Hook-Text oben im Video
    hook_uppercase: bool = False
    hook_color: str = "#FFFFFF"
    credit_text: bool = True             # "twitch.tv/<streamer>" im Video
    caption_color: str = "#FFFFFF"
    caption_highlight_color: str = "#FFE600"
    keep_source: bool = False            # Original-Download nach dem Rendern behalten


@dataclass
class CaptionsConfig:
    enabled: bool = False                # braucht: pip install faster-whisper
    model: str = "small"
    language: str | None = None          # None = automatisch erkennen
    device: str = "auto"
    compute_type: str = "int8"
    max_words: int = 3
    max_chunk_seconds: float = 1.6
    uppercase: bool = True


@dataclass
class MetadataConfig:
    use_claude: Any = "auto"             # auto | true | false
    model: str = "claude-opus-5-5"
    effort: str = "low"
    language: str = "de"
    default_hashtags: list[str] = field(default_factory=lambda: ["twitch", "twitchclips", "gaming", "fyp", "viral"])
    max_hashtags: int = 8
    extra_instructions: str = ""


@dataclass
class TikTokConfig:
    enabled: bool = False
    # auto = API, wenn TIKTOK_CLIENT_KEY/SECRET gesetzt sind, sonst Browser
    # browser = mit deinem normalen TikTok-Account über TikTok Studio (keine Entwickler-App nötig)
    # api = offizielle Content Posting API (Entwickler-App)
    method: str = "auto"
    browser_headless: bool = False       # true nur für Server ohne Bildschirm
    browser_channel: str | None = "chrome"   # installiertes Google Chrome; null = Playwright-Chromium
    browser_executable: str | None = None    # optional: Pfad zu Chrome/Chromium
    browser_upload_timeout_seconds: float = 600
    browser_selectors: dict = field(default_factory=dict)   # nur nötig, falls TikTok die Seite umbaut
    client_key: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:17564/tiktok/callback"
    mode: str = "direct"                 # direct = sofort posten, inbox = als Entwurf in die TikTok-App
    privacy_level: str = "SELF_ONLY"     # nach App-Audit: PUBLIC_TO_EVERYONE
    disable_comment: bool = False
    disable_duet: bool = False
    disable_stitch: bool = False
    cover_timestamp_ms: int = 1000
    use_pkce: bool = True
    status_timeout_seconds: float = 300


@dataclass
class YouTubeConfig:
    enabled: bool = False
    client_secrets_file: str = "client_secret.json"
    privacy_status: str = "public"
    category_id: str = "20"              # 20 = Gaming
    add_shorts_tag: bool = True
    made_for_kids: bool = False


@dataclass
class InstagramConfig:
    enabled: bool = False
    user_id: str = ""
    access_token: str = ""
    graph_host: str = "graph.instagram.com"  # Facebook-Login-Variante: graph.facebook.com
    api_version: str = "v23.0"
    share_to_feed: bool = True
    processing_timeout_seconds: float = 600


@dataclass
class PostingConfig:
    max_posts_per_day: int = 4           # pro Plattform, rollierende 24 Stunden
    min_minutes_between_posts: float = 120
    active_hours: list[int] | None = field(default_factory=lambda: [10, 23])  # [Start, Ende) Ortszeit; null = immer
    timezone: str | None = None          # z. B. "Europe/Berlin"; leer = Systemzeitzone
    max_clip_age_hours: float = 72
    max_attempts: int = 3
    queue_size: int = 4                  # so viele fertige Videos auf Vorrat halten
    cleanup_after_post: bool = True


@dataclass
class AppConfig:
    streamers: list[StreamerConfig]
    data_dir: Path
    loop_interval_seconds: float = 60
    dry_run: bool = False
    min_free_disk_gb: float = 1.0        # darunter wird nichts Neues heruntergeladen/gerendert
    twitch: TwitchConfig = field(default_factory=TwitchConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    editing: EditingConfig = field(default_factory=EditingConfig)
    captions: CaptionsConfig = field(default_factory=CaptionsConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    tiktok: TikTokConfig = field(default_factory=TikTokConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    instagram: InstagramConfig = field(default_factory=InstagramConfig)
    posting: PostingConfig = field(default_factory=PostingConfig)
    config_path: Path | None = None

    def streamer(self, login: str) -> StreamerConfig | None:
        login = (login or "").lower()
        return next((s for s in self.streamers if s.login == login), None)

    @property
    def tokens_dir(self) -> Path:
        return self.data_dir / "tokens"

    @property
    def tiktok_method(self) -> str:
        """Tatsächlich genutzte TikTok-Methode ('api' oder 'browser')."""
        if self.tiktok.method == "auto":
            return "api" if self.tiktok.client_key and self.tiktok.client_secret else "browser"
        return self.tiktok.method


def _section(cls, raw: Any, name: str):
    """Baut eine Config-Dataclass aus einem YAML-Abschnitt; unbekannte Schlüssel werden gemeldet."""
    if raw is None:
        return cls()
    if not isinstance(raw, dict):
        raise ConfigError(f"Abschnitt '{name}' muss ein Mapping sein")
    known = {f.name for f in fields(cls)}
    kwargs = {}
    for key, value in raw.items():
        if key in known:
            kwargs[key] = value
        else:
            log.warning("Unbekannte Option '%s.%s' wird ignoriert (Tippfehler?)", name, key)
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ConfigError(f"Abschnitt '{name}': {exc}") from exc


def find_config_file(explicit: str | os.PathLike | None = None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    elif os.getenv("CLIPMASCHINE_CONFIG"):
        candidates.append(Path(os.environ["CLIPMASCHINE_CONFIG"]))
    else:
        candidates += [Path.cwd() / "config.yaml", PACKAGE_DIR / "config.yaml"]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    tried = ", ".join(str(p) for p in candidates)
    raise ConfigError(
        f"Keine config.yaml gefunden (gesucht: {tried}).\n"
        f"Kopiere die Vorlage: cp {PACKAGE_DIR / 'config.example.yaml'} config.yaml"
    )


def _load_dotenv(config_dir: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional
        return
    for env_file in (Path.cwd() / ".env", config_dir / ".env", PACKAGE_DIR / ".env"):
        if env_file.is_file():
            load_dotenv(env_file, override=False)


def _env_default(current: str, *names: str) -> str:
    if current:
        return str(current)
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def load_config(path: str | os.PathLike | None = None) -> AppConfig:
    config_path = find_config_file(path)
    _load_dotenv(config_path.parent)

    with open(config_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path}: Inhalt muss ein YAML-Mapping sein")

    streamers = []
    for entry in raw.get("streamers") or []:
        if isinstance(entry, str):
            entry = {"login": entry}
        streamers.append(_section(StreamerConfig, entry, "streamers[]"))

    data_dir = Path(raw.get("data_dir") or "clipmaschine_data")
    if not data_dir.is_absolute():
        data_dir = (Path.cwd() / data_dir).resolve()

    cfg = AppConfig(
        streamers=streamers,
        data_dir=data_dir,
        loop_interval_seconds=float(raw.get("loop_interval_seconds", 60)),
        dry_run=bool(raw.get("dry_run", False)),
        min_free_disk_gb=float(raw.get("min_free_disk_gb", 1.0)),
        twitch=_section(TwitchConfig, raw.get("twitch"), "twitch"),
        discovery=_section(DiscoveryConfig, raw.get("discovery"), "discovery"),
        live=_section(LiveConfig, raw.get("live"), "live"),
        editing=_section(EditingConfig, raw.get("editing"), "editing"),
        captions=_section(CaptionsConfig, raw.get("captions"), "captions"),
        metadata=_section(MetadataConfig, raw.get("metadata"), "metadata"),
        tiktok=_section(TikTokConfig, raw.get("tiktok"), "tiktok"),
        youtube=_section(YouTubeConfig, raw.get("youtube"), "youtube"),
        instagram=_section(InstagramConfig, raw.get("instagram"), "instagram"),
        posting=_section(PostingConfig, raw.get("posting"), "posting"),
        config_path=config_path,
    )
    known_top = {"streamers", "data_dir", "loop_interval_seconds", "dry_run", "min_free_disk_gb", "twitch",
                 "discovery", "live", "editing", "captions", "metadata", "tiktok", "youtube", "instagram", "posting"}
    for key in raw:
        if key not in known_top:
            log.warning("Unbekannte Option '%s' wird ignoriert (Tippfehler?)", key)

    # Zugangsdaten kommen bevorzugt aus der Umgebung / .env
    cfg.twitch.client_id = _env_default(cfg.twitch.client_id, "TWITCH_CLIENT_ID")
    cfg.twitch.client_secret = _env_default(cfg.twitch.client_secret, "TWITCH_CLIENT_SECRET")
    cfg.tiktok.client_key = _env_default(cfg.tiktok.client_key, "TIKTOK_CLIENT_KEY")
    cfg.tiktok.client_secret = _env_default(cfg.tiktok.client_secret, "TIKTOK_CLIENT_SECRET")
    cfg.instagram.user_id = _env_default(cfg.instagram.user_id, "INSTAGRAM_USER_ID")
    cfg.instagram.access_token = _env_default(cfg.instagram.access_token, "INSTAGRAM_ACCESS_TOKEN")
    secrets_file = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "").strip()
    if secrets_file:
        cfg.youtube.client_secrets_file = secrets_file
    yt_path = Path(cfg.youtube.client_secrets_file)
    if not yt_path.is_absolute():
        # relativ zur config.yaml, falls dort vorhanden – sonst relativ zum Arbeitsverzeichnis
        beside_config = config_path.parent / yt_path
        cfg.youtube.client_secrets_file = str(beside_config if beside_config.exists() else Path.cwd() / yt_path)

    validate(cfg)
    return cfg


def validate(cfg: AppConfig) -> None:
    problems = []
    if cfg.editing.layout not in LAYOUTS:
        problems.append(f"editing.layout muss eines von {LAYOUTS} sein")
    for s in cfg.streamers:
        if s.layout and s.layout not in LAYOUTS:
            problems.append(f"streamers[{s.login}].layout muss eines von {LAYOUTS} sein")
        if (s.layout or cfg.editing.layout) == "split" and not s.facecam:
            log.warning("Streamer '%s' nutzt layout 'split' ohne facecam – es wird 'blur' verwendet", s.login)
    if cfg.tiktok.method not in TIKTOK_METHODS:
        problems.append(f"tiktok.method muss eines von {TIKTOK_METHODS} sein")
    if cfg.tiktok.mode not in TIKTOK_MODES:
        problems.append(f"tiktok.mode muss eines von {TIKTOK_MODES} sein")
    if cfg.tiktok.privacy_level not in TIKTOK_PRIVACY:
        problems.append(f"tiktok.privacy_level muss eines von {TIKTOK_PRIVACY} sein")
    if cfg.youtube.privacy_status not in YOUTUBE_PRIVACY:
        problems.append(f"youtube.privacy_status muss eines von {YOUTUBE_PRIVACY} sein")
    hours = cfg.posting.active_hours
    if hours is not None and (len(hours) != 2 or not all(0 <= int(h) <= 24 for h in hours)):
        problems.append("posting.active_hours muss [Start, Ende] mit Stunden 0–24 sein oder null")
    if not 0.1 <= cfg.editing.split_top_ratio <= 0.6:
        problems.append("editing.split_top_ratio muss zwischen 0.1 und 0.6 liegen")
    if cfg.editing.width % 2 or cfg.editing.height % 2:
        problems.append("editing.width/height müssen gerade Zahlen sein")
    p = cfg.posting
    if p.max_posts_per_day < 1 or p.max_attempts < 1 or p.queue_size < 1:
        problems.append("posting.max_posts_per_day, max_attempts und queue_size müssen mindestens 1 sein")
    if p.timezone:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(p.timezone)
        except Exception:
            problems.append(f"posting.timezone '{p.timezone}' ist unbekannt (Windows: pip install tzdata)")
    if cfg.loop_interval_seconds < 5:
        problems.append("loop_interval_seconds muss mindestens 5 sein")
    if problems:
        raise ConfigError("Fehler in der Konfiguration:\n  - " + "\n  - ".join(problems))
