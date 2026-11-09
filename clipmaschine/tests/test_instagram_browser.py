"""Instagram-Browser-Modus gegen eine nachgebaute instagram.com-Seite (echter Chromium, headless)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from clipmaschine.config import AppConfig, InstagramConfig, StreamerConfig
from clipmaschine.models import PostMetadata
from clipmaschine.uploaders import make_uploader
from clipmaschine.uploaders.base import UploadError
from clipmaschine.uploaders.browser_base import SESSION_PAUSE_SECONDS, browser_caption
from clipmaschine.uploaders.instagram import InstagramUploader
from clipmaschine.uploaders.instagram_browser import InstagramBrowserUploader

from .conftest import make_clip

pytest.importorskip("playwright.sync_api")

META = PostMetadata(hook="Hook", title="Krasser Clip 😳", description="Original: twitch.tv/streamer_a",
                    hashtags=["streamer_a", "minecraft", "fyp"])

HOME_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Instagram</title></head><body>
<nav><a href="#" id="create"><svg aria-label="Neuer Beitrag" width="24" height="24"><rect width="24" height="24"/></svg>
<span>Erstellen</span></a></nav>
<div role="dialog" id="notify"><p>Benachrichtigungen aktivieren?</p>
  <button onclick="this.parentNode.remove()">Aktivieren</button>
  <button onclick="this.parentNode.remove()">Jetzt nicht</button></div>
<div id="menu" style="display:none"><a href="#" id="post-item"><span>Beitrag</span></a><a href="#"><span>Live-Video</span></a></div>
<div role="dialog" id="modal" style="display:none">
  <h2>Neuen Beitrag erstellen</h2>
  <input type="file" accept="video/mp4,video/quicktime,image/jpeg" id="file" style="display:none">
  <div id="notice" style="display:none"><p>Videobeiträge werden jetzt als Reels geteilt</p><button id="ok">OK</button></div>
  <div id="crop" style="display:none">
    <svg aria-label="Zuschnitt auswählen" width="24" height="24" id="cropbtn"><rect width="24" height="24"/></svg>
    <div id="cropmenu" style="display:none"><span id="orig">Original</span><span>1:1</span><span>4:5</span></div>
    <div role="button" id="next1">Weiter</div></div>
  <div id="edit" style="display:none"><p>Titelbild</p><div role="button" id="next2">Weiter</div></div>
  <div id="share" style="display:none">
    <div contenteditable="true" role="textbox" aria-label="Bildunterschrift verfassen …" id="caption"></div>
    %SHARE%</div>
  <div id="done" style="display:none"><img alt="Animiertes Häkchen" src="data:," width="10" height="10">
    <p>Dein Reel wurde geteilt.</p></div>
</div>
<script>
const MODE = "%MODE%";
const show = (id) => document.getElementById(id).style.display = 'block';
const hide = (id) => document.getElementById(id).style.display = 'none';
window.__crop = 'standard';
document.getElementById('create').addEventListener('click', (e) => { e.preventDefault(); show('menu'); });
document.getElementById('post-item').addEventListener('click', (e) => { e.preventDefault(); hide('menu'); show('modal'); });
document.getElementById('file').addEventListener('change', (e) => {
  const f = e.target.files[0]; window.__file = {name: f.name, size: f.size}; show('notice');
});
document.getElementById('ok').addEventListener('click', () => { hide('notice'); show('crop'); });
document.getElementById('cropbtn').addEventListener('click', () => show('cropmenu'));
document.getElementById('orig').addEventListener('click', () => { window.__crop = 'original'; hide('cropmenu'); });
document.getElementById('next1').addEventListener('click', () => { hide('crop'); show('edit'); });
document.getElementById('next2').addEventListener('click', () => { hide('edit'); show('share'); });
const share = document.getElementById('sharebtn');
if (share) share.addEventListener('click', async () => {
  await fetch('/posted', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(
    {caption: document.getElementById('caption').innerText, file: window.__file, crop: window.__crop})});
  if (MODE !== 'never') { hide('share'); setTimeout(() => show('done'), 600); }
});
</script></body></html>"""

