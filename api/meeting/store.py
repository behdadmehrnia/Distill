from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from .models import (
    MeetingInsights,
    MeetingRecord,
    MeetingStatus,
    TranscriptSegment,
)


class TranscriptStore:
    """SQLite persistence for meetings, transcript segments, and insights."""

    def __init__(self, db_path: str = "./data/meetings.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS meetings (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL,
                        sample_rate INTEGER NOT NULL,
                        created_at REAL NOT NULL,
                        started_at REAL,
                        stopped_at REAL,
                        audio_path TEXT,
                        participants TEXT NOT NULL DEFAULT '[]',
                        speaker_map TEXT NOT NULL DEFAULT '{}'
                    );

                    CREATE TABLE IF NOT EXISTS segments (
                        id TEXT PRIMARY KEY,
                        meeting_id TEXT NOT NULL,
                        speaker_id TEXT NOT NULL,
                        start_ms INTEGER NOT NULL,
                        end_ms INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        is_overlap INTEGER NOT NULL DEFAULT 0,
                        provisional INTEGER NOT NULL DEFAULT 0,
                        created_at REAL NOT NULL,
                        FOREIGN KEY(meeting_id) REFERENCES meetings(id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_segments_meeting
                        ON segments(meeting_id, start_ms);

                    CREATE TABLE IF NOT EXISTS insights (
                        meeting_id TEXT PRIMARY KEY,
                        summary TEXT NOT NULL,
                        highlights TEXT NOT NULL,
                        decisions TEXT NOT NULL,
                        action_items TEXT NOT NULL,
                        raw_json TEXT,
                        created_at REAL NOT NULL,
                        FOREIGN KEY(meeting_id) REFERENCES meetings(id)
                    );
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def save_meeting(self, meeting: MeetingRecord) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO meetings (
                        id, title, status, sample_rate, created_at, started_at,
                        stopped_at, audio_path, participants, speaker_map
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title=excluded.title,
                        status=excluded.status,
                        sample_rate=excluded.sample_rate,
                        started_at=excluded.started_at,
                        stopped_at=excluded.stopped_at,
                        audio_path=excluded.audio_path,
                        participants=excluded.participants,
                        speaker_map=excluded.speaker_map
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
                        json.dumps(meeting.participants, ensure_ascii=False),
                        json.dumps(meeting.speaker_map, ensure_ascii=False),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_meeting(self, meeting_id: str) -> Optional[MeetingRecord]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM meetings WHERE id = ?", (meeting_id,)
                ).fetchone()
                if not row:
                    return None
                return self._row_to_meeting(row)
            finally:
                conn.close()

    def list_meetings(self, limit: int = 50) -> List[MeetingRecord]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM meetings ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [self._row_to_meeting(r) for r in rows]
            finally:
                conn.close()

    def update_status(self, meeting_id: str, status: MeetingStatus) -> None:
        meeting = self.get_meeting(meeting_id)
        if not meeting:
            raise KeyError(f"Meeting not found: {meeting_id}")
        meeting.status = status
        self.save_meeting(meeting)

    def save_segment(self, segment: TranscriptSegment) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO segments (
                        id, meeting_id, speaker_id, start_ms, end_ms, text,
                        is_overlap, provisional, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        speaker_id=excluded.speaker_id,
                        start_ms=excluded.start_ms,
                        end_ms=excluded.end_ms,
                        text=excluded.text,
                        is_overlap=excluded.is_overlap,
                        provisional=excluded.provisional
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
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def replace_meeting_segments(
        self, meeting_id: str, segments: List[TranscriptSegment]
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM segments WHERE meeting_id = ?", (meeting_id,))
                conn.executemany(
                    """
                    INSERT INTO segments (
                        id, meeting_id, speaker_id, start_ms, end_ms, text,
                        is_overlap, provisional, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        )
                        for s in segments
                    ],
                )
                conn.commit()
            finally:
                conn.close()

    def get_segments(self, meeting_id: str) -> List[TranscriptSegment]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM segments
                    WHERE meeting_id = ?
                    ORDER BY start_ms ASC, created_at ASC
                    """,
                    (meeting_id,),
                ).fetchall()
                return [self._row_to_segment(r) for r in rows]
            finally:
                conn.close()

    def delete_provisional_segments(self, meeting_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM segments WHERE meeting_id = ? AND provisional = 1",
                    (meeting_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def save_insights(self, insights: MeetingInsights) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO insights (
                        meeting_id, summary, highlights, decisions,
                        action_items, raw_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(meeting_id) DO UPDATE SET
                        summary=excluded.summary,
                        highlights=excluded.highlights,
                        decisions=excluded.decisions,
                        action_items=excluded.action_items,
                        raw_json=excluded.raw_json,
                        created_at=excluded.created_at
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
            finally:
                conn.close()

    def get_insights(self, meeting_id: str) -> Optional[MeetingInsights]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM insights WHERE meeting_id = ?", (meeting_id,)
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
                    created_at=row["created_at"],
                )
            finally:
                conn.close()

    @staticmethod
    def _row_to_meeting(row: sqlite3.Row) -> MeetingRecord:
        return MeetingRecord(
            id=row["id"],
            title=row["title"],
            status=MeetingStatus(row["status"]),
            sample_rate=row["sample_rate"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            stopped_at=row["stopped_at"],
            audio_path=row["audio_path"],
            participants=json.loads(row["participants"] or "[]"),
            speaker_map=json.loads(row["speaker_map"] or "{}"),
        )

    @staticmethod
    def _row_to_segment(row: sqlite3.Row) -> TranscriptSegment:
        return TranscriptSegment(
            id=row["id"],
            meeting_id=row["meeting_id"],
            speaker_id=row["speaker_id"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            text=row["text"],
            is_overlap=bool(row["is_overlap"]),
            provisional=bool(row["provisional"]),
            created_at=row["created_at"],
        )
