from __future__ import annotations

from typing import Any, Dict, Set

from fastapi import FastAPI, WebSocket


async def broadcast(app: FastAPI, meeting_id: str, event: Dict[str, Any]) -> None:
    sockets: Set[WebSocket] = set(app.state.ws_by_meeting.get(meeting_id, set()))
    dead = []
    for ws in sockets:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    bucket = app.state.ws_by_meeting.get(meeting_id)
    if not bucket:
        return
    for ws in dead:
        bucket.discard(ws)
