"""Live-Modus: Chat beobachten und bei Hype sofort einen Twitch-Clip erstellen."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from .chat import ChatMessage, HypeDetector, HypeEvent, TwitchChatClient
from .config import AppConfig
from .db import Database
from .models import Clip
from .twitch_api import TwitchAPI, TwitchAPIError

log = logging.getLogger(__name__)


class LiveClipper:
    def __init__(self, cfg: AppConfig, api: TwitchAPI, db: Database, stop_event: threading.Event):
        self.cfg = cfg
        self.api = api
        self.db = db
        self.stop_event = stop_event
        self.detector = HypeDetector(cfg.live)
        self.users: dict[str, dict] = {}
        self.streams: dict[str, dict] = {}       # login -> aktueller Stream (Titel, Spiel)
        self._busy: set[str] = set()
        self._busy_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="clipper")
        self._chat: TwitchChatClient | None = None
        self._status_thread: threading.Thread | None = None

    def start(self) -> bool:
        if not self.api.has_user_token():
            log.error("Live-Modus braucht einen Twitch-Login mit clips:edit: python -m clipmaschine auth twitch")
            return False
        self.users = self.api.get_users(logins=[s.login for s in self.cfg.streamers])
        if not self.users:
            log.error("Live-Modus: keine gültigen Twitch-Kanäle gefunden")
            return False
        self._refresh_live_status()
        self._start_status_thread()
        self._start_chat()
        log.info("Live-Modus aktiv für %d Kanäle", len(self.users))
        return True

    def stop(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def ensure_running(self) -> None:
        """Watchdog: startet Chat- oder Statusthread neu, falls einer unerwartet beendet wurde."""
        if self.stop_event.is_set():
            return
        if self._chat is None or not self._chat.is_alive():
            log.warning("Chat-Verbindung war beendet – starte neu")
            self._start_chat()
        if self._status_thread is None or not self._status_thread.is_alive():
            log.warning("Live-Status-Prüfung war beendet – starte neu")
            self._start_status_thread()

    # ------------------------------------------------------------------ intern
    def _start_chat(self) -> None:
        self._chat = TwitchChatClient(list(self.users), self._on_message, self.stop_event)
        self._chat.start()

    def _start_status_thread(self) -> None:
        self._status_thread = threading.Thread(target=self._status_loop, daemon=True, name="live-status")
        self._status_thread.start()

    def _status_loop(self) -> None:
        while not self.stop_event.wait(self.cfg.live.live_check_interval_seconds):
            try:
                self._refresh_live_status()
            except Exception:
                log.exception("Fehler bei der Live-Status-Prüfung – nächster Versuch gleich")

    def _refresh_live_status(self) -> None:
        try:
            live = self.api.get_streams([u["id"] for u in self.users.values()])
        except TwitchAPIError as exc:
            log.warning("Live-Status konnte nicht geprüft werden: %s", exc)
            return
        for login, user in self.users.items():
            stream = live.get(user["id"])
            was_live = login in self.streams
            if stream:
                self.streams[login] = stream
                if not was_live:
                    log.info("🔴 %s ist live: %s", login, stream.get("title", ""))
            else:
                self.streams.pop(login, None)
            self.detector.set_live(login, bool(stream))

    def _on_message(self, msg: ChatMessage) -> None:
        event = self.detector.add(msg)
        if event is None:
            return
        with self._busy_lock:
            if event.channel in self._busy:
                return
            self._busy.add(event.channel)
        log.info("🔥 Hype bei %s: %.1fx normal, %.1f Nachr./s, %d Chatter %s", event.channel, event.ratio,
                 event.messages_per_second, event.unique_chatters, event.top_keywords)
        self._executor.submit(self._clip_event, event)

    def _clip_event(self, event: HypeEvent) -> None:
        try:
            self._create_and_store(event)
        except TwitchAPIError as exc:
            log.warning("Clip für %s fehlgeschlagen: %s", event.channel, exc)
        except Exception:
            log.exception("Unerwarteter Fehler beim Clippen von %s", event.channel)
        finally:
            with self._busy_lock:
                self._busy.discard(event.channel)

    def _create_and_store(self, event: HypeEvent) -> None:
        user = self.users.get(event.channel)
        if not user or self.stop_event.wait(self.cfg.live.clip_delay_seconds):
            return
        clip_id = self.api.create_clip(user["id"])
        # Twitch braucht einige Sekunden; laut Doku nach ~15 s per Get Clips prüfen
        data = None
        for wait in (10, 5, 5, 5):
            if self.stop_event.wait(wait):
                return
            data = self.api.get_clip(clip_id)
            if data:
                break
        if not data:
            log.warning("Twitch hat den Clip %s nicht fertiggestellt – übersprungen", clip_id)
            return
        game = self.api.game_names([str(data.get("game_id") or "")]).get(str(data.get("game_id") or ""), "")
        clip = Clip.from_helix(data, event.channel, game)
        context = event.to_context()
        stream = self.streams.get(event.channel) or {}
        if stream.get("title"):
            context["stream_title"] = stream["title"]
        score = self.cfg.live.hype_score_bonus + event.ratio * 10
        if self.db.upsert_clip(clip, "chat_hype", score, context=context):
            log.info("🎬 Hype-Clip erstellt: %s (%s)", clip.url, clip.title)
