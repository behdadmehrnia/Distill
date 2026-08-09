from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

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
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA busy_timeout=5000;")
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
                        overlap_speakers TEXT NOT NULL DEFAULT '[]',
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

                    CREATE TABLE IF NOT EXISTS speaker_intervals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        meeting_id TEXT NOT NULL,
                        speaker_id TEXT NOT NULL,
                        start_ms INTEGER NOT NULL,
                        end_ms INTEGER NOT NULL,
                        is_overlap INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY(meeting_id) REFERENCES meetings(id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_speaker_intervals_meeting
                        ON speaker_intervals(meeting_id);

                    CREATE TABLE IF NOT EXISTS minutes (
                        meeting_id TEXT PRIMARY KEY,
                        subject TEXT NOT NULL DEFAULT '',
                        meeting_date TEXT NOT NULL DEFAULT '',
                        location TEXT NOT NULL DEFAULT '',
                        attendees TEXT NOT NULL DEFAULT '[]',
                        absentees TEXT NOT NULL DEFAULT '[]',
                        secretary TEXT NOT NULL DEFAULT '',
                        summary TEXT NOT NULL DEFAULT '',
                        decisions TEXT NOT NULL DEFAULT '[]',
                        raw_json TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        FOREIGN KEY(meeting_id) REFERENCES meetings(id)
                    );
                    """
                )
                cols = {
                    r[1]
                    for r in conn.execute("PRAGMA table_info(segments)").fetchall()
                }
                if "overlap_speakers" not in cols:
                    conn.execute(
                        "ALTER TABLE segments ADD COLUMN overlap_speakers TEXT NOT NULL DEFAULT '[]'"
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
                        is_overlap, provisional, created_at, overlap_speakers
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        speaker_id=excluded.speaker_id,
                        start_ms=excluded.start_ms,
                        end_ms=excluded.end_ms,
                        text=excluded.text,
                        is_overlap=excluded.is_overlap,
                        provisional=excluded.provisional,
                        overlap_speakers=excluded.overlap_speakers
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
                        is_overlap, provisional, created_at, overlap_speakers
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            json.dumps(s.overlap_speakers or [], ensure_ascii=False),
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

    def get_segment(self, meeting_id: str, segment_id: str) -> Optional[TranscriptSegment]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT * FROM segments
                    WHERE meeting_id = ? AND id = ?
                    """,
                    (meeting_id, segment_id),
                ).fetchone()
                return self._row_to_segment(row) if row else None
            finally:
                conn.close()

    def update_segment_text(
        self, meeting_id: str, segment_id: str, text: str
    ) -> List[TranscriptSegment]:
        """Update finalized segment text. Overlap peers with same span/text are synced."""
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("text must not be empty")

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    """
                    SELECT * FROM segments
                    WHERE meeting_id = ? AND id = ?
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
                        WHERE meeting_id = ?
                          AND provisional = 0
                          AND is_overlap = 1
                          AND start_ms = ?
                          AND end_ms = ?
                          AND text = ?
                        """,
                        (
                            meeting_id,
                            row["start_ms"],
                            row["end_ms"],
                            row["text"],
                        ),
                    ).fetchall()
                    ids = [r["id"] for r in peer_rows] or ids

                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"""
                    UPDATE segments
                    SET text = ?
                    WHERE meeting_id = ? AND id IN ({placeholders})
                    """,
                    (cleaned, meeting_id, *ids),
                )
                conn.commit()

                updated_rows = conn.execute(
                    f"""
                    SELECT * FROM segments
                    WHERE meeting_id = ? AND id IN ({placeholders})
                    ORDER BY start_ms ASC, created_at ASC
                    """,
                    (meeting_id, *ids),
                ).fetchall()
                return [self._row_to_segment(r) for r in updated_rows]
            finally:
                conn.close()

    def replace_speaker_intervals(
        self, meeting_id: str, intervals: List[SpeakerInterval]
    ) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM speaker_intervals WHERE meeting_id = ?", (meeting_id,)
                )
                conn.executemany(
                    """
                    INSERT INTO speaker_intervals (
                        meeting_id, speaker_id, start_ms, end_ms, is_overlap
                    ) VALUES (?, ?, ?, ?, ?)
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
            finally:
                conn.close()

    def get_speaker_intervals(self, meeting_id: str) -> List[SpeakerInterval]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM speaker_intervals
                    WHERE meeting_id = ?
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

    def delete_insights(self, meeting_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM insights WHERE meeting_id = ?", (meeting_id,))
                conn.commit()
            finally:
                conn.close()

    def save_minutes(self, minutes: MeetingMinutes) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO minutes (
                        meeting_id, subject, meeting_date, location, attendees,
                        absentees, secretary, summary, decisions, raw_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(meeting_id) DO UPDATE SET
                        subject=excluded.subject,
                        meeting_date=excluded.meeting_date,
                        location=excluded.location,
                        attendees=excluded.attendees,
                        absentees=excluded.absentees,
                        secretary=excluded.secretary,
                        summary=excluded.summary,
                        decisions=excluded.decisions,
                        raw_json=excluded.raw_json,
                        updated_at=excluded.updated_at
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
                            [d.to_dict() for d in minutes.decisions], ensure_ascii=False
                        ),
                        json.dumps(minutes.raw_json, ensure_ascii=False)
                        if minutes.raw_json
                        else None,
                        minutes.created_at,
                        minutes.updated_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_minutes(self, meeting_id: str) -> Optional[MeetingMinutes]:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM minutes WHERE meeting_id = ?", (meeting_id,)
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
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
            finally:
                conn.close()

    def delete_minutes(self, meeting_id: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM minutes WHERE meeting_id = ?", (meeting_id,))
                conn.commit()
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
        keys = row.keys()
        overlap_speakers = []
        if "overlap_speakers" in keys and row["overlap_speakers"]:
            try:
                overlap_speakers = json.loads(row["overlap_speakers"])
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
            created_at=row["created_at"],
        )
