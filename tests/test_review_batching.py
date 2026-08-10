from __future__ import annotations

import json

import pytest

from api.meeting.models import TranscriptSegment
from api.meeting.review import (
    TranscriptReviewAgent,
    chunk_review_items,
)


def test_chunk_review_items_splits_on_budget():
    items = [{"id": f"t{i}", "text": "x" * 30} for i in range(10)]
    batches = chunk_review_items(items, max_chars=100)
    assert len(batches) >= 2
    assert sum(len(b) for b in batches) == len(items)


def test_chunk_review_items_under_budget_single():
    items = [{"id": "t0", "text": "hello"}]
    assert chunk_review_items(items, max_chars=1000) == [items]


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def complete(self, messages, temperature=0.0, max_tokens=1024, **kwargs):
        self.calls.append(messages[-1]["content"])
        # Echo keep for whatever ids appear in this batch
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
    monkeypatch.setattr(
        "api.meeting.review.review_max_batch_chars", lambda: 80
    )
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
