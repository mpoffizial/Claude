"""YouTube-Browser-Modus gegen eine nachgebaute YouTube-Studio-Seite (echter Chromium, headless)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from clipmaschine.config import AppConfig, StreamerConfig, YouTubeConfig
from clipmaschine.models import PostMetadata
from clipmaschine.uploaders import make_uploader
from clipmaschine.uploaders.base import UploadError
from clipmaschine.uploaders.browser_base import SESSION_PAUSE_SECONDS, SETUP_PAUSE_SECONDS
from clipmaschine.uploaders.youtube import YouTubeUploader, youtube_title
from clipmaschine.uploaders.youtube_browser import (LIMIT_PAUSE_SECONDS, YouTubeBrowserUploader, video_id_from_url,
                                                     youtube_description)

from .conftest import make_clip

pytest.importorskip("playwright.sync_api")

META = PostMetadata(hook="Hook", title="GTA 6: Die krasseste Flucht <3", description="Original: twitch.tv/streamer_a",
                    hashtags=["gta6", "gaming", "fyp"])

DIALOG = """<div id="dialog">
  <input type="file" accept="video/*" id="file" style="display:none">
  <div id="details" style="display:none">
    <a class="ytcp-video-info" id="link" style="display:none" href="https://youtu.be/abcDEF12345">youtu.be/abcDEF12345</a>
    <div id="title-textarea"><div id="textbox" contenteditable="true">Upload_XYZ</div></div>
    <div id="description-textarea"><div id="textbox" contenteditable="true"></div></div>
    <tp-yt-paper-radio-button name="VIDEO_MADE_FOR_KIDS_MFK" class="kids">Ja, speziell für Kinder</tp-yt-paper-radio-button>
    <tp-yt-paper-radio-button name="VIDEO_MADE_FOR_KIDS_NOT_MFK" class="kids">Nein, nicht speziell für Kinder
    </tp-yt-paper-radio-button>
  </div>
  <div id="step2" style="display:none"><p>Videoelemente</p></div>
  <div id="step3" style="display:none"><p>Überprüfungen</p></div>
  <div id="vis" style="display:none">
    <tp-yt-paper-radio-button name="PRIVATE" class="vis">Privat</tp-yt-paper-radio-button>
    <tp-yt-paper-radio-button name="UNLISTED" class="vis">Nicht gelistet</tp-yt-paper-radio-button>
    <tp-yt-paper-radio-button name="PUBLIC" class="vis">Öffentlich</tp-yt-paper-radio-button>
  </div>
  <footer id="footer" style="display:none">
    <ytcp-video-upload-progress><span class="progress-label" id="progress">Wird hochgeladen … 0 %</span>
    </ytcp-video-upload-progress></footer>
  <button id="next-button" style="display:none">Weiter</button>
  <button id="done-button" style="display:none">Veröffentlichen</button>
</div>"""

STUDIO_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Kanal-Dashboard – YouTube Studio</title></head><body>
<header><button id="create-icon">Erstellen</button>
<div id="menu" style="display:none"><span id="upload-item">Videos hochladen</span><span>Livestream starten</span></div></header>
<main id="main">%DIALOG%</main>
<ytcp-video-share-dialog id="share" style="display:none"><p>Video veröffentlicht</p>
  <a id="share-url" href="https://youtube.com/shorts/abcDEF12345">youtube.com/shorts/abcDEF12345</a></ytcp-video-share-dialog>
<script>
const MODE = "%MODE%";
const DIALOG = %DIALOG_JSON%;
const show = (id) => document.getElementById(id).style.display = 'block';
const hide = (id) => document.getElementById(id).style.display = 'none';
window.__kids = null; window.__vis = null; window.__uploaded = false; window.__step = 0;
document.getElementById('create-icon').addEventListener('click', () => show('menu'));
document.getElementById('upload-item').addEventListener('click', () => {
  hide('menu'); document.getElementById('main').innerHTML = DIALOG; wire();
});
function wire() {
  document.getElementById('file').addEventListener('change', (e) => {
    const f = e.target.files[0]; window.__file = {name: f.name, size: f.size};
    show('details'); show('footer'); show('next-button'); show('done-button');
    document.getElementById('link').style.display = 'inline';
    setTimeout(() => {
      const label = document.getElementById('progress');
      if (MODE === 'limit') { label.innerText = 'Daily upload limit reached'; return; }
      label.innerText = 'Wird hochgeladen … 64 %';
      setTimeout(() => { label.innerText = 'Hochladen abgeschlossen … Verarbeitung wird gestartet';
                         window.__uploaded = true; }, 1500);
    }, 500);
  });
  document.querySelectorAll('.kids').forEach((el) => el.addEventListener('click', () => {
    window.__kids = el.getAttribute('name'); }));
  document.querySelectorAll('.vis').forEach((el) => el.addEventListener('click', () => {
    window.__vis = el.getAttribute('name'); }));
  document.getElementById('next-button').addEventListener('click', () => {
    if (window.__step === 0 && !window.__kids) return;          // wie Studio: Zielgruppe ist Pflicht
    const order = ['details', 'step2', 'step3', 'vis'];
    if (window.__step >= 3) return;
    hide(order[window.__step]); window.__step += 1; show(order[window.__step]);
  });
  const done = document.getElementById('done-button');
  if (MODE === 'broken') done.remove();
  else done.addEventListener('click', async () => {
    const boxes = document.querySelectorAll('#textbox');
    await fetch('/posted', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
      title: boxes[0].innerText, description: boxes[1].innerText, file: window.__file,
      kids: window.__kids, vis: window.__vis, uploaded: window.__uploaded})});
    if (MODE !== 'never') { hide('dialog'); setTimeout(() => show('share'), 600); }
  });
}
if (document.getElementById('file')) wire();
</script></body></html>"""

