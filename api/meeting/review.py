"""Whisper STT quality gate + optional LLM review/enhance (Gemma-style).

Inspired by Whisper → Gemma enhancement pipelines: cheap heuristics catch
hallucinations (repetition loops, junk), then an LLM can polish accepted text.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Sequence

from .models import TranscriptSegment

logger = logging.getLogger(__name__)

ReviewAction = Literal["keep", "fix", "drop"]

_WORD_RE = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)
_PUNCT_ONLY = re.compile(r"^[\s\.\,\!\?\;\:\-\—\…\u06D4\u061F«»\"'()]+$")

# Whisper / ASR non-speech event tags → Persian labels
_NONSPEECH_FA: dict[str, str] = {
    "cough": "سرفه",
    "coughs": "سرفه",
    "coughing": "سرفه",
    "sigh": "آه",
    "sighs": "آه",
    "sighing": "آه",
    "laugh": "خنده",
    "laughs": "خنده",
    "laughter": "خنده",
    "laughing": "خنده",
    "chuckle": "خنده",
    "giggle": "خنده",
    "sneeze": "عطسه",
    "sneezing": "عطسه",
    "sniff": "فین",
    "sniffle": "فین",
    "clears throat": "صاف کردن گلو",
    "clearing throat": "صاف کردن گلو",
    "breath": "تنفس",
    "breathing": "تنفس",
    "inhale": "دم",
    "exhale": "بازدم",
    "silence": "سکوت",
    "pause": "مکث",
    "music": "موسیقی",
    "applause": "تشویق",
    "clapping": "تشویق",
    "inaudible": "نامفهوم",
    "unintelligible": "نامفهوم",
    "blank audio": "بی‌صدا",
    "blank_audio": "بی‌صدا",
    "noise": "سر و صدا",
    "static": "نویز",
    "hum": "زمزمه",
    "humming": "زمزمه",
    "whistle": "سوت",
    "whistling": "سوت",
    "cry": "گریه",
    "crying": "گریه",
    "sobbing": "گریه",
    "yawn": "خمیازه",
    "yawning": "خمیازه",
}
_NONSPEECH_RE = re.compile(r"[\(\[]\s*([a-zA-Z][a-zA-Z\s_]*)\s*[\)\]]")


def localize_nonspeech_events(text: str) -> str:
    """Translate English ASR event tags like (cough)/(Sigh) to Persian."""

    def _repl(match: re.Match[str]) -> str:
        raw = match.group(1).strip().lower()
        key = re.sub(r"[\s_]+", " ", raw).strip()
        fa = _NONSPEECH_FA.get(key) or _NONSPEECH_FA.get(key.replace(" ", "_"))
        return f"({fa})" if fa else match.group(0)

    return _NONSPEECH_RE.sub(_repl, text or "")


REVIEW_SYSTEM_PROMPT = """You are Distill's ASR review agent for Persian (and mixed) meeting transcripts.
You receive raw Whisper speech-to-text output. Your job:
1) DROP hallucinated / nonsense / pure repetition / noise-only text
2) FIX minor grammar, punctuation, and obvious ASR typos while keeping meaning
3) KEEP good text unchanged

