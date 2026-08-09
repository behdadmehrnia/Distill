from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import wave
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import numpy as np

from api.providers.http_util import client_session

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gapgpt/whisper-1"
DEFAULT_ENDPOINT = "https://api.gapgpt.app/v1/audio/transcriptions"
_MAX_ERROR_BODY = 240

# Models known to reject response_format=verbose_json (OpenAI transcribe family).
_NO_VERBOSE_JSON_MARKERS = (
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
    "gpt-4o-transcribe-diarize",
)


def _model_supports_verbose_json(model: str) -> bool:
    name = (model or "").lower()
    return not any(marker in name for marker in _NO_VERBOSE_JSON_MARKERS)


def _format_http_error(status: int, body: str, content_type: str = "") -> str:
    """Compact STT HTTP errors; avoid logging full HTML 404 pages."""
    text = (body or "").strip()
    ctype = (content_type or "").lower()
    looks_html = "text/html" in ctype or text[:32].lower().startswith(
        ("<!doctype", "<html")
    )
    if looks_html:
        title = ""
        lower = text.lower()
        start = lower.find("<title>")
        end = lower.find("</title>")
        if 0 <= start < end:
            title = " ".join(text[start + 7 : end].split())
        hint = f" ({title})" if title else ""
        return f"STT error {status}: HTML error page{hint}"
    if len(text) > _MAX_ERROR_BODY:
        text = text[:_MAX_ERROR_BODY].rstrip() + "…"
    return f"STT error {status}: {text or '(empty body)'}"


@dataclass
class TimedWord:
    """Word with times relative to the audio chunk (seconds)."""

    word: str
    start_s: float
    end_s: float


