"""Lightweight WER / DER-style metrics for PoC evaluation harnesses."""

from __future__ import annotations

from typing import List, Sequence, Tuple

from api.meeting.models import SpeakerInterval


def _tokens(text: str) -> List[str]:
    return [t for t in (text or "").lower().split() if t]


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Classic Levenshtein WER over whitespace tokens. 0 = perfect."""
    ref = _tokens(reference)
    hyp = _tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    # DP edit distance
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i]
        for j, h in enumerate(hyp, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if r == h else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1] / len(ref)


def diarization_error_rate(
    reference: Sequence[SpeakerInterval],
    hypothesis: Sequence[SpeakerInterval],
    step_ms: int = 100,
) -> float:
    """
    Frame-level DER approximation (miss + false alarm + confusion) / scored time.

    Ignores collar; good enough for regression tests on synthetic timelines.
    """
    if not reference and not hypothesis:
        return 0.0
    end = 0
    for iv in list(reference) + list(hypothesis):
        end = max(end, iv.end_ms)
    if end <= 0:
        return 0.0

    def labels_at(t: int, intervals: Sequence[SpeakerInterval]) -> Tuple[str, ...]:
        found = []
        for iv in intervals:
            if iv.start_ms <= t < iv.end_ms:
                found.append(iv.speaker_id)
        return tuple(sorted(found))

    scored = 0
    errors = 0
    for t in range(0, end, max(1, step_ms)):
        ref = labels_at(t, reference)
        hyp = labels_at(t, hypothesis)
        if not ref and not hyp:
            continue
        scored += 1
        if not ref and hyp:
            errors += 1  # false alarm
        elif ref and not hyp:
            errors += 1  # miss
        elif ref != hyp:
            # Same count but different ids, or different multiplicity
            if set(ref) != set(hyp):
                errors += 1
    return errors / scored if scored else 0.0
