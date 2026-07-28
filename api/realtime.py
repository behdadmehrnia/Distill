from __future__ import annotations

from typing import Any, Dict

from aiohttp import web


async def broadcast(app: web.Application, meeting_id: str, event: Dict[str, Any]) -> None:
    sockets = list(app["ws_by_meeting"].get(meeting_id, set()))
    dead = []
    for ws in sockets:
        if ws.closed:
            dead.append(ws)
            continue
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        app["ws_by_meeting"].get(meeting_id, set()).discard(ws)
