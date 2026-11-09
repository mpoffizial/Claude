"""Instagram Reels über die Instagram Graph API (Content Publishing, resumable Upload)."""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import requests

from ..config import InstagramConfig
from ..models import Clip, PostMetadata
from ..oauth import TokenStore
from .base import UploadError, UploadResult, Uploader, build_caption, redact

log = logging.getLogger(__name__)

RUPLOAD_HOST = "rupload.facebook.com"
REFRESH_EVERY_SECONDS = 7 * 86400        # langlebige Instagram-Tokens laufen nach 60 Tagen ab
REFRESH_RETRY_SECONDS = 6 * 3600


class InstagramUploader(Uploader):
    name = "instagram"

    def __init__(self, cfg: InstagramConfig, session: requests.Session | None = None, poll_seconds: float = 10,
                 token_store: TokenStore | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        self.poll_seconds = poll_seconds
        self.token_store = token_store

    @property
    def base(self) -> str:
        return f"https://{self.cfg.graph_host}/{self.cfg.api_version}"

    def problem(self) -> str | None:
        if not self.cfg.user_id or not self.cfg.access_token:
            return "INSTAGRAM_USER_ID / INSTAGRAM_ACCESS_TOKEN fehlen in der .env"
        return None

    # ------------------------------------------------------------- Token
    def _token(self) -> str:
        """Aktueller Token. Bei 'Instagram API with Instagram Login' wird er automatisch erneuert,
        damit er nicht nach 60 Tagen abläuft (gespeichert in tokens/instagram.json)."""
        configured = self.cfg.access_token
        if self.token_store is None or self.cfg.graph_host != "graph.instagram.com":
            return configured
        origin = hashlib.sha256(configured.encode()).hexdigest()[:16]
        data = self.token_store.load() or {}
        if data.get("origin") != origin:          # neuer Token in der .env → von vorn
            data = {"origin": origin, "access_token": configured, "next_refresh": 0}
        if time.time() >= float(data.get("next_refresh", 0)):
            data = self._refresh(data)
            self.token_store.save(data)
        return data["access_token"]

    def _refresh(self, data: dict) -> dict:
        try:
            resp = self.session.request("GET", f"https://{self.cfg.graph_host}/refresh_access_token", timeout=30,
                                        params={"grant_type": "ig_refresh_token",
                                                "access_token": data["access_token"]})
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Instagram-Token-Erneuerung fehlgeschlagen: %s", redact(str(exc)))
            payload, resp = {}, None
        if resp is not None and resp.status_code == 200 and payload.get("access_token"):
            days = float(payload.get("expires_in", 0)) / 86400
            log.info("Instagram-Token erneuert (gültig für %.0f Tage)", days)
            return {**data, "access_token": payload["access_token"],
                    "next_refresh": time.time() + REFRESH_EVERY_SECONDS}
        if resp is not None:
            message = (payload.get("error") or {}).get("message", resp.status_code)
            log.warning("Instagram-Token-Erneuerung abgelehnt: %s – neuer Versuch in 6 h", redact(str(message)))
        return {**data, "next_refresh": time.time() + REFRESH_RETRY_SECONDS}

    def _call(self, method: str, url: str, data: dict | None = None, params: dict | None = None) -> dict:
        # wie in der Meta-Doku: access_token als Parameter (Fehlermeldungen geben nie die URL aus)
        if method == "GET":
            params = {**(params or {}), "access_token": self._token()}
        else:
            data = {**(data or {}), "access_token": self._token()}
        try:
            resp = self.session.request(method, url, timeout=60, data=data, params=params)
        except requests.RequestException as exc:
            raise UploadError(f"Instagram nicht erreichbar: {exc.__class__.__name__}") from exc
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if resp.status_code >= 400 or "error" in payload:
            err = payload.get("error") or {}
            code = err.get("code")
            # 4/17/32/613 = Rate-Limits, 9007 = Medium noch nicht bereit
            retryable = resp.status_code >= 500 or code in (4, 17, 32, 613, 9007)
            pause = 3600 if code in (4, 17, 32, 613) else 0
            raise UploadError(f"Instagram: {err.get('message', resp.status_code)} (code {code})",
                              retryable=retryable, pause_seconds=pause)
        return payload

    # ------------------------------------------------------------ Upload
    def upload(self, video_path: Path, meta: PostMetadata, clip: Clip) -> UploadResult:
        problem = self.problem()
        if problem:
            raise UploadError(problem, retryable=False)
        caption = build_caption(meta, limit=2200, max_hashtags=30)
        container = self._call("POST", f"{self.base}/{self.cfg.user_id}/media", data={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": "true" if self.cfg.share_to_feed else "false",
        })
        container_id = container.get("id")
        if not container_id:
            raise UploadError(f"Instagram: kein Container erstellt ({redact(str(container))})")
        upload_uri = container.get("uri") or f"https://{RUPLOAD_HOST}/ig-api-upload/{self.cfg.api_version}/{container_id}"

        size = video_path.stat().st_size
        with open(video_path, "rb") as fh:
            try:
                resp = self.session.post(upload_uri, data=fh, timeout=600, headers={
                    "Authorization": f"OAuth {self._token()}",
                    "offset": "0",
                    "file_size": str(size),
                })
            except requests.RequestException as exc:
                raise UploadError(f"Instagram-Upload abgebrochen: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise UploadError(f"Instagram-Upload: HTTP {resp.status_code} {redact(resp.text[:300])}")

        self._wait_until_ready(container_id)
        published = self._call("POST", f"{self.base}/{self.cfg.user_id}/media_publish",
                               data={"creation_id": container_id})
        media_id = published.get("id", "")
        url = ""
        try:
            url = self._call("GET", f"{self.base}/{media_id}", params={"fields": "permalink"}).get("permalink", "")
        except UploadError:
            pass  # Link ist nur nice-to-have
        return UploadResult("posted", remote_id=media_id, url=url)

    def _wait_until_ready(self, container_id: str) -> None:
        deadline = time.monotonic() + self.cfg.processing_timeout_seconds
        while time.monotonic() < deadline:
            data = self._call("GET", f"{self.base}/{container_id}", params={"fields": "status_code,status"})
            status = data.get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise UploadError(f"Instagram konnte das Video nicht verarbeiten: {data.get('status', status)}",
                                  retryable=False)
            time.sleep(self.poll_seconds)
        raise UploadError("Instagram: Verarbeitung dauert zu lange")
