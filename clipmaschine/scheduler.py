"""Posting-Zeitplan: Tageslimit, Mindestabstand und Uhrzeiten pro Plattform.

Ist ein Limit erreicht, wird exakt berechnet, wann der nächste Post erlaubt ist –
die Maschine macht dann automatisch weiter.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from .config import PostingConfig
from .db import Database


def local_timezone(name: str | None) -> tzinfo:
    if name:
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo


def in_active_hours(local_dt: datetime, hours: list[int] | None) -> bool:
    if not hours:
        return True
    start, end = int(hours[0]) % 24, int(hours[1]) % 24
    if start == end:
        return True
    hour = local_dt.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end   # über Mitternacht, z. B. [18, 2]


def next_active_time(when: datetime, hours: list[int] | None, tz: tzinfo) -> datetime:
    """Frühester Zeitpunkt ≥ when, der in den Posting-Zeiten liegt."""
    local = when.astimezone(tz)
    if in_active_hours(local, hours):
        return when
    start = local.replace(hour=int(hours[0]) % 24, minute=0, second=0, microsecond=0)
    if start <= local:
        start += timedelta(days=1)
    return start.astimezone(timezone.utc)


def burst_position(recent: list[datetime], cfg: PostingConfig) -> int:
    """Wie viele Posts die aktuelle Serie schon hat (recent: neueste zuerst).

    Posts gehören zu einer Serie, solange ihr Abstand kürzer ist als die normale Pause – so zählt ein
    Clip, der etwas länger zum Rendern brauchte, trotzdem noch zur laufenden Serie.
    """
    if not recent:
        return 0
    count = 1
    pause = timedelta(minutes=cfg.min_minutes_between_posts)
    for newer, older in zip(recent, recent[1:]):
        if newer - older >= pause:
            break
        count += 1
    return count


def next_allowed_time(db: Database, platform: str, cfg: PostingConfig, now: datetime,
                      max_posts: int | None = None) -> tuple[datetime, str]:
    """Wann darf auf `platform` frühestens wieder gepostet werden – und warum nicht sofort?

    max_posts überschreibt das allgemeine Tageslimit für diese Plattform (z. B. YouTube-Kontingent).
    """
    limit = max_posts or cfg.max_posts_per_day
    when, reason = now, ""
    recent = db.post_times_since(platform, now - timedelta(hours=24))
    if len(recent) >= limit:
        # sobald der älteste der letzten N Posts aus dem 24-h-Fenster fällt, ist wieder Platz
        free_at = recent[limit - 1] + timedelta(hours=24)
        if free_at > when:
            when, reason = free_at, f"Tageslimit erreicht ({len(recent)}/{limit})"
    if recent:
        in_series = burst_position(recent, cfg) < max(cfg.burst_size, 1)
        gap = cfg.burst_gap_minutes if in_series else cfg.min_minutes_between_posts
        gap_end = recent[0] + timedelta(minutes=gap)
        if gap_end > when:
            when, reason = gap_end, ("nächster Clip der Serie" if in_series else "Mindestabstand zwischen Posts")
    active = next_active_time(when, cfg.active_hours, local_timezone(cfg.timezone))
    if active > when:
        when, reason = active, reason or "außerhalb der Posting-Zeiten"
    return when, reason


def slot_available(db: Database, platform: str, cfg: PostingConfig, now: datetime) -> tuple[bool, str]:
    when, reason = next_allowed_time(db, platform, cfg, now)
    if when <= now:
        return True, ""
    local = when.astimezone(local_timezone(cfg.timezone))
    return False, f"{reason} – nächster Post {local:%d.%m. %H:%M}"
