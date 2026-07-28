from __future__ import annotations

from pathlib import Path

from aiohttp import web

_CONTENT_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".svg": "image/svg+xml",
}


def _web_file(request: web.Request, name: str) -> web.Response:
    path: Path = request.app["settings"].web_dir / name
    if not path.exists():
        raise web.HTTPNotFound(text=f"missing: {name}")
    content_type = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
    if path.suffix in {".html", ".css", ".js"}:
        return web.Response(
            text=path.read_text(encoding="utf-8"),
            content_type=content_type,
            charset="utf-8",
        )
    return web.Response(body=path.read_bytes(), content_type=content_type)


async def serve_landing(request: web.Request) -> web.Response:
    return _web_file(request, "landing.html")


async def serve_assistant(request: web.Request) -> web.Response:
    return _web_file(request, "assistant.html")


async def serve_assistant_session(request: web.Request) -> web.Response:
    """Open assistant UI scoped to an existing meeting id."""
    meeting_id = request.match_info["meeting_id"]
    meeting = request.app["manager"].store.get_meeting(meeting_id)
    if not meeting:
        raise web.HTTPNotFound(text="meeting not found")
    return _web_file(request, "assistant.html")


async def serve_css(request: web.Request) -> web.Response:
    return _web_file(request, "styles.css")


async def serve_js(request: web.Request) -> web.Response:
    return _web_file(request, "meeting.js")


async def serve_logo(request: web.Request) -> web.Response:
    return _web_file(request, "logo.svg")
