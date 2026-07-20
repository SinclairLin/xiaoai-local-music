"""Music catalogue scanning and Mina playback coordination."""

from __future__ import annotations

import hashlib
import os
import random
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from .mina_client import MinaClient, MinaDeviceError, MockMinaClient
from .models import PlaybackOrder, RepeatMode, Track


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
        self._order: PlaybackOrder = "sequential"
        self._repeat: RepeatMode = "off"
        self._play_sequence: tuple[int, ...] = ()
        self._play_cursor: int | None = None
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
            "order": self._order,
            "repeat": self._repeat,
            "current_index": self._current_index,
            "queue_revision": self._queue_revision,
            "playback_status": self._playback_status,
            "playback_error": self._playback_error,
        }

    def _set_loop_mode(self, repeat: RepeatMode) -> None:
        setter = getattr(self.mina_client, "set_loop", None)
        if setter is None:
            return
        # MiService: 0=single-track repeat, 1=list/sequential playback.
        setter(0 if repeat == "one" else 1, self._require_device())

    def _play_target(self, target: Track, target_index: int) -> Track:
        device_id = self._require_device()
        self._set_loop_mode(self._repeat)
        self.mina_client.play_by_url(target.path, device_id)
        self._current_index = target_index
        self._play_cursor = self._play_sequence.index(target_index)
        self._state = "playing"
        self._playback_status = "playing"
        self._playback_error = None
        self._started_at = time.monotonic()
        self._queue_revision += 1
        return target

    @staticmethod
    def _validate_playback_options(order: str, repeat: str) -> None:
        if order not in {"sequential", "shuffle"}:
            raise PlaybackStateError("unsupported playback order")
        if repeat not in {"off", "all", "one"}:
            raise PlaybackStateError("unsupported repeat mode")

    @staticmethod
    def _new_shuffle_sequence(length: int, *, exclude_index: int | None = None) -> tuple[int, ...]:
        sequence = list(range(length))
        random.shuffle(sequence)
        if exclude_index is not None and length > 1 and sequence[0] == exclude_index:
            sequence[0], sequence[1] = sequence[1], sequence[0]
        return tuple(sequence)

    def _build_play_sequence(self, length: int, current_index: int) -> tuple[int, ...]:
        if self._order == "sequential":
            return tuple(range(length))
        sequence = list(self._new_shuffle_sequence(length))
        sequence.remove(current_index)
        sequence.insert(0, current_index)
        return tuple(sequence)

    def play(
        self,
        track_id: str,
        queue_ids: list[str] | None = None,
        order: PlaybackOrder | None = None,
        repeat: RepeatMode | None = None,
    ) -> Track | None:
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
        selected_order: PlaybackOrder = order or "sequential"
        selected_repeat: RepeatMode = repeat or "off"
        self._validate_playback_options(selected_order, selected_repeat)
        previous = (
            self._queue,
            self._current_index,
            self._state,
            self._order,
            self._repeat,
            self._play_sequence,
            self._play_cursor,
            self._queue_revision,
            self._playback_status,
            self._playback_error,
            self._started_at,
        )
        self._queue = queue
        self._order = selected_order
        self._repeat = selected_repeat
        self._play_sequence = self._build_play_sequence(len(queue), current_index)
        self._play_cursor = self._play_sequence.index(current_index)
        try:
            return self._play_target(track, current_index)
        except Exception:
            (
                self._queue,
                self._current_index,
                self._state,
                self._order,
                self._repeat,
                self._play_sequence,
                self._play_cursor,
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
        if not self._play_sequence or self._play_cursor is None:
            raise PlaybackStateError("playback sequence is empty")
        target_cursor = self._play_cursor + delta
        if target_cursor < 0 or target_cursor >= len(self._play_sequence):
            if self._repeat != "all":
                return self._queue[self._current_index]
            if delta > 0 and self._order == "shuffle":
                self._play_sequence = self._new_shuffle_sequence(
                    len(self._queue), exclude_index=self._current_index
                )
            target_cursor %= len(self._play_sequence)
        target_index = self._play_sequence[target_cursor]
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
        self._queue_revision += 1

    def stop(self) -> None:
        self.mina_client.stop(self._require_device())
        self._state = "stopped"
        self._playback_status = "stopped"
        self._playback_error = None
        self._queue_revision += 1

    def resume(self) -> Track:
        if self._current_index is None or not self._queue:
            raise PlaybackStateError("playback queue is empty")
        self.mina_client.play(self._require_device())
        self._state = "playing"
        self._playback_status = "playing"
        self._playback_error = None
        self._started_at = time.monotonic()
        self._queue_revision += 1
        return self._queue[self._current_index]

    def set_volume(self, volume: int) -> None:
        self.mina_client.set_volume(volume, self._require_device())

    def set_device_id(self, device_id: str | None) -> None:
        self.device_id = device_id

    def monitor_snapshot(self) -> tuple[str, PlaybackOrder, int | None, float, int]:
        return self._state, self._order, self._current_index, self._started_at, self._queue_revision

    def set_playback_probe(self, status: str, error: str | None = None) -> None:
        self._playback_status = status
        self._playback_error = error

    def advance_after_completion(self, expected_revision: int | None = None) -> Track | None:
        """Advance once after a terminal device status was observed.

        ``expected_revision`` guards against a stale probe: any user-initiated
        play/pause/stop/resume bumps the revision, invalidating observations
        that were in flight when the request landed.
        """
        if expected_revision is not None and expected_revision != self._queue_revision:
            return None
        if self._state != "playing":
            return None
        if self._current_index is None or not self._queue:
            return None
        index = self._current_index
        if self._repeat == "one":
            return self._play_target(self._queue[index], index)
        if self._play_sequence and self._play_cursor is not None:
            target_cursor = self._play_cursor + 1
            if target_cursor < len(self._play_sequence):
                target_index = self._play_sequence[target_cursor]
                return self._play_target(self._queue[target_index], target_index)
            if self._repeat == "all":
                if self._order == "shuffle":
                    self._play_sequence = self._new_shuffle_sequence(
                        len(self._queue), exclude_index=index
                    )
                else:
                    self._play_sequence = tuple(range(len(self._queue)))
                target_index = self._play_sequence[0]
                return self._play_target(self._queue[target_index], target_index)
        self._state = "stopped"
        self._playback_status = "finished"
        self._queue_revision += 1
        return None
