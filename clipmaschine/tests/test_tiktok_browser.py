"""TikTok-Browser-Modus gegen eine nachgebaute TikTok-Studio-Seite (echter Chromium, headless)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from clipmaschine.config import AppConfig, StreamerConfig, TikTokConfig
from clipmaschine.models import PostMetadata
from clipmaschine.uploaders import make_uploader
from clipmaschine.uploaders.base import UploadError
from clipmaschine.uploaders.tiktok import TikTokUploader
from clipmaschine.uploaders.tiktok_browser import SESSION_PAUSE_SECONDS, TikTokBrowserUploader, browser_caption

from .conftest import make_clip

playwright = pytest.importorskip("playwright.sync_api")

META = PostMetadata(hook="Hook", title="Krasser Clip 😳", description="Original: twitch.tv/streamer_a",
                    hashtags=["streamer_a", "minecraft", "fyp"])

UPLOAD_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>TikTok Studio</title></head><body>
<h1>Video hochladen</h1>
<input type="file" accept="video/*" id="file">
<div id="details" style="display:none">
  <div class="editor"><div contenteditable="true" id="caption"></div></div>
  %BUTTON%
</div>
<script>
const MODE = "%MODE%";
document.getElementById('file').addEventListener('change', (e) => {
  const f = e.target.files[0];
  window.__file = {name: f.name, size: f.size};
  document.getElementById('caption').innerText = f.name.replace(/\\.mp4$/, '');   // TikTok füllt den Dateinamen vor
  document.getElementById('details').style.display = 'block';
  setTimeout(() => { const b = document.getElementById('post'); if (b) b.disabled = false; }, 700);
});
async function finish() {
  await fetch('/posted', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({caption: document.getElementById('caption').innerText, file: window.__file})});
  if (MODE !== 'never') location.href = '/tiktokstudio/content';
}
const post = document.getElementById('post');
if (post) post.addEventListener('click', () => {
  if (MODE === 'confirm') {
    const d = document.createElement('div');
    d.setAttribute('role', 'dialog');
    d.innerHTML = '<p>Weiter posten?</p><button id="confirm">Jetzt posten</button>';
    document.body.appendChild(d);
    document.getElementById('confirm').addEventListener('click', finish);
  } else { finish(); }
});
</script></body></html>"""

LOGIN_PAGE = """<!doctype html><html><body><h1>Bei TikTok anmelden</h1>
<script>setTimeout(() => { document.cookie = "sessionid=test123; path=/; max-age=86400"; }, 800);</script>
</body></html>"""


class StudioServer:
    def __init__(self):
        self.mode = "normal"
        self.posted: list[dict] = []
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.startswith("/tiktokstudio/upload"):
                    if state.mode == "logged_out":
                        self.send_response(302)
                        self.send_header("Location", "/login?redirect=upload")
                        self.end_headers()
                        return
                    button = {
                        "broken": "",
                        "text_only": '<button id="post" disabled>Veröffentlichen</button>',
                    }.get(state.mode, '<button data-e2e="post_video_button" id="post" disabled>Posten</button>')
                    self._html(UPLOAD_PAGE.replace("%BUTTON%", button).replace("%MODE%", state.mode))
                elif self.path.startswith("/login"):
                    self._html(LOGIN_PAGE)
                elif self.path.startswith("/tiktokstudio/content"):
                    self._html("<h1>Beiträge verwalten</h1>")
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                state.posted.append(json.loads(self.rfile.read(length) or b"{}"))
                self.send_response(200)
                self.end_headers()

            def _html(self, body):
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture(scope="module")
def studio():
    server = StudioServer()
    yield server
    server.close()


