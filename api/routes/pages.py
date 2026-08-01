from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

router = APIRouter(tags=["pages"])

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _web_path(request: Request, name: str) -> Path:
    path: Path = request.app.state.settings.web_dir / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"missing: {name}")
    return path


def _web_file(request: Request, name: str) -> Response:
    path = _web_path(request, name)
    media_type = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
    if path.suffix in {".html", ".css", ".js"}:
        return Response(
            content=path.read_text(encoding="utf-8"),
            media_type=media_type,
        )
    return FileResponse(path, media_type=media_type)


@router.get("/", response_class=HTMLResponse)
async def serve_landing(request: Request) -> Response:
    return _web_file(request, "landing.html")


@router.get("/assistant", response_class=HTMLResponse)
async def serve_assistant(request: Request) -> Response:
    return _web_file(request, "assistant.html")


@router.get("/assistant/{meeting_id}", response_class=HTMLResponse)
async def serve_assistant_session(request: Request, meeting_id: str) -> Response:
    """Open assistant UI scoped to an existing meeting id."""
    meeting = request.app.state.manager.store.get_meeting(meeting_id)
    if not meeting:
        return RedirectResponse(url="/assistant", status_code=302)
    return _web_file(request, "assistant.html")


@router.get("/styles.css")
async def serve_css(request: Request) -> Response:
    return _web_file(request, "styles.css")


@router.get("/meeting.js")
async def serve_js(request: Request) -> Response:
    return _web_file(request, "meeting.js")


@router.get("/logo.svg")
async def serve_logo(request: Request) -> Response:
    return _web_file(request, "logo.svg")
