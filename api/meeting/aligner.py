from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from .models import SpeakerInterval, TranscriptSegment


def _overlap_ms(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _speakers_at(
    t_ms: int,
    intervals: Sequence[SpeakerInterval],
) -> List[str]:
    """Active speaker ids at time t (half-open [start, end))."""
    found = []
    for iv in intervals:
        if iv.start_ms <= t_ms < iv.end_ms:
            if iv.speaker_id not in found:
                found.append(iv.speaker_id)
    return found


def _cut_points(
    start_ms: int,
    end_ms: int,
    intervals: Sequence[SpeakerInterval],
) -> List[int]:
    pts = {start_ms, end_ms}
    for iv in intervals:
        if start_ms < iv.start_ms < end_ms:
            pts.add(iv.start_ms)
        if start_ms < iv.end_ms < end_ms:
            pts.add(iv.end_ms)
    return sorted(pts)


def _atomic_regions(
    start_ms: int,
    end_ms: int,
    intervals: Sequence[SpeakerInterval],
) -> List[Tuple[int, int, List[str]]]:
    """Split [start,end) into atomic slices with the set of active speakers."""
    cuts = _cut_points(start_ms, end_ms, intervals)
    regions: List[Tuple[int, int, List[str]]] = []
    for i in range(len(cuts) - 1):
        a, b = cuts[i], cuts[i + 1]
        if b <= a:
            continue
        mid = (a + b) // 2
        speakers = _speakers_at(mid, intervals)
        if not speakers:
            continue
        regions.append((a, b, speakers))
    return regions


def dominant_speaker(
    start_ms: int,
    end_ms: int,
    intervals: Sequence[SpeakerInterval],
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
    has_overlap = any(len(spks) >= 2 for _, _, spks in _atomic_regions(start_ms, end_ms, intervals))
    return speaker, has_overlap


def merge_adjacent_segments(
    segments: Iterable[TranscriptSegment],
    max_gap_ms: int = 600,
) -> List[TranscriptSegment]:
    ordered = sorted(
        [s for s in segments if s.text.strip() or s.is_overlap],
        key=lambda s: (s.start_ms, s.end_ms, s.speaker_id),
    )
    if not ordered:
        return []

    merged: List[TranscriptSegment] = [ordered[0]]
    for seg in ordered[1:]:
        prev = merged[-1]
        same_speaker = prev.speaker_id == seg.speaker_id
        same_overlap_set = sorted(prev.overlap_speakers) == sorted(seg.overlap_speakers)
        close = seg.start_ms - prev.end_ms <= max_gap_ms
        # Never merge dual-speaker overlap rows into exclusive rows
        if (
            same_speaker
            and same_overlap_set
            and close
            and prev.is_overlap == seg.is_overlap
            and not prev.is_overlap
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
) -> List[TranscriptSegment]:
    """
    Map STT windows onto speaker timelines.

    - Exclusive talk → one segment for that speaker
    - True simultaneous talk → one segment per active speaker on the same
      time range, sharing the mixed STT text, all marked is_overlap
      (single-mic cannot separate words; both speakers still appear)
    """
    segments: List[TranscriptSegment] = []
    for start_ms, end_ms, text in stt_windows:
        text = (text or "").strip()
        if not text:
            continue

        regions = _atomic_regions(start_ms, end_ms, intervals)
        if not regions:
            segments.append(
                TranscriptSegment.create(
                    meeting_id=meeting_id,
                    speaker_id="SPEAKER_00",
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                    provisional=provisional,
                )
            )
            continue

        has_multi = any(len(spks) >= 2 for _, _, spks in regions)
        if not has_multi:
            speaker, _ = dominant_speaker(start_ms, end_ms, intervals)
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
            continue

        # Collapse contiguous multi-speaker slices into one overlap span,
        # then emit one row per speaker so both show up together.
        overlap_start = min(a for a, b, spks in regions if len(spks) >= 2)
        overlap_end = max(b for a, b, spks in regions if len(spks) >= 2)
        speakers: List[str] = []
        for a, b, spks in regions:
            if len(spks) < 2:
                continue
            for s in spks:
                if s not in speakers:
                    speakers.append(s)
        if len(speakers) < 2:
            speaker, _ = dominant_speaker(start_ms, end_ms, intervals)
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
            continue

        for spk in speakers:
            segments.append(
                TranscriptSegment.create(
                    meeting_id=meeting_id,
                    speaker_id=spk,
                    start_ms=overlap_start,
                    end_ms=overlap_end,
                    text=text,
                    is_overlap=True,
                    provisional=provisional,
                    overlap_speakers=speakers,
                )
            )

    return merge_adjacent_segments(segments)


def dedupe_overlapping_transcripts(
    segments: Sequence[TranscriptSegment],
    similarity_threshold: float = 0.55,
    min_time_overlap_ratio: float = 0.45,
) -> List[TranscriptSegment]:
    """Collapse hop-window duplicates; keep multi-speaker overlap rows intact."""
    if not segments:
        return []

    ordered = sorted(segments, key=lambda s: (s.start_ms, s.end_ms, s.speaker_id))
    kept: List[TranscriptSegment] = []
    for seg in ordered:
        if not kept:
            kept.append(seg)
            continue

        # Overlap rows: dedupe only exact same speaker+span against another overlap row
        if seg.is_overlap:
            twin = next(
                (
                    k
                    for k in kept
                    if k.is_overlap
                    and k.speaker_id == seg.speaker_id
                    and abs(k.start_ms - seg.start_ms) < 400
                    and abs(k.end_ms - seg.end_ms) < 400
                ),
                None,
            )
            if twin is not None:
                if len(seg.text) > len(twin.text):
                    twin.text = seg.text
                twin.overlap_speakers = sorted(
                    set(twin.overlap_speakers) | set(seg.overlap_speakers) | {seg.speaker_id}
                )
                continue
            kept.append(seg)
            continue

        prev = kept[-1]
        if prev.is_overlap:
            kept.append(seg)
            continue

        ov = _overlap_ms(prev.start_ms, prev.end_ms, seg.start_ms, seg.end_ms)
        shorter = max(1, min(prev.end_ms - prev.start_ms, seg.end_ms - seg.start_ms))
        time_dup = ov / shorter >= min_time_overlap_ratio
        text_dup = _text_similarity(prev.text, seg.text) >= similarity_threshold
        same_or_near_speaker = prev.speaker_id == seg.speaker_id or time_dup

        if same_or_near_speaker and (time_dup or text_dup):
            prefer_new = len(seg.text) >= len(prev.text)
            winner = seg if prefer_new else prev
            winner.start_ms = min(prev.start_ms, seg.start_ms)
            winner.end_ms = max(prev.end_ms, seg.end_ms)
            winner.is_overlap = False
            winner.provisional = prev.provisional and seg.provisional
            kept[-1] = winner
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
