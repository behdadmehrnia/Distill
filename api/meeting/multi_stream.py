from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

from .chunker import AudioChunk, OverlappingChunker
from .ingest import AudioIngest
from .models import SpeakerInterval


@dataclass
class StreamSttItem:
    """STT queue entry for a tagged per-participant audio stream."""

    speaker_id: str
    chunk: AudioChunk


def normalize_speaker_id(raw: str) -> str:
    """Map external participant ids to stable Distill speaker ids."""
    value = str(raw or "").strip()
    if not value:
        raise ValueError("speaker_id is required")
    if value.startswith("SPEAKER_"):
        return value
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    return f"SPEAKER_{safe}"


def merge_speaker_intervals(
    intervals: Sequence[SpeakerInterval],
    *,
    gap_ms: int = 400,
) -> List[SpeakerInterval]:
    """Merge adjacent intervals for the same speaker."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda iv: (iv.speaker_id, iv.start_ms, iv.end_ms))
    merged: List[SpeakerInterval] = []
    cur = ordered[0]
    for nxt in ordered[1:]:
        if (
            nxt.speaker_id == cur.speaker_id
            and not cur.is_overlap
            and not nxt.is_overlap
            and nxt.start_ms <= cur.end_ms + gap_ms
        ):
            cur = SpeakerInterval(
                cur.speaker_id,
                cur.start_ms,
                max(cur.end_ms, nxt.end_ms),
                cur.is_overlap,
            )
        else:
            merged.append(cur)
            cur = nxt
    merged.append(cur)
    return merged


class MultiStreamIngest:
    """Per-participant mono PCM buffers for multi-source capture."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        audio_dir: str = "./data/audio",
        meeting_id: str,
    ):
        self.sample_rate = sample_rate
        self.audio_dir = audio_dir
        self.meeting_id = meeting_id
        self._streams: Dict[str, AudioIngest] = {}

    @property
    def stream_ids(self) -> List[str]:
        return sorted(self._streams.keys())

    def duration_ms(self) -> int:
        if not self._streams:
            return 0
        return max(s.duration_ms for s in self._streams.values())

    def register_stream(self, speaker_id: str) -> AudioIngest:
        speaker_id = normalize_speaker_id(speaker_id)
        if speaker_id not in self._streams:
            path = os.path.join(
                self.audio_dir, f"{self.meeting_id}_{speaker_id}.wav"
            )
            self._streams[speaker_id] = AudioIngest(
                sample_rate=self.sample_rate, audio_path=path
            )
        return self._streams[speaker_id]

    def append_int16(self, speaker_id: str, samples: np.ndarray) -> None:
        self.register_stream(speaker_id).append_int16(samples)

    def get_buffer(self, speaker_id: str) -> np.ndarray:
        stream = self._streams.get(normalize_speaker_id(speaker_id))
        if stream is None:
            return np.zeros(0, dtype=np.float32)
        return stream.get_buffer()

    def save_stream_wavs(self) -> Dict[str, str]:
        """Persist each stream to disk; return speaker_id → path."""
        paths: Dict[str, str] = {}
        for speaker_id, ingest in self._streams.items():
            if ingest.total_samples <= 0:
                continue
            try:
                paths[speaker_id] = ingest.save_wav()
            except Exception:
                continue
        return paths

    def mix_down(self) -> np.ndarray:
        """Sum all streams into one mono buffer (same timeline length)."""
        if not self._streams:
            return np.zeros(0, dtype=np.float32)
        max_len = max(len(s.get_buffer()) for s in self._streams.values())
        if max_len <= 0:
            return np.zeros(0, dtype=np.float32)
        mixed = np.zeros(max_len, dtype=np.float32)
        for stream in self._streams.values():
            buf = stream.get_buffer()
            if len(buf) == 0:
                continue
            mixed[: len(buf)] += buf
        peak = float(np.max(np.abs(mixed))) if len(mixed) else 0.0
        if peak > 1.0:
            mixed = (mixed / peak).astype(np.float32)
        return mixed

    def clear(self) -> None:
        for stream in self._streams.values():
            stream.clear()
        self._streams.clear()


class MultiStreamChunkerRegistry:
    """One overlapping chunker per registered stream."""

    def __init__(
        self,
        *,
        sample_rate: int,
        window_ms: int,
        hop_ms: int,
        min_speech_rms: float,
    ):
        self.sample_rate = sample_rate
        self.window_ms = window_ms
        self.hop_ms = hop_ms
        self.min_speech_rms = min_speech_rms
        self._chunkers: Dict[str, OverlappingChunker] = {}

    def reset(self) -> None:
        for chunker in self._chunkers.values():
            chunker.reset()
        self._chunkers.clear()

    def _chunker_for(self, speaker_id: str) -> OverlappingChunker:
        speaker_id = normalize_speaker_id(speaker_id)
        if speaker_id not in self._chunkers:
            self._chunkers[speaker_id] = OverlappingChunker(
                sample_rate=self.sample_rate,
                window_ms=self.window_ms,
                hop_ms=self.hop_ms,
                min_speech_rms=self.min_speech_rms,
            )
        return self._chunkers[speaker_id]

    def pop_ready(self, speaker_id: str, buffer: np.ndarray) -> List[AudioChunk]:
        return self._chunker_for(speaker_id).pop_ready_chunks(buffer)

    def flush(self, speaker_id: str, buffer: np.ndarray) -> Optional[AudioChunk]:
        return self._chunker_for(speaker_id).flush_remainder(buffer)

    def flush_all(
        self, buffers: Dict[str, np.ndarray]
    ) -> List[StreamSttItem]:
        items: List[StreamSttItem] = []
        for speaker_id, buf in buffers.items():
            rem = self.flush(speaker_id, buf)
            if rem is not None:
                items.append(StreamSttItem(speaker_id=speaker_id, chunk=rem))
        return items
