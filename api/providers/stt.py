from __future__ import annotations

import hashlib
import io
import logging
import os
import wave
from typing import Optional

import aiohttp
import numpy as np

from api.providers.http_util import client_session

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gapgpt/whisper-1"
DEFAULT_ENDPOINT = "https://api.gapgpt.app/v1/audio/transcriptions"


class OpenAICompatibleSTT:
    """OpenAI-compatible speech-to-text client."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        api_key: str = "",
        cache_dir: str = "./data/stt_cache",
        sample_rate: int = 16000,
        model: str = DEFAULT_MODEL,
    ):
        self.endpoint = endpoint
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.sample_rate = sample_rate
        self.model = model
        os.makedirs(cache_dir, exist_ok=True)

    def _pcm_to_wav(self, pcm_content: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm_content)
        return buf.getvalue()

    def _wav_to_mp3(self, wav_content: bytes) -> bytes:
        try:
            from pydub import AudioSegment
        except ImportError as exc:
            raise ImportError(
                "pydub is required for STT encoding. Install pydub and audioop-lts."
            ) from exc
        audio = AudioSegment.from_wav(io.BytesIO(wav_content))
        out = io.BytesIO()
        audio.export(out, format="mp3", bitrate="128k")
        return out.getvalue()

    @staticmethod
    def _file_hash(content: bytes) -> str:
        return hashlib.md5(content).hexdigest()

    async def transcribe(
        self,
        file_content: np.ndarray,
        model: Optional[str] = None,
        language: Optional[str] = "fa",
    ) -> str:
        audio_int16 = (np.asarray(file_content, dtype=np.float32) * 32768.0).astype(np.int16)
        pcm_bytes = audio_int16.tobytes()

        cache_file = os.path.join(self.cache_dir, f"{self._file_hash(pcm_bytes)}.txt")
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                return f.read().strip()

        wav_content = self._pcm_to_wav(pcm_bytes)
        mp3_content = self._wav_to_mp3(wav_content)

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        form = aiohttp.FormData()
        form.add_field(
            "file",
            mp3_content,
            filename="audio.mp3",
            content_type="audio/mpeg",
        )
        form.add_field("language", language or "fa")
        form.add_field("model", model or self.model)

        async with client_session() as session:
            async with session.post(self.endpoint, data=form, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise RuntimeError(f"STT error {response.status}: {error_text}")
                result = await response.json()
                transcription = (result.get("text") or result.get("transcription") or "").strip()

        with open(cache_file, "w", encoding="utf-8") as f:
            f.write(transcription)
        return transcription

    def is_persian_valid(self, text: str) -> bool:
        if not text or not isinstance(text, str):
            return False
        persian_chars = set("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیئ")
        has_persian = any(char in persian_chars for char in text)
        latin_count = sum(
            1 for char in text.lower() if char in "abcdefghijklmnopqrstuvwxyz"
        )
        latin_ratio = latin_count / max(len(text), 1)
        return has_persian and latin_ratio < 0.3
