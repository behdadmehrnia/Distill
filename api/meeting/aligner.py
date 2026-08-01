from __future__ import annotations

import re
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Union

from .models import SpeakerInterval, TranscriptSegment

_JUNK_TEXT = re.compile(r"^[\s\.\,\!\?\;\:\-\—\…\u06D4\u061F]+$")

# (start_ms, end_ms, text) or (start_ms, end_ms, text, words)
# words: list of (word, abs_start_ms, abs_end_ms)
SttWindow = Union[
    Tuple[int, int, str],
    Tuple[int, int, str, Optional[Sequence[Tuple[str, int, int]]]],
]


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


def _unpack_window(
    window: SttWindow,
) -> Tuple[int, int, str, Optional[Sequence[Tuple[str, int, int]]]]:
    if len(window) >= 4:
        return int(window[0]), int(window[1]), str(window[2]), window[3]  # type: ignore[misc]
    return int(window[0]), int(window[1]), str(window[2]), None


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
    """Merge turn-taking neighbors. Coalesce Whisper hop variants of one sentence."""
    ordered = sorted(
        [s for s in segments if _is_meaningful_text(s.text)],
        key=lambda s: (s.start_ms, s.end_ms, s.speaker_id),
    )
    if not ordered:
        return []

    merged: List[TranscriptSegment] = [ordered[0]]
    for seg in ordered[1:]:
        prev = merged[-1]
        same_spk = (
            not prev.is_overlap
            and not seg.is_overlap
            and prev.speaker_id == seg.speaker_id
        )
        gap = seg.start_ms - prev.end_ms
        ov = _overlap_ms(prev.start_ms, prev.end_ms, seg.start_ms, seg.end_ms)
        shorter = max(1, min(prev.end_ms - prev.start_ms, seg.end_ms - seg.start_ms))
        hop_overlap = ov > 0 and ov / shorter >= 0.1
        # Also treat near-abutting hops of the same utterance (tiny gap after truncate)
        near_hop = same_spk and gap <= 1500 and gap >= -2000

        if same_spk and (hop_overlap or near_hop) and _same_utterance(prev.text, seg.text):
            prev.text = _collapse_internal_repeats(
                _pick_hop_text(prev.text, seg.text)
            )
            prev.start_ms = min(prev.start_ms, seg.start_ms)
            prev.end_ms = max(prev.end_ms, seg.end_ms)
            prev.provisional = prev.provisional or seg.provisional
            continue

        if same_spk and hop_overlap and not _same_utterance(prev.text, seg.text):
            # Truly different speech in overlapping windows: keep both
            prev.end_ms = min(prev.end_ms, max(prev.start_ms + 400, seg.start_ms))
            if prev.end_ms - prev.start_ms < 400:
                merged[-1] = seg
            else:
                seg.text = _collapse_internal_repeats(seg.text)
                merged.append(seg)
            continue

        if same_spk and gap <= max_gap_ms and ov == 0:
            if _same_utterance(prev.text, seg.text):
                prev.text = _collapse_internal_repeats(
                    _pick_hop_text(prev.text, seg.text)
                )
            else:
                prev.text = _collapse_internal_repeats(
                    _join_adjacent_texts(prev.text, seg.text)
                )
            prev.end_ms = max(prev.end_ms, seg.end_ms)
            prev.provisional = prev.provisional or seg.provisional
            continue

        seg.text = _collapse_internal_repeats(seg.text)
        merged.append(seg)
    return merged


def _pick_hop_text(prev: str, new: str) -> str:
    """
    Choose the best transcript among Whisper variants of the same utterance.
    Prefer longer/more complete; never concatenate full copies.
    """
    a = (prev or "").strip()
    b = (new or "").strip()
    if not a:
        return b
    if not b:
        return a
    if b in a:
        return a
    if a in b:
        return b

    ta, tb = a.split(), b.split()
    max_k = min(len(ta), len(tb))
    best_k = 0
    for k in range(1, max_k + 1):
        if ta[-k:] == tb[:k]:
            best_k = k
    if best_k >= 2:
        return _collapse_internal_repeats(" ".join(ta + tb[best_k:]).strip())

    # Prefer more complete (more tokens), then longer string
    if len(tb) != len(ta):
        return b if len(tb) > len(ta) else a
    return a if len(a) >= len(b) else b


def _join_adjacent_texts(prev: str, new: str) -> str:
    """Join non-overlapping adjacent turns; still avoid duplicating a shared edge."""
    a = (prev or "").strip()
    b = (new or "").strip()
    if not a:
        return b
    if not b:
        return a
    if b in a:
        return a
    if a in b:
        return b
    ta, tb = a.split(), b.split()
    best_k = 0
    for k in range(1, min(len(ta), len(tb)) + 1):
        if ta[-k:] == tb[:k]:
            best_k = k
    if best_k >= 2:
        return " ".join(ta + tb[best_k:]).strip()
    if _same_utterance(a, b):
        return _pick_hop_text(a, b)
    return f"{a} {b}".strip()


