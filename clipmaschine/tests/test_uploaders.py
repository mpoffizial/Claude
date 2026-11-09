import time

import pytest

from clipmaschine.config import InstagramConfig, TikTokConfig
from clipmaschine.models import PostMetadata
from clipmaschine.oauth import TokenStore, pkce_pair
from clipmaschine.uploaders.base import UploadError, build_caption, truncate_utf16, utf16_len
from clipmaschine.uploaders.instagram import InstagramUploader
from clipmaschine.uploaders.tiktok import TikTokUploader, chunk_plan
from clipmaschine.uploaders.youtube import youtube_tags, youtube_title

from .conftest import make_clip

META = PostMetadata(hook="Hook", title="Krasser Clip 😳", description="Original: twitch.tv/streamer_a",
                    hashtags=["streamer_a", "minecraft", "fyp"])


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.ok = status_code < 400

    def json(self):
        if self._payload is None:
            raise ValueError("kein JSON")
        return self._payload


class FakeSession:
    def __init__(self, routes):
        self.routes = routes        # Liste von (methode, url-teil, antwort oder callable)
        self.requests = []

    def _handle(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        for route_method, fragment, response in self.routes:
            if route_method == method and fragment in url:
                return response(kwargs) if callable(response) else response
        raise AssertionError(f"Unerwarteter Request: {method} {url}")

    def post(self, url, **kwargs):
        return self._handle("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._handle("PUT", url, **kwargs)

    def request(self, method, url, **kwargs):
        return self._handle(method, url, **kwargs)


def test_chunk_plan_rules():
    assert (chunk_plan(1_000_000).chunk_size, chunk_plan(1_000_000).count) == (1_000_000, 1)
    assert chunk_plan(64_000_000).count == 1
    plan = chunk_plan(155_000_000)
    assert (plan.chunk_size, plan.count) == (10_000_000, 15)
    ranges = list(plan.ranges(155_000_000))
    assert ranges[0] == (0, 9_999_999)
    assert ranges[-1] == (140_000_000, 154_999_999)              # Rest hängt am letzten Teil
    assert all(b[0] == a[1] + 1 for a, b in zip(ranges, ranges[1:]))
    with pytest.raises(UploadError):
        chunk_plan(0)


def test_caption_helpers():
    caption = build_caption(META)
    assert caption.startswith("Krasser Clip 😳\n\nOriginal") and caption.endswith("#streamer_a #minecraft #fyp")
    assert utf16_len("😳") == 2
    assert utf16_len(truncate_utf16("😳" * 50, 11)) <= 11
    assert youtube_title(META, True) == "Krasser Clip 😳 #Shorts"
    long = PostMetadata(hook="h", title="x" * 120, description="", hashtags=[])
    assert len(youtube_title(long, True)) == 100
    assert sum(len(t) + 1 for t in youtube_tags(["a" * 100] * 10)) <= 450


def test_pkce_variants():
    verifier, challenge = pkce_pair("hex")
    assert 43 <= len(verifier) <= 128 and len(challenge) == 64
    _, challenge_b64 = pkce_pair()
    assert len(challenge_b64) == 43 and "=" not in challenge_b64


def _tiktok(tmp_path, routes, **cfg_kwargs):
    store = TokenStore(tmp_path / "tiktok.json")
    store.save({"access_token": "tok", "refresh_token": "ref", "expires_at": time.time() + 3600,
                "refresh_expires_at": time.time() + 86400})
    cfg = TikTokConfig(enabled=True, client_key="k", client_secret="s", **cfg_kwargs)
    session = FakeSession(routes)
    return TikTokUploader(cfg, store, session=session), session


def test_tiktok_direct_post(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 1234)
    ok = {"error": {"code": "ok", "message": ""}}
    uploader, session = _tiktok(tmp_path, [
        ("POST", "creator_info", FakeResponse(200, {**ok, "data": {
            "privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"], "max_video_post_duration_sec": 600}})),
        ("POST", "/video/init/", FakeResponse(200, {**ok, "data": {
            "publish_id": "pub1", "upload_url": "https://upload.example/u"}})),
        ("PUT", "upload.example", FakeResponse(201)),
        ("POST", "status/fetch", FakeResponse(200, {**ok, "data": {"status": "PUBLISH_COMPLETE"}})),
    ], privacy_level="PUBLIC_TO_EVERYONE")
    result = uploader.upload(video, META, make_clip())
    assert (result.status, result.remote_id) == ("posted", "pub1")

    init = next(kw for m, url, kw in session.requests if url.endswith("/video/init/"))
    assert init["json"]["post_info"]["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert init["json"]["post_info"]["title"].startswith("Krasser Clip")
    assert init["json"]["source_info"] == {"source": "FILE_UPLOAD", "video_size": 1234,
                                           "chunk_size": 1234, "total_chunk_count": 1}
    assert init["headers"]["Authorization"] == "Bearer tok"
    put = next(kw for m, url, kw in session.requests if m == "PUT")
    assert put["headers"]["Content-Range"] == "bytes 0-1233/1234"


def test_tiktok_inbox_and_unaudited_error(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 10)
    ok = {"error": {"code": "ok"}}
    uploader, session = _tiktok(tmp_path, [
        ("POST", "/inbox/video/init/", FakeResponse(200, {**ok, "data": {"publish_id": "p", "upload_url": "https://u.x/"}})),
        ("PUT", "u.x", FakeResponse(201)),
        ("POST", "status/fetch", FakeResponse(200, {**ok, "data": {"status": "SEND_TO_USER_INBOX"}})),
    ], mode="inbox")
    assert uploader.upload(video, META, make_clip()).status == "inbox"
    assert uploader.scopes == "user.info.basic,video.upload"

    uploader, _ = _tiktok(tmp_path, [
        ("POST", "creator_info", FakeResponse(200, {**ok, "data": {"privacy_level_options": ["SELF_ONLY"]}})),
        ("POST", "/video/init/", FakeResponse(403, {"error": {
            "code": "unaudited_client_can_only_post_to_private_accounts", "message": "nope"}})),
    ])
    with pytest.raises(UploadError) as err:
        uploader.upload(video, META, make_clip())
    assert not err.value.retryable and "Audit" in str(err.value)


def test_instagram_reel_flow_and_redaction(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"y" * 99)
    statuses = iter([{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}])
    cfg = InstagramConfig(enabled=True, user_id="1784", access_token="SECRET", api_version="v23.0")
    session = FakeSession([
        ("POST", "/1784/media_publish", FakeResponse(200, {"id": "media9"})),
        ("POST", "/1784/media", FakeResponse(200, {"id": "cont1", "uri": "https://rupload.facebook.com/ig-api-upload/v23.0/cont1"})),
        ("POST", "rupload.facebook.com", FakeResponse(200, {"success": True})),
        ("GET", "/cont1", lambda kw: FakeResponse(200, next(statuses))),
        ("GET", "/media9", FakeResponse(200, {"permalink": "https://instagram.com/reel/abc"})),
    ])
    result = InstagramUploader(cfg, session=session, poll_seconds=0).upload(video, META, make_clip())
    assert (result.remote_id, result.url) == ("media9", "https://instagram.com/reel/abc")
    create = session.requests[0][2]
    assert create["data"]["media_type"] == "REELS" and create["data"]["upload_type"] == "resumable"
    upload = session.requests[1][2]
    assert upload["headers"]["file_size"] == "99" and upload["headers"]["offset"] == "0"

    failing = FakeSession([("POST", "/media", FakeResponse(400, {"error": {
        "message": "Invalid token access_token=SECRET", "code": 190}}))])
    with pytest.raises(UploadError) as err:
        InstagramUploader(cfg, session=failing).upload(video, META, make_clip())
    assert "SECRET" not in str(err.value) and not err.value.retryable
