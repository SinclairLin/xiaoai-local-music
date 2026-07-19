from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.mina_client import MinaMiserviceClient, MockMinaClient
from app.service import MusicService
from app.voice_worker import VoicePollResult


class EmptyVoiceSource:
    async def poll(self, after_timestamp: int) -> VoicePollResult:
        return VoicePollResult(())


def test_api_health_tracks_and_play(tmp_path) -> None:
    (tmp_path / "周杰伦").mkdir()
    (tmp_path / "周杰伦" / "稻香.mp3").touch()
    public_base_url = "https://music.example/proxy"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)
    service = MusicService(tmp_path, public_base_url)

    with TestClient(create_app(settings=settings, service=service)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        tracks = client.get("/api/tracks").json()["tracks"]
        assert tracks[0]["title"] == "稻香"
        track_id = tracks[0]["id"]
        expected_url = f"{public_base_url}/media/by-id/{track_id}"
        assert tracks[0]["path"] == expected_url
        assert client.get("/api/tracks", params={"q": "周杰伦"}).json()["tracks"][0]["id"] == track_id
        play_response = client.post("/api/play", json={"track_id": track_id})
        assert play_response.status_code == 200
        assert play_response.json()["track"]["path"] == expected_url
        assert client.post("/api/play", json={"track_id": "missing"}).status_code == 404
        voice_response = client.post("/api/voice", json={"text": "播放 稻香"})
        assert voice_response.status_code == 200
        assert voice_response.json()["track"]["path"] == expected_url


def test_index_serves_admin_page(tmp_path: Path) -> None:
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)

    with TestClient(
        create_app(settings=settings, service=MusicService(tmp_path, public_base_url))
    ) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "小爱本地音乐 · 管理台" in response.text


def test_voice_status_enable_and_logs(tmp_path: Path) -> None:
    (tmp_path / "稻香.mp3").touch()
    config_dir = tmp_path / "config"
    settings = Settings(
        config_dir=config_dir,
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
        mina_device_id="mock-device",
        voice={"hardware": "LX06"},
    )
    app = create_app(settings=settings, service=MusicService(tmp_path, settings.public_base_url), voice_source=EmptyVoiceSource())
    with TestClient(app) as client:
        assert client.get("/api/voice/status").json()["enabled"] is False
        enabled = client.post("/api/voice/enable", json={"enabled": True})
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True
        injected = client.post("/api/voice", json={"text": "播放稻香"})
        assert injected.status_code == 200
        assert client.get("/api/logs").json()["logs"][-1]["raw_query"] == "播放稻香"
        disabled = client.post("/api/voice/enable", json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
    assert "enabled: false" in (config_dir / "config.yaml").read_text(encoding="utf-8")


def test_voice_rejects_bare_play_command(tmp_path: Path) -> None:
    (tmp_path / "稻香.mp3").touch()
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)

    with TestClient(
        create_app(settings=settings, service=MusicService(tmp_path, public_base_url))
    ) as client:
        assert client.post("/api/voice", json={"text": "播放"}).status_code == 400
        assert client.post("/api/voice", json={"text": "播放 本地"}).status_code == 400


def test_voice_play_matches_titles_containing_spaces(tmp_path: Path) -> None:
    (tmp_path / "Shape of You.mp3").touch()
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)

    with TestClient(
        create_app(settings=settings, service=MusicService(tmp_path, public_base_url))
    ) as client:
        response = client.post("/api/voice", json={"text": "播放 Shape of You"})
        assert response.status_code == 200
        assert response.json()["track"]["title"] == "Shape of You"
        spaced = client.get("/api/tracks", params={"q": "Shape of You"}).json()["tracks"]
        assert [track["title"] for track in spaced] == ["Shape of You"]


def test_config_update_voice_enabled_without_hardware_returns_422(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    config_dir = tmp_path / "config"
    settings = Settings(
        config_dir=config_dir,
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
        mina_device_id="mock-device",
    )
    app = create_app(settings=settings, service=MusicService(tmp_path, settings.public_base_url))
    with TestClient(app) as client:
        response = client.put("/api/config", json={"voice": {"enabled": True}})
        assert response.status_code == 422
        assert "voice.hardware" in response.json()["detail"]

        accepted = client.put("/api/config", json={"voice": {"enabled": True, "hardware": "LX06"}})
        assert accepted.status_code == 200
        assert accepted.json()["voice"]["enabled"] is True
        assert accepted.json()["voice"]["hardware"] == "LX06"


def test_config_api_redacts_password_and_preserves_it_on_masked_update(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    config_dir = tmp_path / "config"
    settings = Settings(
        config_dir=config_dir,
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
        xiaomi_user="user",
        xiaomi_password="secret",
        mina_device_id="device-1",
    )
    with TestClient(create_app(settings=settings, service=MusicService(tmp_path, settings.public_base_url))) as client:
        response = client.get("/api/config")
        assert response.json()["xiaomi_password"] == "********"
        assert "secret" not in response.text
        updated = client.put("/api/config", json={"mina_device_id": "device-2", "xiaomi_password": "********"})
        assert updated.status_code == 200
        assert updated.json()["mina_device_id"] == "device-2"
    assert "secret" in (config_dir / "config.yaml").read_text(encoding="utf-8")


def test_tracks_query_does_not_match_media_url_parts(tmp_path: Path) -> None:
    (tmp_path / "稻香.mp3").touch()
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)

    with TestClient(
        create_app(settings=settings, service=MusicService(tmp_path, public_base_url))
    ) as client:
        for query in ("8123", "http", "media", "by-id", "speaker-host", "music"):
            assert client.get("/api/tracks", params={"q": query}).json()["tracks"] == []
        assert client.post("/api/voice", json={"text": "播放 8123"}).status_code == 404


def test_media_supports_head_requests(tmp_path: Path) -> None:
    payload = b"0123456789"
    (tmp_path / "track.mp3").write_bytes(payload)
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)

    with TestClient(
        create_app(settings=settings, service=MusicService(tmp_path, public_base_url))
    ) as client:
        track_id = client.get("/api/tracks").json()["tracks"][0]["id"]
        response = client.head(f"/media/by-id/{track_id}")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == str(len(payload))
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["accept-ranges"] == "bytes"


