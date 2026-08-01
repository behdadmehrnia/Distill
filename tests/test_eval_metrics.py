"""Minimal WER/DER harness + word-timing alignment tests."""

from __future__ import annotations

import numpy as np

from api.meeting.aligner import align_stt_with_diarization
from api.meeting.diarization import SpeakerDiarizer
from api.meeting.eval_metrics import diarization_error_rate, word_error_rate
from api.meeting.models import SpeakerInterval


def test_word_error_rate_perfect_and_errors():
    assert word_error_rate("سلام دوستان", "سلام دوستان") == 0.0
    wer = word_error_rate("سلام دوستان عزیز", "سلام دوستان")
    assert 0.0 < wer <= 1.0


def test_diarization_error_rate_identical_zero():
    ref = [
        SpeakerInterval("SPEAKER_00", 0, 4000),
        SpeakerInterval("SPEAKER_01", 4000, 8000),
    ]
    assert diarization_error_rate(ref, ref) == 0.0


def test_diarization_error_rate_swap_is_high():
    ref = [
        SpeakerInterval("SPEAKER_00", 0, 4000),
        SpeakerInterval("SPEAKER_01", 4000, 8000),
    ]
    hyp = [
        SpeakerInterval("SPEAKER_01", 0, 4000),
        SpeakerInterval("SPEAKER_00", 4000, 8000),
    ]
    assert diarization_error_rate(ref, hyp) > 0.5


def test_align_splits_on_word_timings():
    intervals = [
        SpeakerInterval("SPEAKER_00", 0, 4000, False),
        SpeakerInterval("SPEAKER_01", 4000, 8000, False),
    ]
    words = [
        ("سلام", 0, 1000),
        ("علی", 1000, 2000),
        ("صبح", 4500, 5500),
        ("بخیر", 5500, 6500),
    ]
    segments = align_stt_with_diarization(
        "m1",
        [(0, 8000, "سلام علی صبح بخیر", words)],
        intervals,
    )
    speakers = {s.speaker_id for s in segments}
    assert speakers == {"SPEAKER_00", "SPEAKER_01"}
    by_spk = {s.speaker_id: s.text for s in segments}
    assert "سلام" in by_spk["SPEAKER_00"]
    assert "صبح" in by_spk["SPEAKER_01"]


def test_diarizer_fork_isolates_state():
    parent = SpeakerDiarizer(max_speakers=2, energy_threshold=0.005)
    sr = 16000
    t = np.linspace(0, 3, sr * 3, endpoint=False)
    audio = (0.1 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    parent.diarize(audio, sr)
    assert parent._speaker_centroids

    child = parent.fork()
    assert child._pipeline is parent._pipeline
    assert child.backend == parent.backend
    assert child._speaker_centroids == {}
    assert child._label_map == {}
    assert child._prev_intervals == []

    # Mutating child must not clear parent caches
    child.diarize(audio, sr)
    assert parent._speaker_centroids
    assert child._speaker_centroids
    # Different dict objects
    assert child._speaker_centroids is not parent._speaker_centroids
