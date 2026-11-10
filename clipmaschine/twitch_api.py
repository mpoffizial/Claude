"""Twitch Helix API: Streamer, Live-Status, Clips suchen und Clips erstellen."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from .oauth import TokenStore, generate_state, obtain_authorization_code

log = logging.getLogger(__name__)

AUTH_URL = "https://id.twitch.tv/oauth2"
HELIX_URL = "https://api.twitch.tv/helix"


class TwitchAPIError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class TwitchAPI:
    """App-Token für Lesezugriffe; User-Token (Scope clips:edit) nur zum Erstellen von Clips."""

    def __init__(self, client_id: str, client_secret: str, user_token_store: TokenStore | None = None,
                 redirect_uri: str = "http://localhost:17563/twitch/callback",
                 session: requests.Session | None = None):
        if not client_id or not client_secret:
            raise TwitchAPIError(
                "TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET fehlen. Lege eine App unter "
                "https://dev.twitch.tv/console/apps an und trage die Werte in die .env ein."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.user_store = user_token_store
        self.session = session or requests.Session()
        self._lock = threading.RLock()       # Session + Token werden von mehreren Threads genutzt
        self._app_token: str | None = None
        self._app_token_expiry = 0.0
        self._games: dict[str, str] = {}

    # ----------------------------------------------------------------- Tokens
    def _post_token(self, data: dict) -> requests.Response:
        try:
            return self.session.post(f"{AUTH_URL}/token", data=data, timeout=20)
        except requests.RequestException as exc:
            raise TwitchAPIError(f"Twitch nicht erreichbar ({exc.__class__.__name__})") from exc

    def _app_access_token(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._app_token and time.time() < self._app_token_expiry - 300:
                return self._app_token
            resp = self._post_token({
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            })
            payload = _json(resp)
            if resp.status_code != 200 or "access_token" not in payload:
                raise TwitchAPIError(f"App-Token fehlgeschlagen: HTTP {resp.status_code} {payload.get('message', '')}",
                                     resp.status_code)
            self._app_token = payload["access_token"]
            self._app_token_expiry = time.time() + float(payload.get("expires_in", 3600))
            return self._app_token

    def _user_access_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            data = self.user_store.load() if self.user_store else None
            if not data or "access_token" not in data:
                raise TwitchAPIError("Kein Twitch-Benutzer-Token. Führe einmal aus: python -m clipmaschine auth twitch")
            if force_refresh or time.time() > float(data.get("expires_at", 0)) - 300:
                data = self._refresh_user_token(data)
            return data["access_token"]

    def _refresh_user_token(self, data: dict) -> dict:
        if not data.get("refresh_token"):
            raise TwitchAPIError("Twitch-Token abgelaufen – bitte erneut: python -m clipmaschine auth twitch")
        resp = self._post_token({
            "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"],
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        payload = _json(resp)
        if resp.status_code != 200 or "access_token" not in payload:
            raise TwitchAPIError(
                f"Twitch-Token konnte nicht erneuert werden (HTTP {resp.status_code}). "
                "Bitte erneut: python -m clipmaschine auth twitch", resp.status_code)
        data = self._token_record(payload, fallback_refresh=data.get("refresh_token"))
        self.user_store.save(data)
        return data

    @staticmethod
    def _token_record(payload: dict, fallback_refresh: str | None = None) -> dict:
        return {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token") or fallback_refresh,
            "expires_at": time.time() + float(payload.get("expires_in", 3600)),
            "scope": payload.get("scope", []),
        }

    def authorize_user(self, scopes: tuple[str, ...] = ("clips:edit",)) -> str:
        """Interaktiver Login des Bot-Accounts. Gibt den Twitch-Login zurück."""
        if not self.user_store:
            raise TwitchAPIError("Kein Token-Speicher konfiguriert")
        state = generate_state()
        url = f"{AUTH_URL}/authorize?" + urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "force_verify": "true",
        })
        code = obtain_authorization_code(url, self.redirect_uri, state)
        resp = self._post_token({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        })
        payload = _json(resp)
        if resp.status_code != 200 or "access_token" not in payload:
            raise TwitchAPIError(f"Token-Tausch fehlgeschlagen: HTTP {resp.status_code} {payload.get('message', '')}")
        self.user_store.save(self._token_record(payload))
        return self.validate_user_token().get("login", "?")

    def validate_user_token(self) -> dict:
        token = self._user_access_token()
        try:
            resp = self.session.get(f"{AUTH_URL}/validate", headers={"Authorization": f"OAuth {token}"}, timeout=20)
        except requests.RequestException as exc:
            raise TwitchAPIError(f"Twitch nicht erreichbar ({exc.__class__.__name__})") from exc
        if resp.status_code != 200:
            raise TwitchAPIError(f"Token ungültig (HTTP {resp.status_code})", resp.status_code)
        return _json(resp)

    def has_user_token(self) -> bool:
        data = self.user_store.load() if self.user_store else None
        return bool(data and data.get("access_token"))

    # ---------------------------------------------------------------- Requests
    def _request(self, method: str, path: str, params=None, user_auth: bool = False) -> dict:
        refreshed = False
        for attempt in range(4):
            with self._lock:
                token = self._user_access_token() if user_auth else self._app_access_token()
                try:
                    resp = self.session.request(method, f"{HELIX_URL}{path}", params=params, timeout=30, headers={
                        "Client-Id": self.client_id,
                        "Authorization": f"Bearer {token}",
                    })
                except requests.RequestException as exc:
                    if attempt == 3:
                        raise TwitchAPIError(f"Twitch nicht erreichbar: {exc.__class__.__name__}") from exc
                    time.sleep(2 ** attempt)
                    continue
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                with self._lock:
                    if user_auth:
                        self._user_access_token(force_refresh=True)
                    else:
                        self._app_access_token(force=True)
                continue
            if resp.status_code == 429 and attempt < 3:
                reset = float(resp.headers.get("Ratelimit-Reset", time.time() + 5))
                time.sleep(min(max(reset - time.time(), 1), 60))
                continue
            if resp.status_code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                message = _json(resp).get("message", "")
                raise TwitchAPIError(f"Twitch {method} {path}: HTTP {resp.status_code} {message}".strip(),
                                     resp.status_code)
            return _json(resp)
        raise TwitchAPIError(f"Twitch {method} {path}: zu viele Fehlversuche")

    # -------------------------------------------------------------- Endpunkte
    def get_users(self, logins: list[str] | None = None, ids: list[str] | None = None) -> dict[str, dict]:
        """Gibt {login: user} zurück."""
        result: dict[str, dict] = {}
        for key, values in (("login", [v.lower() for v in logins or []]), ("id", list(ids or []))):
            for chunk in _chunks(values, 100):
                data = self._request("GET", "/users", params=[(key, v) for v in chunk])
                for user in data.get("data", []):
                    result[user["login"].lower()] = user
        return result

    def get_streams(self, user_ids: list[str]) -> dict[str, dict]:
        """Gibt {user_id: stream} für alle aktuell live sendenden Kanäle zurück."""
        live: dict[str, dict] = {}
        for chunk in _chunks(list(user_ids), 100):
            params = [("user_id", uid) for uid in chunk] + [("first", "100")]
            for stream in self._request("GET", "/streams", params=params).get("data", []):
                live[stream["user_id"]] = stream
        return live

    def get_clips(self, broadcaster_id: str | None, started_at: datetime, ended_at: datetime,
                  limit: int = 100, featured_only: bool = False, game_id: str | None = None) -> list[dict]:
        """Top-Clips eines Kanals oder (mit game_id) eines Spiels im Zeitraum – Twitch sortiert nach Views."""
        clips: list[dict] = []
        cursor = None
        while len(clips) < limit:
            params = {
                **({"game_id": game_id} if game_id else {"broadcaster_id": broadcaster_id}),
                "started_at": rfc3339(started_at),
                "ended_at": rfc3339(ended_at),
                "first": str(min(100, limit - len(clips))),
            }
            if featured_only:
                params["is_featured"] = "true"
            if cursor:
                params["after"] = cursor
            data = self._request("GET", "/clips", params=params)
            clips.extend(data.get("data", []))
            cursor = (data.get("pagination") or {}).get("cursor")
            if not cursor or not data.get("data"):
                break
        return clips[:limit]

    def get_games(self, names: list[str]) -> dict[str, dict]:
        """Spiele/Kategorien per exaktem Namen. Gibt {name_klein: spiel} zurück."""
        params = [("name", n) for n in names if n]
        if not params:
            return {}
        return {g["name"].lower(): g for g in self._request("GET", "/games", params=params).get("data", [])}

    def search_categories(self, query: str, limit: int = 20) -> list[dict]:
        return self._request("GET", "/search/categories", params={"query": query, "first": str(limit)}).get("data", [])

    def get_clip(self, clip_id: str) -> dict | None:
        data = self._request("GET", "/clips", params={"id": clip_id}).get("data", [])
        return data[0] if data else None

    def game_names(self, game_ids: list[str]) -> dict[str, str]:
        missing = [gid for gid in {g for g in game_ids if g} if gid not in self._games]
        for chunk in _chunks(missing, 100):
            for game in self._request("GET", "/games", params=[("id", gid) for gid in chunk]).get("data", []):
                self._games[game["id"]] = game["name"]
        return {gid: self._games.get(gid, "") for gid in game_ids if gid}

    def create_clip(self, broadcaster_id: str, has_delay: bool = False) -> str:
        """Erstellt einen Clip der letzten Sekunden (Kanal muss live sein). Gibt die Clip-ID zurück."""
        data = self._request("POST", "/clips", user_auth=True, params={
            "broadcaster_id": broadcaster_id,
            "has_delay": "true" if has_delay else "false",
        })
        items = data.get("data") or []
        if not items:
            raise TwitchAPIError("Twitch hat keinen Clip zurückgegeben")
        return items[0]["id"]


def _json(resp: requests.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}
