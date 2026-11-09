"""ASS-Untertiteldatei für Hook-Text, Karaoke-Untertitel und Credit (gerendert von ffmpeg/libass)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# libass kann keine farbigen Emojis zeichnen → werden aus Texten im Video entfernt
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002B00-\U00002BFF"
    "\U0000FE0F\U0000200D\U000020E3\U00002300-\U000023FF]+"
)


def strip_emojis(text: str) -> str:
    return " ".join(_EMOJI_RE.sub(" ", text).split())


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """'#RRGGBB' → ASS-Farbe '&HAABBGGRR' (Alpha 0 = deckend)."""
    value = hex_rgb.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError(f"Ungültige Farbe '{hex_rgb}' (erwartet #RRGGBB)")
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(seconds: float) -> str:
    cs = max(int(round(seconds * 100)), 0)
    hours, rest = divmod(cs, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    """Verhindert, dass Text als ASS-Steuercode interpretiert wird."""
    text = text.replace("\\", "/").replace("{", "(").replace("}", ")")
    return "\\N".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


@dataclass
class AssStyle:
    name: str
    font: str
    size: int
    color: str = "#FFFFFF"
    alpha: int = 0
    outline_color: str = "#000000"
    outline: float = 6.0
    shadow: float = 2.0
    bold: bool = True
    alignment: int = 2           # Numpad-Logik: 2 = unten Mitte, 5 = Mitte, 8 = oben Mitte
    margin_l: int = 70
    margin_r: int = 70
    margin_v: int = 100

    def line(self) -> str:
        primary = ass_color(self.color, self.alpha)
        outline = ass_color(self.outline_color)
        back = ass_color("#000000", 0x80)
        bold = -1 if self.bold else 0
        return (f"Style: {self.name},{self.font},{self.size},{primary},{primary},{outline},{back},"
                f"{bold},0,0,0,100,100,0,0,1,{self.outline:g},{self.shadow:g},{self.alignment},"
                f"{self.margin_l},{self.margin_r},{self.margin_v},1")


@dataclass
class AssEvent:
    start: float
    end: float
    style: str
    text: str                    # bereits escaped (darf Override-Tags enthalten)
    layer: int = 0

    def line(self) -> str:
        return f"Dialogue: {self.layer},{ass_time(self.start)},{ass_time(self.end)},{self.style},,0,0,0,,{self.text}"


@dataclass
class AssDocument:
    width: int
    height: int
    styles: list[AssStyle] = field(default_factory=list)
    events: list[AssEvent] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {self.width}",
            f"PlayResY: {self.height}",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            *(style.line() for style in self.styles),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *(event.line() for event in sorted(self.events, key=lambda e: (e.start, e.layer))),
            "",
        ]
        return "\n".join(lines)


def karaoke_events(chunks, style: str, highlight_color: str, base_color: str) -> list[AssEvent]:
    """Pro Wort ein Event: der ganze Häppchen-Text, das gerade gesprochene Wort farbig hervorgehoben."""
    hl = ass_color(highlight_color)
    base = ass_color(base_color)
    events: list[AssEvent] = []
    for chunk in chunks:
        words = [w for w in chunk.words if strip_emojis(w.text)]
        if not words:
            continue
        texts = [ass_escape(strip_emojis(w.text)) for w in words]
        for i, word in enumerate(words):
            start = chunk.start if i == 0 else word.start
            end = chunk.end if i == len(words) - 1 else words[i + 1].start
            if end <= start:
                continue
            parts = [f"{{\\c{hl}}}{t}{{\\c{base}}}" if j == i else t for j, t in enumerate(texts)]
            pop = "{\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
            events.append(AssEvent(start, end, style, pop + " ".join(parts), layer=1))
    return events
