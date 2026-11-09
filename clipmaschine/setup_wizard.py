"""Einrichtungs-Assistent:  python -m clipmaschine setup

Fragt das Nötigste ab, schreibt config.yaml und .env (Kommentare der Vorlage bleiben erhalten),
verbindet TikTok mit deinem Account und prüft am Ende die Einrichtung.
"""

from __future__ import annotations

import getpass
import re
import shutil
from pathlib import Path
from typing import Callable

import yaml

from .config import PACKAGE_DIR, load_config

LOGIN_RE = re.compile(r"^[A-Za-z0-9_]{3,25}$")
STREAMER_TEMPLATE = "  - login: dein_streamer\n    hashtags: [deinstreamer]          # immer mitposten\n"
TIKTOK_TEMPLATE = "tiktok:\n  enabled: false\n"


def parse_logins(text: str) -> list[str]:
    """'twitch.tv/Name, anderer' → ['name', 'anderer'] (ungültige Einträge werden verworfen)."""
    logins = []
    for part in re.split(r"[,\s;]+", text.strip()):
        part = re.sub(r"^(https?://)?(www\.|m\.)?twitch\.tv/", "", part.strip(), flags=re.IGNORECASE).strip("/@")
        part = part.split("/")[0].lower()
        if LOGIN_RE.fullmatch(part) and part not in logins:
            logins.append(part)
    return logins


def build_config_text(template: str, logins: list[str], tiktok: bool) -> str:
    streamers = "".join(f"  - login: {login}\n" for login in logins)
    if STREAMER_TEMPLATE in template and TIKTOK_TEMPLATE in template:
        text = template.replace(STREAMER_TEMPLATE, streamers, 1)
        if tiktok:
            text = text.replace(TIKTOK_TEMPLATE, "tiktok:\n  enabled: true\n", 1)
        return text
    # Vorlage wurde verändert → sicherer Weg über YAML (ohne Kommentare)
    data = yaml.safe_load(template) or {}
    data["streamers"] = [{"login": login} for login in logins]
    data.setdefault("tiktok", {})["enabled"] = tiktok
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def update_env_text(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        if not value:
            continue
        line = f"{key}={value}"
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(lambda _: line, text, count=1)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
    return text


def _yes(answer: str, default: bool = True) -> bool:
    answer = answer.strip().lower()
    if not answer:
        return default
    return answer[0] in ("j", "y")


def run_setup(target_dir: Path, ask: Callable[[str], str] | None = None,
              ask_secret: Callable[[str], str] | None = None, connect_tiktok: bool | None = None) -> Path:
    """Führt durch die Einrichtung und gibt den Pfad der config.yaml zurück."""
    ask = ask or input
    ask_secret = ask_secret or getpass.getpass
    target_dir = Path(target_dir)
    config_path = target_dir / "config.yaml"
    env_path = target_dir / ".env"
    print("\n🎬 Clipmaschine – Einrichtung\n")
    if not shutil.which("ffmpeg"):
        print("⚠ ffmpeg fehlt noch. Windows: winget install Gyan.FFmpeg | macOS: brew install ffmpeg | "
              "Linux: sudo apt install ffmpeg – danach das Terminal neu öffnen.\n")

    write_config = True
    if config_path.exists():
        write_config = not _yes(ask(f"{config_path.name} gibt es schon. Behalten? [J/n] "))

    if write_config:
        print("1/4 Welche Twitch-Kanäle sollen geclippt werden?")
        print("    Nur Kanäle, die dir das erlaubt haben (oder dein eigener). Mehrere mit Komma trennen.")
        logins: list[str] = []
        while not logins:
            logins = parse_logins(ask("    Kanäle: "))
            if not logins:
                print("    Bitte mindestens einen gültigen Twitch-Namen eingeben (z. B. meinkanal).")
        template = (PACKAGE_DIR / "config.example.yaml").read_text(encoding="utf-8")
        config_path.write_text(build_config_text(template, logins, tiktok=True), encoding="utf-8")
        print(f"    ✔ {config_path.name} geschrieben ({', '.join(logins)})\n")

    print("2/4 Twitch-Zugang für die Clip-Suche")
    print("    App anlegen: https://dev.twitch.tv/console/apps → OAuth-Redirect-URL:")
    print("    http://localhost:17563/twitch/callback  (Kategorie egal, Client-Typ: Confidential)")
    client_id = ask("    Client-ID (Enter = später): ").strip()
    client_secret = ask_secret("    Client-Secret (unsichtbar, Enter = später): ").strip() if client_id else ""

    print("\n3/4 KI-Titel mit Claude (optional): Key von https://console.anthropic.com")
    anthropic_key = ask_secret("    ANTHROPIC_API_KEY (unsichtbar, Enter = Vorlagen nutzen): ").strip()

    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else \
        (PACKAGE_DIR / ".env.example").read_text(encoding="utf-8")
    env_path.write_text(update_env_text(env_text, {
        "TWITCH_CLIENT_ID": client_id,
        "TWITCH_CLIENT_SECRET": client_secret,
        "ANTHROPIC_API_KEY": anthropic_key,
    }), encoding="utf-8")
    print(f"    ✔ Zugangsdaten in {env_path.name} gespeichert\n")

    cfg = load_config(config_path)
    print("4/4 TikTok mit deinem Account verbinden")
    if connect_tiktok is None:
        connect_tiktok = _yes(ask("    Jetzt verbinden? Es öffnet sich ein Browserfenster. [J/n] "))
    if connect_tiktok and cfg.tiktok.enabled:
        from .uploaders import make_uploader
        try:
            make_uploader(cfg, "tiktok").authorize()
        except Exception as exc:  # Einrichtung trotzdem abschließen
            print(f"    ✘ TikTok-Verbindung fehlgeschlagen: {exc}\n"
                  "      Später erneut: python -m clipmaschine auth tiktok")
    else:
        print("    Später verbinden mit: python -m clipmaschine auth tiktok")
    print("\n✔ Einrichtung abgeschlossen.\n")
    return config_path
