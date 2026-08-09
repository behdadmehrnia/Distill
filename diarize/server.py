"""Standalone pyannote diarization HTTP service for Distill."""

from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger("diarize")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

MODEL_ID = os.getenv("PYANNOTE_MODEL", "pyannote/speaker-diarization-3.1")
# Local offline config (see diarize/models/README.md). When set, Hub download is skipped.
_LOCAL_CONFIG = (os.getenv("PYANNOTE_CONFIG") or "").strip()
DIARIZE_ROOT = Path(__file__).resolve().parent


def _resolve_model_source() -> str:
    """Return Hub model id OR absolute path to a local pipeline YAML."""
    if _LOCAL_CONFIG:
        path = Path(_LOCAL_CONFIG)
        if not path.is_absolute():
            # Try cwd, then repo-relative from diarize/, then diarize/ itself.
            candidates = [
                path,
                DIARIZE_ROOT.parent / path,
                DIARIZE_ROOT / path,
                DIARIZE_ROOT / "models" / path.name,
            ]
            for cand in candidates:
                if cand.is_file():
                    return str(cand.resolve())
            return str((DIARIZE_ROOT / path).resolve())
        return str(path.resolve())
    # Auto-detect default offline layout if weights are present.
    default_cfg = DIARIZE_ROOT / "models" / "pyannote_diarization_config.yaml"
    seg = DIARIZE_ROOT / "models" / "pyannote_model_segmentation-3.0.bin"
    emb = DIARIZE_ROOT / "models" / "pyannote_model_wespeaker-voxceleb-resnet34-LM.bin"
    if default_cfg.is_file() and seg.is_file() and emb.is_file():
        return str(default_cfg.resolve())
    return MODEL_ID



class IntervalOut(BaseModel):
    speaker_id: str
    start_ms: int
    end_ms: int
    is_overlap: bool = False


class DiarizeResponse(BaseModel):
    intervals: List[IntervalOut] = Field(default_factory=list)
    backend: str = "pyannote"
    model: str = MODEL_ID


class PipelineState:
    def __init__(self) -> None:
        self.pipeline: Any = None
        self.backend: str = "unloaded"
        self.error: Optional[str] = None
        self._lock = threading.Lock()
        self._permanent_fail = False

    @property
    def ready(self) -> bool:
        return self.backend == "pyannote" and self.pipeline is not None

    def ensure_loaded(self) -> None:
        with self._lock:
            if self.ready or self._permanent_fail:
                return
            # Retry after transient Hub/network failures.
            self._load()

    def _load(self) -> None:
        source = _resolve_model_source()
        local = Path(source).is_file()
        token = (
            os.getenv("HF_TOKEN")
            or os.getenv("HUGGINGFACE_TOKEN")
            or os.getenv("HUGGING_FACE_HUB_TOKEN")
            or ""
        ).strip()

        if not local and not token:
            self.backend = "error"
            self.error = (
                "HF_TOKEN not set (required for Hub download). "
                "Or place offline weights under diarize/models/ — see models/README.md"
            )
            self._permanent_fail = True
            logger.error(self.error)
            return

        if local:
            logger.info("Loading pyannote from local config %s", source)
        else:
            hub = (
                os.getenv("HF_ENDPOINT")
                or os.getenv("HUGGINGFACE_HUB_ENDPOINT")
                or "https://huggingface.co"
            )
            logger.info(
                "Loading %s (HF_ENDPOINT=%s, token=set)",
                source,
                hub.rstrip("/"),
            )

        try:
            from pyannote.audio import Pipeline  # type: ignore
        except Exception as exc:
            self.backend = "error"
            self.error = f"pyannote import failed: {exc}"
            self._permanent_fail = True
            logger.error(self.error)
            return

        try:
            # Local YAML paths are relative to diarize/ (embedding/segmentation files).
            prev_cwd = Path.cwd()
            try:
                if local:
                    os.chdir(DIARIZE_ROOT)
                if local:
                    pipeline = Pipeline.from_pretrained(source)
                else:
                    try:
                        pipeline = Pipeline.from_pretrained(source, token=token)
                    except TypeError:
                        pipeline = Pipeline.from_pretrained(
                            source, use_auth_token=token
                        )
            finally:
                if local:
                    os.chdir(prev_cwd)

            self.pipeline = pipeline
            self.backend = "pyannote"
            self.error = None
            self._permanent_fail = False
            logger.info("Loaded %s", source)
        except Exception as exc:
            msg = str(exc)
            self.backend = "error"
            self.error = msg
            # Auth / gated-model / missing-token style errors won't fix themselves
            # without config changes; Hub connectivity can be retried.
            lower = msg.lower()
            permanent = local or any(
                tip in lower
                for tip in (
                    "401",
                    "403",
                    "gated",
                    "restricted",
                    "invalid username or password",
                    "invalid token",
                    "cannot access gated",
                    "no such file",
                    "not found",
                )
            )
            self._permanent_fail = permanent
            logger.exception(
                "Failed to load %s (permanent=%s). "
                "For offline use put .bin weights in diarize/models/ "
                "(see models/README.md). For Hub: accept gated terms, check HF_TOKEN, "
                "and avoid broken HF_ENDPOINT mirrors.",
                source,
                permanent,
            )


