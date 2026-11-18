"""Serien-Posting: mehrere Clips direkt nacheinander, danach die normale Pause."""

from datetime import datetime, timedelta, timezone

import pytest

from clipmaschine.config import ConfigError, PostingConfig, load_config
from clipmaschine.db import Database
from clipmaschine.scheduler import burst_position, next_allowed_time

from .conftest import make_clip

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
SERIES = PostingConfig(max_posts_per_day=10, min_minutes_between_posts=240, burst_size=3, burst_gap_minutes=10,
                       active_hours=None, timezone="UTC")


def _db_with_posts(minutes_ago: list[float], platform: str = "tiktok") -> Database:
    db = Database(":memory:")
    for i, ago in enumerate(minutes_ago):
        db.upsert_clip(make_clip(f"c{i}"), "top_clips", 1)
        stamp = (NOW - timedelta(minutes=ago)).isoformat(timespec="seconds")
        db._execute("INSERT INTO uploads (clip_id, platform, status, attempts, created_at, updated_at) "
                    "VALUES (?, ?, 'posted', 1, ?, ?)", (f"c{i}", platform, stamp, stamp))
    return db


def test_series_posts_three_clips_ten_minutes_apart_then_pauses():
    assert next_allowed_time(_db_with_posts([]), "tiktok", SERIES, NOW) == (NOW, "")

    when, reason = next_allowed_time(_db_with_posts([2]), "tiktok", SERIES, NOW)         # 1. Clip der Serie
    assert when == NOW + timedelta(minutes=8) and reason == "nächster Clip der Serie"

    when, reason = next_allowed_time(_db_with_posts([12, 2]), "tiktok", SERIES, NOW)     # 2. Clip
    assert when == NOW + timedelta(minutes=8) and reason == "nächster Clip der Serie"

    when, reason = next_allowed_time(_db_with_posts([22, 12, 2]), "tiktok", SERIES, NOW)  # Serie voll → Pause
    assert when == NOW + timedelta(minutes=238) and reason == "Mindestabstand zwischen Posts"


def test_series_starts_fresh_after_the_pause():
    # alte Serie vor über 4 Stunden, dann ein neuer Clip → das ist Clip 1 der neuen Serie
    db = _db_with_posts([300, 290, 280, 5])
    when, reason = next_allowed_time(db, "tiktok", SERIES, NOW)
    assert when == NOW + timedelta(minutes=5) and reason == "nächster Clip der Serie"


def test_slow_render_still_counts_as_part_of_the_series():
    # 2. Clip kam erst nach 35 Minuten (Rendern dauerte), gehört aber noch zur Serie
    times = [NOW - timedelta(minutes=m) for m in (1, 36)]
    assert burst_position(times, SERIES) == 2
    when, _ = next_allowed_time(_db_with_posts([36, 1]), "tiktok", SERIES, NOW)
    assert when == NOW + timedelta(minutes=9)


def test_daily_limit_still_wins_over_series():
    cfg = PostingConfig(max_posts_per_day=2, min_minutes_between_posts=240, burst_size=3, burst_gap_minutes=10,
                        active_hours=None, timezone="UTC")
    when, reason = next_allowed_time(_db_with_posts([12, 2]), "tiktok", cfg, NOW)
    assert "Tageslimit" in reason and when > NOW + timedelta(hours=23)


def test_without_series_the_normal_gap_applies():
    cfg = PostingConfig(max_posts_per_day=10, min_minutes_between_posts=60, active_hours=None, timezone="UTC")
    when, reason = next_allowed_time(_db_with_posts([2]), "tiktok", cfg, NOW)
    assert when == NOW + timedelta(minutes=58) and reason == "Mindestabstand zwischen Posts"


def test_series_settings_are_validated(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("streamers: [x]\nposting:\n  burst_size: 3\n  burst_gap_minutes: 300\n"
                      "  min_minutes_between_posts: 240\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="burst_gap_minutes"):
        load_config(config)
    config.write_text("streamers: [x]\nposting:\n  burst_size: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="burst_size"):
        load_config(config)
