"""Application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .config import Settings
from .mina_client import MinaHttpClient, MockMinaClient
from .routes import router
from .service import MusicService


def create_app(settings: Settings | None = None, service: MusicService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if service is not None:
        configured_service = service
        mina_client = service.mina_client
    else:
        if settings.mina_mode == "mock":
            mina_client = MockMinaClient(settings.mina_device_id)
        else:
            mina_client = MinaHttpClient(
                settings.mina_api_base_url or "",
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

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.service.scan()
        yield

    application = FastAPI(title="XiaoAI Local Music", version="0.0.1", lifespan=lifespan)
    application.state.settings = settings
    application.state.service = configured_service
    application.state.mina_client = mina_client
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    settings = Settings.from_env()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)
