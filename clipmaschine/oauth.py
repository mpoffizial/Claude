"""OAuth-Hilfen: Token-Speicher, PKCE und das Abfangen des Redirects im Browser."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)


class OAuthError(Exception):
    pass


class TokenStore:
    """Speichert Tokens als JSON-Datei (nur für den eigenen Benutzer lesbar)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict | None:
        if not self.path.is_file():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Token-Datei %s ist unlesbar: %s", self.path, exc)
            return None

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # z. B. Windows
        tmp.replace(self.path)


def generate_state() -> str:
    return secrets.token_urlsafe(24)


def pkce_pair(encoding: str = "base64url") -> tuple[str, str]:
    """Erzeugt (code_verifier, code_challenge).

    RFC 7636 verlangt base64url; TikToks Login Kit für Desktop erwartet laut Doku
    den SHA-256-Hash hex-kodiert – daher wählbar.
    """
    verifier = secrets.token_urlsafe(64)[:86]  # 43–128 Zeichen aus [A-Za-z0-9-_]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    if encoding == "hex":
        challenge = digest.hex()
    else:
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _extract_code(query: dict[str, list[str]], expected_state: str) -> str:
    if "error" in query:
        desc = query.get("error_description", [""])[0]
        raise OAuthError(f"Autorisierung abgelehnt: {query['error'][0]} {desc}".strip())
    state = query.get("state", [""])[0]
    if state != expected_state:
        raise OAuthError("Ungültiger 'state' im Redirect – bitte erneut versuchen")
    code = query.get("code", [""])[0]
    if not code:
        raise OAuthError("Kein 'code' im Redirect gefunden")
    return code


def obtain_authorization_code(authorize_url: str, redirect_uri: str, expected_state: str,
                              timeout: float = 300) -> str:
    """Öffnet den Login im Browser und gibt den Autorisierungscode zurück.

    Zeigt die Redirect-URI auf localhost/127.0.0.1, wird der Redirect automatisch
    abgefangen. Sonst fügt man die URL, auf der der Browser gelandet ist, hier ein.
    """
    parsed = urlparse(redirect_uri)
    print("\nÖffne diese URL im Browser und melde dich an:\n")
    print(f"  {authorize_url}\n")

    if parsed.hostname not in ("localhost", "127.0.0.1"):
        redirected = input("Füge hier die komplette URL ein, auf die du weitergeleitet wurdest:\n> ").strip()
        return _extract_code(parse_qs(urlparse(redirected).query), expected_state)

    result: dict[str, list[str]] = {}
    expected_path = (parsed.path or "/").rstrip("/") or "/"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server-API)
            url = urlparse(self.path)
            if (url.path.rstrip("/") or "/") != expected_path:
                self.send_response(404)
                self.end_headers()
                return
            result.update(parse_qs(url.query))
            body = "<h2>Fertig &#10004;</h2><p>Du kannst dieses Fenster schließen und zur Clipmaschine zurückkehren.</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *args):  # keine Zugriffslogs auf der Konsole
            pass

    port = parsed.port or 80
    bind_host = "127.0.0.1" if parsed.hostname == "127.0.0.1" else "localhost"
    try:
        server = HTTPServer((bind_host, port), Handler)
    except OSError as exc:
        raise OAuthError(f"Kann lokalen Port {port} nicht öffnen ({exc}). Ist er belegt?") from exc
    server.timeout = 1.0
    try:
        webbrowser.open(authorize_url)
    except Exception:  # kein Browser verfügbar – URL wurde oben ausgegeben
        pass
    print(f"Warte auf die Weiterleitung an {redirect_uri} …")
    deadline = time.monotonic() + timeout
    try:
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not result:
        raise OAuthError("Zeitüberschreitung beim Warten auf die Anmeldung")
    return _extract_code(result, expected_state)
