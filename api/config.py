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
    database_url: str = "postgresql://distill:distill@127.0.0.1:5432/distill"
    upload_dir: Path = UPLOAD_DIR
    audio_dir: Path = AUDIO_DIR
    web_dir: Path = WEB_DIR
    stt_cache_dir: Path = STT_CACHE_DIR

    sample_rate: int = 16000
    channels: int = 1
    window_ms: int = 10000
    hop_ms: int = 8500
    diarize_every_ms: int = 0

    stt_endpoint: str | None = None
    stt_api_key: str | None = None
    stt_model: str | None = None

    llm_endpoint: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None

    hf_token: str | None = None
    diarization_endpoint: str | None = None
    diarization_timeout_s: float = 120.0
    diarization_allow_fallback: bool | None = None
    upload_denoise_enabled: bool = True

    # --- Auth (JWT in cookie) ---
    jwt_secret: str = "dev-insecure-change-me-please-use-a-long-secret-value-1234567890abcdef"
    jwt_expire_minutes: int = 10080
    auth_cookie_secure: bool = False

    # --- Bootstrap admin account (created/promoted on startup if no admin exists) ---
    admin_username: str | None = None
    admin_password: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        # Prefer MEETING_PORT; fall back to PORT (common on PaaS like Hamdocker)
        port = _env_int("MEETING_PORT", 0) or _env_int("PORT", 8000)
        timeout_raw = _env_str("DIARIZATION_TIMEOUT_S")
        try:
            diarization_timeout_s = float(timeout_raw) if timeout_raw else 120.0
        except ValueError:
            diarization_timeout_s = 120.0

        allow_raw = (_env_str("DIARIZATION_ALLOW_FALLBACK") or "").strip().lower()
        if allow_raw in {"0", "false", "no", "off"}:
            diarization_allow_fallback: bool | None = False
        elif allow_raw in {"1", "true", "yes", "on"}:
            diarization_allow_fallback = True
        else:
            # None → SpeakerDiarizer defaults (no fallback when sidecar is set)
            diarization_allow_fallback = None

        denoise_raw = (_env_str("AUDIO_DENOISE_UPLOAD") or "1").strip().lower()
        upload_denoise_enabled = denoise_raw not in {"0", "false", "no", "off"}

        jwt_secret = (
            _env_str("JWT_SECRET")
            or "dev-insecure-change-me-please-use-a-long-secret-value-1234567890abcdef"
        )
        jwt_expire_minutes = _env_int("JWT_EXPIRE_MINUTES", 10080)

        cookie_secure_raw = (_env_str("AUTH_COOKIE_SECURE") or "0").strip().lower()
        auth_cookie_secure = cookie_secure_raw in {"1", "true", "yes", "on"}

        database_url = (
            _env_str("DATABASE_URL")
            or "postgresql://distill:distill@127.0.0.1:5432/distill"
        )

        admin_username = _env_str("ADMIN_USERNAME")
        admin_password = _env_str("ADMIN_PASSWORD")

        return cls(
            host=_env_str("MEETING_HOST", "0.0.0.0") or "0.0.0.0",
            port=port,
            db_path=DB_PATH,
            database_url=database_url,
            upload_dir=UPLOAD_DIR,
            audio_dir=AUDIO_DIR,
            web_dir=WEB_DIR,
            stt_cache_dir=STT_CACHE_DIR,
            sample_rate=_env_int("AUDIO_SAMPLE_RATE", 16000),
            channels=_env_int("AUDIO_CHANNELS", 1),
            window_ms=_env_int("MEETING_WINDOW_MS", 15000),
            hop_ms=_env_int("MEETING_HOP_MS", 15000),
            diarize_every_ms=_env_int("MEETING_DIARIZE_EVERY_MS", 0),
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
            diarization_endpoint=_env_str("DIARIZATION_ENDPOINT"),
            diarization_timeout_s=diarization_timeout_s,
            diarization_allow_fallback=diarization_allow_fallback,
            upload_denoise_enabled=upload_denoise_enabled,
            jwt_secret=jwt_secret,
            jwt_expire_minutes=jwt_expire_minutes,
            auth_cookie_secure=auth_cookie_secure,
            admin_username=admin_username,
            admin_password=admin_password,
        )

    def ensure_dirs(self) -> None:
        for path in (
            self.db_path.parent,
            self.upload_dir,
            self.audio_dir,
            self.stt_cache_dir,
            # HF/torch caches (Docker sets HF_HOME/TORCH_HOME under /app/data)
            DATA_DIR / "hf_cache",
            DATA_DIR / "torch_cache",
            DATA_DIR / "cache",
        ):
            path.mkdir(parents=True, exist_ok=True)
