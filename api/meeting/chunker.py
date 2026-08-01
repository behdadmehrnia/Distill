from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class AudioChunk:
    """A time-aligned window of audio for STT."""

    start_ms: int
    end_ms: int
    audio: np.ndarray  # float32 mono
    index: int


class OverlappingChunker:
    """
    Emit overlapping windows from a continuous PCM buffer.

    Defaults: 8s window / 6s hop (~2s context overlap for Whisper continuity).
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        window_ms: int = 8000,
        hop_ms: int = 6000,
        min_speech_rms: float = 0.008,
    ):
        self.sample_rate = sample_rate
        self.window_samples = int(sample_rate * window_ms / 1000)
        self.hop_samples = int(sample_rate * hop_ms / 1000)
        self.min_speech_rms = min_speech_rms
        self._next_start = 0
        self._chunk_index = 0

    def reset(self) -> None:
        self._next_start = 0
        self._chunk_index = 0

    def samples_to_ms(self, samples: int) -> int:
        return int(samples * 1000 / self.sample_rate)

    def pop_ready_chunks(self, buffer: np.ndarray) -> List[AudioChunk]:
        """Return all complete windows available from the current buffer."""
        chunks: List[AudioChunk] = []
        while self._next_start + self.window_samples <= len(buffer):
            start = self._next_start
            end = start + self.window_samples
            audio = buffer[start:end]
            if float(np.sqrt(np.mean(np.square(audio)))) >= self.min_speech_rms:
                chunks.append(
                    AudioChunk(
                        start_ms=self.samples_to_ms(start),
                        end_ms=self.samples_to_ms(end),
                        audio=audio.copy(),
                        index=self._chunk_index,
                    )
                )
            self._chunk_index += 1
            self._next_start += self.hop_samples
        return chunks

    def flush_remainder(self, buffer: np.ndarray) -> Optional[AudioChunk]:
        """Emit a final partial window on stop, if enough audio remains."""
        if self._next_start >= len(buffer):
            return None
        audio = buffer[self._next_start :]
        min_samples = int(self.sample_rate * 0.4)  # at least 400ms
        if len(audio) < min_samples:
            return None
        if float(np.sqrt(np.mean(np.square(audio)))) < self.min_speech_rms:
            return None
        chunk = AudioChunk(
            start_ms=self.samples_to_ms(self._next_start),
            end_ms=self.samples_to_ms(len(buffer)),
            audio=audio.copy(),
            index=self._chunk_index,
        )
        self._chunk_index += 1
        self._next_start = len(buffer)
        return chunk
