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


def test_live_whisper_variants_coalesce_to_one_row():
    """User said one sentence; overlapping hops must show as one provisional row."""
    intervals = [SpeakerInterval("SPEAKER_00", 0, 20000, False)]
    segments = align_stt_with_diarization(
        "m1",
        [
            (0, 8000, "خب سلام الان میخوام ببینم متن رو چهار بار نمیرسی برام یا نه؟ میشه لطفا فعالان متن های"),
            (
                2000,
                10000,
                "سلام، الان می‌خوام ببینم متن رو چهار بار می‌نویسی برام یا نه؟ می‌شه لطفاً فعالان متن‌هایی که دارم بهت می‌گم رو سامیز نکن؟",
            ),
            (6000, 14000, "میشه لطفاً فعالان متن هایی که دارم بهت میگم رو سامورایز نکنی؟"),
            (8000, 16000, "دارم بهت میگم رو سامورایز نکنی."),
        ],
        intervals,
    )
    assert len(segments) == 1
    text = segments[0].text
    assert "چهار بار" in text
    # Should keep the fuller form, not 4 separate cards
    assert text.count("سامیز") + text.count("سامورایز") <= 1


def test_same_utterance_helper():
    from api.meeting.aligner import _same_utterance

    partial = "خب سلام الان میخوام ببینم متن رو چهار بار"
    full = "سلام، الان می‌خوام ببینم متن رو چهار بار می‌نویسی برام یا نه؟"
    assert _same_utterance(partial, full)
    assert not _same_utterance(
        "امروز درباره بودجه پروژه صحبت می‌کنیم",
        "خب الان یه نفر دیگه بخواد حرف بزنه",
    )


def test_dissimilar_hops_do_not_erase_earlier_speech():
    """Regression: later hop must not replace the whole timeline text."""
    intervals = [SpeakerInterval("SPEAKER_00", 0, 60000, False)]
    early = "امروز درباره بودجه پروژه و زمان‌بندی اسپرینت صحبت می‌کنیم"
    late = "خب الان یه نفر دیگه بخواد حرف بزنه شما ببینید"
    segments = align_stt_with_diarization(
        "m1",
        [
            (0, 8000, early),
            (6000, 14000, "ادامه بحث بودجه و زمان‌بندی"),
            (60000, 68000, late),  # much later, no overlap chain swallow
            (66000, 74000, late),
        ],
        intervals,
    )
    texts = " ".join(s.text for s in segments)
    assert "بودجه" in texts
    assert "حرف بزنه" in texts
    # Must not be a single segment spanning 0→74s with only the late text
    assert not (
        len(segments) == 1
        and segments[0].start_ms == 0
        and "بودجه" not in segments[0].text
    )


def test_collapse_internal_repeats_helper():
    from api.meeting.aligner import _collapse_internal_repeats

    doubled = "البته این بار چهار بار ننوشتش عجیبه. البته این بار چهار بار ننوشتش عجیبه."
    assert _collapse_internal_repeats(doubled).count("عجیبه") == 1
    assert (
        _collapse_internal_repeats("ننوشتش عجیبه ننوشتش عجیبه")
        == "ننوشتش عجیبه"
    )


def test_pick_hop_text_never_concatenates():
    from api.meeting.aligner import _pick_hop_text

    a = "خب میخوام ببینم بازم همه چیو چهار بار تکرار میکنه یا نه"
    b = "خب میخوام ببینم بازم همه چیو چهار بار تکرار میکنه یا نه عجیبه"
    out = _pick_hop_text(a, b)
    assert out != f"{a} {b}"
    assert "عجیبه" in out or out == a


def test_monologue_hops_stitch_into_one_continuous_row():
    """TDD-style continuous reading must not become many overlapping cards."""
    intervals = [SpeakerInterval("SPEAKER_00", 0, 60000, False)]
    segments = align_stt_with_diarization(
        "m1",
        [
            (
                0,
                8000,
                "توسعه مبتنی بر تست یا همان تستینگ و دیزاین رویکردی در مهندسی نرم‌افزار است که در آن ابتدا یک تست واحد یا همان یونیت تست",
            ),
            (
                6000,
                14000,
                "برای عبور از آن تست پیاده سازی شده و در نهایت فرآیند رفاکتورینگ انجام میگیرد تا ساختار کد بدون تغییر رفتار آن بهبود یابد",
            ),
            (
                12000,
                20000,
                "این چرخه با نام Red-Green-Refactor شناخته میشود علاوه بر افزایش قابلیت اطمینان نرم‌افزار باعث کاهش تکنیکال دپت",
            ),
            (
                18000,
                26000,
                "بهبود دیزاین API و افزایش maintainability میشود در پروژه‌های مدرن TDD معمولاً در کنار ابزارهایی مانند JUnit Pytest",
            ),
            (
                24000,
                32000,
                "تی دی دی معمولاً در کنار ابزارهایی مانند جی یونیت پایتست و پایپلاینهای سی آی سی دی اجرا میشود تا هر کامیت به سرعت",
            ),
            (
                30000,
                38000,
                "استفاده از ماک و استاب نیز در این فرآیند اهمیت بالایی دارد زیرا امکان تست مستقل اجزای سیستم را فراهم میکند",
            ),
        ],
        intervals,
    )
    texts = " ".join(s.text for s in segments)
    assert "یونیت تست" in texts or "تست واحد" in texts
    assert "رفاکتورینگ" in texts or "Refactor" in texts.lower() or "بهبود" in texts
    assert "ماک" in texts or "استاب" in texts or "مستقل" in texts
    # Should coalesce overlapping hops, not leave ~6 near-duplicate cards
    assert len(segments) <= 3


