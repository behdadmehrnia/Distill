from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Tuple

from .models import SpeakerInterval, TranscriptSegment

_JUNK_TEXT = re.compile(r"^[\s\.\,\!\?\;\:\-\—\…\u06D4\u061F]+$")


def _overlap_ms(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _is_meaningful_text(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 2:
        return False
    if _JUNK_TEXT.match(t):
        return False
    # Need at least one letter (Persian or Latin)
    return any(ch.isalpha() for ch in t)


def _speakers_at(t_ms: int, intervals: Sequence[SpeakerInterval]) -> List[str]:
    found: List[str] = []
    for iv in intervals:
        if iv.start_ms <= t_ms < iv.end_ms and iv.speaker_id not in found:
            found.append(iv.speaker_id)
    return found


def _atomic_regions(
    start_ms: int,
    end_ms: int,
    intervals: Sequence[SpeakerInterval],
) -> List[Tuple[int, int, List[str]]]:
    pts = {start_ms, end_ms}
    for iv in intervals:
        if start_ms < iv.start_ms < end_ms:
            pts.add(iv.start_ms)
        if start_ms < iv.end_ms < end_ms:
            pts.add(iv.end_ms)
    cuts = sorted(pts)
    regions: List[Tuple[int, int, List[str]]] = []
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        if b <= a:
            continue
        speakers = _speakers_at((a + b) // 2, intervals)
        if speakers:
            regions.append((a, b, speakers))
    return regions


def _significant_overlap_speakers(
    start_ms: int,
    end_ms: int,
    intervals: Sequence[SpeakerInterval],
    min_overlap_ms: int = 1200,
    min_region_ms: int = 250,
) -> List[str]:
    """
    Speakers that truly share time for at least min_overlap_ms inside the window.

    Turn-taking (A then B) does not count.
    Tiny frame-edge flickers (< min_region_ms) are ignored so mono-mic diarization
    cannot invent هم‌صحبتی from abutting/nearly-abutting labels.
    """
    regions = _atomic_regions(start_ms, end_ms, intervals)
    multi_ms = 0
    speakers: List[str] = []
    for a, b, spks in regions:
        if len(spks) < 2:
            continue
        dur = b - a
        if dur < min_region_ms:
            continue
        multi_ms += dur
        for s in spks:
            if s not in speakers:
                speakers.append(s)
    if multi_ms < min_overlap_ms or len(speakers) < 2:
        return []
    return speakers


def dominant_speaker(
    start_ms: int,
    end_ms: int,
    intervals: Sequence[SpeakerInterval],
    min_overlap_ms: int = 1200,
) -> Tuple[str, bool]:
    if not intervals:
        return "SPEAKER_00", False

    scores: dict[str, int] = {}
    for iv in intervals:
        ov = _overlap_ms(start_ms, end_ms, iv.start_ms, iv.end_ms)
        if ov <= 0:
            continue
        scores[iv.speaker_id] = scores.get(iv.speaker_id, 0) + ov

    if not scores:
        return "SPEAKER_00", False

    speaker = max(scores.items(), key=lambda x: x[1])[0]
    overlap = bool(
        _significant_overlap_speakers(
            start_ms, end_ms, intervals, min_overlap_ms=min_overlap_ms
        )
    )
    return speaker, overlap


def merge_adjacent_segments(
    segments: Iterable[TranscriptSegment],
    max_gap_ms: int = 800,
) -> List[TranscriptSegment]:
    ordered = sorted(
        [s for s in segments if _is_meaningful_text(s.text)],
        key=lambda s: (s.start_ms, s.end_ms, s.speaker_id),
    )
    if not ordered:
        return []

    merged: List[TranscriptSegment] = [ordered[0]]
    for seg in ordered[1:]:
        prev = merged[-1]
        if (
            not prev.is_overlap
            and not seg.is_overlap
            and prev.speaker_id == seg.speaker_id
            and seg.start_ms - prev.end_ms <= max_gap_ms
        ):
            prev.end_ms = max(prev.end_ms, seg.end_ms)
            if seg.text and seg.text not in prev.text:
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
    min_overlap_ms: int = 1200,
    dedupe_similarity: float = 0.45,
    dedupe_time_overlap: float = 0.35,
) -> List[TranscriptSegment]:
    """
    Map STT windows onto speaker timelines.

    Default: one segment per window with dominant speaker.
    Only when two speakers share time for >= min_overlap_ms: emit overlap rows.
    """
    segments: List[TranscriptSegment] = []
    for start_ms, end_ms, text in stt_windows:
        text = (text or "").strip()
        if not _is_meaningful_text(text):
            continue

        overlap_speakers = _significant_overlap_speakers(
            start_ms, end_ms, intervals, min_overlap_ms=min_overlap_ms
        )
        if overlap_speakers:
            regions = _atomic_regions(start_ms, end_ms, intervals)
            ov_regions = [(a, b) for a, b, spks in regions if len(spks) >= 2]
            ov_start = min(a for a, _ in ov_regions)
            ov_end = max(b for _, b in ov_regions)
            for spk in overlap_speakers:
                segments.append(
                    TranscriptSegment.create(
                        meeting_id=meeting_id,
                        speaker_id=spk,
                        start_ms=ov_start,
                        end_ms=ov_end,
                        text=text,
                        is_overlap=True,
                        provisional=provisional,
                        overlap_speakers=overlap_speakers,
                    )
                )
            continue

        speaker, _ = dominant_speaker(
            start_ms, end_ms, intervals, min_overlap_ms=min_overlap_ms
        )
        segments.append(
            TranscriptSegment.create(
                meeting_id=meeting_id,
                speaker_id=speaker,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                provisional=provisional,
            )
        )

    return dedupe_overlapping_transcripts(
        merge_adjacent_segments(segments),
        similarity_threshold=dedupe_similarity,
        min_time_overlap_ratio=dedupe_time_overlap,
    )


def dedupe_overlapping_transcripts(
    segments: Sequence[TranscriptSegment],
    similarity_threshold: float = 0.45,
    min_time_overlap_ratio: float = 0.35,
) -> List[TranscriptSegment]:
    """Collapse hop-window duplicates into fewer readable rows."""
    if not segments:
        return []

    ordered = sorted(segments, key=lambda s: (s.start_ms, s.end_ms, s.speaker_id))
    # First collapse overlap-groups by (speaker, approximate span)
    kept: List[TranscriptSegment] = []
    for seg in ordered:
        if not _is_meaningful_text(seg.text):
            continue
        if not kept:
            kept.append(seg)
            continue

        # Find a prior segment that is a hop-duplicate of this one
        twin_idx = None
        for i in range(len(kept) - 1, -1, -1):
            prev = kept[i]
            # Only compare within a nearby time neighborhood
            if seg.start_ms - prev.end_ms > 4000:
                break
            ov = _overlap_ms(prev.start_ms, prev.end_ms, seg.start_ms, seg.end_ms)
            shorter = max(1, min(prev.end_ms - prev.start_ms, seg.end_ms - seg.start_ms))
            time_dup = ov / shorter >= min_time_overlap_ratio
            text_dup = _text_similarity(prev.text, seg.text) >= similarity_threshold
            same_spk = prev.speaker_id == seg.speaker_id
            both_overlap = prev.is_overlap and seg.is_overlap and same_spk
            if both_overlap and (time_dup or text_dup or abs(prev.start_ms - seg.start_ms) < 2500):
                twin_idx = i
                break
            if (not prev.is_overlap) and (not seg.is_overlap) and same_spk and (time_dup or text_dup):
                twin_idx = i
                break

        if twin_idx is None:
            kept.append(seg)
            continue

        prev = kept[twin_idx]
        prefer_new = len(seg.text) > len(prev.text) and _is_meaningful_text(seg.text)
        if prefer_new:
            prev.text = seg.text
        prev.start_ms = min(prev.start_ms, seg.start_ms)
        prev.end_ms = max(prev.end_ms, seg.end_ms)
        prev.is_overlap = prev.is_overlap or seg.is_overlap
        prev.provisional = prev.provisional and seg.provisional
        if seg.overlap_speakers:
            prev.overlap_speakers = sorted(
                set(prev.overlap_speakers) | set(seg.overlap_speakers)
            )

    return kept


def _text_similarity(a: str, b: str) -> float:
    ta = set(a.split())
    tb = set(b.split())
    if not ta or not tb:
        # punctuation-only already filtered; short strings
        return 1.0 if a.strip() == b.strip() and a.strip() else 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0
