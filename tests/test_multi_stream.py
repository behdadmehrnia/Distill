"""Tests for multi-stream capture (diarization bypass)."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from api.meeting.models import CaptureMode, MeetingRecord
from api.meeting.multi_stream import (
    MultiStreamIngest,
    merge_speaker_intervals,
    normalize_speaker_id,
)
from api.meeting.session import MeetingSession
from api.meeting.store import TranscriptStore
from api.tuning import make_tuning


def _tone(sr: int, seconds: float, freq: float, amp: float = 0.2) -> np.ndarray:
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class MockSTT:
    def __init__(self, text: str = "سلام از استریم"):
        self.text = text
        self.calls = 0
        self.cache_hits = 0
        self.cache_misses = 0

    async def transcribe(self, file_content, model=None, language=None) -> str:
        self.calls += 1
        self.cache_misses += 1
        return self.text


@pytest.fixture
def store(database_url):
    return TranscriptStore(database_url=database_url)


def test_normalize_speaker_id():
    assert normalize_speaker_id("alice") == "SPEAKER_alice"
    assert normalize_speaker_id("SPEAKER_00") == "SPEAKER_00"


def test_merge_speaker_intervals():
    from api.meeting.models import SpeakerInterval

    merged = merge_speaker_intervals(
        [
            SpeakerInterval("SPEAKER_a", 0, 1000, False),
            SpeakerInterval("SPEAKER_a", 1100, 2000, False),
            SpeakerInterval("SPEAKER_b", 500, 1500, False),
        ]
    )
    assert len(merged) == 2
    assert merged[0].speaker_id == "SPEAKER_a"
    assert merged[0].end_ms == 2000


def test_multi_stream_ingest_mix_down():
    ingest = MultiStreamIngest(sample_rate=16000, audio_dir="/tmp", meeting_id="m1")
    a = _tone(16000, 0.5, 200)
    b = _tone(16000, 0.25, 400)
    ingest.append_int16("p1", (a * 32768).astype(np.int16))
    ingest.append_int16("p2", (b * 32768).astype(np.int16))
    mixed = ingest.mix_down()
    assert len(mixed) == len(a)
    assert float(np.max(np.abs(mixed))) <= 1.0


@pytest.mark.asyncio
async def test_multi_stream_session_skips_diarization(store, tmp_path):
    record = MeetingRecord.create(
        title="Multi",
        capture_mode=CaptureMode.MULTI_STREAM,
        streams={"alice": "Alice", "bob": "Bob"},
    )
    store.save_meeting(record)
    events = []

    async def on_event(event):
        events.append(event)

    session = MeetingSession(
        record=record,
        store=store,
        stt_provider=MockSTT(text="سلام از استریم چند‌مسیره"),
        diarizer=None,
        sample_rate=16000,
        window_ms=400,
        hop_ms=400,
        audio_dir=str(tmp_path),
        on_event=on_event,
        tuning=make_tuning(
            {
                "min_speech_rms": 0.01,
                "stt_workers": 1,
                "window_ms": 400,
                "hop_ms": 400,
                "stt_review_mode": "off",
            }
        ),
    )
    assert session.uses_diarization is False
    assert session.is_multi_stream is True

    await session.start()
    tone = _tone(16000, 1.0, 300, amp=0.25)
    pcm = (tone * 32768).astype(np.int16)
    stt = session.stt
    await session.append_stream_audio_int16("alice", pcm)
    await session.append_stream_audio_int16("bob", pcm)
    # Live path must not call STT — only buffer.
    assert stt.calls == 0
    await asyncio.sleep(0.05)
    record = await session.stop()

    assert record.status.value == "stopped"
    assert record.speaker_map.get("SPEAKER_alice") == "Alice"
    assert stt.calls >= 1  # batch STT on stop
    segments = store.get_segments(record.id)
    assert segments
    speakers = {s.speaker_id for s in segments}
    assert "SPEAKER_alice" in speakers or "SPEAKER_bob" in speakers
    intervals = store.get_speaker_intervals(record.id)
    assert intervals
    assert all(iv.speaker_id.startswith("SPEAKER_") for iv in intervals)
    speaker_updates = [e for e in events if e.get("type") == "speaker_update"]
    assert speaker_updates
    assert speaker_updates[-1].get("backend") == "stream"
    texts = " ".join(s.text for s in segments)
    assert "سلام" in texts or "استریم" in texts
