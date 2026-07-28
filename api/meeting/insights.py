from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from .models import MeetingInsights, TranscriptSegment

logger = logging.getLogger(__name__)

INSIGHTS_SYSTEM_PROMPT = """You are Distill, a meeting analyst.
You analyze Persian (and mixed Persian/English) management meetings.
Given a speaker-labeled transcript, extract structured insights.
Respond ONLY with valid JSON using this schema:
{
  "summary": "2-5 sentence summary in Persian",
  "highlights": ["important point 1", "..."],
  "decisions": ["decision 1", "..."],
  "action_items": ["owner/task/deadline if available", "..."]
}
If a field has nothing, use an empty array (or empty string for summary).
Do not invent facts that are not supported by the transcript.
"""


def format_transcript_for_llm(
    segments: List[TranscriptSegment],
    speaker_map: Optional[dict] = None,
) -> str:
    speaker_map = speaker_map or {}
    lines = []
    seen_overlap = set()
    for seg in segments:
        start = _fmt_ts(seg.start_ms)
        end = _fmt_ts(seg.end_ms)
        if seg.is_overlap:
            key = (seg.start_ms, seg.end_ms, seg.text)
            if key in seen_overlap:
                continue
            seen_overlap.add(key)
            names = seg.overlap_speakers or [seg.speaker_id]
            labels = [speaker_map.get(s, s) for s in names]
            lines.append(
                f"[{start}-{end}] {' + '.join(labels)} [OVERLAP / هم‌صحبتی]: {seg.text}"
            )
        else:
            label = speaker_map.get(seg.speaker_id, seg.speaker_id)
            lines.append(f"[{start}-{end}] {label}: {seg.text}")
    return "\n".join(lines)


def _fmt_ts(ms: int) -> str:
    s = max(0, ms // 1000)
    m, sec = divmod(s, 60)
    return f"{m:02d}:{sec:02d}"


class MeetingInsightsGenerator:
    def __init__(self, llm):
        self.llm = llm

    async def generate(
        self,
        meeting_id: str,
        segments: List[TranscriptSegment],
        speaker_map: Optional[dict] = None,
    ) -> MeetingInsights:
        transcript = format_transcript_for_llm(segments, speaker_map)
        if not transcript.strip():
            return MeetingInsights(
                meeting_id=meeting_id,
                summary="متن پیاده‌شده‌ای برای تحلیل وجود ندارد.",
                highlights=[],
                decisions=[],
                action_items=[],
            )

        messages = [
            {"role": "system", "content": INSIGHTS_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Analyze this meeting transcript and return JSON only.\n\n"
                    f"{transcript}"
                ),
            },
        ]
        raw = await self.llm.complete(messages, temperature=0.2, max_tokens=2048)
        data = _parse_json_response(raw)
        insights = MeetingInsights(
            meeting_id=meeting_id,
            summary=str(data.get("summary") or "").strip(),
            highlights=_as_str_list(data.get("highlights")),
            decisions=_as_str_list(data.get("decisions")),
            action_items=_as_str_list(data.get("action_items")),
            raw_json=data,
        )
        return insights


def _as_str_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _parse_json_response(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {}
    # Strip markdown fences if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find first {...} block
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                logger.warning("Failed to parse insights JSON")
        return {
            "summary": text[:1000],
            "highlights": [],
            "decisions": [],
            "action_items": [],
        }
