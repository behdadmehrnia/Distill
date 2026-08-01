from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check(request: Request) -> dict:
    """Liveness/readiness: returns as soon as the HTTP server is up.

    Diarization may still be loading in the background; that must not block probes.
    """
    diarizer = getattr(request.app.state.manager, "diarizer", None)
    backend = getattr(diarizer, "backend", "unknown") if diarizer else "unknown"
    ready = bool(getattr(diarizer, "ready", False)) if diarizer else False
    return {
        "status": "ok",
        "service": "distill",
        "diarization_backend": backend if ready else "loading",
        "diarization_ready": ready,
        "diarization_quality": (
            "high" if ready and backend == "pyannote" else "fallback"
        ),
    }
