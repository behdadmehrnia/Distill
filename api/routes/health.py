from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Liveness/readiness — must stay cheap and never touch torch/pyannote."""
    manager = getattr(request.app.state, "manager", None)
    diarizer = getattr(manager, "diarizer", None) if manager else None
    ready = bool(getattr(diarizer, "ready", False)) if diarizer else False
    backend = getattr(diarizer, "backend", "unknown") if diarizer else "unknown"
    quality_backends = {"pyannote", "nemo"}
    high = ready and backend in quality_backends
    endpoint = getattr(diarizer, "remote_endpoint", None) if diarizer else None
    allow_fallback = getattr(diarizer, "allow_fallback", True) if diarizer else True
    return {
        "status": "ok",
        "service": "distill",
        "diarization_backend": backend if ready else "unloaded",
        "diarization_ready": ready,
        "diarization_quality": "high" if high else "fallback",
        "diarization_endpoint": endpoint,
        "diarization_allow_fallback": bool(allow_fallback),
    }
