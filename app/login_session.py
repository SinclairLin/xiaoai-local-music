"""Background Xiaomi login sessions with interactive OTP for the web console.

The manager runs a single login attempt on a daemon thread (same
``asyncio.run`` bridging as ``MinaMiserviceClient._run``). It deliberately
shares nothing with the client except the token file: once the login
coroutine persists ``{config_dir}/.mi.token``, every later client call reuses
it via ``MiTokenStore``. While a session is mid-login, a concurrent client
failure may delete the token file; callers accept that residual race instead
of holding the client lock for the whole OTP wait.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from enum import Enum
from pathlib import Path
from typing import Any, AsyncContextManager, Awaitable, Callable

from miservice import MiAccount, MiNAService, MiTokenStore

from .mina_client import MinaDevice, parse_device_list

OTP_TIMEOUT_SEC = 300.0
# aiohttp 单请求默认 total=300s，整体上限需覆盖 OTP 等待加网络余量。
TOTAL_TIMEOUT_SEC = 480.0

OtpCallback = Callable[[str], Awaitable[str]]
AccountFactory = Callable[[str, str, Path, OtpCallback], AsyncContextManager[Any]]


class LoginState(str, Enum):
    IDLE = "idle"
    PENDING = "pending"
    OTP_REQUIRED = "otp_required"
    VERIFYING = "verifying"
    SUCCESS = "success"
    FAILED = "failed"


_ACTIVE_STATES = frozenset({LoginState.PENDING, LoginState.OTP_REQUIRED, LoginState.VERIFYING})


class LoginSessionManager:
    """Single-slot login session; terminal states persist until the next start."""

    def __init__(
        self,
        *,
        otp_timeout_sec: float = OTP_TIMEOUT_SEC,
        total_timeout_sec: float = TOTAL_TIMEOUT_SEC,
        account_factory: AccountFactory | None = None,
    ) -> None:
        self.otp_timeout_sec = otp_timeout_sec
        self.total_timeout_sec = total_timeout_sec
        self._account_factory = account_factory or self._default_account
        self._lock = threading.Lock()
        self._generation = 0
        self._state = LoginState.IDLE
        self._otp_method: str | None = None
        self._error: str | None = None
        self._devices: list[dict[str, str]] | None = None
        self._started_at: float | None = None
        self._otp_event = threading.Event()
        self._otp_holder: dict[str, str | None] = {"code": None}

    @asynccontextmanager
    async def _default_account(self, username: str, password: str, token_path: Path, otp_callback: OtpCallback):
        try:
            from aiohttp import ClientSession
        except ImportError:
            from miservice.biohttp import ClientSession
        async with ClientSession() as session:
            yield MiAccount(
                session,
                username,
                password,
                token_store=MiTokenStore(str(token_path)),
                otp_callback=otp_callback,
            )

    def is_active(self) -> bool:
        with self._lock:
            return self._state in _ACTIVE_STATES

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self._state.value,
                "otp_method": self._otp_method,
                "error": self._error,
                "devices": self._devices,
                "started_at": self._started_at,
            }

    def start(self, username: str, password: str, token_path: str | Path) -> bool:
        with self._lock:
            if self._state in _ACTIVE_STATES:
                return False
            self._generation += 1
            generation = self._generation
            self._state = LoginState.PENDING
            self._otp_method = None
            self._error = None
            self._devices = None
            self._started_at = time.time()
            # 事件与验证码槽按会话独立，防止被取消的旧回调消费新会话的输入。
            event = threading.Event()
            holder: dict[str, str | None] = {"code": None}
            self._otp_event = event
            self._otp_holder = holder
            thread = threading.Thread(
                target=self._run_login,
                args=(generation, username, password, Path(token_path), event, holder),
                daemon=True,
                name="mi-login",
            )
        thread.start()
        return True

    def start_mock(self, devices: list[MinaDevice]) -> bool:
        with self._lock:
            if self._state in _ACTIVE_STATES:
                return False
            self._generation += 1
            self._state = LoginState.SUCCESS
            self._otp_method = None
            self._error = None
            self._devices = [{"id": item.id, "name": item.name} for item in devices]
            self._started_at = time.time()
        return True

    def submit_otp(self, code: str) -> bool:
        with self._lock:
            if self._state is not LoginState.OTP_REQUIRED:
                return False
            self._state = LoginState.VERIFYING
            self._otp_holder["code"] = code
            self._otp_event.set()
        return True

    def cancel(self) -> None:
        with self._lock:
            if self._state not in _ACTIVE_STATES:
                return
            # 自增 generation 使后台线程的后续写入全部失效；线程本身通过
            # OTP 回调的空验证码或整体超时自行退出。
            self._generation += 1
            self._state = LoginState.FAILED
            self._error = "登录已取消"
            self._otp_holder["code"] = None
            self._otp_event.set()

    def _run_login(self, generation: int, username: str, password: str, token_path: Path, event: threading.Event, holder: dict[str, str | None]) -> None:
        try:
            asyncio.run(
                asyncio.wait_for(
                    self._login_coro(generation, username, password, token_path, event, holder),
                    self.total_timeout_sec,
                )
            )
        except Exception as exc:
            self._finish(generation, LoginState.FAILED, error=str(exc) or type(exc).__name__)

    async def _login_coro(self, generation: int, username: str, password: str, token_path: Path, event: threading.Event, holder: dict[str, str | None]) -> None:
        otp_callback = self._make_otp_callback(generation, event, holder)
        async with self._account_factory(username, password, token_path, otp_callback) as account:
            if not await account.login("micoapi"):
                error = getattr(account, "_login_error", None) or "登录失败"
                self._finish(generation, LoginState.FAILED, error=str(error))
                return
            try:
                raw_devices = await MiNAService(account).device_list()
                devices = [{"id": item.id, "name": item.name} for item in parse_device_list(raw_devices)]
            except Exception:
                # token 已写入，登录本身成功；设备列表可稍后经 /api/devices 重取。
                devices = []
            self._finish(generation, LoginState.SUCCESS, devices=devices)

    def _make_otp_callback(self, generation: int, event: threading.Event, holder: dict[str, str | None]) -> OtpCallback:
        async def callback(otp_method: str) -> str:
            self._set_state(generation, LoginState.OTP_REQUIRED, otp_method=otp_method)
            loop = asyncio.get_running_loop()
            got = await loop.run_in_executor(None, event.wait, self.otp_timeout_sec)
            code = holder["code"]
            if not got or not code:
                raise TimeoutError("验证码输入超时或登录已取消")
            return code

        return callback

    def _set_state(self, generation: int, state: LoginState, *, otp_method: str | None = None) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._state = state
            if otp_method is not None:
                self._otp_method = otp_method

    def _finish(self, generation: int, state: LoginState, *, error: str | None = None, devices: list[dict[str, str]] | None = None) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._state = state
            self._error = error
            self._devices = devices