LOGIN_PAGE = """<!doctype html><html><body><h1>Anmelden – Google-Konten</h1>%TEXT%
<script>if (!%BLOCKED%) setTimeout(() => { document.cookie = "LOGIN_INFO=yt123; path=/; max-age=86400"; }, 800);
</script></body></html>"""

NO_CHANNEL_PAGE = """<!doctype html><html><body><h1>Wie du auf YouTube erscheinst</h1>
<button>Kanal erstellen</button></body></html>"""


class StudioServer:
    def __init__(self):
        self.mode = "normal"
        self.posted: list[dict] = []
        state = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                logged_in = "LOGIN_INFO=" in (self.headers.get("Cookie") or "")
                if self.path.startswith("/ServiceLogin"):
                    blocked = state.mode == "blocked"
                    text = "<p>Dieser Browser oder diese App ist möglicherweise nicht sicher.</p>" if blocked else ""
                    return self._html(LOGIN_PAGE.replace("%TEXT%", text).replace("%BLOCKED%", str(blocked).lower()))
                if self.path.startswith("/upload"):
                    if not logged_in or state.mode == "logged_out":
                        self.send_response(302)
                        self.send_header("Location", "/ServiceLogin?service=youtube&continue=/upload")
                        self.end_headers()
                        return
                    if state.mode == "no_channel":
                        return self._html(NO_CHANNEL_PAGE)
                    page = (STUDIO_PAGE.replace("%DIALOG%", "" if state.mode == "menu" else DIALOG)
                            .replace("%DIALOG_JSON%", json.dumps(DIALOG)).replace("%MODE%", state.mode))
                    return self._html(page)
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


def _uploader(root: Path, browser_exec, url: str) -> YouTubeBrowserUploader:
    cfg = YouTubeConfig(enabled=True, method="browser", browser_headless=True, browser_channel=None,
                        browser_executable=browser_exec, browser_upload_timeout_seconds=25)
    uploader = YouTubeBrowserUploader(cfg, profile_dir=root / "profile", marker_path=root / "tokens" / "yb.json",
                                      debug_dir=root / "shots", base_url=url)
    uploader.DIRECT_DIALOG_TIMEOUT_MS = 2000
    return uploader


@pytest.fixture(scope="module")
def studio():
    server = StudioServer()
    yield server
    server.close()


@pytest.fixture(scope="module")
def connected(tmp_path_factory, browser_exec, studio):
    studio.mode = "normal"
    uploader = _uploader(tmp_path_factory.mktemp("youtube"), browser_exec, studio.url)
    assert uploader.problem() == "Noch nicht verbunden: python -m clipmaschine auth youtube"
    uploader.authorize(headless=True, timeout=30)
    assert uploader.problem() is None and uploader.connected_at()
    return uploader


@pytest.fixture
def video(tmp_path) -> Path:
    path = tmp_path / "Short_XYZ.mp4"
    path.write_bytes(b"\x00" * 4096)
    return path