LOGIN_PAGE = """<!doctype html><html><body><h1>Instagram – Anmelden</h1>
<script>setTimeout(() => { document.cookie = "sessionid=ig123; path=/; max-age=86400"; }, 800);</script>
</body></html>"""


class InstagramServer:
    def __init__(self):
        self.mode = "normal"
        self.posted: list[dict] = []
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.startswith("/accounts/login"):
                    return self._html(LOGIN_PAGE)
                if self.path == "/" or self.path.startswith("/?"):
                    if state.mode == "logged_out":
                        self.send_response(302)
                        self.send_header("Location", "/accounts/login/?next=/")
                        self.end_headers()
                        return
                    share = "" if state.mode == "broken" else '<div role="button" id="sharebtn">Teilen</div>'
                    return self._html(HOME_PAGE.replace("%SHARE%", share).replace("%MODE%", state.mode))
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
def insta():
    server = InstagramServer()
    yield server
    server.close()


@pytest.fixture(scope="module")
def connected(tmp_path_factory, browser_exec, insta):
    root = tmp_path_factory.mktemp("instagram")
    cfg = InstagramConfig(enabled=True, method="browser", browser_headless=True, browser_channel=None,
                          browser_executable=browser_exec, browser_upload_timeout_seconds=20)
    uploader = InstagramBrowserUploader(cfg, profile_dir=root / "profile", marker_path=root / "tokens" / "ib.json",
                                        debug_dir=root / "shots", base_url=insta.url)
    assert uploader.problem() == "Noch nicht verbunden: python -m clipmaschine auth instagram"
    uploader.authorize(headless=True, timeout=30)
    assert uploader.problem() is None and uploader.connected_at()
    return uploader


@pytest.fixture
def video(tmp_path) -> Path:
    path = tmp_path / "Reel_XYZ.mp4"
    path.write_bytes(b"\x00" * 2048)
    return path


def test_reel_is_shared_once_in_portrait(connected, insta, video):
    insta.mode, insta.posted = "normal", []
    result = connected.upload(video, META, make_clip())
    assert result.status == "posted"
    assert len(insta.posted) == 1
    posted = insta.posted[0]
    assert posted["caption"].strip() == browser_caption(META, max_hashtags=30)
    assert posted["file"] == {"name": "Reel_XYZ.mp4", "size": 2048}
    assert posted["crop"] == "original"                              # Hochformat behalten, nicht quadratisch


def test_unconfirmed_share_counts_as_submitted(connected, insta, video):
    insta.mode, insta.posted = "never", []
    connected.confirm_timeout = 2
    try:
        assert connected.upload(video, META, make_clip()).status == "submitted"
    finally:
        connected.confirm_timeout = connected.CONFIRM_TIMEOUT
    assert len(insta.posted) == 1                                    # kein Doppel-Post


def test_missing_share_button_fails_before_posting(connected, insta, video):
    insta.mode, insta.posted = "broken", []
    connected.cfg.browser_upload_timeout_seconds = 14      # genug für alle Schritte bis „Teilen“
    try:
        with pytest.raises(UploadError) as err:
            connected.upload(video, META, make_clip())
    finally:
        connected.cfg.browser_upload_timeout_seconds = 20
    assert err.value.retryable and "Teilen" in str(err.value) and "Screenshot:" in str(err.value)
    assert insta.posted == []


def test_expired_login_pauses(connected, insta, video):
    insta.mode, insta.posted = "logged_out", []
    with pytest.raises(UploadError) as err:
        connected.upload(video, META, make_clip())
    assert err.value.pause_seconds == SESSION_PAUSE_SECONDS and "auth instagram" in str(err.value)


def test_instagram_method_selection(tmp_path):
    cfg = AppConfig(streamers=[StreamerConfig("x")], data_dir=tmp_path)
    assert cfg.instagram_method == "browser"                         # ohne Token → dein Account
    assert isinstance(make_uploader(cfg, "instagram"), InstagramBrowserUploader)
    cfg.instagram.user_id, cfg.instagram.access_token = "1", "token"
    assert cfg.instagram_method == "api"
    assert isinstance(make_uploader(cfg, "instagram"), InstagramUploader)
