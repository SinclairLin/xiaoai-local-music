"""HTTP routes for the local music bridge."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .config import ConfigError, Settings
from .mina_client import MinaClientError, MinaDeviceError, MinaMiserviceClient, MockMinaClient
from .models import ConfigUpdate, PlayRequest, VoiceEnableRequest, VoiceRequest, VolumeRequest
from .service import PlaybackStateError, TrackNotFoundError
from .voice import VoiceIntent, parse_command

router = APIRouter()

STATIC_DIR = Path(__file__).resolve().parent / "static"


@router.get("/", response_class=FileResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


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
async def voice(payload: VoiceRequest, request: Request) -> dict[str, object]:
    parsed = parse_command(payload.text)
    if parsed is None:
        raise HTTPException(status_code=400, detail="unsupported voice command")
    try:
        results = await request.app.state.voice_worker.dispatch_text(payload.text, raise_errors=True)
    except MinaDeviceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PlaybackStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    if any(item.get("error") == "track not found" for item in results):
        raise HTTPException(status_code=404, detail="track not found")
    if parsed.intent is VoiceIntent.PLAY:
        match = next((item for item in results if item.get("matched_track")), None)
        if match is None:
            raise HTTPException(status_code=409, detail="play command was not hijacked")
        track = request.app.state.service.get_track(match["matched_track"]["id"])
        response = _queue_response(request)
        response.update({"command": parsed.query, "status": "playing", "track": track})
        return response
    response = _queue_response(request)
    response["command"] = parsed.intent.value
    return response


@router.get("/api/voice/status")
async def voice_status(request: Request) -> dict[str, object]:
    return await request.app.state.voice_worker.status()


@router.post("/api/voice/enable")
async def voice_enable(payload: VoiceEnableRequest, request: Request) -> dict[str, object]:
    worker = request.app.state.voice_worker
    settings: Settings = request.app.state.settings
    if payload.enabled:
        if not settings.mina_device_id or not settings.voice.hardware:
            raise HTTPException(status_code=422, detail="mina_device_id and voice.hardware are required")
        validator = getattr(request.app.state.mina_client, "validate_voice_device", None)
        if validator is not None:
            try:
                await asyncio.to_thread(validator, settings.mina_device_id, settings.voice.hardware)
            except MinaClientError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        updated_voice = replace(settings.voice, enabled=payload.enabled)
        updated = replace(settings, voice=updated_voice)
        updated.save()
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.settings = updated
    worker.update_runtime(enabled=payload.enabled, device_id=updated.mina_device_id, hardware=updated.voice.hardware, hijack_all_play=updated.voice.hijack_all_play, speak_confirm=updated.voice.speak_confirm, poll_interval_sec=updated.voice.poll_interval_sec)
    await worker.set_enabled(payload.enabled)
    return await worker.status()


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
        "voice": {
            "enabled": settings.voice.enabled,
            "poll_interval_sec": settings.voice.poll_interval_sec,
            "hijack_all_play": settings.voice.hijack_all_play,
            "speak_confirm": settings.voice.speak_confirm,
            "hardware": settings.voice.hardware,
        },
    }


@router.put("/api/config")
async def update_config(payload: ConfigUpdate, request: Request) -> dict[str, object]:
    old: Settings = request.app.state.settings
    password = old.xiaomi_password if payload.xiaomi_password in (None, "********") else payload.xiaomi_password
    try:
        voice = old.voice
        if payload.voice is not None:
            voice = replace(
                voice,
                enabled=payload.voice.enabled if payload.voice.enabled is not None else voice.enabled,
                poll_interval_sec=payload.voice.poll_interval_sec if payload.voice.poll_interval_sec is not None else voice.poll_interval_sec,
                hijack_all_play=payload.voice.hijack_all_play if payload.voice.hijack_all_play is not None else voice.hijack_all_play,
                speak_confirm=payload.voice.speak_confirm if payload.voice.speak_confirm is not None else voice.speak_confirm,
                hardware=payload.voice.hardware if payload.voice.hardware is not None else voice.hardware,
            )
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
            "voice": voice,
        }
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
    worker = getattr(request.app.state, "voice_worker", None)
    if worker is not None:
        source = getattr(worker, "source", None)
        if hasattr(source, "mina_client"):
            source.mina_client = client
            source.device_id = updated.mina_device_id
            source.hardware = updated.voice.hardware
        worker.update_runtime(
            mina_client=client,
            device_id=updated.mina_device_id,
            hardware=updated.voice.hardware,
            enabled=updated.voice.enabled,
            hijack_all_play=updated.voice.hijack_all_play,
            speak_confirm=updated.voice.speak_confirm,
            poll_interval_sec=updated.voice.poll_interval_sec,
        )
        if updated.voice.enabled != old.voice.enabled:
            await worker.set_enabled(updated.voice.enabled)
    response = get_config(request)
    # 这些字段只在进程启动时被消费，运行时修改需重启才生效。
    response["restart_required"] = any(
        getattr(updated, key) != getattr(old, key)
        for key in ("music_root", "host", "port", "public_base_url")
    )
    return response


@router.get("/api/devices")
def devices(request: Request) -> dict[str, object]:
    try:
        listed = request.app.state.service.mina_client.list_devices()
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    return {
        "devices": [{"id": item.id, "name": item.name} for item in listed],
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
    except MinaClientError as exc:
        raise _mina_failure(exc) from exc
    return _queue_response(request)


@router.get("/api/logs")
async def logs(request: Request, limit: int | None = None) -> dict[str, object]:
    if limit is not None and limit < 1:
        raise HTTPException(status_code=422, detail="limit must be positive")
    return {"logs": await request.app.state.voice_worker.log.snapshot(limit)}
