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


def next_allowed_time(db: Database, platform: str, cfg: PostingConfig, now: datetime) -> tuple[datetime, str]:
    """Wann darf auf `platform` frühestens wieder gepostet werden – und warum nicht sofort?"""
    when, reason = now, ""
    recent = db.post_times_since(platform, now - timedelta(hours=24))
    if len(recent) >= cfg.max_posts_per_day:
        # sobald der älteste der letzten N Posts aus dem 24-h-Fenster fällt, ist wieder Platz
        free_at = recent[cfg.max_posts_per_day - 1] + timedelta(hours=24)
        if free_at > when:
            when, reason = free_at, f"Tageslimit erreicht ({len(recent)}/{cfg.max_posts_per_day})"
    if recent:
        gap_end = recent[0] + timedelta(minutes=cfg.min_minutes_between_posts)
        if gap_end > when:
            when, reason = gap_end, "Mindestabstand zwischen Posts"
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
