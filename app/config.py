"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    music_dir: str = "/music"
    config_dir: str = "/config"
    host: str = "0.0.0.0"
    port: int = 8123

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            music_dir=os.getenv("MUSIC_DIR", cls.music_dir),
            config_dir=os.getenv("CONFIG_DIR", cls.config_dir),
            host=os.getenv("HOST", cls.host),
            port=int(os.getenv("PORT", str(cls.port))),
        )

