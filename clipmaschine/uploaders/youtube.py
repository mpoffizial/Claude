"""YouTube Shorts über die YouTube Data API v3 (resumable Upload)."""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config import YouTubeConfig
from ..models import Clip, PostMetadata
from .base import UploadError, UploadResult, Uploader, file_mtime

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
RETRIABLE_STATUS = {500, 502, 503, 504}


def _google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise UploadError("Google-Bibliotheken fehlen: pip install google-api-python-client google-auth-oauthlib",
                          retryable=False) from exc
    return Request, Credentials, InstalledAppFlow, build, HttpError, MediaFileUpload


def seconds_until_quota_reset(now: datetime | None = None) -> float:
    """YouTube setzt das Tageskontingent um Mitternacht pazifischer Zeit zurück (+10 min Puffer)."""
    pacific = ZoneInfo("America/Los_Angeles")
    local = (now or datetime.now(timezone.utc)).astimezone(pacific)
    reset = (local + timedelta(days=1)).replace(hour=0, minute=10, second=0, microsecond=0)
    if local.hour == 0 and local.minute < 10:
        reset = local.replace(minute=10, second=0, microsecond=0)
    return max((reset - local).total_seconds(), 300.0)


def youtube_title(meta: PostMetadata, add_shorts_tag: bool) -> str:
    title = meta.title.replace("<", "").replace(">", "").strip() or meta.hook
    suffix = " #Shorts" if add_shorts_tag else ""
    if len(title) + len(suffix) > 100:
        title = title[: 100 - len(suffix) - 1].rstrip() + "…"
    return title + suffix


def youtube_tags(hashtags: list[str]) -> list[str]:
    tags, total = [], 0
    for tag in hashtags:
        if total + len(tag) + 1 > 450:   # YouTube erlaubt ca. 500 Zeichen insgesamt
            break
        tags.append(tag)
        total += len(tag) + 1
    return tags


class YouTubeUploader(Uploader):
    name = "youtube"
    description = "YouTube Data API"

    def __init__(self, cfg: YouTubeConfig, token_path: Path):
        self.cfg = cfg
        self.token_path = Path(token_path)

    def connected_at(self) -> float | None:
        return file_mtime(self.token_path)

    def problem(self) -> str | None:
        if not self.token_path.is_file():
            return "Noch nicht angemeldet: python -m clipmaschine auth youtube"
        return None

    def authorize(self) -> None:
        _, _, InstalledAppFlow, build, _, _ = _google()
        secrets = Path(self.cfg.client_secrets_file)
        if not secrets.is_file():
            raise UploadError(f"OAuth-Client-Datei nicht gefunden: {secrets} (siehe README, Abschnitt YouTube)",
                              retryable=False)
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets), scopes=SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent", open_browser=True)
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"YouTube verbunden. Token gespeichert in {self.token_path}")

    def _service(self):
        Request, Credentials, _, build, _, _ = _google()
        if not self.token_path.is_file():
            raise UploadError("YouTube nicht verbunden: python -m clipmaschine auth youtube", retryable=False)
        creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                from google.auth.exceptions import RefreshError
                try:
                    creds.refresh(Request())
                except RefreshError as exc:   # Zugriff widerrufen/abgelaufen → neu anmelden nötig
                    raise UploadError(f"YouTube-Token nicht erneuerbar ({exc}) – bitte: "
                                      "python -m clipmaschine auth youtube", retryable=False) from exc
                except Exception as exc:      # Netzwerkproblem → später erneut
                    raise UploadError(f"YouTube nicht erreichbar ({exc.__class__.__name__})") from exc
                self.token_path.write_text(creds.to_json(), encoding="utf-8")
            else:
                raise UploadError("YouTube-Login ungültig: python -m clipmaschine auth youtube", retryable=False)
        return build("youtube", "v3", credentials=creds, cache_discovery=False)

    def upload(self, video_path: Path, meta: PostMetadata, clip: Clip) -> UploadResult:
        _, _, _, _, HttpError, MediaFileUpload = _google()
        description = "\n\n".join(filter(None, [
            meta.description,
            f"Original-Clip: {clip.url}" if clip.url else "",
            meta.hashtag_text(),
        ]))
        body = {
            "snippet": {
                "title": youtube_title(meta, self.cfg.add_shorts_tag),
                "description": description[:4900],
                "tags": youtube_tags(meta.hashtags),
                "categoryId": str(self.cfg.category_id),
            },
            "status": {
                "privacyStatus": self.cfg.privacy_status,
                "selfDeclaredMadeForKids": self.cfg.made_for_kids,
            },
        }
        media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        request = self._service().videos().insert(part="snippet,status", body=body, media_body=media)
        response, retry = None, 0
        while response is None:
            try:
                _status, response = request.next_chunk()
            except HttpError as exc:
                status = int(getattr(exc.resp, "status", 0) or 0)
                content = str(getattr(exc, "content", b"")[:500])
                if status in RETRIABLE_STATUS and retry < 5:
                    retry += 1
                    time.sleep(min(2 ** retry + random.random(), 60))
                    continue
                if "quotaExceeded" in content or "uploadLimitExceeded" in content:
                    raise UploadError("YouTube-Tageskontingent aufgebraucht",
                                      pause_seconds=seconds_until_quota_reset()) from exc
                raise UploadError(f"YouTube-Upload: HTTP {status} {content}",
                                  retryable=status not in (400, 401, 403)) from exc
            except (OSError, TimeoutError) as exc:
                if retry < 5:
                    retry += 1
                    time.sleep(min(2 ** retry + random.random(), 60))
                    continue
                raise UploadError(f"YouTube-Upload abgebrochen: {exc}") from exc
        video_id = response.get("id", "")
        if not video_id:
            raise UploadError(f"YouTube-Antwort ohne Video-ID: {response}")
        return UploadResult("posted", remote_id=video_id, url=f"https://youtube.com/shorts/{video_id}")
