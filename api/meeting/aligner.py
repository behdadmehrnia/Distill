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
    cannot invent overlap from abutting/nearly-abutting labels.
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
    """Merge only clear same-utterance hop twins or abutting turns.

    Dissimilar overlapping hops stay separate so a later partial window cannot
    rewrite / absorb an earlier complete sentence.
    """
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
        hop_overlap = ov > 0 and ov / shorter >= 0.5
        near_hop = same_spk and gap <= 1500 and gap >= -2000

        if same_spk and (hop_overlap or near_hop) and _same_utterance(prev.text, seg.text):
            prev.text = _collapse_internal_repeats(
                _pick_hop_text(prev.text, seg.text)
            )
            prev.start_ms = min(prev.start_ms, seg.start_ms)
            prev.end_ms = max(prev.end_ms, seg.end_ms)
            prev.provisional = prev.provisional or seg.provisional
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


def _text_quality(text: str) -> float:
    """Heuristic STT quality in [0,1]; lazy import avoids review↔aligner cycle."""
    from api.meeting.review import score_stt_text

    score, _ = score_stt_text(text or "", language="en")
    return float(score)


def _pick_hop_text(prev: str, new: str) -> str:
    """
    Choose / stitch transcripts among Whisper variants of the same utterance.
    Prefer higher quality over raw length so a later hop cannot wipe good text.
    """
    return _stitch_hop_texts(prev, new)


def _norm_token(tok: str) -> str:
    t = (tok or "").replace("\u200c", "").replace("\u200d", "")
    t = t.replace("ي", "ی").replace("ك", "ک")
    t = re.sub(r"[^\w\u0600-\u06FF]+", "", t, flags=re.UNICODE)
    return t.lower()


