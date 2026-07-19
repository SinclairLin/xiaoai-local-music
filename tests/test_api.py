from pathlib import Path
from contextlib import asynccontextmanager
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.login_session import LoginSessionManager
from app.main import create_app
from app.mina_client import MinaDevice, MockMinaClient
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


def test_config_update_flags_restart_required_and_preserves_injected_client(tmp_path: Path) -> None:
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

        assert isinstance(app.state.mina_client, MockMinaClient)
        assert app.state.mina_client.device_id == "device-9"

        same_value = client.put("/api/config", json={"music_root": str(tmp_path)})
        assert same_value.json()["restart_required"] is False
        moved = client.put("/api/config", json={"music_root": str(tmp_path / "elsewhere")})
        assert moved.json()["restart_required"] is True


class _OtpAccount:
    """Fake MiAccount：走一轮 OTP 回调后登录成功。"""

    def __init__(self, otp_callback) -> None:
        self.otp_callback = otp_callback
        self._login_error: str | None = None
        self.received_code: str | None = None

    async def login(self, sid: str) -> bool:
        try:
            self.received_code = await self.otp_callback("Phone")
        except Exception as exc:
            self._login_error = str(exc)
            return False
        return True

    async def mi_request(self, sid: str, url: str, data: Any, headers: dict) -> dict:
        return {"data": [{"deviceID": "d1", "alias": "客厅音箱"}]}


def _fake_otp_manager() -> tuple[LoginSessionManager, list[_OtpAccount]]:
    created: list[_OtpAccount] = []

    @asynccontextmanager
    async def factory(username, password, token_path, otp_callback):
        account = _OtpAccount(otp_callback)
        created.append(account)
        yield account

    return LoginSessionManager(account_factory=factory, otp_timeout_sec=2.0, total_timeout_sec=5.0), created


def _poll_login(client: TestClient, *states: str, attempts: int = 100) -> dict:
    for _ in range(attempts):
        status = client.get("/api/login/status").json()
        if status["status"] in states:
            return status
        time.sleep(0.02)
    pytest.fail(f"login status never reached {states}: {status}")


