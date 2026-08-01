from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent.parent

# Fixed on-disk layout (not env-configurable) so Docker volume `/app/data` always matches.
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "meetings.db"
UPLOAD_DIR = DATA_DIR / "uploads"
AUDIO_DIR = DATA_DIR / "audio"
STT_CACHE_DIR = DATA_DIR / "stt_cache"
WEB_DIR = ROOT_DIR / "api" / "web"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Paths are fixed; secrets/endpoints come from the environment."""

    host: str = "0.0.0.0"
    port: int = 8000
    db_path: Path = DB_PATH
    upload_dir: Path = UPLOAD_DIR
    audio_dir: Path = AUDIO_DIR
    web_dir: Path = WEB_DIR
    stt_cache_dir: Path = STT_CACHE_DIR

    sample_rate: int = 16000
    channels: int = 1
    window_ms: int = 6000
    hop_ms: int = 1500
    diarize_every_ms: int = 20000

    stt_endpoint: str | None = None
    stt_api_key: str | None = None
    stt_model: str | None = None

    llm_endpoint: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None

    hf_token: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        # Prefer MEETING_PORT; fall back to PORT (common on PaaS like Hamdocker)
        port = _env_int("MEETING_PORT", 0) or _env_int("PORT", 8000)
        return cls(
            host=_env_str("MEETING_HOST", "0.0.0.0") or "0.0.0.0",
            port=port,
            db_path=DB_PATH,
            upload_dir=UPLOAD_DIR,
            audio_dir=AUDIO_DIR,
            web_dir=WEB_DIR,
            stt_cache_dir=STT_CACHE_DIR,
            sample_rate=_env_int("AUDIO_SAMPLE_RATE", 16000),
            channels=_env_int("AUDIO_CHANNELS", 1),
            window_ms=_env_int("MEETING_WINDOW_MS", 6000),
            hop_ms=_env_int("MEETING_HOP_MS", 1500),
            diarize_every_ms=_env_int("MEETING_DIARIZE_EVERY_MS", 20000),
            stt_endpoint=_env_str("STT_ENDPOINT"),
            stt_api_key=_env_str("STT_API_KEY")
            or _env_str("GAP_TOKEN")
            or _env_str("LLM_API_KEY"),
            stt_model=_env_str("STT_MODEL"),
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
