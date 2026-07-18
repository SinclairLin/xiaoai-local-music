"""Application entry point."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from .config import Settings
from .routes import router
from .service import MusicService


def create_app(settings: Settings | None = None, service: MusicService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    application = FastAPI(title="XiaoAI Local Music", version="0.0.1")
    application.state.settings = settings
    application.state.service = service or MusicService(settings.music_dir)
    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    settings = Settings.from_env()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port)
