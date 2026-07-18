"""Runtime configuration loaded from ``/config/config.yaml`` and the environment."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


class ConfigError(ValueError):
    """Raised when the persisted or environment configuration is invalid."""


def _non_empty_env(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigError(f"cannot read configuration file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"configuration root in {path} must be a mapping")
    return loaded


def _string_value(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"config key {key!r} must be a non-empty string")
    return value


def _optional_string_value(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"config key {key!r} must be a string or null")
    return value


def _port_value(value: Any, source: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ConfigError(f"{source} must be an integer between 1 and 65535")
    return value


def _public_base_url_value(value: Any, source: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{source} must be a non-empty absolute HTTP(S) URL")

    normalized = value.strip().rstrip("/")
    if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in normalized):
        raise ConfigError(f"{source} must not contain whitespace or control characters")
    if "?" in normalized or "#" in normalized:
        raise ConfigError(f"{source} must not include a query string or fragment")
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{source} must be a valid absolute HTTP(S) URL") from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ConfigError(f"{source} must be a valid absolute HTTP(S) URL")
    if port == 0:
        raise ConfigError(f"{source} port must be between 1 and 65535")
    return parsed.scheme + normalized[len(parsed.scheme):]


@dataclass(frozen=True)
class Settings:
    music_root: str = "/music"
    config_dir: str = "/config"
    host: str = "0.0.0.0"
    port: int = 8123
    music_dir: str | Path | None = field(default=None, repr=False, compare=False)
    xiaomi_user: str | None = field(default=None, repr=False)
    xiaomi_password: str | None = field(default=None, repr=False)
    public_base_url: str = ""

    def __post_init__(self) -> None:
        if self.music_dir is not None:
            if self.music_root != "/music" and os.fspath(self.music_root) != os.fspath(self.music_dir):
                raise ConfigError("music_root and legacy music_dir disagree")
            object.__setattr__(self, "music_root", os.fspath(self.music_dir))
        else:
            try:
                music_root = os.fspath(self.music_root)
            except TypeError as exc:
                raise ConfigError("settings field 'music_root' must be a path string") from exc
            if not music_root.strip():
                raise ConfigError("settings field 'music_root' must be a non-empty string")
            object.__setattr__(self, "music_root", music_root)
        object.__setattr__(self, "music_dir", self.music_root)

        for key in ("config_dir", "host"):
            if not isinstance(getattr(self, key), str) or not getattr(self, key).strip():
                raise ConfigError(f"settings field {key!r} must be a non-empty string")
        for key in ("xiaomi_user", "xiaomi_password"):
            value = getattr(self, key)
            if value is not None and not isinstance(value, str):
                raise ConfigError(f"settings field {key!r} must be a string or null")
        object.__setattr__(self, "port", _port_value(self.port, "settings field 'port'"))
        object.__setattr__(
            self,
            "public_base_url",
            _public_base_url_value(self.public_base_url, "settings field 'public_base_url'"),
        )

    @property
    def config_path(self) -> Path:
        return Path(self.config_dir) / "config.yaml"

    @classmethod
    def from_env(cls) -> "Settings":
        config_dir = _non_empty_env("CONFIG_DIR") or cls.config_dir
        data = _load_yaml(Path(config_dir) / "config.yaml")

        yaml_music_root = data.get("music_root", data.get("music_dir", cls.music_root))
        music_root = _string_value({"music_root": yaml_music_root}, "music_root", cls.music_root)
        host = _string_value(data, "host", cls.host)
        xiaomi_user = _optional_string_value(data, "xiaomi_user")
        xiaomi_password = _optional_string_value(data, "xiaomi_password")
        public_base_url = data.get("public_base_url")

        music_root = _non_empty_env("MUSIC_ROOT") or _non_empty_env("MUSIC_DIR") or music_root
        host = _non_empty_env("HOST") or host
        xiaomi_user = _non_empty_env("XIAOMI_USER") or xiaomi_user
        xiaomi_password = _non_empty_env("XIAOMI_PASSWORD") or xiaomi_password
        public_base_url = _non_empty_env("PUBLIC_BASE_URL") or public_base_url

        env_port = _non_empty_env("PORT")
        if env_port is not None:
            try:
                port = int(env_port)
            except ValueError as exc:
                raise ConfigError("environment variable PORT must be an integer") from exc
            port = _port_value(port, "environment variable PORT")
        else:
            port = _port_value(data.get("port", cls.port), "config key 'port'")

        return cls(
            music_root=music_root,
            config_dir=config_dir,
            host=host,
            port=port,
            xiaomi_user=xiaomi_user,
            xiaomi_password=xiaomi_password,
            public_base_url=_public_base_url_value(public_base_url, "public_base_url"),
        )

    def save(self, path: str | Path | None = None) -> Path:
        """Atomically persist application settings and return the target path."""
        target = Path(path) if path is not None else self.config_path
        payload = {
            "xiaomi_user": self.xiaomi_user,
            "xiaomi_password": self.xiaomi_password,
            "public_base_url": self.public_base_url,
            "music_root": self.music_root,
            "host": self.host,
            "port": self.port,
        }
        temporary: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
            ) as handle:
                temporary = Path(handle.name)
                yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except (OSError, yaml.YAMLError) as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise ConfigError(f"cannot write configuration file {target}: {exc}") from exc
        return target
