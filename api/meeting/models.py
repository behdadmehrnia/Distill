from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional
import time
import uuid


class MeetingStatus(str, Enum):
    CREATED = "created"
    RECORDING = "recording"
    PROCESSING = "processing"
    STOPPED = "stopped"
    FAILED = "failed"


class CaptureMode(str, Enum):
    """How audio is captured for a meeting."""

    MONO = "mono"
    MULTI_STREAM = "multi_stream"


@dataclass
class TranscriptSegment:
    id: str
    meeting_id: str
    speaker_id: str
    start_ms: int
    end_ms: int
    text: str
    is_overlap: bool = False
    provisional: bool = False
    overlap_speakers: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @staticmethod
    def create(
        meeting_id: str,
        speaker_id: str,
        start_ms: int,
        end_ms: int,
        text: str,
        is_overlap: bool = False,
        provisional: bool = False,
        overlap_speakers: Optional[List[str]] = None,
    ) -> "TranscriptSegment":
        speakers = list(overlap_speakers or [])
        if is_overlap and speaker_id not in speakers:
            speakers = [speaker_id, *[s for s in speakers if s != speaker_id]]
        return TranscriptSegment(
            id=str(uuid.uuid4()),
            meeting_id=meeting_id,
            speaker_id=speaker_id,
            start_ms=start_ms,
            end_ms=end_ms,
            text=text.strip(),
            is_overlap=is_overlap,
            provisional=provisional,
            overlap_speakers=speakers,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SpeakerInterval:
    speaker_id: str
    start_ms: int
    end_ms: int
    is_overlap: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MeetingInsights:
    meeting_id: str
    summary: str
    highlights: List[str]
    decisions: List[str]
    action_items: List[str] = field(default_factory=list)
    raw_json: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "meeting_id": self.meeting_id,
            "summary": self.summary,
            "highlights": self.highlights,
            "decisions": self.decisions,
            "action_items": self.action_items,
            "created_at": self.created_at,
        }


@dataclass
class MinutesDecision:
    id: str
    description: str
    executor: str = ""
    due_date: str = ""
    status: str = "pending"  # pending | done

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "MinutesDecision":
        return MinutesDecision(
            id=str(data.get("id") or uuid.uuid4()),
            description=str(data.get("description") or "").strip(),
            executor=str(data.get("executor") or "").strip(),
            due_date=str(data.get("due_date") or "").strip(),
            status=str(data.get("status") or "pending").strip() or "pending",
        )


@dataclass
class MeetingMinutes:
    meeting_id: str
    subject: str = ""
    meeting_date: str = ""
    location: str = ""
    attendees: List[str] = field(default_factory=list)
    absentees: List[str] = field(default_factory=list)
    secretary: str = ""
    summary: str = ""
    decisions: List[MinutesDecision] = field(default_factory=list)
    raw_json: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "meeting_id": self.meeting_id,
            "subject": self.subject,
            "meeting_date": self.meeting_date,
            "location": self.location,
            "attendees": self.attendees,
            "absentees": self.absentees,
            "secretary": self.secretary,
            "summary": self.summary,
            "decisions": [d.to_dict() for d in self.decisions],
            "raw_json": self.raw_json,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class MeetingRecord:
    id: str
    title: str
    status: MeetingStatus
    sample_rate: int = 16000
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    audio_path: Optional[str] = None
    participants: List[str] = field(default_factory=list)
    speaker_map: Dict[str, str] = field(default_factory=dict)
    user_id: Optional[str] = None
    capture_mode: CaptureMode = CaptureMode.MONO
    stream_paths: Dict[str, str] = field(default_factory=dict)

    @staticmethod
    def create(
        title: str = "Untitled Meeting",
        participants: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        *,
        capture_mode: CaptureMode = CaptureMode.MONO,
        streams: Optional[Dict[str, str]] = None,
    ) -> "MeetingRecord":
        from .multi_stream import normalize_speaker_id

        speaker_map: Dict[str, str] = {}
        if streams:
            speaker_map = {
                normalize_speaker_id(k): str(v).strip()
                for k, v in streams.items()
                if str(k).strip() and str(v).strip()
            }
        return MeetingRecord(
            id=str(uuid.uuid4()),
            title=title,
            status=MeetingStatus.CREATED,
            participants=participants or [],
            user_id=user_id,
            capture_mode=capture_mode,
            speaker_map=speaker_map,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "sample_rate": self.sample_rate,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "audio_path": self.audio_path,
            "participants": self.participants,
            "speaker_map": self.speaker_map,
            "user_id": self.user_id,
            "capture_mode": self.capture_mode.value,
            "stream_paths": self.stream_paths,
        }
