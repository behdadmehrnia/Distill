from __future__ import annotations

import json

import pytest

from api.meeting.diarization import SpeakerDiarizer
from api.meeting.minutes import MeetingMinutesGenerator, merge_partials_local
from api.meeting.models import TranscriptSegment
from api.meeting.review import (
    TranscriptReviewAgent,
    _apply_llm_edit,
    _is_faithful_edit,
    gate_stt_text,
    needs_llm_polish,
)


def _seg(text: str, *, start_ms: int = 0, meeting_id: str = "m") -> TranscriptSegment:
    return TranscriptSegment.create(
        meeting_id=meeting_id,
        speaker_id="SPEAKER_00",
        start_ms=start_ms,
        end_ms=start_ms + 1000,
        text=text,
    )


def test_faithful_edit_rejects_summaries_and_rewrites():
    original = "امروز درباره بودجه پروژه و زمان‌بندی تحویل صحبت کردیم"
    assert _is_faithful_edit(original, original) is True
    # Typo-ish near edit should pass
    near = "امروز درباره بودجه پروژه و زمان بندی تحویل صحبت کردیم"
    assert _is_faithful_edit(original, near) is True
    # Summarization must be rejected
    summary = "درباره بودجه حرف زدیم"
    assert _is_faithful_edit(original, summary) is False
    fixed, action = _apply_llm_edit(original, "fix", summary)
    assert action == "keep"
    assert fixed == original or "بودجه" in fixed


def test_gate_keeps_clean_persian_and_flags_dirty():
    clean = gate_stt_text("امروز جلسه خوبی داشتیم و تصمیم گرفتیم ادامه دهیم.")
    assert clean.accepted
    assert needs_llm_polish(clean.score, clean.reasons) is False

    dirty = gate_stt_text("test test test hello world project")
    # May drop or keep with low score / latin reasons
    if dirty.accepted:
        assert needs_llm_polish(dirty.score, dirty.reasons) is True
    else:
        assert dirty.action == "drop"


class RecordingLLM:
    def __init__(self, responses: list[str] | None = None, *, fail_after: int | None = None):
        self.calls = 0
        self.payloads: list[str] = []
        self.responses = list(responses or [])
        self.fail_after = fail_after

    async def complete(self, messages, temperature=0.0, max_tokens=1024, **kwargs):
        self.calls += 1
        user = messages[-1]["content"]
        self.payloads.append(user)
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("سرور مدل زبانی (LLM) در host در دسترس نیست.")
        if self.responses:
            return self.responses.pop(0)
        start = user.find("[")
        end = user.rfind("]")
        payload = json.loads(user[start : end + 1])
        return json.dumps(
            {
                "items": [
                    {
                        "id": item["id"],
                        "action": "fix",
                        "text": item["text"].replace("تستت", "تست"),
                    }
                    for item in payload
                ]
            },
            ensure_ascii=False,
        )


@pytest.mark.asyncio
async def test_review_only_sends_shaky_segments_to_llm(monkeypatch):
    monkeypatch.setattr("api.meeting.review.review_max_items", lambda: 10)
    llm = RecordingLLM()
    agent = TranscriptReviewAgent(llm=llm, enabled=True)
    clean = "امروز درباره بودجه پروژه صحبت کردیم و تصمیم گرفتیم."
    shaky = "تست تست تست برای بهبود متن جلسه امروز"
    out = await agent.review_segments(
        [_seg(clean, start_ms=0), _seg(shaky, start_ms=1000)],
        language="fa",
    )
    assert len(out) == 2
    assert llm.calls == 1
    assert "تست" in llm.payloads[0]
    assert clean not in llm.payloads[0]


@pytest.mark.asyncio
async def test_review_keeps_heuristic_text_when_llm_unreachable(monkeypatch):
    monkeypatch.setattr("api.meeting.review.needs_llm_polish", lambda score, reasons: True)
    monkeypatch.setattr("api.meeting.review.review_max_batch_chars", lambda: 40)
    llm = RecordingLLM(fail_after=0)
    agent = TranscriptReviewAgent(llm=llm, enabled=True)
    segs = [
        _seg(f"متن جلسه شماره {i} برای بهبود تست", start_ms=i * 1000)
        for i in range(4)
    ]
    out = await agent.review_segments(segs, language="fa")
    assert len(out) == 4
    assert llm.calls == 1  # aborted after first failure
    assert all("متن جلسه" in s.text for s in out)


@pytest.mark.asyncio
async def test_review_rejects_unfaithful_batch_rewrite(monkeypatch):
    monkeypatch.setattr("api.meeting.review.needs_llm_polish", lambda score, reasons: True)

    class SummarizingLLM:
        calls = 0

        async def complete(self, messages, temperature=0.0, max_tokens=1024, **kwargs):
            self.calls += 1
            user = messages[-1]["content"]
            start = user.find("[")
            end = user.rfind("]")
            payload = json.loads(user[start : end + 1])
            return json.dumps(
                {
                    "items": [
                        {
                            "id": item["id"],
                            "action": "fix",
                            "text": "خلاصه کوتاه",
                        }
                        for item in payload
                    ]
                },
                ensure_ascii=False,
            )

    llm = SummarizingLLM()
    agent = TranscriptReviewAgent(llm=llm, enabled=True)
    original = "امروز درباره بودجه پروژه و زمان‌بندی تحویل صحبت کردیم"
    out = await agent.review_segments([_seg(original)], language="fa")
    assert len(out) == 1
    assert out[0].text == original  # summarization discarded


