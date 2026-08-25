from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from fastapi import (
    APIRouter,
    File,
    Depends,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response

from api.auth.deps import authenticate_websocket, get_current_user, require_meeting
from api.auth.models import UserRecord
from api.meeting.ingest import AudioIngest
from api.meeting.models import (
    CaptureMode,
    MeetingMinutes,
    MeetingRecord,
    MeetingStatus,
    MinutesDecision,
)
from api.meeting.session import ProcessingCancelled
from api.realtime import broadcast

logger = logging.getLogger(__name__)

router = APIRouter(tags=["meetings"])

_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_MAX_SAMPLE_MS = 4000
_MIN_SAMPLE_MS = 600
# Fallback diarization often emits short turns; still allow a short clip.
_MIN_SAMPLE_MS_RELAXED = 200


def _to_persian_digits(value: int) -> str:
    return "".join(_PERSIAN_DIGITS[int(ch)] for ch in str(value))


def _default_speaker_label(speaker_id: str) -> str:
    digits = "".join(ch for ch in str(speaker_id) if ch.isdigit())
    if not digits:
        return "سخنگو"
    return f"سخنگوی {_to_persian_digits(int(digits) + 1)}"


def _pick_speaker_sample_span(
    *,
    speaker_id: str,
    intervals,
    segments,
) -> tuple[Optional[int], Optional[int]]:
    """Pick a playable [start,end) for speaker samples; tolerant of short fallback turns."""
    for min_ms in (_MIN_SAMPLE_MS, _MIN_SAMPLE_MS_RELAXED, 1):
        candidates = [
            iv
            for iv in intervals
            if iv.speaker_id == speaker_id
            and not iv.is_overlap
            and (iv.end_ms - iv.start_ms) >= min_ms
        ]
        if candidates:
            best = max(candidates, key=lambda iv: iv.end_ms - iv.start_ms)
            start_ms = best.start_ms
            end_ms = min(best.end_ms, best.start_ms + _MAX_SAMPLE_MS)
            if end_ms > start_ms:
                return start_ms, end_ms

    for min_ms in (_MIN_SAMPLE_MS, _MIN_SAMPLE_MS_RELAXED, 1):
        seg_candidates = [
            s
            for s in segments
            if s.speaker_id == speaker_id
            and not getattr(s, "is_overlap", False)
            and (s.end_ms - s.start_ms) >= min_ms
        ]
        if seg_candidates:
            best_seg = max(seg_candidates, key=lambda s: s.end_ms - s.start_ms)
            start_ms = best_seg.start_ms
            end_ms = min(best_seg.end_ms, best_seg.start_ms + _MAX_SAMPLE_MS)
            if end_ms > start_ms:
                return start_ms, end_ms

    return None, None


def _resolve_recording_path(
    meeting_id: str,
    audio_path: Optional[str],
    *,
    audio_dir: Path,
    upload_dir: Path,
) -> Optional[str]:
    """Return a readable recording path confined to known data dirs."""
    allowed_roots = [
        audio_dir.resolve(),
        upload_dir.resolve(),
    ]
    candidates = []
    if audio_path:
        candidates.append(Path(audio_path))
    candidates.append(audio_dir / f"{meeting_id}.wav")

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not any(
            resolved == root or root in resolved.parents for root in allowed_roots
        ):
            continue
        if resolved.is_file() and resolved.stat().st_size > 0:
            return str(resolved)
    return None


def _meeting_payload(meeting, *, has_recording: Optional[bool] = None) -> Dict[str, Any]:
    data = meeting.to_dict()
    if has_recording is not None:
        data["has_recording"] = has_recording
    return data


@router.get("/meetings")
async def list_meetings(
    request: Request,
    user: UserRecord = Depends(get_current_user),
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    meetings = request.app.state.manager.store.list_meetings(
        user_id=user.id, limit=limit, offset=offset
    )
    settings = request.app.state.settings
    out = []
    for m in meetings:
        path = _resolve_recording_path(
            m.id,
            m.audio_path,
            audio_dir=settings.audio_dir,
            upload_dir=settings.upload_dir,
        )
        out.append(_meeting_payload(m, has_recording=path is not None))
    return {"meetings": out}


@router.post("/meetings", status_code=201)
async def create_meeting(
    request: Request,
    user: UserRecord = Depends(get_current_user),
) -> Dict[str, Any]:
    try:
        raw = await request.json()
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if "title" in raw:
        title = str(raw.get("title") or "").strip()
    else:
        title = "جلسه جدید"
    participants = raw.get("participants") or []
    start = bool(raw.get("start", True))
    capture_raw = str(raw.get("capture_mode") or "mono").strip().lower()
    try:
        capture_mode = CaptureMode(capture_raw)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="capture_mode must be 'mono' or 'multi_stream'",
        )
    streams: Dict[str, str] = {}
    for item in raw.get("streams") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("speaker_id") or item.get("id") or "").strip()
        name = str(item.get("name") or item.get("label") or "").strip()
        if sid and name:
            streams[sid] = name
    stream_map = raw.get("stream_map")
    if isinstance(stream_map, dict):
        for sid, name in stream_map.items():
            if str(sid).strip() and str(name).strip():
                streams[str(sid).strip()] = str(name).strip()
    app = request.app

    async def on_event(event: Dict[str, Any]) -> None:
        meeting_id = event.get("meeting_id")
        if not meeting_id and "segment" in event:
            meeting_id = event["segment"].get("meeting_id")
        if meeting_id:
            await broadcast(app, meeting_id, event)

    session = app.state.manager.create_meeting(
        title=title,
        participants=participants,
        user_id=user.id,
        on_event=on_event,
        capture_mode=capture_mode,
        streams=streams or None,
    )
    if start:
        await session.start()
    return _meeting_payload(session.record, has_recording=False)


