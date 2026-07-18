"""Small, injectable Mina client with safe local credential persistence."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx


class MinaClientError(RuntimeError):
    """Base class for safe, user-facing Mina errors."""


class MinaAuthError(MinaClientError):
    """Mina rejected authentication or credentials are missing."""


class MinaUpstreamError(MinaClientError):
    """Mina returned an invalid or unsuccessful upstream response."""


class MinaDeviceError(MinaClientError):
    """No usable device was selected."""


@dataclass(frozen=True)
class MinaDevice:
    id: str
    name: str


class MinaTransport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class MinaClient(Protocol):
    def login(self) -> str: ...
    def list_devices(self) -> list[MinaDevice]: ...
    def text_to_speech(self, text: str, device_id: str) -> Any: ...
    def play_by_url(self, url: str, device_id: str) -> Any: ...
    def pause(self, device_id: str) -> Any: ...
    def stop(self, device_id: str) -> Any: ...
    def play(self, device_id: str) -> Any: ...
    def set_volume(self, volume: int, device_id: str) -> Any: ...


def _atomic_secret_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(value)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise MinaClientError(f"cannot persist Mina secret {path.name}: {exc}") from exc


class MinaHttpClient:
    def __init__(self, base_url: str, username: str | None, password: str | None, config_dir: str | Path, transport: MinaTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.config_dir = Path(config_dir)
        self.token_path = self.config_dir / ".mi.token"
        self.cookies_path = self.config_dir / ".mina.cookies"
        self.transport = transport or httpx.Client(timeout=15.0)
        self.token: str | None = self._read_token()
        self.cookies: dict[str, str] = self._read_cookies()

    def _read_token(self) -> str | None:
        try:
            value = self.token_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise MinaClientError(f"cannot read Mina token: {exc}") from exc
        return value or None

    def _read_cookies(self) -> dict[str, str]:
        try:
            value = json.loads(self.cookies_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise MinaClientError(f"cannot read Mina cookies: {exc}") from exc
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise MinaClientError("Mina cookies file must contain a JSON object")
        return value

    def _persist_cookies(self) -> None:
        _atomic_secret_write(self.cookies_path, json.dumps(self.cookies, ensure_ascii=False, sort_keys=True) + "\n")

    def login(self) -> str:
        if self.token:
            return self.token
        if not self.base_url:
            raise MinaAuthError("Mina API endpoint is not configured")
        if not self.username or not self.password:
            raise MinaAuthError("Mina username and password are required")
        try:
            response = self.transport.request("POST", f"{self.base_url}/login", json={"username": self.username, "password": self.password, "cookies": self.cookies})
        except Exception as exc:
            raise MinaUpstreamError(f"Mina login request failed: {exc}") from exc
        if getattr(response, "status_code", 0) >= 400:
            raise MinaAuthError(f"Mina login failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except Exception as exc:
            raise MinaUpstreamError("Mina login returned invalid JSON") from exc
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise MinaAuthError("Mina login response did not contain a token")
        self.token = token
        _atomic_secret_write(self.token_path, token + "\n")
        response_cookies = getattr(response, "cookies", None)
        if response_cookies is not None:
            try:
                self.cookies.update({str(key): str(value) for key, value in response_cookies.items()})
            except Exception:
                pass
            if self.cookies:
                self._persist_cookies()
        return token

    def update_credentials(self, username: str | None, password: str | None) -> None:
        if username != self.username or password != self.password:
            self.token = None
            self.token_path.unlink(missing_ok=True)
        self.username = username
        self.password = password

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        token = self.login()
        try:
            response = self.transport.request(method, f"{self.base_url}{path}", json=payload, headers={"Authorization": f"Bearer {token}"}, cookies=self.cookies)
        except Exception as exc:
            raise MinaUpstreamError(f"Mina request failed: {exc}") from exc
        if getattr(response, "status_code", 0) in {401, 403}:
            self.token = None
            self.token_path.unlink(missing_ok=True)
            token = self.login()
            try:
                response = self.transport.request(method, f"{self.base_url}{path}", json=payload, headers={"Authorization": f"Bearer {token}"}, cookies=self.cookies)
            except Exception as exc:
                raise MinaUpstreamError(f"Mina retry failed: {exc}") from exc
        if getattr(response, "status_code", 0) >= 400:
            raise MinaUpstreamError(f"Mina request {path} failed with HTTP {response.status_code}")
        try:
            return response.json()
        except Exception:
            return {"ok": True}

    def list_devices(self) -> list[MinaDevice]:
        payload = self._request("GET", "/devices")
        devices = payload.get("devices") if isinstance(payload, dict) else payload
        if not isinstance(devices, list):
            raise MinaUpstreamError("Mina devices response was invalid")
        result: list[MinaDevice] = []
        for device in devices:
            if not isinstance(device, dict):
                continue
            device_id = device.get("id", device.get("device_id"))
            name = device.get("name", device_id)
            if isinstance(device_id, str) and isinstance(name, str):
                result.append(MinaDevice(id=device_id, name=name))
        return result

    def text_to_speech(self, text: str, device_id: str) -> Any:
        return self._request("POST", "/tts", {"device_id": device_id, "text": text})

    def play_by_url(self, url: str, device_id: str) -> Any:
        return self._request("POST", "/play_by_url", {"device_id": device_id, "url": url})

    def pause(self, device_id: str) -> Any:
        return self._request("POST", "/pause", {"device_id": device_id})

    def stop(self, device_id: str) -> Any:
        return self._request("POST", "/stop", {"device_id": device_id})

    def play(self, device_id: str) -> Any:
        return self._request("POST", "/play", {"device_id": device_id})

    def set_volume(self, volume: int, device_id: str) -> Any:
        return self._request("POST", "/volume", {"device_id": device_id, "volume": volume})


class MockMinaClient:
    def __init__(self, device_id: str | None = None) -> None:
        self.device_id = device_id or "mock-device"
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def login(self) -> str:
        self.calls.append(("login", ()))
        return "mock-token"

    def list_devices(self) -> list[MinaDevice]:
        self.calls.append(("list_devices", ()))
        return [MinaDevice(id=self.device_id, name="Mock Mina")]

    def text_to_speech(self, text: str, device_id: str) -> Any:
        self.calls.append(("text_to_speech", (text, device_id)))
        return {"ok": True}

    def play_by_url(self, url: str, device_id: str) -> Any:
        self.calls.append(("play_by_url", (url, device_id)))
        return {"ok": True}

    def pause(self, device_id: str) -> Any:
        self.calls.append(("pause", (device_id,)))
        return {"ok": True}

    def stop(self, device_id: str) -> Any:
        self.calls.append(("stop", (device_id,)))
        return {"ok": True}

    def play(self, device_id: str) -> Any:
        self.calls.append(("play", (device_id,)))
        return {"ok": True}

    def set_volume(self, volume: int, device_id: str) -> Any:
        self.calls.append(("set_volume", (volume, device_id)))
        return {"ok": True}
