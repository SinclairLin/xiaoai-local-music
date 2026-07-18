from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.main import create_app
from app.service import MusicScanError, MusicService


def test_yaml_music_root_and_environment_override(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("music_root: /yaml-music\nport: 9000\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    assert Settings.from_env().music_root == "/yaml-music"

    monkeypatch.setenv("MUSIC_ROOT", "/env-music")
    assert Settings.from_env().music_root == "/env-music"


def test_legacy_music_dir_constructor_alias(tmp_path: Path) -> None:
    settings = Settings(music_dir=tmp_path)
    assert settings.music_root == str(tmp_path)
    assert settings.music_dir == str(tmp_path)


def test_startup_scans_once_and_serves_snapshot(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "稻香.mp3").touch()
    service = MusicService(tmp_path)

    with TestClient(create_app(service=service)) as client:
        assert [track["title"] for track in client.get("/api/tracks").json()["tracks"]] == ["稻香"]
        (tmp_path / "后来.flac").touch()
        assert [track["title"] for track in client.get("/api/tracks").json()["tracks"]] == ["稻香"]


def test_missing_music_root_prevents_startup(tmp_path: Path) -> None:
    with pytest.raises(MusicScanError, match="music root"):
        with TestClient(create_app(service=MusicService(tmp_path / "missing"))):
            pass


def test_invalid_yaml_music_root_fails(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("music_root: 123\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    with pytest.raises(ConfigError, match="music_root"):
        Settings.from_env()
