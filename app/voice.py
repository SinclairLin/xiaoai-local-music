"""Chinese voice command parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class VoiceIntent(str, Enum):
    PLAY = "play"
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"
    NEXT = "next"
    PREVIOUS = "previous"


@dataclass(frozen=True)
class ParsedIntent:
    intent: VoiceIntent
    query: str | None = None
    raw: str = ""


_PLAY_PREFIXES = ("放一首", "放一下", "我要听", "我想听", "点播", "来一首", "播放")
_CONTROL_PATTERNS = (
    (VoiceIntent.STOP, ("退出播放", "别放了", "停止", "停下")),
    (VoiceIntent.PAUSE, ("暂停",)),
    (VoiceIntent.RESUME, ("继续", "接着放")),
    (VoiceIntent.NEXT, ("下一首", "切歌")),
    (VoiceIntent.PREVIOUS, ("上一首", "上一曲")),
)


def _normalize(text: str | None) -> str:
    value = re.sub(r"\s+", "", text or "")
    # Both "小爱同学请播放" and "请小爱同学播放" occur in ASR output.
    changed = True
    while changed:
        changed = False
        for prefix in ("小爱同学", "请"):
            if value.startswith(prefix):
                value = value[len(prefix) :]
                changed = True
    return value.lstrip("，,。！？!?：:")


def parse_command(text: str | None) -> ParsedIntent | None:
    """Parse one normalized Chinese voice command."""
    normalized = _normalize(text)
    if not normalized:
        return None
    for prefix in _PLAY_PREFIXES:
        if normalized.startswith(prefix):
            query = normalized[len(prefix) :]
            if query.startswith("本地"):
                query = query[2:]
            # A bare prefix like "播放" must not turn into a play-everything.
            if not query:
                return None
            return ParsedIntent(VoiceIntent.PLAY, query, normalized)
    for intent, phrases in _CONTROL_PATTERNS:
        if normalized in phrases or any(normalized.startswith(phrase) for phrase in phrases):
            return ParsedIntent(intent, raw=normalized)
    return None


def parse_intents(text: str | None) -> list[ParsedIntent]:
    """Parse a possibly punctuated ASR result into independent commands."""
    parts = re.split(r"[，,。；;！!\n]+", text or "")
    parsed = [parse_command(part) for part in parts]
    return [item for item in parsed if item is not None]


def parse_play_command(text: str | None) -> str | None:
    parsed = parse_command(text)
    return parsed.query if parsed and parsed.intent is VoiceIntent.PLAY else None
