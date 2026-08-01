from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

import numpy as np
from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)

from api.realtime import broadcast

logger = logging.getLogger(__name__)

router = APIRouter(tags=["meetings"])


@router.get("/meetings")
async def list_meetings(request: Request) -> Dict[str, Any]:
    meetings = request.app.state.manager.store.list_meetings()
    return {"meetings": [m.to_dict() for m in meetings]}


@router.post("/meetings", status_code=201)
async def create_meeting(request: Request) -> Dict[str, Any]:
    try:
        raw = await request.json()
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    title = raw.get("title") or "جلسه جدید"
    participants = raw.get("participants") or []
    start = bool(raw.get("start", True))
    app = request.app

    async def on_event(event: Dict[str, Any]) -> None:
        meeting_id = event.get("meeting_id")
        if not meeting_id and "segment" in event:
            meeting_id = event["segment"].get("meeting_id")
        if meeting_id:
            await broadcast(app, meeting_id, event)

    session = app.state.manager.create_meeting(
        title=title, participants=participants, on_event=on_event
    )
    if start:
        await session.start()
    return session.record.to_dict()


@router.get("/meetings/{meeting_id}")
async def get_meeting(request: Request, meeting_id: str) -> Dict[str, Any]:
    meeting = request.app.state.manager.store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="meeting not found")
    return meeting.to_dict()


@router.post("/meetings/{meeting_id}/stop")
async def stop_meeting(request: Request, meeting_id: str) -> Dict[str, Any]:
    session = request.app.state.manager.get_or_restore(meeting_id)
    if not session:
        raise HTTPException(status_code=404, detail="meeting not found")
    record = await session.stop()
    return record.to_dict()


@router.get("/meetings/{meeting_id}/transcript")
async def get_transcript(request: Request, meeting_id: str) -> Dict[str, Any]:
    store = request.app.state.manager.store
    if not store.get_meeting(meeting_id):
        raise HTTPException(status_code=404, detail="meeting not found")
    segments = store.get_segments(meeting_id)
    return {"meeting_id": meeting_id, "segments": [s.to_dict() for s in segments]}


@router.post("/meetings/{meeting_id}/upload")
async def upload_audio(
    request: Request,
    meeting_id: str,
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    app = request.app
    settings = app.state.settings

    async def on_event(event: Dict[str, Any]) -> None:
        if "segment" in event and "meeting_id" not in event:
            event = {**event, "meeting_id": meeting_id}
        await broadcast(app, meeting_id, event)

    session = app.state.manager.get_or_restore(meeting_id, on_event=on_event)
    if not session:
        raise HTTPException(status_code=404, detail="meeting not found")

    filename = file.filename or "upload.wav"
    dest = str(settings.upload_dir / f"{meeting_id}_{os.path.basename(filename)}")
    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            out.write(chunk)

    logger.info("Uploaded %s (%d bytes) for meeting %s", dest, size, meeting_id)
    segments = await session.process_uploaded_file(dest)
    return {
        "meeting_id": meeting_id,
        "audio_path": dest,
        "segments": [s.to_dict() for s in segments],
    }


@router.post("/meetings/{meeting_id}/insights")
async def generate_insights(request: Request, meeting_id: str) -> Dict[str, Any]:
    store = request.app.state.manager.store
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="meeting not found")
    segments = store.get_segments(meeting_id)
    final = [s for s in segments if not s.provisional] or segments
    try:
        result = await request.app.state.insights.generate(
            meeting_id, final, speaker_map=meeting.speaker_map
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    store.save_insights(result)
    await broadcast(
        request.app, meeting_id, {"type": "insights", "insights": result.to_dict()}
    )
    return result.to_dict()


@router.get("/meetings/{meeting_id}/insights")
async def get_insights(request: Request, meeting_id: str) -> Dict[str, Any]:
    result = request.app.state.manager.store.get_insights(meeting_id)
    if not result:
        raise HTTPException(status_code=404, detail="insights not found")
    return result.to_dict()


@router.websocket("/meetings/{meeting_id}/audio")
async def audio_ws(websocket: WebSocket, meeting_id: str) -> None:
    await websocket.accept()
    app = websocket.app

    async def on_event(event: Dict[str, Any]) -> None:
        if "meeting_id" not in event:
            event = {**event, "meeting_id": meeting_id}
        await broadcast(app, meeting_id, event)

    session = app.state.manager.get_or_restore(meeting_id, on_event=on_event)
    if not session:
        await websocket.send_json({"type": "error", "message": "meeting not found"})
        await websocket.close()
        return

    app.state.ws_by_meeting.setdefault(meeting_id, set()).add(websocket)
    await websocket.send_json(
        {
            "type": "status",
            "status": session.record.status.value,
            "meeting_id": meeting_id,
        }
    )

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "text" in message and message["text"] is not None:
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {"type": "error", "message": "invalid json"}
                    )
                    continue
                await _handle_ws_message(session, websocket, payload)
            elif "bytes" in message and message["bytes"] is not None:
                samples = np.frombuffer(message["bytes"], dtype=np.int16)
                await session.append_audio_int16(samples)
    except WebSocketDisconnect:
        pass
    finally:
        app.state.ws_by_meeting.get(meeting_id, set()).discard(websocket)


async def _handle_ws_message(session, ws: WebSocket, payload: dict) -> None:
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
