"""Server-side Mina playback completion monitoring."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .mina_client import MinaClientError
from .service import MusicService


def normalize_playback_status(raw: dict[str, Any] | None) -> str:
    if not isinstance(raw, dict):
        return "unknown"
    value: Any = None
    for key in ("status", "state", "play_status", "player_state"):
        if key in raw:
            value = raw[key]
            break
    if isinstance(value, dict):
        value = value.get("status") or value.get("state")
    if isinstance(value, bool):
        return "playing" if value else "stopped"
    text = str(value or "").strip().casefold()
    if any(token in text for token in ("playing", "play", "running", "playing_music")):
        return "playing"
    if "pause" in text:
        return "paused"
    if any(token in text for token in ("finish", "complete", "ended", "end")):
        return "finished"
    if any(token in text for token in ("stop", "idle", "none", "empty")):
        return "stopped"
    return "unknown"


class PlaybackMonitor:
    """Poll Mina status and advance the service queue after completion."""

    def __init__(self, service: MusicService, *, interval_sec: float = 2.0, grace_sec: float = 3.0) -> None:
        self.service = service
        self.interval_sec = interval_sec
        self.grace_sec = grace_sec
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="playback-monitor")

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        await task
        self._task = None

    async def poll_once(self) -> str | None:
        state, _mode, current_index, started_at = self.service.monitor_snapshot()
        device_id = self.service.device_id
        getter = getattr(self.service.mina_client, "get_playback_status", None)
        if state != "playing" or current_index is None or not device_id or getter is None:
            return None
        if time.monotonic() - started_at < self.grace_sec:
            return None
        try:
            raw = await asyncio.to_thread(getter, device_id)
        except MinaClientError as exc:
            self.service.set_playback_probe("unknown", str(exc))
            return "unknown"
        except Exception as exc:  # defensive: an injected adapter must not kill the monitor
            self.service.set_playback_probe("unknown", str(exc))
            return "unknown"
        status = normalize_playback_status(raw)
        self.service.set_playback_probe(status)
        if status in {"finished", "stopped"}:
            try:
                await asyncio.to_thread(self.service.advance_after_completion)
            except MinaClientError as exc:
                self.service.set_playback_probe("unknown", str(exc))
                return "unknown"
            except Exception as exc:  # keep the monitor alive for transient adapter errors
                self.service.set_playback_probe("unknown", str(exc))
                return "unknown"
        return status

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(0.05, self.interval_sec))
            except asyncio.TimeoutError:
                pass