@router.post("/meetings/{meeting_id}/streams", status_code=201)
async def register_stream(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    """Register a participant audio stream for multi-stream capture."""
    if meeting.capture_mode != CaptureMode.MULTI_STREAM:
        raise HTTPException(
            status_code=400,
            detail="meeting is not configured for multi_stream capture",
        )
    try:
        raw = await request.json()
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    speaker_id = str(raw.get("speaker_id") or raw.get("id") or "").strip()
    name = str(raw.get("name") or raw.get("label") or "").strip() or None
    if not speaker_id:
        raise HTTPException(status_code=400, detail="speaker_id is required")

    session = request.app.state.manager.get_or_restore(meeting.id)
    if not session:
        raise HTTPException(status_code=404, detail="meeting session not found")
    sid = session.register_stream(speaker_id, name=name)
    return {
        "meeting_id": meeting.id,
        "speaker_id": sid,
        "name": session.record.speaker_map.get(sid),
        "stream_ids": (
            session._multi_ingest.stream_ids if session._multi_ingest else [sid]
        ),
    }


@router.get("/meetings/{meeting_id}")
async def get_meeting(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    # Heal stale "recording"/"processing" left behind when the capture process
    # died (or stop was cancelled) and nothing is actually running.
    if meeting.status in (MeetingStatus.RECORDING, MeetingStatus.PROCESSING):
        meeting = request.app.state.manager.heal_orphaned_recording(meeting)
    settings = request.app.state.settings
    path = _resolve_recording_path(
        meeting.id,
        meeting.audio_path,
        audio_dir=settings.audio_dir,
        upload_dir=settings.upload_dir,
    )
    return _meeting_payload(meeting, has_recording=path is not None)


@router.delete("/meetings/{meeting_id}", status_code=204)
async def delete_meeting(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Response:
    settings = request.app.state.settings
    deleted = await request.app.state.manager.delete_meeting(
        meeting.id, audio_dir=str(settings.audio_dir)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="meeting not found")

    request.app.state.ws_by_meeting.pop(meeting.id, None)
    return Response(status_code=204)


@router.get("/meetings/{meeting_id}/recording")
async def get_recording(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
):
    settings = request.app.state.settings
    path = _resolve_recording_path(
        meeting.id,
        meeting.audio_path,
        audio_dir=settings.audio_dir,
        upload_dir=settings.upload_dir,
    )
    if not path:
        raise HTTPException(status_code=404, detail="recording not found")
    media = "audio/wav" if path.lower().endswith(".wav") else "application/octet-stream"
    return FileResponse(
        path,
        media_type=media,
        filename=os.path.basename(path),
        content_disposition_type="inline",
    )


@router.post("/meetings/{meeting_id}/start")
async def start_meeting(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    """Start (or restart) capture on an existing meeting."""
    meeting_id = meeting.id
    try:
        raw = await request.json()
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    reset = bool(raw.get("reset", False))
    title = str(raw.get("title") or "").strip()

    app = request.app

    async def on_event(event: Dict[str, Any]) -> None:
        mid = event.get("meeting_id") or meeting_id
        await broadcast(app, mid, event)

    session = app.state.manager.get_or_restore(meeting_id, on_event=on_event)
    if not session:
        raise HTTPException(status_code=404, detail="meeting not found")
    if title:
        session.record.title = title
        app.state.manager.store.save_meeting(session.record)
    if session._running:
        raise HTTPException(status_code=409, detail="already recording")
    if session.record.status in (
        MeetingStatus.RECORDING,
        MeetingStatus.PROCESSING,
    ):
        # Stale DB/status after crash or missed stop — not a live capture.
        # No-op when a live stop is still in progress (_running/_stopping).
        app.state.manager.heal_orphaned_recording(session.record)
    if session.record.status == MeetingStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="meeting is still processing")

    settings = app.state.settings
    has_file = (
        _resolve_recording_path(
            meeting_id,
            session.record.audio_path,
            audio_dir=settings.audio_dir,
            upload_dir=settings.upload_dir,
        )
        is not None
    )
    if has_file and not reset:
        raise HTTPException(
            status_code=409,
            detail="recording exists; pass reset=true to overwrite",
        )

    await session.start(reset=reset or has_file)
    return _meeting_payload(session.record, has_recording=False)


@router.patch("/meetings/{meeting_id}/speakers")
async def update_speaker_map(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    """Map SPEAKER_XX ids to display names. Body: {"SPEAKER_00": "علی", ...} or {"speaker_map": {...}}."""
    meeting_id = meeting.id
    session = request.app.state.manager.get_or_restore(meeting_id)
    if not session:
        raise HTTPException(status_code=404, detail="meeting not found")
    try:
        raw = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="expected object")
    mapping = raw.get("speaker_map") if "speaker_map" in raw else raw
    if not isinstance(mapping, dict):
        raise HTTPException(status_code=400, detail="speaker_map must be an object")
    record = session.set_speaker_map(mapping)
    await broadcast(
        request.app,
        meeting_id,
        {"type": "speaker_map", "meeting_id": meeting_id, "speaker_map": record.speaker_map},
    )
    return record.to_dict()


@router.patch("/meetings/{meeting_id}/segments/{segment_id}")
async def update_segment_text(
    request: Request,
    segment_id: str,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    """Edit finalized (non-provisional) segment text. Body: {"text": "..."}."""
    meeting_id = meeting.id
    store = request.app.state.manager.store
    try:
        raw = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="expected object")
    if "text" not in raw:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        updated = store.update_segment_text(meeting_id, segment_id, str(raw["text"]))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="segment not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = {
        "type": "segment_update",
        "meeting_id": meeting_id,
        "segments": [s.to_dict() for s in updated],
    }
    await broadcast(request.app, meeting_id, payload)
    return {
        "meeting_id": meeting_id,
        "segments": [s.to_dict() for s in updated],
    }


@router.get("/meetings/{meeting_id}/speakers")
async def list_speakers(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    """List distinct speakers with current label + a suggested sample span for playback."""
    meeting_id = meeting.id
    store = request.app.state.manager.store

    settings = request.app.state.settings
    has_recording = (
        _resolve_recording_path(
            meeting_id,
            meeting.audio_path,
            audio_dir=settings.audio_dir,
            upload_dir=settings.upload_dir,
        )
        is not None
    )

    segments = store.get_segments(meeting_id)
    final_segments = [s for s in segments if not s.provisional]
    intervals = store.get_speaker_intervals(meeting_id)

    speaker_ids = set()
    for s in final_segments:
        if s.speaker_id:
            speaker_ids.add(s.speaker_id)
        speaker_ids.update(x for x in (s.overlap_speakers or []) if x)
    for iv in intervals:
        if iv.speaker_id:
            speaker_ids.add(iv.speaker_id)

    out = []
    for spk in sorted(speaker_ids):
        sample_start_ms, sample_end_ms = _pick_speaker_sample_span(
            speaker_id=spk,
            intervals=intervals,
            segments=final_segments,
        )
        has_sample = bool(
            has_recording and sample_start_ms is not None and sample_end_ms is not None
        )
        # Naming UI is useless without a playable clip — skip ghost speakers.
        if not has_sample:
            continue

        out.append(
            {
                "id": spk,
                "label": meeting.speaker_map.get(spk) or _default_speaker_label(spk),
                "custom_label": meeting.speaker_map.get(spk),
                "sample_start_ms": sample_start_ms,
                "sample_end_ms": sample_end_ms,
                "has_sample": True,
                "has_recording": has_recording,
            }
        )
    return {"meeting_id": meeting_id, "speakers": out}


@router.get("/meetings/{meeting_id}/speakers/{speaker_id}/audio")
async def get_speaker_sample_audio(
    request: Request,
    speaker_id: str,
    meeting: MeetingRecord = Depends(require_meeting),
):
    """Return a short WAV clip for the given speaker, for naming/preview UI."""
    meeting_id = meeting.id
    store = request.app.state.manager.store

    settings = request.app.state.settings
    path = _resolve_recording_path(
        meeting_id,
        meeting.audio_path,
        audio_dir=settings.audio_dir,
        upload_dir=settings.upload_dir,
    )
    stream_path = meeting.stream_paths.get(speaker_id)
    if stream_path:
        per_stream = _resolve_recording_path(
            meeting_id,
            stream_path,
            audio_dir=settings.audio_dir,
            upload_dir=settings.upload_dir,
        )
        if per_stream:
            path = per_stream
    if not path:
        raise HTTPException(status_code=404, detail="recording not found")

    intervals = [
        iv
        for iv in store.get_speaker_intervals(meeting_id)
        if iv.speaker_id == speaker_id and not iv.is_overlap
    ]
    segments = [
        s
        for s in store.get_segments(meeting_id)
        if s.speaker_id == speaker_id and not s.provisional and not s.is_overlap
    ]
    start_ms, end_ms = _pick_speaker_sample_span(
        speaker_id=speaker_id,
        intervals=intervals,
        segments=segments,
    )

    if start_ms is None or end_ms is None:
        raise HTTPException(status_code=404, detail="no sample available for speaker")

    try:
        wav_bytes = AudioIngest.slice_wav_file(path, start_ms, end_ms)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"could not slice audio: {exc}") from exc

    if not wav_bytes:
        raise HTTPException(status_code=404, detail="empty audio sample")

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(wav_bytes)),
        },
    )


