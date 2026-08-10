from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from .insights import _parse_json_response, format_transcript_for_llm, has_meaningful_speech
from .models import MeetingMinutes, MinutesDecision, TranscriptSegment

logger = logging.getLogger(__name__)

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
- Only include decisions/action items that are actually supported by the transcript.
- If nothing qualifies as a decision or action item, use an empty array.
- Do not invent people, facts, or names that are not in the provided attendee list / transcript.
- All free-text fields must be in Persian.
"""

_EMPTY_SUMMARY = "متن پیاده‌شده‌ای برای تحلیل وجود ندارد."
_NOISE_ONLY_SUMMARY = (
    "متن معناداری برای تحلیل وجود ندارد "
    "(فقط نشانه‌های غیرکلامی یا صدای محیط ثبت شده است)."
)


class MeetingMinutesGenerator:
    def __init__(self, llm):
        self.llm = llm

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
        raw = await self.llm.complete(messages, temperature=0.2, max_tokens=2048)
        data = _parse_json_response(raw)

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

        # Never trust the model for attendance: attendees = registered speakers,
        # absentees always empty for the user to fill later.
        attendees = list(attendee_names)
        absentees: List[str] = []
        secretary = str(data.get("secretary") or "").strip()
        if secretary not in attendees:
            secretary = ""
        summary = str(data.get("summary") or "").strip()
        if not summary and not decisions:
            summary = (
                "تحلیل محتوایی کافی برای تنظیم صورت جلسه استخراج نشد. "
                "می‌توانید فیلدها را دستی تکمیل کنید."
            )

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

    # Include any remaining mapped speakers (named but maybe sparse in segments).
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
