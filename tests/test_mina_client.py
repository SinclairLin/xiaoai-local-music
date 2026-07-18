import os
from pathlib import Path

from app.mina_client import MinaAuthError, MinaDevice, MinaHttpClient


class Response:
    def __init__(self, status_code: int, payload: object, cookies: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.cookies = cookies or {}

    def json(self) -> object:
        return self._payload


class Transport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.responses = [Response(200, {"token": "secret-token"}, {"sid": "cookie-value"})]

    def request(self, method: str, url: str, **kwargs: object) -> Response:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)


def test_login_persists_token_and_cookies_with_restricted_permissions(tmp_path: Path) -> None:
    transport = Transport()
    client = MinaHttpClient("https://mina.example", "user", "password", tmp_path, transport=transport)

    assert client.login() == "secret-token"
    assert (tmp_path / ".mi.token").read_text(encoding="utf-8").strip() == "secret-token"
    assert (tmp_path / ".mina.cookies").read_text(encoding="utf-8").strip() == '{"sid": "cookie-value"}'
    if os.name == "posix":
        assert (tmp_path / ".mi.token").stat().st_mode & 0o777 == 0o600
        assert (tmp_path / ".mina.cookies").stat().st_mode & 0o777 == 0o600


def test_existing_token_is_reused_without_login(tmp_path: Path) -> None:
    (tmp_path / ".mi.token").write_text("cached-token\n", encoding="utf-8")
    client = MinaHttpClient("https://mina.example", "user", "password", tmp_path, transport=Transport())

    assert client.login() == "cached-token"


def test_missing_endpoint_has_clear_auth_error(tmp_path: Path) -> None:
    client = MinaHttpClient("", "user", "password", tmp_path, transport=Transport())

    try:
        client.login()
    except MinaAuthError as exc:
        assert "endpoint" in str(exc)
    else:
        raise AssertionError("expected MinaAuthError")


def test_device_and_playback_methods_use_minimal_rest_contract(tmp_path: Path) -> None:
    transport = Transport()
    transport.responses.extend(
        [
            Response(200, {"devices": [{"id": "d1", "name": "Kitchen"}]}),
            Response(200, {"ok": True}),
        ]
    )
    client = MinaHttpClient("https://mina.example", "user", "password", tmp_path, transport=transport)

    assert client.list_devices() == [MinaDevice(id="d1", name="Kitchen")]
    assert client.play_by_url("http://music/track.mp3", "d1") == {"ok": True}
    assert transport.calls[1][1].endswith("/devices")
    assert transport.calls[2][2]["json"] == {"device_id": "d1", "url": "http://music/track.mp3"}