def _tokens_close(a: str, b: str) -> bool:
    if a == b:
        return True
    if not a or not b:
        return False
    if a in b or b in a:
        return abs(len(a) - len(b)) <= max(2, len(a) // 3)
    # cheap edit tolerance for short ASR variants
    if abs(len(a) - len(b)) > 2:
        return False
    mismatches = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
    return mismatches <= 2


def _best_suffix_prefix_overlap(ta: List[str], tb: List[str]) -> int:
    """Return k such that ta[-k:] ≈ tb[:k] (exact or fuzzy). 0 if none."""
    if not ta or not tb:
        return 0
    na = [_norm_token(t) for t in ta]
    nb = [_norm_token(t) for t in tb]
    max_k = min(len(na), len(nb), 16)
    best_k = 0
    best_score = 0.0
    for k in range(1, max_k + 1):
        suf, pref = na[-k:], nb[:k]
        if suf == pref:
            score = 1.0
        else:
            matches = sum(1 for x, y in zip(suf, pref) if _tokens_close(x, y))
            score = matches / k
        if score >= 0.6 and (k > best_k or (k == best_k and score > best_score)):
            # Prefer longer overlaps when score is decent
            if k >= 2 or score >= 0.99:
                best_k = k
                best_score = score
    return best_k if best_score >= 0.6 else 0


def _continuation_skip(ta: List[str], tb: List[str]) -> int:
    """
    How many leading tokens of tb restating the end of ta should be skipped
    before appending the new continuation.
    """
    if not ta or not tb:
        return 0
    na = [_norm_token(t) for t in ta]
    nb = [_norm_token(t) for t in tb]
    a_set = set(na)
    # Prefer matching near the end of A
    tail = set(na[max(0, len(na) - 14) :])
    best = 0
    for j in range(1, min(len(nb), 14) + 1):
        pref = nb[:j]
        pref_set = set(pref)
        hit_tail = len(pref_set & tail) / max(1, len(pref_set))
        hit_all = len(pref_set & a_set) / max(1, len(pref_set))
        if hit_tail >= 0.5 or hit_all >= 0.65:
            best = j
    return best


def _better_text(a: str, b: str) -> str:
    """Prefer clearly higher quality; on a tie keep established text (a)."""
    qa, qb = _text_quality(a), _text_quality(b)
    if qb > qa + 0.08:
        return b
    return a


def _stitch_hop_texts(prev: str, new: str) -> str:
    """
    Merge overlapping-window transcripts into continuous text.

    Keeps unique prefixes from earlier hops and unique suffixes from later hops,
    but refuses to overwrite a high-quality transcript with hop junk/hallucinations.
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
        # Modest completion of a partial hop is OK; a huge junk wrap is not.
        ta, tb = a.split(), b.split()
        if len(tb) <= len(ta) + max(4, len(ta) // 2):
            return _better_text(a, b) if _text_quality(b) + 0.08 < _text_quality(a) else b
        return _better_text(a, b)

    ta, tb = a.split(), b.split()
    qa, qb = _text_quality(a), _text_quality(b)
    sim = _text_similarity(a, b)
    cont = _token_containment(a, b)

    k = _best_suffix_prefix_overlap(ta, tb)
    if k >= 2 or (k == 1 and len(ta) <= 4):
        stitched = _collapse_internal_repeats(" ".join(ta + tb[k:]).strip())
        qs = _text_quality(stitched)
        # Stitch that tanks quality vs the established text → keep the better raw hop
        if qa >= 0.6 and qs + 0.12 < qa:
            return _better_text(a, b)
        return stitched

    # Same utterance variants: quality first, then fuller form
    if sim >= 0.38 or cont >= 0.55 or _same_utterance(a, b):
        if abs(qa - qb) >= 0.12:
            return a if qa > qb else b
        if cont >= 0.7:
            return a if len(ta) >= len(tb) else b
        skip = _continuation_skip(ta, tb)
        if skip > 0 and skip < len(tb):
            stitched = _collapse_internal_repeats(" ".join(ta + tb[skip:]).strip())
            if _text_quality(stitched) + 0.12 < max(qa, qb) and max(qa, qb) >= 0.6:
                return _better_text(a, b)
            return stitched
        return _better_text(a, b)

    # Dissimilar heavy-overlap hops: only suppress clear junk overwrites.
    # Real monologue continuations (different words, both decent) still stitch.
    if sim < 0.25 and cont < 0.35:
        if qa >= 0.65 and qb < 0.5:
            return a
        if qb >= 0.65 and qa < 0.5:
            return b
        if qa >= 0.7 and qb + 0.25 < qa:
            return a
        if qb >= 0.7 and qa + 0.25 < qb:
            return b

    skip = _continuation_skip(ta, tb)
    if skip > 0 and skip < len(tb):
        stitched = _collapse_internal_repeats(" ".join(ta + tb[skip:]).strip())
        if _text_quality(stitched) + 0.12 < max(qa, qb) and max(qa, qb) >= 0.6:
            return _better_text(a, b)
        return stitched
    if skip >= len(tb):
        return a

    # Blind concat is a last resort; refuse when new hop is clearly worse junk
    if qa >= 0.65 and qb < 0.5:
        return a
    return _collapse_internal_repeats(" ".join(ta + tb).strip())


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
    return _stitch_hop_texts(a, b)


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


def _merge_word_timings(
    left: Optional[Sequence[Tuple[str, int, int]]],
    right: Optional[Sequence[Tuple[str, int, int]]],
) -> Optional[List[Tuple[str, int, int]]]:
    """Union hop word timings; never let a later partial hop erase earlier words."""
    if not left and not right:
        return None
    if not left:
        return [(w, int(s), int(e)) for w, s, e in right or []]
    if not right:
        return [(w, int(s), int(e)) for w, s, e in left]

    merged = sorted(
        [(str(w), int(s), int(e)) for w, s, e in list(left) + list(right)],
        key=lambda item: (item[1], item[2], item[0]),
    )
    out: List[Tuple[str, int, int]] = []
    for word, start, end in merged:
        if end < start:
            start, end = end, start
        if not out:
            out.append((word, start, end))
            continue
        pw, ps, pe = out[-1]
        ov = _overlap_ms(ps, pe, start, end)
        shorter = max(1, min(pe - ps, end - start))
        same_tok = _norm_token(pw) == _norm_token(word)
        if ov / shorter >= 0.5 and same_tok:
            out[-1] = (pw, min(ps, start), max(pe, end))
            continue
        # Competing ASR spellings on the same instant — keep the earlier one
        if ov / shorter >= 0.7:
            continue
        out.append((word, start, end))
    return out


def _collapse_overlapping_stt_windows(
    stt_windows: Sequence[SttWindow],
) -> List[SttWindow]:
    """
    Append-only collapse: refine the same time span, otherwise keep windows apart.

    Heavy overlap (>=0.5 of shorter) updates one row with the fuller text.
    Mild/no overlap never stitches dissimilar speech into one span.
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
        # Find best heavy-overlap target among existing rows (usually last).
        best_idx = None
        best_ratio = 0.0
        for i, prev in enumerate(out):
            ov = _overlap_ms(prev[0], prev[1], item[0], item[1])
            shorter = max(1, min(prev[1] - prev[0], item[1] - item[0]))
            ratio = ov / shorter if shorter else 0.0
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = i

        if best_idx is not None and best_ratio >= 0.5:
            prev = out[best_idx]
            prev_text = str(prev[2])
            new_text = str(item[2])
            related = _same_utterance(prev_text, new_text) or _token_containment(
                prev_text, new_text
            ) >= 0.4
            if related and len(new_text.split()) >= len(prev_text.split()):
                chosen = new_text
            else:
                chosen = prev_text
            prev[0] = min(prev[0], item[0])
            prev[1] = max(prev[1], item[1])
            prev[2] = chosen
            prev[3] = _merge_word_timings(prev[3], item[3])
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
                word_text = " ".join(s.text for s in word_segs).strip()
                text_tokens = max(1, len(text.split()))
                word_tokens = len(word_text.split())
                # Partial later-hop word timings must not erase fuller window text.
                # Require words to cover most of the window text (not merely be
                # a subset of it — a filler word inside a full sentence would wrongly pass).
                if word_tokens >= max(1, int(text_tokens * 0.55)):
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
            # Whisper hop variants of one sentence (partial vs full)
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
    (partial vs full), not two different turns.
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
    # Keep this stricter than before: loose matching let junk hops "win"
    # and wipe a complete good transcript on the next overlapping window.
    return sim >= 0.40 or cont >= 0.55
