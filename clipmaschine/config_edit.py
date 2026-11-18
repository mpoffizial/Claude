"""Einstellungen in der config.yaml per Befehl ändern – Kommentare und Reihenfolge bleiben erhalten.

    python -m clipmaschine set test_mode=false posting.max_posts_per_day=10
    python -m clipmaschine set --profil maximal
"""

from __future__ import annotations

import re

# Fertige Profile: so viel posten, wie die Plattformen zuverlässig mitmachen
PROFILES: dict[str, dict[str, str]] = {
    "maximal": {
        "test_mode": "false",
        "posting.max_posts_per_day": "10",          # TikTok + Instagram
        "posting.burst_size": "3",                  # 3 Clips direkt nacheinander (10 min Abstand) ...
        "posting.burst_gap_minutes": "10",
        "posting.min_minutes_between_posts": "240", # ... dann 4 h Pause → Serien bis in den Abend verteilt
        "posting.active_hours": "[8, 24]",
        "posting.queue_size": "10",
        "youtube.max_posts_per_day": "10",          # Browser-Modus; per API pausiert YouTube bei leerem Kontingent
        "discovery.interval_minutes": "10",
    },
    "normal": {
        "test_mode": "false",
        "posting.max_posts_per_day": "6",
        "posting.burst_size": "1",
        "posting.min_minutes_between_posts": "150",
        "posting.queue_size": "6",
    },
    "test": {
        "test_mode": "true",
    },
}

_KEY_RE = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")


def parse_assignments(items: list[str]) -> dict[str, str]:
    changes: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not _KEY_RE.fullmatch(key) or not value:
            raise ValueError(f"'{item}' ist keine gültige Zuweisung (Beispiel: posting.max_posts_per_day=10)")
        changes[key] = value
    return changes


def _value_line(indent: str, key: str, value: str, comment: str = "") -> str:
    return f"{indent}{key}: {value}{comment}"


def set_value(text: str, dotted_key: str, value: str) -> str:
    """Setzt `abschnitt.schlüssel` (oder einen Schlüssel ganz oben) auf `value`."""
    lines = text.splitlines()
    if "." not in dotted_key:
        pattern = re.compile(rf"^{re.escape(dotted_key)}:[ \t]*([^#\n]*?)([ \t]+#.*)?$")
        for i, line in enumerate(lines):
            match = pattern.match(line)
            if match:
                lines[i] = _value_line("", dotted_key, value, match.group(2) or "")
                return "\n".join(lines) + "\n"
        insert_at = 0
        while insert_at < len(lines) and (not lines[insert_at].strip() or lines[insert_at].startswith("#")):
            insert_at += 1
        lines.insert(insert_at, f"{dotted_key}: {value}")
        return "\n".join(lines) + "\n"

    section, key = dotted_key.split(".", 1)
    header = re.compile(rf"^{re.escape(section)}:[ \t]*(#.*)?$")
    start = next((i for i, line in enumerate(lines) if header.match(line)), None)
    if start is None:
        lines += ["", f"{section}:", f"  {key}: {value}"]
        return "\n".join(lines) + "\n"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end = i
            break
    child = re.compile(rf"^([ \t]+){re.escape(key)}:[ \t]*([^#\n]*?)([ \t]+#.*)?$")
    for i in range(start + 1, end):
        match = child.match(lines[i])
        if match:
            lines[i] = _value_line(match.group(1), key, value, match.group(3) or "")
            return "\n".join(lines) + "\n"
    lines.insert(start + 1, f"  {key}: {value}")
    return "\n".join(lines) + "\n"


def apply_changes(text: str, changes: dict[str, str]) -> str:
    for key, value in changes.items():
        text = set_value(text, key, value)
    return text
