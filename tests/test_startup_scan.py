from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.main import create_app
from app.service import MusicScanError, MusicService


PUBLIC_BASE_URL = "http://speaker-host:8123"


def test_yaml_music_root_and_environment_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    (tmp_path / "config.yaml").write_text(
        "public_base_url: https://yaml.example/base/\nmusic_root: /yaml-music\nport: 9000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    settings = Settings.from_env()
    assert settings.music_root == "/yaml-music"
    assert settings.public_base_url == "https://yaml.example/base"

    monkeypatch.setenv("MUSIC_ROOT", "/env-music")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://env.example:8123/prefix/")
    settings = Settings.from_env()
    assert settings.music_root == "/env-music"
    assert settings.public_base_url == "http://env.example:8123/prefix"


def test_legacy_music_dir_constructor_alias(tmp_path: Path) -> None:
    settings = Settings(public_base_url=PUBLIC_BASE_URL, music_dir=tmp_path)
    assert settings.music_root == str(tmp_path)
    assert settings.music_dir == str(tmp_path)


def test_startup_scans_once_and_serves_snapshot(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "稻香.mp3").touch()
    settings = Settings(public_base_url=PUBLIC_BASE_URL, music_dir=tmp_path)
    service = MusicService(tmp_path, PUBLIC_BASE_URL)

    with TestClient(create_app(settings=settings, service=service)) as client:
        assert [track["title"] for track in client.get("/api/tracks").json()["tracks"]] == ["稻香"]
        (tmp_path / "后来.flac").touch()
        assert [track["title"] for track in client.get("/api/tracks").json()["tracks"]] == ["稻香"]


def test_missing_music_root_prevents_startup(tmp_path: Path) -> None:
    settings = Settings(public_base_url=PUBLIC_BASE_URL, music_dir=tmp_path / "missing")
    with pytest.raises(MusicScanError, match="music root"):
        with TestClient(
            create_app(
                settings=settings,
                service=MusicService(tmp_path / "missing", PUBLIC_BASE_URL),
            )
        ):
            pass


def test_invalid_yaml_music_root_fails(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        f"public_base_url: {PUBLIC_BASE_URL}\nmusic_root: 123\n", encoding="utf-8"
    )
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    with pytest.raises(ConfigError, match="music_root"):
        Settings.from_env()


def test_missing_public_base_url_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    with pytest.raises(ConfigError, match="public_base_url"):
        Settings.from_env()


@pytest.mark.parametrize(
    "public_base_url",
    [
        "ftp://music.example",
        "http:///missing-host",
        "https://music.example/base?token=value",
        "https://music.example/base#fragment",
        "https://user:password@music.example",
    ],
)
def test_invalid_public_base_url_fails(public_base_url: str) -> None:
    with pytest.raises(ConfigError, match="public_base_url"):
        Settings(public_base_url=public_base_url)
