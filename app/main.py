from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.config import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # refuse to start with missing or invalid settings instead of failing on the first request
    get_settings()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Car Detailer Booking System", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