def _collapse_internal_repeats(text: str) -> str:
    """Remove consecutive duplicated phrases; do not delete later unique speech."""
    t = (text or "").strip()
    if not t:
        return t

    parts = [
        p.strip()
        for p in re.split(r"(?<=[\.\!\?\u06D4])\s+", t)
        if p and p.strip()
    ]
    if len(parts) >= 2:
        out = [parts[0]]
        for p in parts[1:]:
            if (
                _text_similarity(out[-1], p) >= 0.85
                or p in out[-1]
                or out[-1] in p
            ):
                if len(p) > len(out[-1]):
                    out[-1] = p
                continue
            out.append(p)
        t = " ".join(out)

    words = t.split()
    # Only collapse consecutive phrase runs — not distant intentional repeats
    words = _collapse_consecutive_phrase_runs(words)
    return " ".join(words).strip()


def _collapse_consecutive_phrase_runs(words: List[str]) -> List[str]:
    if len(words) < 4:
        return words
    n = len(words)
    if n >= 4 and n % 2 == 0:
        mid = n // 2
        if words[:mid] == words[mid:]:
            words = words[:mid]

    changed = True
    while changed and len(words) >= 4:
        changed = False
        for phrase_len in range(min(8, len(words) // 2), 1, -1):
            i = 0
            new_words: List[str] = []
            while i < len(words):
                j = i + phrase_len
                k = j + phrase_len
                if k <= len(words) and words[i:j] == words[j:k] and phrase_len >= 2:
                    new_words.extend(words[i:j])
                    i = k
                    changed = True
                else:
                    new_words.append(words[i])
                    i += 1
            words = new_words
            if changed:
                break
    return words


def _collapse_overlapping_stt_windows(
    stt_windows: Sequence[SttWindow],
) -> List[SttWindow]:
    """
    Pre-pass: only merge hop windows that restate the *same* utterance.

    Critical: do NOT chain-merge dissimilar hops into one span with the latest
    text — that erased earlier speech and looked like "summarization".
    """
    items: List[List[Any]] = []
    for window in stt_windows:
        start_ms, end_ms, text, words = _unpack_window(window)
        text = _collapse_internal_repeats(text)
        if not _is_meaningful_text(text):
            continue
        items.append([start_ms, end_ms, text, words])
    items.sort(key=lambda x: (x[0], -(x[1] - x[0])))

    out: List[List[Any]] = []
    for item in items:
        if not out:
            out.append(item)
            continue
        prev = out[-1]
        ov = _overlap_ms(prev[0], prev[1], item[0], item[1])
        shorter = max(1, min(prev[1] - prev[0], item[1] - item[0]))
        if ov / shorter >= 0.12 and _same_utterance(prev[2], item[2]):
            prev[1] = max(prev[1], item[1])
            prev[2] = _pick_hop_text(prev[2], item[2])
            if item[3] and (not prev[3] or len(item[2].split()) >= len(prev[2].split())):
                prev[3] = item[3]
        else:
            out.append(item)
    return [(int(a), int(b), str(t), w) for a, b, t, w in out]


def _segments_from_words(
    meeting_id: str,
    words: Sequence[Tuple[str, int, int]],
    intervals: Sequence[SpeakerInterval],
    provisional: bool,
    min_overlap_ms: int,
) -> List[TranscriptSegment]:
    """Split timed words onto speaker turns when diarization boundaries exist."""
    if not words:
        return []

    segments: List[TranscriptSegment] = []
    cur_words: List[str] = []
    cur_speaker: Optional[str] = None
    cur_start = words[0][1]
    cur_end = words[0][2]

    def flush() -> None:
        nonlocal cur_words, cur_speaker, cur_start, cur_end
        text = " ".join(cur_words).strip()
        if text and cur_speaker is not None and cur_end > cur_start:
            overlap_speakers = _significant_overlap_speakers(
                cur_start, cur_end, intervals, min_overlap_ms=min_overlap_ms
            )
            if overlap_speakers:
                for spk in overlap_speakers:
                    segments.append(
                        TranscriptSegment.create(
                            meeting_id=meeting_id,
                            speaker_id=spk,
                            start_ms=cur_start,
                            end_ms=cur_end,
                            text=text,
                            is_overlap=True,
                            provisional=provisional,
                            overlap_speakers=overlap_speakers,
                        )
                    )
            else:
                segments.append(
                    TranscriptSegment.create(
                        meeting_id=meeting_id,
                        speaker_id=cur_speaker,
                        start_ms=cur_start,
                        end_ms=cur_end,
                        text=text,
                        provisional=provisional,
                    )
                )
        cur_words = []

    for word, w_start, w_end in words:
        mid = (w_start + w_end) // 2
        speaker, _ = dominant_speaker(mid, mid + 1, intervals, min_overlap_ms=min_overlap_ms)
        if cur_speaker is None:
            cur_speaker = speaker
            cur_start = w_start
            cur_end = w_end
            cur_words = [word]
            continue
        if speaker != cur_speaker:
            flush()
            cur_speaker = speaker
            cur_start = w_start
            cur_end = w_end
            cur_words = [word]
        else:
            cur_words.append(word)
            cur_end = max(cur_end, w_end)
    flush()
    return segments


def align_stt_with_diarization(
    meeting_id: str,
    stt_windows: Sequence[SttWindow],
    intervals: Sequence[SpeakerInterval],
    provisional: bool = False,
    min_overlap_ms: int = 1200,
    dedupe_similarity: float = 0.45,
    dedupe_time_overlap: float = 0.35,
) -> List[TranscriptSegment]:
    """
    Map STT windows onto speaker timelines.

    When word/segment timings are present, split text at speaker changes.
    Otherwise: one segment per window with dominant speaker.
    Only when two speakers share time for >= min_overlap_ms: emit overlap rows.
    """
    segments: List[TranscriptSegment] = []
    for window in _collapse_overlapping_stt_windows(stt_windows):
        start_ms, end_ms, text, words = _unpack_window(window)
        text = (text or "").strip()
        if not _is_meaningful_text(text):
            continue

        if words:
            word_segs = _segments_from_words(
                meeting_id, words, intervals, provisional, min_overlap_ms
            )
            if word_segs:
                segments.extend(word_segs)
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

    # Dedupe hop windows FIRST, then merge only true adjacent turns.
    # (Old order concatenated overlapping windows → repeated phrases in UI.)
    deduped = dedupe_overlapping_transcripts(
        segments,
        similarity_threshold=dedupe_similarity,
        min_time_overlap_ratio=dedupe_time_overlap,
    )
    return merge_adjacent_segments(deduped)


def dedupe_overlapping_transcripts(
    segments: Sequence[TranscriptSegment],
    similarity_threshold: float = 0.45,
    min_time_overlap_ratio: float = 0.35,
) -> List[TranscriptSegment]:
    """Collapse hop-window duplicates into fewer readable rows."""
    if not segments:
        return []

    ordered = sorted(segments, key=lambda s: (s.start_ms, s.end_ms, s.speaker_id))
    kept: List[TranscriptSegment] = []
    for seg in ordered:
        seg.text = _collapse_internal_repeats(seg.text)
        if not _is_meaningful_text(seg.text):
            continue
        if not kept:
            kept.append(seg)
            continue

        twin_idx = None
        for i in range(len(kept) - 1, -1, -1):
            prev = kept[i]
            if seg.start_ms - prev.end_ms > 4000:
                break
            ov = _overlap_ms(prev.start_ms, prev.end_ms, seg.start_ms, seg.end_ms)
            shorter = max(1, min(prev.end_ms - prev.start_ms, seg.end_ms - seg.start_ms))
            time_dup = ov / shorter >= min_time_overlap_ratio
            same_spk = prev.speaker_id == seg.speaker_id
            both_overlap = prev.is_overlap and seg.is_overlap and same_spk
            # Whisper hop variants of one sentence (سامیز vs سامورایز, partial vs full)
            if not _same_utterance(prev.text, seg.text):
                continue
            if both_overlap and (time_dup or abs(prev.start_ms - seg.start_ms) < 2500):
                twin_idx = i
                break
            if (not prev.is_overlap) and (not seg.is_overlap) and same_spk and (
                time_dup or ov > 0 or seg.start_ms - prev.end_ms <= 1500
            ):
                twin_idx = i
                break

        if twin_idx is None:
            kept.append(seg)
            continue

        prev = kept[twin_idx]
        prev.text = _collapse_internal_repeats(_pick_hop_text(prev.text, seg.text))
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
    ta = set(_norm_tokens(a))
    tb = set(_norm_tokens(b))
    if not ta or not tb:
        return 1.0 if (a or "").strip() == (b or "").strip() and (a or "").strip() else 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _norm_tokens(text: str) -> List[str]:
    """Normalize Persian ASR quirks before comparing hop variants."""
    t = (text or "").replace("\u200c", "").replace("\u200d", "")
    t = t.replace("ي", "ی").replace("ك", "ک")
    t = re.sub(r"[^\w\u0600-\u06FF\s]+", " ", t, flags=re.UNICODE)
    return [w for w in t.split() if w]


def _token_containment(a: str, b: str) -> float:
    """Fraction of the shorter text's tokens found in the longer text."""
    ta = set(_norm_tokens(a))
    tb = set(_norm_tokens(b))
    if not ta or not tb:
        return 1.0 if (a or "").strip() == (b or "").strip() else 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _same_utterance(a: str, b: str) -> bool:
    """
    True when two Whisper outputs are variants of the same spoken sentence
    (partial vs full, سامیز vs سامورایز), not two different turns.
    """
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return False
    na, nb = " ".join(_norm_tokens(a)), " ".join(_norm_tokens(b))
    if na and nb and (na in nb or nb in na):
        return True
    sim = _text_similarity(a, b)
    cont = _token_containment(a, b)
    return sim >= 0.30 or cont >= 0.45
