from datetime import datetime, timedelta, timezone

from clipmaschine.db import Database
from clipmaschine.highlights import ClipDiscovery, dedupe_moments, velocity_score
from clipmaschine.models import utcnow
from clipmaschine.scheduler import in_active_hours, slot_available

from .conftest import FakeTwitch, helix_clip, make_clip


def test_velocity_prefers_fresh_fast_clips():
    now = datetime.now(timezone.utc)
    fresh = velocity_score(300, now - timedelta(hours=1), now, 1.3)
    old = velocity_score(1000, now - timedelta(hours=20), now, 1.3)
    assert fresh > old


def test_dedupe_by_vod_offset_and_created_at():
    a = make_clip("A", views=500, video_id="v1", vod_offset=100)
    b = make_clip("B", views=200, video_id="v1", vod_offset=110)      # gleicher Moment
    c = make_clip("C", views=150, video_id="v1", vod_offset=400)      # anderer Moment
    kept = dedupe_moments([(b, 2.0), (a, 5.0), (c, 1.0)], 30)
    assert [clip.id for clip, _ in kept] == ["A", "C"]

    now = datetime.now(timezone.utc)
    d = make_clip("D", now=now)
    e = make_clip("E", now=now + timedelta(seconds=10))               # ohne VOD: per Erstellzeit
    assert len(dedupe_moments([(d, 2.0), (e, 1.0)], 30)) == 1


def test_database_upsert_and_upload_tracking():
    db = Database(":memory:")
    clip = make_clip("X1", views=100)
    assert db.upsert_clip(clip, "top_clips", 10.0) is True
    clip.view_count = 250
    assert db.upsert_clip(clip, "top_clips", 20.0) is False
    row = db.get_clip("X1")
    assert row["view_count"] == 250 and row["score"] == 20.0

    hype = make_clip("H1")
    db.upsert_clip(hype, "chat_hype", 1000.0)
    db.upsert_clip(hype, "top_clips", 5.0)                             # Bonus bleibt erhalten
    assert db.get_clip("H1")["score"] == 1000.0

    db.mark("X1", "rendered", rendered_path="/tmp/x.mp4", meta_json="{}")
    db.mark("H1", "rendered", rendered_path="/tmp/h.mp4", meta_json="{}")
    old = utcnow() - timedelta(days=5)
    assert db.next_for_platform("tiktok", 3, old)["id"] == "H1"        # höchster Score zuerst

    db.record_upload("H1", "tiktok", "failed", error="kaputt")
    assert db.next_for_platform("tiktok", 3, old)["id"] == "H1"        # erneuter Versuch erlaubt
    db.give_up_upload("H1", "tiktok", 3)
    assert db.next_for_platform("tiktok", 3, old)["id"] == "X1"
    db.record_upload("X1", "tiktok", "posted", remote_id="p1")
    assert db.next_for_platform("tiktok", 3, old) is None
    assert db.posts_since("tiktok", utcnow() - timedelta(hours=1)) == 1
    assert db.last_post_at("tiktok") is not None
    assert db.upload_statuses("H1")["tiktok"]["attempts"] == 3


def test_overlap_check_against_database():
    db = Database(":memory:")
    db.upsert_clip(make_clip("A", video_id="v1", vod_offset=100), "top_clips", 1)
    assert db.has_overlapping(make_clip("B", video_id="v1", vod_offset=115), 30)
    assert not db.has_overlapping(make_clip("C", video_id="v1", vod_offset=300), 30)
    now = utcnow()
    db.upsert_clip(make_clip("D", login="streamer_b", now=now), "top_clips", 1)
    assert db.has_overlapping(make_clip("E", login="streamer_b", now=now + timedelta(seconds=20)), 30)
    assert not db.has_overlapping(make_clip("F", login="streamer_b", now=now + timedelta(minutes=5)), 30)


