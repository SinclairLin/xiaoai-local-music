"""Runtime configuration loaded from ``config.yaml`` and the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when persisted or environment configuration is invalid."""


def _non_empty_env(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load configuration {path}: {exc}") from exc

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


def _port_value(value: Any, source: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ConfigError(f"{source} must be an integer between 1 and 65535")
    return value


@dataclass(frozen=True)
class Settings:
    music_root: str = "/music"
    config_dir: str = "/config"
    host: str = "0.0.0.0"
    port: int = 8123
    music_dir: str | Path | None = field(default=None, repr=False, compare=False)

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
        yaml_port = data.get("port", cls.port)
        port = _port_value(yaml_port, "config key 'port'")

        music_root = _non_empty_env("MUSIC_ROOT") or _non_empty_env("MUSIC_DIR") or music_root
        host = _non_empty_env("HOST") or host
        env_port = _non_empty_env("PORT")
        if env_port is not None:
            try:
                port = _port_value(int(env_port), "environment variable PORT")
            except ValueError as exc:
                raise ConfigError("environment variable PORT must be an integer") from exc

        return cls(
            music_root=music_root,
            config_dir=config_dir,
            host=host,
            port=port,
        )
