"""API models."""

from __future__ import annotations

from typing import Literal

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
    mode: Literal["once", "single_loop", "sequential", "list_loop"] | None = None


class PlaylistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    track_ids: list[str] = Field(default_factory=list)


class PlaylistUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    track_ids: list[str] | None = None


class PlaylistPlayRequest(BaseModel):
    mode: Literal["sequential", "list_loop", "single_loop"] = "sequential"


class VoiceRequest(BaseModel):
    text: str = Field(min_length=1)


class VoiceEnableRequest(BaseModel):
    enabled: bool


class VoiceConfigUpdate(BaseModel):
    enabled: bool | None = None
    poll_interval_sec: float | None = Field(default=None, gt=0)
    hijack_all_play: bool | None = None
    speak_confirm: bool | None = None
    hardware: str | None = None


class ConfigUpdate(BaseModel):
    xiaomi_user: str | None = None
    xiaomi_password: str | None = None
    public_base_url: str | None = None
    music_root: str | None = None
    host: str | None = None
    port: int | None = None
    mina_device_id: str | None = None
    voice: VoiceConfigUpdate | None = None


class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)


class OtpSubmitRequest(BaseModel):
    code: str = Field(min_length=1)


class CookieLoginRequest(BaseModel):
    """手动粘贴凭证登录；显式字段优先于 cookies 串中解析出的同名字段。"""

    cookies: str | None = None
    user_id: str | None = None
    service_token: str | None = None
    ssecurity: str | None = None
    pass_token: str | None = None
    device_id: str | None = None
