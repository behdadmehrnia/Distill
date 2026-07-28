from fastapi import FastAPI

from api.routes import health, meetings, pages, tuning


def setup_routes(app: FastAPI) -> None:
    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(tuning.router)
    app.include_router(meetings.router)
