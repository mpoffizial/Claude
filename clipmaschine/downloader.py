"""Clips herunterladen (yt-dlp)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_CLIP_URL_RE = re.compile(
    r"(?:clips\.twitch\.tv/(?:embed\?clip=)?|twitch\.tv/(?:[^/?#]+/)?clip/)([A-Za-z0-9_-]+)"
)
_SLUG_RE = re.compile(r"[A-Za-z0-9_-]+")


class DownloadError(Exception):
    pass


class QuietYtDlpLogger:
    """yt-dlp soll nichts selbst ausgeben – Fehler meldet die Clipmaschine verständlich im eigenen Log."""

    def debug(self, msg: str) -> None:
        pass

    info = warning = error = debug


def short_error(exc: Exception) -> str:
    """Kürzt yt-dlp-Fehler auf das Wesentliche (ohne 'please report this issue …')."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    text = re.sub(r"^ERROR:\s*", "", text)
    text = re.split(r";\s*please report this issue", text, maxsplit=1)[0]
    text = re.sub(r"\s*\(caused by .*\)$", "", text)
    return text[:200]


def clip_id_from_ref(ref: str) -> str:
    """Akzeptiert eine Clip-URL (clips.twitch.tv/…, twitch.tv/<kanal>/clip/…) oder direkt die Clip-ID."""
    ref = ref.strip()
    match = _CLIP_URL_RE.search(ref)
    if match:
        return match.group(1)
    if _SLUG_RE.fullmatch(ref):
        return ref
    raise ValueError(f"'{ref}' ist keine Twitch-Clip-URL oder -ID")


def _ytdlp():
    try:
        import yt_dlp
    except ImportError as exc:
        raise DownloadError("yt-dlp ist nicht installiert: pip install yt-dlp") from exc
    return yt_dlp


def download_clip(url: str, dest_dir: Path, basename: str) -> Path:
    yt_dlp = _ytdlp()
    dest_dir.mkdir(parents=True, exist_ok=True)
    for old in dest_dir.glob(f"{basename}.*"):
        if old.suffix in (".part", ".ytdl"):
            old.unlink(missing_ok=True)
    opts = {
        "outtmpl": str(dest_dir / f"{basename}.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "overwrites": True,
        "logger": QuietYtDlpLogger(),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
    except Exception as exc:  # yt-dlp wirft eigene Fehlertypen
        raise DownloadError(f"Download von {url} fehlgeschlagen: {short_error(exc)}") from exc
    if not path.exists():
        matches = [p for p in dest_dir.glob(f"{basename}.*") if p.suffix not in (".part", ".ytdl")]
        if not matches:
            raise DownloadError(f"Download von {url} lieferte keine Datei")
        path = matches[0]
    log.debug("Heruntergeladen: %s", path)
    return path


def fetch_clip_info(url: str) -> dict:
    """Metadaten eines Clips ohne Download (Fallback, wenn keine Twitch-API-Daten vorliegen)."""
    yt_dlp = _ytdlp()
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                               "logger": QuietYtDlpLogger()}) as ydl:
            return ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        raise DownloadError(f"Clip-Infos für {url} nicht abrufbar: {short_error(exc)}") from exc
