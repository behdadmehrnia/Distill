import pytest
import numpy as np

from api.meeting.aligner import (
    align_stt_with_diarization,
    dedupe_overlapping_transcripts,
    dominant_speaker,
)
from api.meeting.chunker import OverlappingChunker
from api.meeting.diarization import SpeakerDiarizer
from api.meeting.insights import _parse_json_response, format_transcript_for_llm
from api.meeting.models import MeetingRecord, SpeakerInterval, TranscriptSegment
from api.meeting.store import TranscriptStore


@pytest.fixture
def store(tmp_path):
    return TranscriptStore(db_path=str(tmp_path / "meetings.db"))


def test_chunker_emits_overlapping_windows():
    sr = 16000
    audio = (np.random.randn(sr * 10) * 0.05).astype(np.float32)
    chunker = OverlappingChunker(sample_rate=sr, window_ms=8000, hop_ms=2000)
    chunks = chunker.pop_ready_chunks(audio)
    assert len(chunks) >= 1
    assert chunks[0].end_ms - chunks[0].start_ms == 8000


def test_dominant_speaker_and_overlap():
    intervals = [
        SpeakerInterval("SPEAKER_00", 0, 3000, False),
        SpeakerInterval("SPEAKER_01", 2000, 5000, True),
    ]
    speaker, overlap = dominant_speaker(2200, 2800, intervals)
    assert speaker in {"SPEAKER_00", "SPEAKER_01"}
    assert overlap is True


def test_align_and_dedupe():
    intervals = [SpeakerInterval("SPEAKER_00", 0, 10000, False)]
    segments = align_stt_with_diarization(
        "m1",
        [(0, 2000, "سلام"), (100, 2100, "سلام")],
        intervals,
    )
    deduped = dedupe_overlapping_transcripts(segments)
    assert len(deduped) >= 1
    assert deduped[0].speaker_id == "SPEAKER_00"


def test_store_roundtrip(store):
    meeting = MeetingRecord.create("جلسه تست")
    store.save_meeting(meeting)
    loaded = store.get_meeting(meeting.id)
    assert loaded is not None
    assert loaded.title == "جلسه تست"

    seg = TranscriptSegment.create(meeting.id, "SPEAKER_00", 0, 1000, "سلام")
    store.save_segment(seg)
    segs = store.get_segments(meeting.id)
    assert len(segs) == 1
    assert segs[0].text == "سلام"


def test_diarization_fallback_returns_intervals():
    sr = 16000
    audio = (np.random.randn(sr * 5) * 0.08).astype(np.float32)
    diarizer = SpeakerDiarizer()
    intervals = diarizer.diarize(audio, sr)
    assert diarizer.backend in {"fallback", "pyannote"}
    assert isinstance(intervals, list)


def test_insights_json_parse_and_format():
    data = _parse_json_response(
        '```json\n{"summary":"خوب","highlights":["ا"],"decisions":[],"action_items":[]}\n```'
    )
    assert data["summary"] == "خوب"
    seg = TranscriptSegment.create("m1", "SPEAKER_00", 0, 1500, "سلام")
    text = format_transcript_for_llm([seg])
    assert "SPEAKER_00" in text
    assert "سلام" in text