state = PipelineState()


def _normalize_speaker(label: str) -> str:
    label = str(label)
    if label.startswith("SPEAKER_"):
        return label
    digits = "".join(ch for ch in label if ch.isdigit())
    if digits:
        return f"SPEAKER_{int(digits):02d}"
    return f"SPEAKER_{abs(hash(label)) % 100:02d}"


def _mark_overlaps(turns: List[tuple[int, int, str]]) -> List[IntervalOut]:
    intervals: List[IntervalOut] = []
    for i, (start_ms, end_ms, speaker) in enumerate(turns):
        is_overlap = False
        for j, (s2, e2, spk2) in enumerate(turns):
            if i == j or speaker == spk2:
                continue
            if min(end_ms, e2) > max(start_ms, s2):
                is_overlap = True
                break
        intervals.append(
            IntervalOut(
                speaker_id=_normalize_speaker(speaker),
                start_ms=start_ms,
                end_ms=end_ms,
                is_overlap=is_overlap,
            )
        )
    return sorted(intervals, key=lambda x: x.start_ms)


def _as_annotation(output: Any) -> Any:
    """pyannote 3.x → Annotation; 4.x → DiarizeOutput.speaker_diarization."""
    if hasattr(output, "itertracks"):
        return output
    for attr in ("speaker_diarization", "exclusive_speaker_diarization"):
        ann = getattr(output, attr, None)
        if ann is not None and hasattr(ann, "itertracks"):
            return ann
    raise TypeError(f"Unsupported pyannote output type: {type(output)!r}")


def _run_pipeline(
    audio: np.ndarray,
    sample_rate: int,
    min_speakers: Optional[int],
    max_speakers: Optional[int],
) -> List[IntervalOut]:
    state.ensure_loaded()
    if not state.ready:
        raise HTTPException(
            status_code=503,
            detail=state.error or "diarization model not ready",
        )

    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return []

    kwargs: Dict[str, Any] = {}
    if min_speakers is not None:
        kwargs["min_speakers"] = int(min_speakers)
    if max_speakers is not None:
        kwargs["max_speakers"] = int(max_speakers)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = tmp.name
        sf.write(path, audio, sample_rate)

    try:
        import torch

        try:
            waveform = torch.from_numpy(audio).unsqueeze(0)
            output = state.pipeline(
                {"waveform": waveform, "sample_rate": sample_rate},
                **kwargs,
            )
        except Exception:
            output = state.pipeline(path, **kwargs)

        annotation = _as_annotation(output)
        turns: List[tuple[int, int, str]] = []
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            turns.append(
                (
                    int(turn.start * 1000),
                    int(turn.end * 1000),
                    str(speaker),
                )
            )
        return _mark_overlaps(turns)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Load in a background thread so the port binds immediately.
    threading.Thread(target=state.ensure_loaded, name="pyannote-load", daemon=True).start()
    logger.info("Diarization service starting (model loads in background)")
    yield


app = FastAPI(
    title="Distill Diarization",
    version="0.1.0",
    description="Remote pyannote speaker diarization for Distill",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    source = _resolve_model_source()
    return {
        "status": "ok" if state.backend != "error" else "error",
        "service": "distill-diarize",
        "ready": state.ready,
        "backend": state.backend,
        "model": source,
        "local": Path(source).is_file(),
        "error": state.error,
        "hf_endpoint": (
            os.getenv("HF_ENDPOINT")
            or os.getenv("HUGGINGFACE_HUB_ENDPOINT")
            or "https://huggingface.co"
        ),
    }


@app.post("/v1/reload")
def reload_model() -> dict:
    """Force another model load attempt (e.g. after fixing network / token)."""
    with state._lock:
        state.pipeline = None
        state.backend = "unloaded"
        state.error = None
        state._permanent_fail = False
    state.ensure_loaded()
    return health()


@app.post("/v1/diarize", response_model=DiarizeResponse)
async def diarize(
    file: UploadFile = File(...),
    sample_rate: Optional[int] = Form(None),
    min_speakers: Optional[int] = Form(None),
    max_speakers: Optional[int] = Form(None),
) -> DiarizeResponse:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio")

    try:
        audio, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid audio: {exc}") from exc

    if getattr(audio, "ndim", 1) > 1:
        audio = np.mean(audio, axis=-1)

    sr_i = int(sample_rate or sr)
    if int(sr) != sr_i and sample_rate is not None:
        # Client declared a rate; prefer file metadata when present.
        sr_i = int(sr)

    intervals = _run_pipeline(
        np.asarray(audio, dtype=np.float32),
        sr_i,
        min_speakers,
        max_speakers,
    )
    return DiarizeResponse(intervals=intervals, backend="pyannote", model=MODEL_ID)


def main() -> None:
    import uvicorn

    host = os.getenv("DIARIZE_HOST", "0.0.0.0")
    port = int(os.getenv("DIARIZE_PORT") or os.getenv("PORT") or "8090")
    uvicorn.run("server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
