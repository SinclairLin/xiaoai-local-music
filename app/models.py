"""API models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Track(BaseModel):
    id: str
    title: str
    artist: str = ""
    path: str


class PlayRequest(BaseModel):
    track_id: str = Field(min_length=1)


class VoiceRequest(BaseModel):
    text: str = Field(min_length=1)

