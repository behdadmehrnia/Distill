from __future__ import annotations

from pathlib import Path

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from api.auth.deps import get_optional_user
from api.auth.models import UserRecord

router = APIRouter(tags=["pages"])

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
}


def _web_path(request: Request, name: str) -> Path:
    path: Path = request.app.state.settings.web_dir / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"missing: {name}")
    return path


def _web_file(request: Request, name: str) -> Response:
    path = _web_path(request, name)
    media_type = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
    headers = {}
    if path.suffix in {".html", ".css", ".js"}:
        # Avoid sticky CDN/browser caches for UI logic during deploys.
        headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return Response(
            content=path.read_text(encoding="utf-8"),
            media_type=media_type,
            headers=headers,
        )
    return FileResponse(path, media_type=media_type)


@router.get("/", response_class=HTMLResponse)
async def serve_landing(request: Request) -> Response:
    return _web_file(request, "landing.html")


@router.get("/login", response_class=HTMLResponse)
async def serve_login(request: Request) -> Response:
    return _web_file(request, "login.html")


@router.get("/register", response_class=HTMLResponse)
async def serve_register(request: Request) -> Response:
    return _web_file(request, "register.html")


@router.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard(
    request: Request,
    user: Optional[UserRecord] = Depends(get_optional_user),
) -> Response:
    if not user:
        return RedirectResponse(url="/login?next=/dashboard", status_code=302)
    if user.is_admin:
        return RedirectResponse(url="/admin", status_code=302)
    return _web_file(request, "dashboard.html")


@router.get("/assistant", response_class=HTMLResponse)
async def serve_assistant(
    request: Request,
    user: Optional[UserRecord] = Depends(get_optional_user),
) -> Response:
    if not user:
        return RedirectResponse(url="/login?next=/assistant", status_code=302)
    return _web_file(request, "assistant.html")


@router.get("/assistant/{meeting_id}", response_class=HTMLResponse)
async def serve_assistant_session(
    request: Request,
    meeting_id: str,
    user: Optional[UserRecord] = Depends(get_optional_user),
) -> Response:
    """Open assistant UI scoped to an existing meeting id."""
    meeting = request.app.state.manager.store.get_meeting(meeting_id)
    if not meeting:
        return RedirectResponse(url="/dashboard", status_code=302)
    meeting_owner_id = getattr(meeting, "user_id", None)
    if user and meeting_owner_id and meeting_owner_id != user.id:
        return RedirectResponse(url="/dashboard", status_code=302)
    if not user:
        return RedirectResponse(url=f"/login?next=/assistant/{meeting_id}", status_code=302)
    return _web_file(request, "assistant.html")


@router.get("/admin", response_class=HTMLResponse)
async def serve_admin(
    request: Request,
    user: Optional[UserRecord] = Depends(get_optional_user),
) -> Response:
    if not user:
        return RedirectResponse(url="/login?next=/admin", status_code=302)
    if not user.is_admin:
        return RedirectResponse(url="/dashboard", status_code=302)
    return _web_file(request, "admin.html")


@router.get("/styles.css")
async def serve_css(request: Request) -> Response:
    return _web_file(request, "styles.css")


@router.get("/assistant.css")
async def serve_assistant_css(request: Request) -> Response:
    return _web_file(request, "assistant.css")


@router.get("/meeting.js")
async def serve_js(request: Request) -> Response:
    return _web_file(request, "meeting.js")


@router.get("/auth.js")
async def serve_auth_js(request: Request) -> Response:
    return _web_file(request, "auth.js")


@router.get("/dashboard.js")
async def serve_dashboard_js(request: Request) -> Response:
    return _web_file(request, "dashboard.js")


@router.get("/admin.js")
async def serve_admin_js(request: Request) -> Response:
    return _web_file(request, "admin.js")


@router.get("/logo.svg")
async def serve_logo(request: Request) -> Response:
    return _web_file(request, "logo.svg")


@router.get("/fonts/{font_name}")
async def serve_font(request: Request, font_name: str) -> Response:
    if "/" in font_name or "\\" in font_name or ".." in font_name:
        raise HTTPException(status_code=400, detail="invalid font name")
    path = request.app.state.settings.web_dir / "fonts" / font_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"missing font: {font_name}")
    media_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)
