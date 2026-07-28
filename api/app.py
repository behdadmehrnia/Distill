from __future__ import annotations

import logging
from typing import Any, Dict

from aiohttp import web

from api.config import Settings
from api.meeting.diarization import SpeakerDiarizer
from api.meeting.insights import MeetingInsightsGenerator
from api.meeting.session import MeetingManager
from api.meeting.store import TranscriptStore
from api.providers.llm import OpenAICompatibleLLM
from api.providers.stt import OpenAICompatibleSTT
from api.routes import setup_routes

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> web.Application:
    """Application factory for Distill API."""
    settings = settings or Settings.from_env()
    settings.ensure_dirs()

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

    stt = OpenAICompatibleSTT(**stt_kwargs)
    llm = OpenAICompatibleLLM(
        endpoint=settings.llm_endpoint,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
    )
    store = TranscriptStore(db_path=str(settings.db_path))
    diarizer = SpeakerDiarizer(
        sample_rate=settings.sample_rate,
        hf_token=settings.hf_token,
    )
    manager = MeetingManager(
        store=store,
        stt_provider=stt,
        diarizer=diarizer,
        sample_rate=settings.sample_rate,
        window_ms=settings.window_ms,
        hop_ms=settings.hop_ms,
        diarize_every_ms=settings.diarize_every_ms,
        audio_dir=str(settings.audio_dir),
    )
    insights = MeetingInsightsGenerator(llm)

    app = web.Application(client_max_size=200 * 1024 * 1024)
    app["settings"] = settings
    app["manager"] = manager
    app["insights"] = insights
    app["ws_by_meeting"] = {}

    setup_routes(app)
    logger.info("Distill app created (diarization=%s)", diarizer.backend)
    return app
