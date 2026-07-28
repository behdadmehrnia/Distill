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
    ) -> "TranscriptSegment":
        return TranscriptSegment(
            id=str(uuid.uuid4()),
            meeting_id=meeting_id,
            speaker_id=speaker_id,
            start_ms=start_ms,
            end_ms=end_ms,
            text=text.strip(),
            is_overlap=is_overlap,
            provisional=provisional,
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

    @staticmethod
    def create(title: str = "Untitled Meeting", participants: Optional[List[str]] = None) -> "MeetingRecord":
        return MeetingRecord(
            id=str(uuid.uuid4()),
            title=title,
            status=MeetingStatus.CREATED,
            participants=participants or [],
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
        }
