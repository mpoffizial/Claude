"""Einrichtungs-Assistent und Verhalten, wenn Twitch nicht erreichbar ist."""

import requests
import yaml

from clipmaschine.__main__ import main
from clipmaschine.config import PACKAGE_DIR, load_config
from clipmaschine.setup_wizard import build_config_text, parse_logins, run_setup, update_env_text
from clipmaschine.twitch_api import TwitchAPI, TwitchAPIError


def scripted(*answers):
    queue = list(answers)
    return lambda prompt="": queue.pop(0)


def test_parse_logins():
    assert parse_logins("https://www.twitch.tv/Papaplatte, @Trymacs  twitch.tv/x/  meinkanal") == \
        ["papaplatte", "trymacs", "meinkanal"]
    assert parse_logins("a, !!!, ") == []                              # zu kurz bzw. ungültig


def test_config_text_keeps_comments_and_is_valid():
    template = (PACKAGE_DIR / "config.example.yaml").read_text(encoding="utf-8")
    text = build_config_text(template, ["kanal_eins", "kanal2"], tiktok=True)
    data = yaml.safe_load(text)
    assert data["streamers"] == [{"login": "kanal_eins"}, {"login": "kanal2"}]
    assert data["tiktok"]["enabled"] is True and data["tiktok"]["method"] == "browser"
    assert "# Nur Streamer eintragen" in text                         # Kommentare bleiben erhalten


def test_env_update_replaces_and_appends():
    text = "TWITCH_CLIENT_ID=\nTWITCH_CLIENT_SECRET=\n# Kommentar\n"
    out = update_env_text(text, {"TWITCH_CLIENT_ID": "abc", "ANTHROPIC_API_KEY": "sk-x", "LEER": ""})
    assert "TWITCH_CLIENT_ID=abc" in out and "ANTHROPIC_API_KEY=sk-x" in out
    assert "TWITCH_CLIENT_SECRET=\n" in out and "LEER" not in out and "# Kommentar" in out


def test_run_setup_writes_working_config(tmp_path, monkeypatch, capsys):
    for name in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "ANTHROPIC_API_KEY", "CLIPMASCHINE_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    config = run_setup(tmp_path, ask=scripted("ungültig!", "twitch.tv/MeinKanal", "client123"),
                       ask_secret=scripted("geheim", ""), connect_tiktok=False)
    cfg = load_config(config)
    assert [s.login for s in cfg.streamers] == ["meinkanal"]
    assert cfg.tiktok.enabled and cfg.tiktok_method == "browser"
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "TWITCH_CLIENT_ID=client123" in env and "TWITCH_CLIENT_SECRET=geheim" in env
    assert "ANTHROPIC_API_KEY=\n" in env                                # leer gelassen → Vorlagen
    assert "mindestens einen gültigen" in capsys.readouterr().out

    # zweiter Lauf: vorhandene Konfiguration behalten
    run_setup(tmp_path, ask=scripted("J", ""), ask_secret=scripted(""), connect_tiktok=False)
    assert [s.login for s in load_config(config).streamers] == ["meinkanal"]


def test_cli_setup_without_start(tmp_path, monkeypatch, capsys):
    for name in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET", "ANTHROPIC_API_KEY", "CLIPMASCHINE_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", scripted("kanal_eins", "", "n"))
    monkeypatch.setattr("getpass.getpass", scripted(""))
    assert main(["setup", "--no-start"]) == 0
    out = capsys.readouterr().out
    assert "Einrichtung abgeschlossen" in out and "öffentlichen Clip-Seite" in out
    assert (tmp_path / "config.yaml").is_file()


class OfflineSession(requests.Session):
    def request(self, *args, **kwargs):
        raise requests.ConnectionError("kein Netz")


def test_twitch_offline_raises_clean_error():
    api = TwitchAPI("id", "secret", session=OfflineSession())
    try:
        api.get_users(logins=["kanal"])
    except TwitchAPIError as exc:
        assert "nicht erreichbar" in str(exc)
    else:
        raise AssertionError("TwitchAPIError erwartet")
