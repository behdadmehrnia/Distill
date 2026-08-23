"""Distill local diarization HTTP service.

Backends (DIARIZATION_BACKEND=auto|pyannote|nemo):
  auto     → offline pyannote weights → Hub (only if HF token has access) → NeMo
  pyannote → offline weights, else Hub when token validates
  nemo     → NVIDIA NeMo ClusteringDiarizer (no gated pyannote HF required)

Always runs locally. Hugging Face is used only for weight download when the
token is valid and has accepted gated model terms.
"""

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

from hf_access import resolve_hf_token, should_use_hub_download, validate_pyannote_access
from nemo_backend import NeMoDiarizer

logger = logging.getLogger("diarize")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

MODEL_ID = os.getenv("PYANNOTE_MODEL", "pyannote/speaker-diarization-3.1")
_LOCAL_CONFIG = (os.getenv("PYANNOTE_CONFIG") or "").strip()
DIARIZE_ROOT = Path(__file__).resolve().parent
BACKEND_PREF = (os.getenv("DIARIZATION_BACKEND") or "auto").strip().lower()


def _resolve_local_pyannote_config() -> Optional[str]:
    """Return absolute path to offline pipeline YAML when weights exist."""
    if _LOCAL_CONFIG:
        path = Path(_LOCAL_CONFIG)
        candidates = [
            path,
            DIARIZE_ROOT.parent / path,
            DIARIZE_ROOT / path,
            DIARIZE_ROOT / "models" / path.name,
        ]
        for cand in candidates:
            if cand.is_file():
                return str(cand.resolve())
        return None

    default_cfg = DIARIZE_ROOT / "models" / "pyannote_diarization_config.yaml"
    seg = DIARIZE_ROOT / "models" / "pyannote_model_segmentation-3.0.bin"
    emb = DIARIZE_ROOT / "models" / "pyannote_model_wespeaker-voxceleb-resnet34-LM.bin"
    if default_cfg.is_file() and seg.is_file() and emb.is_file():
        return str(default_cfg.resolve())
    return None


def _cuda_kernels_usable() -> bool:
    """True only if a tiny CUDA op succeeds (catches sm_120 + old cu124 wheels)."""
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        major, minor = torch.cuda.get_device_capability(0)
        arches = set(torch.cuda.get_arch_list() or [])
        tag = f"sm_{major}{minor}"
        if arches and tag not in arches and f"compute_{major}{minor}" not in arches:
            logger.warning(
                "CUDA device %s not in torch arch list %s — falling back to CPU",
                tag,
                sorted(arches),
            )
            return False
        x = torch.zeros(1, device="cuda")
        torch.cuda.synchronize()
        del x
        return True
    except Exception as exc:
        logger.warning("CUDA probe failed (%s) — falling back to CPU", exc)
        return False


def _pick_device():
    forced = (os.getenv("DIARIZATION_DEVICE") or "").strip().lower()
    try:
        import torch

        if forced == "cpu":
            return torch.device("cpu")
        if forced == "cuda":
            if _cuda_kernels_usable():
                return torch.device("cuda")
            logger.warning(
                "DIARIZATION_DEVICE=cuda but kernels are unusable; using CPU"
            )
            return torch.device("cpu")
        if forced == "mps" and getattr(torch.backends, "mps", None):
            if torch.backends.mps.is_available():
                return torch.device("mps")
        if _cuda_kernels_usable():
            return torch.device("cuda")
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    except Exception:
        return None


class IntervalOut(BaseModel):
    speaker_id: str
    start_ms: int
    end_ms: int
    is_overlap: bool = False


class DiarizeResponse(BaseModel):
    intervals: List[IntervalOut] = Field(default_factory=list)
    backend: str = "unknown"
    model: str = MODEL_ID


