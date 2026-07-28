from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return Path(raw)


def _env_str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw


@dataclass(frozen=True)
class Settings:
    """All Distill settings are loaded from environment variables."""

    host: str = "0.0.0.0"
    port: int = 8030
    db_path: Path = ROOT_DIR / "data" / "meetings.db"
    upload_dir: Path = ROOT_DIR / "data" / "uploads"
    audio_dir: Path = ROOT_DIR / "data" / "audio"
    web_dir: Path = ROOT_DIR / "api" / "web"

    sample_rate: int = 16000
    channels: int = 1
    window_ms: int = 8000
    hop_ms: int = 2000
    diarize_every_ms: int = 20000

    stt_endpoint: str | None = None
    stt_api_key: str | None = None
    stt_model: str | None = None
    stt_cache_dir: Path = ROOT_DIR / "data" / "stt_cache"

    llm_endpoint: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None

    hf_token: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=_env_str("MEETING_HOST", "0.0.0.0") or "0.0.0.0",
            port=_env_int("MEETING_PORT", 8030),
            db_path=_env_path("MEETING_DB", ROOT_DIR / "data" / "meetings.db"),
            upload_dir=_env_path("MEETING_UPLOAD_DIR", ROOT_DIR / "data" / "uploads"),
            audio_dir=_env_path("MEETING_AUDIO_DIR", ROOT_DIR / "data" / "audio"),
            web_dir=_env_path("MEETING_WEB_DIR", ROOT_DIR / "api" / "web"),
            sample_rate=_env_int("AUDIO_SAMPLE_RATE", 16000),
            channels=_env_int("AUDIO_CHANNELS", 1),
            window_ms=_env_int("MEETING_WINDOW_MS", 8000),
            hop_ms=_env_int("MEETING_HOP_MS", 2000),
            diarize_every_ms=_env_int("MEETING_DIARIZE_EVERY_MS", 20000),
            stt_endpoint=_env_str("STT_ENDPOINT"),
            stt_api_key=_env_str("STT_API_KEY")
            or _env_str("GAP_TOKEN")
            or _env_str("LLM_API_KEY"),
            stt_model=_env_str("STT_MODEL"),
            stt_cache_dir=_env_path("STT_CACHE_DIR", ROOT_DIR / "data" / "stt_cache"),
            llm_endpoint=_env_str("LLM_ENDPOINT"),
            llm_api_key=_env_str("LLM_API_KEY"),
            llm_model=_env_str("LLM_MODEL_NAME"),
            hf_token=_env_str("HF_TOKEN")
            or _env_str("HUGGINGFACE_TOKEN")
            or _env_str("HUGGING_FACE_HUB_TOKEN"),
        )

    def ensure_dirs(self) -> None:
        for path in (
            self.db_path.parent,
            self.upload_dir,
            self.audio_dir,
            self.stt_cache_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
