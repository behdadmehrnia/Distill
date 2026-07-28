from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

import numpy as np

from .aligner import (
    align_stt_with_diarization,
    dedupe_overlapping_transcripts,
)
from .chunker import OverlappingChunker
from .diarization import SpeakerDiarizer
from .ingest import AudioIngest
from .models import MeetingRecord, MeetingStatus, TranscriptSegment
from .store import TranscriptStore

logger = logging.getLogger(__name__)

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class MeetingSession:
    """Owns continuous capture, windowed STT, and periodic diarization for one meeting."""

    def __init__(
        self,
        record: MeetingRecord,
        store: TranscriptStore,
        stt_provider,
        diarizer: Optional[SpeakerDiarizer] = None,
        sample_rate: int = 16000,
        window_ms: int = 8000,
        hop_ms: int = 2000,
        diarize_every_ms: int = 20000,
        audio_dir: str = "./data/audio",
        on_event: Optional[EventCallback] = None,
    ):
        self.record = record
        self.store = store
        self.stt = stt_provider
        self.diarizer = diarizer or SpeakerDiarizer(sample_rate=sample_rate)
        self.sample_rate = sample_rate
        self.diarize_every_ms = diarize_every_ms
        self.on_event = on_event

        os.makedirs(audio_dir, exist_ok=True)
        audio_path = os.path.join(audio_dir, f"{record.id}.wav")
        self.ingest = AudioIngest(sample_rate=sample_rate, audio_path=audio_path)
        self.chunker = OverlappingChunker(
            sample_rate=sample_rate, window_ms=window_ms, hop_ms=hop_ms
        )

        self._lock = asyncio.Lock()
        self._stt_queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._last_diarize_ms = 0
        self._speaker_intervals = []
        self._pending_stt: List[tuple[int, int, str]] = []
        self._running = False

    @property
    def meeting_id(self) -> str:
        return self.record.id

    async def _emit(self, event: Dict[str, Any]) -> None:
        if self.on_event:
            try:
                await self.on_event(event)
            except Exception as exc:
                logger.warning("Event callback failed: %s", exc)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.record.status = MeetingStatus.RECORDING
        self.record.started_at = time.time()
        self.record.audio_path = self.ingest.audio_path
        self.store.save_meeting(self.record)
        self._worker_task = asyncio.create_task(self._stt_worker())
        await self._emit({"type": "status", "status": self.record.status.value, "meeting_id": self.meeting_id})

    async def append_audio_int16(self, samples: np.ndarray) -> None:
        """Append PCM int16 without dropping — always keeps recording."""
        if not self._running:
            return
        self.ingest.append_int16(samples)
        buf = self.ingest.get_buffer()
        chunks = self.chunker.pop_ready_chunks(buf)
        for chunk in chunks:
            await self._stt_queue.put(chunk)

        if self.ingest.duration_ms - self._last_diarize_ms >= self.diarize_every_ms:
            asyncio.create_task(self._refresh_diarization(provisional=True))

    async def _stt_worker(self) -> None:
        while True:
            chunk = await self._stt_queue.get()
            if chunk is None:
                self._stt_queue.task_done()
                break
            try:
                text = await self.stt.transcribe(chunk.audio)
                text = (text or "").strip()
                if not text:
                    continue
                self._pending_stt.append((chunk.start_ms, chunk.end_ms, text))
                segments = align_stt_with_diarization(
                    self.meeting_id,
                    [(chunk.start_ms, chunk.end_ms, text)],
                    self._speaker_intervals,
                    provisional=True,
                )
                for seg in segments:
                    self.store.save_segment(seg)
                    await self._emit({"type": "segment", "segment": seg.to_dict()})
            except Exception as exc:
                logger.exception("STT chunk failed: %s", exc)
                await self._emit({"type": "error", "message": f"STT error: {exc}"})
            finally:
                self._stt_queue.task_done()

    async def _refresh_diarization(self, provisional: bool = True) -> None:
        async with self._lock:
            audio = self.ingest.get_buffer()
            if len(audio) < self.sample_rate:
                return
            intervals = await asyncio.to_thread(self.diarizer.diarize, audio, self.sample_rate)
            self._speaker_intervals = intervals
            self._last_diarize_ms = self.ingest.duration_ms

            await self._emit(
                {
                    "type": "speaker_update",
                    "backend": self.diarizer.backend,
                    "intervals": [iv.to_dict() for iv in intervals],
                    "overlaps": [iv.to_dict() for iv in intervals if iv.is_overlap],
                }
            )

            if self._pending_stt:
                segments = align_stt_with_diarization(
                    self.meeting_id,
                    list(self._pending_stt),
                    intervals,
                    provisional=provisional,
                )
                segments = dedupe_overlapping_transcripts(segments)
                # Always rebuild from pending STT windows after diarization refresh
                self.store.delete_provisional_segments(self.meeting_id)
                if not provisional:
                    self.store.replace_meeting_segments(self.meeting_id, segments)
                else:
                    for seg in segments:
                        self.store.save_segment(seg)
                for seg in segments:
                    await self._emit({"type": "segment", "segment": seg.to_dict()})

    async def stop(self) -> MeetingRecord:
        if not self._running:
            return self.record

        self.record.status = MeetingStatus.PROCESSING
        self.store.save_meeting(self.record)
        await self._emit({"type": "status", "status": "processing", "meeting_id": self.meeting_id})

        # Flush remaining audio window
        rem = self.chunker.flush_remainder(self.ingest.get_buffer())
        if rem is not None:
            await self._stt_queue.put(rem)

        await self._stt_queue.put(None)
        if self._worker_task:
            await self._worker_task

        # Finalize diarization + alignment
        await self._refresh_diarization(provisional=False)

        try:
            path = self.ingest.save_wav()
            self.record.audio_path = path
        except Exception as exc:
            logger.warning("Could not save WAV: %s", exc)

        self._running = False
        self.record.status = MeetingStatus.STOPPED
        self.record.stopped_at = time.time()
        self.store.save_meeting(self.record)
        await self._emit({"type": "status", "status": "stopped", "meeting_id": self.meeting_id})
        return self.record

    async def process_uploaded_file(self, path: str) -> List[TranscriptSegment]:
        """Offline path: load file → diarize → windowed STT → align → store."""
        self.record.status = MeetingStatus.PROCESSING
        self.record.started_at = time.time()
        self.store.save_meeting(self.record)
        await self._emit({"type": "status", "status": "processing", "meeting_id": self.meeting_id})

        self.ingest.load_from_file(path)
        # Keep a copy under data/audio
        out_path = self.ingest.audio_path or os.path.join("./data/audio", f"{self.meeting_id}.wav")
        try:
            self.ingest.save_wav(out_path)
            self.record.audio_path = out_path
        except Exception:
            self.record.audio_path = path

        audio = self.ingest.get_buffer()
        intervals = await asyncio.to_thread(self.diarizer.diarize, audio, self.sample_rate)
        self._speaker_intervals = intervals
        await self._emit(
            {
                "type": "speaker_update",
                "backend": self.diarizer.backend,
                "intervals": [iv.to_dict() for iv in intervals],
                "overlaps": [iv.to_dict() for iv in intervals if iv.is_overlap],
            }
        )

        self.chunker.reset()
        windows = self.chunker.pop_ready_chunks(audio)
        rem = self.chunker.flush_remainder(audio)
        if rem is not None:
            windows.append(rem)

        stt_results: List[tuple[int, int, str]] = []
        for chunk in windows:
            try:
                text = await self.stt.transcribe(chunk.audio)
                text = (text or "").strip()
                if text:
                    stt_results.append((chunk.start_ms, chunk.end_ms, text))
                    await self._emit(
                        {
                            "type": "status",
                            "status": "transcribing",
                            "progress": {
                                "chunk": chunk.index,
                                "start_ms": chunk.start_ms,
                                "end_ms": chunk.end_ms,
                            },
                        }
                    )
            except Exception as exc:
                logger.exception("Offline STT failed: %s", exc)

        segments = align_stt_with_diarization(
            self.meeting_id, stt_results, intervals, provisional=False
        )
        segments = dedupe_overlapping_transcripts(segments)
        self.store.replace_meeting_segments(self.meeting_id, segments)
        for seg in segments:
            await self._emit({"type": "segment", "segment": seg.to_dict()})

        self.record.status = MeetingStatus.STOPPED
        self.record.stopped_at = time.time()
        self.store.save_meeting(self.record)
        await self._emit({"type": "status", "status": "stopped", "meeting_id": self.meeting_id})
        return segments