class PipelineState:
    def __init__(self) -> None:
        self.pipeline: Any = None
        self.nemo: Optional[NeMoDiarizer] = None
        self.backend: str = "unloaded"  # pyannote | nemo | error | unloaded
        self.model_id: str = MODEL_ID
        self.error: Optional[str] = None
        self.mode: str = "none"  # local | hub | nemo
        self._lock = threading.Lock()
        self._permanent_fail = False

    @property
    def ready(self) -> bool:
        if self.backend == "pyannote":
            return self.pipeline is not None
        if self.backend == "nemo":
            return self.nemo is not None and self.nemo.ready
        return False

    def ensure_loaded(self) -> None:
        with self._lock:
            if self.ready or self._permanent_fail:
                return
            self._load()

    def _load(self) -> None:
        pref = BACKEND_PREF
        errors: List[str] = []

        try_pyannote = pref in {"auto", "pyannote"}
        try_nemo = pref in {"auto", "nemo"}

        if try_pyannote:
            ok, err = self._try_load_pyannote()
            if ok:
                return
            errors.append(err or "pyannote failed")

        if try_nemo:
            ok, err = self._try_load_nemo()
            if ok:
                return
            errors.append(err or "nemo failed")

        self.backend = "error"
        self.error = " | ".join(errors) or "no diarization backend available"
        self._permanent_fail = True
        logger.error("Diarization load failed: %s", self.error)

    def _try_load_pyannote(self) -> tuple[bool, str]:
        local = _resolve_local_pyannote_config()
        token = resolve_hf_token()
        use_hub = False
        source: Optional[str] = None

        if local:
            source = local
            logger.info("Loading pyannote from offline weights: %s", source)
        else:
            if not token:
                return False, "pyannote: no offline weights and HF_TOKEN not set"
            ok, reason = validate_pyannote_access(token)
            if not ok:
                return False, f"pyannote: Hub blocked ({reason})"
            if not should_use_hub_download(token):
                return False, f"pyannote: Hub access not usable ({reason})"
            use_hub = True
            source = MODEL_ID
            logger.info("Loading pyannote from Hugging Face Hub: %s", source)

        try:
            from pyannote.audio import Pipeline  # type: ignore
        except Exception as exc:
            return False, f"pyannote import failed: {exc}"

        try:
            prev_cwd = Path.cwd()
            try:
                if local:
                    os.chdir(DIARIZE_ROOT)
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

            device = _pick_device()
            if device is not None:
                try:
                    pipeline.to(device)
                    logger.info("pyannote moved to %s", device)
                except Exception as exc:
                    logger.warning("Could not move pyannote to %s: %s", device, exc)

            self.pipeline = pipeline
            self.backend = "pyannote"
            self.model_id = source or MODEL_ID
            self.mode = "local" if local else "hub"
            self.error = None
            self._permanent_fail = False
            logger.info("Loaded pyannote (%s)", self.mode)
            return True, "ok"
        except Exception as exc:
            msg = str(exc)
            logger.exception("Failed to load pyannote")
            return False, f"pyannote load failed: {msg}"

    def _try_load_nemo(self) -> tuple[bool, str]:
        nemo = NeMoDiarizer()
        if not nemo.ensure_importable():
            return False, nemo.error or "NeMo not importable"
        self.nemo = nemo
        self.pipeline = None
        self.backend = "nemo"
        self.model_id = (
            f"nemo:{os.getenv('NEMO_SPK_MODEL') or 'titanet_large'}"
        )
        self.mode = "nemo"
        self.error = None
        self._permanent_fail = False
        logger.info("Using NVIDIA NeMo ClusteringDiarizer")
        return True, "ok"


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
    if hasattr(output, "itertracks"):
        return output
    for attr in ("speaker_diarization", "exclusive_speaker_diarization"):
        ann = getattr(output, attr, None)
        if ann is not None and hasattr(ann, "itertracks"):
            return ann
    raise TypeError(f"Unsupported pyannote output type: {type(output)!r}")


def _run_pyannote(
    audio: np.ndarray,
    sample_rate: int,
    min_speakers: Optional[int],
    max_speakers: Optional[int],
) -> List[IntervalOut]:
    assert state.pipeline is not None
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


def _run_nemo(
    audio: np.ndarray,
    sample_rate: int,
    min_speakers: Optional[int],
    max_speakers: Optional[int],
) -> List[IntervalOut]:
    assert state.nemo is not None
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = tmp.name
        sf.write(path, audio, sample_rate)
    try:
        raw = state.nemo.diarize(
            path,
            sample_rate=sample_rate,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
        return [
            IntervalOut(
                speaker_id=item["speaker_id"],
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
                is_overlap=bool(item.get("is_overlap", False)),
            )
            for item in raw
        ]
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


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

    if state.backend == "nemo":
        return _run_nemo(audio, sample_rate, min_speakers, max_speakers)
    return _run_pyannote(audio, sample_rate, min_speakers, max_speakers)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    threading.Thread(target=state.ensure_loaded, name="diarize-load", daemon=True).start()
    logger.info(
        "Diarization service starting (backend_pref=%s; model loads in background)",
        BACKEND_PREF,
    )
    yield


app = FastAPI(
    title="Distill Diarization",
    version="0.2.0",
    description="Local speaker diarization (pyannote / NVIDIA NeMo) for Distill",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    local = _resolve_local_pyannote_config()
    tok = resolve_hf_token()
    hf_ok, hf_reason = (False, "no token")
    if tok:
        hf_ok, hf_reason = validate_pyannote_access(tok)
    return {
        "status": "ok" if state.backend != "error" else "error",
        "service": "distill-diarize",
        "ready": state.ready,
        "backend": state.backend,
        "mode": state.mode,
        "model": state.model_id,
        "backend_pref": BACKEND_PREF,
        "local_weights": bool(local),
        "hf_token_present": bool(tok),
        "hf_pyannote_access": hf_ok,
        "hf_access_reason": hf_reason if not hf_ok else "ok",
        "error": state.error,
        "hf_endpoint": (
            os.getenv("HF_ENDPOINT")
            or os.getenv("HUGGINGFACE_HUB_ENDPOINT")
            or "https://huggingface.co"
        ),
    }


@app.post("/v1/reload")
def reload_model() -> dict:
    with state._lock:
        state.pipeline = None
        state.nemo = None
        state.backend = "unloaded"
        state.error = None
        state.mode = "none"
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

    sr_i = int(sr)
    if sample_rate is not None:
        # Prefer file metadata when present; Form value is advisory.
        sr_i = int(sr)

    intervals = _run_pipeline(
        np.asarray(audio, dtype=np.float32),
        sr_i,
        min_speakers,
        max_speakers,
    )
    return DiarizeResponse(
        intervals=intervals,
        backend=state.backend,
        model=state.model_id,
    )


def main() -> None:
    import uvicorn

    host = os.getenv("DIARIZE_HOST", "0.0.0.0")
    port = int(os.getenv("DIARIZE_PORT") or os.getenv("PORT") or "8090")
    uvicorn.run("server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
