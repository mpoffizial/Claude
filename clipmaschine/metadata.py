"""Hook, Titel, Beschreibung und Hashtags – optional mit Claude, sonst per Vorlage."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field

from .config import MetadataConfig
from .models import Clip, PostMetadata
from .overlay import strip_emojis

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You write the on-screen hook and the post text for short vertical videos cut from Twitch \
stream highlights. They are posted to TikTok, YouTube Shorts and Instagram Reels, always crediting the streamer.

Aim for text that makes a scrolling viewer stop and watch, while staying truthful: describe only what the clip \
title, stream context or transcript support. Do not invent events, quotes, drama or outcomes, and avoid \
misleading clickbait - a hook that overpromises loses viewers and hurts the account.

Fields:
- hook: on-screen text over the video, at most 7 words, no emojis and no hashtags (the video renderer cannot \
draw emojis). Curiosity or reaction framing works well.
- title: post title, at most 90 characters, may contain one or two emojis, no hashtags.
- description: one or two short sentences that credit the streamer as twitch.tv/<login>.
- hashtags: 5 to 8 relevant hashtags without the # sign, lowercase, no spaces. Include the streamer and the \
game when known.

Write all text in the language you are asked for."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "hook": {"type": "string", "description": "On-screen hook, at most 7 words, no emojis"},
        "title": {"type": "string", "description": "Post title, at most 90 characters"},
        "description": {"type": "string", "description": "1-2 sentences crediting twitch.tv/<login>"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "description": "5-8 hashtags without #"},
    },
    "required": ["hook", "title", "description", "hashtags"],
    "additionalProperties": False,
}

LANGUAGE_NAMES = {"de": "German", "en": "English", "es": "Spanish", "fr": "French", "it": "Italian",
                  "pt": "Portuguese", "tr": "Turkish", "pl": "Polish", "nl": "Dutch"}

_HYPE_HOOKS = {
    "de": ["Der Chat ist komplett eskaliert", "Damit hat keiner gerechnet", "Dieser Moment war unfassbar",
           "Der Chat konnte nicht mehr"],
    "en": ["Chat went absolutely crazy", "Nobody saw this coming", "This moment was unreal",
           "Chat could not handle this"],
}
_DESCRIPTION = {
    "de": "Highlight aus dem Stream von {name} 🎮 Original: twitch.tv/{login}",
    "en": "Highlight from {name}'s stream 🎮 Original: twitch.tv/{login}",
}


@dataclass
class MetadataContext:
    clip: Clip
    source: str = "top_clips"                    # top_clips | chat_hype | manual
    transcript: str = ""
    context: dict = field(default_factory=dict)  # z. B. Hype-Infos, Stream-Titel
    extra_hashtags: list[str] = field(default_factory=list)


def normalize_hashtag(tag: str) -> str:
    tag = strip_emojis(str(tag)).lstrip("#").lower()
    return re.sub(r"[^\w]", "", tag, flags=re.UNICODE)


def merge_hashtags(*groups: list[str], limit: int = 8) -> list[str]:
    seen: list[str] = []
    for group in groups:
        for tag in group:
            norm = normalize_hashtag(tag)
            if norm and norm not in seen:
                seen.append(norm)
    return seen[:limit]


def _truncate(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return (cut or text[: limit - 1]).rstrip(" ,.;:-") + "…"


def _limit_words(text: str, max_words: int) -> str:
    words = text.split()
    return " ".join(words[:max_words])


def with_prefix(title: str, prefix: str) -> str:
    """Stellt z. B. "GTA 6: " voran – außer der Titel enthält es schon."""
    core = prefix.strip(" :-|")
    if not core or core.lower() in title.lower():
        return title
    return f"{prefix}{title}"


def template_metadata(ctx: MetadataContext, cfg: MetadataConfig) -> PostMetadata:
    clip = ctx.clip
    lang = cfg.language if cfg.language in _DESCRIPTION else "en"
    clean_title = strip_emojis(clip.title).strip(" -|")
    if ctx.source == "chat_hype" or not clean_title:
        options = _HYPE_HOOKS[lang]
        digest = int(hashlib.sha256(clip.id.encode()).hexdigest(), 16)
        hook = options[digest % len(options)]
    else:
        hook = _limit_words(clean_title, 7)
    title = _truncate(with_prefix(f"{clip.title or hook} | {clip.broadcaster_name}", cfg.title_prefix), 90)
    description = _DESCRIPTION[lang].format(name=clip.broadcaster_name, login=clip.broadcaster_login)
    hashtags = merge_hashtags([clip.broadcaster_login, clip.game_name], ctx.extra_hashtags, cfg.default_hashtags,
                              limit=cfg.max_hashtags)
    return PostMetadata(hook=hook, title=title, description=description, hashtags=hashtags, source="template")


def _claude_mode(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    value = str(value).strip().lower()
    if value in ("true", "on", "yes", "1"):
        return "on"
    if value in ("false", "off", "no", "0"):
        return "off"
    return "auto"


class MetadataGenerator:
    """Erzeugt Post-Texte. Claude wird genutzt, wenn verfügbar – jeder Fehler fällt auf die Vorlage zurück."""

    def __init__(self, cfg: MetadataConfig, client=None):
        self.cfg = cfg
        self.mode = _claude_mode(cfg.use_claude)
        self._client = client
        self._disabled_reason: str | None = None
        self._anthropic = None
        if self.mode == "off":
            self._disabled_reason = "in der Konfiguration deaktiviert"
            return
        try:
            import anthropic
            self._anthropic = anthropic
        except ImportError:
            self._disable("Paket 'anthropic' nicht installiert (pip install anthropic)")
            return
        if self._client is None:
            self._client = anthropic.Anthropic(max_retries=3)

    @property
    def uses_claude(self) -> bool:
        return self._client is not None and self._disabled_reason is None

    def _disable(self, reason: str) -> None:
        self._disabled_reason = reason
        level = logging.WARNING if self.mode == "on" else logging.INFO
        log.log(level, "KI-Titel aus (%s) – verwende Vorlagen", reason)

    def generate(self, ctx: MetadataContext) -> PostMetadata:
        if self.uses_claude:
            meta = self._try_claude(ctx)
            if meta:
                return meta
        return template_metadata(ctx, self.cfg)

    # ------------------------------------------------------------------ Claude
    def _try_claude(self, ctx: MetadataContext) -> PostMetadata | None:
        anthropic = self._anthropic
        try:
            return self._generate_with_claude(ctx)
        except TypeError as exc:
            # Das SDK wirft TypeError, wenn gar keine Zugangsdaten gefunden werden
            if "authentication" in str(exc).lower():
                self._disable("kein ANTHROPIC_API_KEY gefunden")
            else:
                log.warning("KI-Titel fehlgeschlagen (%s) – verwende Vorlage", exc)
        except anthropic.AuthenticationError:
            self._disable("API-Key ungültig")
        except anthropic.PermissionDeniedError:
            self._disable("API-Key hat keine Berechtigung")
        except anthropic.NotFoundError:
            self._disable(f"Modell '{self.cfg.model}' nicht verfügbar")
        except anthropic.RateLimitError:
            log.warning("Claude: Rate-Limit erreicht – diesmal Vorlage")
        except anthropic.APIStatusError as exc:
            log.warning("Claude: API-Fehler %s – diesmal Vorlage", exc.status_code)
        except anthropic.APIConnectionError:
            log.warning("Claude nicht erreichbar – diesmal Vorlage")
        except (ValueError, KeyError) as exc:
            log.warning("Claude-Antwort unbrauchbar (%s) – diesmal Vorlage", exc)
        return None

    def _user_message(self, ctx: MetadataContext) -> str:
        clip = ctx.clip
        language = LANGUAGE_NAMES.get(self.cfg.language, self.cfg.language)
        info = {
            "streamer_name": clip.broadcaster_name,
            "streamer_login": clip.broadcaster_login,
            "game": clip.game_name or "unknown",
            "clip_title": clip.title,
            "clip_views": clip.view_count,
            "clip_seconds": round(clip.duration),
            "how_it_was_found": {
                "chat_hype": "the live chat suddenly exploded at this moment",
                "top_clips": "one of the most viewed clips of the stream",
            }.get(ctx.source, "picked manually"),
        }
        info.update({k: v for k, v in ctx.context.items() if v})
        parts = [f"Write the texts in {language}.", "Clip data:", json.dumps(info, ensure_ascii=False, indent=2)]
        if ctx.transcript:
            parts += ["Transcript of the clip (automatic, may contain errors):", ctx.transcript]
        if ctx.extra_hashtags:
            parts.append("Always include these hashtags: " + ", ".join(ctx.extra_hashtags))
        if self.cfg.extra_instructions:
            parts.append(self.cfg.extra_instructions)
        return "\n\n".join(parts)

    def _generate_with_claude(self, ctx: MetadataContext) -> PostMetadata | None:
        response = self._client.beta.messages.create(
            model=self.cfg.model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": self._user_message(ctx)}],
            output_config={"effort": self.cfg.effort, "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            # Falls ein Sicherheitsfilter ablehnt, beantwortet ein Ersatzmodell dieselbe Anfrage
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            log.info("Claude hat den Clip %s abgelehnt – verwende Vorlage", ctx.clip.id)
            return None
        if response.stop_reason == "max_tokens":
            raise ValueError("Antwort abgeschnitten")
        text = next((block.text for block in response.content if block.type == "text"), "")
        data = json.loads(text)
        return self._postprocess(data, ctx)

    def _postprocess(self, data: dict, ctx: MetadataContext) -> PostMetadata:
        clip = ctx.clip
        hook = _limit_words(strip_emojis(str(data["hook"])).replace("#", ""), 9)
        title = _truncate(with_prefix(str(data["title"]).replace("#", ""), self.cfg.title_prefix), 95)
        description = " ".join(str(data["description"]).split())
        credit = f"twitch.tv/{clip.broadcaster_login}"
        if credit.lower() not in description.lower():
            description = f"{description} Original: {credit}".strip()
        hashtags = merge_hashtags(ctx.extra_hashtags, list(data.get("hashtags") or []),
                                  [clip.broadcaster_login], self.cfg.default_hashtags, limit=self.cfg.max_hashtags)
        if not hook or not title:
            raise ValueError("leerer Hook oder Titel")
        return PostMetadata(hook=hook, title=title, description=description, hashtags=hashtags, source="claude")
