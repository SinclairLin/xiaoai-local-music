"""Async XiaoAI conversation polling and local playback dispatch."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from .voice import ParsedIntent, VoiceIntent, parse_intents


@dataclass(frozen=True)
class ConversationEvent:
    text: str
    timestamp: int
    event_id: str | None = None
    source: str = "conversation"


@dataclass(frozen=True)
class VoicePollResult:
    events: tuple[ConversationEvent, ...] = ()
    source: str = "conversation"


class VoiceSource(Protocol):
    async def poll(self, after_timestamp: int) -> VoicePollResult: ...


class RingLog:
    def __init__(self, maxlen: int = 200) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = asyncio.Lock()

    async def append(self, item: dict[str, Any]) -> None:
        async with self._lock:
            self._items.append(dict(item))

    async def snapshot(self, limit: int | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            values = list(self._items)
        if limit is not None:
            values = values[-max(1, min(limit, len(values))) :]
        return values


class VoiceWorker:
    def __init__(
        self,
        source: VoiceSource,
        service: Any,
        *,
        mina_client: Any = None,
        device_id: str | None = None,
        hardware: str = "",
        enabled: bool = False,
        hijack_all_play: bool = True,
        speak_confirm: bool = True,
        poll_interval_sec: float = 1.5,
        backoff_max: float = 60.0,
        log: RingLog | None = None,
    ) -> None:
        self.source = source
        self.service = service
        self.mina_client = mina_client or getattr(service, "mina_client", None)
        self.device_id = device_id or getattr(service, "device_id", None)
        self.hardware = hardware
        self.enabled = enabled
        self.hijack_all_play = hijack_all_play
        self.speak_confirm = speak_confirm
        self.poll_interval_sec = poll_interval_sec
        self.backoff_max = backoff_max
        self.log = log or RingLog()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()
        self._cursor = 0
        self._last_poll_at: float | None = None
        self._last_event_at: float | None = None
        self._source_name = "idle"
        self._errors = 0
        self._backoff_sec = poll_interval_sec
        self._last_error: str | None = None
        self._baseline_pending = True

    async def start(self) -> None:
        async with self._lifecycle_lock:
            task = self._task
            if task and not task.done() and not self._stop.is_set():
                return
            if task:
                # A concurrent stop() may still be draining the old task.
                self._stop.set()
                await task
                if self._task is task:
                    self._task = None
            self._stop = asyncio.Event()
            self._baseline_pending = True
            self._task = asyncio.create_task(self._run(), name="voice-worker")

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            task = self._task
            if task is None:
                return
            self._stop.set()
        try:
            await task
        finally:
            async with self._lifecycle_lock:
                # A concurrent start() may already own a fresh task.
                if self._task is task:
                    self._task = None

    async def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            await self.start()
        else:
            await self.stop()

    def update_runtime(self, *, source: VoiceSource | None = None, mina_client: Any = None, device_id: str | None = None, hardware: str | None = None, enabled: bool | None = None, hijack_all_play: bool | None = None, speak_confirm: bool | None = None, poll_interval_sec: float | None = None) -> None:
        if source is not None:
            self.source = source
        if mina_client is not None:
            self.mina_client = mina_client
        if device_id is not None:
            self.device_id = device_id
        if hardware is not None:
            self.hardware = hardware
        if enabled is not None:
            self.enabled = enabled
        if hijack_all_play is not None:
            self.hijack_all_play = hijack_all_play
        if speak_confirm is not None:
            self.speak_confirm = speak_confirm
        if poll_interval_sec is not None:
            self.poll_interval_sec = poll_interval_sec

    async def dispatch_text(self, text: str, *, timestamp: int = 0, source: str = "manual", raise_errors: bool = False) -> list[dict[str, Any]]:
        event = ConversationEvent(text=text, timestamp=timestamp, source=source)
        return await self.dispatch(event, raise_errors=raise_errors)

    async def dispatch(self, event: ConversationEvent, *, raise_errors: bool = False) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        intents = parse_intents(event.text)
        if not intents:
            await self._log(event, outcome="ignored", error="unsupported command")
            return results
        for parsed in intents:
            if parsed.intent is VoiceIntent.PLAY and not self.hijack_all_play:
                await self._log(event, parsed, outcome="ignored", error="play hijack disabled")
                continue
            try:
                result = await self._dispatch_one(parsed)
                results.append(result)
                await self._log(event, parsed, outcome="ok", **result)
            except Exception as exc:  # one bad command must not kill the worker
                await self._log(event, parsed, outcome="error", error=str(exc))
                if raise_errors:
                    raise
        return results

    async def _dispatch_one(self, parsed: ParsedIntent) -> dict[str, Any]:
        if parsed.intent is VoiceIntent.PLAY:
            matches = await asyncio.to_thread(self.service.list_tracks, parsed.query)
            if not matches:
                return {"error": "track not found"}
            track = matches[0]
            if self.speak_confirm and self.mina_client and self.device_id:
                await asyncio.to_thread(self.mina_client.text_to_speech, f"好的，正在播放{track.title}", self.device_id)
            played = await asyncio.to_thread(
                self.service.play, track.id, [track.id], "sequential", "off"
            )
            return {"matched_track": {"id": track.id, "title": track.title}, "stream_url": getattr(played, "path", track.path)}
        method = {
            VoiceIntent.STOP: "stop",
            VoiceIntent.PAUSE: "pause",
            VoiceIntent.RESUME: "resume",
            VoiceIntent.NEXT: "next",
            VoiceIntent.PREVIOUS: "previous",
        }.get(parsed.intent)
        if method is None:
            raise ValueError(f"unsupported intent: {parsed.intent.value}")
        track = await asyncio.to_thread(getattr(self.service, method))
        if track is not None:
            return {"matched_track": {"id": track.id, "title": track.title}, "stream_url": track.path}
        return {}

    async def _run(self) -> None:
        delay = self.poll_interval_sec
        while not self._stop.is_set():
            try:
                self._last_poll_at = time.time()
                result = await self.source.poll(self._cursor)
                self._source_name = result.source
                events = sorted(result.events, key=lambda event: event.timestamp)
                if self._baseline_pending:
                    if events:
                        self._cursor = max(event.timestamp for event in events)
                        for event in events:
                            await self._log(event, outcome="baseline")
                    self._baseline_pending = False
                    if not events:
                        await self._log(ConversationEvent("", self._cursor, source=result.source), outcome="baseline")
                else:
                    for event in events:
                        if event.timestamp <= self._cursor:
                            await self._log(event, outcome="ignored", error="duplicate timestamp")
                            continue
                        self._cursor = event.timestamp
                        self._last_event_at = time.time()
                        await self.dispatch(event)
                self._errors = 0
                self._last_error = None
                self._backoff_sec = self.poll_interval_sec
                # Reset before sleeping so recovery does not sit out one more
                # stale backoff period.
                delay = self.poll_interval_sec
                await self._wait(delay)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._errors += 1
                self._last_error = str(exc)
                self._backoff_sec = min(self.backoff_max, max(self.poll_interval_sec, delay * 2))
                await self._log(ConversationEvent("", self._cursor, source=self._source_name), outcome="error", error=str(exc))
                await self._wait(self._backoff_sec)
                delay = self._backoff_sec

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.01, seconds))
        except asyncio.TimeoutError:
            pass

    async def _log(self, event: ConversationEvent, parsed: ParsedIntent | None = None, **fields: Any) -> None:
        item: dict[str, Any] = {
            "timestamp": time.time(),
            "source": event.source,
            "raw_query": event.text,
            "intent": parsed.intent.value if parsed else None,
        }
        item.update(fields)
        await self.log.append(item)

    async def status(self) -> dict[str, Any]:
        task = self._task
        return {
            "enabled": self.enabled,
            "running": bool(task and not task.done()),
            "source": self._source_name,
            "last_timestamp": self._cursor,
            "last_poll_at": self._last_poll_at,
            "last_event_at": self._last_event_at,
            "consecutive_errors": self._errors,
            "backoff_sec": self._backoff_sec,
            "last_error": self._last_error,
            "device_id": self.device_id,
            "hardware": self.hardware,
        }


class MinaVoiceSource:
    """Async adapter around the existing synchronous Mina client bridge."""

    def __init__(self, mina_client: Any, device_id: str | None, hardware: str) -> None:
        self.mina_client = mina_client
        self.device_id = device_id
        self.hardware = hardware

    async def poll(self, after_timestamp: int) -> VoicePollResult:
        if not self.device_id or not self.hardware:
            raise ValueError("voice device_id and hardware are required")
        records = await asyncio.to_thread(self.mina_client.fetch_voice_events, self.device_id, self.hardware, after_timestamp)
        events = tuple(
            ConversationEvent(str(item.get("query", "")), int(item.get("timestamp", 0)), str(item.get("request_id")) if item.get("request_id") else None, str(item.get("source", "conversation")))
            for item in records
            if item.get("query") and int(item.get("timestamp", 0)) > after_timestamp
        )
        source = str(records[0].get("source", "conversation")) if records else "conversation"
        return VoicePollResult(events, source)
