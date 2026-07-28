from __future__ import annotations

import logging
import os
import tempfile
from typing import Dict, List, Optional, Tuple

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
    ):
        self.sample_rate = sample_rate
        self.min_speakers = min_speakers
        self.max_speakers = max_speakers
        self.hf_token = (
            hf_token
            or os.getenv("HF_TOKEN")
            or os.getenv("HUGGINGFACE_TOKEN")
            or os.getenv("HUGGING_FACE_HUB_TOKEN")
        )
        self._pipeline = None
        self._backend = "fallback"
        self._try_load_pyannote()

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
        Lightweight speaker-change heuristic:
        - Frame energy + spectral centroid
        - KMeans clustering on active frames
        - Consecutive same-cluster frames become intervals
        - Overlap approximated when energy stays high across a speaker change
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
            if rms < 0.01:
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
        labels, centers = self._kmeans_with_centers(X, n_speakers)
        dists = ((X[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)

        # Soft multi-label: ambiguous frames belong to top-2 speakers → overlapping spans
        membership: Dict[int, List[Tuple[int, int]]] = {i: [] for i in range(n_speakers)}
        for idx, t in enumerate(times):
            order = np.argsort(dists[idx])
            primary = int(order[0])
            membership[primary].append((t, t + frame))
            if n_speakers >= 2:
                second = int(order[1])
                d0 = float(dists[idx, primary])
                d1 = float(dists[idx, second])
                rms_feat = float(X[idx, 0])
                if d1 <= d0 * 1.35 + 0.15 and rms_feat > 0.2:
                    membership[second].append((t, t + frame))

        intervals: List[SpeakerInterval] = []
        for lab, spans in membership.items():
            if not spans:
                continue
            intervals.extend(self._spans_to_intervals(f"SPEAKER_{lab:02d}", spans, sr))
        if not intervals:
            return [
                SpeakerInterval("SPEAKER_00", 0, int(len(audio) * 1000 / sr), False)
            ]
        return self._mark_true_overlaps(self._merge_short_intervals(intervals))

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
        by_spk: Dict[str, List[SpeakerInterval]] = {}
        for iv in sorted(intervals, key=lambda x: (x.speaker_id, x.start_ms)):
            by_spk.setdefault(iv.speaker_id, []).append(iv)

        merged: List[SpeakerInterval] = []
        for items in by_spk.values():
            cur = items[0]
            for iv in items[1:]:
                gap = iv.start_ms - cur.end_ms
                if gap <= 120 or (iv.end_ms - iv.start_ms) < min_ms:
                    cur.end_ms = max(cur.end_ms, iv.end_ms)
                    cur.is_overlap = cur.is_overlap or iv.is_overlap
                else:
                    merged.append(cur)
                    cur = iv
            merged.append(cur)
        return sorted(merged, key=lambda x: x.start_ms)

    def _estimate_speakers(self, X: np.ndarray) -> int:
        n = min(self.max_speakers, max(self.min_speakers, min(4, max(2, len(X) // 40))))
        return n

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
