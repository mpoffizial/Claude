"""Spiele-Modus (z. B. GTA 6), Voreinstellungen, übersprungene Plattformen und Weiterempfehlungs-Check."""

import shutil
import subprocess
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest

from clipmaschine.config import PACKAGE_DIR, MetadataConfig, load_config
from clipmaschine.db import Database
from clipmaschine.highlights import ClipDiscovery, best_category_match, normalize_game_name
from clipmaschine.metadata import MetadataContext, template_metadata, with_prefix
from clipmaschine.models import utcnow
from clipmaschine.setup_wizard import preset_path, run_setup
from clipmaschine.uploaders.base import ReviewResult

from .conftest import helix_clip, make_clip, needs_ffmpeg
from .test_pipeline import FakeUploader, clock, downloads, make_machine  # noqa: F401  (Fixtures)
from .test_setup_wizard import scripted


class GameTwitch:
    """Fake-Twitch-API für Clips nach Spiel."""

    def __init__(self, clips, category_exists=True):
        self.clips = clips
        self.category_exists = category_exists
        self.clip_calls = []

    def get_games(self, names):
        return {}                                             # exakter Name "GTA 6" existiert nicht …

    def search_categories(self, query, limit=20):             # … die Suche findet die echte Kategorie
        if not self.category_exists:
            return [{"id": "5", "name": "Grand Theft Auto V"}]
        return [{"id": "5", "name": "Grand Theft Auto V"}, {"id": "66", "name": "Grand Theft Auto VI"}]

    def get_clips(self, broadcaster_id, started_at, ended_at, limit=100, featured_only=False, game_id=None):
        self.clip_calls.append((game_id, started_at))
        return self.clips[:limit]

    def get_users(self, logins=None, ids=None):
        return {i.removeprefix("id_"): {"id": i, "login": i.removeprefix("id_")} for i in (ids or [])}

    def game_names(self, ids):
        return {}


def game_clips():
    return [
        helix_clip("big1", login="alpha", views=5000, age_hours=1, video_id="v1", vod_offset=100),
        helix_clip("big2", login="alpha", views=4000, age_hours=1, video_id="v1", vod_offset=900),
        helix_clip("big3", login="alpha", views=3000, age_hours=1, video_id="v1", vod_offset=2000),
        helix_clip("beta1", login="beta", views=2000, age_hours=2),
        helix_clip("blocked", login="keinrepost", views=9000, age_hours=1),
        helix_clip("gamma1", login="gamma", views=900, age_hours=3),
        helix_clip("tiny", login="delta", views=5, age_hours=1),
    ]


def test_game_name_matching():
    names = ["GTA 6", "gta6", "GTA VI", "Grand Theft Auto 6", "Grand Theft Auto VI"]
    assert {normalize_game_name(n) for n in names} == {"grandtheftautovi"}
    assert normalize_game_name("Grand Theft Auto V") != normalize_game_name("GTA 6")
    cats = [{"id": "5", "name": "Grand Theft Auto V"}, {"id": "66", "name": "Grand Theft Auto VI"}]
    assert best_category_match("GTA 6", cats)["id"] == "66"
    assert best_category_match("GTA 6", cats[:1]) is None


def test_game_discovery_across_all_streamers(app_config):
    app_config.streamers = []
    d = app_config.discovery
    d.games, d.max_per_broadcaster_per_day, d.exclude_broadcasters = ["GTA 6"], 2, ["KeinRepost"]
    api, db = GameTwitch(game_clips()), Database(":memory:")
    discovery = ClipDiscovery(app_config, api, db)
    stored = discovery.discover()
    assert [clip.id for clip, _ in stored] == ["big1", "big2", "beta1", "gamma1"]   # max. 2 je Streamer
    row = db.get_clip("big1")
    assert row["broadcaster_login"] == "alpha" and row["game_name"] == "Grand Theft Auto VI"
    assert api.clip_calls[0][0] == "66"
    assert discovery.discover() == []                           # Tageslimit je Streamer gilt auch später
    assert db.get_clip("big3") is None and db.get_clip("blocked") is None


