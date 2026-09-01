from __future__ import annotations

import asyncio
import json

import pytest

from api.meeting.minutes import (
    MeetingMinutesGenerator,
    cap_chunks,
    merge_partials_local,
    split_transcript,
)
from api.meeting.models import TranscriptSegment


class FakeLLM:
    def __init__(self, *, delay: float = 0.0):
        self.calls: list[dict] = []
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0

    async def complete(self, messages, temperature=0.2, max_tokens=2048, **kwargs):
        system = (messages[0].get("content") or "") if messages else ""
        user = (messages[-1].get("content") or "") if messages else ""
        self.calls.append(
            {
                "system": system,
                "user": user,
                "max_tokens": max_tokens,
                "timeout": kwargs.get("timeout"),
            }
        )
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        if self.delay:
            await asyncio.sleep(self.delay)
        self.in_flight -= 1

        if "ONE PART" in system or "This is part" in user:
            n = sum(
                1
                for c in self.calls
                if "ONE PART" in c["system"] or "This is part" in c["user"]
            )
            return json.dumps(
                {
                    "subject_hint": f"Part {n}",
                    "meeting_date": "",
                    "location": "",
                    "summary": f"Summary of part {n}",
                    "decisions": [
                        {
                            "description": f"Decision in part {n}",
                            "executor": "Alex",
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
                    "subject": "LLM merged meeting",
                    "meeting_date": "",
                    "location": "",
                    "attendees": ["Alex"],
                    "absentees": ["should be dropped"],
                    "secretary": "Outsider",
                    "summary": "final merged summary",
                    "decisions": [
                        {
                            "description": "final decision",
                            "executor": "Alex",
                            "due_date": "tomorrow",
                            "status": "pending",
                        }
                    ],
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "subject": "Short meeting",
                "meeting_date": "",
                "location": "Office",
                "attendees": ["Alex", "Fabricated guest"],
                "absentees": ["Fabricated absentee"],
                "secretary": "Alex",
                "summary": "Short summary",
                "decisions": [
                    {
                        "description": "A decision",
                        "executor": "Alex",
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


def test_cap_chunks_folds_overflow():
    chunks = ["a", "b", "c", "d", "e"]
    capped = cap_chunks(chunks, 3)
    assert capped == ["a", "b", "c\nd\ne"]


def test_merge_partials_local_dedupes_and_keeps_attendees():
    merged = merge_partials_local(
        [
            {
                "subject_hint": "Budget",
                "summary": "Summary one",
                "decisions": [
                    {"description": "Shared decision", "executor": "Alex"},
                    {"description": "Decision A", "executor": "Outsider"},
                ],
            },
            {
                "subject_hint": "Ignored",
                "summary": "Summary two",
                "decisions": [
                    {"description": "Shared decision", "executor": "Alex"},
                    {"description": "Decision B", "executor": "Alex"},
                ],
            },
        ],
        ["Alex"],
    )
    assert merged["subject"] == "Budget"
    assert "Summary one" in merged["summary"] and "Summary two" in merged["summary"]
    assert merged["attendees"] == ["Alex"]
    assert merged["absentees"] == []
    descs = [d["description"] for d in merged["decisions"]]
    assert descs == ["Shared decision", "Decision A", "Decision B"]
    assert merged["decisions"][1]["executor"] == ""  # non-attendee stripped


@pytest.mark.asyncio
async def test_minutes_short_transcript_single_llm_call():
    llm = FakeLLM()
    gen = MeetingMinutesGenerator(llm, max_chars=50_000)
    segments = [_seg("m1", "Hello, today we are discussing the budget.")]
    result = await gen.generate(
        "m1",
        segments,
        speaker_map={"SPEAKER_00": "Alex"},
    )
    assert len(llm.calls) == 1
    assert result.subject == "Short meeting"
    assert result.attendees == ["Alex"]
    assert result.absentees == []
    assert result.secretary == "Alex"
    assert len(result.decisions) == 1
    assert llm.calls[0]["max_tokens"] <= 2048


@pytest.mark.asyncio
async def test_minutes_long_transcript_uses_parallel_map_and_local_merge(monkeypatch):
    monkeypatch.setenv("MINUTES_LLM_MERGE", "0")
    llm = FakeLLM(delay=0.05)
    gen = MeetingMinutesGenerator(llm, max_chars=120)
    segments = [
        _seg("m2", f"Remark number {i} about the long meeting topic.", start_ms=i * 1000)
        for i in range(12)
    ]
    result = await gen.generate(
        "m2",
        segments,
        speaker_map={"SPEAKER_00": "Alex"},
    )

    map_calls = [
        c
        for c in llm.calls
        if "ONE PART" in c["system"] or "This is part" in c["user"]
    ]
    merge_calls = [
        c
        for c in llm.calls
        if "partial minutes extracts" in c["system"] or "partial extracts" in c["user"]
    ]
    assert len(map_calls) >= 2
    assert merge_calls == []  # local merge when MINUTES_LLM_MERGE=0
    assert len(llm.calls) == len(map_calls)
    assert llm.max_in_flight >= 2  # parallel extracts

    assert result.subject.startswith("Part")
    assert "Summary of part" in result.summary
    assert result.attendees == ["Alex"]
    assert result.absentees == []
    assert len(result.decisions) >= 2


@pytest.mark.asyncio
async def test_minutes_optional_llm_merge(monkeypatch):
    monkeypatch.setenv("MINUTES_LLM_MERGE", "1")
    llm = FakeLLM()
    gen = MeetingMinutesGenerator(llm, max_chars=120)
    segments = [
        _seg("m3", f"Remark number {i} about the long meeting topic.", start_ms=i * 1000)
        for i in range(12)
    ]
    result = await gen.generate(
        "m3",
        segments,
        speaker_map={"SPEAKER_00": "Alex"},
    )
    merge_calls = [
        c
        for c in llm.calls
        if "partial minutes extracts" in c["system"] or "partial extracts" in c["user"]
    ]
    assert len(merge_calls) == 1
    assert result.subject == "LLM merged meeting"
    assert result.absentees == []
    assert result.secretary == ""
