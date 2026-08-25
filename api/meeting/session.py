from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .aligner import (
    align_stt_with_diarization,
    dedupe_overlapping_transcripts,
)
from .chunker import AudioChunk, OverlappingChunker
from .diarization import SpeakerDiarizer
from .ingest import AudioIngest
from .models import CaptureMode, MeetingRecord, MeetingStatus, SpeakerInterval, TranscriptSegment
from .multi_stream import (
    MultiStreamChunkerRegistry,
    MultiStreamIngest,
    merge_speaker_intervals,
    normalize_speaker_id,
)
from .review import (
    TranscriptReviewAgent,
    gate_stt_text,
    is_whisper_boilerplate,
    localize_nonspeech_events,
    score_stt_text,
)
from .store import TranscriptStore

logger = logging.getLogger(__name__)

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]

_STT_BACKOFF_S = (0.5, 1.0, 2.0)

# (start_ms, end_ms, text, optional absolute word timings)
PendingStt = Tuple[int, int, str, Optional[Sequence[Tuple[str, int, int]]]]


@dataclass
class _SttWorkItem:
    chunk: AudioChunk
    speaker_id: Optional[str] = None


class ProcessingCancelled(Exception):
    """Raised when a client aborts an in-flight processing pipeline."""


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
        window_ms: int = 10000,
        hop_ms: int = 8500,
        diarize_every_ms: int = 0,
        audio_dir: str = "./data/audio",
        on_event: Optional[EventCallback] = None,
        tuning: Optional[Dict[str, Any]] = None,
        upload_denoise_enabled: bool = True,
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
        self.upload_denoise_enabled = upload_denoise_enabled

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

        self._diarize_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        # Serializes pending_stt mutations + DB replace + transcript emit
        self._transcript_lock = asyncio.Lock()
        self._stt_queue: asyncio.Queue = asyncio.Queue()
        self._worker_tasks: List[asyncio.Task] = []
        self._last_diarize_ms = 0
        self._speaker_intervals = []
        self._pending_stt: List[PendingStt] = []
        self._stream_pending_stt: Dict[str, List[PendingStt]] = {}
        self._is_multi_stream = record.capture_mode == CaptureMode.MULTI_STREAM
        self._multi_ingest: Optional[MultiStreamIngest] = None
        self._multi_chunkers: Optional[MultiStreamChunkerRegistry] = None
        if self._is_multi_stream:
            self._multi_ingest = MultiStreamIngest(
                sample_rate=sample_rate,
                audio_dir=audio_dir,
                meeting_id=record.id,
            )
            self._multi_chunkers = MultiStreamChunkerRegistry(
                sample_rate=sample_rate,
                window_ms=win,
                hop_ms=hop,
                min_speech_rms=float(self.tuning.get("min_speech_rms", 0.008)),
            )
            for speaker_id in record.speaker_map:
                self._multi_ingest.register_stream(speaker_id)
        self._running = False
        self._stopping = False
        self._cancel_requested = False
        self._pipeline_active = False

        # Debug counters
        self._stt_calls = 0
        self._stt_retries = 0
        self._stt_dropped = 0
        self._stt_total_ms = 0.0
        self._chunks_processed = 0
        self._stt_with_timings = 0
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
        if hasattr(self.diarizer, "apply_tuning"):
            self.diarizer.apply_tuning(tuning)

    @property
    def is_multi_stream(self) -> bool:
        return self._is_multi_stream

    @property
    def uses_diarization(self) -> bool:
        return not self._is_multi_stream

    def register_stream(self, speaker_id: str, name: Optional[str] = None) -> str:
        """Register a participant audio stream (multi-stream mode only)."""
        if not self._is_multi_stream or self._multi_ingest is None:
            raise RuntimeError("multi-stream capture is not enabled for this meeting")
        sid = normalize_speaker_id(speaker_id)
        self._multi_ingest.register_stream(sid)
        if name and str(name).strip():
            self.record.speaker_map[sid] = str(name).strip()
            self.store.save_meeting(self.record)
        return sid

    def _record_stream_activity(
        self, speaker_id: str, start_ms: int, end_ms: int
    ) -> None:
        self._speaker_intervals.append(
            SpeakerInterval(speaker_id, start_ms, end_ms, False)
        )
        self._speaker_intervals = merge_speaker_intervals(self._speaker_intervals)

    def set_speaker_map(self, mapping: Dict[str, str]) -> MeetingRecord:
        cleaned = {
            str(k).strip(): str(v).strip()
            for k, v in (mapping or {}).items()
            if str(k).strip() and str(v).strip()
        }
        self.record.speaker_map.update(cleaned)
        self.store.save_meeting(self.record)
        return self.record

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
        return max(1, min(5, int(self.tuning.get("stt_workers", 1))))

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
            "stt_with_timings": self._stt_with_timings,
            "stt_cache_hits": hits,
            "stt_cache_misses": misses,
            "stt_cache_hit_rate": round(hits / total_cache, 3) if total_cache else None,
            "diarization_backend": (
                "stream" if self._is_multi_stream else self.diarizer.backend
            ),
            "capture_mode": self.record.capture_mode.value,
            "stream_ids": (
                self._multi_ingest.stream_ids if self._multi_ingest else []
            ),
            "speakers_detected": len(speakers),
            "speaker_ids": sorted(speakers),
            "speaker_map": dict(self.record.speaker_map),
            "tuning": dict(self._active_tuning),
        }

    async def _gate_stt_text(self, text: str) -> Optional[str]:
        mode = self._review_mode()
        lang = str(self.tuning.get("stt_language") or "fa")
        if is_whisper_boilerplate(text):
            logger.info("STT gate dropped Whisper boilerplate text=%r", (text or "")[:80])
            return None
        if mode == "off":
            cleaned = (text or "").strip()
            if len(cleaned) < 2 or not any(ch.isalpha() for ch in cleaned):
                return None
            # Language-drift guard only; skip full heuristic polish when off
            score, reasons = score_stt_text(cleaned, language=lang)
            if score < 0.35 and any(
                r in {"wrong_script", "no_persian", "whisper_boilerplate"}
                or r.startswith("latin_")
                for r in reasons
            ):
                logger.info(
                    "STT gate dropped text score=%.2f reasons=%s text=%r",
                    score,
                    ",".join(reasons),
                    cleaned[:100],
                )
                return None
            return localize_nonspeech_events(cleaned)

        if mode == "live" and self.review_agent is not None:
            result = await self.review_agent.review_text(
                text, language=lang, min_score=self._review_min_score()
            )
        else:
            result = gate_stt_text(
                text, min_score=self._review_min_score(), language=lang
            )

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
            if mode == "off":
                return segments
            agent = TranscriptReviewAgent(llm=None, enabled=False)
            return await agent.review_segments(
                segments,
                language=str(self.tuning.get("stt_language") or "fa"),
            )

        agent = self.review_agent or TranscriptReviewAgent(llm=None, enabled=False)
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

    def recording_file_path(self) -> Optional[str]:
        """Return filesystem path of a saved recording if the file exists."""
        candidates = []
        if self.record.audio_path:
            candidates.append(self.record.audio_path)
        if self.ingest.audio_path and self.ingest.audio_path not in candidates:
            candidates.append(self.ingest.audio_path)
        for path in candidates:
            if path and os.path.isfile(path) and os.path.getsize(path) > 0:
                return path
        return None

    def clear_for_rerecord(self) -> None:
        """Wipe audio buffer, transcript, and insights before a fresh capture."""
        if self._running:
            raise RuntimeError("cannot clear while recording")
        paths = set()
        if self.record.audio_path:
            paths.add(self.record.audio_path)
        if self.ingest.audio_path:
            paths.add(self.ingest.audio_path)
        for path in paths:
            try:
                if path and os.path.isfile(path):
                    os.remove(path)
            except OSError as exc:
                logger.warning("Could not remove recording %s: %s", path, exc)

        self.ingest.clear()
        self.chunker.reset()
        self._pending_stt = []
        self._stream_pending_stt = {}
        self._speaker_intervals = []
        if self._multi_ingest is not None:
            self._multi_ingest.clear()
        if self._multi_chunkers is not None:
            self._multi_chunkers.reset()
        self._last_diarize_ms = 0
        self._stopping = False
        self._stt_calls = 0
        self._stt_retries = 0
        self._stt_dropped = 0
        self._stt_total_ms = 0.0
        self._chunks_processed = 0
        self._stt_with_timings = 0
        self._cache_hits_at_start = getattr(self.stt, "cache_hits", 0)
        self._cache_misses_at_start = getattr(self.stt, "cache_misses", 0)

        self.store.replace_meeting_segments(self.meeting_id, [])
        self.store.delete_insights(self.meeting_id)
        self.store.replace_speaker_intervals(self.meeting_id, [])
        self.store.delete_minutes(self.meeting_id)
        self.record.audio_path = self.ingest.audio_path
        self.record.started_at = None
        self.record.stopped_at = None
        self.record.status = MeetingStatus.CREATED
        self.store.save_meeting(self.record)

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _check_cancelled(self) -> None:
        if self._cancel_requested:
            raise ProcessingCancelled()

    async def _halt_workers(self) -> None:
        if not self._worker_tasks:
            return
        n_workers = len(self._worker_tasks) or 1
        for _ in range(n_workers):
            try:
                await self._stt_queue.put(None)
            except Exception:
                pass
        for task in self._worker_tasks:
            task.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks = []

    async def _apply_cancel(self, extra_paths: Optional[Sequence[str]] = None) -> MeetingRecord:
        self._running = False
        await self._halt_workers()
        self._stopping = False
        self._cancel_requested = False
        self._pipeline_active = False
        for path in extra_paths or ():
            try:
                if path and os.path.isfile(path):
                    os.remove(path)
            except OSError as exc:
                logger.warning("Could not remove cancelled upload %s: %s", path, exc)
        self.clear_for_rerecord()
        await self._emit(
            {
                "type": "status",
                "status": MeetingStatus.CREATED.value,
                "meeting_id": self.meeting_id,
                "cancelled": True,
            }
        )
        return self.record

    async def cancel(self, extra_paths: Optional[Sequence[str]] = None) -> MeetingRecord:
        self.request_cancel()
        async with self._stop_lock:
            return await self._apply_cancel(extra_paths=extra_paths)

    async def start(self, *, reset: bool = False) -> None:
        if self._running:
            return
        if reset:
            self.clear_for_rerecord()
        self._stopping = False
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
        await self._emit(
            {
                "type": "status",
                "status": self.record.status.value,
                "meeting_id": self.meeting_id,
                "diarization_backend": self.diarizer.backend,
            }
        )
        if self.diarizer.backend not in {"pyannote", "nemo"} and not self._is_multi_stream:
            await self._emit(
                {
                    "type": "warning",
                    "meeting_id": self.meeting_id,
                    "code": "fallback_diarization",
                    "message": (
                        "Diarization backend is fallback (not pyannote/nemo). "
                        "Speaker labels may be unreliable on a single mic. "
                        "Start runtime diarize and set DIARIZATION_ENDPOINT."
                    ),
                }
            )

    async def append_audio_int16(self, samples: np.ndarray) -> None:
        """Append PCM int16 without dropping — always keeps recording (mono mode)."""
        if not self._running or self._is_multi_stream:
            return
        self.ingest.append_int16(samples)
        buf = self.ingest.get_buffer()
        chunks = self.chunker.pop_ready_chunks(buf)
        for chunk in chunks:
            await self._stt_queue.put(_SttWorkItem(chunk=chunk))

        if (
            self.diarize_every_ms > 0
            and self.ingest.duration_ms - self._last_diarize_ms >= self.diarize_every_ms
        ):
            asyncio.create_task(self._refresh_diarization(provisional=True))

    async def append_stream_audio_int16(
        self, speaker_id: str, samples: np.ndarray
    ) -> None:
        """Append tagged PCM for one participant stream (multi-stream mode)."""
        if not self._running or not self._is_multi_stream:
            return
        if self._multi_ingest is None or self._multi_chunkers is None:
            return
        sid = self.register_stream(speaker_id)
        self._multi_ingest.append_int16(sid, samples)
        buf = self._multi_ingest.get_buffer(sid)
        for chunk in self._multi_chunkers.pop_ready(sid, buf):
            self._record_stream_activity(sid, chunk.start_ms, chunk.end_ms)
            await self._stt_queue.put(_SttWorkItem(chunk=chunk, speaker_id=sid))

    def _words_abs(
        self, chunk_start_ms: int, result
    ) -> Optional[List[Tuple[str, int, int]]]:
        words = getattr(result, "words", None) or []
        if not words:
            return None
        abs_words: List[Tuple[str, int, int]] = []
        for w in words:
            abs_words.append(
                (
                    w.word,
                    chunk_start_ms + int(w.start_s * 1000),
                    chunk_start_ms + int(w.end_s * 1000),
                )
            )
        return abs_words

    async def _transcribe_with_retry(
        self, audio: np.ndarray, language: str, prompt: Optional[str] = None
    ):
        """Call STT with exponential backoff; return None if all attempts fail."""
        retries = self._stt_retry_count()
        last_exc: Optional[Exception] = None
        detailed = getattr(self.stt, "transcribe_detailed", None)
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                if detailed is not None:
                    try:
                        result = await detailed(
                            audio, language=language, prompt=prompt
                        )
                    except TypeError:
                        result = await detailed(audio, language=language)
                else:
                    kwargs: Dict[str, Any] = {"language": language}
                    if prompt:
                        kwargs["prompt"] = prompt
                    try:
                        text = await self.stt.transcribe(audio, **kwargs)
                    except TypeError:
                        text = await self.stt.transcribe(audio, language=language)
                    from api.providers.stt import STTResult

                    result = STTResult(text=text, words=[])
                self._stt_calls += 1
                self._stt_total_ms += (time.perf_counter() - t0) * 1000.0
                return result
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

    def _stt_context_prompt(self, speaker_id: Optional[str] = None) -> Optional[str]:
        """Previous accepted transcript — Whisper's intended `prompt` usage."""
        if speaker_id:
            pending = self._stream_pending_stt.get(speaker_id) or []
        else:
            pending = self._pending_stt
        if not pending:
            return None
        text = (pending[-1][2] or "").strip()
        if len(text) < 8:
            return None
        return text[-240:]

    async def _stt_worker(self) -> None:
        while True:
            item = await self._stt_queue.get()
            if item is None:
                self._stt_queue.task_done()
                break
            try:
                if isinstance(item, _SttWorkItem):
                    chunk = item.chunk
                    speaker_id = item.speaker_id
                else:
                    chunk = item
                    speaker_id = None
                lang = str(self.tuning.get("stt_language") or "fa")
                prompt = self._stt_context_prompt(speaker_id)
                result = await self._transcribe_with_retry(
                    chunk.audio, lang, prompt=prompt
                )
                if result is None:
                    continue
                raw = (result.text or "").strip()
                if not raw:
                    logger.debug(
                        "STT empty chunk %dms-%dms (rms gate passed, model returned '')",
                        chunk.start_ms,
                        chunk.end_ms,
                    )
                    continue
                text = await self._gate_stt_text(raw)
                if not text:
                    continue
                words = self._words_abs(chunk.start_ms, result)
                if words:
                    self._stt_with_timings += 1
                async with self._transcript_lock:
                    if speaker_id:
                        pending = self._stream_pending_stt.setdefault(speaker_id, [])
                        self._upsert_pending_stt(
                            chunk.start_ms,
                            chunk.end_ms,
                            text,
                            words,
                            target=pending,
                        )
                    else:
                        self._upsert_pending_stt(
                            chunk.start_ms, chunk.end_ms, text, words
                        )
                    self._chunks_processed += 1
                    if speaker_id:
                        await self._publish_live_transcript_multi_unlocked()
                    else:
                        await self._publish_live_transcript_unlocked()
            except Exception as exc:
                logger.exception("STT chunk failed: %s", exc)
                self._stt_dropped += 1
                await self._emit({"type": "error", "message": f"STT error: {exc}"})
            finally:
                self._stt_queue.task_done()

    def _upsert_pending_stt(
        self,
        start_ms: int,
        end_ms: int,
        text: str,
        words: Optional[Sequence[Tuple[str, int, int]]],
        *,
        target: Optional[List[PendingStt]] = None,
    ) -> None:
        """Append-only live buffer: update same span, otherwise keep a new row."""
        from api.meeting.aligner import (
            _merge_word_timings,
            _overlap_ms,
            _same_utterance,
            _token_containment,
        )

        pending = self._pending_stt if target is None else target
        text = (text or "").strip()
        if not text:
            return

        best_idx = None
        best_ratio = 0.0
        for i, (ps, pe, _pt, _pw) in enumerate(pending):
            ov = _overlap_ms(ps, pe, start_ms, end_ms)
            shorter = max(1, min(pe - ps, end_ms - start_ms))
            ratio = ov / shorter
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = i

        if best_idx is not None and best_ratio >= 0.5:
            ps, pe, pt, pw = pending[best_idx]
            pt = pt or ""
            new_tokens = len(text.split())
            old_tokens = len(pt.split())
            related = (not pt) or _same_utterance(pt, text) or _token_containment(
                pt, text
            ) >= 0.4
            if related and new_tokens >= old_tokens:
                chosen_text, chosen_words = text, words
            else:
                chosen_text, chosen_words = pt, pw
            pending[best_idx] = (
                min(ps, start_ms),
                max(pe, end_ms),
                chosen_text,
                _merge_word_timings(pw, words) or chosen_words,
            )
        else:
            pending.append((start_ms, end_ms, text, words))

        pending.sort(key=lambda row: row[0])

    async def _publish_live_transcript_unlocked(self) -> None:
        """Caller must hold `_transcript_lock`."""
        if not self._pending_stt:
            return
        # Live path uses STT text only — hop word timings are often partial and
        # previously rebuilt the whole UI from a single late word like «خب».
        windows = [
            (start_ms, end_ms, text, None)
            for start_ms, end_ms, text, _words in self._pending_stt
        ]
        segments = align_stt_with_diarization(
            self.meeting_id,
            windows,
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

    def _segments_from_stream_pending(
        self, *, provisional: bool
    ) -> List[TranscriptSegment]:
        rows: List[TranscriptSegment] = []
        for speaker_id, pending in self._stream_pending_stt.items():
            for start_ms, end_ms, text, _words in pending:
                if not (text or "").strip():
                    continue
                rows.append(
                    TranscriptSegment.create(
                        self.meeting_id,
                        speaker_id,
                        start_ms,
                        end_ms,
                        text,
                        provisional=provisional,
                    )
                )
        rows.sort(key=lambda s: (s.start_ms, s.speaker_id))
        return dedupe_overlapping_transcripts(
            rows,
            similarity_threshold=float(self.tuning.get("dedupe_similarity", 0.45)),
            min_time_overlap_ratio=float(self.tuning.get("dedupe_time_overlap", 0.35)),
        )

    async def _publish_live_transcript_multi_unlocked(self) -> None:
        """Caller must hold `_transcript_lock`. Multi-stream: skip diarization align."""
        segments = self._segments_from_stream_pending(provisional=True)
        if not segments:
            return
        self.store.replace_meeting_segments(self.meeting_id, segments)
        await self._emit(
            {
                "type": "transcript",
                "meeting_id": self.meeting_id,
                "segments": [s.to_dict() for s in segments],
            }
        )

    async def _emit_stream_speaker_update(self, *, provisional: bool) -> None:
        intervals = merge_speaker_intervals(self._speaker_intervals)
        self._speaker_intervals = intervals
        if not provisional:
            self.store.replace_speaker_intervals(self.meeting_id, intervals)
        await self._emit(
            {
                "type": "speaker_update",
                "backend": "stream",
                "intervals": [iv.to_dict() for iv in intervals],
                "overlaps": [iv.to_dict() for iv in intervals if iv.is_overlap],
            }
        )

    async def _refresh_diarization(
        self, provisional: bool = True, review_phase: Optional[str] = None
    ) -> None:
        if self._is_multi_stream:
            await self._emit_stream_speaker_update(provisional=provisional)
            return
        # Ignore late live-diarize tasks once stop() has started — they can race
        # the final pass and wipe/replace the transcript mid-processing.
        if provisional and self._stopping:
            return

        async with self._diarize_lock:
            if provisional and self._stopping:
                return
            audio = self.ingest.get_buffer()
            if len(audio) < self.sample_rate:
                return
            intervals = await asyncio.to_thread(
                self.diarizer.diarize, audio, self.sample_rate
            )
            duration_ms = self.ingest.duration_ms

        review_input: Optional[List[TranscriptSegment]] = None
        async with self._transcript_lock:
            if provisional and self._stopping:
                return
            self._speaker_intervals = intervals
            self._last_diarize_ms = duration_ms
            if not provisional:
                self.store.replace_speaker_intervals(self.meeting_id, intervals)

            await self._emit(
                {
                    "type": "speaker_update",
                    "backend": self.diarizer.backend,
                    "intervals": [iv.to_dict() for iv in intervals],
                    "overlaps": [iv.to_dict() for iv in intervals if iv.is_overlap],
                }
            )

            pending = list(self._pending_stt)
            if pending:
                # Live/provisional: text-only windows. Final stop may keep words
                # for speaker splits when they adequately cover the text.
                align_windows: list = (
                    [(s, e, t, None) for s, e, t, _w in pending]
                    if provisional
                    else pending
                )
                segments = align_stt_with_diarization(
                    self.meeting_id,
                    align_windows,
                    intervals,
                    provisional=provisional,
                    **self._align_kwargs(),
                )
                segments = dedupe_overlapping_transcripts(
                    segments,
                    similarity_threshold=float(
                        self.tuning.get("dedupe_similarity", 0.45)
                    ),
                    min_time_overlap_ratio=float(
                        self.tuning.get("dedupe_time_overlap", 0.35)
                    ),
                )
            elif not provisional:
                # Final pass without pending STT: keep whatever is already stored.
                segments = [
                    TranscriptSegment(
                        id=s.id,
                        meeting_id=s.meeting_id,
                        speaker_id=s.speaker_id,
                        start_ms=s.start_ms,
                        end_ms=s.end_ms,
                        text=s.text,
                        provisional=False,
                        is_overlap=s.is_overlap,
                        overlap_speakers=list(s.overlap_speakers or []),
                        created_at=s.created_at,
                    )
                    for s in self.store.get_segments(self.meeting_id)
                    if (s.text or "").strip()
                ]
            else:
                segments = []

            if provisional:
                if segments:
                    self.store.replace_meeting_segments(self.meeting_id, segments)
                    await self._emit(
                        {
                            "type": "transcript",
                            "meeting_id": self.meeting_id,
                            "segments": [s.to_dict() for s in segments],
                        }
                    )
            else:
                review_input = segments

        if review_input is None:
            return

        if review_phase:
            await self._emit_phase(review_phase)
        try:
            reviewed = await self._finalize_review(review_input)
        except Exception as exc:
            logger.exception("Finalize review failed; keeping unpolished text: %s", exc)
            reviewed = review_input

        # Safety: never persist an empty wipe over a non-empty transcript.
        if not reviewed and review_input:
            logger.warning(
                "Finalize review returned 0 segments from %d; keeping pre-review text",
                len(review_input),
            )
            reviewed = review_input

        async with self._transcript_lock:
            self.store.replace_meeting_segments(self.meeting_id, reviewed)
            await self._emit(
                {
                    "type": "transcript",
                    "meeting_id": self.meeting_id,
                    "segments": [s.to_dict() for s in reviewed],
                }
            )

    async def _emit_phase(self, phase: str) -> None:
        await self._emit(
            {
                "type": "status",
                "status": "processing",
                "phase": phase,
                "meeting_id": self.meeting_id,
            }
        )

    async def stop(self) -> MeetingRecord:
        async with self._stop_lock:
            if self._stopping or not self._running:
                return self.record
            if self._cancel_requested:
                return await self._apply_cancel()
            self._stopping = True
            try:
                self.record.status = MeetingStatus.PROCESSING
                self.store.save_meeting(self.record)
                await self._emit(
                    {
                        "type": "status",
                        "status": "processing",
                        "meeting_id": self.meeting_id,
                    }
                )

                self._check_cancelled()
                await self._emit_phase("save_audio")
                if self._is_multi_stream and self._multi_ingest is not None:
                    try:
                        self.record.stream_paths = self._multi_ingest.save_stream_wavs()
                        mixed = self._multi_ingest.mix_down()
                        if len(mixed) > 0:
                            self.ingest.replace_buffer(mixed)
                            path = self.ingest.save_wav()
                            self.record.audio_path = path
                        elif self.record.stream_paths:
                            first_path = next(iter(self.record.stream_paths.values()))
                            self.record.audio_path = first_path
                        self.store.save_meeting(self.record)
                    except Exception as exc:
                        logger.warning("Could not save multi-stream WAV: %s", exc)
                else:
                    try:
                        path = self.ingest.save_wav()
                        self.record.audio_path = path
                        self.store.save_meeting(self.record)
                    except Exception as exc:
                        logger.warning("Could not save WAV: %s", exc)

                self._check_cancelled()
                await self._emit_phase("flush_stt")
                if self._is_multi_stream and self._multi_ingest and self._multi_chunkers:
                    buffers = {
                        sid: self._multi_ingest.get_buffer(sid)
                        for sid in self._multi_ingest.stream_ids
                    }
                    for sid, buf in buffers.items():
                        rem = self._multi_chunkers.flush(sid, buf)
                        if rem is not None:
                            self._record_stream_activity(
                                sid, rem.start_ms, rem.end_ms
                            )
                            await self._stt_queue.put(
                                _SttWorkItem(chunk=rem, speaker_id=sid)
                            )
                else:
                    rem = self.chunker.flush_remainder(self.ingest.get_buffer())
                    if rem is not None:
                        await self._stt_queue.put(_SttWorkItem(chunk=rem))

                n_workers = len(self._worker_tasks) or 1
                for _ in range(n_workers):
                    await self._stt_queue.put(None)
                if self._worker_tasks:
                    if self._cancel_requested:
                        await self._halt_workers()
                        return await self._apply_cancel()
                    await asyncio.gather(*self._worker_tasks)
                self._worker_tasks = []

                self._check_cancelled()
                if self._is_multi_stream:
                    await self._finalize_multi_stream_stop()
                else:
                    await self._emit_phase("diarize")
                    try:
                        await self._refresh_diarization(
                            provisional=False, review_phase="review"
                        )
                    except Exception as exc:
                        logger.exception(
                            "Final diarize/review failed during stop: %s", exc
                        )

                self._running = False
                self.record.status = MeetingStatus.STOPPED
                self.record.stopped_at = time.time()
                self.store.save_meeting(self.record)
                await self._emit(
                    {
                        "type": "status",
                        "status": "stopped",
                        "meeting_id": self.meeting_id,
                    }
                )
                return self.record
            except ProcessingCancelled:
                return await self._apply_cancel()
            except BaseException:
                # Cancellation mid-stop must not sticky-lock the session forever.
                if self._running:
                    self._stopping = False
                raise

    async def _finalize_multi_stream_stop(self) -> None:
        """Finalize transcript and speaker intervals without diarization."""
        await self._emit_phase("review")
        async with self._transcript_lock:
            segments = self._segments_from_stream_pending(provisional=False)
            await self._emit_stream_speaker_update(provisional=False)

        try:
            reviewed = await self._finalize_review(segments)
        except Exception as exc:
            logger.exception("Multi-stream finalize review failed: %s", exc)
            reviewed = segments

        if not reviewed and segments:
            reviewed = segments

        async with self._transcript_lock:
            self.store.replace_meeting_segments(self.meeting_id, reviewed)
            await self._emit(
                {
                    "type": "transcript",
                    "meeting_id": self.meeting_id,
                    "segments": [s.to_dict() for s in reviewed],
                }
            )

    async def _diarize_upload_audio(
        self, audio: np.ndarray
    ) -> List[SpeakerInterval]:
        """Diarize uploaded audio; fall back to one speaker so STT can still run."""
        await self._emit_phase("diarize")
        try:
            return await asyncio.to_thread(
                self.diarizer.diarize, audio, self.sample_rate
            )
        except RuntimeError as exc:
            logger.warning(
                "Upload diarization unavailable (%s); using single-speaker span for STT",
                exc,
            )
            await self._emit(
                {
                    "type": "warning",
                    "meeting_id": self.meeting_id,
                    "code": "diarization_unavailable",
                    "message": (
                        "سرویس تفکیک گوینده در دسترس نبود؛ پیاده‌سازی با یک گوینده "
                        "ادامه می‌یابد. برای برچسب‌گذاری دقیق‌تر، runtime diarize را "
                        "اجرا کنید (./runtime/scripts/start.sh)."
                    ),
                }
            )
            duration_ms = int(len(audio) * 1000 / self.sample_rate)
            if duration_ms <= 0:
                return []
            return [SpeakerInterval("SPEAKER_00", 0, duration_ms, False)]

    async def process_uploaded_file(self, path: str) -> List[TranscriptSegment]:
        """Offline path: load file → diarize → windowed STT → align → store."""
        self._pipeline_active = True
        self._cancel_requested = False
        try:
            self._active_tuning = dict(self.tuning)
            self.record.status = MeetingStatus.PROCESSING
            self.record.started_at = time.time()
            self.store.save_meeting(self.record)
            await self._emit(
                {"type": "status", "status": "processing", "meeting_id": self.meeting_id}
            )
            await self._emit_phase("save_audio")

            await asyncio.to_thread(self.ingest.load_from_file, path)
            self._check_cancelled()

            if self.upload_denoise_enabled:
                from api.audio.denoise import denoise_upload_audio

                await self._emit_phase("enhance_audio")
                denoise_result = await asyncio.to_thread(
                    denoise_upload_audio,
                    self.ingest.get_buffer(),
                    self.sample_rate,
                    enabled=True,
                )
                if denoise_result.applied:
                    self.ingest.replace_buffer(denoise_result.audio)

            if self.diarizer.backend not in {"pyannote", "nemo"}:
                await self._emit(
                    {
                        "type": "warning",
                        "meeting_id": self.meeting_id,
                        "code": "fallback_diarization",
                        "message": (
                            "Diarization backend is fallback (not pyannote/nemo). "
                            "Speaker labels may be unreliable on a single mic. "
                            "Start runtime diarize and set DIARIZATION_ENDPOINT."
                        ),
                    }
                )
            out_path = os.path.join("./data/audio", f"{self.meeting_id}.wav")
            try:
                await asyncio.to_thread(self.ingest.save_wav, out_path)
                self.record.audio_path = out_path
            except Exception:
                self.record.audio_path = path
            self.store.save_meeting(self.record)
            self._check_cancelled()

            audio = self.ingest.get_buffer()
            intervals = await self._diarize_upload_audio(audio)
            self._check_cancelled()
            self._speaker_intervals = intervals
            self.store.replace_speaker_intervals(self.meeting_id, intervals)
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

            self._check_cancelled()
            await self._emit_phase("flush_stt")
            stt_results: List[PendingStt] = []
            results_lock = asyncio.Lock()
            sem = asyncio.Semaphore(self._stt_workers())
            total = len(windows)
            done = 0

            async def _process_chunk(chunk) -> None:
                nonlocal done
                self._check_cancelled()
                async with sem:
                    self._check_cancelled()
                    lang = str(self.tuning.get("stt_language") or "fa")
                    ctx = None
                    async with results_lock:
                        if stt_results:
                            ctx = (stt_results[-1][2] or "").strip()[-240:] or None
                    result = await self._transcribe_with_retry(
                        chunk.audio, lang, prompt=ctx
                    )
                    self._check_cancelled()
                    if result is None:
                        async with results_lock:
                            done += 1
                            current = done
                        await self._emit(
                            {
                                "type": "status",
                                "status": "transcribing",
                                "progress": {
                                    "done": current,
                                    "total": total,
                                    "chunk": chunk.index,
                                    "start_ms": chunk.start_ms,
                                    "end_ms": chunk.end_ms,
                                },
                            }
                        )
                        return
                    raw = (result.text or "").strip()
                    text = await self._gate_stt_text(raw) if raw else None
                    words = self._words_abs(chunk.start_ms, result) if text else None
                    if words:
                        self._stt_with_timings += 1
                    async with results_lock:
                        if text:
                            stt_results.append(
                                (chunk.start_ms, chunk.end_ms, text, words)
                            )
                            self._chunks_processed += 1
                        done += 1
                        current = done
                    await self._emit(
                        {
                            "type": "status",
                            "status": "transcribing",
                            "progress": {
                                "done": current,
                                "total": total,
                                "chunk": chunk.index,
                                "start_ms": chunk.start_ms,
                                "end_ms": chunk.end_ms,
                            },
                        }
                    )

            if windows:
                await asyncio.gather(*[_process_chunk(c) for c in windows])
            self._check_cancelled()
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
                min_time_overlap_ratio=float(
                    self.tuning.get("dedupe_time_overlap", 0.35)
                ),
            )
            self._check_cancelled()
            await self._emit_phase("review")
            try:
                segments = await self._finalize_review(segments)
            except Exception as exc:
                logger.exception(
                    "Finalize review failed on upload; keeping unpolished text: %s", exc
                )
            self._check_cancelled()
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
            await self._emit(
                {"type": "status", "status": "stopped", "meeting_id": self.meeting_id}
            )
            return segments
        except ProcessingCancelled:
            await self._apply_cancel(extra_paths=[path])
            raise
        finally:
            self._pipeline_active = False


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
        self.session_kwargs["window_ms"] = int(tuning.get("window_ms", 15000))
        self.session_kwargs["hop_ms"] = int(tuning.get("hop_ms", 15000))
        self.session_kwargs["diarize_every_ms"] = int(
            tuning.get("diarize_every_ms", 0)
        )
        if hasattr(self.diarizer, "apply_tuning"):
            self.diarizer.apply_tuning(tuning)
        for session in self._sessions.values():
            session.apply_tuning(tuning)

    def _fork_diarizer(self) -> SpeakerDiarizer:
        if hasattr(self.diarizer, "fork"):
            child = self.diarizer.fork()
        else:
            child = SpeakerDiarizer(sample_rate=self.diarizer.sample_rate)
        if self.tuning and hasattr(child, "apply_tuning"):
            child.apply_tuning(self.tuning)
        return child

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
        user_id: Optional[str] = None,
        on_event: Optional[EventCallback] = None,
        *,
        capture_mode: CaptureMode = CaptureMode.MONO,
        streams: Optional[Dict[str, str]] = None,
    ) -> MeetingSession:
        record = MeetingRecord.create(
            title=title,
            participants=participants,
            user_id=user_id,
            capture_mode=capture_mode,
            streams=streams,
        )
        self.store.save_meeting(record)
        session = MeetingSession(
            record=record,
            store=self.store,
            stt_provider=self.stt,
            diarizer=self._fork_diarizer(),
            review_agent=self.review_agent,
            **self._session_args(on_event),
        )
        self._sessions[record.id] = session
        return session

    def get(self, meeting_id: str) -> Optional[MeetingSession]:
        return self._sessions.get(meeting_id)

    def heal_orphaned_recording(self, record: MeetingRecord) -> MeetingRecord:
        """Recover a meeting stuck at recording/processing with no live
        in-memory session (e.g. after a server restart or crash mid-flight).

        This must NOT discard already-captured work: segments, minutes, and
        insights computed before the interruption are legitimate and stay.
        Only the status/timestamps are healed so the meeting becomes usable
        again instead of being stuck (or silently wiped) forever.
        """
        if record.status not in (MeetingStatus.RECORDING, MeetingStatus.PROCESSING):
            return record
        live = self._sessions.get(record.id)
        if live is not None and (
            live._running or live._stopping or live._pipeline_active
        ):
            return record
        record.status = MeetingStatus.STOPPED
        if record.stopped_at is None:
            record.stopped_at = time.time()
        self.store.save_meeting(record)
        return record

    def _reset_cancelled_record(self, record: MeetingRecord) -> MeetingRecord:
        self.store.replace_meeting_segments(record.id, [])
        self.store.replace_speaker_intervals(record.id, [])
        self.store.delete_insights(record.id)
        self.store.delete_minutes(record.id)
        record.status = MeetingStatus.CREATED
        record.started_at = None
        record.stopped_at = None
        self.store.save_meeting(record)
        live = self._sessions.get(record.id)
        if live is not None:
            live.record.status = record.status
            live.record.started_at = record.started_at
            live.record.stopped_at = record.stopped_at
        return record

    async def cancel_meeting(
        self, meeting_id: str, extra_paths: Optional[Sequence[str]] = None
    ) -> Optional[MeetingRecord]:
        session = self._sessions.get(meeting_id)
        if session:
            return await session.cancel(extra_paths=extra_paths)
        record = self.store.get_meeting(meeting_id)
        if not record:
            return None
        if record.status in (MeetingStatus.RECORDING, MeetingStatus.PROCESSING):
            return self._reset_cancelled_record(record)
        return record

    async def delete_meeting(self, meeting_id: str, *, audio_dir: str) -> bool:
        """Delete a meeting and derived artifacts (segments, insights, minutes, audio)."""
        session = self._sessions.pop(meeting_id, None)
        if session is not None:
            try:
                await session.cancel()
            except Exception:
                logger.exception("Meeting cancel during delete failed: %s", meeting_id)

        record = self.store.get_meeting(meeting_id)
        if not record:
            return False

        deleted = self.store.delete_meeting(meeting_id)
        if not deleted:
            return False

        # Best-effort cleanup of the primary on-disk recordings.
        for path in (
            os.path.join(audio_dir, f"{meeting_id}.wav"),
            record.audio_path,
        ):
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        return True

    def get_or_restore(
        self, meeting_id: str, on_event: Optional[EventCallback] = None
    ) -> Optional[MeetingSession]:
        existing = self._sessions.get(meeting_id)
        if existing:
            if on_event:
                existing.on_event = on_event
            return existing
        record = self.store.get_meeting(meeting_id)
        if not record:
            return None
        if record.status in (MeetingStatus.RECORDING, MeetingStatus.PROCESSING):
            # In-memory capture is gone; status alone would block /start forever.
            self.heal_orphaned_recording(record)
        session = MeetingSession(
            record=record,
            store=self.store,
            stt_provider=self.stt,
            diarizer=self._fork_diarizer(),
            review_agent=self.review_agent,
            **self._session_args(on_event),
        )
        self._sessions[meeting_id] = session
        return session
