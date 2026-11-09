"""End-to-End: Suche → Download → Schnitt (echtes ffmpeg) → Posten nach Zeitplan, inkl. Selbstheilung."""

import logging
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from clipmaschine.downloader import DownloadError
from clipmaschine.editor import probe
from clipmaschine.pipeline import ClipMachine
from clipmaschine.uploaders.base import UploadError, UploadResult, Uploader

from .conftest import FakeTwitch, helix_clip, needs_ffmpeg

pytestmark = needs_ffmpeg


class FakeUploader(Uploader):
    def __init__(self, name, errors=None):
        self.name = name
        self.errors = list(errors or [])
        self.uploads = []
        self.connected = None

    def connected_at(self):
        return self.connected

    def upload(self, video_path, meta, clip):
        assert video_path.is_file()
        if self.errors:
            raise self.errors.pop(0)
        self.uploads.append(clip.id)
        return UploadResult("posted", remote_id=f"{self.name}-{clip.id}", url=f"https://{self.name}.example/{clip.id}")


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


CLIPS = {
    "streamer_a": [
        helix_clip("a1", views=900, age_hours=1, title="Unmöglicher Clutch", video_id="v", vod_offset=100),
        helix_clip("a2", views=500, age_hours=2, title="Rage Moment", video_id="v", vod_offset=900),
        helix_clip("a3", views=300, age_hours=3, title="Chat eskaliert", video_id="v", vod_offset=2000),
    ],
    "streamer_b": [helix_clip("b1", login="streamer_b", views=120, age_hours=1, title="Fail des Tages")],
}


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr("clipmaschine.pipeline.utcnow", c)
    monkeypatch.setattr("clipmaschine.db.utcnow", c)
    return c


@pytest.fixture
def downloads(monkeypatch, sample_video):
    state = {"calls": [], "errors": []}

    def fake_download(url, dest_dir, basename):
        state["calls"].append(url)
        if state["errors"]:
            raise state["errors"].pop(0)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / f"{basename}.mp4"
        shutil.copy(sample_video, target)
        return target

    monkeypatch.setattr("clipmaschine.pipeline.download_clip", fake_download)
    return state


def make_machine(cfg, uploaders, **kwargs):
    return ClipMachine(cfg, uploaders=uploaders, twitch=FakeTwitch(CLIPS), **kwargs)


def test_full_cycle_with_limits_and_auto_resume(app_config, clock, downloads, caplog):
    caplog.set_level(logging.INFO, logger="clipmaschine")
    p = app_config.posting
    p.max_posts_per_day, p.min_minutes_between_posts, p.queue_size = 2, 60, 2
    tiktok, youtube = FakeUploader("tiktok"), FakeUploader("youtube")
    machine = make_machine(app_config, [tiktok, youtube])

    machine.run_cycle()
    # bester Clip zuerst, auf beiden Plattformen
    assert tiktok.uploads == ["a1"] and youtube.uploads == ["a1"]
    assert machine.db.get_clip("a1")["status"] == "done"
    assert not (machine.rendered_dir / "a1.mp4").exists()            # nach dem Posten aufgeräumt
    rendered = machine.db.clips_with_status("rendered")
    assert [r["id"] for r in rendered] == ["a2"]
    info = probe(rendered[0]["rendered_path"])
    assert (info.width, info.height) == (540, 960)
    assert machine.heartbeat_path.is_file()
    assert not list(machine.downloads_dir.glob("*"))                  # Downloads gelöscht

    machine.run_cycle()                                               # Mindestabstand → nichts posten
    assert tiktok.uploads == ["a1"]
    assert len(machine.db.clips_with_status("rendered")) == 2         # Warteschlange aufgefüllt
    assert "nächster Post automatisch ab" in caplog.text

    clock.advance(minutes=61)
    machine.run_cycle()                                               # macht automatisch weiter
    assert tiktok.uploads == ["a1", "a2"] and youtube.uploads == ["a1", "a2"]

    caplog.clear()
    clock.advance(minutes=61)
    machine.run_cycle()
    assert tiktok.uploads == ["a1", "a2"]                             # Tageslimit 2 erreicht
    assert "Tageslimit erreicht" in caplog.text

    clock.advance(hours=23)
    machine.run_cycle()                                               # neuer Tag → weiter geht's
    assert tiktok.uploads == ["a1", "a2", "a3"]


def test_upload_errors_retry_pause_and_give_up(app_config, clock, downloads):
    app_config.posting.min_minutes_between_posts = 0
    app_config.posting.queue_size = 3
    flaky = FakeUploader("tiktok", errors=[
        UploadError("Netzwerk weg"),                                  # 1. Versuch: später erneut
        UploadError("Quota", pause_seconds=3600),                     # 2. Versuch: Plattform pausiert
    ])
    strict = FakeUploader("instagram", errors=[UploadError("Video abgelehnt", retryable=False)])
    machine = make_machine(app_config, [flaky, strict])

    machine.run_cycle()
    statuses = machine.db.upload_statuses("a1")
    assert statuses["tiktok"]["status"] == "failed" and statuses["tiktok"]["attempts"] == 1
    assert statuses["instagram"]["attempts"] == app_config.posting.max_attempts   # aufgegeben

    machine.run_cycle()                                               # Retry → Quota-Fehler → Pause
    assert machine.db.upload_statuses("a1")["tiktok"]["attempts"] == 1            # zählt nicht als Versuch
    assert "tiktok" in machine._paused_until
    machine.run_cycle()
    assert flaky.uploads == []                                        # noch pausiert

    clock.advance(minutes=61)
    machine.run_cycle()                                               # Pause vorbei → Erfolg
    assert flaky.uploads == ["a1"]
    assert strict.uploads == ["a2", "a3"]                             # Instagram macht mit den nächsten Clips weiter
    assert machine.db.get_clip("a1")["status"] == "done"


