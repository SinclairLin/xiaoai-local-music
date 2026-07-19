import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from app.mina_client import (
    MinaAuthError,
    MinaDevice,
    MinaMiserviceClient,
    MinaUpstreamError,
    _otp_unavailable,
)


class FakeMiNAService:
    def __init__(self, devices: object = None, error: Exception | None = None) -> None:
        self.devices = devices
        self.error = error
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def _record(self, name: str, *args: Any) -> Any:
        self.calls.append((name, args))
        if self.error is not None:
            raise self.error
        return True

    async def device_list(self, master: int = 0) -> Any:
        self.calls.append(("device_list", (master,)))
        if self.error is not None:
            raise self.error
        return self.devices

    async def text_to_speech(self, deviceId: str, text: str) -> Any:
        return await self._record("text_to_speech", deviceId, text)

    async def play_by_url(self, deviceId: str, url: str, _type: int = 2) -> Any:
        return await self._record("play_by_url", deviceId, url)

    async def player_pause(self, deviceId: str) -> Any:
        return await self._record("player_pause", deviceId)

    async def player_stop(self, deviceId: str) -> Any:
        return await self._record("player_stop", deviceId)

    async def player_play(self, deviceId: str) -> Any:
        return await self._record("player_play", deviceId)

    async def player_set_volume(self, deviceId: str, volume: int) -> Any:
        return await self._record("player_set_volume", deviceId, volume)

    async def get_latest_ask(self, deviceId: str) -> Any:
        self.calls.append(("get_latest_ask", (deviceId,)))
        return [{
            "request_id": "r1",
            "timestamp_ms": 123,
            "response": {"answer": [{"question": "播放稻香"}]},
        }]


def make_client(tmp_path: Path, service: FakeMiNAService, username: str | None = "user", password: str | None = "password") -> MinaMiserviceClient:
    @asynccontextmanager
    async def factory():
        yield service

    return MinaMiserviceClient(username, password, tmp_path, service_factory=factory)


def test_device_list_mapping_prefers_alias_and_skips_invalid(tmp_path: Path) -> None:
    service = FakeMiNAService(
        devices=[
            {"deviceID": "d1", "alias": "客厅音箱", "name": "Speaker"},
            {"deviceID": "d2", "name": "卧室音箱"},
            {"miotDID": 123},
            {"alias": "no-id"},
            "not-a-dict",
        ]
    )
    client = make_client(tmp_path, service)

    assert client.list_devices() == [
        MinaDevice(id="d1", name="客厅音箱"),
        MinaDevice(id="d2", name="卧室音箱"),
        MinaDevice(id="123", name="123"),
    ]


def test_device_list_none_becomes_empty(tmp_path: Path) -> None:
    client = make_client(tmp_path, FakeMiNAService(devices=None))

    assert client.list_devices() == []


def test_playback_methods_flip_argument_order(tmp_path: Path) -> None:
    service = FakeMiNAService()
    client = make_client(tmp_path, service)

    client.play_by_url("http://music/track.mp3", "d1")
    client.text_to_speech("你好", "d1")
    client.pause("d1")
    client.stop("d1")
    client.play("d1")
    client.set_volume(30, "d1")

    assert service.calls == [
        ("play_by_url", ("d1", "http://music/track.mp3")),
        ("text_to_speech", ("d1", "你好")),
        ("player_pause", ("d1",)),
        ("player_stop", ("d1",)),
        ("player_play", ("d1",)),
        ("player_set_volume", ("d1", 30)),
    ]


def test_otp_callback_raises_auth_error_with_host_login_hint() -> None:
    with pytest.raises(MinaAuthError, match="python -m miservice"):
        asyncio.run(_otp_unavailable("sms"))


def test_generic_exception_is_wrapped_as_upstream_error(tmp_path: Path) -> None:
    client = make_client(tmp_path, FakeMiNAService(error=RuntimeError("boom")))

    with pytest.raises(MinaUpstreamError, match="boom"):
        client.list_devices()


def test_mina_client_error_passes_through_unwrapped(tmp_path: Path) -> None:
    client = make_client(tmp_path, FakeMiNAService(error=MinaAuthError("需要 OTP")))

    with pytest.raises(MinaAuthError, match="需要 OTP"):
        client.list_devices()


def test_update_credentials_removes_token_file_on_change(tmp_path: Path) -> None:
    token_path = tmp_path / ".mi.token"
    token_path.write_text("{}", encoding="utf-8")
    client = make_client(tmp_path, FakeMiNAService())

    client.update_credentials("user", "password")
    assert token_path.exists()

    client.update_credentials("user", "new-password")
    assert not token_path.exists()
    assert client.username == "user"
    assert client.password == "new-password"


def test_login_without_credentials_raises_auth_error(tmp_path: Path) -> None:
    client = make_client(tmp_path, FakeMiNAService(devices=[]), username=None, password=None)

    with pytest.raises(MinaAuthError, match="username and password"):
        client.login()


def test_login_probes_devices_and_returns_authenticated(tmp_path: Path) -> None:
    service = FakeMiNAService(devices=[{"deviceID": "d1", "name": "Speaker"}])
    client = make_client(tmp_path, service)

    assert client.login() == "authenticated"
    assert service.calls == [("device_list", (0,))]


def test_run_without_credentials_or_token_raises_auth_error_before_network(tmp_path: Path) -> None:
    entered = False

    @asynccontextmanager
    async def factory():
        nonlocal entered
        entered = True
        yield FakeMiNAService()

    client = MinaMiserviceClient(None, None, tmp_path, service_factory=factory)

    with pytest.raises(MinaAuthError, match="token"):
        client.list_devices()
    assert entered is False


def test_run_with_token_file_but_no_credentials_proceeds(tmp_path: Path) -> None:
    (tmp_path / ".mi.token").write_text("{}", encoding="utf-8")
    client = make_client(tmp_path, FakeMiNAService(devices=[]), username=None, password=None)

    assert client.list_devices() == []


def test_voice_events_fall_back_to_ubus_shape(tmp_path: Path) -> None:
    service = FakeMiNAService(devices=[])
    client = make_client(tmp_path, service)

    events = client.fetch_voice_events("d1", "LX06", 0)

    assert events == [{"timestamp": 123, "query": "播放稻香", "request_id": "r1", "source": "ubus"}]
    assert ("get_latest_ask", ("d1",)) in service.calls


def test_voice_device_validation_checks_hardware(tmp_path: Path) -> None:
    service = FakeMiNAService(devices=[{"deviceID": "d1", "hardware": "LX06"}])
    client = make_client(tmp_path, service)

    client.validate_voice_device("d1", "LX06")
    with pytest.raises(Exception, match="hardware mismatch"):
        client.validate_voice_device("d1", "LX04")
