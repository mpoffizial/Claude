"""Twitch-Chat (IRC, anonym/nur lesend) und Hype-Erkennung über Chat-Spitzen."""

from __future__ import annotations

import logging
import random
import re
import socket
import ssl
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Callable

from .config import LiveConfig

log = logging.getLogger(__name__)

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6697
_TAG_UNESCAPE = {"\\s": " ", "\\:": ";", "\\\\": "\\", "\\r": "\r", "\\n": "\n"}
_WORD_RE = re.compile(r"[\w+!']+", re.UNICODE)


@dataclass
class ChatMessage:
    channel: str
    user: str
    text: str
    timestamp: float
    emote_count: int = 0


@dataclass
class HypeEvent:
    channel: str
    timestamp: float
    ratio: float                     # wie viel aktiver als die Baseline
    messages_per_second: float
    unique_chatters: int
    top_keywords: list[str] = field(default_factory=list)

    def to_context(self) -> dict:
        return {
            "hype_ratio": round(self.ratio, 2),
            "messages_per_second": round(self.messages_per_second, 2),
            "unique_chatters": self.unique_chatters,
            "chat_keywords": self.top_keywords,
        }


def _unescape_tag(value: str) -> str:
    return re.sub(r"\\[s:\\rn]", lambda m: _TAG_UNESCAPE[m.group(0)], value)


def split_irc_line(line: str) -> tuple[dict[str, str], str, str, str]:
    """Zerlegt eine IRC-Zeile in (tags, prefix, command, params)."""
    rest = line.rstrip("\r\n")
    tags: dict[str, str] = {}
    if rest.startswith("@"):
        raw_tags, _, rest = rest[1:].partition(" ")
        for item in raw_tags.split(";"):
            key, _, value = item.partition("=")
            tags[key] = _unescape_tag(value)
    prefix = ""
    if rest.startswith(":"):
        prefix, _, rest = rest[1:].partition(" ")
    command, _, params = rest.partition(" ")
    return tags, prefix, command, params


def parse_irc_line(line: str, now: float | None = None) -> ChatMessage | None:
    """Parst eine IRC-Zeile; gibt nur für PRIVMSG (Chatnachrichten) etwas zurück."""
    tags, prefix, command, params = split_irc_line(line)
    if command != "PRIVMSG":
        return None
    target, _, text = params.partition(" :")
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        text = text[8:-1]
    user = prefix.split("!", 1)[0] or tags.get("display-name", "")
    emotes = tags.get("emotes", "")
    emote_count = sum(len(group.split(":", 1)[1].split(",")) for group in emotes.split("/") if ":" in group)
    return ChatMessage(
        channel=target.lstrip("#").lower(),
        user=user.lower(),
        text=text,
        timestamp=time.time() if now is None else now,
        emote_count=emote_count,
    )


class _ChannelWindow:
    """Laufende Summen für Kurzfenster und Baseline (O(1) pro Nachricht)."""

    def __init__(self, first_seen: float):
        self.first_seen = first_seen
        self.short: deque = deque()      # (ts, user, weight, keywords)
        self.base: deque = deque()       # (ts, weight)
        self.short_weight = 0.0
        self.base_weight = 0.0
        self.users: Counter = Counter()
        self.keywords: Counter = Counter()


class HypeDetector:
    """Erkennt Momente, in denen der Chat deutlich aktiver ist als normal.

    Gewicht einer Nachricht = 1 + keyword_weight × (Hype-Wörter + Emotes, max. 3).
    Ausgelöst wird, wenn im Kurzfenster
      • genug Nachrichten/Sekunde und genug verschiedene Chatter sind und
      • die gewichtete Rate ≥ spike_factor × Baseline-Rate ist.
    """

    def __init__(self, cfg: LiveConfig):
        self.cfg = cfg
        self._windows: dict[str, _ChannelWindow] = {}
        self._last_trigger: dict[str, float] = {}
        self._live: dict[str, bool] = {}
        self._lock = threading.Lock()
        words = [k.lower().strip() for k in cfg.hype_keywords if k and k.strip()]
        self._token_keywords = {k for k in words if _WORD_RE.fullmatch(k)}
        self._phrase_keywords = [k for k in words if k not in self._token_keywords]

    def set_live(self, channel: str, live: bool) -> None:
        with self._lock:
            channel = channel.lower()
            if live and not self._live.get(channel, False):
                # Stream (wieder) gestartet: Baseline neu aufbauen
                self._windows.pop(channel, None)
            self._live[channel] = live

    def keyword_hits(self, text: str) -> list[str]:
        lowered = text.lower()
        hits = [tok for tok in _WORD_RE.findall(lowered) if tok in self._token_keywords]
        for phrase in self._phrase_keywords:
            count = lowered.count(phrase)
            hits.extend([phrase] * count)
        return hits

    def add(self, msg: ChatMessage) -> HypeEvent | None:
        cfg = self.cfg
        hits = self.keyword_hits(msg.text)
        n_hits = min(len(hits) + msg.emote_count, 3)
        weight = 1.0 + cfg.keyword_weight * n_hits
        now = msg.timestamp
        with self._lock:
            win = self._windows.get(msg.channel)
            if win is None:
                win = self._windows[msg.channel] = _ChannelWindow(now)
            keywords = tuple(dict.fromkeys(hits))[:3]
            win.short.append((now, msg.user, weight, keywords))
            win.short_weight += weight
            win.users[msg.user] += 1
            win.keywords.update(keywords)
            self._slide(win, now)
            return self._evaluate(msg.channel, win, now)

    def _slide(self, win: _ChannelWindow, now: float) -> None:
        short_start = now - self.cfg.window_seconds
        while win.short and win.short[0][0] < short_start:
            ts, user, weight, keywords = win.short.popleft()
            win.short_weight -= weight
            win.users[user] -= 1
            if win.users[user] <= 0:
                del win.users[user]
            win.keywords.subtract(keywords)
            win.base.append((ts, weight))
            win.base_weight += weight
        base_start = short_start - self.cfg.baseline_seconds
        while win.base and win.base[0][0] < base_start:
            win.base_weight -= win.base.popleft()[1]
        win.short_weight = max(win.short_weight, 0.0)
        win.base_weight = max(win.base_weight, 0.0)

    def _evaluate(self, channel: str, win: _ChannelWindow, now: float) -> HypeEvent | None:
        cfg = self.cfg
        if not self._live.get(channel, True):
            return None
        if now - win.first_seen < cfg.warmup_seconds:
            return None
        if now - self._last_trigger.get(channel, float("-inf")) < cfg.cooldown_seconds:
            return None
        rate = len(win.short) / cfg.window_seconds
        if rate < cfg.min_messages_per_second or len(win.users) < cfg.min_unique_chatters:
            return None
        observed = min(cfg.baseline_seconds, max(now - cfg.window_seconds - win.first_seen, 1.0))
        base_rate = max(win.base_weight / observed, cfg.min_baseline_rate)
        ratio = (win.short_weight / cfg.window_seconds) / base_rate
        if ratio < cfg.spike_factor:
            return None
        self._last_trigger[channel] = now
        top = [k for k, n in win.keywords.most_common(3) if n > 0]
        return HypeEvent(channel=channel, timestamp=now, ratio=ratio, messages_per_second=rate,
                         unique_chatters=len(win.users), top_keywords=top)


