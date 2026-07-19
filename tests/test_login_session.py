import asyncio
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from app.login_session import LoginSessionManager
from app.mina_client import MinaDevice


class FakeAccount:
    """Mimics MiAccount.login: OTP goes through the callback, all callback
    exceptions are swallowed into ``_login_error`` + ``False``."""

    def __init__(
        self,
        username: str,
        password: str,
        otp_callback,
        *,
        otp_method: str | None = None,
        login_ok: bool = True,
        login_error: str | None = None,
        fail_after_otp: bool = False,
        devices: Any = None,
        devices_error: Exception | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.otp_callback = otp_callback
        self.otp_method = otp_method
        self.login_ok = login_ok
        self._login_error = login_error
        self.fail_after_otp = fail_after_otp
        self.devices = devices
        self.devices_error = devices_error
        self.received_code: str | None = None

    async def login(self, sid: str) -> bool:
        try:
            if self.otp_method is not None:
                self.received_code = await self.otp_callback(self.otp_method)
                if self.fail_after_otp:
                    self._login_error = "验证码错误"
                    return False
        except Exception as exc:
            self._login_error = str(exc)
            return False
        return self.login_ok

    async def mi_request(self, sid: str, url: str, data: Any, headers: dict) -> dict:
        if self.devices_error is not None:
            raise self.devices_error
        return {"data": self.devices}


def make_manager(account_kwargs: dict | None = None, **manager_kwargs) -> tuple[LoginSessionManager, list[FakeAccount]]:
    created: list[FakeAccount] = []

    @asynccontextmanager
    async def factory(username, password, token_path, otp_callback):
        account = FakeAccount(username, password, otp_callback, **(account_kwargs or {}))
        created.append(account)
        yield account

    manager_kwargs.setdefault("otp_timeout_sec", 2.0)
    manager_kwargs.setdefault("total_timeout_sec", 5.0)
    manager = LoginSessionManager(account_factory=factory, **manager_kwargs)
    return manager, created


def wait_for_state(manager: LoginSessionManager, *states: str, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status()
        if status["status"] in states:
            return status
        time.sleep(0.01)
    pytest.fail(f"timed out waiting for {states}, last status: {manager.status()}")


def test_success_without_otp_lists_devices(tmp_path: Path) -> None:
    manager, created = make_manager({"devices": [{"deviceID": "d1", "alias": "客厅音箱"}]})

    assert manager.start("user", "password", tmp_path / ".mi.token")
    status = wait_for_state(manager, "success")

    assert status["devices"] == [{"id": "d1", "name": "客厅音箱"}]
    assert status["error"] is None
    assert created[0].username == "user"


def test_otp_flow_passes_submitted_code(tmp_path: Path) -> None:
    manager, created = make_manager({"otp_method": "Phone", "devices": []})

    assert manager.start("user", "password", tmp_path / ".mi.token")
    status = wait_for_state(manager, "otp_required")
    assert status["otp_method"] == "Phone"

    assert manager.submit_otp("123456")
    assert manager.status()["status"] in ("verifying", "success")
    wait_for_state(manager, "success")
    assert created[0].received_code == "123456"


def test_wrong_code_fails_with_error(tmp_path: Path) -> None:
    manager, _ = make_manager({"otp_method": "Phone", "fail_after_otp": True})

    manager.start("user", "password", tmp_path / ".mi.token")
    wait_for_state(manager, "otp_required")
    manager.submit_otp("000000")

    status = wait_for_state(manager, "failed")
    assert "验证码错误" in status["error"]


def test_login_failure_surfaces_login_error(tmp_path: Path) -> None:
    manager, _ = make_manager({"login_ok": False, "login_error": "Login auth failed"})

    manager.start("user", "bad-password", tmp_path / ".mi.token")
    status = wait_for_state(manager, "failed")
    assert "Login auth failed" in status["error"]


def test_otp_timeout_fails(tmp_path: Path) -> None:
    manager, _ = make_manager({"otp_method": "Phone"}, otp_timeout_sec=0.1)

    manager.start("user", "password", tmp_path / ".mi.token")
    status = wait_for_state(manager, "failed")
    assert "超时" in status["error"]


def test_duplicate_start_is_rejected_while_active(tmp_path: Path) -> None:
    manager, _ = make_manager({"otp_method": "Phone"})

    assert manager.start("user", "password", tmp_path / ".mi.token")
    wait_for_state(manager, "otp_required")
    assert not manager.start("user", "password", tmp_path / ".mi.token")
    assert not manager.start_mock([])
    manager.cancel()


def test_cancel_marks_failed_and_rejects_late_otp(tmp_path: Path) -> None:
    manager, _ = make_manager({"otp_method": "Phone"})

    manager.start("user", "password", tmp_path / ".mi.token")
    wait_for_state(manager, "otp_required")
    manager.cancel()

    status = manager.status()
    assert status["status"] == "failed"
    assert "取消" in status["error"]
    assert not manager.submit_otp("123456")


def test_cancelled_session_does_not_pollute_next_one(tmp_path: Path) -> None:
    manager, _ = make_manager({"otp_method": "Phone"})

    manager.start("user", "password", tmp_path / ".mi.token")
    wait_for_state(manager, "otp_required")
    manager.cancel()

    assert manager.start_mock([MinaDevice(id="mock-device", name="Mock Mina")])
    time.sleep(0.2)  # 留时间让被取消的旧线程收尾
    status = manager.status()
    assert status["status"] == "success"
    assert status["devices"] == [{"id": "mock-device", "name": "Mock Mina"}]


def test_device_fetch_failure_still_succeeds_with_empty_list(tmp_path: Path) -> None:
    manager, _ = make_manager({"devices_error": RuntimeError("device_list boom")})

    manager.start("user", "password", tmp_path / ".mi.token")
    status = wait_for_state(manager, "success")
    assert status["devices"] == []


def test_start_mock_is_immediate_success() -> None:
    manager = LoginSessionManager()

    assert manager.status()["status"] == "idle"
    assert manager.start_mock([MinaDevice(id="mock-device", name="Mock Mina")])
    status = manager.status()
    assert status["status"] == "success"
    assert status["devices"] == [{"id": "mock-device", "name": "Mock Mina"}]


def test_cancel_is_noop_on_terminal_state() -> None:
    manager = LoginSessionManager()
    manager.cancel()
    assert manager.status()["status"] == "idle"


def _wait_until_gone(path: Path, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not path.exists()


def test_success_promotes_temp_token_to_real_path(tmp_path: Path) -> None:
    real = tmp_path / ".mi.token"
    seen: dict[str, Path] = {}

    @asynccontextmanager
    async def factory(username, password, token_path, otp_callback):
        seen["store"] = Path(token_path)

        class Account:
            async def login(self, sid: str) -> bool:
                Path(token_path).write_text('{"userId": 1}', encoding="utf-8")
                return True

        yield Account()

    manager = LoginSessionManager(account_factory=factory, otp_timeout_sec=1.0, total_timeout_sec=5.0)
    assert manager.start("user", "password", real)
    wait_for_state(manager, "success")

    assert seen["store"] != real  # 登录写的是会话临时文件
    assert '"userId": 1' in real.read_text()  # 成功后晋升为正式 token
    _wait_until_gone(seen["store"])


def test_failed_login_keeps_existing_real_token(tmp_path: Path) -> None:
    real = tmp_path / ".mi.token"
    real.write_text('{"userId": 9, "micoapi": ["", "good"]}', encoding="utf-8")

    @asynccontextmanager
    async def factory(username, password, token_path, otp_callback):
        class Account:
            _login_error = "Login auth failed"

            async def login(self, sid: str) -> bool:
                # miservice 失败路径会删除 token 存储文件
                Path(token_path).unlink(missing_ok=True)
                return False

        yield Account()

    manager = LoginSessionManager(account_factory=factory, otp_timeout_sec=1.0, total_timeout_sec=5.0)
    manager.start("user", "bad-password", real)
    wait_for_state(manager, "failed")
    assert '"good"' in real.read_text()


def test_cancelled_session_discards_login_token(tmp_path: Path) -> None:
    real = tmp_path / ".mi.token"
    login_done = threading.Event()

    @asynccontextmanager
    async def factory(username, password, token_path, otp_callback):
        class Account:
            async def login(self, sid: str) -> bool:
                await asyncio.sleep(0.3)  # 给测试留出 cancel 窗口
                Path(token_path).write_text('{"userId": 1}', encoding="utf-8")
                login_done.set()
                return True

        yield Account()

    manager = LoginSessionManager(account_factory=factory, otp_timeout_sec=1.0, total_timeout_sec=5.0)
    manager.start("user", "password", real)
    manager.cancel()
    assert login_done.wait(3.0)

    assert manager.status()["status"] == "failed"
    # 临时文件清理发生在晋升决策之后，等它消失再断言正式 token 未被写入。
    for leftover in tmp_path.glob(".mi.token.login*"):
        _wait_until_gone(leftover)
    assert not real.exists()  # 被取消的会话不得晋升 token
