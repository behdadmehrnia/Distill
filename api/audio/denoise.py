"""DeepFilterNet denoising for uploaded meeting audio (upload path only).

Uses ``deepfilter-stream`` (ONNX Runtime, no PyTorch) when installed. Fails open
on any error so uploads still complete with the original audio.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

_model_lock = threading.Lock()
_model: object | None = None
_model_error: str | None = None


def upload_denoise_enabled() -> bool:
    raw = (os.getenv("AUDIO_DENOISE_UPLOAD") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _atten_lim_db() -> float | None:
    raw = (os.getenv("AUDIO_DENOISE_ATTEN_LIM_DB") or "12").strip()
    if raw.lower() in {"", "none", "off"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return 12.0


def _onnx_threads() -> int:
    raw = (os.getenv("AUDIO_DENOISE_THREADS") or "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


@dataclass(frozen=True)
class DenoiseResult:
    audio: np.ndarray
    applied: bool
    backend: str
    elapsed_ms: float


def _align_length(audio: np.ndarray, target_len: int) -> np.ndarray:
    if len(audio) == target_len:
        return audio
    if len(audio) > target_len:
        return audio[:target_len].copy()
    out = np.zeros(target_len, dtype=np.float32)
    out[: len(audio)] = audio
    return out


def _get_stream_model():
    global _model, _model_error
    with _model_lock:
        if _model is not None:
            return _model
        if _model_error:
            raise RuntimeError(_model_error)
        try:
            from deepfilter_stream import DeepFilterModel

            _model = DeepFilterModel(intra_op_num_threads=_onnx_threads())
            logger.info(
                "Upload denoiser ready (deepfilter-stream, threads=%d)",
                _onnx_threads(),
            )
            return _model
        except Exception as exc:
            _model_error = str(exc)
            raise


def _denoise_deepfilter_stream(
    audio: np.ndarray, sample_rate: int, atten_lim_db: float | None
) -> np.ndarray:
    model = _get_stream_model()
    stream = model.new_stream(atten_lim_db=atten_lim_db)
    enhanced = stream.process(audio, sample_rate)
    tail = stream.flush()
    if tail.size:
        enhanced = np.concatenate([enhanced, tail])
    return _align_length(
        np.asarray(enhanced, dtype=np.float32).reshape(-1), len(audio)
    )


def denoise_upload_audio(
    audio: np.ndarray,
    sample_rate: int = 16000,
    *,
    enabled: bool | None = None,
) -> DenoiseResult:
    """Enhance mono float32 PCM for upload processing. Never raises."""
    src = np.asarray(audio, dtype=np.float32).reshape(-1)
    if enabled is None:
        enabled = upload_denoise_enabled()
    if not enabled or len(src) == 0:
        return DenoiseResult(
            audio=src, applied=False, backend="disabled", elapsed_ms=0.0
        )

    t0 = time.perf_counter()
    try:
        clean = _denoise_deepfilter_stream(src, sample_rate, _atten_lim_db())
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logger.warning(
            "Upload denoise skipped (%s); using original audio (%.0fms)",
            exc,
            elapsed_ms,
        )
        return DenoiseResult(
            audio=src,
            applied=False,
            backend="unavailable",
            elapsed_ms=elapsed_ms,
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    duration_s = len(src) / max(sample_rate, 1)
    rtf = (elapsed_ms / 1000.0) / duration_s if duration_s > 0 else 0.0
    logger.info(
        "Upload denoise applied (deepfilter-stream, %.0fms, rtf=%.3f, %.1fs audio)",
        elapsed_ms,
        rtf,
        duration_s,
    )
    return DenoiseResult(
        audio=clean,
        applied=True,
        backend="deepfilter-stream",
        elapsed_ms=elapsed_ms,
    )
