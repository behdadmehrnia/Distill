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
from api.meeting.review import TranscriptReviewAgent, gate_stt_text
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
        SpeakerInterval("SPEAKER_00", 0, 5000, False),
        SpeakerInterval("SPEAKER_01", 1000, 6000, True),
    ]
    speaker, overlap = dominant_speaker(0, 6000, intervals)
    assert speaker in {"SPEAKER_00", "SPEAKER_01"}
    assert overlap is True

    turns = [
        SpeakerInterval("SPEAKER_00", 0, 4000, False),
        SpeakerInterval("SPEAKER_01", 4000, 8000, False),
    ]
    _, overlap2 = dominant_speaker(0, 8000, turns)
    assert overlap2 is False


def test_align_emits_both_speakers_on_overlap():
    intervals = [
        SpeakerInterval("SPEAKER_00", 0, 8000, True),
        SpeakerInterval("SPEAKER_01", 2000, 7000, True),
    ]
    segments = align_stt_with_diarization(
        "m1",
        [(0, 8000, "سلام هم‌زمان صحبت می‌کنیم")],
        intervals,
    )
    overlap_rows = [s for s in segments if s.is_overlap]
    speakers = {s.speaker_id for s in overlap_rows}
    assert speakers == {"SPEAKER_00", "SPEAKER_01"}
    assert all(s.text for s in overlap_rows)


def test_align_and_dedupe():
    intervals = [SpeakerInterval("SPEAKER_00", 0, 20000, False)]
    segments = align_stt_with_diarization(
        "m1",
        [
            (0, 8000, "سلام دوستان امروز درباره چت جی پی تی صحبت می‌کنیم"),
            (2000, 10000, "سلام دوستان امروز درباره چت جی پی تی صحبت می‌کنیم و کاربردش"),
            (4000, 12000, "امروز درباره چت جی پی تی صحبت می‌کنیم و کاربردش در ۱۴۰۵"),
            (6000, 14000, "."),
        ],
        intervals,
    )
    assert len(segments) == 1
    assert segments[0].speaker_id == "SPEAKER_00"
    assert not segments[0].is_overlap


def test_align_skips_false_overlap_from_frame_edge_flicker():
    """Abutting turns + tiny 40ms overlaps must not become هم‌صحبتی."""
    intervals = [
        SpeakerInterval("SPEAKER_00", 0, 3040, False),
        SpeakerInterval("SPEAKER_01", 3000, 6040, False),  # 40ms edge flicker
        SpeakerInterval("SPEAKER_00", 6000, 8000, False),
    ]
    segments = align_stt_with_diarization(
        "m1",
        [(0, 8000, "سلام فقط یک نفر صحبت می‌کند")],
        intervals,
        min_overlap_ms=1200,
    )
    assert segments
    assert all(not s.is_overlap for s in segments)


def test_fallback_diarization_prefers_single_speaker_on_mono_noise():
    sr = 16000
    rng = np.random.default_rng(0)
    # One continuous voice-like signal (not two talkers)
    t = np.linspace(0, 6, sr * 6, endpoint=False)
    audio = (0.05 * np.sin(2 * np.pi * 180 * t) + 0.01 * rng.standard_normal(len(t))).astype(
        np.float32
    )
    diarizer = SpeakerDiarizer(max_speakers=2, energy_threshold=0.005)
    intervals = diarizer.diarize(audio, sr)
    speakers = {iv.speaker_id for iv in intervals}
    assert len(speakers) == 1
    # No temporal overlaps between intervals
    ordered = sorted(intervals, key=lambda x: x.start_ms)
    for a, b in zip(ordered, ordered[1:]):
        assert a.end_ms <= b.start_ms + 1


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


def test_stt_review_drops_whisper_hallucination_loop():
    junk = " ".join(["خیلی"] * 40)
    result = gate_stt_text(junk)
    assert result.action == "drop"
    assert not result.accepted
    assert result.score < 0.35


def test_stt_review_keeps_normal_persian():
    result = gate_stt_text("چه خبره صدای چیز")
    assert result.accepted
    assert result.action in {"keep", "fix"}
    assert "خبر" in result.text


def test_stt_review_collapses_mild_repetition():
    result = gate_stt_text("خیلی خیلی خیلی خوب بود جلسه")
    assert result.accepted
    # Consecutive runs collapsed to one token
    assert result.text.count("خیلی") <= 2


def test_localize_nonspeech_events_to_persian():
    from api.meeting.review import localize_nonspeech_events

    assert localize_nonspeech_events("(cough) .") == "(سرفه) ."
    assert localize_nonspeech_events("(cough) . (Sigh) .") == "(سرفه) . (آه) ."
    result = gate_stt_text("(cough) . (Sigh) .")
    assert result.accepted
    assert "(سرفه)" in result.text
    assert "(آه)" in result.text
    assert "cough" not in result.text.lower()
    assert "sigh" not in result.text.lower()


@pytest.mark.asyncio
async def test_review_agent_finalize_without_llm():
    agent = TranscriptReviewAgent(llm=None, enabled=False)
    segs = [
        TranscriptSegment.create("m1", "SPEAKER_00", 0, 1000, " ".join(["خیلی"] * 30)),
        TranscriptSegment.create("m1", "SPEAKER_00", 1000, 2000, "سلام دوستان"),
    ]
    out = await agent.review_segments(segs)
    assert len(out) == 1
    assert out[0].text == "سلام دوستان"
