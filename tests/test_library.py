from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.service import MusicService


def test_track_metadata_defaults_are_api_compatible(tmp_path: Path) -> None:
    (tmp_path / "untagged.mp3").touch()
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)

    with TestClient(
        create_app(settings=settings, service=MusicService(tmp_path, public_base_url))
    ) as client:
        track = client.get("/api/tracks").json()["tracks"][0]

    assert track["album"] == ""
    assert track["duration"] == 0.0
    assert track["mtime"] == 0.0
    assert track["size"] == 0


def test_library_snapshot_remains_stable_after_startup(tmp_path: Path) -> None:
    (tmp_path / "first.mp3").touch()
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)
    service = MusicService(tmp_path, public_base_url)

    with TestClient(create_app(settings=settings, service=service)) as client:
        assert [track["title"] for track in client.get("/api/tracks").json()["tracks"]] == ["first"]
        (tmp_path / "second.mp3").touch()
        assert [track["title"] for track in client.get("/api/tracks").json()["tracks"]] == ["first"]
