"""HTTP routes for the local music bridge."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from .config import ConfigError, Settings
from .mina_client import MinaClientError, MinaDeviceError, MinaMiserviceClient, MockMinaClient, MinaDevice
from .models import ConfigUpdate, PlayRequest, VoiceRequest, VolumeRequest
from .service import PlaybackStateError, TrackNotFoundError
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


def _mina_failure(exc: MinaClientError) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


def _queue_response(request: Request) -> dict[str, object]:
    return request.app.state.service.queue_state()


@router.post("/api/play")
def play(payload: PlayRequest, request: Request) -> dict[str, object]:
    try:
        track = request.app.state.service.play(payload.track_id, payload.queue_ids)
    except MinaDeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except TrackNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlaybackStateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    if track is None:
        raise HTTPException(status_code=404, detail="track not found")
    response = _queue_response(request)
    response["status"] = "playing"
    response["track"] = track
    return response


@router.post("/api/voice")
def voice(payload: VoiceRequest, request: Request) -> dict[str, object]:
    title = parse_play_command(payload.text)
    if title is None:
        raise HTTPException(status_code=400, detail="unsupported voice command")
    matches = request.app.state.service.list_tracks(title)
    if not matches:
        raise HTTPException(status_code=404, detail="track not found")
    try:
        track = request.app.state.service.play(matches[0].id, [item.id for item in matches])
    except MinaDeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    response = _queue_response(request)
    response.update({"command": title, "status": "playing", "track": track})
    return response


@router.get("/api/config")
def get_config(request: Request) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    return {
        "music_root": settings.music_root,
        "config_dir": settings.config_dir,
        "host": settings.host,
        "port": settings.port,
        "public_base_url": settings.public_base_url,
        "xiaomi_user": settings.xiaomi_user,
        "xiaomi_password": "********" if settings.xiaomi_password else None,
        "mina_mode": settings.mina_mode,
        "mina_device_id": settings.mina_device_id,
    }


@router.put("/api/config")
def update_config(payload: ConfigUpdate, request: Request) -> dict[str, object]:
    old: Settings = request.app.state.settings
    password = old.xiaomi_password if payload.xiaomi_password in (None, "********") else payload.xiaomi_password
    values = {
        "music_root": payload.music_root if payload.music_root is not None else old.music_root,
        "config_dir": old.config_dir,
        "host": payload.host if payload.host is not None else old.host,
        "port": payload.port if payload.port is not None else old.port,
        "public_base_url": payload.public_base_url if payload.public_base_url is not None else old.public_base_url,
        "xiaomi_user": payload.xiaomi_user if payload.xiaomi_user is not None else old.xiaomi_user,
        "xiaomi_password": password,
        "mina_mode": payload.mina_mode if payload.mina_mode is not None else old.mina_mode,
        "mina_device_id": payload.mina_device_id if payload.mina_device_id is not None else old.mina_device_id,
    }
    try:
        updated = Settings(**values)
        updated.save()
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.settings = updated
    request.app.state.service.set_device_id(updated.mina_device_id)
    client = request.app.state.service.mina_client
    if updated.mina_mode != old.mina_mode:
        client = (
            MockMinaClient(updated.mina_device_id)
            if updated.mina_mode == "mock"
            else MinaMiserviceClient(updated.xiaomi_user, updated.xiaomi_password, updated.config_dir)
        )
        request.app.state.service.mina_client = client
        request.app.state.mina_client = client
    elif hasattr(client, "update_credentials"):
        client.update_credentials(updated.xiaomi_user, updated.xiaomi_password)
    if isinstance(client, MockMinaClient):
        client.device_id = updated.mina_device_id or "mock-device"
    return get_config(request)


@router.get("/api/devices")
def devices(request: Request) -> dict[str, object]:
    try:
        listed = request.app.state.service.mina_client.list_devices()
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    return {
        "devices": [{"id": item.id, "name": item.name} if isinstance(item, MinaDevice) else item for item in listed],
        "selected_device_id": request.app.state.service.device_id,
    }


@router.get("/api/queue")
def queue(request: Request) -> dict[str, object]:
    return _queue_response(request)


@router.post("/api/next")
def next_track(request: Request) -> dict[str, object]:
    try:
        request.app.state.service.next()
    except MinaDeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlaybackStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    return _queue_response(request)


@router.post("/api/previous")
def previous_track(request: Request) -> dict[str, object]:
    try:
        request.app.state.service.previous()
    except MinaDeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlaybackStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    return _queue_response(request)


@router.post("/api/pause")
def pause(request: Request) -> dict[str, object]:
    try:
        request.app.state.service.pause()
    except MinaDeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    return _queue_response(request)


@router.post("/api/stop")
def stop(request: Request) -> dict[str, object]:
    try:
        request.app.state.service.stop()
    except MinaDeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    return _queue_response(request)


@router.post("/api/resume")
def resume(request: Request) -> dict[str, object]:
    try:
        request.app.state.service.resume()
    except MinaDeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlaybackStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    return _queue_response(request)


@router.post("/api/volume")
def volume(payload: VolumeRequest, request: Request) -> dict[str, object]:
    try:
        request.app.state.service.set_volume(payload.volume)
    except MinaDeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    return _queue_response(request)
