from clipmaschine.chat import ChatMessage, HypeDetector, parse_irc_line, split_irc_line
from clipmaschine.config import LiveConfig


def test_parse_privmsg_with_tags_and_emotes():
    line = ("@badge-info=;color=#FF0000;display-name=Max\\sMuster;emotes=25:0-4,6-10/1902:12-16;"
            "tmi-sent-ts=1700000000000 :maxmuster!maxmuster@maxmuster.tmi.twitch.tv PRIVMSG #SomeStreamer "
            ":Kappa Kappa Keepo KEKW")
    msg = parse_irc_line(line, now=123.0)
    assert msg.channel == "somestreamer"
    assert msg.user == "maxmuster"
    assert msg.text == "Kappa Kappa Keepo KEKW"
    assert msg.emote_count == 3
    assert msg.timestamp == 123.0


def test_parse_action_and_ignores_other_commands():
    msg = parse_irc_line(":a!a@a.tmi.twitch.tv PRIVMSG #chan :\x01ACTION tanzt\x01", now=1)
    assert msg.text == "tanzt"
    assert parse_irc_line(":tmi.twitch.tv 001 justinfan123 :Welcome", now=1) is None
    assert split_irc_line("PING :tmi.twitch.tv")[2] == "PING"
    assert split_irc_line(":tmi.twitch.tv RECONNECT")[2] == "RECONNECT"


def _cfg(**kw) -> LiveConfig:
    base = dict(window_seconds=10, baseline_seconds=120, warmup_seconds=60, spike_factor=3.0,
                min_messages_per_second=1.5, min_unique_chatters=8, cooldown_seconds=120)
    base.update(kw)
    return LiveConfig(**base)


def _feed_baseline(detector, start=0.0, seconds=120, rate=0.5, users=10, channel="chan"):
    events = []
    t = start
    i = 0
    while t < start + seconds:
        events.append(detector.add(ChatMessage(channel, f"user{i % users}", "hallo zusammen", t)))
        t += 1 / rate
        i += 1
    return t, [e for e in events if e]


def _feed_burst(detector, start, seconds=8, rate=6.0, users=25, text="KEKW KEKW", channel="chan"):
    events = []
    t = start
    i = 0
    while t < start + seconds:
        events.append(detector.add(ChatMessage(channel, f"hype{i % users}", text, t)))
        t += 1 / rate
        i += 1
    return t, [e for e in events if e]


def test_detector_triggers_on_spike_once_with_cooldown():
    detector = HypeDetector(_cfg())
    t, early = _feed_baseline(detector)
    assert early == []
    t, events = _feed_burst(detector, t)
    assert len(events) == 1
    event = events[0]
    assert event.channel == "chan"
    assert event.ratio >= 3.0
    assert event.unique_chatters >= 8
    assert "kekw" in event.top_keywords
    # direkt danach: Cooldown verhindert weitere Clips
    _, more = _feed_burst(detector, t + 1)
    assert more == []


def test_detector_respects_warmup_and_offline():
    detector = HypeDetector(_cfg())
    _, events = _feed_burst(detector, 0.0)        # Burst direkt am Anfang: noch in der Warmup-Phase
    assert events == []

    detector = HypeDetector(_cfg())
    detector.set_live("chan", False)
    t, _ = _feed_baseline(detector)
    _, events = _feed_burst(detector, t)
    assert events == []


def test_detector_ignores_steady_busy_chat():
    detector = HypeDetector(_cfg())
    # konstant viel los (6 Nachr./s), aber keine Spitze → kein Hype
    t, events = _feed_burst(detector, 0.0, seconds=200, rate=6.0, text="gg")
    assert events == []


def test_keyword_matching():
    detector = HypeDetector(_cfg(hype_keywords=["kekw", "clip it", "😂", "+2"]))
    assert detector.keyword_hits("KEKW kekw") == ["kekw", "kekw"]
    assert detector.keyword_hits("bitte CLIP IT jemand") == ["clip it"]
    assert detector.keyword_hits("😂😂") == ["😂", "😂"]
    assert detector.keyword_hits("+2 lol") == ["+2"]
    assert detector.keyword_hits("kekwait") == []


def test_chat_client_ping_messages_and_reconnect():
    """Echter Socket-Durchlauf gegen einen lokalen Fake-IRC-Server."""
    import socket
    import threading
    import time as _time

    from clipmaschine.chat import TwitchChatClient

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    received_lines: list[str] = []

    def serve():
        for connection in range(2):
            conn, _ = server.accept()
            with conn:
                buf = b""
                while b"JOIN" not in buf:
                    buf += conn.recv(4096)
                received_lines.extend(buf.decode().split("\r\n"))
                conn.sendall(b"PING :tmi.twitch.tv\r\n")
                pong = conn.recv(4096).decode()
                received_lines.append(pong.strip())
                conn.sendall(f"@emotes= :fan!fan@fan.tmi.twitch.tv PRIVMSG #kanal :Nachricht {connection}\r\n"
                             .encode())
                if connection == 0:
                    conn.sendall(b":tmi.twitch.tv RECONNECT\r\n")   # Twitch verlangt Neuverbindung
                    _time.sleep(0.2)
                else:
                    _time.sleep(0.5)

    threading.Thread(target=serve, daemon=True).start()
    messages = []
    stop = threading.Event()
    client = TwitchChatClient(["Kanal"], messages.append, stop, host="127.0.0.1", port=port, tls=False)
    client.start()
    deadline = _time.monotonic() + 15
    while len(messages) < 2 and _time.monotonic() < deadline:
        _time.sleep(0.05)
    stop.set()
    client.join(timeout=5)
    server.close()

    assert [m.text for m in messages] == ["Nachricht 0", "Nachricht 1"]
    assert client.connections == 2
    assert "JOIN #kanal" in received_lines and "PONG :tmi.twitch.tv" in received_lines
    assert any(line.startswith("NICK justinfan") for line in received_lines)
