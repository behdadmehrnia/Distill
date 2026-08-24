from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI

from api.config import Settings
from api.meeting.diarization import SpeakerDiarizer
from api.meeting.insights import MeetingInsightsGenerator
from api.meeting.minutes import MeetingMinutesGenerator
from api.meeting.review import TranscriptReviewAgent
from api.meeting.session import MeetingManager
from api.meeting.store import TranscriptStore
from api.providers.llm import OpenAICompatibleLLM
from api.providers.stt import OpenAICompatibleSTT
from api.routes import setup_routes
from api.tuning import make_tuning
from api.auth.store import UserStore

logger = logging.getLogger(__name__)


def _build_services(settings: Settings) -> Dict[str, Any]:
    settings.ensure_dirs()
    tuning = make_tuning(
        {
            "window_ms": settings.window_ms,
            "hop_ms": settings.hop_ms,
            "diarize_every_ms": settings.diarize_every_ms,
        }
    )

    stt_kwargs: Dict[str, Any] = {
        "cache_dir": str(settings.stt_cache_dir),
        "sample_rate": settings.sample_rate,
    }
    if settings.stt_endpoint:
        stt_kwargs["endpoint"] = settings.stt_endpoint
    if settings.stt_api_key:
        stt_kwargs["api_key"] = settings.stt_api_key
    if settings.stt_model:
        stt_kwargs["model"] = settings.stt_model

    if not settings.stt_api_key:
        logger.warning(
            "STT_API_KEY is not set; GapGPT STT will return 401 (no token provided)"
        )

    stt = OpenAICompatibleSTT(**stt_kwargs)
    llm = OpenAICompatibleLLM(
        endpoint=settings.llm_endpoint,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    store = TranscriptStore(database_url=settings.database_url)
    user_store = UserStore(database_url=settings.database_url)
    # Never load pyannote/torch here — it OOMs small pods and blocks readiness.
    # Prefer DIARIZATION_ENDPOINT (remote sidecar) when set.
    diarizer = SpeakerDiarizer(
        sample_rate=settings.sample_rate,
        hf_token=settings.hf_token,
        remote_endpoint=settings.diarization_endpoint,
        remote_timeout_s=settings.diarization_timeout_s,
        allow_fallback=settings.diarization_allow_fallback,
        min_speakers=int(tuning["min_speakers"]),
        max_speakers=int(tuning["max_speakers"]),
        energy_threshold=float(tuning["energy_threshold"]),
        merge_short_ms=int(tuning["merge_short_ms"]),
    )
    insights = MeetingInsightsGenerator(llm)
    minutes_generator = MeetingMinutesGenerator(llm)
    review_agent = TranscriptReviewAgent(llm)
    manager = MeetingManager(
        store=store,
        stt_provider=stt,
        diarizer=diarizer,
        review_agent=review_agent,
        tuning=tuning,
        sample_rate=settings.sample_rate,
        window_ms=int(tuning["window_ms"]),
        hop_ms=int(tuning["hop_ms"]),
        diarize_every_ms=int(tuning["diarize_every_ms"]),
        audio_dir=str(settings.audio_dir),
        upload_denoise_enabled=settings.upload_denoise_enabled,
    )
    return {
        "settings": settings,
        "tuning": tuning,
        "manager": manager,
        "user_store": user_store,
        "insights": insights,
        "minutes": minutes_generator,
        "diarizer": diarizer,
        "ws_by_meeting": {},
    }


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Application factory for Distill API (FastAPI / uvicorn)."""
    settings = settings or Settings.from_env()
    services = _build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Keep startup empty of torch/pyannote. Optionally ping the local sidecar.
        diarizer = getattr(app.state.manager, "diarizer", None)
        backend_hint = "fallback"
        if diarizer is not None and getattr(diarizer, "remote_endpoint", None):
            backend_hint = f"sidecar ({diarizer.remote_endpoint})"
            try:
                # Cheap health bind — no model load in this process.
                diarizer.ensure_loaded()
            except Exception as exc:
                logger.warning("Diarization sidecar bind deferred: %s", exc)
        elif diarizer is not None and getattr(diarizer, "pyannote_enabled", False):
            backend_hint = "pyannote/nemo (lazy)"
        logger.info(
            "Distill HTTP starting (diarization=%s; quality model not loaded in API)",
            backend_hint,
        )
        yield

    app = FastAPI(
        title="Distill API",
        version="0.1.0",
        description="Meeting assistant — live STT, diarization, insights",
        lifespan=lifespan,
    )
    app.state.settings = services["settings"]
    app.state.tuning = services["tuning"]
    app.state.manager = services["manager"]
    app.state.user_store = services["user_store"]
    app.state.insights = services["insights"]
    app.state.minutes = services["minutes"]
    app.state.ws_by_meeting = services["ws_by_meeting"]

    setup_routes(app)
    return app
