"""Application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .config import Settings
from .routes import router
from .service import MusicService


def create_app(settings: Settings | None = None, service: MusicService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configured_service = service or MusicService(settings.music_root)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.service.scan()
        yield

    application = FastAPI(title="XiaoAI Local Music", version="0.0.1", lifespan=lifespan)
    application.state.settings = settings
    application.state.service = configured_service
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    settings = Settings.from_env()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)