@router.post("/meetings/{meeting_id}/stop")
async def stop_meeting(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    meeting_id = meeting.id
    session = request.app.state.manager.get_or_restore(meeting_id)
    if not session:
        raise HTTPException(status_code=404, detail="meeting not found")

    # Deliberately do NOT cancel on client disconnect: a dropped connection
    # (navigation, refresh, flaky network, server restart racing the
    # request) is not the same as the user pressing cancel. Processing
    # keeps running and persisting to completion; only the explicit
    # POST /meetings/{id}/cancel route wipes the meeting.
    try:
        record = await session.stop()
    except ProcessingCancelled:
        record = session.record
        raise HTTPException(status_code=409, detail="processing cancelled") from None
    except asyncio.CancelledError:
        record = await session.cancel()
        raise HTTPException(status_code=409, detail="processing cancelled") from None

    settings = request.app.state.settings
    path = _resolve_recording_path(
        meeting_id,
        record.audio_path,
        audio_dir=settings.audio_dir,
        upload_dir=settings.upload_dir,
    )
    return _meeting_payload(record, has_recording=path is not None)


@router.post("/meetings/{meeting_id}/cancel")
async def cancel_meeting(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    meeting_id = meeting.id
    manager = request.app.state.manager
    record = await manager.cancel_meeting(meeting_id)
    if not record:
        raise HTTPException(status_code=404, detail="meeting not found")
    await broadcast(
        request.app,
        meeting_id,
        {
            "type": "status",
            "status": record.status.value,
            "meeting_id": meeting_id,
            "cancelled": True,
        },
    )
    settings = request.app.state.settings
    path = _resolve_recording_path(
        meeting_id,
        record.audio_path,
        audio_dir=settings.audio_dir,
        upload_dir=settings.upload_dir,
    )
    return _meeting_payload(record, has_recording=path is not None)


@router.get("/meetings/{meeting_id}/transcript")
async def get_transcript(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    meeting_id = meeting.id
    segments = request.app.state.manager.store.get_segments(meeting_id)
    return {"meeting_id": meeting_id, "segments": [s.to_dict() for s in segments]}


@router.get("/meetings/{meeting_id}/debug")
async def get_meeting_debug(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    meeting_id = meeting.id
    session = request.app.state.manager.get(meeting_id)
    if session is not None:
        return session.debug_stats()
    # Session already gone — return stored meeting metadata + empty counters
    return {
        "meeting_id": meeting_id,
        "chunks_processed": 0,
        "chunks_retried": 0,
        "chunks_dropped": 0,
        "stt_calls": 0,
        "stt_retries": 0,
        "stt_dropped": 0,
        "stt_total_ms": 0,
        "stt_cache_hits": 0,
        "stt_cache_misses": 0,
        "stt_cache_hit_rate": None,
        "diarization_backend": getattr(
            request.app.state.manager.diarizer, "backend", "unknown"
        ),
        "speakers_detected": 0,
        "speaker_ids": [],
        "tuning": dict(request.app.state.tuning),
        "note": "session not in memory; counters unavailable",
    }


@router.post("/meetings/{meeting_id}/upload")
async def upload_audio(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
    file: UploadFile = File(...),
) -> Dict[str, Any]:
    meeting_id = meeting.id
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
    data = await file.read()
    size = len(data)
    if size == 0:
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > 0:
            detail = "فایل در مسیر آپلود به سرور ناقص رسید"
        else:
            detail = "فایل صوتی خالی است"
        logger.warning(
            "Empty upload for meeting %s (filename=%s content-type=%s content-length=%s)",
            meeting_id,
            filename,
            file.content_type,
            content_length,
        )
        raise HTTPException(status_code=400, detail=detail)

    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    with open(dest, "wb") as out:
        out.write(data)

    logger.info("Uploaded %s (%d bytes) for meeting %s", dest, size, meeting_id)

    # Deliberately do NOT cancel on client disconnect: a dropped connection
    # (navigation, refresh, flaky network, server restart racing the
    # request) is not the same as the user pressing cancel. Processing
    # keeps running and persisting to completion; only the explicit
    # POST /meetings/{id}/cancel route wipes the meeting.
    try:
        segments = await session.process_uploaded_file(dest)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProcessingCancelled:
        raise HTTPException(status_code=409, detail="processing cancelled") from None
    except asyncio.CancelledError:
        await session.cancel(extra_paths=[dest])
        raise HTTPException(status_code=409, detail="processing cancelled") from None
    return {
        "meeting_id": meeting_id,
        "audio_path": dest,
        "segments": [s.to_dict() for s in segments],
    }


@router.post("/meetings/{meeting_id}/insights")
async def generate_insights(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    meeting_id = meeting.id
    store = request.app.state.manager.store
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
async def get_insights(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    result = request.app.state.manager.store.get_insights(meeting.id)
    if not result:
        raise HTTPException(status_code=404, detail="insights not found")
    return result.to_dict()


@router.post("/meetings/{meeting_id}/minutes/generate")
async def generate_minutes(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    meeting_id = meeting.id
    store = request.app.state.manager.store
    segments = store.get_segments(meeting_id)
    final = [s for s in segments if not s.provisional] or segments
    try:
        result = await request.app.state.minutes.generate(
            meeting_id,
            final,
            speaker_map=meeting.speaker_map,
            participants=meeting.participants,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        # Defense in depth: never leak a raw 500 for LLM/network failures.
        logger.exception("Minutes generation failed for %s", meeting_id)
        raise HTTPException(
            status_code=502,
            detail=(
                "تولید صورت جلسه ناموفق بود. "
                f"({type(exc).__name__}: {exc})"
            ),
        ) from exc
    store.save_minutes(result)
    await broadcast(
        request.app, meeting_id, {"type": "minutes", "minutes": result.to_dict()}
    )
    return result.to_dict()


@router.get("/meetings/{meeting_id}/minutes")
async def get_minutes(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    result = request.app.state.manager.store.get_minutes(meeting.id)
    if not result:
        raise HTTPException(status_code=404, detail="minutes not found")
    return result.to_dict()


@router.put("/meetings/{meeting_id}/minutes")
async def update_minutes(
    request: Request,
    meeting: MeetingRecord = Depends(require_meeting),
) -> Dict[str, Any]:
    """Persist a fully user-edited minutes document (no LLM call)."""
    meeting_id = meeting.id
    store = request.app.state.manager.store
    try:
        raw = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="expected object")

    existing = store.get_minutes(meeting_id)
    decisions_raw = raw.get("decisions") or []
    if not isinstance(decisions_raw, list):
        raise HTTPException(status_code=400, detail="decisions must be a list")

    minutes_kwargs: Dict[str, Any] = dict(
        meeting_id=meeting_id,
        subject=str(raw.get("subject") or "").strip(),
        meeting_date=str(raw.get("meeting_date") or "").strip(),
        location=str(raw.get("location") or "").strip(),
        attendees=[str(a).strip() for a in (raw.get("attendees") or []) if str(a).strip()],
        absentees=[str(a).strip() for a in (raw.get("absentees") or []) if str(a).strip()],
        secretary=str(raw.get("secretary") or "").strip(),
        summary=str(raw.get("summary") or "").strip(),
        decisions=[MinutesDecision.from_dict(d) for d in decisions_raw if isinstance(d, dict)],
        raw_json=(existing.raw_json if existing else None),
    )
    if existing:
        minutes_kwargs["created_at"] = existing.created_at
    minutes = MeetingMinutes(**minutes_kwargs)
    store.save_minutes(minutes)
    if meeting.status != MeetingStatus.STOPPED:
        meeting.status = MeetingStatus.STOPPED
        if meeting.stopped_at is None:
            meeting.stopped_at = time.time()
        store.save_meeting(meeting)
    await broadcast(
        request.app, meeting_id, {"type": "minutes", "minutes": minutes.to_dict()}
    )
    return minutes.to_dict()


async def _ws_send_json(ws: WebSocket, payload: dict) -> None:
    """Best-effort JSON send; never raise if the socket is already closing."""
    try:
        await ws.send_json(payload)
    except Exception:
        logger.debug("websocket send skipped (socket closing)", exc_info=True)


@router.websocket("/meetings/{meeting_id}/audio")
async def audio_ws(websocket: WebSocket, meeting_id: str) -> None:
    app = websocket.app
    user = await authenticate_websocket(websocket)
    if not user:
        await websocket.close(code=4401, reason="not authenticated")
        return

    meeting = app.state.manager.store.get_meeting(meeting_id)
    if not meeting or meeting.user_id != user.id:
        await websocket.close(code=4403, reason="forbidden")
        return

    await websocket.accept()

    async def on_event(event: Dict[str, Any]) -> None:
        if "meeting_id" not in event:
            event = {**event, "meeting_id": meeting_id}
        await broadcast(app, meeting_id, event)

    session = app.state.manager.get_or_restore(meeting_id, on_event=on_event)
    if not session:
        await _ws_send_json(websocket, {"type": "error", "message": "meeting not found"})
        try:
            await websocket.close()
        except Exception:
            pass
        return

    app.state.ws_by_meeting.setdefault(meeting_id, set()).add(websocket)
    await _ws_send_json(
        websocket,
        {
            "type": "status",
            "status": session.record.status.value,
            "meeting_id": meeting_id,
        },
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
                    await _ws_send_json(
                        websocket, {"type": "error", "message": "invalid json"}
                    )
                    continue
                await _handle_ws_message(session, websocket, payload)
            elif "bytes" in message and message["bytes"] is not None:
                raw = message["bytes"]
                if session.is_multi_stream:
                    speaker_id, samples = _parse_multistream_pcm_frame(raw)
                    if not speaker_id:
                        await _ws_send_json(
                            websocket,
                            {
                                "type": "error",
                                "message": (
                                    "multi_stream binary frame must be "
                                    "[u8 name_len][utf8 speaker_id][int16le pcm]"
                                ),
                            },
                        )
                        continue
                    await session.append_stream_audio_int16(speaker_id, samples)
                else:
                    samples = np.frombuffer(raw, dtype=np.int16)
                    await session.append_audio_int16(samples)
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:
        # e.g. send/receive after close during proxy teardown
        logger.debug("websocket runtime error for %s: %s", meeting_id, exc)
    finally:
        sockets = app.state.ws_by_meeting.get(meeting_id, set())
        sockets.discard(websocket)
        # Tab close / refresh never hits POST /stop. Finalize in a detached
        # task: awaiting stop() here is cancelled with the WebSocket ASGI task
        # and would leave the meeting stuck as processing/recording.
        if not sockets and (
            session._running or session._stopping or session._pipeline_active
        ):
            asyncio.create_task(
                _auto_finalize_after_disconnect(
                    app.state.manager, session, meeting_id
                ),
                name=f"auto-finalize-{meeting_id}",
            )


def _parse_multistream_pcm_frame(
    raw: bytes,
) -> tuple[Optional[str], np.ndarray]:
    """Parse [u8 name_len][utf8 speaker_id][int16le pcm] binary frames."""
    if not raw or len(raw) < 2:
        return None, np.zeros(0, dtype=np.int16)
    name_len = raw[0]
    if name_len < 1 or name_len > 64 or len(raw) < 1 + name_len:
        return None, np.zeros(0, dtype=np.int16)
    try:
        speaker_id = raw[1 : 1 + name_len].decode("utf-8").strip()
    except UnicodeDecodeError:
        return None, np.zeros(0, dtype=np.int16)
    if not speaker_id:
        return None, np.zeros(0, dtype=np.int16)
    pcm = raw[1 + name_len :]
    if len(pcm) % 2:
        pcm = pcm[:-1]
    samples = np.frombuffer(pcm, dtype=np.int16) if pcm else np.zeros(0, dtype=np.int16)
    return speaker_id, samples


async def _auto_finalize_after_disconnect(
    manager, session, meeting_id: str
) -> None:
    """Best-effort finalize when the last client disconnects (refresh/tab close).

    Unlike explicit POST /cancel, this must preserve captured work.
    """
    try:
        if session._running and not session._stopping:
            await session.stop()
        elif session._stopping or session._pipeline_active:
            # Stop/upload processing already in flight — let it finish.
            return
        else:
            record = manager.store.get_meeting(meeting_id)
            if record is not None:
                manager.heal_orphaned_recording(record)
    except Exception:
        logger.exception(
            "Auto-finalize after WebSocket disconnect failed for %s", meeting_id
        )


async def _handle_ws_message(session, ws: WebSocket, payload: dict) -> None:
    msg_type = payload.get("type")
    if msg_type == "audio":
        speaker_id = payload.get("speaker_id")
        samples = np.asarray(payload.get("data") or [], dtype=np.int16)
        if session.is_multi_stream:
            if not speaker_id:
                await _ws_send_json(
                    ws,
                    {"type": "error", "message": "speaker_id is required for multi_stream"},
                )
                return
            await session.append_stream_audio_int16(str(speaker_id), samples)
        else:
            await session.append_audio_int16(samples)
    elif msg_type == "register_stream":
        if not session.is_multi_stream:
            await _ws_send_json(
                ws,
                {
                    "type": "error",
                    "message": "register_stream requires multi_stream capture_mode",
                },
            )
            return
        speaker_id = str(payload.get("speaker_id") or payload.get("id") or "").strip()
        name = str(payload.get("name") or payload.get("label") or "").strip() or None
        if not speaker_id:
            await _ws_send_json(ws, {"type": "error", "message": "speaker_id is required"})
            return
        sid = session.register_stream(speaker_id, name=name)
        await _ws_send_json(
            ws,
            {
                "type": "stream_registered",
                "speaker_id": sid,
                "name": session.record.speaker_map.get(sid),
            },
        )
    elif msg_type == "stop":
        record = await session.stop()
        await _ws_send_json(
            ws,
            {
                "type": "status",
                "status": record.status.value,
                "meeting_id": session.meeting_id,
            },
        )
    elif msg_type == "ping":
        await _ws_send_json(ws, {"type": "pong"})
    else:
        await _ws_send_json(
            ws, {"type": "error", "message": f"unknown type: {msg_type}"}
        )