@pytest.fixture(scope="module")
def connected(tmp_path_factory, browser_exec, studio):
    """Einmal verbinden (die Testseite setzt das Session-Cookie wie nach einem echten Login)."""
    root = tmp_path_factory.mktemp("tiktok")
    cfg = TikTokConfig(enabled=True, method="browser", browser_headless=True, browser_channel=None,
                       browser_executable=browser_exec, browser_upload_timeout_seconds=20)
    uploader = TikTokBrowserUploader(cfg, profile_dir=root / "profile", marker_path=root / "tokens" / "tb.json",
                                     debug_dir=root / "shots", base_url=studio.url)
    assert uploader.problem() == "Noch nicht verbunden: python -m clipmaschine auth tiktok"
    uploader.authorize(headless=True, timeout=30)
    assert uploader.problem() is None and uploader.connected_at()
    return uploader


@pytest.fixture
def video(tmp_path) -> Path:
    path = tmp_path / "Clip_ABC.mp4"
    path.write_bytes(b"\x00" * 4096)
    return path


def test_caption_is_single_line():
    assert browser_caption(META) == "Krasser Clip 😳 Original: twitch.tv/streamer_a #streamer_a #minecraft #fyp"


@pytest.mark.parametrize("mode", ["normal", "confirm", "text_only"])
def test_upload_posts_once_with_caption(connected, studio, video, mode):
    studio.mode, studio.posted = mode, []
    result = connected.upload(video, META, make_clip())
    assert result.status == "posted"
    assert len(studio.posted) == 1                                   # genau ein Post
    posted = studio.posted[0]
    assert posted["caption"].strip() == browser_caption(META)        # Dateiname ersetzt, Text vollständig
    assert posted["file"] == {"name": "Clip_ABC.mp4", "size": 4096}


def test_unconfirmed_post_counts_as_submitted_without_retry(connected, studio, video):
    studio.mode, studio.posted = "never", []
    connected.confirm_timeout = 2
    try:
        result = connected.upload(video, META, make_clip())
    finally:
        connected.confirm_timeout = 120
    assert result.status == "submitted"                              # kein zweiter Versuch → kein Doppel-Post
    assert len(studio.posted) == 1
    assert list(connected.debug_dir.glob("tiktok_*nach-posten.png"))


def test_broken_page_fails_before_posting_with_screenshot(connected, studio, video):
    studio.mode, studio.posted = "broken", []
    connected.cfg.browser_upload_timeout_seconds = 3
    try:
        with pytest.raises(UploadError) as err:
            connected.upload(video, META, make_clip())
    finally:
        connected.cfg.browser_upload_timeout_seconds = 20
    assert err.value.retryable and not err.value.pause_seconds
    assert "Screenshot:" in str(err.value) and "Posten" in str(err.value)
    assert studio.posted == []


def test_expired_login_pauses_instead_of_giving_up(connected, studio, video):
    studio.mode, studio.posted = "logged_out", []
    with pytest.raises(UploadError) as err:
        connected.upload(video, META, make_clip())
    assert err.value.pause_seconds == SESSION_PAUSE_SECONDS
    assert "auth tiktok" in str(err.value)


def test_not_connected_does_not_start_browser(tmp_path, video):
    cfg = TikTokConfig(enabled=True, method="browser")
    uploader = TikTokBrowserUploader(cfg, tmp_path / "p", tmp_path / "missing.json", tmp_path / "s")
    with pytest.raises(UploadError) as err:
        uploader.upload(video, META, make_clip())
    assert err.value.pause_seconds and not (tmp_path / "p").exists()


def test_method_auto_and_factory(tmp_path):
    cfg = AppConfig(streamers=[StreamerConfig("x")], data_dir=tmp_path)
    cfg.tiktok.method = "auto"
    assert cfg.tiktok_method == "browser"                            # ohne API-Keys → dein Account
    assert isinstance(make_uploader(cfg, "tiktok"), TikTokBrowserUploader)
    cfg.tiktok.client_key, cfg.tiktok.client_secret = "k", "s"
    assert cfg.tiktok_method == "api"
    assert isinstance(make_uploader(cfg, "tiktok"), TikTokUploader)
    cfg.tiktok.method = "browser"
    assert isinstance(make_uploader(cfg, "tiktok"), TikTokBrowserUploader)