def test_game_discovery_waits_for_release_and_category(app_config):
    app_config.streamers = []
    app_config.discovery.games = ["GTA 6"]
    app_config.discovery.games_start_at = (utcnow() + timedelta(days=3)).date().isoformat()
    api = GameTwitch(game_clips())
    assert ClipDiscovery(app_config, api, Database(":memory:")).discover() == []
    assert api.clip_calls == []                                 # vor dem Release wird nichts abgefragt

    app_config.discovery.games_start_at = None
    missing = GameTwitch(game_clips(), category_exists=False)
    assert ClipDiscovery(app_config, missing, Database(":memory:")).discover() == []
    assert missing.clip_calls == []                             # Kategorie fehlt noch → geduldig warten

    app_config.discovery.games_start_at = (utcnow() - timedelta(hours=2)).date().isoformat()
    api = GameTwitch(game_clips())
    ClipDiscovery(app_config, api, Database(":memory:")).discover()
    assert api.clip_calls and api.clip_calls[0][1] >= utcnow() - timedelta(hours=app_config.discovery.lookback_hours)


def test_gta6_preset_is_valid(tmp_path, monkeypatch):
    for name in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "CLIPMASCHINE_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    shutil.copy(preset_path("gta6"), tmp_path / "config.yaml")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.discovery.games == ["Grand Theft Auto VI"] and cfg.discovery.games_start_at == "2026-11-19"
    assert cfg.tiktok.enabled and cfg.instagram.enabled and cfg.youtube.enabled
    assert cfg.tiktok_method == "browser" and cfg.tiktok.check_recommendation
    assert cfg.metadata.title_prefix == "GTA 6: " and "gta6" in cfg.metadata.default_hashtags
    with pytest.raises(ValueError):
        preset_path("../etc")


def test_title_prefix():
    assert with_prefix("Wilder Stunt", "GTA 6: ") == "GTA 6: Wilder Stunt"
    assert with_prefix("Mein erster GTA 6 Raub", "GTA 6: ") == "Mein erster GTA 6 Raub"
    meta = template_metadata(MetadataContext(clip=make_clip(title="Wilder Stunt")),
                             MetadataConfig(use_claude=False, title_prefix="GTA 6: "))
    assert meta.title.startswith("GTA 6: Wilder Stunt")


def test_wizard_with_preset(tmp_path, monkeypatch, capsys):
    for name in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "ANTHROPIC_API_KEY", "CLIPMASCHINE_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    config = run_setup(tmp_path, ask=scripted("meine-id"), ask_secret=scripted("mein-secret", ""),
                       connect=False, preset="gta6")
    cfg = load_config(config)
    assert cfg.discovery.games == ["Grand Theft Auto VI"]
    assert cfg.twitch.client_id == "meine-id" and cfg.twitch.client_secret == "mein-secret"
    out = capsys.readouterr().out
    assert "Grand Theft Auto VI" in out and "gta6_profil.md" in out and "auth youtube" in out


@needs_ffmpeg
def test_unconnected_platform_does_not_block(app_config, clock, downloads):  # noqa: F811
    app_config.posting.min_minutes_between_posts = 0
    app_config.posting.queue_size = 1
    tiktok, youtube = FakeUploader("tiktok"), FakeUploader("youtube")
    youtube.problem = lambda: "Noch nicht angemeldet: python -m clipmaschine auth youtube"
    machine = make_machine(app_config, [tiktok, youtube])
    machine.run_cycle()
    assert tiktok.uploads == ["a1"] and youtube.uploads == []
    assert machine.db.get_clip("a1")["status"] == "done"        # YouTube hält nichts auf

    youtube.problem = lambda: None                              # später verbunden → wird mit bespielt
    machine.run_cycle()
    assert youtube.uploads == ["a2"] and tiktok.uploads == ["a1", "a2"]


