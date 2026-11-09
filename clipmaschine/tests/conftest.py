from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipmaschine.config import AppConfig, EditingConfig, MetadataConfig, StreamerConfig
from clipmaschine.models import Clip

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg nicht installiert")


def make_video(path: Path, size: str = "1280x720", seconds: float = 2.0, audio: bool = True) -> Path:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg nicht installiert")
    return make_video(tmp_path_factory.mktemp("media") / "sample.mp4")


@pytest.fixture
def app_config(tmp_path) -> AppConfig:
    cfg = AppConfig(
        streamers=[StreamerConfig(login="streamer_a", hashtags=["teama"]), StreamerConfig(login="streamer_b")],
        data_dir=tmp_path / "data",
        editing=EditingConfig(width=540, height=960, preset="ultrafast", crf=30),
        metadata=MetadataConfig(use_claude=False),
    )
    cfg.posting.active_hours = None
    return cfg


def make_clip(clip_id: str = "Clip1", login: str = "streamer_a", views: int = 100, age_hours: float = 1.0,
              duration: float = 20.0, video_id: str = "", vod_offset: int | None = None,
              title: str = "Unglaublicher Moment", now: datetime | None = None) -> Clip:
    now = now or datetime.now(timezone.utc)
    return Clip(
        id=clip_id, url=f"https://clips.twitch.tv/{clip_id}", broadcaster_id=f"id_{login}",
        broadcaster_login=login, broadcaster_name=login.title(), title=title, view_count=views,
        created_at=now - timedelta(hours=age_hours), duration=duration, game_name="Minecraft",
        video_id=video_id, vod_offset=vod_offset,
    )


def helix_clip(clip_id: str, login: str = "streamer_a", views: int = 100, age_hours: float = 1.0,
               duration: float = 20.0, title: str = "Clip", video_id: str = "", vod_offset=None,
               now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    created = (now - timedelta(hours=age_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "id": clip_id, "url": f"https://clips.twitch.tv/{clip_id}", "broadcaster_id": f"id_{login}",
        "broadcaster_name": login.title(), "title": title, "view_count": views, "created_at": created,
        "duration": duration, "game_id": "27471", "language": "de", "video_id": video_id, "vod_offset": vod_offset,
    }


class FakeTwitch:
    """Minimaler Ersatz für TwitchAPI."""

    def __init__(self, clips_by_login: dict[str, list[dict]]):
        self.clips_by_login = clips_by_login
        self.calls: list[tuple] = []

    def get_users(self, logins=None, ids=None):
        return {login: {"id": f"id_{login}", "login": login} for login in (logins or [])
                if login in self.clips_by_login}

    def get_clips(self, broadcaster_id, started_at, ended_at, limit=100, featured_only=False):
        self.calls.append(("get_clips", broadcaster_id))
        login = broadcaster_id.removeprefix("id_")
        return self.clips_by_login.get(login, [])[:limit]

    def game_names(self, ids):
        return {gid: "Minecraft" for gid in ids if gid}