def test_discovery_filters_ranks_and_limits(app_config):
    app_config.discovery.max_new_per_streamer = 2
    app_config.discovery.exclude_keywords = ["werbung"]
    clips = {
        "streamer_a": [
            helix_clip("a_top", views=900, age_hours=2, video_id="v", vod_offset=100),
            helix_clip("a_dupe", views=800, age_hours=2, video_id="v", vod_offset=110),
            helix_clip("a_few_views", views=10),
            helix_clip("a_too_long", views=500, duration=90),
            helix_clip("a_ad", views=700, title="Werbung für XY"),
            helix_clip("a_second", views=300, age_hours=3, video_id="v", vod_offset=900),
            helix_clip("a_third", views=200, age_hours=4, video_id="v", vod_offset=2000),
        ],
        "streamer_b": [helix_clip("b_one", login="streamer_b", views=60)],
    }
    db = Database(":memory:")
    stored = ClipDiscovery(app_config, FakeTwitch(clips), db).discover()
    ids = [clip.id for clip, _ in stored]
    assert ids == ["a_top", "a_second", "b_one"]
    assert db.get_clip("a_dupe") is None and db.get_clip("a_third") is None


def test_active_hours_and_slots():
    assert in_active_hours(datetime(2026, 1, 1, 12), [10, 23])
    assert not in_active_hours(datetime(2026, 1, 1, 23, 30), [10, 23])
    assert in_active_hours(datetime(2026, 1, 1, 1), [18, 2])            # über Mitternacht
    assert not in_active_hours(datetime(2026, 1, 1, 5), [18, 2])
    assert in_active_hours(datetime(2026, 1, 1, 5), None)

    from clipmaschine.config import PostingConfig
    cfg = PostingConfig(max_posts_per_day=2, min_minutes_between_posts=60, active_hours=None)
    db = Database(":memory:")
    now = utcnow()
    assert slot_available(db, "tiktok", cfg, now)[0]
    db.upsert_clip(make_clip("P1"), "top_clips", 1)
    db.record_upload("P1", "tiktok", "posted")
    ok, reason = slot_available(db, "tiktok", cfg, now)
    assert not ok and "nächster Post" in reason
    assert slot_available(db, "tiktok", cfg, now + timedelta(minutes=61))[0]
    assert slot_available(db, "youtube", cfg, now)[0]                   # Limits gelten pro Plattform


def _public_entry(slug, views, age_hours, duration=20.0, title="Clip", login="streamer_a"):
    created = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return {"url": f"https://www.twitch.tv/{login}/clip/{slug}", "id": "12345", "title": title,
            "view_count": views, "duration": duration, "timestamp": created.timestamp(), "language": "de"}


def test_discovery_without_twitch_credentials_uses_public_clip_page(app_config, monkeypatch):
    from clipmaschine import highlights
    calls = []

    def fake_list(url, limit):
        calls.append((url, limit))
        login = url.split("/")[3]
        if login != "streamer_a":
            return []
        return [_public_entry("TopClip-abc", 800, 2), _public_entry("ZweiterClip", 300, 5),
                _public_entry("ZuAlt", 5000, 30), _public_entry("ZuWenig", 3, 1), {"url": "kaputt"}]

    monkeypatch.setattr(highlights, "_list_public_clips", fake_list)
    db = Database(":memory:")
    stored = ClipDiscovery(app_config, None, db).discover()
    assert [clip.id for clip, _ in stored] == ["TopClip-abc", "ZweiterClip"]
    assert calls[0][0] == "https://www.twitch.tv/streamer_a/clips?filter=clips&range=24hr"
    row = db.get_clip("TopClip-abc")
    assert row["broadcaster_login"] == "streamer_a" and row["url"].endswith("/clip/TopClip-abc")


def test_discovery_falls_back_to_public_page_when_api_fails(app_config, monkeypatch):
    from clipmaschine import highlights
    from clipmaschine.twitch_api import TwitchAPIError

    class BrokenTwitch(FakeTwitch):
        def get_clips(self, *args, **kwargs):
            raise TwitchAPIError("HTTP 503")

    monkeypatch.setattr(highlights, "_list_public_clips",
                        lambda url, limit: [_public_entry("Ersatz", 500, 1)] if "streamer_a" in url else [])
    db = Database(":memory:")
    stored = ClipDiscovery(app_config, BrokenTwitch({"streamer_a": [], "streamer_b": []}), db).discover()
    assert [clip.id for clip, _ in stored] == ["Ersatz"]


def test_public_period_mapping():
    from clipmaschine.highlights import public_period
    assert [public_period(h) for h in (6, 24, 48, 168, 500, 2000)] == ["24hr", "24hr", "7d", "7d", "30d", "all"]
