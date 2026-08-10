from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from .insights import _parse_json_response, format_transcript_for_llm, has_meaningful_speech
from .models import MeetingMinutes, MinutesDecision, TranscriptSegment

logger = logging.getLogger(__name__)

# Prefer one high-quality LLM call for typical meetings; map-reduce only for very long text.
DEFAULT_MAX_TRANSCRIPT_CHARS = 40_000
DEFAULT_MINUTES_MAX_CHUNKS = 4
DEFAULT_MINUTES_PARALLEL = 2
# Minutes agent gets a long budget — remote gemma-class models are slow but required.
DEFAULT_MINUTES_LLM_TIMEOUT_S = 180.0
DEFAULT_MINUTES_MAX_TOKENS = 2048

MINUTES_SYSTEM_PROMPT = """You are Distill, producing a formal Persian meeting minutes document (صورت جلسه).
Given a speaker-labeled transcript and the list of confirmed attendee names, extract structured minutes.
Respond ONLY with valid JSON using this schema:
{
  "subject": "موضوع جلسه به فارسی",
  "meeting_date": "تاریخ جلسه اگر در متن ذکر شده، وگرنه خالی",
  "location": "محل برگزاری اگر ذکر شده، وگرنه خالی",
  "attendees": ["نام حاضر ۱", "..."],
  "absentees": [],
  "secretary": "",
  "summary": "2-4 sentence overview of the meeting in Persian",
  "decisions": [
    {
      "description": "شرح مصوبه یا موضوع پیگیری",
      "executor": "نام مجری اگر مشخص است، وگرنه خالی",
      "due_date": "سررسید اگر مشخص است، وگرنه خالی",
      "status": "pending"
    }
  ]
}
Rules:
- attendees MUST be exactly the provided confirmed attendee names, verbatim, with no additions or omissions.
- absentees MUST always be an empty array []. Never invent absentees; the user fills that field later.
- secretary MUST be empty or one of the provided attendee names.
- Extract every decision and follow-up (مصوبه / پیگیری / اقدام) that is actually supported by the transcript into decisions[].
- Include owner/executor and due date when the transcript states them.
- If nothing qualifies as a decision or action item, use an empty array.
- Do not invent people, facts, or names that are not in the provided attendee list / transcript.
- All free-text fields must be in Persian.
"""

PARTIAL_SYSTEM_PROMPT = """You are Distill. You are analyzing ONE PART of a long Persian meeting transcript.
Extract only what is supported by this part. Respond ONLY with valid JSON:
{
  "subject_hint": "موضوع احتمالی این بخش اگر مشخص است، وگرنه خالی",
  "meeting_date": "تاریخ اگر در این بخش ذکر شده، وگرنه خالی",
  "location": "محل اگر در این بخش ذکر شده، وگرنه خالی",
  "summary": "1-3 جمله خلاصه همین بخش به فارسی",
  "decisions": [
    {
      "description": "شرح مصوبه یا پیگیری",
      "executor": "نام مجری اگر مشخص و در لیست حاضرین است، وگرنه خالی",
      "due_date": "سررسید اگر مشخص است، وگرنه خالی",
      "status": "pending"
    }
  ]
}
Rules:
- Extract decisions/follow-ups (مصوبه / پیگیری / اقدام) supported by THIS part.
- Do not invent people or facts.
- executor must be empty or one of the provided attendee names.
- All free-text fields must be in Persian.
"""

MERGE_SYSTEM_PROMPT = """You are Distill. You will receive partial minutes extracts from consecutive parts of one long meeting.
Merge them into a single formal Persian meeting minutes JSON. Respond ONLY with valid JSON using this schema:
{
  "subject": "موضوع جلسه به فارسی",
  "meeting_date": "تاریخ جلسه اگر در partialها آمده، وگرنه خالی",
  "location": "محل برگزاری اگر آمده، وگرنه خالی",
  "attendees": ["نام حاضر ۱", "..."],
  "absentees": [],
  "secretary": "",
  "summary": "2-4 جمله خلاصه کل جلسه به فارسی",
  "decisions": [
    {
      "description": "شرح مصوبه یا پیگیری",
      "executor": "نام مجری اگر مشخص است، وگرنه خالی",
      "due_date": "سررسید اگر مشخص است، وگرنه خالی",
      "status": "pending"
    }
  ]
}
Rules:
- attendees MUST be exactly the provided confirmed attendee names.
- absentees MUST always be [].
- secretary MUST be empty or one of the attendees.
- Keep all distinct decisions/follow-ups; deduplicate near-identical ones only.
- Do not invent people, facts, or decisions not present in the partials.
- All free-text fields must be in Persian.
"""

