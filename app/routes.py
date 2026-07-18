"""HTTP routes for the local music bridge."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from .models import PlayRequest, VoiceRequest
from .voice import parse_play_command

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index() -> str:
    return """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><title>小爱本地音乐</title>
<body><h1>小爱本地音乐</h1><p>服务已启动。使用 <code>/api/tracks</code> 查看曲目。</p></body></html>"""


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.api_route("/media/by-id/{track_id}", methods=["GET", "HEAD"], response_class=FileResponse)
def media_by_id(track_id: str, request: Request) -> FileResponse:
    media_file = request.app.state.service.get_media_file(track_id)
    if media_file is None:
        raise HTTPException(status_code=404, detail="track not found")
    return FileResponse(
        media_file.path,
        media_type=media_file.media_type,
        stat_result=media_file.stat_result,
    )


@router.get("/api/tracks")
def tracks(request: Request, q: str | None = None) -> dict[str, object]:
    return {"tracks": request.app.state.service.list_tracks(q)}


@router.post("/api/play")
def play(payload: PlayRequest, request: Request) -> dict[str, object]:
    track = request.app.state.service.play(payload.track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    return {"ok": True, "status": "mock_playing", "track": track}


@router.post("/api/voice")
def voice(payload: VoiceRequest, request: Request) -> dict[str, object]:
    title = parse_play_command(payload.text)
    if title is None:
        raise HTTPException(status_code=400, detail="unsupported voice command")
    matches = request.app.state.service.list_tracks(title)
    if not matches:
        raise HTTPException(status_code=404, detail="track not found")
    track = request.app.state.service.play(matches[0].id)
    return {"ok": True, "command": title, "status": "mock_playing", "track": track}
