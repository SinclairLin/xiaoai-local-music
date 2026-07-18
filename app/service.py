"""Music catalogue and mock playback service."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

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
    """Scan a read-only music directory and expose deterministic mock playback."""

    def __init__(self, music_dir: str | Path, public_base_url: str) -> None:
        self.music_dir = Path(music_dir)
        self.public_base_url = public_base_url.rstrip("/")
        self._music_root: Path | None = None
        self._entries: tuple[_TrackEntry, ...] | None = None
        self._entries_by_id: dict[str, _TrackEntry] = {}

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
                search_text=f"{path.stem} {relative}".casefold(),
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
        needle = query.casefold()
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

    def play(self, track_id: str) -> Track | None:
        """Return the selected track as a mock playback acknowledgement."""
        return self.get_track(track_id)
