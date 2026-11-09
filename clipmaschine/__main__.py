"""Kommandozeile:  python -m clipmaschine <befehl>"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import signal
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .config import AppConfig, ConfigError, load_config

log = logging.getLogger("clipmaschine")


def _setup_logging(cfg: AppConfig | None, verbose: bool) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")   # Emojis in Logs auf alten Windows-Konsolen
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if cfg is not None:
        log_dir = cfg.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_dir / "clipmaschine.log", maxBytes=5_000_000, backupCount=3,
                                            encoding="utf-8"))
    logging.basicConfig(level=level, handlers=handlers, force=True,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("urllib3", "googleapiclient", "google_auth_oauthlib", "httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _machine(cfg: AppConfig, **kwargs):
    from .pipeline import ClipMachine
    return ClipMachine(cfg, **kwargs)


# ---------------------------------------------------------------- Befehle
def cmd_run(cfg: AppConfig, args) -> int:
    from .lock import AlreadyRunning, SingleInstanceLock

    lock = SingleInstanceLock(cfg.data_dir / "clipmaschine.lock")
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        log.error("%s", exc)
        return 3
    try:
        machine = _machine(cfg, dry_run=args.dry_run)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: machine.stop())
        log.info("Clipmaschine %s startet – %d Streamer: %s", __version__, len(cfg.streamers),
                 ", ".join(s.login for s in cfg.streamers))
        try:
            machine.run_forever(once=args.once)
        except KeyboardInterrupt:
            machine.stop()
        log.info("Beendet.")
        return 0
    finally:
        lock.release()


def cmd_discover(cfg: AppConfig, args) -> int:
    machine = _machine(cfg)
    machine.discovery.discover()
    rows = machine.db.clips_with_status("discovered")[: args.limit]
    if not rows:
        print("Keine passenden Clips gefunden (min_views / lookback_hours in der config.yaml prüfen).")
        return 0
    now = datetime.now(timezone.utc)
    print(f"\n{'Score':>8} {'Views':>7} {'Alter':>6} {'Dauer':>5}  {'Streamer':<16} Titel")
    for row in rows:
        age = (now - datetime.fromisoformat(row["created_at"])).total_seconds() / 3600
        print(f"{row['score']:8.1f} {row['view_count']:7d} {age:5.1f}h {row['duration']:4.0f}s  "
              f"{row['broadcaster_login']:<16} {row['title'][:60]}  {row['url']}")
    print(f"\n{len(rows)} Clips vorgemerkt – 'python -m clipmaschine run' verarbeitet sie.")
    return 0


def cmd_process(cfg: AppConfig, args) -> int:
    if not args.clip and not args.file:
        print("Gib eine Clip-URL/-ID oder --file <video.mp4> an.")
        return 2
    machine = _machine(cfg)
    out = machine.process_manual(ref=args.clip, file=args.file, streamer=args.streamer, title=args.title,
                                 upload=args.upload)
    return 0 if out else 1


def cmd_frame(cfg: AppConfig, args) -> int:
    from .downloader import clip_id_from_ref, download_clip
    from .editor import extract_frame

    source = Path(args.clip)
    target_dir = cfg.data_dir / "frames"
    target_dir.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        clip_id = clip_id_from_ref(args.clip)
        url = args.clip if args.clip.startswith("http") else f"https://clips.twitch.tv/{clip_id}"
        source = download_clip(url, cfg.data_dir / "downloads", clip_id)
    out = extract_frame(source, target_dir / f"{source.stem}_raster.png", args.at)
    print(f"Standbild gespeichert: {out}\n"
          "Jedes rote Kästchen = 10 % von Breite/Höhe. Beispiel: Facecam oben links über 3×3 Kästchen →\n"
          "  facecam: {x: 0.0, y: 0.0, w: 0.3, h: 0.3}")
    return 0


def cmd_auth(cfg: AppConfig, args) -> int:
    if args.platform == "twitch":
        from .oauth import TokenStore
        from .twitch_api import TwitchAPI
        api = TwitchAPI(cfg.twitch.client_id, cfg.twitch.client_secret,
                        TokenStore(cfg.tokens_dir / "twitch.json"), cfg.twitch.redirect_uri)
        login = api.authorize_user()
        print(f"Twitch verbunden als {login} – der Live-Modus kann jetzt Clips erstellen.")
        return 0
    from .uploaders import make_uploader
    make_uploader(cfg, args.platform).authorize()
    return 0


def _read_heartbeat(cfg: AppConfig) -> tuple[dict | None, float, bool]:
    """(Heartbeat-Daten, Alter in Sekunden, läuft?)"""
    import json
    try:
        beat = json.loads((cfg.data_dir / "heartbeat.json").read_text(encoding="utf-8"))
        last = datetime.fromisoformat(beat["last_cycle"])
    except (OSError, ValueError, KeyError):
        return None, float("inf"), False
    age = (datetime.now(timezone.utc) - last).total_seconds()
    # ein Durchlauf kann mit Rendern/Uploads einige Minuten dauern
    return beat, age, age < 3 * float(beat.get("loop_interval_seconds", 60)) + 1800


def cmd_health(cfg: AppConfig, args) -> int:
    """Für Docker-HEALTHCHECK/Monitoring: Exit 0, wenn die Maschine regelmäßig Durchläufe macht."""
    _, age, running = _read_heartbeat(cfg)
    print("ok" if running else f"kein Durchlauf seit {age:.0f}s")
    return 0 if running else 1


def _heartbeat_line(cfg: AppConfig) -> str:
    beat, age, running = _read_heartbeat(cfg)
    if beat is None:
        return "Maschine: noch nie gelaufen"
    state = "läuft ✔" if running else "gestoppt ✘"
    extra = ""
    if beat.get("paused_until"):
        extra = " | pausiert: " + ", ".join(f"{p} bis {t[11:16]} UTC" for p, t in beat["paused_until"].items())
    return (f"Maschine: {state} (letzter Durchlauf vor {int(age // 60)} min, PID {beat.get('pid')}, "
            f"Warteschlange {beat.get('queue', 0)}){extra}")


def cmd_status(cfg: AppConfig, args) -> int:
    from .db import Database
    db = Database(cfg.data_dir / "clipmaschine.db")
    stats = db.stats()
    print("\n" + _heartbeat_line(cfg))
    print("Clips:", ", ".join(f"{k}={v}" for k, v in sorted(stats["clips"].items())) or "noch keine")
    for platform, counts in sorted(stats["uploads"].items()):
        print(f"{platform:>10}:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    queue = db.clips_with_status("rendered")
    if queue:
        print("\nFertig zum Posten:")
        for row in queue[:10]:
            print(f"  {row['score']:8.1f}  {row['broadcaster_login']:<16} {row['title'][:60]}")
    uploads = db.recent_uploads(10)
    if uploads:
        print("\nLetzte Uploads:")
        for row in uploads:
            detail = row["url"] or row["remote_id"] or (row["error"] or "")[:80]
            print(f"  {row['updated_at'][:16]}  {row['platform']:<9} {row['status']:<9} "
                  f"{row['broadcaster_login']:<14} {detail}")
    print()
    return 0


def cmd_check(cfg: AppConfig, args) -> int:
    ok = True

    def line(good: bool | None, label: str, detail: str = "") -> None:
        nonlocal ok
        mark = {True: "✔", False: "✘", None: "•"}[good]
        if good is False:
            ok = False
        print(f" {mark} {label}" + (f": {detail}" if detail else ""))

    print(f"\nClipmaschine {__version__} – Konfiguration: {cfg.config_path}\n")
    for binary in ("ffmpeg", "ffprobe"):
        path = shutil.which(binary)
        line(bool(path), binary, path or "nicht gefunden – bitte installieren")
    try:
        import yt_dlp
        line(True, "yt-dlp", yt_dlp.version.__version__)
    except ImportError:
        line(False, "yt-dlp", "pip install yt-dlp")

    line(bool(cfg.streamers), "Streamer", ", ".join(s.login for s in cfg.streamers) or "keine eingetragen")
    from .twitch_api import TwitchAPIError
    try:
        machine = _machine(cfg)
        users = machine.twitch.get_users(logins=[s.login for s in cfg.streamers])
        missing = [s.login for s in cfg.streamers if s.login not in users]
        line(not missing, "Twitch-API", "alle Kanäle gefunden" if not missing else f"nicht gefunden: {missing}")
        if cfg.live.enabled:
            try:
                login = machine.twitch.validate_user_token().get("login")
                line(True, "Live-Modus", f"Clips werden als {login} erstellt")
            except TwitchAPIError as exc:
                line(False, "Live-Modus", str(exc))
        else:
            line(None, "Live-Modus", "aus")
    except TwitchAPIError as exc:
        line(False, "Twitch-API", str(exc))
        machine = None

    if cfg.captions.enabled:
        try:
            import faster_whisper  # noqa: F401
            line(True, "Untertitel", f"faster-whisper, Modell {cfg.captions.model}")
        except ImportError:
            line(False, "Untertitel", "pip install faster-whisper (oder captions.enabled: false)")
    else:
        line(None, "Untertitel", "aus")

    from .metadata import MetadataGenerator
    generator = machine.metadata if machine else MetadataGenerator(cfg.metadata)
    if generator.uses_claude:
        has_key = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
        line(None, "KI-Titel", f"Claude ({cfg.metadata.model})" + ("" if has_key else
             " – kein ANTHROPIC_API_KEY gesetzt, ohne Zugangsdaten werden Vorlagen genutzt"))
    else:
        line(None, "KI-Titel", "Vorlagen")

    from .uploaders import PLATFORMS, make_uploader
    for platform in PLATFORMS:
        if not getattr(cfg, platform).enabled:
            line(None, platform, "deaktiviert")
            continue
        uploader = make_uploader(cfg, platform)
        problem = uploader.problem()
        detail = f"{uploader.description}: {problem}" if problem else f"bereit ({uploader.description})"
        line(problem is None, platform, detail)
    print()
    return 0 if ok else 1


def cmd_setup(args) -> int:
    from .setup_wizard import run_setup

    config_path = run_setup(Path.cwd())
    cfg = load_config(config_path)
    _setup_logging(cfg, args.verbose)
    cmd_check(cfg, args)
    if args.no_start:
        return 0
    answer = input("Clipmaschine jetzt starten? [J/n] ").strip().lower()
    if answer and answer[0] not in ("j", "y"):
        print("Starten später mit: python -m clipmaschine run")
        return 0
    return cmd_run(cfg, argparse.Namespace(dry_run=False, once=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m clipmaschine",
        description="Clipmaschine – Twitch-Highlights automatisch als TikToks, YouTube Shorts und Reels.",
    )
    parser.add_argument("-c", "--config", help="Pfad zur config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="ausführliche Logs")
    parser.add_argument("--version", action="version", version=f"clipmaschine {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup", help="Einrichtungs-Assistent (erster Start)")
    p.add_argument("--no-start", action="store_true", help="nach der Einrichtung nicht direkt starten")

    p = sub.add_parser("run", help="Maschine im Dauerbetrieb starten")
    p.add_argument("--dry-run", action="store_true", help="alles machen außer hochladen")
    p.add_argument("--once", action="store_true", help="nur einen Durchlauf (ohne Live-Modus)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("discover", help="aktuelle Top-Clips suchen und anzeigen")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("process", help="einen bestimmten Clip schneiden (optional sofort posten)")
    p.add_argument("clip", nargs="?", help="Clip-URL oder Clip-ID")
    p.add_argument("--file", type=Path, help="lokale Videodatei statt Twitch-Clip")
    p.add_argument("--streamer", help="Twitch-Login für Credit und Hashtags (bei --file)")
    p.add_argument("--title", help="Clip-Titel überschreiben")
    p.add_argument("--upload", action="store_true", help="sofort auf allen aktivierten Plattformen posten")
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("frame", help="Standbild mit 10-%%-Raster zum Einstellen der Facecam")
    p.add_argument("clip", help="Clip-URL/-ID oder lokale Videodatei")
    p.add_argument("--at", type=float, default=3.0, help="Sekunde im Clip")
    p.set_defaults(func=cmd_frame)

    p = sub.add_parser("auth", help="bei Twitch/TikTok/YouTube anmelden")
    p.add_argument("platform", choices=["twitch", "tiktok", "youtube"])
    p.set_defaults(func=cmd_auth)

    sub.add_parser("status", help="Warteschlange und letzte Uploads").set_defaults(func=cmd_status)
    sub.add_parser("check", help="Einrichtung prüfen").set_defaults(func=cmd_check)
    sub.add_parser("health", help="Exit 0, wenn die Maschine läuft (Monitoring)").set_defaults(func=cmd_health)

    args = parser.parse_args(argv)
    if args.command == "setup":
        _setup_logging(None, args.verbose)
        try:
            return cmd_setup(args)
        except (KeyboardInterrupt, EOFError):
            print("\nEinrichtung abgebrochen.")
            return 130
        except ConfigError as exc:
            print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
            return 2
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        _setup_logging(None, args.verbose)
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2
    _setup_logging(cfg, args.verbose)
    try:
        return args.func(cfg, args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # verständliche Meldung statt Traceback für typische Einrichtungsfehler
        if args.verbose:
            raise
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
