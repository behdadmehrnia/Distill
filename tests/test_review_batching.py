from __future__ import annotations

import json

import pytest

from api.meeting.models import TranscriptSegment
from api.meeting.review import (
    TranscriptReviewAgent,
    chunk_review_items,
    needs_llm_polish,
    polish_max_tokens_for_batch,
    select_polish_items,
)


def test_chunk_review_items_splits_on_budget():
    items = [{"id": f"t{i}", "text": "x" * 30} for i in range(10)]
    batches = chunk_review_items(items, max_chars=100)
    assert len(batches) >= 2
    assert sum(len(b) for b in batches) == len(items)


def test_chunk_review_items_under_budget_single():
    items = [{"id": "t0", "text": "hello"}]
    assert chunk_review_items(items, max_chars=1000) == [items]


def test_needs_llm_polish_skips_clean_high_score():
    assert needs_llm_polish(0.99, []) is False
    assert needs_llm_polish(0.8, []) is True
    assert needs_llm_polish(0.95, ["latin_heavy:0.5"]) is True


def test_select_polish_items_worst_first_and_cap():
    texts = [
        ("clean persian text here", 0.99, []),
        ("bad latin heavy aaa", 0.5, ["latin_heavy:0.6"]),
        ("word run run run", 0.7, ["word_run:3"]),
        ("another dirty", 0.4, ["char_loop:0.4"]),
    ]
    selected = select_polish_items(texts, max_items=2)
    assert selected == ["another dirty", "bad latin heavy aaa"]


def test_polish_max_tokens_is_capped():
    batch = [{"id": "t0", "text": "سلام " * 200}]
    tokens = polish_max_tokens_for_batch(batch)
    assert 256 <= tokens <= 768


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.max_tokens = []

    async def complete(self, messages, temperature=0.0, max_tokens=1024, **kwargs):
        self.calls.append(messages[-1]["content"])
        self.max_tokens.append(max_tokens)
        user = messages[-1]["content"]
        start = user.find("[")
        end = user.rfind("]")
        payload = json.loads(user[start : end + 1])
        return json.dumps(
            {
                "items": [
                    {"id": item["id"], "action": "keep", "text": item["text"]}
                    for item in payload
                ]
            },
            ensure_ascii=False,
        )


@pytest.mark.asyncio
async def test_review_segments_batches_long_payload(monkeypatch):
    monkeypatch.setattr("api.meeting.review.review_max_batch_chars", lambda: 80)
    monkeypatch.setattr("api.meeting.review.review_max_items", lambda: 50)
    # Force every segment to need polish regardless of heuristic score.
    monkeypatch.setattr("api.meeting.review.needs_llm_polish", lambda score, reasons: True)
    llm = FakeLLM()
    agent = TranscriptReviewAgent(llm=llm, enabled=True)
    segs = [
        TranscriptSegment.create(
            meeting_id="m",
            speaker_id="SPEAKER_00",
            start_ms=i * 1000,
            end_ms=i * 1000 + 900,
            text=f"متن شماره {i} برای پالیش " + ("الف" * 8),
        )
        for i in range(6)
    ]
    out = await agent.review_segments(segs, language="fa")
    assert len(llm.calls) >= 2
    assert len(out) == len(segs)
    assert all(t <= 768 for t in llm.max_tokens)


@pytest.mark.asyncio
async def test_review_skips_llm_when_text_is_clean():
    llm = FakeLLM()
    agent = TranscriptReviewAgent(llm=llm, enabled=True)
    segs = [
        TranscriptSegment.create(
            meeting_id="m",
            speaker_id="SPEAKER_00",
            start_ms=0,
            end_ms=1000,
            text="امروز درباره بودجه پروژه صحبت کردیم و تصمیم گرفتیم.",
        )
    ]
    out = await agent.review_segments(segs, language="fa")
    assert llm.calls == []
    assert len(out) == 1
    assert "بودجه" in out[0].text
