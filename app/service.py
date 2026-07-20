"""Music catalogue scanning and Mina playback coordination."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .mina_client import MinaClient, MinaDeviceError, MockMinaClient
from .models import Track


MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
}
SUPPORTED_SUFFIXES = set(MEDIA_TYPES)


class MusicScanError(RuntimeError):
    """Raised when the configured music root cannot be scanned."""


class PlaybackStateError(RuntimeError):
    """Raised when playback cannot be performed with the current state."""


class TrackNotFoundError(PlaybackStateError):
    """Raised when an explicitly requested queue item is missing."""


PlaybackMode = Literal["once", "single_loop", "sequential", "list_loop"]


@dataclass(frozen=True)
class MediaFile:
    path: Path
    media_type: str
    stat_result: os.stat_result


@dataclass(frozen=True)
class _TrackEntry:
    track: Track
    file_path: Path
    media_type: str
    search_text: str


class MusicService:
    """Scan a read-only music directory and coordinate Mina playback."""

    def __init__(self, music_dir: str | Path, public_base_url: str, mina_client: MinaClient | None = None, device_id: str | None = None) -> None:
        self.music_dir = Path(music_dir)
        self.public_base_url = public_base_url.rstrip("/")
        self._music_root: Path | None = None
        self._entries: tuple[_TrackEntry, ...] | None = None
        self._entries_by_id: dict[str, _TrackEntry] = {}
        self.mina_client = mina_client or MockMinaClient(device_id)
        # Omitting the client is a test convenience; only then adopt the
        # implicit mock's device so an injected client never observes a
        # fabricated selection.
        if device_id is None and mina_client is None:
            device_id = self.mina_client.device_id
        self.device_id = device_id
        self._queue: tuple[Track, ...] = ()
        self._current_index: int | None = None
        self._state = "idle"
        self._mode: PlaybackMode = "once"
        self._queue_revision = 0
        self._playback_status = "idle"
        self._playback_error: str | None = None
        self._started_at = 0.0

    def scan(self) -> list[Track]:
        """Build and store a deterministic snapshot of the music directory."""
        if not self.music_dir.is_dir():
            raise MusicScanError(f"music root does not exist or is not a directory: {self.music_dir}")

        try:
            music_root = self.music_dir.resolve(strict=True)
            paths = sorted(self.music_dir.rglob("*"))
        except OSError as exc:
            raise MusicScanError(f"cannot scan music root {self.music_dir}: {exc}") from exc

        entries: list[_TrackEntry] = []
        entries_by_id: dict[str, _TrackEntry] = {}
        for path in paths:
            suffix = path.suffix.lower()
            # A file may vanish between the rglob listing and these checks;
            # skip it instead of failing the whole scan.
            try:
                if not path.is_file() or suffix not in SUPPORTED_SUFFIXES:
                    continue
                resolved_path = path.resolve(strict=True)
            except OSError:
                continue
            if not resolved_path.is_relative_to(music_root):
                continue
            relative = path.relative_to(self.music_dir).as_posix()
            track_id = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12]
            if track_id in entries_by_id:
                raise MusicScanError(
                    f"track id collision between {entries_by_id[track_id].file_path} and {resolved_path}"
                )
            track = Track(
                id=track_id,
                title=path.stem,
                path=f"{self.public_base_url}/media/by-id/{track_id}",
            )
            entry = _TrackEntry(
                track=track,
                file_path=resolved_path,
                media_type=MEDIA_TYPES[suffix],
                # Whitespace-insensitive haystack: the voice parser strips all
                # spaces from queries while filenames usually keep them.
                search_text=re.sub(r"\s+", "", f"{path.stem} {relative}").casefold(),
            )
            entries.append(entry)
            entries_by_id[track_id] = entry

        self._music_root = music_root
        self._entries = tuple(entries)
        self._entries_by_id = entries_by_id
        return [entry.track for entry in self._entries]

    def _snapshot(self) -> tuple[_TrackEntry, ...]:
        if self._entries is None:
            self.scan()
        assert self._entries is not None
        return self._entries

    def list_tracks(self, query: str | None = None) -> list[Track]:
        entries = self._snapshot()
        if not query:
            return [entry.track for entry in entries]
        needle = re.sub(r"\s+", "", query).casefold()
        return [entry.track for entry in entries if needle in entry.search_text]

    def get_track(self, track_id: str) -> Track | None:
        self._snapshot()
        entry = self._entries_by_id.get(track_id)
        return entry.track if entry is not None else None

    def get_media_file(self, track_id: str) -> MediaFile | None:
        self._snapshot()
        entry = self._entries_by_id.get(track_id)
        if entry is None or self._music_root is None:
            return None

        try:
            resolved_path = entry.file_path.resolve(strict=True)
            if not resolved_path.is_relative_to(self._music_root):
                return None
            stat_result = resolved_path.stat()
        except OSError:
            return None
        if not stat.S_ISREG(stat_result.st_mode):
            return None
        return MediaFile(path=resolved_path, media_type=entry.media_type, stat_result=stat_result)

    def _require_device(self) -> str:
        if not self.device_id:
            raise MinaDeviceError("no Mina device is configured")
        return self.device_id

    def queue_state(self) -> dict[str, object]:
        current = None
        if self._current_index is not None and 0 <= self._current_index < len(self._queue):
            current = self._queue[self._current_index]
        return {
            "ok": True,
            "state": self._state,
            "current": current,
            "queue": list(self._queue),
            "device": self.device_id,
            "mode": self._mode,
            "current_index": self._current_index,
            "queue_revision": self._queue_revision,
            "playback_status": self._playback_status,
            "playback_error": self._playback_error,
        }

    def _set_loop_mode(self, mode: PlaybackMode) -> None:
        setter = getattr(self.mina_client, "set_loop", None)
        if setter is None:
            return
        # MiService: 0=single-track repeat, 1=list/sequential playback.
        setter(0 if mode == "single_loop" else 1, self._require_device())

    def _play_target(self, target: Track, target_index: int, *, mode: PlaybackMode | None = None) -> Track:
        device_id = self._require_device()
        selected_mode = mode or self._mode
        self._set_loop_mode(selected_mode)
        self.mina_client.play_by_url(target.path, device_id)
        self._current_index = target_index
        self._state = "playing"
        self._playback_status = "playing"
        self._playback_error = None
        self._started_at = time.monotonic()
        self._queue_revision += 1
        return target

    def play(self, track_id: str, queue_ids: list[str] | None = None, mode: PlaybackMode | None = None) -> Track | None:
        """Play a track after validating the entire requested queue."""
        track = self.get_track(track_id)
        if track is None:
            return None
        ids = queue_ids or [track_id]
        if track_id not in ids:
            raise PlaybackStateError("track_id must be included in queue_ids")
        tracks = [self.get_track(item) for item in ids]
        if any(item is None for item in tracks):
            raise TrackNotFoundError("one or more queue tracks were not found")
        if any(self.get_media_file(item.id) is None for item in tracks if item is not None):
            raise TrackNotFoundError("one or more queue tracks are no longer available")
        queue = tuple(item for item in tracks if item is not None)
        current_index = queue.index(track)
        selected_mode: PlaybackMode = mode or ("single_loop" if len(queue) == 1 else "sequential")
        if selected_mode not in {"once", "single_loop", "sequential", "list_loop"}:
            raise PlaybackStateError("unsupported playback mode")
        previous = (
            self._queue,
            self._current_index,
            self._state,
            self._mode,
            self._queue_revision,
            self._playback_status,
            self._playback_error,
            self._started_at,
        )
        self._queue = queue
        self._mode = selected_mode
        try:
            return self._play_target(track, current_index, mode=selected_mode)
        except Exception:
            (
                self._queue,
                self._current_index,
                self._state,
                self._mode,
                self._queue_revision,
                self._playback_status,
                self._playback_error,
                self._started_at,
            ) = previous
            raise

    def _move(self, delta: int) -> Track | None:
        device_id = self._require_device()
        if self._current_index is None or not self._queue:
            raise PlaybackStateError("playback queue is empty")
        target_index = self._current_index + delta
        if self._mode == "list_loop" and self._queue:
            target_index %= len(self._queue)
        if target_index < 0 or target_index >= len(self._queue):
            return self._queue[self._current_index]
        target = self._queue[target_index]
        return self._play_target(target, target_index)

    def next(self) -> Track | None:
        return self._move(1)

    def previous(self) -> Track | None:
        return self._move(-1)

    def pause(self) -> None:
        self.mina_client.pause(self._require_device())
        self._state = "paused"
        self._playback_status = "paused"

    def stop(self) -> None:
        self.mina_client.stop(self._require_device())
        self._state = "stopped"
        self._playback_status = "stopped"
        self._playback_error = None

    def resume(self) -> Track:
        if self._current_index is None or not self._queue:
            raise PlaybackStateError("playback queue is empty")
        self.mina_client.play(self._require_device())
        self._state = "playing"
        self._playback_status = "playing"
        self._playback_error = None
        self._started_at = time.monotonic()
        return self._queue[self._current_index]

    def set_volume(self, volume: int) -> None:
        self.mina_client.set_volume(volume, self._require_device())

    def set_device_id(self, device_id: str | None) -> None:
        self.device_id = device_id

    def monitor_snapshot(self) -> tuple[str, PlaybackMode, int | None, float]:
        return self._state, self._mode, self._current_index, self._started_at

    def set_playback_probe(self, status: str, error: str | None = None) -> None:
        self._playback_status = status
        self._playback_error = error

    def advance_after_completion(self) -> Track | None:
        """Advance once after a terminal device status was observed."""
        if self._current_index is None or not self._queue:
            return None
        index = self._current_index
        if self._mode == "once":
            self._state = "stopped"
            self._playback_status = "finished"
            self._queue_revision += 1
            return None
        if self._mode == "single_loop":
            return self._play_target(self._queue[index], index)
        if index + 1 < len(self._queue):
            return self._play_target(self._queue[index + 1], index + 1)
        if self._mode == "list_loop":
            return self._play_target(self._queue[0], 0)
        self._state = "stopped"
        self._playback_status = "finished"
        self._queue_revision += 1
        return None
