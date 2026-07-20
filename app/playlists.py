"""Persistent named playlists."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PlaylistStoreError(RuntimeError):
    """Raised when the playlist file cannot be read or written."""


class PlaylistStore:
    """A small atomic JSON store for user-managed playlists."""

    def __init__(self, config_dir: str | Path) -> None:
        self.path = Path(config_dir) / "playlists.json"
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}
        try:
            self._load()
        except PlaylistStoreError as exc:
            self._items = {}
            backup = self._quarantine()
            logger.warning("playlist store unreadable, backed up to %s and starting empty: %s", backup, exc)

    def _quarantine(self) -> Path:
        """Move an unreadable store aside so a later write cannot destroy it."""
        backup = self.path.with_name(f"{self.path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
        try:
            os.replace(self.path, backup)
        except OSError as exc:
            raise PlaylistStoreError(f"cannot back up corrupt playlist store {self.path}: {exc}") from exc
        return backup

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PlaylistStoreError(f"cannot read playlist store {self.path}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(payload.get("playlists"), list):
            raise PlaylistStoreError(f"invalid playlist store format: {self.path}")
        for item in payload["playlists"]:
            if not isinstance(item, dict):
                raise PlaylistStoreError(f"invalid playlist entry in {self.path}")
            playlist_id = item.get("id")
            name = item.get("name")
            track_ids = item.get("track_ids")
            if not isinstance(playlist_id, str) or not playlist_id or not isinstance(name, str) or not isinstance(track_ids, list):
                raise PlaylistStoreError(f"invalid playlist entry in {self.path}")
            if not all(isinstance(track_id, str) and track_id for track_id in track_ids):
                raise PlaylistStoreError(f"invalid track IDs in playlist {playlist_id}")
            if len(set(track_ids)) != len(track_ids):
                raise PlaylistStoreError(f"duplicate track IDs in playlist {playlist_id}")
            self._items[playlist_id] = {"id": playlist_id, "name": name, "track_ids": list(track_ids)}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "playlists": list(self._items.values())}
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
        except OSError as exc:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise PlaylistStoreError(f"cannot write playlist store {self.path}: {exc}") from exc

    @staticmethod
    def _copy(item: dict[str, Any]) -> dict[str, Any]:
        return {"id": item["id"], "name": item["name"], "track_ids": list(item["track_ids"])}

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._copy(item) for item in self._items.values()]

    def get(self, playlist_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(playlist_id)
            return self._copy(item) if item is not None else None

    def create(self, name: str, track_ids: list[str]) -> dict[str, Any]:
        with self._lock:
            playlist = {"id": str(uuid.uuid4()), "name": name, "track_ids": list(track_ids)}
            self._items[playlist["id"]] = playlist
            self._write()
            return self._copy(playlist)

    def update(self, playlist_id: str, *, name: str | None = None, track_ids: list[str] | None = None) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(playlist_id)
            if item is None:
                return None
            if name is not None:
                item["name"] = name
            if track_ids is not None:
                item["track_ids"] = list(track_ids)
            self._write()
            return self._copy(item)

    def delete(self, playlist_id: str) -> bool:
        with self._lock:
            if playlist_id not in self._items:
                return False
            del self._items[playlist_id]
            self._write()
            return True
