"""Automatische Untertitel (optional, lokal mit faster-whisper) im TikTok-Stil."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import CaptionsConfig

log = logging.getLogger(__name__)

_models: dict[tuple, object] = {}
_models_lock = threading.Lock()


class CaptionsUnavailable(Exception):
    pass


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class CaptionChunk:
    start: float
    end: float
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def _model(cfg: CaptionsConfig):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise CaptionsUnavailable("faster-whisper ist nicht installiert: pip install faster-whisper") from exc
    key = (cfg.model, cfg.device, cfg.compute_type)
    with _models_lock:
        if key not in _models:
            log.info("Lade Whisper-Modell '%s' (beim ersten Mal wird es heruntergeladen) …", cfg.model)
            _models[key] = WhisperModel(cfg.model, device=cfg.device, compute_type=cfg.compute_type)
        return _models[key]


def transcribe(video_path: Path, cfg: CaptionsConfig) -> list[Word]:
    model = _model(cfg)
    segments, _info = model.transcribe(
        str(video_path), language=cfg.language or None, word_timestamps=True, vad_filter=True,
    )
    words: list[Word] = []
    for segment in segments:          # Generator – hier läuft die eigentliche Erkennung
        for w in segment.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(float(w.start), float(w.end), text))
    return words


def chunk_words(words: list[Word], max_words: int = 3, max_seconds: float = 1.6,
                max_chars: int = 22, uppercase: bool = True) -> list[CaptionChunk]:
    """Fasst Wörter zu kurzen, schnell wechselnden Untertitel-Häppchen zusammen."""
    chunks: list[CaptionChunk] = []
    current: list[Word] = []

    def flush() -> None:
        if current:
            words_out = [Word(w.start, w.end, w.text.upper() if uppercase else w.text) for w in current]
            chunks.append(CaptionChunk(current[0].start, current[-1].end, words_out))
            current.clear()

    for word in words:
        if current:
            too_many = len(current) >= max_words
            too_long = word.end - current[0].start > max_seconds
            too_wide = len(" ".join(w.text for w in current)) + 1 + len(word.text) > max_chars
            pause = word.start - current[-1].end > 0.6
            if too_many or too_long or too_wide or pause:
                flush()
        current.append(word)
        if word.text[-1] in ".!?":
            flush()
    flush()

    # kleine Lücken schließen, damit die Untertitel nicht flackern
    for this, nxt in zip(chunks, chunks[1:]):
        if 0 < nxt.start - this.end < 0.5:
            this.end = nxt.start
    return chunks
