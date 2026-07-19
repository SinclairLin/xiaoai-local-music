"""Application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .config import Settings
from .mina_client import MinaMiserviceClient, MockMinaClient
from .routes import router
from .service import MusicService
from .voice_worker import MinaVoiceSource, VoiceSource, VoiceWorker


def create_app(settings: Settings | None = None, service: MusicService | None = None, voice_source: VoiceSource | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if service is not None:
        configured_service = service
        mina_client = service.mina_client
    else:
        if settings.mina_mode == "mock":
            mina_client = MockMinaClient(settings.mina_device_id)
        else:
            mina_client = MinaMiserviceClient(
                settings.xiaomi_user,
                settings.xiaomi_password,
                settings.config_dir,
            )
        configured_service = MusicService(
            settings.music_root,
            settings.public_base_url,
            mina_client=mina_client,
            device_id=settings.mina_device_id,
        )

    source = voice_source or MinaVoiceSource(mina_client, settings.mina_device_id, settings.voice.hardware)
    voice_worker = VoiceWorker(
        source,
        configured_service,
        mina_client=mina_client,
        device_id=settings.mina_device_id,
        hardware=settings.voice.hardware,
        enabled=settings.voice.enabled,
        hijack_all_play=settings.voice.hijack_all_play,
        speak_confirm=settings.voice.speak_confirm,
        poll_interval_sec=settings.voice.poll_interval_sec,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.service.scan()
        if application.state.voice_worker.enabled:
            await application.state.voice_worker.start()
        try:
            yield
        finally:
            await application.state.voice_worker.stop()

    application = FastAPI(title="XiaoAI Local Music", version="0.0.1", lifespan=lifespan)
    application.state.settings = settings
    application.state.service = configured_service
    application.state.mina_client = mina_client
    application.state.voice_log = voice_worker.log
    application.state.voice_worker = voice_worker
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    settings = Settings.from_env()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)
