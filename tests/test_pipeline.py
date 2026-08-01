"""Integration tests for STT → diarize → align pipeline (mocked STT/LLM)."""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path
from typing import List, Optional

import numpy as np
import pytest

from api.meeting.aligner import align_stt_with_diarization
from api.meeting.chunker import OverlappingChunker
from api.meeting.diarization import SpeakerDiarizer
from api.meeting.models import MeetingRecord
from api.meeting.review import gate_stt_text
from api.meeting.session import MeetingSession
from api.meeting.store import TranscriptStore
from api.tuning import make_tuning


def _sine(sr: int, seconds: float, freq: float, amp: float = 0.15) -> np.ndarray:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _two_speaker_audio(sr: int = 16000) -> np.ndarray:
    """Low tone then high tone to encourage two fallback clusters."""
    a = _sine(sr, 4.0, 120.0, amp=0.2)
    b = _sine(sr, 4.0, 900.0, amp=0.2)
    return np.concatenate([a, b])


def _write_wav(path: Path, audio: np.ndarray, sr: int = 16000) -> Path:
    pcm = np.clip(audio * 32768.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return path


class MockSTT:
    """Deterministic STT: returns fixed text per call; can fail then succeed."""

    def __init__(
        self,
        text: str = "سلام دوستان جلسه را شروع می‌کنیم",
        fail_times: int = 0,
        texts: Optional[List[str]] = None,
    ):
        self.text = text
        self.texts = list(texts) if texts else None
        self.fail_times = fail_times
        self.calls = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self._call_i = 0

    async def transcribe(self, file_content, model=None, language=None) -> str:
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("simulated STT failure")
        self.cache_misses += 1
        if self.texts is not None:
            idx = min(self._call_i, len(self.texts) - 1)
            self._call_i += 1
            return self.texts[idx]
        return self.text


@pytest.fixture
def store(tmp_path):
    return TranscriptStore(db_path=str(tmp_path / "meetings.db"))


def test_pipeline_chunk_stt_diarize_align():
    sr = 16000
    audio = _two_speaker_audio(sr)
    chunker = OverlappingChunker(
        sample_rate=sr, window_ms=4000, hop_ms=3000, min_speech_rms=0.01
    )
    chunks = chunker.pop_ready_chunks(audio)
    rem = chunker.flush_remainder(audio)
    if rem is not None:
        chunks.append(rem)
    assert len(chunks) >= 1

    # Mock STT: different text per chunk index
    stt_results = []
    for i, chunk in enumerate(chunks):
        stt_results.append((chunk.start_ms, chunk.end_ms, f"متن قطعه {i}"))

    diarizer = SpeakerDiarizer(
        sample_rate=sr, max_speakers=2, energy_threshold=0.01, min_speakers=1
    )
    intervals = diarizer.diarize(audio, sr)
    assert intervals
    assert all(iv.speaker_id.startswith("SPEAKER_") for iv in intervals)

    segments = align_stt_with_diarization("m-pipe", stt_results, intervals)
    assert segments
    assert all(s.speaker_id.startswith("SPEAKER_") for s in segments)
    assert all(s.text for s in segments)


@pytest.mark.asyncio
async def test_stt_retry_succeeds_after_failures(store, tmp_path):
    """Mock STT fails twice then succeeds; chunk is eventually processed."""
    stt = MockSTT(text="متن بعد از retry", fail_times=2)
    record = MeetingRecord.create("retry-test")
    session = MeetingSession(
        record=record,
        store=store,
        stt_provider=stt,
        diarizer=SpeakerDiarizer(max_speakers=1),
        audio_dir=str(tmp_path / "audio"),
        tuning=make_tuning({"stt_retry_count": 3, "stt_review_mode": "heuristic", "stt_workers": 1}),
    )
    # Short tone so one chunk after flush
    audio = _sine(16000, 2.0, 220.0)
    session.ingest.append_float32(audio)
    await session.start()
    rem = session.chunker.flush_remainder(session.ingest.get_buffer())
    assert rem is not None
    await session._stt_queue.put(rem)
    await session._stt_queue.put(None)
    await asyncio.gather(*session._worker_tasks)
    session._worker_tasks = []
    session._running = False

    assert stt.calls == 3  # 2 failures + 1 success
    assert session._stt_retries == 2
    assert session._stt_dropped == 0
    assert session._pending_stt
    assert session._pending_stt[0][2] == "متن بعد از retry"


def test_label_persistence_across_diarization_passes():
    sr = 16000
    audio = _two_speaker_audio(sr)
    # Extend so clustering has enough frames
    audio = np.concatenate([audio, audio])
    diarizer = SpeakerDiarizer(
        sample_rate=sr, max_speakers=2, energy_threshold=0.005, merge_short_ms=200
    )
    first = diarizer.diarize(audio, sr)
    assert first
    first_ids = {iv.speaker_id for iv in first}
    assert diarizer._speaker_centroids  # centroids cached

    # Slightly longer / similar audio — labels should stay consistent
    audio2 = np.concatenate([audio, _sine(sr, 1.0, 120.0, amp=0.18)])
    second = diarizer.diarize(audio2, sr)
    second_ids = {iv.speaker_id for iv in second}
    # Every speaker from first pass still present (or remapped to same IDs)
    assert first_ids <= second_ids or first_ids == second_ids
    # No label flip of the entire set to a disjoint namespace
    assert len(first_ids & second_ids) >= 1


def test_quality_gate_drops_hallucination():
    junk = " ".join(["خیلی"] * 40)
    result = gate_stt_text(junk)
    assert not result.accepted
    assert result.action == "drop"


@pytest.mark.asyncio
async def test_upload_path_stores_segments(store, tmp_path):
    sr = 16000
    audio = _sine(sr, 10.0, 200.0, amp=0.12)
    wav_path = _write_wav(tmp_path / "upload.wav", audio, sr)

    stt = MockSTT(text="سلام این یک جلسه آزمایشی است")
    record = MeetingRecord.create("upload-test")
    session = MeetingSession(
        record=record,
        store=store,
        stt_provider=stt,
        diarizer=SpeakerDiarizer(max_speakers=1, energy_threshold=0.01),
        review_agent=None,
        audio_dir=str(tmp_path / "audio"),
        tuning=make_tuning(
            {
                "window_ms": 8000,
                "hop_ms": 6000,
                "stt_workers": 2,
                "stt_review_mode": "heuristic",
                "stt_retry_count": 1,
            }
        ),
    )
    segments = await session.process_uploaded_file(str(wav_path))
    assert segments
    assert all(s.text for s in segments)
    stored = store.get_segments(record.id)
    assert len(stored) == len(segments)
    assert stored[0].text
    meeting = store.get_meeting(record.id)
    assert meeting is not None
    assert meeting.status.value == "stopped"


@pytest.mark.asyncio
async def test_debug_stats_populated(store, tmp_path):
    stt = MockSTT(text="سلام تست دیباگ")
    record = MeetingRecord.create("debug-test")
    session = MeetingSession(
        record=record,
        store=store,
        stt_provider=stt,
        diarizer=SpeakerDiarizer(max_speakers=1),
        audio_dir=str(tmp_path / "audio"),
        tuning=make_tuning({"stt_review_mode": "heuristic", "stt_workers": 1}),
    )
    audio = _sine(16000, 2.0, 180.0)
    session.ingest.append_float32(audio)
    await session.start()
    rem = session.chunker.flush_remainder(session.ingest.get_buffer())
    await session._stt_queue.put(rem)
    await session._stt_queue.put(None)
    await asyncio.gather(*session._worker_tasks)
    session._worker_tasks = []
    session._running = False

    stats = session.debug_stats()
    assert stats["meeting_id"] == record.id
    assert stats["stt_calls"] >= 1
    assert stats["chunks_processed"] >= 1
    assert "tuning" in stats
    assert stats["diarization_backend"] in {"fallback", "pyannote"}
