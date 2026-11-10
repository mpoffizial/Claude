"""Einrichtungs-Assistent:  python -m clipmaschine setup

Fragt das Nötigste ab, schreibt config.yaml und .env (Kommentare der Vorlage bleiben erhalten),
verbindet TikTok/Instagram mit deinen Accounts und prüft am Ende die Einrichtung.
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
PLATFORM_TEMPLATE = "{name}:\n  enabled: false\n"


def parse_logins(text: str) -> list[str]:
    """'twitch.tv/Name, anderer' → ['name', 'anderer'] (ungültige Einträge werden verworfen)."""
    logins = []
    for part in re.split(r"[,\s;]+", text.strip()):
        part = re.sub(r"^(https?://)?(www\.|m\.)?twitch\.tv/", "", part.strip(), flags=re.IGNORECASE).strip("/@")
        part = part.split("/")[0].lower()
        if LOGIN_RE.fullmatch(part) and part not in logins:
            logins.append(part)
    return logins


def build_config_text(template: str, logins: list[str], tiktok: bool, instagram: bool = False) -> str:
    streamers = "".join(f"  - login: {login}\n" for login in logins)
    platforms = {"tiktok": tiktok, "instagram": instagram}
    if STREAMER_TEMPLATE in template and all(PLATFORM_TEMPLATE.format(name=n) in template for n in platforms):
        text = template.replace(STREAMER_TEMPLATE, streamers, 1)
        for name, enabled in platforms.items():
            if enabled:
                text = text.replace(PLATFORM_TEMPLATE.format(name=name), f"{name}:\n  enabled: true\n", 1)
        return text
    # Vorlage wurde verändert → sicherer Weg über YAML (ohne Kommentare)
    data = yaml.safe_load(template) or {}
    data["streamers"] = [{"login": login} for login in logins]
    for name, enabled in platforms.items():
        data.setdefault(name, {})["enabled"] = enabled
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


def preset_path(name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9_-]+", name or ""):
        raise ValueError(f"Ungültiger Preset-Name: {name!r}")
    path = PACKAGE_DIR / "presets" / f"{name}.yaml"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in (PACKAGE_DIR / "presets").glob("*.yaml")))
        raise ValueError(f"Preset '{name}' gibt es nicht (vorhanden: {available})")
    return path


def run_setup(target_dir: Path, ask: Callable[[str], str] | None = None,
              ask_secret: Callable[[str], str] | None = None, connect: bool | None = None,
              preset: str | None = None) -> Path:
    """Führt durch die Einrichtung und gibt den Pfad der config.yaml zurück."""
    ask = ask or input
    ask_secret = ask_secret or getpass.getpass
    target_dir = Path(target_dir)
    config_path = target_dir / "config.yaml"
    env_path = target_dir / ".env"
    print("\n🎬 Clipmaschine – Einrichtung\n")
    if not shutil.which("ffmpeg"):
        print("⚠ ffmpeg fehlt noch. Mac: brew install ffmpeg | Windows: winget install Gyan.FFmpeg | "
              "Linux: sudo apt install ffmpeg – danach das Terminal neu öffnen.\n")

    write_config = True
    if config_path.exists():
        write_config = not _yes(ask(f"{config_path.name} gibt es schon. Behalten? [J/n] "))

    if write_config and preset:
        template = preset_path(preset).read_text(encoding="utf-8")
        config_path.write_text(template, encoding="utf-8")
        games = (yaml.safe_load(template).get("discovery") or {}).get("games") or []
        print(f"1/5 + 2/5 Voreinstellung „{preset}“ übernommen: Clips aller Streamer von {', '.join(games)},")
        print("    gepostet auf TikTok, Instagram und YouTube Shorts.")
        print(f"    ✔ {config_path.name} geschrieben\n")
    elif write_config:
        print("1/5 Welche Twitch-Kanäle sollen geclippt werden?")
        print("    Nur Kanäle, die dir das erlaubt haben (oder dein eigener). Mehrere mit Komma trennen.")
        logins: list[str] = []
        while not logins:
            logins = parse_logins(ask("    Kanäle: "))
            if not logins:
                print("    Bitte mindestens einen gültigen Twitch-Namen eingeben (z. B. meinkanal).")
        print("\n2/5 Gepostet wird auf TikTok.")
        instagram = _yes(ask("    Zusätzlich auf Instagram (Reels) posten? [J/n] "))
        template = (PACKAGE_DIR / "config.example.yaml").read_text(encoding="utf-8")
        config_path.write_text(build_config_text(template, logins, tiktok=True, instagram=instagram),
                               encoding="utf-8")
        print(f"    ✔ {config_path.name} geschrieben ({', '.join(logins)}; TikTok"
              f"{' + Instagram' if instagram else ''})\n")

    needs_twitch = bool(((yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}).get("discovery") or {})
                        .get("games"))
    if needs_twitch:
        print("3/5 Twitch-Zugang (nötig, um die Clips eines ganzen Spiels zu finden – kostenlos, 2 Minuten)")
        print("    1. https://dev.twitch.tv/console/apps öffnen → „Register Your Application“")
        print("    2. Name beliebig, OAuth-Redirect-URL: http://localhost:17563/twitch/callback,")
        print("       Kategorie beliebig, Client-Typ: Confidential → „Create“")
        print("    3. „Manage“ → Client-ID kopieren, „New Secret“ → Secret kopieren")
        client_id = ask("    Client-ID: ").strip()
        client_secret = ask_secret("    Client-Secret (unsichtbar): ").strip() if client_id else ""
        if not (client_id and client_secret) and "TWITCH_CLIENT_ID=\n" in (
                env_path.read_text(encoding="utf-8") if env_path.exists() else "TWITCH_CLIENT_ID=\n"):
            print("    ⚠ Ohne Twitch-Zugang findet die Maschine keine Clips. Später in die .env eintragen:")
            print("      TWITCH_CLIENT_ID=…  und  TWITCH_CLIENT_SECRET=…")
    else:
        print("3/5 Twitch-Zugang (optional – einfach Enter drücken)")
        print("    Ohne Zugang findet die Maschine die Top-Clips über die öffentliche Twitch-Clip-Seite.")
        print("    Nur für den Live-Modus nötig: App unter https://dev.twitch.tv/console/apps anlegen,")
        print("    OAuth-Redirect-URL http://localhost:17563/twitch/callback, Client-Typ: Confidential")
        client_id = ask("    Client-ID (Enter = ohne): ").strip()
        client_secret = ask_secret("    Client-Secret (unsichtbar): ").strip() if client_id else ""

    print("\n4/5 KI-Titel mit Claude (optional): Key von https://console.anthropic.com")
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
    from .uploaders import make_uploader
    platforms = [name for name in ("tiktok", "instagram") if getattr(cfg, name).enabled]
    labels = " und ".join({"tiktok": "TikTok", "instagram": "Instagram"}[p] for p in platforms)
    print(f"5/5 {labels} mit deinem Account verbinden")
    if connect is None:
        connect = _yes(ask("    Jetzt verbinden? Es öffnen sich Browserfenster zum Anmelden. [J/n] "))
    for platform in platforms:
        if not connect:
            print(f"    {platform}: später verbinden mit: python -m clipmaschine auth {platform}")
            continue
        try:
            make_uploader(cfg, platform).authorize()
        except NotImplementedError:
            print(f"    {platform}: läuft über die API – die Zugangsdaten stehen in der .env")
        except Exception as exc:  # Einrichtung trotzdem abschließen
            print(f"    ✘ {platform}-Verbindung fehlgeschlagen: {exc}\n"
                  f"      Später erneut: python -m clipmaschine auth {platform}")
    if cfg.youtube.enabled and not (cfg.tokens_dir / "youtube.json").is_file():
        print("    YouTube Shorts: braucht einmalig eine Google-Freigabe (README, Abschnitt „YouTube Shorts“),")
        print("    danach: python -m clipmaschine auth youtube. Bis dahin wird YouTube einfach übersprungen.")
    if preset and (PACKAGE_DIR / "presets" / f"{preset}_profil.md").is_file():
        print(f"    Tipp: Namen, Bio und Einstellungen für die Kanäle stehen in "
              f"clipmaschine/presets/{preset}_profil.md")
    print("\n✔ Einrichtung abgeschlossen.\n")
    return config_path
