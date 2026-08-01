from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI

from api.config import Settings
from api.meeting.diarization import SpeakerDiarizer
from api.meeting.insights import MeetingInsightsGenerator
from api.meeting.review import TranscriptReviewAgent
from api.meeting.session import MeetingManager
from api.meeting.store import TranscriptStore
from api.providers.llm import OpenAICompatibleLLM
from api.providers.stt import OpenAICompatibleSTT
from api.routes import setup_routes
from api.tuning import make_tuning

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
    store = TranscriptStore(db_path=str(settings.db_path))
    # Do not load pyannote here — it blocks uvicorn from binding and fails K8s probes.
    diarizer = SpeakerDiarizer(
        sample_rate=settings.sample_rate,
        hf_token=settings.hf_token,
        min_speakers=int(tuning["min_speakers"]),
        max_speakers=int(tuning["max_speakers"]),
        energy_threshold=float(tuning["energy_threshold"]),
        merge_short_ms=int(tuning["merge_short_ms"]),
    )
    insights = MeetingInsightsGenerator(llm)
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
    )
    return {
        "settings": settings,
        "tuning": tuning,
        "manager": manager,
        "insights": insights,
        "diarizer": diarizer,
        "ws_by_meeting": {},
    }


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    """Application factory for Distill API (FastAPI / uvicorn)."""
    settings = settings or Settings.from_env()
    services = _build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        diarizer = getattr(app.state.manager, "diarizer", None)
        warm_task: Optional[asyncio.Task[None]] = None

        async def _warm_diarizer() -> None:
            if diarizer is None:
                return
            logger.info("Warming diarization model in background…")
            backend = await asyncio.to_thread(diarizer.ensure_loaded)
            if backend != "pyannote":
                logger.warning(
                    "Distill ready with FALLBACK diarization (backend=%s). "
                    "Speaker labels will be weak. Install requirements.optional.txt "
                    "and set HF_TOKEN for pyannote quality.",
                    backend,
                )
            else:
                logger.info("Distill diarization ready (backend=%s)", backend)

        # Schedule before yield so warm-up runs once the event loop accepts traffic.
        warm_task = asyncio.create_task(_warm_diarizer())
        logger.info("Distill HTTP ready (diarization loading in background)")
        try:
            yield
        finally:
            if warm_task is not None and not warm_task.done():
                warm_task.cancel()
                try:
                    await warm_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(
        title="Distill API",
        version="0.1.0",
        description="Meeting assistant — live STT, diarization, insights",
        lifespan=lifespan,
    )
    app.state.settings = services["settings"]
    app.state.tuning = services["tuning"]
    app.state.manager = services["manager"]
    app.state.insights = services["insights"]
    app.state.ws_by_meeting = services["ws_by_meeting"]

    setup_routes(app)
    logger.info("Distill app created (diarization deferred until after bind)")
    return app


# Default ASGI app for `uvicorn api.app:app`
app = create_app()
