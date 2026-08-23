"""NVIDIA NeMo ClusteringDiarizer backend for the Distill diarization sidecar.

Uses NeMo pretrained VAD + TitaNet embeddings (NGC / NeMo cache) — no gated
pyannote Hugging Face access required.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# NeMo / older ASR helpers still touch NumPy 1.x aliases removed in 2.x.
import numpy as np

if not hasattr(np, "long"):
    np.long = np.int_  # type: ignore[attr-defined]
if not hasattr(np, "sctypes"):
    np.sctypes = {  # type: ignore[attr-defined]
        "int": [
            np.int8,
            np.int16,
            np.int32,
            np.int64,
            int,
        ],
        "uint": [
            np.uint8,
            np.uint16,
            np.uint32,
            np.uint64,
        ],
        "float": [
            np.float16,
            np.float32,
            np.float64,
            float,
        ],
        "complex": [
            np.complex64,
            np.complex128,
            complex,
        ],
        "others": [bool],
    }

logger = logging.getLogger("diarize.nemo")

# Meeting-domain defaults aligned with NeMo diar_infer_meeting.yaml
_DEFAULT_VAD = "vad_multilingual_marblenet"
_DEFAULT_SPK = "titanet_large"
_LOCAL_VAD = (
    Path(__file__).resolve().parent
    / "models"
    / "nemo"
    / "frame_vad_multilingual_marblenet_v2.0.nemo"
)
_LOCAL_SPK = (
    Path(__file__).resolve().parent
    / "models"
    / "nemo"
    / "speakerverification_en_titanet_large.nemo"
)
_CLUSTERING_PATCHED = False


def _patch_torch_tensor_format() -> None:
    """Torch 2.13 refuses '{0:0.4f}'.format(tensor); NeMo 2.0 still does that."""
    import torch

    if getattr(torch.Tensor, "_distill_format_patched", False):
        return

    orig = torch.Tensor.__format__

    def _format(self: Any, spec: str) -> str:  # type: ignore[override]
        try:
            return orig(self, spec)
        except TypeError:
            val = self.detach().cpu().reshape(-1)
            if val.numel() == 0:
                raise
            return format(float(val[0].item()), spec)

    torch.Tensor.__format__ = _format  # type: ignore[method-assign]
    torch.Tensor._distill_format_patched = True  # type: ignore[attr-defined]


def _adapt_frame_vad(vad_model: Any) -> None:
    """Collapse Frame-VAD [B, T, 2] logits to segment-VAD [B, 2] for ClusteringDiarizer."""
    import torch

    if getattr(vad_model, "_distill_frame_adapted", False):
        return
    orig_fwd = vad_model.forward

    def forward(*args: Any, **kwargs: Any) -> Any:
        out = orig_fwd(*args, **kwargs)
        if torch.is_tensor(out) and out.ndim == 3:
            # Pick the most speech-like frame so ClusteringDiarizer's windowed
            # VAD still sees speech on short windows (mean-pool was all silence).
            idx = out[..., 1].argmax(dim=1)
            batch = torch.arange(out.size(0), device=out.device)
            out = out[batch, idx]
        return out

    vad_model.forward = forward
    vad_model._distill_frame_adapted = True


def _patch_clustering_diarizer(ClusteringDiarizer: Any) -> None:
    global _CLUSTERING_PATCHED
    if _CLUSTERING_PATCHED:
        return
    orig_init_vad = ClusteringDiarizer._init_vad_model

    def _init_vad_local(self: Any) -> None:
        model_path = str(self._cfg.diarizer.vad.model_path)
        if model_path.endswith(".nemo"):
            from nemo.collections.asr.models import EncDecClassificationModel

            self._vad_model = EncDecClassificationModel.restore_from(
                model_path,
                map_location=self._cfg.device,
                strict=False,
            )
            logger.info("VAD model loaded locally from %s", model_path)
            self._vad_window_length_in_sec = self._vad_params.window_length_in_sec
            self._vad_shift_length_in_sec = self._vad_params.shift_length_in_sec
            self.has_vad_model = True
            return
        return orig_init_vad(self)

    ClusteringDiarizer._init_vad_model = _init_vad_local
    _CLUSTERING_PATCHED = True


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


def _device() -> str:
    forced = (os.getenv("DIARIZATION_DEVICE") or "").strip().lower()
    if forced == "cpu":
        return "cpu"
    if forced == "mps":
        return "mps"
    if forced == "cuda":
        if _cuda_kernels_usable():
            return "cuda"
        logger.warning("DIARIZATION_DEVICE=cuda but kernels are unusable; using CPU")
        return "cpu"
    try:
        import torch

        if _cuda_kernels_usable():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _build_cfg(
    manifest_path: str,
    out_dir: str,
    min_speakers: Optional[int],
    max_speakers: Optional[int],
    external_vad_manifest: Optional[str] = None,
) -> Any:
    from omegaconf import OmegaConf

    vad_model = (os.getenv("NEMO_VAD_MODEL") or "").strip()
    spk_model = (os.getenv("NEMO_SPK_MODEL") or "").strip()
    if not vad_model:
        vad_model = str(_LOCAL_VAD) if _LOCAL_VAD.is_file() else _DEFAULT_VAD
    if not spk_model:
        spk_model = str(_LOCAL_SPK) if _LOCAL_SPK.is_file() else _DEFAULT_SPK
    if external_vad_manifest:
        vad_model = None
    max_spk = int(max_speakers or os.getenv("NEMO_MAX_SPEAKERS") or 8)
    min_spk = int(min_speakers or 1)
    oracle_num = min_spk == max_spk and min_spk > 0

    cfg_dict: Dict[str, Any] = {
        "name": "DistillNeMoClusteringDiarizer",
        "num_workers": 0,
        "sample_rate": 16000,
        "batch_size": 64,
        "device": _device(),
        "verbose": False,
        "diarizer": {
            "manifest_filepath": manifest_path,
            "out_dir": out_dir,
            "oracle_vad": False,
            "collar": 0.25,
            "ignore_overlap": True,
            "vad": {
                "model_path": vad_model,
                "external_vad_manifest": external_vad_manifest,
                "parameters": {
                    "window_length_in_sec": 0.15,
                    "shift_length_in_sec": 0.01,
                    "smoothing": "median",
                    "overlap": 0.5,
                    "onset": 0.05,
                    "offset": 0.05,
                    "pad_onset": 0.1,
                    "pad_offset": -0.05,
                    "min_duration_on": 0.2,
                    "min_duration_off": 0.2,
                    "filter_speech_first": True,
                },
            },
            "speaker_embeddings": {
                "model_path": spk_model,
                "parameters": {
                    "window_length_in_sec": [1.5, 1.25, 1.0, 0.75, 0.5],
                    "shift_length_in_sec": [0.75, 0.625, 0.5, 0.375, 0.25],
                    "multiscale_weights": [1, 1, 1, 1, 1],
                    "save_embeddings": False,
                },
            },
            "clustering": {
                "parameters": {
                    "oracle_num_speakers": oracle_num,
                    "max_num_speakers": max_spk,
                    "enhanced_count_thres": 80,
                    "max_rp_threshold": 0.25,
                    "sparse_search_volume": 30,
                    "maj_vote_spk_count": False,
                }
            },
            "msdd_model": {
                "model_path": None,
                "parameters": {
                    "use_speaker_model_from_msdd": False,
                },
            },
        },
    }
    if oracle_num:
        cfg_dict["diarizer"]["clustering"]["parameters"]["oracle_num_speakers"] = True
    return OmegaConf.create(cfg_dict)


def _parse_rttm(path: Path) -> List[Tuple[float, float, str]]:
    turns: List[Tuple[float, float, str]] = []
    if not path.is_file():
        return turns
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split()
        if len(parts) < 8 or parts[0].upper() != "SPEAKER":
            continue
        try:
            start = float(parts[3])
            dur = float(parts[4])
        except ValueError:
            continue
        speaker = parts[7]
        turns.append((start, start + dur, speaker))
    return turns


def _normalize_speaker(label: str) -> str:
    label = str(label)
    if label.startswith("SPEAKER_"):
        return label
    digits = "".join(ch for ch in label if ch.isdigit())
    if digits:
        return f"SPEAKER_{int(digits):02d}"
    # NeMo often emits speaker_0 / speaker_1
    if "speaker" in label.lower():
        digits = "".join(ch for ch in label if ch.isdigit())
        if digits:
            return f"SPEAKER_{int(digits):02d}"
    return f"SPEAKER_{abs(hash(label)) % 100:02d}"


class NeMoDiarizer:
    """Lazy-loading NeMo ClusteringDiarizer wrapper."""

    def __init__(self) -> None:
        self._ready = False
        self._error: Optional[str] = None
        self._checked_import = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def error(self) -> Optional[str]:
        return self._error

    def ensure_importable(self) -> bool:
        if self._checked_import and self._ready:
            return True
        self._checked_import = True
        try:
            from nemo.collections.asr.models import ClusteringDiarizer  # noqa: F401
            from omegaconf import OmegaConf  # noqa: F401

            self._ready = True
            self._error = None
            logger.info("NeMo ClusteringDiarizer import OK (device=%s)", _device())
            return True
        except Exception as exc:
            self._ready = False
            self._error = f"NeMo import failed: {exc}"
            logger.warning(self._error)
            return False

    def diarize(
        self,
        audio_path: str,
        sample_rate: int = 16000,
        min_speakers: Optional[int] = None,
        max_speakers: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not self.ensure_importable():
            raise RuntimeError(self._error or "NeMo not available")

        from nemo.collections.asr.models import ClusteringDiarizer

        work = Path(tempfile.mkdtemp(prefix="distill-nemo-"))
        try:
            manifest = work / "manifest.json"
            out_dir = work / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            uniq_id = Path(audio_path).stem
            meta = {
                "audio_filepath": str(Path(audio_path).resolve()),
                "offset": 0,
                "duration": None,
                "label": "infer",
                "text": "-",
                "uniq_id": uniq_id,
                "num_speakers": (
                    int(min_speakers)
                    if min_speakers is not None
                    and max_speakers is not None
                    and int(min_speakers) == int(max_speakers)
                    else None
                ),
                "rttm_filepath": None,
                "uem_filepath": None,
            }
            with manifest.open("w", encoding="utf-8") as fp:
                json.dump(meta, fp)
                fp.write("\n")

            import soundfile as sf

            duration = float(sf.info(audio_path).duration)
            vad_manifest = work / "external_vad.json"
            with vad_manifest.open("w", encoding="utf-8") as fp:
                json.dump(
                    {
                        "audio_filepath": str(Path(audio_path).resolve()),
                        "offset": 0.0,
                        "duration": duration,
                        "label": "UNK",
                        "uniq_id": uniq_id,
                    },
                    fp,
                )
                fp.write("\n")

            cfg = _build_cfg(
                str(manifest),
                str(out_dir),
                min_speakers,
                max_speakers,
                external_vad_manifest=str(vad_manifest),
            )
            # sample_rate is informational; NeMo resamples internally as needed
            _ = sample_rate

            _patch_torch_tensor_format()
            _patch_clustering_diarizer(ClusteringDiarizer)
            model = ClusteringDiarizer(cfg=cfg)
            if getattr(model, "_vad_model", None) is not None:
                _adapt_frame_vad(model._vad_model)
            try:
                import torch

                if cfg.device == "cuda" and torch.cuda.is_available():
                    model = model.to("cuda")
            except Exception:
                pass

            model.diarize()

            # Prefer pred_rttms/<stem>.rttm
            stem = Path(audio_path).stem
            candidates = list(out_dir.rglob(f"{stem}.rttm")) + list(
                out_dir.rglob("*.rttm")
            )
            turns: List[Tuple[float, float, str]] = []
            for cand in candidates:
                turns = _parse_rttm(cand)
                if turns:
                    break

            intervals: List[Dict[str, Any]] = []
            for start_s, end_s, spk in turns:
                start_ms = int(round(start_s * 1000))
                end_ms = int(round(end_s * 1000))
                if end_ms <= start_ms:
                    continue
                intervals.append(
                    {
                        "speaker_id": _normalize_speaker(spk),
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "is_overlap": False,
                    }
                )

            # Mark overlaps
            for i, a in enumerate(intervals):
                for j, b in enumerate(intervals):
                    if i == j or a["speaker_id"] == b["speaker_id"]:
                        continue
                    if min(a["end_ms"], b["end_ms"]) > max(a["start_ms"], b["start_ms"]):
                        a["is_overlap"] = True
                        break

            return sorted(intervals, key=lambda x: x["start_ms"])
        except ValueError as exc:
            # NeMo VAD raises when the clip is all silence / non-speech.
            msg = str(exc).lower()
            if "silence" in msg or "no speech" in msg:
                logger.info("NeMo VAD: no speech detected (%s)", exc)
                return []
            raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
