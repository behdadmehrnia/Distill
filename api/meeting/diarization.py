from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .models import SpeakerInterval

logger = logging.getLogger(__name__)


class SpeakerDiarizer:
    """
    Speaker diarization with overlap detection.

    Tries pyannote.audio when available (HF token via HF_TOKEN / HUGGINGFACE_TOKEN).
    Falls back to energy/spectral change clustering so the meeting pipeline still works offline.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        min_speakers: int = 1,
        max_speakers: int = 8,
        hf_token: Optional[str] = None,
        energy_threshold: float = 0.01,
        merge_short_ms: int = 400,
    ):
        self.sample_rate = sample_rate
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.energy_threshold = energy_threshold
        self.merge_short_ms = merge_short_ms
        self.hf_token = (
            hf_token
            or os.getenv("HF_TOKEN")
            or os.getenv("HUGGINGFACE_TOKEN")
            or os.getenv("HUGGING_FACE_HUB_TOKEN")
        )
        self._pipeline = None
        self._backend = "fallback"
        self._try_load_pyannote()

    def apply_tuning(self, tuning: Dict[str, Any]) -> None:
        if "min_speakers" in tuning:
            self.min_speakers = int(tuning["min_speakers"])
        if "max_speakers" in tuning:
            self.max_speakers = int(tuning["max_speakers"])
        if "energy_threshold" in tuning:
            self.energy_threshold = float(tuning["energy_threshold"])
        if "merge_short_ms" in tuning:
            self.merge_short_ms = int(tuning["merge_short_ms"])

    def _try_load_pyannote(self) -> None:
        try:
            from pyannote.audio import Pipeline  # type: ignore
        except Exception as exc:
            logger.info("pyannote not available (%s); using fallback diarization", exc)
            return

        if not self.hf_token:
            logger.info("No HF token set; using fallback diarization")
            return

        try:
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=self.hf_token,
            )
            self._backend = "pyannote"
            logger.info("Loaded pyannote speaker-diarization-3.1")
        except Exception as exc:
            logger.warning("Failed to load pyannote pipeline: %s", exc)
            self._pipeline = None
            self._backend = "fallback"

    @property
    def backend(self) -> str:
        return self._backend

    def diarize(self, audio: np.ndarray, sample_rate: Optional[int] = None) -> List[SpeakerInterval]:
        sr = sample_rate or self.sample_rate
        if audio is None or len(audio) == 0:
            return []

        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if self._pipeline is not None:
            try:
                return self._diarize_pyannote(audio, sr)
            except Exception as exc:
                logger.warning("pyannote diarization failed, falling back: %s", exc)

        return self._diarize_fallback(audio, sr)

    def _diarize_pyannote(self, audio: np.ndarray, sr: int) -> List[SpeakerInterval]:
        import torch
        import soundfile as sf

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
            sf.write(path, audio, sr)

        try:
            # Prefer waveform dict when supported
            try:
                waveform = torch.from_numpy(audio).unsqueeze(0)
                diarization = self._pipeline({"waveform": waveform, "sample_rate": sr})
            except Exception:
                diarization = self._pipeline(path)

            intervals: List[SpeakerInterval] = []
            # Collect raw turns
            turns = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                turns.append(
                    (
                        int(turn.start * 1000),
                        int(turn.end * 1000),
                        str(speaker),
                    )
                )

            # Mark overlaps: any time point covered by >1 speaker
            for i, (s, e, spk) in enumerate(turns):
                is_overlap = False
                for j, (s2, e2, spk2) in enumerate(turns):
                    if i == j or spk == spk2:
                        continue
                    if min(e, e2) > max(s, s2):
                        is_overlap = True
                        break
                intervals.append(
                    SpeakerInterval(
                        speaker_id=self._normalize_speaker(spk),
                        start_ms=s,
                        end_ms=e,
                        is_overlap=is_overlap,
                    )
                )
            return sorted(intervals, key=lambda x: x.start_ms)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _normalize_speaker(self, label: str) -> str:
        label = str(label)
        if label.startswith("SPEAKER_"):
            return label
        # pyannote sometimes returns SPEAKER_00 already; otherwise map
        digits = "".join(ch for ch in label if ch.isdigit())
        if digits:
            return f"SPEAKER_{int(digits):02d}"
        return f"SPEAKER_{abs(hash(label)) % 100:02d}"

    def _diarize_fallback(self, audio: np.ndarray, sr: int) -> List[SpeakerInterval]:
        """
        Lightweight speaker-change heuristic for mono mic:
        - Frame energy + spectral centroid
        - Prefer a single speaker unless clusters are clearly separated
        - Emit abutting (non-overlapping) hard labels — frame hop must not
          invent simultaneous-talk overlaps on one microphone
        """
        frame_ms = 30
        hop_ms = 15
        frame = int(sr * frame_ms / 1000)
        hop = int(sr * hop_ms / 1000)
        if len(audio) < frame:
            return [
                SpeakerInterval("SPEAKER_00", 0, int(len(audio) * 1000 / sr), False)
            ]

        feats = []
        times = []
        for start in range(0, len(audio) - frame + 1, hop):
            chunk = audio[start : start + frame]
            rms = float(np.sqrt(np.mean(np.square(chunk)) + 1e-12))
            if rms < self.energy_threshold:
                continue
            # crude spectral centroid via FFT magnitude
            spec = np.abs(np.fft.rfft(chunk * np.hanning(len(chunk))))
            freqs = np.fft.rfftfreq(len(chunk), d=1.0 / sr)
            centroid = float(np.sum(freqs * spec) / (np.sum(spec) + 1e-12))
            zcr = float(np.mean(np.abs(np.diff(np.sign(chunk)))) / 2.0)
            feats.append([np.log1p(rms * 100), centroid / 1000.0, zcr])
            times.append(start)

        if len(feats) < 4:
            return [
                SpeakerInterval("SPEAKER_00", 0, int(len(audio) * 1000 / sr), False)
            ]

        X = np.asarray(feats, dtype=np.float32)
        # Normalize
        X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-6)

        n_speakers = self._estimate_speakers(X)
        labels = self._kmeans(X, n_speakers)
        # Smooth label flicker (mono mic often oscillates between 2 clusters)
        labels = self._smooth_labels(labels, window=5)

        # Abutting hard labels: end previous turn at the new turn's start.
        # Overlapping analysis frames (30ms/15ms hop) must NOT create fake overlaps.
        intervals: List[SpeakerInterval] = []
        cur_label = int(labels[0])
        cur_start = times[0]
        for idx in range(1, len(labels)):
            lab = int(labels[idx])
            t = times[idx]
            if lab != cur_label:
                end_ms = int(t * 1000 / sr)
                start_ms = int(cur_start * 1000 / sr)
                if end_ms > start_ms:
                    intervals.append(
                        SpeakerInterval(
                            speaker_id=f"SPEAKER_{cur_label:02d}",
                            start_ms=start_ms,
                            end_ms=end_ms,
                            is_overlap=False,
                        )
                    )
                cur_label = lab
                cur_start = t
        end_ms = int((times[-1] + frame) * 1000 / sr)
        start_ms = int(cur_start * 1000 / sr)
        if end_ms > start_ms:
            intervals.append(
                SpeakerInterval(
                    speaker_id=f"SPEAKER_{cur_label:02d}",
                    start_ms=start_ms,
                    end_ms=end_ms,
                    is_overlap=False,
                )
            )
        return self._merge_short_intervals(intervals, min_ms=self.merge_short_ms)

    @staticmethod
    def _spans_to_intervals(
        speaker_id: str,
        spans: List[Tuple[int, int]],
        sr: int,
        max_gap_samples: Optional[int] = None,
    ) -> List[SpeakerInterval]:
        if not spans:
            return []
        max_gap = max_gap_samples if max_gap_samples is not None else int(sr * 0.08)
        spans = sorted(spans, key=lambda x: x[0])
        cur_s, cur_e = spans[0]
        out: List[SpeakerInterval] = []
        for s, e in spans[1:]:
            if s <= cur_e + max_gap:
                cur_e = max(cur_e, e)
            else:
                out.append(
                    SpeakerInterval(
                        speaker_id=speaker_id,
                        start_ms=int(cur_s * 1000 / sr),
                        end_ms=int(cur_e * 1000 / sr),
                        is_overlap=False,
                    )
                )
                cur_s, cur_e = s, e
        out.append(
            SpeakerInterval(
                speaker_id=speaker_id,
                start_ms=int(cur_s * 1000 / sr),
                end_ms=int(cur_e * 1000 / sr),
                is_overlap=False,
            )
        )
        return out

    @staticmethod
    def _mark_true_overlaps(intervals: List[SpeakerInterval]) -> List[SpeakerInterval]:
        for i, a in enumerate(intervals):
            for b in intervals[i + 1 :]:
                if a.speaker_id == b.speaker_id:
                    continue
                if min(a.end_ms, b.end_ms) > max(a.start_ms, b.start_ms):
                    a.is_overlap = True
                    b.is_overlap = True
        return intervals

    @staticmethod
    def _merge_short_intervals(
        intervals: List[SpeakerInterval], min_ms: int = 400
    ) -> List[SpeakerInterval]:
        if not intervals:
            return []
        # Never bridge across another speaker — that invents fake overlaps on mono mic.
        ordered = sorted(intervals, key=lambda x: (x.start_ms, x.end_ms, x.speaker_id))
        merged: List[SpeakerInterval] = []
        for iv in ordered:
            if (iv.end_ms - iv.start_ms) < min_ms and merged:
                # Absorb very short turn into previous turn (same timeline continuity)
                prev = merged[-1]
                if iv.speaker_id == prev.speaker_id and iv.start_ms - prev.end_ms <= 120:
                    prev.end_ms = max(prev.end_ms, iv.end_ms)
                    prev.is_overlap = prev.is_overlap or iv.is_overlap
                    continue
                if iv.speaker_id != prev.speaker_id and iv.start_ms <= prev.end_ms + 80:
                    # Tiny flicker after a switch: keep previous speaker
                    prev.end_ms = max(prev.end_ms, iv.end_ms)
                    continue
            if (
                merged
                and merged[-1].speaker_id == iv.speaker_id
                and iv.start_ms - merged[-1].end_ms <= 120
            ):
                merged[-1].end_ms = max(merged[-1].end_ms, iv.end_ms)
                merged[-1].is_overlap = merged[-1].is_overlap or iv.is_overlap
            else:
                merged.append(
                    SpeakerInterval(
                        speaker_id=iv.speaker_id,
                        start_ms=iv.start_ms,
                        end_ms=iv.end_ms,
                        is_overlap=iv.is_overlap,
                    )
                )
        return sorted(merged, key=lambda x: x.start_ms)

    def _estimate_speakers(self, X: np.ndarray) -> int:
        """
        Conservative for single-mic meetings: default to 1 speaker.
        Only split to 2+ when clusters are large and well separated.
        """
        if self.max_speakers <= 1 or len(X) < 160:
            return 1

        k = min(2, self.max_speakers)
        labels, centers = self._kmeans_with_centers(X, k)
        counts = np.bincount(labels, minlength=k)
        if counts.min() < max(40, int(0.22 * len(X))):
            return 1

        # Require clear centroid separation in normalized feature space
        sep = float(np.linalg.norm(centers[0] - centers[1]))
        if sep < 1.35:
            return 1

        # Intra-cluster compactness vs separation
        inertia = 0.0
        for i in range(k):
            members = X[labels == i]
            if len(members):
                inertia += float(((members - centers[i]) ** 2).sum())
        inertia /= max(1, len(X))
        if sep < 2.2 * max(inertia, 0.15):
            return 1

        return k

    @staticmethod
    def _smooth_labels(labels: np.ndarray, window: int = 5) -> np.ndarray:
        """Majority-filter label flicker from mono-mic KMeans oscillation."""
        if len(labels) < 3 or window < 3:
            return labels
        out = labels.copy()
        radius = window // 2
        for i in range(len(labels)):
            a = max(0, i - radius)
            b = min(len(labels), i + radius + 1)
            chunk = labels[a:b]
            # mode
            vals, counts = np.unique(chunk, return_counts=True)
            out[i] = int(vals[int(np.argmax(counts))])
        return out

    @staticmethod
    def _kmeans_with_centers(
        X: np.ndarray, k: int, iters: int = 15
    ) -> Tuple[np.ndarray, np.ndarray]:
        rng = np.random.default_rng(42)
        centers = X[rng.choice(len(X), size=k, replace=False)].copy()
        labels = np.zeros(len(X), dtype=np.int32)
        for _ in range(iters):
            dists = ((X[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
            labels = dists.argmin(axis=1).astype(np.int32)
            for i in range(k):
                members = X[labels == i]
                if len(members):
                    centers[i] = members.mean(axis=0)
        return labels, centers

    @staticmethod
    def _kmeans(X: np.ndarray, k: int, iters: int = 15) -> np.ndarray:
        labels, _ = SpeakerDiarizer._kmeans_with_centers(X, k, iters)
        return labels
