from fastapi import FastAPI

from app.api import health


def create_app() -> FastAPI:
    app = FastAPI(title="Car Detailer Booking System")
    app.include_router(health.router)
    return app


app = create_app()
