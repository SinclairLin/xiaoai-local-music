"""Music catalogue and mock playback service."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .models import Track


SUPPORTED_SUFFIXES = {".mp3", ".flac", ".m4a", ".wav"}


class MusicScanError(RuntimeError):
    """Raised when the configured music root cannot be scanned."""


class MusicService:
    """Scan a read-only music directory and expose deterministic mock playback."""

    def __init__(self, music_dir: str | Path = "/music") -> None:
        self.music_dir = Path(music_dir)
        self._tracks: tuple[Track, ...] | None = None

    def scan(self) -> list[Track]:
        """Build and store a deterministic snapshot of the music directory."""
        if not self.music_dir.is_dir():
            raise MusicScanError(f"music root does not exist or is not a directory: {self.music_dir}")

        try:
            paths = sorted(self.music_dir.rglob("*"))
            tracks: list[Track] = []
            for path in paths:
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue
                relative = path.relative_to(self.music_dir).as_posix()
                track_id = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12]
                tracks.append(Track(id=track_id, title=path.stem, path=f"/music/{relative}"))
        except OSError as exc:
            raise MusicScanError(f"cannot scan music root {self.music_dir}: {exc}") from exc

        self._tracks = tuple(tracks)
        return list(self._tracks)

    def list_tracks(self, query: str | None = None) -> list[Track]:
        if self._tracks is None:
            self.scan()
        assert self._tracks is not None
        if not query:
            return list(self._tracks)
        needle = query.casefold()
        return [
            track
            for track in self._tracks
            if needle in f"{track.title} {track.path}".casefold()
        ]

    def get_track(self, track_id: str) -> Track | None:
        return next((track for track in self.list_tracks() if track.id == track_id), None)

    def play(self, track_id: str) -> Track | None:
        """Return the selected track as a mock playback acknowledgement."""
        return self.get_track(track_id)