@needs_ffmpeg
def test_recommendation_check_and_protection_pause(app_config, clock, downloads, caplog):  # noqa: F811
    p = app_config.posting
    p.min_minutes_between_posts, p.queue_size, p.max_posts_per_day = 0, 1, 20
    tiktok = FakeUploader("tiktok")
    tiktok.supports_review = True
    verdicts, seen = {}, []

    def review(probes):
        seen.append(dict(probes))
        return {cid: verdicts.get(cid, ReviewResult("ok")) for cid in probes}

    tiktok.review_posts = review
    machine = make_machine(app_config, [tiktok])
    verdicts["a1"] = ReviewResult("restricted", "Nicht für den Für-dich-Feed: nicht originaler Inhalt")
    verdicts["a2"] = ReviewResult("restricted", "Not eligible for the For You feed")

    machine.run_cycle()
    machine.run_cycle()
    assert tiktok.uploads == ["a1", "a2"] and seen == []          # Check erst nach 20 Minuten

    clock.advance(minutes=21)
    with caplog.at_level("WARNING", logger="clipmaschine"):
        machine.run_cycle()
    assert set(seen[0]) == {"a1", "a2"} and seen[0]["a1"].startswith("Unmöglicher Clutch")
    statuses = machine.db.upload_statuses("a1")["tiktok"]
    assert statuses["review_status"] == "restricted" and "originaler" in statuses["review_reason"]
    assert "NICHT weiterempfohlen" in caplog.text and "nicht-originalen Inhalt" in caplog.text
    assert "Schutzpause" in caplog.text

    count = len(tiktok.uploads)
    machine._next_review.clear()
    clock.advance(hours=2)
    machine.run_cycle()
    assert len(tiktok.uploads) == count                           # Schutzpause: nichts Neues, auch nach Neustart
    restarted = make_machine(app_config, [tiktok])
    restarted.run_cycle()
    assert len(tiktok.uploads) == count

    clock.advance(hours=23)
    restarted.run_cycle()
    assert len(tiktok.uploads) == count + 1                       # nach 24 h geht es automatisch weiter


def test_mac_installer_with_preset(tmp_path):
    if not all(shutil.which(tool) for tool in ("bash", "curl", "unzip")):
        pytest.skip("bash/curl/unzip fehlen")
    archive = tmp_path / "branch.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in PACKAGE_DIR.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, Path("Claude-branch/clipmaschine") / path.relative_to(PACKAGE_DIR))
    home = tmp_path / "home"
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(home), "CLIPMASCHINE_TEST": "1",
           "CLIPMASCHINE_HOME": str(home), "CLIPMASCHINE_NO_START": "1", "CLIPMASCHINE_PRESET": "gta6",
           "CLIPMASCHINE_ZIP_URL": archive.as_uri()}
    script = PACKAGE_DIR / "deploy" / "install_mac.sh"
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    target = home / "Desktop" / "Clipmaschine-GTA6"
    assert (target / "preset.txt").read_text().strip() == "gta6"
    assert (target / "clipmaschine" / "presets" / "gta6.yaml").is_file()
    starter = home / "Desktop" / "GTA6-Clipmaschine starten.command"
    assert "Clipmaschine-GTA6/clipmaschine/deploy/start_mac.command" in starter.read_text()
    assert 'CLIPMASCHINE_PRESET="gta6"' in (home / "Desktop" / "GTA6-Clipmaschine aktualisieren.command").read_text()
    assert not (home / "Desktop" / "Clipmaschine").exists()       # die erste Maschine bleibt unberührt

    env["CLIPMASCHINE_PRESET"] = "gibtsnicht"
    bad = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=120)
    assert bad.returncode != 0 and "gibt es nicht" in bad.stdout
