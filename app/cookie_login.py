"""Turn user-pasted Xiaomi credentials into a miservice ``.mi.token`` file.

Accepted input formats:

* Cookie string, e.g. ``userId=123; serviceToken=xxx; deviceId=ABC...``
* A full ``.mi.token`` JSON document copied from another machine
* A flat JSON object with ``userId``/``serviceToken`` keys

Only ``userId`` and the ``micoapi`` serviceToken are required for MiNA
calls; ``ssecurity``/``passToken``/``deviceId`` are kept when provided.
"""

from __future__ import annotations

import json
import random
import string
from pathlib import Path
from typing import Any


class CookieParseError(ValueError):
    """User-provided credentials are malformed or incomplete."""


# 用户粘贴的键名大小写/风格不一，统一按小写无分隔符归一化。
_FIELD_ALIASES = {
    "userid": "userId",
    "user_id": "userId",
    "servicetoken": "serviceToken",
    "service_token": "serviceToken",
    "ssecurity": "ssecurity",
    "passtoken": "passToken",
    "pass_token": "passToken",
    "deviceid": "deviceId",
    "device_id": "deviceId",
}


def _normalize_key(key: str) -> str | None:
    return _FIELD_ALIASES.get(key.strip().lower())


def _fields_from_json(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        raise CookieParseError("JSON 凭证必须是对象")
    fields: dict[str, str] = {}
    for key, value in data.items():
        canonical = _normalize_key(str(key))
        if canonical and value not in (None, ""):
            fields[canonical] = str(value)
    micoapi = data.get("micoapi")
    if isinstance(micoapi, (list, tuple)):
        if len(micoapi) > 0 and micoapi[0]:
            fields.setdefault("ssecurity", str(micoapi[0]))
        if len(micoapi) > 1 and micoapi[1]:
            fields.setdefault("serviceToken", str(micoapi[1]))
    elif isinstance(micoapi, dict):
        for key, value in micoapi.items():
            canonical = _normalize_key(str(key))
            if canonical and value not in (None, ""):
                fields.setdefault(canonical, str(value))
    return fields


def _fields_from_cookie_string(raw: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in raw.replace("\n", ";").split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        canonical = _normalize_key(key)
        value = value.strip().strip('"')
        if canonical and value:
            fields[canonical] = value
    return fields


def parse_credentials(raw: str) -> dict[str, str]:
    """Extract known credential fields from a pasted cookie string or JSON."""
    raw = raw.strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            return _fields_from_json(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise CookieParseError(f"JSON 凭证解析失败：{exc}") from exc
    return _fields_from_cookie_string(raw)


def build_token(fields: dict[str, str]) -> dict[str, Any]:
    """Assemble a miservice-compatible token dict, validating required fields."""
    missing = [name for name in ("userId", "serviceToken") if not fields.get(name)]
    if missing:
        raise CookieParseError(f"缺少必需字段：{'、'.join(missing)}")
    user_id: Any = fields["userId"]
    if user_id.isdigit():
        user_id = int(user_id)
    token: dict[str, Any] = {
        "deviceId": fields.get("deviceId") or "".join(random.choices(string.ascii_uppercase, k=16)),
        "userId": user_id,
        "micoapi": [fields.get("ssecurity", ""), fields["serviceToken"]],
    }
    if fields.get("passToken"):
        token["passToken"] = fields["passToken"]
    return token


def write_token_file(token_path: Path, token: dict[str, Any]) -> None:
    """Persist the token with the same layout and permissions as MiTokenStore."""
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(token, indent=2), encoding="utf-8")
    token_path.chmod(0o600)
