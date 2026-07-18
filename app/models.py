"""API models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Track(BaseModel):
    id: str
    title: str
    artist: str = ""
    album: str = ""
    duration: float = 0.0
    mtime: float = 0.0
    size: int = 0
    path: str


class PlayRequest(BaseModel):
    track_id: str = Field(min_length=1)
    queue_ids: list[str] | None = None


class VoiceRequest(BaseModel):
    text: str = Field(min_length=1)


class ConfigUpdate(BaseModel):
    xiaomi_user: str | None = None
    xiaomi_password: str | None = None
    public_base_url: str | None = None
    music_root: str | None = None
    host: str | None = None
    port: int | None = None
    mina_api_base_url: str | None = None
    mina_mode: str | None = None
    mina_device_id: str | None = None


class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)