def test_short_is_published_publicly_after_upload_finished(connected, studio, video):
    studio.mode, studio.posted = "normal", []
    clip = make_clip()
    result = connected.upload(video, META, clip)
    assert result.status == "posted"
    assert result.remote_id == "abcDEF12345" and result.url == "https://youtube.com/shorts/abcDEF12345"
    assert len(studio.posted) == 1
    posted = studio.posted[0]
    assert posted["title"].strip() == youtube_title(META, True)             # ohne „<“, mit #Shorts
    assert "<" not in posted["title"] and posted["title"].strip().endswith("#Shorts")
    assert "Original-Clip: https://clips.twitch.tv/Clip1" in posted["description"]
    assert "#gta6" in posted["description"]
    assert posted["file"] == {"name": "Short_XYZ.mp4", "size": 4096}
    assert posted["kids"] == "VIDEO_MADE_FOR_KIDS_NOT_MFK"
    assert posted["vis"] == "PUBLIC"
    assert posted["uploaded"] is True               # erst veröffentlichen, wenn die Datei komplett oben ist


def test_upload_dialog_is_opened_via_create_menu(connected, studio, video):
    studio.mode, studio.posted = "menu", []
    assert connected.upload(video, META, make_clip()).status == "posted"
    assert len(studio.posted) == 1 and studio.posted[0]["vis"] == "PUBLIC"


def test_unconfirmed_publish_counts_as_submitted(connected, studio, video):
    studio.mode, studio.posted = "never", []
    connected.confirm_timeout = 2
    try:
        assert connected.upload(video, META, make_clip()).status == "submitted"
    finally:
        connected.confirm_timeout = connected.CONFIRM_TIMEOUT
    assert len(studio.posted) == 1                                         # kein Doppel-Post


def test_missing_publish_button_fails_before_posting(connected, studio, video):
    studio.mode, studio.posted = "broken", []
    connected.cfg.browser_upload_timeout_seconds = 12
    try:
        with pytest.raises(UploadError) as err:
            connected.upload(video, META, make_clip())
    finally:
        connected.cfg.browser_upload_timeout_seconds = 25
    assert err.value.retryable and "Screenshot:" in str(err.value)
    assert studio.posted == []


def test_daily_upload_limit_pauses_youtube(connected, studio, video):
    studio.mode, studio.posted = "limit", []
    with pytest.raises(UploadError) as err:
        connected.upload(video, META, make_clip())
    assert err.value.pause_seconds == LIMIT_PAUSE_SECONDS and "Tageslimit" in str(err.value)
    assert studio.posted == []


def test_account_without_channel_pauses(connected, studio, video):
    studio.mode, studio.posted = "no_channel", []
    with pytest.raises(UploadError) as err:
        connected.upload(video, META, make_clip())
    assert err.value.pause_seconds == SETUP_PAUSE_SECONDS and "Kanal" in str(err.value)


def test_expired_login_pauses(connected, studio, video):
    studio.mode, studio.posted = "logged_out", []
    with pytest.raises(UploadError) as err:
        connected.upload(video, META, make_clip())
    assert err.value.pause_seconds == SESSION_PAUSE_SECONDS and "auth youtube" in str(err.value)


def test_blocked_google_login_explains_what_to_do(tmp_path, browser_exec, studio):
    studio.mode = "blocked"
    uploader = _uploader(tmp_path, browser_exec, studio.url)
    try:
        with pytest.raises(UploadError) as err:
            uploader.authorize(headless=True, timeout=20)
    finally:
        studio.mode = "normal"
    assert "nicht sicher" in str(err.value) and "Chrome" in str(err.value)
    assert uploader.problem() is not None                                  # nicht als verbunden markiert


def test_youtube_method_selection(tmp_path):
    cfg = AppConfig(streamers=[StreamerConfig("x")], data_dir=tmp_path)
    assert cfg.youtube_method == "browser"                                 # ohne Google-Freigabe → dein Konto
    assert isinstance(make_uploader(cfg, "youtube"), YouTubeBrowserUploader)
    cfg.tokens_dir.mkdir(parents=True)
    (cfg.tokens_dir / "youtube.json").write_text("{}")
    assert cfg.youtube_method == "api"                                     # bestehende API-Verbindung bleibt
    assert isinstance(make_uploader(cfg, "youtube"), YouTubeUploader)
    cfg.youtube.method = "browser"
    assert isinstance(make_uploader(cfg, "youtube"), YouTubeBrowserUploader)


def test_video_id_and_description_helpers():
    assert video_id_from_url("https://youtu.be/abcDEF12345") == "abcDEF12345"
    assert video_id_from_url("https://youtube.com/shorts/abcDEF12345?feature=share") == "abcDEF12345"
    assert video_id_from_url("https://www.youtube.com/watch?v=abcDEF12345") == "abcDEF12345"
    assert video_id_from_url("https://studio.youtube.com/channel/UC1") == ""
    text = youtube_description(PostMetadata(hook="h", title="t", description="a <b> c", hashtags=["x"]), make_clip())
    assert "<" not in text and ">" not in text and "#x" in text
