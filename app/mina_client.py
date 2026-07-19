"""Injectable Mina clients backed by the miservice library or a local mock."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncContextManager, Awaitable, Callable, Protocol

from miservice import MiAccount, MiNAService, MiTokenStore


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


class MinaClient(Protocol):
    def login(self) -> str: ...
    def list_devices(self) -> list[MinaDevice]: ...
    def text_to_speech(self, text: str, device_id: str) -> Any: ...
    def play_by_url(self, url: str, device_id: str) -> Any: ...
    def pause(self, device_id: str) -> Any: ...
    def stop(self, device_id: str) -> Any: ...
    def play(self, device_id: str) -> Any: ...
    def set_volume(self, volume: int, device_id: str) -> Any: ...


async def _otp_unavailable(otp_method: str) -> str:
    raise MinaAuthError(
        "小米账号需 OTP 验证，服务进程内无法交互；"
        "请在宿主机设置 MI_USER/MI_PASS 后执行 `python -m miservice mina` 完成登录，"
        "再将 ~/.mi.token 复制到 config_dir"
    )


class MinaMiserviceClient:
    """Synchronous Mina client bridging to the async miservice library.

    Each call runs a single coroutine via ``asyncio.run``, so this client must
    be used from a synchronous context without a running event loop (FastAPI
    sync endpoints executed in thread-pool workers qualify). If endpoints ever
    become ``async def``, the bridging strategy must change.
    """

    def __init__(
        self,
        username: str | None,
        password: str | None,
        config_dir: str | Path,
        service_factory: Callable[[], AsyncContextManager[MiNAService]] | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.config_dir = Path(config_dir)
        self.token_path = self.config_dir / ".mi.token"
        self._service_factory = service_factory or self._default_service

    @asynccontextmanager
    async def _default_service(self):
        try:
            from aiohttp import ClientSession
        except ImportError:
            from miservice.biohttp import ClientSession
        async with ClientSession() as session:
            account = MiAccount(
                session,
                self.username,
                self.password,
                token_store=MiTokenStore(str(self.token_path)),
                otp_callback=_otp_unavailable,
            )
            yield MiNAService(account)

    def _run(self, op: Callable[[MiNAService], Awaitable[Any]]) -> Any:
        if not (self.username and self.password) and not self.token_path.is_file():
            raise MinaAuthError(
                "Mina 凭据未配置且缺少 token 文件："
                "请配置 xiaomi_user/xiaomi_password，"
                f"或按 README 在宿主机预登录后将 .mi.token 放入 {self.config_dir}"
            )

        async def runner() -> Any:
            async with self._service_factory() as service:
                return await op(service)

        try:
            return asyncio.run(runner())
        except MinaClientError:
            raise
        except Exception as exc:
            raise MinaUpstreamError(f"Mina request failed: {exc}") from exc

    def login(self) -> str:
        if not self.username or not self.password:
            raise MinaAuthError("Mina username and password are required")
        self.list_devices()
        return "authenticated"

    def update_credentials(self, username: str | None, password: str | None) -> None:
        if username != self.username or password != self.password:
            self.token_path.unlink(missing_ok=True)
        self.username = username
        self.password = password

    def list_devices(self) -> list[MinaDevice]:
        devices = self._run(lambda service: service.device_list())
        if devices is None:
            return []
        result: list[MinaDevice] = []
        for device in devices:
            if not isinstance(device, dict):
                continue
            raw_id = device.get("deviceID") or device.get("miotDID")
            if raw_id is None:
                continue
            device_id = str(raw_id)
            name = device.get("alias") or device.get("name") or device_id
            result.append(MinaDevice(id=device_id, name=str(name)))
        return result

    def text_to_speech(self, text: str, device_id: str) -> Any:
        return self._run(lambda service: service.text_to_speech(device_id, text))

    def play_by_url(self, url: str, device_id: str) -> Any:
        return self._run(lambda service: service.play_by_url(device_id, url))

    def pause(self, device_id: str) -> Any:
        return self._run(lambda service: service.player_pause(device_id))

    def stop(self, device_id: str) -> Any:
        return self._run(lambda service: service.player_stop(device_id))

    def play(self, device_id: str) -> Any:
        return self._run(lambda service: service.player_play(device_id))

    def set_volume(self, volume: int, device_id: str) -> Any:
        return self._run(lambda service: service.player_set_volume(device_id, volume))


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
