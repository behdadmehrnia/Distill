"""OpenAI-compatible Whisper STT server for Distill (faster-whisper).

Exposes:
  GET  /health
  GET  /v1/models
  POST /v1/audio/transcriptions   (multipart: file, model, language, response_format, prompt)
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger("distill-stt")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = (os.getenv("WHISPER_DEVICE") or "auto").strip().lower()
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE_TYPE", "auto")
WHISPER_DOWNLOAD = os.getenv("WHISPER_DOWNLOAD_ROOT") or None


def _resolve_device() -> tuple[str, str]:
    device = WHISPER_DEVICE
    compute = WHISPER_COMPUTE
    try:
        import torch

        if device in {"", "auto"}:
            if torch.cuda.is_available():
                device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                device = "cpu"  # faster-whisper CTranslate2 has no MPS
            else:
                device = "cpu"
        if compute in {"", "auto"}:
            compute = "float16" if device == "cuda" else "int8"
    except Exception:
        if device in {"", "auto"}:
            device = "cpu"
        if compute in {"", "auto"}:
            compute = "int8"
    return device, compute


class WhisperState:
    def __init__(self) -> None:
        self.model: Any = None
        self.device = "cpu"
        self.compute_type = "int8"
        self.error: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.model is not None

    def ensure_loaded(self) -> None:
        with self._lock:
            if self.model is not None or self.error:
                return
            try:
                from faster_whisper import WhisperModel

                self.device, self.compute_type = _resolve_device()
                logger.info(
                    "Loading Whisper %s on %s (%s)",
                    WHISPER_MODEL,
                    self.device,
                    self.compute_type,
                )
                kwargs: Dict[str, Any] = {
                    "device": self.device,
                    "compute_type": self.compute_type,
                }
                if WHISPER_DOWNLOAD:
                    kwargs["download_root"] = WHISPER_DOWNLOAD
                self.model = WhisperModel(WHISPER_MODEL, **kwargs)
                logger.info("Whisper ready")
            except Exception as exc:
                self.error = str(exc)
                logger.exception("Failed to load Whisper")


state = WhisperState()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    threading.Thread(target=state.ensure_loaded, name="whisper-load", daemon=True).start()
    yield


app = FastAPI(
    title="Distill STT",
    version="0.1.0",
    description="OpenAI-compatible Whisper transcription for Distill",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if not state.error else "error",
        "service": "distill-stt",
        "ready": state.ready,
        "model": WHISPER_MODEL,
        "device": state.device,
        "compute_type": state.compute_type,
        "error": state.error,
    }


@app.get("/v1/models")
def list_models() -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": WHISPER_MODEL,
                "object": "model",
                "owned_by": "distill-local",
            }
        ],
    }


def _transcribe_file(
    path: str,
    language: Optional[str],
    prompt: Optional[str],
    word_timestamps: bool,
) -> Dict[str, Any]:
    state.ensure_loaded()
    if not state.ready:
        raise HTTPException(status_code=503, detail=state.error or "STT model not ready")

    segments_iter, info = state.model.transcribe(
        path,
        language=(language or None) or None,
        initial_prompt=(prompt or None) or None,
        word_timestamps=word_timestamps,
        vad_filter=True,
    )
    segments: List[Dict[str, Any]] = []
    words: List[Dict[str, Any]] = []
    texts: List[str] = []
    for seg in segments_iter:
        texts.append(seg.text.strip())
        segments.append(
            {
                "id": len(segments),
                "seek": 0,
                "start": float(seg.start),
                "end": float(seg.end),
                "text": seg.text,
                "tokens": [],
                "temperature": 0.0,
                "avg_logprob": 0.0,
                "compression_ratio": 0.0,
                "no_speech_prob": 0.0,
            }
        )
        if word_timestamps and seg.words:
            for w in seg.words:
                words.append(
                    {
                        "word": w.word,
                        "start": float(w.start),
                        "end": float(w.end),
                    }
                )
    text = " ".join(t for t in texts if t).strip()
    payload: Dict[str, Any] = {
        "text": text,
        "language": getattr(info, "language", language) or language or "en",
        "duration": float(getattr(info, "duration", 0.0) or 0.0),
        "segments": segments,
    }
    if word_timestamps:
        payload["words"] = words
    return payload


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: Optional[str] = Form(None),
    language: Optional[str] = Form("en"),
    prompt: Optional[str] = Form(None),
    response_format: Optional[str] = Form("json"),
    temperature: Optional[str] = Form(None),
) -> Any:
    _ = model, temperature  # OpenAI compatibility; single local model
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio")

    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        path = tmp.name

    try:
        fmt = (response_format or "json").strip().lower()
        want_words = fmt in {"verbose_json", "verbose-json"}
        result = _transcribe_file(path, language, prompt, word_timestamps=want_words)
        if fmt == "text":
            return PlainTextResponse(result.get("text") or "")
        if fmt in {"verbose_json", "verbose-json"}:
            return JSONResponse(result)
        return JSONResponse({"text": result.get("text") or ""})
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main() -> None:
    import uvicorn

    host = os.getenv("STT_HOST", "0.0.0.0")
    port = int(os.getenv("STT_PORT") or "8080")
    uvicorn.run("server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
