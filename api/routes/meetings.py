from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

import numpy as np
from aiohttp import web

from api.realtime import broadcast

logger = logging.getLogger(__name__)


async def list_meetings(request: web.Request) -> web.Response:
    meetings = request.app["manager"].store.list_meetings()
    return web.json_response({"meetings": [m.to_dict() for m in meetings]})


async def create_meeting(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = (body or {}).get("title") or "جلسه جدید"
    participants = (body or {}).get("participants") or []
    app = request.app

    async def on_event(event: Dict[str, Any]) -> None:
        meeting_id = event.get("meeting_id")
        if not meeting_id and "segment" in event:
            meeting_id = event["segment"].get("meeting_id")
        if meeting_id:
            await broadcast(app, meeting_id, event)

    session = app["manager"].create_meeting(
        title=title, participants=participants, on_event=on_event
    )
    if bool((body or {}).get("start", True)):
        await session.start()
    return web.json_response(session.record.to_dict(), status=201)


async def get_meeting(request: web.Request) -> web.Response:
    meeting_id = request.match_info["meeting_id"]
    meeting = request.app["manager"].store.get_meeting(meeting_id)
    if not meeting:
        raise web.HTTPNotFound(text="meeting not found")
    return web.json_response(meeting.to_dict())


async def stop_meeting(request: web.Request) -> web.Response:
    meeting_id = request.match_info["meeting_id"]
    session = request.app["manager"].get_or_restore(meeting_id)
    if not session:
        raise web.HTTPNotFound(text="meeting not found")
    record = await session.stop()
    return web.json_response(record.to_dict())


async def get_transcript(request: web.Request) -> web.Response:
    meeting_id = request.match_info["meeting_id"]
    store = request.app["manager"].store
    if not store.get_meeting(meeting_id):
        raise web.HTTPNotFound(text="meeting not found")
    segments = store.get_segments(meeting_id)
    return web.json_response(
        {"meeting_id": meeting_id, "segments": [s.to_dict() for s in segments]}
    )


async def upload_audio(request: web.Request) -> web.Response:
    meeting_id = request.match_info["meeting_id"]
    app = request.app
    settings = app["settings"]

    async def on_event(event: Dict[str, Any]) -> None:
        if "segment" in event and "meeting_id" not in event:
            event = {**event, "meeting_id": meeting_id}
        await broadcast(app, meeting_id, event)

    session = app["manager"].get_or_restore(meeting_id, on_event=on_event)
    if not session:
        raise web.HTTPNotFound(text="meeting not found")

    reader = await request.multipart()
    field = await reader.next()
    if field is None:
        raise web.HTTPBadRequest(text="expected multipart file field")

    filename = field.filename or "upload.wav"
    dest = str(settings.upload_dir / f"{meeting_id}_{os.path.basename(filename)}")
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            size += len(chunk)
            f.write(chunk)

    logger.info("Uploaded %s (%d bytes) for meeting %s", dest, size, meeting_id)
    segments = await session.process_uploaded_file(dest)
    return web.json_response(
        {
            "meeting_id": meeting_id,
            "audio_path": dest,
            "segments": [s.to_dict() for s in segments],
        }
    )


async def generate_insights(request: web.Request) -> web.Response:
    meeting_id = request.match_info["meeting_id"]
    store = request.app["manager"].store
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise web.HTTPNotFound(text="meeting not found")
    segments = store.get_segments(meeting_id)
    final = [s for s in segments if not s.provisional] or segments
    result = await request.app["insights"].generate(
        meeting_id, final, speaker_map=meeting.speaker_map
    )
    store.save_insights(result)
    await broadcast(request.app, meeting_id, {"type": "insights", "insights": result.to_dict()})
    return web.json_response(result.to_dict())


async def get_insights(request: web.Request) -> web.Response:
    meeting_id = request.match_info["meeting_id"]
    result = request.app["manager"].store.get_insights(meeting_id)
    if not result:
        raise web.HTTPNotFound(text="insights not found")
    return web.json_response(result.to_dict())


async def audio_ws(request: web.Request) -> web.WebSocketResponse:
    meeting_id = request.match_info["meeting_id"]
    app = request.app
    ws = web.WebSocketResponse(max_msg_size=8 * 1024 * 1024)
    await ws.prepare(request)

    async def on_event(event: Dict[str, Any]) -> None:
        if "meeting_id" not in event:
            event = {**event, "meeting_id": meeting_id}
        await broadcast(app, meeting_id, event)

    session = app["manager"].get_or_restore(meeting_id, on_event=on_event)
    if not session:
        await ws.send_json({"type": "error", "message": "meeting not found"})
        await ws.close()
        return ws

    app["ws_by_meeting"].setdefault(meeting_id, set()).add(ws)
    await ws.send_json(
        {
            "type": "status",
            "status": session.record.status.value,
            "meeting_id": meeting_id,
        }
    )

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    payload = json.loads(msg.data)
                except json.JSONDecodeError:
                    await ws.send_json({"type": "error", "message": "invalid json"})
                    continue
                await _handle_ws_message(session, ws, payload)
            elif msg.type == web.WSMsgType.BINARY:
                samples = np.frombuffer(msg.data, dtype=np.int16)
                await session.append_audio_int16(samples)
            elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.ERROR):
                break
    finally:
        app["ws_by_meeting"].get(meeting_id, set()).discard(ws)

    return ws


async def _handle_ws_message(session, ws: web.WebSocketResponse, payload: dict) -> None:
    msg_type = payload.get("type")
    if msg_type == "audio":
        samples = np.asarray(payload.get("data") or [], dtype=np.int16)
        await session.append_audio_int16(samples)
    elif msg_type == "stop":
        record = await session.stop()
        await ws.send_json(
            {
                "type": "status",
                "status": record.status.value,
                "meeting_id": session.meeting_id,
            }
        )
    elif msg_type == "ping":
        await ws.send_json({"type": "pong"})
    else:
        await ws.send_json({"type": "error", "message": f"unknown type: {msg_type}"})
