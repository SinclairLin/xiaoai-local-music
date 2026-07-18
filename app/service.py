"""Music catalogue and mock playback service."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .models import Track


SUPPORTED_SUFFIXES = {".mp3", ".flac", ".m4a", ".wav"}


class MusicService:
    """Scan a read-only music directory and expose deterministic mock playback."""

    def __init__(self, music_dir: str | Path = "/music") -> None:
        self.music_dir = Path(music_dir)

    def list_tracks(self, query: str | None = None) -> list[Track]:
        if not self.music_dir.is_dir():
            return []

        tracks: list[Track] = []
        for path in sorted(self.music_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            relative = path.relative_to(self.music_dir).as_posix()
            title = path.stem
            if query and query.casefold() not in f"{title} {relative}".casefold():
                continue
            track_id = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12]
            tracks.append(Track(id=track_id, title=title, path=f"/music/{relative}"))
        return tracks

    def get_track(self, track_id: str) -> Track | None:
        return next((track for track in self.list_tracks() if track.id == track_id), None)

    def play(self, track_id: str) -> Track | None:
        """Return the selected track as a mock playback acknowledgement."""
        return self.get_track(track_id)

