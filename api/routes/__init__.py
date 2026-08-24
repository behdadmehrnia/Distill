from fastapi import FastAPI

from api.routes import admin, auth, health, meetings, pages, tuning


def setup_routes(app: FastAPI) -> None:
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(health.router)
    app.include_router(pages.router)
    app.include_router(tuning.router)
    app.include_router(meetings.router)