class MeetingManager:
    """In-memory registry of active MeetingSession objects."""

    def __init__(
        self,
        store: TranscriptStore,
        stt_provider,
        diarizer: Optional[SpeakerDiarizer] = None,
        **session_kwargs,
    ):
        self.store = store
        self.stt = stt_provider
        self.diarizer = diarizer or SpeakerDiarizer()
        self.session_kwargs = session_kwargs
        self._sessions: Dict[str, MeetingSession] = {}

    def create_meeting(
        self,
        title: str = "Untitled Meeting",
        participants: Optional[List[str]] = None,
        on_event: Optional[EventCallback] = None,
    ) -> MeetingSession:
        record = MeetingRecord.create(title=title, participants=participants)
        self.store.save_meeting(record)
        session = MeetingSession(
            record=record,
            store=self.store,
            stt_provider=self.stt,
            diarizer=self.diarizer,
            on_event=on_event,
            **self.session_kwargs,
        )
        self._sessions[record.id] = session
        return session

    def get(self, meeting_id: str) -> Optional[MeetingSession]:
        return self._sessions.get(meeting_id)

    def get_or_restore(self, meeting_id: str, on_event: Optional[EventCallback] = None) -> Optional[MeetingSession]:
        existing = self._sessions.get(meeting_id)
        if existing:
            if on_event:
                existing.on_event = on_event
            return existing
        record = self.store.get_meeting(meeting_id)
        if not record:
            return None
        session = MeetingSession(
            record=record,
            store=self.store,
            stt_provider=self.stt,
            diarizer=self.diarizer,
            on_event=on_event,
            **self.session_kwargs,
        )
        self._sessions[meeting_id] = session
        return session
