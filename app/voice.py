"""Voice command parsing helpers."""

from __future__ import annotations

import re


_PLAY_RE = re.compile(r"^\s*播放\s*(.+?)\s*$")


def parse_play_command(text: str) -> str | None:
    """Extract a title from any command beginning with ``播放``."""
    match = _PLAY_RE.match(text or "")
    if not match:
        return None
    title = match.group(1).strip()
    if title.startswith("本地"):
        title = title[2:].strip()
    return title or None

