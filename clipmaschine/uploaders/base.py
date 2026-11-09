"""Gemeinsame Basis für alle Upload-Ziele."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..models import Clip, PostMetadata

_SECRET_RE = re.compile(r"(access_token=|Bearer |OAuth )[^&\s\"']+")


def redact(text: str) -> str:
    """Entfernt Tokens aus Fehlermeldungen, bevor sie geloggt oder gespeichert werden."""
    return _SECRET_RE.sub(r"\1***", str(text))


@dataclass
class UploadResult:
    status: str          # posted | inbox | submitted
    remote_id: str = ""
    url: str = ""


class UploadError(Exception):
    def __init__(self, message: str, retryable: bool = True, pause_seconds: float = 0):
        super().__init__(redact(message))
        self.retryable = retryable
        self.pause_seconds = pause_seconds   # Plattform eine Weile pausieren (z. B. Quota aufgebraucht)


class Uploader(ABC):
    name: str = ""

    @abstractmethod
    def upload(self, video_path: Path, meta: PostMetadata, clip: Clip) -> UploadResult:
        ...

    def authorize(self) -> None:
        raise NotImplementedError(f"{self.name}: kein interaktiver Login nötig")

    def problem(self) -> str | None:
        """Gibt einen Hinweis zurück, falls die Plattform noch nicht eingerichtet ist."""
        return None


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def truncate_utf16(text: str, limit: int) -> str:
    if utf16_len(text) <= limit:
        return text
    out = []
    used = 0
    for ch in text:
        size = utf16_len(ch)
        if used + size > limit - 1:
            break
        out.append(ch)
        used += size
    return "".join(out).rstrip() + "…"


def build_caption(meta: PostMetadata, limit: int = 2200, max_hashtags: int | None = None,
                  include_title: bool = True) -> str:
    parts = []
    if include_title and meta.title:
        parts.append(meta.title)
    if meta.description:
        parts.append(meta.description)
    tags = meta.hashtag_text(max_hashtags)
    if tags:
        parts.append(tags)
    return truncate_utf16("\n\n".join(parts), limit)