_EMPTY_SUMMARY = "متن پیاده‌شده‌ای برای تحلیل وجود ندارد."
_NOISE_ONLY_SUMMARY = (
    "متن معناداری برای تحلیل وجود ندارد "
    "(فقط نشانه‌های غیرکلامی یا صدای محیط ثبت شده است)."
)
_FALLBACK_SUMMARY = (
    "تحلیل محتوایی کافی برای تنظیم صورت جلسه استخراج نشد. "
    "می‌توانید فیلدها را دستی تکمیل کنید."
)


def max_transcript_chars() -> int:
    raw = os.getenv("MINUTES_MAX_TRANSCRIPT_CHARS", "").strip()
    if not raw:
        return DEFAULT_MAX_TRANSCRIPT_CHARS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_TRANSCRIPT_CHARS
    return max(2_000, value)


def minutes_llm_timeout_s() -> float:
    raw = os.getenv("MINUTES_LLM_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_MINUTES_LLM_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MINUTES_LLM_TIMEOUT_S
    return max(60.0, value)


def minutes_max_chunks() -> int:
    raw = os.getenv("MINUTES_MAX_CHUNKS", "").strip()
    if not raw:
        return DEFAULT_MINUTES_MAX_CHUNKS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MINUTES_MAX_CHUNKS


def minutes_parallel() -> int:
    raw = os.getenv("MINUTES_PARALLEL", "").strip()
    if not raw:
        return DEFAULT_MINUTES_PARALLEL
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MINUTES_PARALLEL


def minutes_max_tokens() -> int:
    raw = os.getenv("MINUTES_MAX_TOKENS", "").strip()
    if not raw:
        return DEFAULT_MINUTES_MAX_TOKENS
    try:
        return max(256, int(raw))
    except ValueError:
        return DEFAULT_MINUTES_MAX_TOKENS


def minutes_use_llm_merge() -> bool:
    """Default ON for quality; set MINUTES_LLM_MERGE=0 for local-only merge."""
    raw = os.getenv("MINUTES_LLM_MERGE", "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def split_transcript(text: str, max_chars: int) -> List[str]:
    """Split transcript on line boundaries so each chunk fits max_chars."""
    text = text or ""
    if not text:
        return []
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    lines = text.split("\n")
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0

    for line in lines:
        add = len(line) + (1 if current else 0)
        if current and current_len + add > max_chars:
            flush()
            add = len(line)
        if len(line) > max_chars:
            flush()
            for i in range(0, len(line), max_chars):
                chunks.append(line[i : i + max_chars])
            continue
        current.append(line)
        current_len += add

    flush()
    return chunks or [text]


def cap_chunks(chunks: List[str], max_chunks: int) -> List[str]:
    """Keep at most max_chunks by folding overflow into the last chunk."""
    if max_chunks <= 0 or len(chunks) <= max_chunks:
        return chunks
    head = chunks[: max_chunks - 1]
    tail = "\n".join(chunks[max_chunks - 1 :])
    return head + [tail]


def merge_partials_local(
    partials: List[dict], attendee_names: List[str]
) -> dict:
    """Deterministic merge used when MINUTES_LLM_MERGE=0."""
    subject = ""
    meeting_date = ""
    location = ""
    summaries: List[str] = []
    decisions: List[dict] = []
    seen_desc: set[str] = set()

    for partial in partials:
        if not isinstance(partial, dict):
            continue
        if not subject:
            subject = str(
                partial.get("subject_hint") or partial.get("subject") or ""
            ).strip()
        if not meeting_date:
            meeting_date = str(partial.get("meeting_date") or "").strip()
        if not location:
            location = str(partial.get("location") or "").strip()
        summary = str(partial.get("summary") or "").strip()
        if summary:
            summaries.append(summary)
        raw_decisions = partial.get("decisions")
        if not isinstance(raw_decisions, list):
            continue
        for item in raw_decisions:
            if not isinstance(item, dict):
                continue
            description = str(item.get("description") or "").strip()
            if not description:
                continue
            key = description.casefold()
            if key in seen_desc:
                continue
            seen_desc.add(key)
            executor = str(item.get("executor") or "").strip()
            if executor and executor not in attendee_names:
                executor = ""
            decisions.append(
                {
                    "description": description,
                    "executor": executor,
                    "due_date": str(item.get("due_date") or "").strip(),
                    "status": str(item.get("status") or "pending").strip()
                    or "pending",
                }
            )

    summary = " ".join(summaries).strip()
    if len(summary) > 1200:
        summary = summary[:1197].rstrip() + "…"

    return {
        "subject": subject,
        "meeting_date": meeting_date,
        "location": location,
        "attendees": list(attendee_names),
        "absentees": [],
        "secretary": "",
        "summary": summary,
        "decisions": decisions,
    }


class MeetingMinutesGenerator:
    def __init__(self, llm, max_chars: Optional[int] = None):
        self.llm = llm
        self.max_chars = max_chars if max_chars is not None else max_transcript_chars()

    async def generate(
        self,
        meeting_id: str,
        segments: List[TranscriptSegment],
        speaker_map: Optional[dict] = None,
        participants: Optional[List[str]] = None,
    ) -> MeetingMinutes:
        speaker_map = speaker_map or {}
        attendee_names = _attendees_from_meeting(speaker_map, segments, participants)
        transcript = format_transcript_for_llm(segments, speaker_map)

        if not transcript.strip():
            return MeetingMinutes(
                meeting_id=meeting_id,
                attendees=attendee_names,
                absentees=[],
                summary=_EMPTY_SUMMARY,
            )
        if not has_meaningful_speech(segments):
            return MeetingMinutes(
                meeting_id=meeting_id,
                attendees=attendee_names,
                absentees=[],
                summary=_NOISE_ONLY_SUMMARY,
            )

        chunks = cap_chunks(
            split_transcript(transcript, self.max_chars), minutes_max_chunks()
        )
        if len(chunks) <= 1:
            data = await self._extract_full(
                chunks[0] if chunks else transcript, attendee_names
            )
        else:
            logger.info(
                "Minutes map-reduce: transcript_chars=%d chunks=%d budget=%d parallel=%d llm_merge=%s",
                len(transcript),
                len(chunks),
                self.max_chars,
                minutes_parallel(),
                minutes_use_llm_merge(),
            )
            partials = await self._extract_partials_parallel(chunks, attendee_names)
            if minutes_use_llm_merge():
                data = await self._merge_partials_llm(partials, attendee_names)
            else:
                data = merge_partials_local(partials, attendee_names)

        return self._to_minutes(meeting_id, data, attendee_names)

    async def _extract_partials_parallel(
        self, chunks: List[str], attendee_names: List[str]
    ) -> List[dict]:
        sem = asyncio.Semaphore(minutes_parallel())
        total = len(chunks)

        async def one(idx: int, chunk: str) -> dict:
            async with sem:
                return await self._extract_partial(
                    chunk, attendee_names, part=idx, total=total
                )

        return list(
            await asyncio.gather(
                *[one(i, chunk) for i, chunk in enumerate(chunks, start=1)]
            )
        )

    async def _extract_full(self, transcript: str, attendee_names: List[str]) -> dict:
        messages = [
            {"role": "system", "content": MINUTES_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"حاضرین تأییدشده (فقط همین‌ها را در attendees بگذار؛ absentees را همیشه [] بگذار):\n"
                    f"{', '.join(attendee_names) or '(نامشخص)'}\n\n"
                    "متن کامل جلسه را تحلیل کن و فقط JSON برگردان.\n\n"
                    f"{transcript}"
                ),
            },
        ]
        raw = await self.llm.complete(
            messages,
            temperature=0.2,
            max_tokens=minutes_max_tokens(),
            timeout=minutes_llm_timeout_s(),
        )
        return _parse_json_response(raw)

    async def _extract_partial(
        self,
        transcript_part: str,
        attendee_names: List[str],
        *,
        part: int,
        total: int,
    ) -> dict:
        messages = [
            {"role": "system", "content": PARTIAL_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"حاضرین تأییدشده: {', '.join(attendee_names) or '(نامشخص)'}\n"
                    f"این بخش {part} از {total} متن جلسه است.\n"
                    "فقط JSON برگردان.\n\n"
                    f"{transcript_part}"
                ),
            },
        ]
        raw = await self.llm.complete(
            messages,
            temperature=0.2,
            max_tokens=min(1536, minutes_max_tokens()),
            timeout=minutes_llm_timeout_s(),
        )
        data = _parse_json_response(raw)
        if not isinstance(data, dict):
            return {"summary": "", "decisions": []}
        return data

    async def _merge_partials_llm(
        self, partials: List[dict], attendee_names: List[str]
    ) -> dict:
        compact = []
        for p in partials:
            compact.append(
                {
                    "subject_hint": str(
                        p.get("subject_hint") or p.get("subject") or ""
                    ).strip(),
                    "meeting_date": str(p.get("meeting_date") or "").strip(),
                    "location": str(p.get("location") or "").strip(),
                    "summary": str(p.get("summary") or "").strip(),
                    "decisions": p.get("decisions")
                    if isinstance(p.get("decisions"), list)
                    else [],
                }
            )
        messages = [
            {"role": "system", "content": MERGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"حاضرین تأییدشده (فقط همین‌ها را در attendees بگذار؛ absentees را همیشه [] بگذار):\n"
                    f"{', '.join(attendee_names) or '(نامشخص)'}\n\n"
                    "partial extracts JSON:\n"
                    f"{json.dumps(compact, ensure_ascii=False)}\n\n"
                    "آن‌ها را در یک صورت جلسه واحد ادغام کن و فقط JSON برگردان."
                ),
            },
        ]
        raw = await self.llm.complete(
            messages,
            temperature=0.2,
            max_tokens=minutes_max_tokens(),
            timeout=minutes_llm_timeout_s(),
        )
        return _parse_json_response(raw)

    def _to_minutes(
        self,
        meeting_id: str,
        data: dict,
        attendee_names: List[str],
    ) -> MeetingMinutes:
        data = data if isinstance(data, dict) else {}
        decisions_raw = data.get("decisions")
        decisions: List[MinutesDecision] = []
        if isinstance(decisions_raw, list):
            for item in decisions_raw:
                if not isinstance(item, dict):
                    continue
                description = str(item.get("description") or "").strip()
                if not description:
                    continue
                executor = str(item.get("executor") or "").strip()
                if executor and executor not in attendee_names:
                    executor = ""
                decisions.append(
                    MinutesDecision(
                        id=str(uuid.uuid4()),
                        description=description,
                        executor=executor,
                        due_date=str(item.get("due_date") or "").strip(),
                        status=str(item.get("status") or "pending").strip() or "pending",
                    )
                )

        attendees = list(attendee_names)
        absentees: List[str] = []
        secretary = str(data.get("secretary") or "").strip()
        if secretary not in attendees:
            secretary = ""
        summary = str(data.get("summary") or "").strip()
        if not summary and not decisions:
            summary = _FALLBACK_SUMMARY

        now = time.time()
        return MeetingMinutes(
            meeting_id=meeting_id,
            subject=str(data.get("subject") or "").strip(),
            meeting_date=str(data.get("meeting_date") or "").strip(),
            location=str(data.get("location") or "").strip(),
            attendees=attendees,
            absentees=absentees,
            secretary=secretary,
            summary=summary,
            decisions=decisions,
            raw_json=data,
            created_at=now,
            updated_at=now,
        )


def _attendees_from_meeting(
    speaker_map: Dict[str, Any],
    segments: List[TranscriptSegment],
    participants: Optional[List[str]],
) -> List[str]:
    """Build attendees only from registered speaker names (and segment order)."""
    seen_ids: List[str] = []
    for seg in segments or []:
        sid = str(seg.speaker_id or "").strip()
        if sid and sid not in seen_ids:
            seen_ids.append(sid)
        for other in seg.overlap_speakers or []:
            oid = str(other or "").strip()
            if oid and oid not in seen_ids:
                seen_ids.append(oid)

    names: List[str] = []
    for sid in seen_ids:
        label = str(speaker_map.get(sid) or "").strip()
        if label and label not in names:
            names.append(label)

    for sid, label in speaker_map.items():
        cleaned = str(label or "").strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)

    if not names:
        for p in participants or []:
            cleaned = str(p or "").strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
    return names