@dataclass
class STTResult:
    text: str
    words: List[TimedWord] = field(default_factory=list)

    @property
    def has_timings(self) -> bool:
        return bool(self.words)


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
        self.cache_hits = 0
        self.cache_misses = 0
        self._verbose_supported: Optional[bool] = None
        os.makedirs(cache_dir, exist_ok=True)

    def _pcm_to_wav(self, pcm_content: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.sample_rate)
            wav_file.writeframes(pcm_content)
        return buf.getvalue()

    @staticmethod
    def _file_hash(content: bytes) -> str:
        return hashlib.md5(content).hexdigest()

    def _cache_paths(self, pcm_hash: str) -> Tuple[str, str]:
        base = os.path.join(self.cache_dir, pcm_hash)
        return f"{base}.txt", f"{base}.json"

    @staticmethod
    def _parse_words(payload: Dict[str, Any]) -> List[TimedWord]:
        words: List[TimedWord] = []
        raw_words = payload.get("words")
        if isinstance(raw_words, list):
            for item in raw_words:
                if not isinstance(item, dict):
                    continue
                w = str(item.get("word") or item.get("text") or "").strip()
                if not w:
                    continue
                try:
                    start = float(item.get("start", 0.0))
                    end = float(item.get("end", start))
                except (TypeError, ValueError):
                    continue
                words.append(TimedWord(word=w, start_s=start, end_s=max(end, start)))
            if words:
                return words

        # Fall back to segment-level timings (coarser but still useful)
        segments = payload.get("segments")
        if isinstance(segments, list):
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                text = str(seg.get("text") or "").strip()
                if not text:
                    continue
                try:
                    start = float(seg.get("start", 0.0))
                    end = float(seg.get("end", start))
                except (TypeError, ValueError):
                    continue
                # Split segment text into pseudo-words with equal time share
                toks = text.split()
                if not toks:
                    continue
                dur = max(end - start, 0.01)
                step = dur / len(toks)
                for i, tok in enumerate(toks):
                    words.append(
                        TimedWord(
                            word=tok,
                            start_s=start + i * step,
                            end_s=start + (i + 1) * step,
                        )
                    )
        return words

    @staticmethod
    def _normalize_payload(payload: Any) -> Dict[str, Any]:
        """Accept dict JSON or bare string bodies from OpenAI-compatible APIs."""
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            return {"text": payload}
        return {}

    async def _post_transcribe(
        self,
        wav_content: bytes,
        model: Optional[str],
        language: Optional[str],
        *,
        response_format: str,
    ) -> Dict[str, Any]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        form = aiohttp.FormData()
        form.add_field(
            "file",
            wav_content,
            filename="audio.wav",
            content_type="audio/wav",
        )
        form.add_field("language", language or "fa")
        form.add_field("model", model or self.model)
        form.add_field("response_format", response_format)
        if response_format == "verbose_json":
            # OpenAI-compatible optional hint; ignored by endpoints that don't support it
            form.add_field("timestamp_granularities[]", "word")

        async with client_session() as session:
            async with session.post(self.endpoint, data=form, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    ctype = response.headers.get("Content-Type", "")
                    raise RuntimeError(
                        _format_http_error(response.status, error_text, ctype)
                    )
                ctype = (response.headers.get("Content-Type") or "").lower()
                if response_format == "text" or "application/json" not in ctype:
                    body = await response.text()
                    # Some gateways still return JSON even when text was requested
                    try:
                        return self._normalize_payload(json.loads(body))
                    except json.JSONDecodeError:
                        return {"text": body}
                return self._normalize_payload(await response.json())

    async def transcribe_detailed(
        self,
        file_content: np.ndarray,
        model: Optional[str] = None,
        language: Optional[str] = "fa",
    ) -> STTResult:
        audio_int16 = (np.asarray(file_content, dtype=np.float32) * 32768.0).astype(np.int16)
        pcm_bytes = audio_int16.tobytes()
        pcm_hash = self._file_hash(pcm_bytes)
        text_cache, json_cache = self._cache_paths(pcm_hash)

        if os.path.exists(json_cache):
            self.cache_hits += 1
            try:
                with open(json_cache, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                words = [
                    TimedWord(w["word"], float(w["start_s"]), float(w["end_s"]))
                    for w in cached.get("words") or []
                ]
                return STTResult(text=str(cached.get("text") or "").strip(), words=words)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass

        if os.path.exists(text_cache) and not os.path.exists(json_cache):
            self.cache_hits += 1
            with open(text_cache, "r", encoding="utf-8") as f:
                return STTResult(text=f.read().strip(), words=[])

        self.cache_misses += 1
        wav_content = self._pcm_to_wav(pcm_bytes)
        chosen_model = model or self.model

        result: Dict[str, Any] = {}
        words: List[TimedWord] = []
        # Skip probing models that never support word timestamps.
        if self._verbose_supported is None and not _model_supports_verbose_json(
            chosen_model
        ):
            self._verbose_supported = False

        want_verbose = self._verbose_supported is not False
        if want_verbose:
            try:
                result = await self._post_transcribe(
                    wav_content,
                    model,
                    language,
                    response_format="verbose_json",
                )
                words = self._parse_words(result)
                self._verbose_supported = True
            except Exception as exc:
                logger.info(
                    "verbose_json STT unavailable (%s); falling back to json", exc
                )
                self._verbose_supported = False
                result = {}

        if not result:
            result = await self._post_transcribe(
                wav_content, model, language, response_format="json"
            )
            words = self._parse_words(result)

        transcription = (result.get("text") or result.get("transcription") or "").strip()
        out = STTResult(text=transcription, words=words)

        with open(text_cache, "w", encoding="utf-8") as f:
            f.write(transcription)
        with open(json_cache, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "text": transcription,
                    "words": [
                        {"word": w.word, "start_s": w.start_s, "end_s": w.end_s}
                        for w in words
                    ],
                },
                f,
                ensure_ascii=False,
            )
        return out

    async def transcribe(
        self,
        file_content: np.ndarray,
        model: Optional[str] = None,
        language: Optional[str] = "fa",
    ) -> str:
        result = await self.transcribe_detailed(
            file_content, model=model, language=language
        )
        return result.text

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