def test_stitch_keeps_prefix_and_suffix():
    from api.meeting.aligner import _stitch_hop_texts

    a = "توسعه مبتنی بر تست رویکردی در مهندسی نرم‌افزار است که در آن ابتدا یک تست واحد"
    b = "ابتدا یک تست واحد نوشته میشود سپس حداقل کد لازم برای عبور از آن تست"
    out = _stitch_hop_texts(a, b)
    assert "توسعه مبتنی بر تست" in out
    assert "عبور از آن تست" in out


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


def test_insights_meaningful_speech_gate():
    from api.meeting.insights import has_meaningful_speech

    noise = [
        TranscriptSegment.create("m1", "SPEAKER_00", 0, 1000, ".\n(سرفه) .\n(Sound of a car)"),
    ]
    speech = [
        TranscriptSegment.create("m1", "SPEAKER_00", 0, 1000, "(سرفه) سلام، جلسه را شروع کنیم"),
    ]
    assert not has_meaningful_speech(noise)
    assert has_meaningful_speech(speech)


def test_llm_endpoint_normalization():
    from api.providers.llm import normalize_chat_completions_url

    assert (
        normalize_chat_completions_url("http://host/api/v1")
        == "http://host/api/v1/chat/completions"
    )
    assert (
        normalize_chat_completions_url("http://host/api/v1/chat/completions")
        == "http://host/api/v1/chat/completions"
    )


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


def test_stt_review_drops_latin_language_drift():
    """gpt-4o-mini-transcribe sometimes hops into Polish/Hungarian gibberish."""
    for junk in (
        "penavava a",
        ".Byliśmy nauczeni",
        ".Hov, pasmodovoy",
        ".Hú, azóta egy sűrűj",
        ".Hová az matematikai sérítsem",
    ):
        result = gate_stt_text(junk, language="fa")
        assert not result.accepted, junk
        assert "wrong_script" in result.reasons or "no_persian" in result.reasons


def test_stt_review_keeps_mixed_persian_with_light_latin():
    result = gate_stt_text("سلام OK بود", language="fa")
    assert result.accepted


def test_stt_review_allows_latin_when_language_en():
    result = gate_stt_text("We were taught mathematics", language="en")
    assert result.accepted


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


def test_polish_prompt_file_loads():
    from api.meeting.review import _PROMPT_FILE, _load_polish_prompts

    assert _PROMPT_FILE.is_file(), _PROMPT_FILE
    single, batch = _load_polish_prompts()
    assert "keep" in single.lower()
    assert "drop" in single.lower()
    assert "items" in batch.lower()
    assert "خلاصه" in single or "summar" in single.lower()


def test_faithful_edit_rejects_summaries():
    from api.meeting.review import _is_faithful_edit

    full = "خب سلام الان میتونی متوجه شی من دارم چی میگم یا نمیتونی متوجه شی"
    summary = "سلام، آیا متوجه می‌شوی؟"
    assert not _is_faithful_edit(full, summary)
    assert _is_faithful_edit(full, full)
    # Light typo-style fix should pass
    light = "خب سلام، الان می‌تونی متوجه شی من دارم چی میگم یا نمی‌تونی متوجه شی"
    assert _is_faithful_edit(full, light)


def test_apply_llm_edit_keeps_original_on_summary():
    from api.meeting.review import _apply_llm_edit

    full = "خب سلام الان میتونی متوجه شی من دارم چی میگم یا نمیتونی متوجه شی من دارم چی میگم"
    text, action = _apply_llm_edit(full, "fix", "سلام، متوجه می‌شوی؟")
    assert action == "keep"
    assert text == full or "متوجه شی" in text


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
