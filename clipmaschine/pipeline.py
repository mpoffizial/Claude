"""Die eigentliche Maschine: finden → herunterladen → schneiden → texten → posten.

Selbstheilung:
  • Fehler beim Vorbereiten → später automatisch erneut (wachsende Pause), erst dann aufgeben
  • Upload-Fehler → erneuter Versuch im nächsten Slot; Rate-Limits/Kontingente → Plattform pausiert
    bis zum Reset und macht dann von selbst weiter
  • Tageslimit/Mindestabstand erreicht → exakte Uhrzeit des nächsten Posts wird geloggt, dann geht's weiter
  • Fehlende Videodatei → wird neu gerendert
  • Live-Chat/Statusthread abgestürzt → wird automatisch neu gestartet
  • Wenig Speicherplatz → keine neuen Downloads, bis wieder Platz ist
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .captions import CaptionsUnavailable, chunk_words, transcribe
from .config import AppConfig
from .db import FINAL_STATUSES, Database
from .downloader import clip_id_from_ref, download_clip, fetch_clip_info
from .editor import probe, render_vertical
from .highlights import ClipDiscovery
from .live import LiveClipper
from .metadata import MetadataContext, MetadataGenerator
from .models import Clip, PostMetadata, iso, utcnow
from .oauth import TokenStore
from .scheduler import local_timezone, next_allowed_time
from .twitch_api import TwitchAPI, TwitchAPIError
from .uploaders import UploadError, Uploader, enabled_uploaders

log = logging.getLogger(__name__)

PREPARE_BACKOFF_MINUTES = 5
LIVE_RETRY_SECONDS = 600


class ClipMachine:
    def __init__(self, cfg: AppConfig, dry_run: bool = False, uploaders: list[Uploader] | None = None,
                 metadata: MetadataGenerator | None = None, twitch: TwitchAPI | None = None):
        self.cfg = cfg
        self.dry_run = dry_run or cfg.dry_run
        self.downloads_dir = cfg.data_dir / "downloads"
        self.rendered_dir = cfg.data_dir / "rendered"
        self.heartbeat_path = cfg.data_dir / "heartbeat.json"
        self.db = Database(cfg.data_dir / "clipmaschine.db")
        self.stop_event = threading.Event()
        self.metadata = metadata or MetadataGenerator(cfg.metadata)
        self.uploaders = enabled_uploaders(cfg) if uploaders is None else uploaders
        self._twitch = twitch
        self._discovery: ClipDiscovery | None = None
        self._live: LiveClipper | None = None
        self._live_wanted = False
        self._live_retry_at = 0.0
        self._last_discovery: float | None = None
        self._paused_until: dict[str, datetime] = {}
        self._paused_since: dict[str, float] = {}
        self._announced_wait: dict[str, str] = {}
        self._disk_warned = False
        self._captions_enabled = cfg.captions.enabled
        self._started_at = utcnow()

    # --------------------------------------------------------------- Zugriff
    @property
    def twitch(self) -> TwitchAPI:
        if self._twitch is None:
            self._twitch = TwitchAPI(self.cfg.twitch.client_id, self.cfg.twitch.client_secret,
                                     TokenStore(self.cfg.tokens_dir / "twitch.json"), self.cfg.twitch.redirect_uri)
        return self._twitch

    @property
    def discovery(self) -> ClipDiscovery:
        if self._discovery is None:
            self._discovery = ClipDiscovery(self.cfg, self.twitch, self.db)
        return self._discovery

    def _local(self, dt: datetime) -> str:
        return f"{dt.astimezone(local_timezone(self.cfg.posting.timezone)):%d.%m. %H:%M}"

    # ------------------------------------------------------------ Hauptloop
    def run_forever(self, once: bool = False) -> None:
        if not self.cfg.streamers:
            log.error("Keine Streamer in der config.yaml eingetragen")
            return
        for uploader in self.uploaders:
            problem = uploader.problem()
            if problem:
                log.warning("%s: %s", uploader.name, problem)
        if not self.uploaders:
            log.warning("Keine Plattform aktiviert – Videos werden nur gerendert (Ordner %s)", self.rendered_dir)
        if self.dry_run:
            log.info("DRY-RUN: Es wird nichts hochgeladen")
        log.info("KI-Titel: %s", "Claude" if self.metadata.uses_claude else "Vorlagen")
        self.cleanup_leftovers()

        self._live_wanted = self.cfg.live.enabled and not once
        try:
            while not self.stop_event.is_set():
                started = time.monotonic()
                try:
                    self.run_cycle()
                except Exception:
                    log.exception("Fehler im Durchlauf – die Maschine macht im nächsten Durchlauf weiter")
                if once:
                    break
                remaining = self.cfg.loop_interval_seconds - (time.monotonic() - started)
                self.stop_event.wait(max(remaining, 1))
        finally:
            self.stop_event.set()
            if self._live:
                self._live.stop()

    def stop(self) -> None:
        self.stop_event.set()

    def _start_live(self) -> None:
        try:
            live = LiveClipper(self.cfg, self.twitch, self.db, self.stop_event)
            if live.start():
                self._live = live
                return
        except TwitchAPIError as exc:
            log.error("Live-Modus nicht gestartet: %s", exc)
        self._live_retry_at = time.monotonic() + LIVE_RETRY_SECONDS
        log.info("Live-Modus: neuer Startversuch in %d min", LIVE_RETRY_SECONDS // 60)

    def run_cycle(self) -> None:
        if self._live_wanted and self._live is None and time.monotonic() >= self._live_retry_at:
            self._start_live()                       # auch nach Netzwerkfehlern beim Start
        elif self._live:
            self._live.ensure_running()              # abgestürzte Threads neu starten

        d = self.cfg.discovery
        due = self._last_discovery is None or time.monotonic() - self._last_discovery >= d.interval_minutes * 60
        if d.enabled and due:
            self._last_discovery = time.monotonic()
            try:
                new = self.discovery.discover(utcnow())
                if new:
                    log.info("%d neue Highlights in der Warteschlange", len(new))
            except TwitchAPIError as exc:
                log.error("Clip-Suche fehlgeschlagen: %s – neuer Versuch in %g min", exc, d.interval_minutes)
        self.expire_old()
        self.prepare_pending()
        self.post_ready()
        self.finalize()
        self.write_heartbeat()

    # ------------------------------------------------------------ Vorbereiten
    def expire_old(self) -> None:
        limit = utcnow() - timedelta(hours=self.cfg.posting.max_clip_age_hours)
        for row in self.db.expire_older_than(limit):
            log.info("Clip %s ist zu alt zum Posten – verworfen", row["id"])
            self._remove_rendered(row)

    def _enough_disk(self) -> bool:
        try:
            free = shutil.disk_usage(self.cfg.data_dir).free
        except OSError:
            return True
        if free < self.cfg.min_free_disk_gb * 1e9:
            if not self._disk_warned:
                log.warning("Nur noch %.1f GB frei – pausiere Downloads, bis wieder Platz ist", free / 1e9)
                self._disk_warned = True
            return False
        if self._disk_warned:
            log.info("Wieder genug Speicherplatz – Downloads laufen weiter")
        self._disk_warned = False
        return True

    def prepare_pending(self) -> None:
        if not self._enough_disk():
            return
        if self.uploaders:
            missing = self.cfg.posting.queue_size - self.db.count_with_status("rendered")
        else:
            missing = self.cfg.posting.queue_size
        min_created = utcnow() - timedelta(hours=self.cfg.posting.max_clip_age_hours)
        for row in self.db.clips_to_prepare(missing, min_created):
            if self.stop_event.is_set():
                return
            context = json.loads(row["context_json"] or "{}")
            self.prepare(Clip.from_row(row), row["source"] or "top_clips", context)

    def prepare(self, clip: Clip, source: str, context: dict | None = None,
                local_file: Path | None = None) -> tuple[Path, PostMetadata] | None:
        """Lädt, untertitelt, betextet und rendert einen Clip. Gibt (Video, Texte) zurück."""
        streamer = self.cfg.streamer(clip.broadcaster_login)
        downloaded: Path | None = None
        try:
            log.info("▶ %s: „%s“ (%d Views, %s)", clip.broadcaster_login, clip.title, clip.view_count, source)
            if local_file:
                src = local_file
            else:
                src = downloaded = download_clip(clip.url, self.downloads_dir, _safe_name(clip.id))

            chunks, transcript = [], ""
            if self._captions_enabled:
                try:
                    words = transcribe(src, self.cfg.captions)
                    transcript = " ".join(w.text for w in words)
                    c = self.cfg.captions
                    chunks = chunk_words(words, c.max_words, c.max_chunk_seconds, uppercase=c.uppercase)
                except CaptionsUnavailable as exc:
                    log.warning("Untertitel deaktiviert: %s", exc)
                    self._captions_enabled = False
                except Exception as exc:  # Untertitel sind optional – Video trotzdem fertigstellen
                    log.warning("Untertitel für %s fehlgeschlagen (%s) – Video ohne Untertitel", clip.id, exc)

            meta = self.metadata.generate(MetadataContext(
                clip=clip, source=source, transcript=transcript, context=context or {},
                extra_hashtags=streamer.hashtags if streamer else [],
            ))
            out = render_vertical(
                src, self.rendered_dir / f"{_safe_name(clip.id)}.mp4", self.cfg.editing,
                layout=(streamer.layout if streamer and streamer.layout else self.cfg.editing.layout),
                facecam=streamer.facecam if streamer else None,
                crop_x=streamer.crop_x if streamer else 0.5,
                hook=meta.hook if self.cfg.editing.hook_text else None,
                credit=f"twitch.tv/{clip.broadcaster_login}" if self.cfg.editing.credit_text else None,
                captions=chunks,
            )
            self.db.mark(clip.id, "rendered", rendered_path=str(out), error=None,
                         video_path=str(src) if (local_file or self.cfg.editing.keep_source) else None,
                         meta_json=json.dumps(meta.to_dict(), ensure_ascii=False))
            log.info("✔ Fertig: %s | Hook: „%s“ | Titel: %s (%s)", out.name, meta.hook, meta.title, meta.source)
            return out, meta
        except Exception as exc:
            gave_up = self.db.prepare_failed(clip.id, str(exc)[:1000], self.cfg.posting.max_attempts,
                                             PREPARE_BACKOFF_MINUTES)
            log.error("✘ Clip %s fehlgeschlagen: %s – %s", clip.id, exc,
                      "aufgegeben" if gave_up else "wird später automatisch erneut versucht")
            return None
        finally:
            if downloaded and not self.cfg.editing.keep_source:
                downloaded.unlink(missing_ok=True)

    # --------------------------------------------------------------- Posten
    def post_ready(self) -> None:
        p = self.cfg.posting
        min_created = utcnow() - timedelta(hours=p.max_clip_age_hours)
        for uploader in self.uploaders:
            if self.stop_event.is_set():
                return
            now = utcnow()
            paused = self._paused_until.get(uploader.name)
            if paused:
                # neu angemeldet (z. B. "auth tiktok") → Pause sofort beenden
                reconnected = (uploader.connected_at() or 0) > self._paused_since.get(uploader.name, float("inf"))
                if now < paused and not reconnected:
                    continue
                del self._paused_until[uploader.name]
                self._paused_since.pop(uploader.name, None)
                log.info("%s: %s – es geht automatisch weiter", uploader.name,
                         "neu verbunden" if reconnected else "Pause vorbei")
            when, reason = next_allowed_time(self.db, uploader.name, p, now)
            if when > now:
                self._announce_wait(uploader.name, when, reason)
                continue
            row = self.db.next_for_platform(uploader.name, p.max_attempts, min_created)
            if row is None:
                continue
            self._announced_wait.pop(uploader.name, None)
            self.post_one(uploader, row)

    def _announce_wait(self, platform: str, when: datetime, reason: str) -> None:
        """Wartezeit einmal verständlich loggen statt jede Minute."""
        key = f"{reason}|{when:%Y%m%d%H%M}"
        if self._announced_wait.get(platform) != key:
            self._announced_wait[platform] = key
            log.info("%s: %s – nächster Post automatisch ab %s", platform, reason, self._local(when))

    def post_one(self, uploader: Uploader, row) -> bool:
        clip = Clip.from_row(row)
        meta = PostMetadata.from_dict(json.loads(row["meta_json"] or "{}"))
        path = Path(row["rendered_path"] or "")
        if not path.is_file():
            log.warning("Video für %s fehlt – wird neu gerendert", clip.id)
            self.db.prepare_failed(clip.id, "gerendertes Video fehlte", self.cfg.posting.max_attempts, 0)
            return False
        if self.dry_run:
            log.info("[DRY-RUN] %s ← %s | %s %s", uploader.name, path.name, meta.title, meta.hashtag_text())
            self.db.record_upload(clip.id, uploader.name, "dry_run")
            return True
        log.info("⬆ %s: lade %s hoch …", uploader.name, path.name)
        try:
            result = uploader.upload(path, meta, clip)
        except UploadError as exc:
            if exc.pause_seconds:
                until = utcnow() + timedelta(seconds=exc.pause_seconds)
                self._paused_until[uploader.name] = until
                self._paused_since[uploader.name] = time.time()
                log.warning("%s: %s – pausiert bis %s, danach geht es automatisch weiter",
                            uploader.name, exc, self._local(until))
            else:
                log.error("%s: Upload fehlgeschlagen: %s%s", uploader.name, exc,
                          "" if exc.retryable else " (kein erneuter Versuch für diesen Clip)")
            self.db.record_upload(clip.id, uploader.name, "failed", error=str(exc)[:1000],
                                  count_attempt=not exc.pause_seconds)
            if not exc.retryable:
                self.db.give_up_upload(clip.id, uploader.name, self.cfg.posting.max_attempts)
            return False
        except Exception as exc:
            log.exception("%s: unerwarteter Fehler beim Upload – neuer Versuch im nächsten Slot", uploader.name)
            self.db.record_upload(clip.id, uploader.name, "failed", error=str(exc)[:1000])
            return False
        self.db.record_upload(clip.id, uploader.name, result.status, result.remote_id, result.url)
        log.info("✅ %s: %s %s", uploader.name, {"inbox": "als Entwurf in der App", "submitted": "eingereicht"}
                 .get(result.status, "gepostet"), result.url)
        return True

    def finalize(self) -> None:
        """Clips, die überall erledigt sind, abschließen und Dateien aufräumen."""
        platforms = [u.name for u in self.uploaders]
        for row in self.db.clips_with_status("rendered"):
            if platforms:
                statuses = self.db.upload_statuses(row["id"])
                finished = all(
                    p in statuses and (statuses[p]["status"] in FINAL_STATUSES
                                       or statuses[p]["attempts"] >= self.cfg.posting.max_attempts)
                    for p in platforms
                )
                if not finished:
                    continue
                self.db.mark(row["id"], "done")
                if self.cfg.posting.cleanup_after_post and not self.dry_run:
                    self._remove_rendered(row)
            else:
                self.db.mark(row["id"], "done")   # nur rendern: Videos bleiben liegen

    def _remove_rendered(self, row) -> None:
        value = row["rendered_path"]
        if value and Path(value).parent == self.rendered_dir:   # nur eigene Dateien löschen
            Path(value).unlink(missing_ok=True)

    # ----------------------------------------------------------- Wartung
    def cleanup_leftovers(self) -> None:
        """Reste eines abgebrochenen Laufs entfernen (halbe Downloads, Render-Arbeitsordner)."""
        for workdir in self.rendered_dir.glob(".work_*"):
            shutil.rmtree(workdir, ignore_errors=True)
        if self.downloads_dir.is_dir() and not self.cfg.editing.keep_source:
            for leftover in self.downloads_dir.iterdir():
                if leftover.is_file():
                    leftover.unlink(missing_ok=True)

    def write_heartbeat(self) -> None:
        data = {
            "pid": os.getpid(),
            "started_at": iso(self._started_at),
            "last_cycle": iso(utcnow()),
            "loop_interval_seconds": self.cfg.loop_interval_seconds,
            "queue": self.db.count_with_status("rendered"),
            "paused_until": {k: iso(v) for k, v in self._paused_until.items()},
            "live": bool(self._live),
        }
        try:
            tmp = self.heartbeat_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.heartbeat_path)
        except OSError as exc:
            log.debug("Heartbeat nicht schreibbar: %s", exc)

    # ------------------------------------------------------- Einzel-Befehle
    def clip_from_ref(self, ref: str) -> Clip:
        clip_id = clip_id_from_ref(ref)
        url = ref if ref.startswith("http") else f"https://clips.twitch.tv/{clip_id}"
        try:
            data = self.twitch.get_clip(clip_id)
            if data:
                users = self.twitch.get_users(ids=[data["broadcaster_id"]])
                login = next(iter(users), (data.get("broadcaster_name") or "").lower())
                game_id = str(data.get("game_id") or "")
                game = self.twitch.game_names([game_id]).get(game_id, "") if game_id else ""
                return Clip.from_helix(data, login, game)
        except TwitchAPIError as exc:
            log.info("Twitch-API nicht nutzbar (%s) – hole Infos per yt-dlp", exc)
        info = fetch_clip_info(url)
        match = re.search(r"twitch\.tv/([^/]+)/clip/", url)
        channel = info.get("channel") or info.get("uploader") or "unbekannt"
        login = match.group(1).lower() if match else re.sub(r"\W", "", str(channel)).lower()
        return Clip(id=clip_id, url=url, broadcaster_id="", broadcaster_login=login, broadcaster_name=str(channel),
                    title=info.get("title") or "", view_count=int(info.get("view_count") or 0),
                    created_at=utcnow(), duration=float(info.get("duration") or 0))

    def process_manual(self, ref: str | None = None, file: Path | None = None, streamer: str | None = None,
                       title: str | None = None, upload: bool = False) -> Path | None:
        if file:
            file = Path(file).resolve()
            login = (streamer or "unbekannt").lower()
            clip = Clip(id=_safe_name(file.stem), url="", broadcaster_id="", broadcaster_login=login,
                        broadcaster_name=streamer or login, title=title or file.stem, view_count=0,
                        created_at=utcnow(), duration=probe(file).duration)
        else:
            clip = self.clip_from_ref(ref or "")
            if title:
                clip.title = title
        self.db.upsert_clip(clip, "manual", score=1e9)
        result = self.prepare(clip, "manual", local_file=file)
        if not result:
            return None
        out, meta = result
        print(f"\nVideo:   {out}\nHook:    {meta.hook}\nTitel:   {meta.title}\n"
              f"Text:    {meta.description}\nTags:    {meta.hashtag_text()}\n")
        if upload:
            if not self.uploaders:
                print("Keine Plattform aktiviert (tiktok/youtube/instagram → enabled: true)")
            for uploader in self.uploaders:
                self.post_one(uploader, self.db.get_clip(clip.id))
            self.finalize()
        return out


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value)[:120] or "clip"