def test_prepare_failure_is_retried_later(app_config, clock, downloads):
    app_config.posting.queue_size = 1
    app_config.posting.min_minutes_between_posts = 0
    downloads["errors"].append(DownloadError("Twitch hakt"))
    uploader = FakeUploader("tiktok")
    machine = make_machine(app_config, [uploader])

    machine.run_cycle()
    row = machine.db.get_clip("a1")
    assert row["status"] == "discovered" and row["attempts"] == 1 and row["retry_after"]
    machine.run_cycle()
    assert machine.db.get_clip("a1")["status"] == "discovered"        # Wartezeit noch nicht um
    assert uploader.uploads == ["a2"]                                 # solange geht es mit dem nächsten weiter

    clock.advance(minutes=6)
    machine.run_cycle()
    assert machine.db.get_clip("a1")["status"] == "done"              # zweiter Versuch hat geklappt
    assert uploader.uploads == ["a2", "a1"]

    downloads["errors"].extend([DownloadError("weg")] * 3)            # dauerhaft kaputt → aufgeben
    for _ in range(3):
        clock.advance(minutes=30)
        machine.run_cycle()
    assert machine.db.get_clip("a3")["status"] == "failed"
    assert machine.db.get_clip("a3")["attempts"] == app_config.posting.max_attempts


def test_missing_video_is_rendered_again(app_config, clock, downloads):
    app_config.posting.queue_size = 1
    uploader = FakeUploader("tiktok")
    machine = make_machine(app_config, [uploader])
    app_config.posting.active_hours = [0, 0]
    machine.prepare_pending()
    path = machine.db.get_clip("a1")["rendered_path"] if machine.db.get_clip("a1") else None
    assert path is None                                               # ohne Suche noch keine Clips

    machine.discovery.discover(clock())
    machine.prepare_pending()
    (machine.rendered_dir / "a1.mp4").unlink()                        # Datei geht verloren
    machine.post_ready()
    assert machine.db.get_clip("a1")["status"] == "discovered"
    machine.run_cycle()
    assert uploader.uploads == ["a1"]


def test_dry_run_and_render_only(app_config, clock, downloads):
    app_config.posting.queue_size = 1
    machine = make_machine(app_config, [FakeUploader("tiktok")], dry_run=True)
    machine.run_cycle()
    assert machine.db.upload_statuses("a1")["tiktok"]["status"] == "dry_run"
    assert machine.db.get_clip("a1")["status"] == "done"
    assert (machine.rendered_dir / "a1.mp4").exists()                 # im Dry-Run bleibt das Video liegen

    cfg2 = app_config
    cfg2.data_dir = app_config.data_dir.parent / "data2"
    render_only = make_machine(cfg2, [])
    render_only.run_cycle()
    done = render_only.db.clips_with_status("done")
    assert len(done) == cfg2.posting.queue_size
    assert all((render_only.rendered_dir / f"{row['id']}.mp4").exists() for row in done)


def test_process_local_file(app_config, sample_video, clock, capsys):
    machine = ClipMachine(app_config, uploaders=[])
    out = machine.process_manual(file=sample_video, streamer="Streamer_A", title="Mein Testclip")
    assert out.is_file()
    printed = capsys.readouterr().out
    assert "Mein Testclip" in printed and "#streamer_a" in printed


def test_leftovers_are_cleaned_on_start(app_config):
    machine = ClipMachine(app_config, uploaders=[])
    (machine.rendered_dir / ".work_x").mkdir(parents=True)
    machine.downloads_dir.mkdir(parents=True)
    (machine.downloads_dir / "halb.mp4.part").write_bytes(b"x")
    machine.cleanup_leftovers()
    assert not (machine.rendered_dir / ".work_x").exists()
    assert not list(machine.downloads_dir.iterdir())


def test_reconnect_ends_login_pause_immediately(app_config, clock, downloads):
    import time as _time
    app_config.posting.min_minutes_between_posts = 0
    app_config.posting.queue_size = 1
    tiktok = FakeUploader("tiktok", errors=[UploadError("Anmeldung abgelaufen", pause_seconds=6 * 3600)])
    machine = make_machine(app_config, [tiktok])

    machine.run_cycle()
    assert "tiktok" in machine._paused_until
    machine.run_cycle()
    assert tiktok.uploads == []                                       # pausiert

    tiktok.connected = _time.time() + 1                               # "python -m clipmaschine auth tiktok"
    machine.run_cycle()                                               # ohne 6 h zu warten
    assert tiktok.uploads == ["a1"] and "tiktok" not in machine._paused_until
