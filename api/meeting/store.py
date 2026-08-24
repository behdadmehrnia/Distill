from __future__ import annotations

import json
import threading
from typing import Any, List, Mapping, Optional

from api.db import connect

from .models import (
    MeetingInsights,
    MeetingMinutes,
    MeetingRecord,
    MeetingStatus,
    MinutesDecision,
    SpeakerInterval,
    TranscriptSegment,
)


class TranscriptStore:
    """PostgreSQL persistence for meetings, transcript segments, and insights."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self):
        return connect(self.database_url)

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS meetings (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL,
                        sample_rate INTEGER NOT NULL,
                        created_at DOUBLE PRECISION NOT NULL,
                        started_at DOUBLE PRECISION,
                        stopped_at DOUBLE PRECISION,
                        audio_path TEXT,
                        user_id TEXT,
                        participants TEXT NOT NULL DEFAULT '[]',
                        speaker_map TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS segments (
                        id TEXT PRIMARY KEY,
                        meeting_id TEXT NOT NULL REFERENCES meetings(id),
                        speaker_id TEXT NOT NULL,
                        start_ms INTEGER NOT NULL,
                        end_ms INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        is_overlap INTEGER NOT NULL DEFAULT 0,
                        provisional INTEGER NOT NULL DEFAULT 0,
                        created_at DOUBLE PRECISION NOT NULL,
                        overlap_speakers TEXT NOT NULL DEFAULT '[]'
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_segments_meeting
                        ON segments(meeting_id, start_ms)
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS insights (
                        meeting_id TEXT PRIMARY KEY REFERENCES meetings(id),
                        summary TEXT NOT NULL,
                        highlights TEXT NOT NULL,
                        decisions TEXT NOT NULL,
                        action_items TEXT NOT NULL,
                        raw_json TEXT,
                        created_at DOUBLE PRECISION NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS speaker_intervals (
                        id SERIAL PRIMARY KEY,
                        meeting_id TEXT NOT NULL REFERENCES meetings(id),
                        speaker_id TEXT NOT NULL,
                        start_ms INTEGER NOT NULL,
                        end_ms INTEGER NOT NULL,
                        is_overlap INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_speaker_intervals_meeting
                        ON speaker_intervals(meeting_id)
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS minutes (
                        meeting_id TEXT PRIMARY KEY REFERENCES meetings(id),
                        subject TEXT NOT NULL DEFAULT '',
                        meeting_date TEXT NOT NULL DEFAULT '',
                        location TEXT NOT NULL DEFAULT '',
                        attendees TEXT NOT NULL DEFAULT '[]',
                        absentees TEXT NOT NULL DEFAULT '[]',
                        secretary TEXT NOT NULL DEFAULT '',
                        summary TEXT NOT NULL DEFAULT '',
                        decisions TEXT NOT NULL DEFAULT '[]',
                        raw_json TEXT,
                        created_at DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_meetings_user_created
                        ON meetings(user_id, created_at DESC)
                    """
                )
                conn.commit()

    def save_meeting(self, meeting: MeetingRecord) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO meetings (
                        id, title, status, sample_rate, created_at, started_at,
                        stopped_at, audio_path, user_id, participants, speaker_map
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        status = EXCLUDED.status,
                        sample_rate = EXCLUDED.sample_rate,
                        started_at = EXCLUDED.started_at,
                        stopped_at = EXCLUDED.stopped_at,
                        audio_path = EXCLUDED.audio_path,
                        user_id = EXCLUDED.user_id,
                        participants = EXCLUDED.participants,
                        speaker_map = EXCLUDED.speaker_map
                    """,
                    (
                        meeting.id,
                        meeting.title,
                        meeting.status.value,
                        meeting.sample_rate,
                        meeting.created_at,
                        meeting.started_at,
                        meeting.stopped_at,
                        meeting.audio_path,
                        meeting.user_id,
                        json.dumps(meeting.participants, ensure_ascii=False),
                        json.dumps(meeting.speaker_map, ensure_ascii=False),
                    ),
                )
                conn.commit()

    def get_meeting(self, meeting_id: str) -> Optional[MeetingRecord]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM meetings WHERE id = %s", (meeting_id,)
                ).fetchone()
                if not row:
                    return None
                return self._row_to_meeting(row)

    def list_meetings(
        self, *, user_id: str, limit: int = 50, offset: int = 0
    ) -> List[MeetingRecord]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM meetings
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (user_id, limit, offset),
                ).fetchall()
                return [self._row_to_meeting(r) for r in rows]

    def delete_meeting(self, meeting_id: str) -> bool:
        """Delete meeting and all derived artifacts."""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM segments WHERE meeting_id = %s", (meeting_id,)
                )
                conn.execute(
                    "DELETE FROM speaker_intervals WHERE meeting_id = %s",
                    (meeting_id,),
                )
                conn.execute(
                    "DELETE FROM insights WHERE meeting_id = %s", (meeting_id,)
                )
                conn.execute(
                    "DELETE FROM minutes WHERE meeting_id = %s", (meeting_id,)
                )
                cur = conn.execute(
                    "DELETE FROM meetings WHERE id = %s", (meeting_id,)
                )
                conn.commit()
                return cur.rowcount > 0

    def update_status(self, meeting_id: str, status: MeetingStatus) -> None:
        meeting = self.get_meeting(meeting_id)
        if not meeting:
            raise KeyError(f"Meeting not found: {meeting_id}")
        meeting.status = status
        self.save_meeting(meeting)

    def save_segment(self, segment: TranscriptSegment) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO segments (
                        id, meeting_id, speaker_id, start_ms, end_ms, text,
                        is_overlap, provisional, created_at, overlap_speakers
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        speaker_id = EXCLUDED.speaker_id,
                        start_ms = EXCLUDED.start_ms,
                        end_ms = EXCLUDED.end_ms,
                        text = EXCLUDED.text,
                        is_overlap = EXCLUDED.is_overlap,
                        provisional = EXCLUDED.provisional,
                        overlap_speakers = EXCLUDED.overlap_speakers
                    """,
                    (
                        segment.id,
                        segment.meeting_id,
                        segment.speaker_id,
                        segment.start_ms,
                        segment.end_ms,
                        segment.text,
                        1 if segment.is_overlap else 0,
                        1 if segment.provisional else 0,
                        segment.created_at,
                        json.dumps(segment.overlap_speakers or [], ensure_ascii=False),
                    ),
                )
                conn.commit()

    def replace_meeting_segments(
        self, meeting_id: str, segments: List[TranscriptSegment]
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM segments WHERE meeting_id = %s", (meeting_id,)
                )
                if segments:
                    with conn.cursor() as cur:
                        cur.executemany(
                            """
                            INSERT INTO segments (
                                id, meeting_id, speaker_id, start_ms, end_ms, text,
                                is_overlap, provisional, created_at, overlap_speakers
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            [
                                (
                                    s.id,
                                    s.meeting_id,
                                    s.speaker_id,
                                    s.start_ms,
                                    s.end_ms,
                                    s.text,
                                    1 if s.is_overlap else 0,
                                    1 if s.provisional else 0,
                                    s.created_at,
                                    json.dumps(
                                        s.overlap_speakers or [], ensure_ascii=False
                                    ),
                                )
                                for s in segments
                            ],
                        )
                conn.commit()

    def get_segments(self, meeting_id: str) -> List[TranscriptSegment]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM segments
                    WHERE meeting_id = %s
                    ORDER BY start_ms ASC, created_at ASC
                    """,
                    (meeting_id,),
                ).fetchall()
                return [self._row_to_segment(r) for r in rows]

    def get_segment(
        self, meeting_id: str, segment_id: str
    ) -> Optional[TranscriptSegment]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM segments
                    WHERE meeting_id = %s AND id = %s
                    """,
                    (meeting_id, segment_id),
                ).fetchone()
                return self._row_to_segment(row) if row else None

    def update_segment_text(
        self, meeting_id: str, segment_id: str, text: str
    ) -> List[TranscriptSegment]:
        """Update finalized segment text. Overlap peers with same span/text are synced."""
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("text must not be empty")

        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM segments
                    WHERE meeting_id = %s AND id = %s
                    """,
                    (meeting_id, segment_id),
                ).fetchone()
                if not row:
                    raise KeyError(f"Segment not found: {segment_id}")
                if bool(row["provisional"]):
                    raise ValueError("provisional segments cannot be edited")

                ids = [row["id"]]
                if bool(row["is_overlap"]):
                    peer_rows = conn.execute(
                        """
                        SELECT id FROM segments
                        WHERE meeting_id = %s
                          AND provisional = 0
                          AND is_overlap = 1
                          AND start_ms = %s
                          AND end_ms = %s
                          AND text = %s
                        """,
                        (
                            meeting_id,
                            row["start_ms"],
                            row["end_ms"],
                            row["text"],
                        ),
                    ).fetchall()
                    ids = [r["id"] for r in peer_rows] or ids

                conn.execute(
                    """
                    UPDATE segments
                    SET text = %s
                    WHERE meeting_id = %s AND id = ANY(%s)
                    """,
                    (cleaned, meeting_id, ids),
                )
                conn.commit()

                updated_rows = conn.execute(
                    """
                    SELECT * FROM segments
                    WHERE meeting_id = %s AND id = ANY(%s)
                    ORDER BY start_ms ASC, created_at ASC
                    """,
                    (meeting_id, ids),
                ).fetchall()
                return [self._row_to_segment(r) for r in updated_rows]

    def replace_speaker_intervals(
        self, meeting_id: str, intervals: List[SpeakerInterval]
    ) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM speaker_intervals WHERE meeting_id = %s",
                    (meeting_id,),
                )
                if intervals:
                    with conn.cursor() as cur:
                        cur.executemany(
                            """
                            INSERT INTO speaker_intervals (
                                meeting_id, speaker_id, start_ms, end_ms, is_overlap
                            ) VALUES (%s, %s, %s, %s, %s)
                            """,
                            [
                                (
                                    meeting_id,
                                    iv.speaker_id,
                                    iv.start_ms,
                                    iv.end_ms,
                                    1 if iv.is_overlap else 0,
                                )
                                for iv in intervals
                            ],
                        )
                conn.commit()

    def get_speaker_intervals(self, meeting_id: str) -> List[SpeakerInterval]:
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM speaker_intervals
                    WHERE meeting_id = %s
                    ORDER BY start_ms ASC
                    """,
                    (meeting_id,),
                ).fetchall()
                return [
                    SpeakerInterval(
                        speaker_id=r["speaker_id"],
                        start_ms=r["start_ms"],
                        end_ms=r["end_ms"],
                        is_overlap=bool(r["is_overlap"]),
                    )
                    for r in rows
                ]

    def delete_provisional_segments(self, meeting_id: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM segments WHERE meeting_id = %s AND provisional = 1",
                    (meeting_id,),
                )
                conn.commit()

    def save_insights(self, insights: MeetingInsights) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO insights (
                        meeting_id, summary, highlights, decisions,
                        action_items, raw_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (meeting_id) DO UPDATE SET
                        summary = EXCLUDED.summary,
                        highlights = EXCLUDED.highlights,
                        decisions = EXCLUDED.decisions,
                        action_items = EXCLUDED.action_items,
                        raw_json = EXCLUDED.raw_json,
                        created_at = EXCLUDED.created_at
                    """,
                    (
                        insights.meeting_id,
                        insights.summary,
                        json.dumps(insights.highlights, ensure_ascii=False),
                        json.dumps(insights.decisions, ensure_ascii=False),
                        json.dumps(insights.action_items, ensure_ascii=False),
                        json.dumps(insights.raw_json, ensure_ascii=False)
                        if insights.raw_json
                        else None,
                        insights.created_at,
                    ),
                )
                conn.commit()

    def get_insights(self, meeting_id: str) -> Optional[MeetingInsights]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM insights WHERE meeting_id = %s", (meeting_id,)
                ).fetchone()
                if not row:
                    return None
                return MeetingInsights(
                    meeting_id=row["meeting_id"],
                    summary=row["summary"],
                    highlights=json.loads(row["highlights"] or "[]"),
                    decisions=json.loads(row["decisions"] or "[]"),
                    action_items=json.loads(row["action_items"] or "[]"),
                    raw_json=json.loads(row["raw_json"]) if row["raw_json"] else None,
                    created_at=float(row["created_at"]),
                )

    def delete_insights(self, meeting_id: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM insights WHERE meeting_id = %s", (meeting_id,)
                )
                conn.commit()

    def save_minutes(self, minutes: MeetingMinutes) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO minutes (
                        meeting_id, subject, meeting_date, location, attendees,
                        absentees, secretary, summary, decisions, raw_json,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (meeting_id) DO UPDATE SET
                        subject = EXCLUDED.subject,
                        meeting_date = EXCLUDED.meeting_date,
                        location = EXCLUDED.location,
                        attendees = EXCLUDED.attendees,
                        absentees = EXCLUDED.absentees,
                        secretary = EXCLUDED.secretary,
                        summary = EXCLUDED.summary,
                        decisions = EXCLUDED.decisions,
                        raw_json = EXCLUDED.raw_json,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        minutes.meeting_id,
                        minutes.subject,
                        minutes.meeting_date,
                        minutes.location,
                        json.dumps(minutes.attendees, ensure_ascii=False),
                        json.dumps(minutes.absentees, ensure_ascii=False),
                        minutes.secretary,
                        minutes.summary,
                        json.dumps(
                            [d.to_dict() for d in minutes.decisions],
                            ensure_ascii=False,
                        ),
                        json.dumps(minutes.raw_json, ensure_ascii=False)
                        if minutes.raw_json
                        else None,
                        minutes.created_at,
                        minutes.updated_at,
                    ),
                )
                conn.commit()

    def get_minutes(self, meeting_id: str) -> Optional[MeetingMinutes]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM minutes WHERE meeting_id = %s", (meeting_id,)
                ).fetchone()
                if not row:
                    return None
                decisions_raw = json.loads(row["decisions"] or "[]")
                return MeetingMinutes(
                    meeting_id=row["meeting_id"],
                    subject=row["subject"] or "",
                    meeting_date=row["meeting_date"] or "",
                    location=row["location"] or "",
                    attendees=json.loads(row["attendees"] or "[]"),
                    absentees=json.loads(row["absentees"] or "[]"),
                    secretary=row["secretary"] or "",
                    summary=row["summary"] or "",
                    decisions=[MinutesDecision.from_dict(d) for d in decisions_raw],
                    raw_json=json.loads(row["raw_json"]) if row["raw_json"] else None,
                    created_at=float(row["created_at"]),
                    updated_at=float(row["updated_at"]),
                )

    def delete_minutes(self, meeting_id: str) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM minutes WHERE meeting_id = %s", (meeting_id,)
                )
                conn.commit()

    @staticmethod
    def _row_to_meeting(row: Mapping[str, Any]) -> MeetingRecord:
        return MeetingRecord(
            id=row["id"],
            title=row["title"],
            status=MeetingStatus(row["status"]),
            sample_rate=row["sample_rate"],
            created_at=float(row["created_at"]),
            started_at=float(row["started_at"]) if row["started_at"] is not None else None,
            stopped_at=float(row["stopped_at"]) if row["stopped_at"] is not None else None,
            audio_path=row["audio_path"],
            participants=json.loads(row["participants"] or "[]"),
            speaker_map=json.loads(row["speaker_map"] or "{}"),
            user_id=row.get("user_id"),
        )

    @staticmethod
    def _row_to_segment(row: Mapping[str, Any]) -> TranscriptSegment:
        overlap_speakers = []
        raw = row.get("overlap_speakers")
        if raw:
            try:
                overlap_speakers = json.loads(raw)
            except json.JSONDecodeError:
                overlap_speakers = []
        return TranscriptSegment(
            id=row["id"],
            meeting_id=row["meeting_id"],
            speaker_id=row["speaker_id"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            text=row["text"],
            is_overlap=bool(row["is_overlap"]),
            provisional=bool(row["provisional"]),
            overlap_speakers=overlap_speakers,
            created_at=float(row["created_at"]),
        )
