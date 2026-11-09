"""TikTok Content Posting API: Direct Post oder Upload in die Inbox (Entwurf)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import requests

from ..config import TikTokConfig
from ..models import Clip, PostMetadata
from ..oauth import TokenStore, generate_state, obtain_authorization_code, pkce_pair
from .base import UploadError, UploadResult, Uploader, build_caption, file_mtime

log = logging.getLogger(__name__)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API_URL = "https://open.tiktokapis.com"
SINGLE_CHUNK_MAX = 64_000_000      # bis hierhin in einem Stück hochladen
CHUNK_SIZE = 10_000_000            # sonst 10-MB-Teile (erlaubt: 5–64 MB, letzter Teil bis 128 MB)


@dataclass
class ChunkPlan:
    chunk_size: int
    count: int

    def ranges(self, total: int):
        for i in range(self.count):
            start = i * self.chunk_size
            end = total - 1 if i == self.count - 1 else start + self.chunk_size - 1
            yield start, end


def chunk_plan(size: int) -> ChunkPlan:
    if size <= 0:
        raise UploadError("Videodatei ist leer", retryable=False)
    if size <= SINGLE_CHUNK_MAX:
        return ChunkPlan(chunk_size=size, count=1)
    # Anzahl = abgerundet; der Rest wird an den letzten Teil angehängt
    return ChunkPlan(chunk_size=CHUNK_SIZE, count=size // CHUNK_SIZE)


class TikTokUploader(Uploader):
    name = "tiktok"
    description = "offizielle API"

    def __init__(self, cfg: TikTokConfig, token_store: TokenStore, session: requests.Session | None = None):
        self.cfg = cfg
        self.store = token_store
        self.session = session or requests.Session()

    def connected_at(self) -> float | None:
        return file_mtime(self.store.path)

    @property
    def scopes(self) -> str:
        return "user.info.basic," + ("video.publish" if self.cfg.mode == "direct" else "video.upload")

    def problem(self) -> str | None:
        if not self.cfg.client_key or not self.cfg.client_secret:
            return "TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET fehlen in der .env"
        if not (self.store.load() or {}).get("refresh_token"):
            return "Noch nicht angemeldet: python -m clipmaschine auth tiktok"
        return None

    # ------------------------------------------------------------------ OAuth
    def authorize(self) -> None:
        if not self.cfg.client_key or not self.cfg.client_secret:
            raise UploadError("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET fehlen in der .env", retryable=False)
        state = generate_state()
        params = {
            "client_key": self.cfg.client_key,
            "scope": self.scopes,
            "response_type": "code",
            "redirect_uri": self.cfg.redirect_uri,
            "state": state,
        }
        verifier = None
        if self.cfg.use_pkce:
            verifier, challenge = pkce_pair("hex")   # TikTok (Desktop) erwartet SHA-256 als Hex
            params.update(code_challenge=challenge, code_challenge_method="S256")
        code = obtain_authorization_code(f"{AUTH_URL}?{urlencode(params)}", self.cfg.redirect_uri, state)
        data = {
            "client_key": self.cfg.client_key,
            "client_secret": self.cfg.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.cfg.redirect_uri,
        }
        if verifier:
            data["code_verifier"] = verifier
        self._save_tokens(self._token_request(data))
        info = self.creator_info() if self.cfg.mode == "direct" else {}
        name = info.get("creator_username") or info.get("creator_nickname") or "dein Account"
        print(f"TikTok verbunden: {name}")

    def _token_request(self, data: dict) -> dict:
        try:
            resp = self.session.post(f"{API_URL}/v2/oauth/token/", data=data, timeout=30,
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        except requests.RequestException as exc:
            raise UploadError(f"TikTok nicht erreichbar: {exc.__class__.__name__}") from exc
        payload = _json(resp)
        if "access_token" not in payload:
            raise UploadError(f"TikTok-Token-Fehler: {payload.get('error', resp.status_code)} "
                              f"{payload.get('error_description', '')}".strip(),
                              retryable=resp.status_code >= 500)
        return payload

    def _save_tokens(self, payload: dict) -> dict:
        now = time.time()
        data = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token"),
            "expires_at": now + float(payload.get("expires_in", 86400)),
            "refresh_expires_at": now + float(payload.get("refresh_expires_in", 31536000)),
            "open_id": payload.get("open_id"),
            "scope": payload.get("scope"),
        }
        self.store.save(data)
        return data

    def _access_token(self) -> str:
        data = self.store.load()
        if not data or not data.get("refresh_token"):
            raise UploadError("TikTok nicht verbunden: python -m clipmaschine auth tiktok", retryable=False)
        if time.time() > float(data.get("expires_at", 0)) - 600:
            if time.time() > float(data.get("refresh_expires_at", 0)):
                raise UploadError("TikTok-Login abgelaufen: python -m clipmaschine auth tiktok", retryable=False)
            payload = self._token_request({
                "client_key": self.cfg.client_key,
                "client_secret": self.cfg.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": data["refresh_token"],
            })
            data = self._save_tokens(payload)
        return data["access_token"]

    # ------------------------------------------------------------------- API
    def _post(self, path: str, body: dict) -> dict:
        token = self._access_token()
        for attempt in range(3):
            try:
                resp = self.session.post(f"{API_URL}{path}", json=body, timeout=60, headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                })
                break
            except requests.RequestException as exc:
                if attempt == 2:
                    raise UploadError(f"TikTok nicht erreichbar: {exc.__class__.__name__}") from exc
                time.sleep(2 ** attempt)
        payload = _json(resp)
        error = payload.get("error") or {}
        code = error.get("code", "ok" if resp.ok else str(resp.status_code))
        if code != "ok":
            message = error.get("message", "")
            retryable = code in ("rate_limit_exceeded", "internal_error") or resp.status_code >= 500
            pause = 3600 if code in ("rate_limit_exceeded", "spam_risk_too_many_posts") else 0
            hint = ""
            if code.startswith("unaudited_client"):
                hint = (" – Deine TikTok-App ist noch nicht geprüft (Audit): setze tiktok.privacy_level auf "
                        "SELF_ONLY und den TikTok-Account auf privat, oder nutze tiktok.mode: inbox.")
            raise UploadError(f"TikTok {path}: {code} {message}{hint}".strip(), retryable=retryable,
                              pause_seconds=pause)
        return payload.get("data") or {}

    def creator_info(self) -> dict:
        return self._post("/v2/post/publish/creator_info/query/", {})

    def upload(self, video_path: Path, meta: PostMetadata, clip: Clip) -> UploadResult:
        size = video_path.stat().st_size
        plan = chunk_plan(size)
        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": plan.chunk_size,
            "total_chunk_count": plan.count,
        }
        if self.cfg.mode == "direct":
            info = self.creator_info()
            options = info.get("privacy_level_options") or []
            if options and self.cfg.privacy_level not in options:
                raise UploadError(f"privacy_level {self.cfg.privacy_level} ist für diesen Account nicht erlaubt "
                                  f"(möglich: {', '.join(options)})", retryable=False)
            max_seconds = info.get("max_video_post_duration_sec")
            if max_seconds and clip.duration and min(clip.duration, 600) > float(max_seconds):
                log.warning("Clip ist länger als TikTok für diesen Account erlaubt (%ss)", max_seconds)
            post_info = {
                "title": build_caption(meta, limit=2200),
                "privacy_level": self.cfg.privacy_level,
                "disable_comment": self.cfg.disable_comment or bool(info.get("comment_disabled")),
                "disable_duet": self.cfg.disable_duet or bool(info.get("duet_disabled")),
                "disable_stitch": self.cfg.disable_stitch or bool(info.get("stitch_disabled")),
                "video_cover_timestamp_ms": self.cfg.cover_timestamp_ms,
                "brand_content_toggle": False,
                "brand_organic_toggle": False,
            }
            data = self._post("/v2/post/publish/video/init/", {"post_info": post_info, "source_info": source_info})
        else:
            data = self._post("/v2/post/publish/inbox/video/init/", {"source_info": source_info})

        publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
        if not publish_id or not upload_url:
            raise UploadError(f"TikTok-Antwort ohne publish_id/upload_url: {data}")
        self._upload_file(upload_url, video_path, size, plan)
        status = self._wait_for_status(publish_id)
        if status == "PUBLISH_COMPLETE":
            return UploadResult("posted", remote_id=publish_id)
        if status == "SEND_TO_USER_INBOX":
            return UploadResult("inbox", remote_id=publish_id)
        return UploadResult("submitted", remote_id=publish_id)   # TikTok verarbeitet noch

    def _upload_file(self, upload_url: str, path: Path, size: int, plan: ChunkPlan) -> None:
        with open(path, "rb") as fh:
            for start, end in plan.ranges(size):
                fh.seek(start)
                chunk = fh.read(end - start + 1)
                for attempt in range(3):
                    try:
                        resp = self.session.put(upload_url, data=chunk, timeout=300, headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {start}-{end}/{size}",
                        })
                    except requests.RequestException as exc:
                        if attempt == 2:
                            raise UploadError(f"TikTok-Upload abgebrochen: {exc.__class__.__name__}") from exc
                        time.sleep(2 ** attempt)
                        continue
                    if resp.status_code in (200, 201, 206):
                        break
                    if resp.status_code >= 500 and attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise UploadError(f"TikTok-Upload: HTTP {resp.status_code} bei Bytes {start}-{end}")

    def _wait_for_status(self, publish_id: str) -> str:
        deadline = time.monotonic() + self.cfg.status_timeout_seconds
        status = "PROCESSING_UPLOAD"
        while time.monotonic() < deadline:
            data = self._post("/v2/post/publish/status/fetch/", {"publish_id": publish_id})
            status = data.get("status", status)
            if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                return status
            if status == "FAILED":
                raise UploadError(f"TikTok hat das Video abgelehnt: {data.get('fail_reason', 'unbekannt')}",
                                  retryable=False)
            time.sleep(5)
        log.warning("TikTok verarbeitet %s noch (Status %s) – wird als eingereicht gewertet", publish_id, status)
        return status


def _json(resp: requests.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}