def test_login_without_credentials_returns_422(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    settings = Settings(public_base_url="http://speaker-host:8123", music_dir=tmp_path, mina_device_id="mock-device")

    with TestClient(create_app(settings=settings, service=MusicService(tmp_path, settings.public_base_url, device_id="mock-device"))) as client:
        assert client.get("/api/login/status").json()["status"] == "idle"
        assert client.post("/api/login/otp", json={"code": "123456"}).status_code == 409
        response = client.post("/api/login")
        assert response.status_code == 422
        assert "账号密码" in response.json()["detail"]


def test_devices_without_authentication_do_not_return_mock_device(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    settings = Settings(config_dir=tmp_path / "config", public_base_url="http://speaker-host:8123", music_dir=tmp_path)

    with TestClient(create_app(settings=settings)) as client:
        response = client.get("/api/devices")
        assert response.status_code == 502
        assert "凭据" in response.json()["detail"]


def test_devices_auto_select_persists_first_device_when_none_selected(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    config_dir = tmp_path / "config"
    settings = Settings(config_dir=config_dir, public_base_url="http://speaker-host:8123", music_dir=tmp_path)
    service = MusicService(tmp_path, settings.public_base_url, mina_client=MockMinaClient("real-1"), device_id=None)

    app = create_app(settings=settings, service=service)
    with TestClient(app) as client:
        response = client.get("/api/devices")
        assert response.status_code == 200
        assert response.json()["selected_device_id"] == "real-1"
        assert app.state.service.device_id == "real-1"
        assert app.state.settings.mina_device_id == "real-1"
        assert app.state.voice_worker.device_id == "real-1"
        assert "real-1" in (config_dir / "config.yaml").read_text(encoding="utf-8")


def test_devices_keep_existing_selection_even_if_absent_from_list(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    config_dir = tmp_path / "config"
    settings = Settings(
        config_dir=config_dir,
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
        mina_device_id="stale-device",
    )
    service = MusicService(tmp_path, settings.public_base_url, mina_client=MockMinaClient("real-1"), device_id="stale-device")

    app = create_app(settings=settings, service=service)
    with TestClient(app) as client:
        response = client.get("/api/devices")
        assert response.status_code == 200
        assert response.json()["selected_device_id"] == "stale-device"
        assert app.state.service.device_id == "stale-device"
        assert not (config_dir / "config.yaml").exists()


def test_devices_auto_select_survives_read_only_config(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.chmod(0o500)
    settings = Settings(config_dir=config_dir, public_base_url="http://speaker-host:8123", music_dir=tmp_path)
    service = MusicService(tmp_path, settings.public_base_url, mina_client=MockMinaClient("real-1"), device_id=None)

    try:
        app = create_app(settings=settings, service=service)
        with TestClient(app) as client:
            response = client.get("/api/devices")
            assert response.status_code == 200
            assert response.json()["selected_device_id"] == "real-1"
            assert app.state.service.device_id == "real-1"
            assert not (config_dir / "config.yaml").exists()
    finally:
        config_dir.chmod(0o700)


def test_login_with_saved_credentials_does_not_use_mock_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "track.mp3").touch()
    settings = Settings(
        config_dir=tmp_path / "config",
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
        xiaomi_user="user",
        xiaomi_password="secret",
    )

    class FakeRealClient:
        def __init__(self, username, password, config_dir):
            self.token_path = Path(config_dir) / ".mi.token"

    class FakeLoginManager:
        def __init__(self):
            self.started = None

        def start(self, username, password, token_path):
            self.started = (username, password, Path(token_path))
            return True

        def status(self):
            return {"status": "pending", "devices": None}

    monkeypatch.setattr("app.routes.MinaMiserviceClient", FakeRealClient)
    app = create_app(settings=settings)
    manager = FakeLoginManager()
    app.state.login_manager = manager

    with TestClient(app) as client:
        response = client.post("/api/login")
        assert response.status_code == 200
        assert manager.started == ("user", "secret", tmp_path / "config" / ".mi.token")
        assert not isinstance(app.state.service.mina_client, MockMinaClient)


def test_cookie_login_writes_token_and_switches_from_mock_to_real_devices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    (tmp_path / "track.mp3").touch()
    config_dir = tmp_path / "config"
    settings = Settings(
        config_dir=config_dir,
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
    )

    class FakeRealClient:
        def __init__(self, username, password, config_dir):
            self.token_path = Path(config_dir) / ".mi.token"

        def list_devices(self):
            return [MinaDevice(id="real-device", name="客厅音箱")]

    monkeypatch.setattr("app.routes.MinaMiserviceClient", FakeRealClient)

    with TestClient(create_app(settings=settings)) as client:
        response = client.post("/api/login/cookies", json={"cookies": "userId=123; serviceToken=tok; ssecurity=sec"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["devices"] == [{"id": "real-device", "name": "客厅音箱"}]
        assert body["devices"] != [{"id": "mock-device", "name": "Mock Mina"}]
        # 选择同步集中在 GET /api/devices（前端登录成功后立即拉取）。
        assert client.get("/api/devices").json()["selected_device_id"] == "real-device"
        assert "real-device" in (config_dir / "config.yaml").read_text(encoding="utf-8")
        token = json.loads((config_dir / ".mi.token").read_text())
        assert token["userId"] == 123
        assert token["micoapi"] == ["sec", "tok"]
        assert token["_auth_source"] == "cookies"

        missing = client.post("/api/login/cookies", json={"cookies": "userId=123"})
        assert missing.status_code == 422
        assert "serviceToken" in missing.json()["detail"]

        explicit = client.post("/api/login/cookies", json={"user_id": "9", "service_token": "tok2"})
        assert explicit.status_code == 200
        assert json.loads((config_dir / ".mi.token").read_text())["micoapi"] == ["", "tok2"]


def test_cookie_login_rolls_back_token_on_invalid_credentials(tmp_path: Path) -> None:
    from app.mina_client import MinaUpstreamError

    (tmp_path / "track.mp3").touch()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    token_path = config_dir / ".mi.token"
    token_path.write_text('{"userId": 1, "micoapi": ["", "old"]}')
    settings = Settings(
        config_dir=config_dir,
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
        mina_device_id="mock-device",
    )

    class FailingClient(MockMinaClient):
        def __init__(self) -> None:
            super().__init__("mock-device")
            self.token_path = token_path

        def list_devices(self):
            raise MinaUpstreamError("Mina request failed: 401")

    app = create_app(settings=settings, service=MusicService(tmp_path, settings.public_base_url, device_id="mock-device"))
    app.state.service.mina_client = FailingClient()
    with TestClient(app) as client:
        response = client.post("/api/login/cookies", json={"cookies": "userId=2; serviceToken=bad"})
        assert response.status_code == 401
        assert "无效或已过期" in response.json()["detail"]
        assert "重新获取并粘贴" in response.json()["detail"]
        # 旧 token 被回滚保留
        assert '"old"' in token_path.read_text()


def test_login_without_credentials_in_real_flow_returns_422(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    settings = Settings(
        config_dir=tmp_path / "config",
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
    )

    with TestClient(create_app(settings=settings)) as client:
        response = client.post("/api/login")
        assert response.status_code == 422
        assert "账号密码" in response.json()["detail"]


def test_login_otp_http_flow(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    settings = Settings(
        config_dir=tmp_path / "config",
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
        xiaomi_user="user",
        xiaomi_password="secret",
    )
    app = create_app(settings=settings)
    manager, created = _fake_otp_manager()
    app.state.login_manager = manager

    with TestClient(app) as client:
        assert client.post("/api/login").status_code == 200
        status = _poll_login(client, "otp_required")
        assert status["otp_method"] == "Phone"
        assert client.post("/api/login").status_code == 409
        # 纯空白验证码在路由层被拒绝，不消耗等待中的会话
        assert client.post("/api/login/otp", json={"code": "   "}).status_code == 422
        assert client.get("/api/login/status").json()["status"] == "otp_required"
        assert client.post("/api/login/otp", json={"code": "654321"}).status_code == 200
        status = _poll_login(client, "success")
        assert status["devices"] == [{"id": "d1", "name": "客厅音箱"}]
        assert created[0].received_code == "654321"


def test_config_update_cancels_active_login_session(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    settings = Settings(
        config_dir=tmp_path / "config",
        public_base_url="http://speaker-host:8123",
        music_dir=tmp_path,
        xiaomi_user="user",
        xiaomi_password="secret",
    )
    app = create_app(settings=settings)
    manager, _ = _fake_otp_manager()
    app.state.login_manager = manager

    with TestClient(app) as client:
        client.post("/api/login")
        _poll_login(client, "otp_required")
        assert client.put("/api/config", json={"xiaomi_password": "changed"}).status_code == 200
        status = client.get("/api/login/status").json()
        assert status["status"] == "failed"
        assert "取消" in status["error"]


def test_token_clear_endpoint(tmp_path: Path) -> None:
    (tmp_path / "track.mp3").touch()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".mi.token").write_text("{}", encoding="utf-8")
    settings = Settings(config_dir=config_dir, public_base_url="http://speaker-host:8123", music_dir=tmp_path)

    with TestClient(create_app(settings=settings, service=MusicService(tmp_path, settings.public_base_url))) as client:
        assert client.post("/api/token/clear").json() == {"cleared": True}
        assert not (config_dir / ".mi.token").exists()
        assert client.post("/api/token/clear").json() == {"cleared": False}
