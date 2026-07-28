from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from .models import SpeakerInterval, TranscriptSegment


def _overlap_ms(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def dominant_speaker(
    start_ms: int,
    end_ms: int,
    intervals: Sequence[SpeakerInterval],
) -> Tuple[str, bool]:
    """Pick the speaker with max overlap in [start_ms, end_ms]; detect multi-speaker overlap."""
    if not intervals:
        return "SPEAKER_00", False

    scores: dict[str, int] = {}
    active: List[SpeakerInterval] = []
    for iv in intervals:
        ov = _overlap_ms(start_ms, end_ms, iv.start_ms, iv.end_ms)
        if ov <= 0:
            continue
        active.append(iv)
        scores[iv.speaker_id] = scores.get(iv.speaker_id, 0) + ov

    if not scores:
        return "SPEAKER_00", False

    speaker = max(scores.items(), key=lambda x: x[1])[0]
    distinct = {iv.speaker_id for iv in active}
    overlap_flag = any(iv.is_overlap for iv in active) or len(distinct) > 1
    return speaker, overlap_flag


def merge_adjacent_segments(
    segments: Iterable[TranscriptSegment],
    max_gap_ms: int = 600,
) -> List[TranscriptSegment]:
    """Merge consecutive same-speaker segments with small gaps."""
    ordered = sorted(
        [s for s in segments if s.text.strip()],
        key=lambda s: (s.start_ms, s.end_ms),
    )
    if not ordered:
        return []

    merged: List[TranscriptSegment] = [ordered[0]]
    for seg in ordered[1:]:
        prev = merged[-1]
        same_speaker = prev.speaker_id == seg.speaker_id
        close = seg.start_ms - prev.end_ms <= max_gap_ms
        if same_speaker and close and not (prev.is_overlap or seg.is_overlap):
            prev.end_ms = max(prev.end_ms, seg.end_ms)
            prev.text = f"{prev.text} {seg.text}".strip()
            prev.provisional = prev.provisional or seg.provisional
        else:
            merged.append(seg)
    return merged


def align_stt_with_diarization(
    meeting_id: str,
    stt_windows: Sequence[Tuple[int, int, str]],
    intervals: Sequence[SpeakerInterval],
    provisional: bool = False,
) -> List[TranscriptSegment]:
    """
    Convert (start_ms, end_ms, text) STT windows into speaker-labeled segments.

    For overlapping regions, mark is_overlap=True and keep dominant speaker label.
    """
    segments: List[TranscriptSegment] = []
    for start_ms, end_ms, text in stt_windows:
        text = (text or "").strip()
        if not text:
            continue
        speaker_id, is_overlap = dominant_speaker(start_ms, end_ms, intervals)
        segments.append(
            TranscriptSegment.create(
                meeting_id=meeting_id,
                speaker_id=speaker_id,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                is_overlap=is_overlap,
                provisional=provisional,
            )
        )
    return merge_adjacent_segments(segments)


def dedupe_overlapping_transcripts(
    segments: Sequence[TranscriptSegment],
    similarity_threshold: float = 0.72,
) -> List[TranscriptSegment]:
    """
    Drop hop-window duplicates where text largely repeats the previous segment.
    """
    if not segments:
        return []

    kept: List[TranscriptSegment] = []
    for seg in sorted(segments, key=lambda s: (s.start_ms, s.end_ms)):
        if not kept:
            kept.append(seg)
            continue
        prev = kept[-1]
        if prev.speaker_id == seg.speaker_id and _text_similarity(prev.text, seg.text) >= similarity_threshold:
            # Prefer longer / later refined text
            if len(seg.text) > len(prev.text):
                kept[-1] = seg
            continue
        kept.append(seg)
    return kept


def _text_similarity(a: str, b: str) -> float:
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0
