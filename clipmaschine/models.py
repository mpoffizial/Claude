"""Gemeinsame Datentypen."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    """Einheitliches UTC-Format für die Datenbank (lexikografisch sortierbar)."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Clip:
    id: str
    url: str
    broadcaster_id: str
    broadcaster_login: str
    broadcaster_name: str
    title: str
    view_count: int
    created_at: datetime
    duration: float
    game_id: str = ""
    game_name: str = ""
    language: str = ""
    creator_name: str = ""
    thumbnail_url: str = ""
    video_id: str = ""
    vod_offset: int | None = None

    @classmethod
    def from_helix(cls, data: dict, broadcaster_login: str, game_name: str = "") -> "Clip":
        """Erzeugt einen Clip aus einer Antwort von GET /helix/clips."""
        return cls(
            id=data["id"],
            url=data.get("url") or f"https://clips.twitch.tv/{data['id']}",
            broadcaster_id=str(data.get("broadcaster_id") or ""),
            broadcaster_login=broadcaster_login.lower(),
            broadcaster_name=data.get("broadcaster_name") or broadcaster_login,
            title=(data.get("title") or "").strip(),
            view_count=int(data.get("view_count") or 0),
            created_at=parse_time(data["created_at"]),
            duration=float(data.get("duration") or 0),
            game_id=str(data.get("game_id") or ""),
            game_name=game_name,
            language=data.get("language") or "",
            creator_name=data.get("creator_name") or "",
            thumbnail_url=data.get("thumbnail_url") or "",
            video_id=str(data.get("video_id") or ""),
            vod_offset=data.get("vod_offset"),
        )

    @classmethod
    def from_row(cls, row) -> "Clip":
        return cls(
            id=row["id"],
            url=row["url"],
            broadcaster_id=row["broadcaster_id"] or "",
            broadcaster_login=row["broadcaster_login"] or "",
            broadcaster_name=row["broadcaster_name"] or row["broadcaster_login"] or "",
            title=row["title"] or "",
            view_count=int(row["view_count"] or 0),
            created_at=parse_time(row["created_at"]),
            duration=float(row["duration"] or 0),
            game_name=row["game_name"] or "",
            language=row["language"] or "",
            video_id=row["video_id"] or "",
            vod_offset=row["vod_offset"],
        )


@dataclass
class PostMetadata:
    hook: str                       # Text im Video
    title: str                      # Post-Titel
    description: str                # 1–2 Sätze inkl. Credit
    hashtags: list[str] = field(default_factory=list)  # ohne '#'
    source: str = "template"        # "claude" oder "template"

    def hashtag_text(self, limit: int | None = None) -> str:
        tags = self.hashtags if limit is None else self.hashtags[:limit]
        return " ".join(f"#{tag}" for tag in tags)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PostMetadata":
        return cls(
            hook=data.get("hook", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            hashtags=list(data.get("hashtags") or []),
            source=data.get("source", "template"),
        )