@pytest.mark.parametrize(
    ("suffix", "media_type"),
    [
        (".mp3", "audio/mpeg"),
        (".flac", "audio/flac"),
        (".m4a", "audio/mp4"),
        (".wav", "audio/wav"),
    ],
)
def test_media_full_response_has_canonical_content_type(
    tmp_path: Path, suffix: str, media_type: str
) -> None:
    payload = b"0123456789"
    (tmp_path / f"track{suffix}").write_bytes(payload)
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)

    with TestClient(
        create_app(settings=settings, service=MusicService(tmp_path, public_base_url))
    ) as client:
        track = client.get("/api/tracks").json()["tracks"][0]
        response = client.get(f"/media/by-id/{track['id']}")

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == media_type
    assert response.headers["content-length"] == str(len(payload))
    assert response.headers["accept-ranges"] == "bytes"
    assert "etag" in response.headers
    assert "last-modified" in response.headers


def test_media_supports_byte_ranges_and_rejects_unsatisfiable_range(tmp_path: Path) -> None:
    payload = b"0123456789"
    (tmp_path / "track.mp3").write_bytes(payload)
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)

    with TestClient(
        create_app(settings=settings, service=MusicService(tmp_path, public_base_url))
    ) as client:
        track_id = client.get("/api/tracks").json()["tracks"][0]["id"]
        media_url = f"/media/by-id/{track_id}"

        for range_header, expected_body, expected_range in (
            ("bytes=2-5", b"2345", "bytes 2-5/10"),
            ("bytes=7-", b"789", "bytes 7-9/10"),
            ("bytes=-3", b"789", "bytes 7-9/10"),
        ):
            response = client.get(media_url, headers={"Range": range_header})
            assert response.status_code == 206
            assert response.content == expected_body
            assert response.headers["content-range"] == expected_range
            assert response.headers["content-length"] == str(len(expected_body))
            assert response.headers["content-type"] == "audio/mpeg"
            assert response.headers["accept-ranges"] == "bytes"

        response = client.get(media_url, headers={"Range": "bytes=99-100"})
        assert response.status_code == 416
        assert response.headers["content-range"] == "bytes */10"


def test_media_returns_404_for_unknown_or_removed_track(tmp_path: Path) -> None:
    track_path = tmp_path / "track.mp3"
    track_path.write_bytes(b"music")
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=tmp_path)

    with TestClient(
        create_app(settings=settings, service=MusicService(tmp_path, public_base_url))
    ) as client:
        track_id = client.get("/api/tracks").json()["tracks"][0]["id"]
        assert client.get("/media/by-id/not-a-track").status_code == 404
        track_path.unlink()
        assert client.get(f"/media/by-id/{track_id}").status_code == 404


def test_scan_excludes_symlink_outside_music_root(tmp_path: Path) -> None:
    music_root = tmp_path / "music"
    music_root.mkdir()
    outside_track = tmp_path / "outside.mp3"
    outside_track.write_bytes(b"outside")
    (music_root / "linked.mp3").symlink_to(outside_track)
    public_base_url = "http://speaker-host:8123"
    settings = Settings(public_base_url=public_base_url, music_dir=music_root)

    with TestClient(
        create_app(settings=settings, service=MusicService(music_root, public_base_url))
    ) as client:
        assert client.get("/api/tracks").json()["tracks"] == []


def test_config_update_flags_restart_required_and_rebuilds_client(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    config_dir = tmp_path / "config"
    settings = Settings(
        config_dir=config_dir,
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
        xiaomi_user="user",
        xiaomi_password="secret",
    )
    app = create_app(settings=settings, service=MusicService(tmp_path, settings.public_base_url))
    with TestClient(app) as client:
        runtime_only = client.put("/api/config", json={"mina_device_id": "device-9"})
        assert runtime_only.status_code == 200
        assert runtime_only.json()["restart_required"] is False

        switched = client.put("/api/config", json={"mina_mode": "miservice"})
        assert switched.status_code == 200
        assert switched.json()["restart_required"] is False
        assert isinstance(app.state.mina_client, MinaMiserviceClient)
        assert app.state.service.mina_client is app.state.mina_client

        client.put("/api/config", json={"mina_mode": "mock"})
        assert isinstance(app.state.mina_client, MockMinaClient)
        assert app.state.mina_client.device_id == "device-9"

        same_value = client.put("/api/config", json={"music_root": str(tmp_path)})
        assert same_value.json()["restart_required"] is False
        moved = client.put("/api/config", json={"music_root": str(tmp_path / "elsewhere")})
        assert moved.json()["restart_required"] is True
