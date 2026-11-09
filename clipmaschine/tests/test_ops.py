"""Betrieb: Einzelinstanz-Sperre, Zeitplan-Berechnung, Token-Erneuerung, Kommandozeile."""

import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from clipmaschine.__main__ import main
from clipmaschine.config import PostingConfig
from clipmaschine.db import Database
from clipmaschine.lock import AlreadyRunning, SingleInstanceLock
from clipmaschine.oauth import TokenStore
from clipmaschine.scheduler import next_allowed_time
from clipmaschine.uploaders.instagram import InstagramUploader
from clipmaschine.uploaders.youtube import seconds_until_quota_reset

from .conftest import make_clip, needs_ffmpeg
from .test_uploaders import FakeResponse, FakeSession

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def test_single_instance_lock(tmp_path):
    first = SingleInstanceLock(tmp_path / "x.lock")
    first.acquire()
    with pytest.raises(AlreadyRunning) as err:
        SingleInstanceLock(tmp_path / "x.lock").acquire()
    assert str(os.getpid()) in str(err.value)
    first.release()
    with SingleInstanceLock(tmp_path / "x.lock"):
        pass


def test_next_allowed_time_daily_limit_and_hours():
    db = Database(":memory:")
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    for i, hours_ago in enumerate((23, 1)):
        db.upsert_clip(make_clip(f"c{i}"), "top_clips", 1)
        db._execute("INSERT INTO uploads (clip_id, platform, status, attempts, created_at, updated_at) "
                    "VALUES (?, 'tiktok', 'posted', 1, ?, ?)",
                    (f"c{i}", (now - timedelta(hours=hours_ago)).isoformat(),
                     (now - timedelta(hours=hours_ago)).isoformat(timespec="seconds")))
    cfg = PostingConfig(max_posts_per_day=2, min_minutes_between_posts=30, active_hours=None, timezone="UTC")
    when, reason = next_allowed_time(db, "tiktok", cfg, now)
    assert when == now + timedelta(hours=1) and "Tageslimit" in reason

    cfg.active_hours = [14, 22]                                        # erst ab 14 Uhr wieder
    when, _ = next_allowed_time(db, "tiktok", cfg, now)
    assert when == datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)

    cfg = PostingConfig(max_posts_per_day=5, min_minutes_between_posts=90, active_hours=None, timezone="UTC")
    when, reason = next_allowed_time(db, "tiktok", cfg, now)
    assert when == now + timedelta(minutes=30) and "Mindestabstand" in reason
    assert next_allowed_time(db, "youtube", cfg, now)[0] == now


def test_youtube_quota_reset_is_pacific_midnight():
    # 20:00 UTC im Juni = 13:00 PDT → Reset 00:10 PDT = 07:10 UTC am nächsten Tag
    now = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
    assert seconds_until_quota_reset(now) == pytest.approx(11 * 3600 + 600)


def test_instagram_token_auto_refresh(tmp_path):
    from clipmaschine.config import InstagramConfig
    cfg = InstagramConfig(enabled=True, user_id="1", access_token="OLD", graph_host="graph.instagram.com")
    store = TokenStore(tmp_path / "instagram.json")
    session = FakeSession([("GET", "refresh_access_token",
                            FakeResponse(200, {"access_token": "NEW", "expires_in": 5184000}))])
    uploader = InstagramUploader(cfg, session=session, token_store=store)
    assert uploader._token() == "NEW"
    assert uploader._token() == "NEW" and len(session.requests) == 1     # erst in 7 Tagen wieder
    cfg.access_token = "FROM_ENV"                                          # neuer Token in der .env
    session.routes[0] = ("GET", "refresh_access_token", FakeResponse(400, {"error": {"message": "zu jung"}}))
    assert uploader._token() == "FROM_ENV"
    assert store.load()["next_refresh"] > time.time() + 3600               # Retry später, kein Dauerfeuer


@pytest.fixture
def cli_config(tmp_path, monkeypatch):
    for name in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "ANTHROPIC_API_KEY", "CLIPMASCHINE_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    data["streamers"] = [{"login": "streamer_a"}]
    data["metadata"]["use_claude"] = False
    data["editing"].update(width=540, height=960, preset="ultrafast")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_example_config_loads(cli_config):
    from clipmaschine.config import load_config
    cfg = load_config(cli_config)
    assert cfg.streamers[0].login == "streamer_a"
    assert cfg.posting.timezone == "Europe/Berlin" and cfg.editing.layout == "blur"


def test_cli_status_check_health_and_run_once(cli_config, capsys, monkeypatch):
    monkeypatch.setattr("clipmaschine.highlights._list_public_clips", lambda url, limit: [])
    assert main(["-c", str(cli_config), "health"]) == 1               # noch nie gelaufen
    assert main(["-c", str(cli_config), "status"]) == 0
    assert "noch nie gelaufen" in capsys.readouterr().out

    assert main(["-c", str(cli_config), "check"]) == 0                # Twitch-App ist optional
    out = capsys.readouterr().out
    assert "öffentlichen Clip-Seite" in out and "ffmpeg" in out

    assert main(["-c", str(cli_config), "run", "--once", "--dry-run"]) == 0
    assert main(["-c", str(cli_config), "health"]) == 0
    assert main(["-c", str(cli_config), "status"]) == 0
    assert "läuft" in capsys.readouterr().out


def test_cli_rejects_broken_config(tmp_path, capsys):
    bad = tmp_path / "config.yaml"
    bad.write_text("streamers: [x]\nediting:\n  layout: kaputt\n", encoding="utf-8")
    assert main(["-c", str(bad), "status"]) == 2
    assert "editing.layout" in capsys.readouterr().err


@needs_ffmpeg
def test_cli_process_local_file(cli_config, sample_video, capsys):
    target = cli_config.parent / "mein clip.mp4"
    shutil.copy(sample_video, target)
    assert main(["-c", str(cli_config), "process", "--file", str(target), "--streamer", "streamer_a"]) == 0
    assert "Video:" in capsys.readouterr().out
    assert list((cli_config.parent / "clipmaschine_data" / "rendered").glob("*.mp4"))
