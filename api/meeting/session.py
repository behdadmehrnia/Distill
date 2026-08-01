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
from .review import TranscriptReviewAgent, gate_stt_text, localize_nonspeech_events
from .store import TranscriptStore

logger = logging.getLogger(__name__)

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]

_STT_BACKOFF_S = (0.5, 1.0, 2.0)


class MeetingSession:
    """Owns continuous capture, windowed STT, and periodic diarization for one meeting."""

    def __init__(
        self,
        record: MeetingRecord,
        store: TranscriptStore,
        stt_provider,
        diarizer: Optional[SpeakerDiarizer] = None,
        review_agent: Optional[TranscriptReviewAgent] = None,
        sample_rate: int = 16000,
        window_ms: int = 8000,
        hop_ms: int = 6000,
        diarize_every_ms: int = 20000,
        audio_dir: str = "./data/audio",
        on_event: Optional[EventCallback] = None,
        tuning: Optional[Dict[str, Any]] = None,
    ):
        self.record = record
        self.store = store
        self.stt = stt_provider
        self.diarizer = diarizer or SpeakerDiarizer(sample_rate=sample_rate)
        self.review_agent = review_agent
        self.sample_rate = sample_rate
        self.diarize_every_ms = diarize_every_ms
        self.on_event = on_event
        self.tuning = tuning if tuning is not None else {}

        os.makedirs(audio_dir, exist_ok=True)
        audio_path = os.path.join(audio_dir, f"{record.id}.wav")
        self.ingest = AudioIngest(sample_rate=sample_rate, audio_path=audio_path)
        win = int(self.tuning.get("window_ms", window_ms))
        hop = int(self.tuning.get("hop_ms", hop_ms))
        if "diarize_every_ms" in self.tuning:
            self.diarize_every_ms = int(self.tuning["diarize_every_ms"])
        self.chunker = OverlappingChunker(
            sample_rate=sample_rate,
            window_ms=win,
            hop_ms=hop,
            min_speech_rms=float(self.tuning.get("min_speech_rms", 0.008)),
        )

        self._lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._pending_lock = asyncio.Lock()
        self._stt_queue: asyncio.Queue = asyncio.Queue()
        self._worker_tasks: List[asyncio.Task] = []
        self._last_diarize_ms = 0
        self._speaker_intervals = []
        self._pending_stt: List[tuple[int, int, str]] = []
        self._running = False
        self._stopping = False

        # Debug counters
        self._stt_calls = 0
        self._stt_retries = 0
        self._stt_dropped = 0
        self._stt_total_ms = 0.0
        self._chunks_processed = 0
        self._cache_hits_at_start = getattr(stt_provider, "cache_hits", 0)
        self._cache_misses_at_start = getattr(stt_provider, "cache_misses", 0)
        self._active_tuning: Dict[str, Any] = dict(self.tuning)

    def apply_tuning(self, tuning: Dict[str, Any]) -> None:
        """Apply live-tunable knobs (window/hop need a new session)."""
        self.tuning = tuning
        if "diarize_every_ms" in tuning:
            self.diarize_every_ms = int(tuning["diarize_every_ms"])
        if "min_speech_rms" in tuning:
            self.chunker.min_speech_rms = float(tuning["min_speech_rms"])

    def _align_kwargs(self) -> Dict[str, Any]:
        return {
            "min_overlap_ms": int(self.tuning.get("min_overlap_ms", 1200)),
            "dedupe_similarity": float(self.tuning.get("dedupe_similarity", 0.45)),
            "dedupe_time_overlap": float(self.tuning.get("dedupe_time_overlap", 0.35)),
        }

    def _review_min_score(self) -> float:
        return float(self.tuning.get("stt_min_quality", 0.35))

    def _review_mode(self) -> str:
        """off | heuristic | finalize | live — default finalize (heuristic always on unless off)."""
        mode = str(self.tuning.get("stt_review_mode") or "finalize").strip().lower()
        if mode not in {"off", "heuristic", "finalize", "live"}:
            return "finalize"
        return mode

    def _stt_workers(self) -> int:
        return max(1, min(5, int(self.tuning.get("stt_workers", 2))))

    def _stt_retry_count(self) -> int:
        return max(0, min(5, int(self.tuning.get("stt_retry_count", 3))))

    def debug_stats(self) -> Dict[str, Any]:
        hits = getattr(self.stt, "cache_hits", 0) - self._cache_hits_at_start
        misses = getattr(self.stt, "cache_misses", 0) - self._cache_misses_at_start
        total_cache = hits + misses
        speakers = {iv.speaker_id for iv in self._speaker_intervals}
        return {
            "meeting_id": self.meeting_id,
            "chunks_processed": self._chunks_processed,
            "chunks_retried": self._stt_retries,
            "chunks_dropped": self._stt_dropped,
            "stt_calls": self._stt_calls,
            "stt_retries": self._stt_retries,
            "stt_dropped": self._stt_dropped,
            "stt_total_ms": round(self._stt_total_ms, 1),
            "stt_cache_hits": hits,
            "stt_cache_misses": misses,
            "stt_cache_hit_rate": round(hits / total_cache, 3) if total_cache else None,
            "diarization_backend": self.diarizer.backend,
            "speakers_detected": len(speakers),
            "speaker_ids": sorted(speakers),
            "tuning": dict(self._active_tuning),
        }

    async def _gate_stt_text(self, text: str) -> Optional[str]:
        mode = self._review_mode()
        if mode == "off":
            cleaned = (text or "").strip()
            if len(cleaned) < 2 or not any(ch.isalpha() for ch in cleaned):
                return None
            return localize_nonspeech_events(cleaned)

        if mode == "live" and self.review_agent is not None:
            result = await self.review_agent.review_text(
                text,
                language=str(self.tuning.get("stt_language") or "fa"),
            )
        else:
            result = gate_stt_text(text, min_score=self._review_min_score())

        if not result.accepted:
            logger.info(
                "STT gate dropped text score=%.2f reasons=%s text=%r",
                result.score,
                ",".join(result.reasons),
                (text or "")[:100],
            )
            return None
        return result.text

    async def _finalize_review(
        self, segments: List[TranscriptSegment]
    ) -> List[TranscriptSegment]:
        mode = self._review_mode()
        if mode in {"off", "heuristic"}:
            # Still apply heuristic-only finalize to catch anything that slipped through
            if mode == "off":
                return segments
            agent = TranscriptReviewAgent(llm=None, enabled=False)
            return await agent.review_segments(
                segments,
                language=str(self.tuning.get("stt_language") or "fa"),
            )

        agent = self.review_agent or TranscriptReviewAgent(llm=None, enabled=False)
        # finalize / live: use LLM on stop/upload if available
        if mode in {"finalize", "live"} and agent.llm is not None:
            agent.enabled = True
        return await agent.review_segments(
            segments,
            language=str(self.tuning.get("stt_language") or "fa"),
        )

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
        self._active_tuning = dict(self.tuning)
        self.record.status = MeetingStatus.RECORDING
        self.record.started_at = time.time()
        self.record.audio_path = self.ingest.audio_path
        self.store.save_meeting(self.record)
        n_workers = self._stt_workers()
        self._worker_tasks = [
            asyncio.create_task(self._stt_worker()) for _ in range(n_workers)
        ]
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

    async def _transcribe_with_retry(self, audio: np.ndarray, language: str) -> Optional[str]:
        """Call STT with exponential backoff; return None if all attempts fail."""
        retries = self._stt_retry_count()
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                text = await self.stt.transcribe(audio, language=language)
                self._stt_calls += 1
                self._stt_total_ms += (time.perf_counter() - t0) * 1000.0
                return text
            except Exception as exc:
                self._stt_calls += 1
                self._stt_total_ms += (time.perf_counter() - t0) * 1000.0
                last_exc = exc
                if attempt >= retries:
                    break
                delay = _STT_BACKOFF_S[min(attempt, len(_STT_BACKOFF_S) - 1)]
                self._stt_retries += 1
                logger.warning(
                    "STT attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt + 1,
                    retries + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        self._stt_dropped += 1
        logger.exception("STT chunk dropped after retries: %s", last_exc)
        await self._emit({"type": "error", "message": f"STT error: {last_exc}"})
        return None

    async def _stt_worker(self) -> None:
        while True:
            chunk = await self._stt_queue.get()
            if chunk is None:
                self._stt_queue.task_done()
                break
            try:
                lang = str(self.tuning.get("stt_language") or "fa")
                text = await self._transcribe_with_retry(chunk.audio, lang)
                if text is None:
                    continue
                text = await self._gate_stt_text(text)
                if not text:
                    continue
                async with self._pending_lock:
                    self._pending_stt.append((chunk.start_ms, chunk.end_ms, text))
                    self._chunks_processed += 1
                    await self._publish_live_transcript()
            except Exception as exc:
                logger.exception("STT chunk failed: %s", exc)
                self._stt_dropped += 1
                await self._emit({"type": "error", "message": f"STT error: {exc}"})
            finally:
                self._stt_queue.task_done()

    async def _publish_live_transcript(self) -> None:
        """Realign + dedupe all pending hop windows and replace provisional UI rows."""
        if not self._pending_stt:
            return
        segments = align_stt_with_diarization(
            self.meeting_id,
            list(self._pending_stt),
            self._speaker_intervals,
            provisional=True,
            **self._align_kwargs(),
        )
        segments = dedupe_overlapping_transcripts(
            segments,
            similarity_threshold=float(self.tuning.get("dedupe_similarity", 0.45)),
            min_time_overlap_ratio=float(self.tuning.get("dedupe_time_overlap", 0.35)),
        )
        self.store.replace_meeting_segments(self.meeting_id, segments)
        await self._emit(
            {
                "type": "transcript",
                "meeting_id": self.meeting_id,
                "segments": [s.to_dict() for s in segments],
            }
        )

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
                    **self._align_kwargs(),
                )
                segments = dedupe_overlapping_transcripts(
                    segments,
                    similarity_threshold=float(self.tuning.get("dedupe_similarity", 0.45)),
                    min_time_overlap_ratio=float(self.tuning.get("dedupe_time_overlap", 0.35)),
                )
                if not provisional:
                    segments = await self._finalize_review(segments)
                self.store.replace_meeting_segments(self.meeting_id, segments)
                await self._emit(
                    {
                        "type": "transcript",
                        "meeting_id": self.meeting_id,
                        "segments": [s.to_dict() for s in segments],
                    }
                )

    async def stop(self) -> MeetingRecord:
        async with self._stop_lock:
            if self._stopping or not self._running:
                return self.record
            self._stopping = True

            self.record.status = MeetingStatus.PROCESSING
            self.store.save_meeting(self.record)
            await self._emit({"type": "status", "status": "processing", "meeting_id": self.meeting_id})

            # Flush remaining audio window
            rem = self.chunker.flush_remainder(self.ingest.get_buffer())
            if rem is not None:
                await self._stt_queue.put(rem)

            n_workers = len(self._worker_tasks) or 1
            for _ in range(n_workers):
                await self._stt_queue.put(None)
            if self._worker_tasks:
                await asyncio.gather(*self._worker_tasks)
            self._worker_tasks = []

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
        self._active_tuning = dict(self.tuning)
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
        results_lock = asyncio.Lock()
        sem = asyncio.Semaphore(self._stt_workers())

        async def _process_chunk(chunk) -> None:
            async with sem:
                lang = str(self.tuning.get("stt_language") or "fa")
                text = await self._transcribe_with_retry(chunk.audio, lang)
                if text is None:
                    return
                text = await self._gate_stt_text(text)
                if not text:
                    return
                async with results_lock:
                    stt_results.append((chunk.start_ms, chunk.end_ms, text))
                    self._chunks_processed += 1
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

        await asyncio.gather(*[_process_chunk(c) for c in windows])
        stt_results.sort(key=lambda x: x[0])

        segments = align_stt_with_diarization(
            self.meeting_id,
            stt_results,
            intervals,
            provisional=False,
            **self._align_kwargs(),
        )
        segments = dedupe_overlapping_transcripts(
            segments,
            similarity_threshold=float(self.tuning.get("dedupe_similarity", 0.45)),
            min_time_overlap_ratio=float(self.tuning.get("dedupe_time_overlap", 0.35)),
        )
        segments = await self._finalize_review(segments)
        self.store.replace_meeting_segments(self.meeting_id, segments)
        await self._emit(
            {
                "type": "transcript",
                "meeting_id": self.meeting_id,
                "segments": [s.to_dict() for s in segments],
            }
        )

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
        review_agent: Optional[TranscriptReviewAgent] = None,
        tuning: Optional[Dict[str, Any]] = None,
        **session_kwargs,
    ):
        self.store = store
        self.stt = stt_provider
        self.diarizer = diarizer or SpeakerDiarizer()
        self.review_agent = review_agent
        self.tuning = tuning if tuning is not None else {}
        self.session_kwargs = session_kwargs
        self._sessions: Dict[str, MeetingSession] = {}

    def apply_tuning(self, tuning: Dict[str, Any]) -> None:
        self.tuning = tuning
        # Keep session_kwargs in sync for newly created meetings
        self.session_kwargs["window_ms"] = int(tuning.get("window_ms", 8000))
        self.session_kwargs["hop_ms"] = int(tuning.get("hop_ms", 6000))
        self.session_kwargs["diarize_every_ms"] = int(tuning.get("diarize_every_ms", 20000))
        if hasattr(self.diarizer, "apply_tuning"):
            self.diarizer.apply_tuning(tuning)
        for session in self._sessions.values():
            session.apply_tuning(tuning)

    def _session_args(self, on_event: Optional[EventCallback] = None) -> Dict[str, Any]:
        return {
            **self.session_kwargs,
            "tuning": self.tuning,
            "on_event": on_event,
        }

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
            review_agent=self.review_agent,
            **self._session_args(on_event),
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
            review_agent=self.review_agent,
            **self._session_args(on_event),
        )
        self._sessions[meeting_id] = session
        return session
