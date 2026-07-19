import json
import stat
from pathlib import Path

import pytest

from app.cookie_login import CookieParseError, build_token, parse_credentials, write_token_file


def test_parse_cookie_string() -> None:
    fields = parse_credentials("userId=123456; serviceToken=abc==; ssecurity=sec; deviceId=ANDROID16; passToken=pt")
    assert fields == {
        "userId": "123456",
        "serviceToken": "abc==",
        "ssecurity": "sec",
        "passToken": "pt",
        "deviceId": "ANDROID16",
    }


def test_parse_cookie_string_ignores_unknown_and_handles_newlines() -> None:
    fields = parse_credentials('user_id=42\nservice_token="tok"; sdkVersion=3.9')
    assert fields == {"userId": "42", "serviceToken": "tok"}


def test_parse_mi_token_json() -> None:
    raw = json.dumps({
        "deviceId": "QWERTYUIOPASDFGH",
        "userId": 123456,
        "passToken": "pt",
        "micoapi": ["sec", "tok"],
    })
    fields = parse_credentials(raw)
    assert fields["userId"] == "123456"
    assert fields["serviceToken"] == "tok"
    assert fields["ssecurity"] == "sec"
    assert fields["passToken"] == "pt"
    assert fields["deviceId"] == "QWERTYUIOPASDFGH"


def test_parse_flat_json() -> None:
    fields = parse_credentials('{"userId": "9", "serviceToken": "tok"}')
    assert fields == {"userId": "9", "serviceToken": "tok"}


def test_parse_invalid_json_raises() -> None:
    with pytest.raises(CookieParseError):
        parse_credentials("{not json")


def test_build_token_requires_user_id_and_service_token() -> None:
    with pytest.raises(CookieParseError, match="userId"):
        build_token({"serviceToken": "tok"})
    with pytest.raises(CookieParseError, match="serviceToken"):
        build_token({"userId": "1"})


def test_build_token_defaults_and_layout() -> None:
    token = build_token({"userId": "123", "serviceToken": "tok"})
    assert token["userId"] == 123
    assert token["micoapi"] == ["", "tok"]
    assert len(token["deviceId"]) == 16 and token["deviceId"].isupper()
    assert "passToken" not in token

    full = build_token({
        "userId": "u-abc",
        "serviceToken": "tok",
        "ssecurity": "sec",
        "passToken": "pt",
        "deviceId": "DEV",
    })
    assert full == {"deviceId": "DEV", "userId": "u-abc", "micoapi": ["sec", "tok"], "passToken": "pt"}


def test_write_token_file_permissions(tmp_path: Path) -> None:
    token_path = tmp_path / "config" / ".mi.token"
    token = build_token({"userId": "1", "serviceToken": "tok"})
    write_token_file(token_path, token)
    assert json.loads(token_path.read_text()) == token
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
