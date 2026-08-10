from __future__ import annotations

import json

import pytest

from api.meeting.minutes import (
    MeetingMinutesGenerator,
    split_transcript,
)
from api.meeting.models import TranscriptSegment


class FakeLLM:
    def __init__(self):
        self.calls: list[dict] = []

    async def complete(self, messages, temperature=0.2, max_tokens=2048, **kwargs):
        system = (messages[0].get("content") or "") if messages else ""
        user = (messages[-1].get("content") or "") if messages else ""
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})

        if "ONE PART" in system or "این بخش" in user:
            # Map step
            n = sum(1 for c in self.calls if "ONE PART" in c["system"] or "این بخش" in c["user"])
            return json.dumps(
                {
                    "subject_hint": f"بخش {n}",
                    "meeting_date": "",
                    "location": "",
                    "summary": f"خلاصه بخش {n}",
                    "decisions": [
                        {
                            "description": f"مصوبه بخش {n}",
                            "executor": "علی",
                            "due_date": "",
                            "status": "pending",
                        }
                    ],
                },
                ensure_ascii=False,
            )

        if "partial minutes extracts" in system or "partial extracts" in user:
            return json.dumps(
                {
                    "subject": "جلسه ادغام‌شده",
                    "meeting_date": "",
                    "location": "",
                    "attendees": ["علی"],
                    "absentees": ["باید حذف شود"],
                    "secretary": "بیگانه",
                    "summary": "خلاصه نهایی ادغام‌شده",
                    "decisions": [
                        {
                            "description": "مصوبه نهایی",
                            "executor": "علی",
                            "due_date": "فردا",
                            "status": "pending",
                        }
                    ],
                },
                ensure_ascii=False,
            )

        # Full single-shot
        return json.dumps(
            {
                "subject": "جلسه کوتاه",
                "meeting_date": "",
                "location": "دفتر",
                "attendees": ["علی", "مهمان ساختگی"],
                "absentees": ["غایب ساختگی"],
                "secretary": "علی",
                "summary": "خلاصه کوتاه",
                "decisions": [
                    {
                        "description": "یک مصوبه",
                        "executor": "علی",
                        "due_date": "",
                        "status": "pending",
                    }
                ],
            },
            ensure_ascii=False,
        )


def _seg(meeting_id: str, text: str, start_ms: int = 0) -> TranscriptSegment:
    return TranscriptSegment.create(
        meeting_id=meeting_id,
        speaker_id="SPEAKER_00",
        start_ms=start_ms,
        end_ms=start_ms + 1000,
        text=text,
    )


def test_split_transcript_keeps_line_boundaries():
    lines = [f"line-{i} " + ("x" * 20) for i in range(10)]
    text = "\n".join(lines)
    chunks = split_transcript(text, max_chars=80)
    assert len(chunks) >= 2
    assert all(len(c) <= 80 for c in chunks)
    assert "\n".join(chunks) == text


def test_split_transcript_under_budget_is_single():
    text = "hello\nworld"
    assert split_transcript(text, max_chars=1000) == [text]


@pytest.mark.asyncio
async def test_minutes_short_transcript_single_llm_call():
    llm = FakeLLM()
    gen = MeetingMinutesGenerator(llm, max_chars=50_000)
    segments = [_seg("m1", "سلام، امروز درباره بودجه صحبت می‌کنیم.")]
    result = await gen.generate(
        "m1",
        segments,
        speaker_map={"SPEAKER_00": "علی"},
    )
    assert len(llm.calls) == 1
    assert result.subject == "جلسه کوتاه"
    assert result.attendees == ["علی"]
    assert result.absentees == []
    assert result.secretary == "علی"
    assert len(result.decisions) == 1


@pytest.mark.asyncio
async def test_minutes_long_transcript_uses_map_reduce():
    llm = FakeLLM()
    # Tiny budget forces multiple chunks from many short segment lines.
    gen = MeetingMinutesGenerator(llm, max_chars=120)
    segments = [
        _seg("m2", f"صحبت شماره {i} درباره موضوع جلسه طولانی است.", start_ms=i * 1000)
        for i in range(12)
    ]
    result = await gen.generate(
        "m2",
        segments,
        speaker_map={"SPEAKER_00": "علی"},
    )

    # N partial extracts + 1 merge
    assert len(llm.calls) >= 3
    map_calls = [
        c
        for c in llm.calls
        if "ONE PART" in c["system"] or "این بخش" in c["user"]
    ]
    merge_calls = [
        c
        for c in llm.calls
        if "partial minutes extracts" in c["system"] or "partial extracts" in c["user"]
    ]
    assert len(map_calls) >= 2
    assert len(merge_calls) == 1
    assert len(llm.calls) == len(map_calls) + len(merge_calls)

    assert result.subject == "جلسه ادغام‌شده"
    assert result.summary == "خلاصه نهایی ادغام‌شده"
    assert result.attendees == ["علی"]
    assert result.absentees == []  # forced empty despite model inventing
    assert result.secretary == ""  # invented secretary stripped
    assert result.decisions[0].description == "مصوبه نهایی"
    assert result.decisions[0].executor == "علی"