class TwitchChatClient(threading.Thread):
    """Liest den Chat mehrerer Kanäle anonym (justinfan) über IRC/TLS und verbindet sich neu."""

    def __init__(self, channels: list[str], on_message: Callable[[ChatMessage], None],
                 stop_event: threading.Event, host: str = IRC_HOST, port: int = IRC_PORT, tls: bool = True):
        super().__init__(daemon=True, name="twitch-chat")
        self.channels = sorted({c.lower() for c in channels})
        self.on_message = on_message
        self.stop_event = stop_event
        self.host = host
        self.port = port
        self.tls = tls
        self.connections = 0

    def run(self) -> None:
        backoff = 1.0
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self._session()
            except (OSError, ConnectionError) as exc:
                log.warning("Chat-Verbindung getrennt (%s) – neuer Versuch in %.0fs", exc, backoff)
            except Exception:
                log.exception("Unerwarteter Chat-Fehler – neuer Versuch in %.0fs", backoff)
            if time.monotonic() - started > 300:
                backoff = 1.0                       # Verbindung lief stabil → schnell neu verbinden
            self.stop_event.wait(backoff)
            backoff = min(backoff * 2, 60)

    def _session(self) -> None:
        raw = socket.create_connection((self.host, self.port), timeout=30)
        sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host) if self.tls else raw
        sock.settimeout(1.0)
        self.connections += 1
        try:
            nick = f"justinfan{random.randint(10000, 99999)}"
            self._send(sock, "CAP REQ :twitch.tv/tags twitch.tv/commands")
            self._send(sock, "PASS SCHMOOPIIE")
            self._send(sock, f"NICK {nick}")
            for i in range(0, len(self.channels), 10):   # Join-Rate-Limit beachten
                self._send(sock, "JOIN " + ",".join(f"#{c}" for c in self.channels[i:i + 10]))
                time.sleep(1.1)
            log.info("Chat verbunden: %s", ", ".join(self.channels))
            buffer = b""
            last_data = time.monotonic()
            while not self.stop_event.is_set():
                try:
                    chunk = sock.recv(65536)
                except (socket.timeout, TimeoutError):
                    if time.monotonic() - last_data > 360:  # Twitch pingt ca. alle 5 Minuten
                        raise ConnectionError("keine Daten seit 6 Minuten") from None
                    continue
                if not chunk:
                    raise ConnectionError("Server hat die Verbindung geschlossen")
                last_data = time.monotonic()
                buffer += chunk
                *lines, buffer = buffer.split(b"\r\n")
                for raw_line in lines:
                    line = raw_line.decode("utf-8", errors="replace")
                    command = split_irc_line(line)[2]
                    if command == "PING":
                        self._send(sock, "PONG " + line.split(" ", 1)[1] if " " in line else "PONG")
                    elif command == "RECONNECT":
                        raise ConnectionError("Twitch fordert Reconnect an")
                    elif command == "PRIVMSG":
                        msg = parse_irc_line(line)
                        if msg:
                            try:
                                self.on_message(msg)
                            except Exception:  # ein Fehler im Callback darf den Chat nicht beenden
                                log.exception("Fehler bei der Verarbeitung einer Chatnachricht")
        finally:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _send(sock: ssl.SSLSocket, line: str) -> None:
        sock.sendall((line + "\r\n").encode("utf-8"))
