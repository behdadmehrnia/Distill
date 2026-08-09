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
- Use the provided attendee names verbatim; do not invent people who did not speak.
- Only include decisions/action items that are actually supported by the transcript.
- If nothing qualifies as a decision or action item, use an empty array.
- Do not invent facts that are not supported by the transcript.
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
        attendee_names = sorted({v for v in speaker_map.values() if v}) or list(
            participants or []
        )
        transcript = format_transcript_for_llm(segments, speaker_map)

        if not transcript.strip():
            return MeetingMinutes(
                meeting_id=meeting_id,
                attendees=attendee_names,
                summary=_EMPTY_SUMMARY,
            )
        if not has_meaningful_speech(segments):
            return MeetingMinutes(
                meeting_id=meeting_id,
                attendees=attendee_names,
                summary=_NOISE_ONLY_SUMMARY,
            )

        messages = [
            {"role": "system", "content": MINUTES_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"حاضرین شناسایی‌شده: {', '.join(attendee_names) or '(نامشخص)'}\n\n"
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
                decisions.append(
                    MinutesDecision(
                        id=str(uuid.uuid4()),
                        description=description,
                        executor=str(item.get("executor") or "").strip(),
                        due_date=str(item.get("due_date") or "").strip(),
                        status=str(item.get("status") or "pending").strip() or "pending",
                    )
                )

        attendees = _as_str_list(data.get("attendees")) or attendee_names
        absentees = _as_str_list(data.get("absentees"))
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
            secretary=str(data.get("secretary") or "").strip(),
            summary=summary,
            decisions=decisions,
            raw_json=data,
            created_at=now,
            updated_at=now,
        )


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]