Never invent meeting content that was not in the input.
Respond ONLY with valid JSON:
{"action":"keep"|"fix"|"drop","text":"final text or empty if drop","reason":"short reason"}
For drop, set text to "".
For keep, set text to the original (or lightly cleaned whitespace).
For fix, set text to the corrected Persian transcript.
"""

BATCH_REVIEW_SYSTEM_PROMPT = """You are Distill's ASR review agent for Persian meeting transcripts.
Review each Whisper segment. DROP hallucinations/repetitions/noise, FIX light ASR errors, or KEEP.
Never invent content. Respond ONLY with valid JSON:
{"items":[{"id":"seg-id","action":"keep"|"fix"|"drop","text":"...","reason":"..."}]}
Include every id. For drop use text "".
"""


@dataclass
class ReviewResult:
    action: ReviewAction
    text: str
    score: float
    reasons: List[str] = field(default_factory=list)
    raw_text: str = ""

    @property
    def accepted(self) -> bool:
        return self.action != "drop" and bool((self.text or "").strip())


def tokenize(text: str) -> List[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text or "")]


def _max_consecutive_run(tokens: Sequence[str]) -> int:
    if not tokens:
        return 0
    best = 1
    run = 1
    for i in range(1, len(tokens)):
        if tokens[i] == tokens[i - 1]:
            run += 1
            best = max(best, run)
        else:
            run = 1
    return best


def _unique_ratio(tokens: Sequence[str]) -> float:
    if not tokens:
        return 0.0
    return len(set(tokens)) / len(tokens)


def _collapse_consecutive(tokens: Sequence[str], keep: int = 1) -> List[str]:
    if not tokens:
        return []
    out: List[str] = []
    prev = tokens[0]
    count = 1
    out.append(prev)
    for tok in tokens[1:]:
        if tok == prev:
            count += 1
            if count <= keep:
                out.append(tok)
        else:
            prev = tok
            count = 1
            out.append(tok)
    return out


def _char_loop_score(text: str) -> float:
    """High score means bad: short n-gram looping (خی خی خی / خیلی خیلی)."""
    compact = re.sub(r"\s+", "", (text or "").strip())
    if len(compact) < 8:
        return 0.0
    for n in (2, 3, 4):
        if len(compact) < n * 4:
            continue
        gram = compact[:n]
        repeats = 0
        i = 0
        while i + n <= len(compact) and compact[i : i + n] == gram:
            repeats += 1
            i += n
        if repeats >= 5 and i >= len(compact) * 0.7:
            return min(1.0, repeats / 8.0)
    return 0.0


def score_stt_text(text: str) -> tuple[float, List[str]]:
    """Return quality score in [0,1] (higher=better) and reason codes."""
    t = (text or "").strip()
    reasons: List[str] = []
    if not t:
        return 0.0, ["empty"]
    if len(t) < 2:
        return 0.0, ["too_short"]
    if _PUNCT_ONLY.match(t):
        return 0.0, ["punct_only"]
    if not any(ch.isalpha() for ch in t):
        return 0.0, ["no_letters"]

    tokens = tokenize(t)
    if not tokens:
        return 0.05, ["no_tokens"]

    score = 1.0
    run = _max_consecutive_run(tokens)
    uniq = _unique_ratio(tokens)
    loop = _char_loop_score(t)

    if run >= 8:
        score -= 0.85
        reasons.append(f"word_run:{run}")
    elif run >= 5:
        score -= 0.55
        reasons.append(f"word_run:{run}")
    elif run >= 3:
        score -= 0.2
        reasons.append(f"word_run:{run}")

    if len(tokens) >= 6 and uniq < 0.25:
        score -= 0.55
        reasons.append(f"low_unique:{uniq:.2f}")
    elif len(tokens) >= 4 and uniq < 0.35:
        score -= 0.3
        reasons.append(f"low_unique:{uniq:.2f}")

    if loop >= 0.5:
        score -= 0.7
        reasons.append(f"char_loop:{loop:.2f}")
    elif loop >= 0.25:
        score -= 0.35
        reasons.append(f"char_loop:{loop:.2f}")

    # Very long with tiny vocabulary → classic Whisper hallucination
    if len(tokens) >= 12 and len(set(tokens)) <= 3:
        score -= 0.4
        reasons.append("tiny_vocab")

    return max(0.0, min(1.0, score)), reasons


def gate_stt_text(
    text: str,
    *,
    min_score: float = 0.35,
    collapse_runs: bool = True,
) -> ReviewResult:
    """Fast heuristic gate: drop / lightly collapse / keep. No LLM."""
    from api.meeting.aligner import _collapse_internal_repeats

    raw = (text or "").strip()
    score, reasons = score_stt_text(raw)
    if score < min_score:
        return ReviewResult(
            action="drop",
            text="",
            score=score,
            reasons=reasons or ["low_score"],
            raw_text=raw,
        )

    tokens = tokenize(raw)
    run = _max_consecutive_run(tokens)
    if collapse_runs and run >= 3:
        collapsed = _collapse_consecutive(tokens, keep=1)
        fixed = localize_nonspeech_events(
            _collapse_internal_repeats(" ".join(collapsed))
        )
        return ReviewResult(
            action="fix",
            text=fixed,
            score=score,
            reasons=reasons + ["collapsed_runs"],
            raw_text=raw,
        )

    cleaned = _collapse_internal_repeats(localize_nonspeech_events(raw))
    action: ReviewAction = "fix" if cleaned != raw else "keep"
    return ReviewResult(
        action=action,
        text=cleaned,
        score=score,
        reasons=reasons + (["collapsed_phrases"] if action == "fix" else []),
        raw_text=raw,
    )


def _parse_review_json(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


class TranscriptReviewAgent:
    """Optional Gemma/LLM polish on top of heuristic gating (reference ASR demos)."""

    def __init__(self, llm=None, enabled: bool = True):
        self.llm = llm
        self.enabled = enabled and llm is not None

    async def review_text(self, text: str, *, language: str = "fa") -> ReviewResult:
        heuristic = gate_stt_text(text)
        if heuristic.action == "drop":
            return heuristic
        if not self.enabled:
            return heuristic

        candidate = heuristic.text
        try:
            messages = [
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Language hint: {language}\n"
                        f"Raw Whisper text:\n{candidate}"
                    ),
                },
            ]
            raw = await self.llm.complete(messages, temperature=0.1, max_tokens=400)
            data = _parse_review_json(raw) or {}
            action = str(data.get("action") or "keep").lower()
            if action not in {"keep", "fix", "drop"}:
                action = "keep"
            out_text = (data.get("text") if action != "drop" else "") or ""
            out_text = str(out_text).strip()
            if action != "drop" and not out_text:
                out_text = candidate
                action = "keep"
            reason = str(data.get("reason") or "llm_review")
            return ReviewResult(
                action=action,  # type: ignore[arg-type]
                text=localize_nonspeech_events(out_text) if action != "drop" else "",
                score=heuristic.score,
                reasons=heuristic.reasons + [reason],
                raw_text=heuristic.raw_text or text,
            )
        except Exception as exc:
            logger.warning("LLM STT review failed, keeping heuristic text: %s", exc)
            return heuristic

    async def review_segments(
        self,
        segments: List[TranscriptSegment],
        *,
        language: str = "fa",
    ) -> List[TranscriptSegment]:
        """Finalize pass: heuristic gate each row, then optional batched LLM polish."""
        if not segments:
            return []

        # First pass: heuristics (always)
        gated: List[TranscriptSegment] = []
        for seg in segments:
            result = gate_stt_text(seg.text)
            if not result.accepted:
                logger.info(
                    "Dropped STT segment %s (%s): %s",
                    seg.id,
                    ",".join(result.reasons),
                    (seg.text or "")[:80],
                )
                continue
            gated.append(
                TranscriptSegment(
                    id=seg.id,
                    meeting_id=seg.meeting_id,
                    speaker_id=seg.speaker_id,
                    start_ms=seg.start_ms,
                    end_ms=seg.end_ms,
                    text=result.text,
                    provisional=seg.provisional,
                    is_overlap=seg.is_overlap,
                    overlap_speakers=list(seg.overlap_speakers or []),
                )
            )

        if not gated or not self.enabled:
            return gated

        # Batch LLM review for unique texts (overlap rows share text)
        unique_texts: dict[str, str] = {}
        for seg in gated:
            unique_texts.setdefault(seg.text, seg.text)

        items_payload = [
            {"id": f"t{i}", "text": text}
            for i, text in enumerate(unique_texts.keys())
        ]
        id_to_text = {item["id"]: item["text"] for item in items_payload}
        reviewed_map: dict[str, str] = {}

        try:
            messages = [
                {"role": "system", "content": BATCH_REVIEW_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Language hint: {language}\n"
                        f"Segments JSON:\n{json.dumps(items_payload, ensure_ascii=False)}"
                    ),
                },
            ]
            raw = await self.llm.complete(
                messages,
                temperature=0.1,
                max_tokens=min(4096, 200 + 120 * len(items_payload)),
            )
            data = _parse_review_json(raw) or {}
            items = data.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("id") or "")
                action = str(item.get("action") or "keep").lower()
                original = id_to_text.get(sid)
                if original is None:
                    continue
                if action == "drop":
                    reviewed_map[original] = ""
                else:
                    fixed = str(item.get("text") or original).strip()
                    reviewed_map[original] = localize_nonspeech_events(
                        fixed or original
                    )
        except Exception as exc:
            logger.warning("Batch LLM STT review failed: %s", exc)
            return gated

        out: List[TranscriptSegment] = []
        seen_overlap: set[tuple] = set()
        for seg in gated:
            new_text = reviewed_map.get(seg.text, seg.text)
            if not (new_text or "").strip():
                continue
            # Keep one overlap card per (time,text) after rewrite
            if seg.is_overlap:
                key = (seg.start_ms, seg.end_ms, new_text)
                # still emit both speakers; only skip exact duplicate of same speaker
                if (key + (seg.speaker_id,)) in seen_overlap:
                    continue
                seen_overlap.add(key + (seg.speaker_id,))
            out.append(
                TranscriptSegment(
                    id=seg.id,
                    meeting_id=seg.meeting_id,
                    speaker_id=seg.speaker_id,
                    start_ms=seg.start_ms,
                    end_ms=seg.end_ms,
                    text=new_text,
                    provisional=seg.provisional,
                    is_overlap=seg.is_overlap,
                    overlap_speakers=list(seg.overlap_speakers or []),
                )
            )
        return out
