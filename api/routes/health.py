from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    diarizer = getattr(request.app.state.manager, "diarizer", None)
    backend = getattr(diarizer, "backend", "unknown") if diarizer else "unknown"
    return {
        "status": "ok",
        "service": "distill",
        "diarization_backend": backend,
        "diarization_quality": "high" if backend == "pyannote" else "fallback",
    }
