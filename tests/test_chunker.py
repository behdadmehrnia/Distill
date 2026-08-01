"""Unit tests for OverlappingChunker."""

from __future__ import annotations

import numpy as np

from api.meeting.chunker import OverlappingChunker


def _tone(sr: int, seconds: float, freq: float = 220.0, amp: float = 0.1) -> np.ndarray:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_chunks_emitted_at_hop_interval():
    sr = 16000
    window_ms, hop_ms = 8000, 6000
    # 20s of speech → chunks at 0, 6, 12 (3 full windows)
    audio = _tone(sr, 20.0)
    chunker = OverlappingChunker(
        sample_rate=sr, window_ms=window_ms, hop_ms=hop_ms, min_speech_rms=0.001
    )
    chunks = chunker.pop_ready_chunks(audio)
    assert len(chunks) == 3
    starts = [c.start_ms for c in chunks]
    assert starts == [0, 6000, 12000]
    for c in chunks:
        assert c.end_ms - c.start_ms == window_ms


def test_flush_remainder_emits_partial_tail():
    sr = 16000
    chunker = OverlappingChunker(
        sample_rate=sr, window_ms=8000, hop_ms=6000, min_speech_rms=0.001
    )
    # 10s: one full window at 0–8s, then remainder from 6s → end after next hop advance?
    # After first chunk (0–8), next_start=6000; 6000+8000=14000 > 10000 so no more.
    # Flush should emit 6000–10000.
    audio = _tone(sr, 10.0)
    chunks = chunker.pop_ready_chunks(audio)
    assert len(chunks) == 1
    assert chunks[0].start_ms == 0
    rem = chunker.flush_remainder(audio)
    assert rem is not None
    assert rem.start_ms == 6000
    assert rem.end_ms == 10000
    assert len(rem.audio) == int(sr * 4.0)


def test_silence_below_min_speech_rms_skipped():
    sr = 16000
    chunker = OverlappingChunker(
        sample_rate=sr, window_ms=8000, hop_ms=6000, min_speech_rms=0.05
    )
    # Very quiet noise — below threshold
    audio = (np.random.randn(sr * 10) * 0.001).astype(np.float32)
    chunks = chunker.pop_ready_chunks(audio)
    assert chunks == []
    rem = chunker.flush_remainder(audio)
    assert rem is None


def test_overlap_matches_context_size():
    """8s window / 6s hop → 2s shared context between consecutive chunks."""
    sr = 16000
    window_ms, hop_ms = 8000, 6000
    expected_overlap_ms = window_ms - hop_ms  # 2000
    audio = _tone(sr, 20.0)
    chunker = OverlappingChunker(
        sample_rate=sr, window_ms=window_ms, hop_ms=hop_ms, min_speech_rms=0.001
    )
    chunks = chunker.pop_ready_chunks(audio)
    assert len(chunks) >= 2
    for a, b in zip(chunks, chunks[1:]):
        overlap = a.end_ms - b.start_ms
        assert overlap == expected_overlap_ms
        # Sample-level check: last 2s of A ≈ first 2s of B
        n = int(sr * expected_overlap_ms / 1000)
        np.testing.assert_allclose(a.audio[-n:], b.audio[:n], atol=1e-6)