def test_minutes_local_merge_quality_preserves_decisions():
    merged = merge_partials_local(
        [
            {
                "subject_hint": "بودجه ۱۴۰۴",
                "meeting_date": "1404/01/01",
                "location": "تهران",
                "summary": "بخش اول درباره بودجه بود.",
                "decisions": [
                    {
                        "description": "تهیه گزارش مالی",
                        "executor": "علی",
                        "due_date": "فردا",
                        "status": "pending",
                    }
                ],
            },
            {
                "subject_hint": "استخدام",
                "summary": "بخش دوم درباره نیروی انسانی بود.",
                "decisions": [
                    {
                        "description": "تهیه گزارش مالی",  # duplicate
                        "executor": "علی",
                    },
                    {
                        "description": "آگهی استخدام منتشر شود",
                        "executor": "مریم",
                    },
                ],
            },
        ],
        ["علی", "مریم"],
    )
    assert merged["subject"] == "بودجه ۱۴۰۴"
    assert merged["meeting_date"] == "1404/01/01"
    assert merged["location"] == "تهران"
    assert "بخش اول" in merged["summary"] and "بخش دوم" in merged["summary"]
    assert [d["description"] for d in merged["decisions"]] == [
        "تهیه گزارش مالی",
        "آگهی استخدام منتشر شود",
    ]
    assert merged["decisions"][1]["executor"] == "مریم"
    assert merged["attendees"] == ["علی", "مریم"]
    assert merged["absentees"] == []


@pytest.mark.asyncio
async def test_minutes_generate_raises_when_llm_unreachable():
    class BoomLLM:
        async def complete(self, *args, **kwargs):
            raise RuntimeError(
                "سرور مدل زبانی (LLM) در 81.29.248.136 در دسترس نیست. "
                "LLM_ENDPOINT و شبکه/فایروال را بررسی کنید."
            )

    gen = MeetingMinutesGenerator(BoomLLM(), max_chars=50_000)
    with pytest.raises(RuntimeError, match="در دسترس نیست"):
        await gen.generate(
            "m-err",
            [_seg("سلام، امروز جلسه برگزار شد و قرار شد گزارش مالی تا فردا آماده شود.")],
            speaker_map={"SPEAKER_00": "علی"},
        )


@pytest.mark.asyncio
async def test_minutes_extracts_decisions_from_llm():
    class DecisionsLLM:
        async def complete(self, messages, temperature=0.2, max_tokens=2048, **kwargs):
            return json.dumps(
                {
                    "subject": "بودجه",
                    "meeting_date": "",
                    "location": "",
                    "attendees": ["علی", "مریم"],
                    "absentees": [],
                    "secretary": "مریم",
                    "summary": "درباره بودجه و پیگیری‌ها صحبت شد.",
                    "decisions": [
                        {
                            "description": "تهیه گزارش مالی",
                            "executor": "علی",
                            "due_date": "فردا",
                            "status": "pending",
                        },
                        {
                            "description": "هماهنگی جلسه بعد",
                            "executor": "مریم",
                            "due_date": "",
                            "status": "pending",
                        },
                    ],
                },
                ensure_ascii=False,
            )

    gen = MeetingMinutesGenerator(DecisionsLLM(), max_chars=50_000)
    result = await gen.generate(
        "m-dec",
        [
            _seg("باید گزارش مالی تا فردا آماده شود."),
            _seg("مریم جلسه بعد را هماهنگ کند.", start_ms=1000),
        ],
        speaker_map={"SPEAKER_00": "علی", "SPEAKER_01": "مریم"},
    )
    assert len(result.decisions) == 2
    assert result.decisions[0].description == "تهیه گزارش مالی"
    assert result.decisions[0].executor == "علی"
    assert result.decisions[0].due_date == "فردا"
    assert result.decisions[1].executor == "مریم"


def test_pyannote_empty_falls_back_to_heuristic(monkeypatch):
    import numpy as np

    d = SpeakerDiarizer(max_speakers=2, energy_threshold=0.005)
    d.ensure_loaded()
    # Force local pyannote path even if pipeline is None by stubbing.
    d._load["mode"] = "local"
    d._load["pipeline"] = object()
    d._load["backend"] = "pyannote"
    monkeypatch.setattr(d, "_diarize_pyannote", lambda audio, sr: [])

    sr = 16000
    t = np.linspace(0, 3, sr * 3, endpoint=False)
    audio = (0.1 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    intervals = d.diarize(audio, sr)
    assert intervals
    assert all(iv.speaker_id.startswith("SPEAKER_") for iv in intervals)
