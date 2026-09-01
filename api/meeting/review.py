"""Whisper STT quality gate + optional LLM review/enhance (Gemma-style).

Inspired by Whisper → Gemma enhancement pipelines: cheap heuristics catch
hallucinations (repetition loops, junk), then an LLM can polish accepted text.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence

from .models import TranscriptSegment

logger = logging.getLogger(__name__)

# Keep each polish LLM call small — large prompts + huge max_tokens make
# remote Gemma-class models crawl and often time out.
DEFAULT_REVIEW_MAX_BATCH_CHARS = 10_000
DEFAULT_REVIEW_BATCH_TIMEOUT_S = 18.0
DEFAULT_REVIEW_TOTAL_BUDGET_S = 28.0
DEFAULT_REVIEW_MAX_BATCHES = 2
DEFAULT_REVIEW_MAX_ITEMS = 24
DEFAULT_REVIEW_MAX_TOKENS = 768


def review_max_batch_chars() -> int:
    raw = os.getenv("REVIEW_MAX_BATCH_CHARS", "").strip()
    if not raw:
        return DEFAULT_REVIEW_MAX_BATCH_CHARS
    try:
        return max(2_000, int(raw))
    except ValueError:
        return DEFAULT_REVIEW_MAX_BATCH_CHARS


def review_batch_timeout_s() -> float:
    raw = os.getenv("REVIEW_BATCH_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_REVIEW_BATCH_TIMEOUT_S
    try:
        return max(8.0, float(raw))
    except ValueError:
        return DEFAULT_REVIEW_BATCH_TIMEOUT_S


def review_total_budget_s() -> float:
    raw = os.getenv("REVIEW_TOTAL_BUDGET_S", "").strip()
    if not raw:
        return DEFAULT_REVIEW_TOTAL_BUDGET_S
    try:
        return max(12.0, float(raw))
    except ValueError:
        return DEFAULT_REVIEW_TOTAL_BUDGET_S


def review_max_batches() -> int:
    raw = os.getenv("REVIEW_MAX_BATCHES", "").strip()
    if not raw:
        return DEFAULT_REVIEW_MAX_BATCHES
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_REVIEW_MAX_BATCHES


def review_max_items() -> int:
    raw = os.getenv("REVIEW_MAX_ITEMS", "").strip()
    if not raw:
        return DEFAULT_REVIEW_MAX_ITEMS
    try:
        return max(4, int(raw))
    except ValueError:
        return DEFAULT_REVIEW_MAX_ITEMS


def review_max_tokens() -> int:
    raw = os.getenv("REVIEW_MAX_TOKENS", "").strip()
    if not raw:
        return DEFAULT_REVIEW_MAX_TOKENS
    try:
        return max(256, int(raw))
    except ValueError:
        return DEFAULT_REVIEW_MAX_TOKENS


def needs_llm_polish(score: float, reasons: Sequence[str]) -> bool:
    """Only spend LLM budget on segments heuristics already flagged as shaky."""
    if score < 0.92:
        return True
    markers = (
        "word_run",
        "latin",
        "char_loop",
        "low_unique",
        "wrong_script",
        "collapsed",
        "tiny_vocab",
        "no_persian",
        "whisper_boilerplate",
    )
    for reason in reasons or ():
        r = str(reason)
        if any(m in r for m in markers):
            return True
    return False


def polish_max_tokens_for_batch(batch: Sequence[Dict[str, str]]) -> int:
    """Cap generation size: polish should echo input, not write essays."""
    approx_chars = sum(len(item.get("text") or "") for item in batch)
    # Rough upper bound on JSON echo + small reasons.
    estimate = approx_chars // 2 + 48 * len(batch) + 64
    return max(256, min(review_max_tokens(), estimate))


def chunk_review_items(
    items: List[Dict[str, str]], max_chars: int
) -> List[List[Dict[str, str]]]:
    """Split polish payload items so each batch's text stays within max_chars."""
    if not items:
        return []
    if max_chars <= 0:
        return [items]

    batches: List[List[Dict[str, str]]] = []
    current: List[Dict[str, str]] = []
    size = 0
    for item in items:
        text_len = len(item.get("text") or "")
        # JSON key/overhead fudge so the prompt stays under budget.
        piece = text_len + 48
        if current and size + piece > max_chars:
            batches.append(current)
            current = []
            size = 0
        if text_len > max_chars and not current:
            # Single oversized item: still send alone (better than dropping).
            batches.append([item])
            continue
        current.append(item)
        size += piece
    if current:
        batches.append(current)
    return batches


def select_polish_items(
    texts_with_meta: List[tuple[str, float, Sequence[str]]],
    *,
    max_items: Optional[int] = None,
) -> List[str]:
    """Pick unique texts that need polish, worst score first, capped."""
    limit = review_max_items() if max_items is None else max_items
    candidates: List[tuple[float, str]] = []
    seen: set[str] = set()
    for text, score, reasons in texts_with_meta:
        cleaned = (text or "").strip()
        if not cleaned or cleaned in seen:
            continue
        if not needs_llm_polish(score, reasons):
            continue
        seen.add(cleaned)
        candidates.append((score, cleaned))
    candidates.sort(key=lambda x: x[0])  # worst first
    return [text for _, text in candidates[: max(1, limit)]] if candidates else []

ReviewAction = Literal["keep", "fix", "drop"]

_WORD_RE = re.compile(r"[\w\u0600-\u06FF]+", re.UNICODE)
_PUNCT_ONLY = re.compile(r"^[\s\.\,\!\?\;\:\-\—\…\u06D4\u061F«»\"'()]+$")
# Arabic/Persian letters (incl. common Arabic variants Whisper may emit)
_PERSIAN_LETTERS = set("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیئآأإؤةىيۀك‌")
_LATIN_LETTERS = set("abcdefghijklmnopqrstuvwxyz")
_PERSIAN_LANGS = frozenset({"fa", "fas", "per", "persian", "farsi"})

# Whisper / ASR non-speech event tags → canonical English labels
_NONSPEECH_LABELS: dict[str, str] = {
    "coughs": "cough",
    "coughing": "cough",
    "sighs": "sigh",
    "sighing": "sigh",
    "laughs": "laughter",
    "laughing": "laughter",
    "laugh": "laughter",
    "chuckle": "laughter",
    "giggle": "laughter",
    "sneezing": "sneeze",
    "sniffle": "sniff",
    "clearing throat": "clears throat",
    "breathing": "breath",
    "humming": "hum",
    "whistling": "whistle",
    "crying": "cry",
    "sobbing": "cry",
    "yawning": "yawn",
    "unintelligible": "inaudible",
    "blank_audio": "blank audio",
    "music playing": "music",
    "background music": "music",
}

# Bare Whisper hallucinations that wipe real speech on the next hop
_WHISPER_BOILERPLATE = frozenset(
    {
        "music",
        "music playing",
        "background music",
        "silence",
        "pause",
        "applause",
        "clapping",
        "subtitles",
        "subtitle",
        "thanks for watching",
        "thank you for watching",
        "subscribe",
        "like and subscribe",
        "inaudible",
        "unintelligible",
        "blank audio",
        "noise",
        "static",
        *_NONSPEECH_LABELS.values(),
    }
)
_NONSPEECH_RE = re.compile(r"[\(\[]\s*([a-zA-Z][a-zA-Z\s_]*)\s*[\)\]]")
_EVENT_TAG_RE = re.compile(r"[\(\[【][^\)\]】]*[\)\]】]")
_BOILERPLATE_STRIP_RE = re.compile(r"[\(\)\[\]【】♪♫\.\،\,\s_\-]+")

# Phrases from a "system instruction" prompt that ASR sometimes transcribes
# verbatim instead of the speech. Empty while DEFAULT_STT_PROMPT is blank —
# add needles here if a prompt is reintroduced.
_STT_PROMPT_ECHO_NEEDLES: tuple[str, ...] = ()


def localize_nonspeech_events(text: str) -> str:
    """Normalize ASR event tags like (Coughs)/(Sigh) to a canonical form."""

    def _repl(match: re.Match[str]) -> str:
        raw = match.group(1).strip().lower()
        key = re.sub(r"[\s_]+", " ", raw).strip()
        label = _NONSPEECH_LABELS.get(key) or _NONSPEECH_LABELS.get(
            key.replace(" ", "_")
        )
        return f"({label})" if label else match.group(0)

    return _NONSPEECH_RE.sub(_repl, text or "")


def _boilerplate_key(text: str) -> str:
    t = (text or "").strip().lower().replace("\u200c", "").replace("\u200d", "")
    t = _BOILERPLATE_STRIP_RE.sub(" ", t)
    return " ".join(t.split())


def is_stt_prompt_echo(text: str) -> bool:
    """True when ASR repeated the old instruction prompt instead of speech."""
    raw = (text or "").strip()
    if not raw:
        return False
    key = _boilerplate_key(raw)
    for needle in _STT_PROMPT_ECHO_NEEDLES:
        nkey = _boilerplate_key(needle)
        if nkey and (nkey in key or key in nkey):
            return True
    return False


def is_whisper_boilerplate(text: str) -> bool:
    """
    True for Whisper junk that is only a non-speech label / YouTube boilerplate.

    These often arrive on a later hop and wipe a complete good transcript
    (classic: a good sentence → suddenly just "music").
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if is_stt_prompt_echo(raw):
        return True
    key = _boilerplate_key(raw)
    if key in _WHISPER_BOILERPLATE:
        return True

    # "(cough) ." / "[Music]" / "♪ music ♪" after normalization
    stripped = _NONSPEECH_RE.sub(" ", raw)
    stripped = _EVENT_TAG_RE.sub(" ", stripped)
    tokens = tokenize(stripped)
    if not tokens:
        return True
    if len(tokens) <= 3 and all(
        _boilerplate_key(tok) in _WHISPER_BOILERPLATE for tok in tokens
    ):
        return True
    return False


REVIEW_SYSTEM_PROMPT = """You are Distill's ASR cleanup agent for meeting transcripts.
You receive raw speech-to-text output. Choose ONE action:
1) drop — hallucinated / nonsense / wrong-language / pure repetition / noise-only
2) fix — ONLY tiny ASR typos, punctuation, or collapsing obvious word loops
3) keep — text is already fine (preferred)

STRICT RULES:
- Do NOT summarize, paraphrase, condense, shorten, or rewrite for style.
- Do NOT invent meeting content that was not in the input.
- Do NOT guess/replace words with unrelated meanings.
- Fixed text must stay close to the original wording and length (almost the same words).
- Prefer keep over fix.

Respond ONLY with valid JSON:
{"action":"keep"|"fix"|"drop","text":"final text or empty if drop","reason":"short reason"}
For drop, set text to "".
For keep, set text to the original (or lightly cleaned whitespace).
For fix, set text to a near-literal correction of the original — not a summary.
"""

BATCH_REVIEW_SYSTEM_PROMPT = """You are Distill's ASR cleanup agent for Persian meeting transcripts.
For each Whisper segment, choose ONE action:
- drop: hallucinated / nonsense / wrong-language / pure repetition / noise-only
- fix: ONLY tiny ASR typos, punctuation, or collapsing obvious word loops
- keep: text is already fine (preferred)

STRICT RULES:
- Do NOT summarize, paraphrase, condense, shorten, merge, or rewrite for style.
- Do NOT invent content. Keep nearly all original words and length.
- Do NOT guess/replace words with unrelated meanings.
- Prefer keep over fix. For keep, copy the original text unchanged.

Respond ONLY with valid JSON:
{"items":[{"id":"seg-id","action":"keep"|"fix"|"drop","text":"...","reason":"short reason"}]}
Include every id. For drop use text "".
"""

_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "polish_agent.md"
_PROMPT_CACHE: dict[str, Any] = {"mtime": None, "single": None, "batch": None}


def _extract_prompt_section(md: str, name: str) -> str:
    begin = f"<!-- BEGIN:{name} -->"
    end = f"<!-- END:{name} -->"
    start = md.find(begin)
    stop = md.find(end)
    if start < 0 or stop < 0 or stop <= start:
        return ""
    return md[start + len(begin) : stop].strip()


def _load_polish_prompts() -> tuple[str, str]:
    """Load single/batch prompts from polish_agent.md (hot-reload on mtime change)."""
    single = REVIEW_SYSTEM_PROMPT
    batch = BATCH_REVIEW_SYSTEM_PROMPT
    try:
        mtime = _PROMPT_FILE.stat().st_mtime
    except OSError:
        return single, batch

    if _PROMPT_CACHE["mtime"] == mtime and _PROMPT_CACHE["single"] and _PROMPT_CACHE["batch"]:
        return str(_PROMPT_CACHE["single"]), str(_PROMPT_CACHE["batch"])

    try:
        md = _PROMPT_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read polish prompt file %s: %s", _PROMPT_FILE, exc)
        return single, batch

    loaded_single = _extract_prompt_section(md, "single") or single
    loaded_batch = _extract_prompt_section(md, "batch") or batch
    _PROMPT_CACHE["mtime"] = mtime
    _PROMPT_CACHE["single"] = loaded_single
    _PROMPT_CACHE["batch"] = loaded_batch
    return loaded_single, loaded_batch


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


def _is_faithful_edit(original: str, edited: str) -> bool:
    """Reject LLM rewrites that summarize or replace the utterance."""
    orig = (original or "").strip()
    edit = (edited or "").strip()
    if not edit:
        return False
    if edit == orig:
        return True

    ot = tokenize(orig)
    et = tokenize(edit)
    if not ot:
        return True

    # Aggressive shortening → summarization
    if len(et) < max(3, int(len(ot) * 0.65)):
        return False
    if len(edit) < max(8, int(len(orig) * 0.55)):
        return False

    # Enough original tokens must survive (typo fixes still pass)
    et_set = set(et)
    retained = sum(1 for t in ot if t in et_set)
    if retained / len(ot) < 0.65:
        return False

    # Polish must not collapse quality into gibberish
    qo, _ = score_stt_text(orig)
    qe, _ = score_stt_text(edit)
    if qo >= 0.7 and qe + 0.2 < qo:
        return False
    return True


# A segment the cheap heuristic already scored this clean is unlikely to be a
# genuine hallucination — don't let one small-model "drop" verdict erase real
# speech the heuristic gate found no fault with. Below this ceiling the LLM's
# own judgment (usually backed by heuristic reasons too) is trusted as before.
_LLM_DROP_SCORE_CEILING = 0.6


def _apply_llm_edit(
    original: str, action: str, edited: str, *, original_score: float = 0.0
) -> tuple[str, str]:
    """
    Apply an LLM keep/fix/drop with a faithfulness guard.
    Returns (text, effective_action). Empty text means drop.
    """
    action = (action or "keep").lower()
    if action not in {"keep", "fix", "drop"}:
        action = "keep"
    if action == "drop":
        if original_score >= _LLM_DROP_SCORE_CEILING:
            logger.info(
                "Rejected LLM drop of heuristically-clean text (score=%.2f): %r",
                original_score,
                (original or "")[:80],
            )
            action = "keep"
        else:
            return "", "drop"

    candidate = str(edited or "").strip() if action == "fix" else str(edited or original).strip()
    if action == "keep":
        # Model sometimes "keeps" but returns a rewritten/summarized string
        if candidate and _is_faithful_edit(original, candidate):
            return localize_nonspeech_events(candidate), "keep"
        return localize_nonspeech_events(original), "keep"

    # fix
    if not candidate:
        return localize_nonspeech_events(original), "keep"
    if not _is_faithful_edit(original, candidate):
        logger.info(
            "Rejected unfaithful LLM polish (%d→%d chars): %r → %r",
            len(original),
            len(candidate),
            original[:80],
            candidate[:80],
        )
        return localize_nonspeech_events(original), "keep"
    return localize_nonspeech_events(candidate), "fix"


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
    """High score means bad: short n-gram looping ("uh uh uh" / "very very")."""
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


def _script_letter_counts(text: str) -> tuple[int, int, int]:
    """Return (persian, latin, other_letters) over alphabetic chars."""
    persian = latin = other = 0
    for ch in text or "":
        if ch in _PERSIAN_LETTERS:
            persian += 1
        elif ch.lower() in _LATIN_LETTERS:
            latin += 1
        elif ch.isalpha():
            other += 1
    return persian, latin, other


def _persian_script_penalty(text: str, language: str) -> tuple[float, List[str]]:
    """Penalize Latin / non-Persian hallucinations when STT language is Persian."""
    lang = (language or "en").strip().lower()
    if lang not in _PERSIAN_LANGS:
        return 0.0, []

    # Ignore ASR event tags like (cough) — they are Latin but get localized later
    stripped = _NONSPEECH_RE.sub(" ", text or "")
    persian, latin, other = _script_letter_counts(stripped)
    letters = persian + latin + other
    if letters == 0:
        return 0.0, []

    reasons: List[str] = []
    penalty = 0.0
    latin_ratio = latin / letters

    if persian == 0 and latin > 0:
        # Pure Latin / wrong-language hop (Polish, Hungarian, gibberish, …)
        penalty = 1.0
        reasons.append("wrong_script")
    elif persian == 0 and other > 0:
        penalty = 0.9
        reasons.append("no_persian")
    elif latin_ratio >= 0.45:
        penalty = 0.7
        reasons.append(f"latin_heavy:{latin_ratio:.2f}")
    elif latin_ratio >= 0.3:
        penalty = 0.35
        reasons.append(f"latin_ratio:{latin_ratio:.2f}")

    return penalty, reasons


def score_stt_text(text: str, *, language: str = "en") -> tuple[float, List[str]]:
    """Return quality score in [0,1] (higher=better) and reason codes."""
    t = (text or "").strip()
    reasons: List[str] = []
    if not t:
        return 0.0, ["empty"]
    if len(t) < 2:
        return 0.0, ["too_short"]
    if _PUNCT_ONLY.match(t):
        return 0.0, ["punct_only"]
    if is_whisper_boilerplate(t):
        return 0.0, ["whisper_boilerplate"]
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

    script_penalty, script_reasons = _persian_script_penalty(t, language)
    if script_penalty:
        score -= script_penalty
        reasons.extend(script_reasons)

    return max(0.0, min(1.0, score)), reasons


def gate_stt_text(
    text: str,
    *,
    min_score: float = 0.35,
    collapse_runs: bool = True,
    language: str = "en",
) -> ReviewResult:
    """Fast heuristic gate: drop / lightly collapse / keep. No LLM."""
    from api.meeting.aligner import _collapse_internal_repeats

    raw = (text or "").strip()
    score, reasons = score_stt_text(raw, language=language)
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

    async def review_text(
        self, text: str, *, language: str = "en", min_score: float = 0.35
    ) -> ReviewResult:
        heuristic = gate_stt_text(text, min_score=min_score, language=language)
        if heuristic.action == "drop":
            return heuristic
        if not self.enabled:
            return heuristic

        candidate = heuristic.text
        try:
            messages = [
                {"role": "system", "content": _load_polish_prompts()[0]},
                {
                    "role": "user",
                    "content": (
                        f"Language hint: {language}\n"
                        f"Raw Whisper text:\n{candidate}"
                    ),
                },
            ]
            raw = await self.llm.complete(
                messages, temperature=0.0, max_tokens=min(512, review_max_tokens())
            )
            data = _parse_review_json(raw) or {}
            action = str(data.get("action") or "keep").lower()
            out_text, action = _apply_llm_edit(
                candidate, action, str(data.get("text") or ""),
                original_score=heuristic.score,
            )
            reason = str(data.get("reason") or "llm_review")
            if action == "drop":
                return ReviewResult(
                    action="drop",
                    text="",
                    score=heuristic.score,
                    reasons=heuristic.reasons + [reason],
                    raw_text=heuristic.raw_text or text,
                )
            return ReviewResult(
                action=action,  # type: ignore[arg-type]
                text=out_text,
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
        language: str = "en",
    ) -> List[TranscriptSegment]:
        """Finalize pass: heuristic gate each row, then optional batched LLM polish."""
        if not segments:
            return []

        # First pass: heuristics (always)
        gated: List[TranscriptSegment] = []
        gate_meta: List[tuple[float, List[str]]] = []
        for seg in segments:
            result = gate_stt_text(seg.text, language=language)
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
            gate_meta.append((result.score, list(result.reasons or [])))

        if not gated or not self.enabled:
            return gated

        # Only polish shaky unique texts — clean heuristic output skips the LLM.
        polish_texts = select_polish_items(
            [(seg.text, meta[0], meta[1]) for seg, meta in zip(gated, gate_meta)]
        )
        if not polish_texts:
            logger.info(
                "STT polish skipped LLM (%d segments already clean after heuristics)",
                len(gated),
            )
            return gated

        items_payload = [
            {"id": f"t{i}", "text": text} for i, text in enumerate(polish_texts)
        ]
        id_to_text = {item["id"]: item["text"] for item in items_payload}
        reviewed_map: dict[str, str] = {}
        batches = chunk_review_items(items_payload, review_max_batch_chars())
        max_batches = review_max_batches()
        if len(batches) > max_batches:
            logger.warning(
                "STT polish truncating batches %d → %d to keep finalize responsive",
                len(batches),
                max_batches,
            )
            # Prefer worst-first items already ordered in select_polish_items.
            batches = batches[:max_batches]
        _, batch_prompt = _load_polish_prompts()
        batch_timeout = review_batch_timeout_s()
        total_budget = review_total_budget_s()
        started = time.monotonic()

        logger.info(
            "STT polish: %d/%d unique texts → %d LLM batch(es) "
            "(char_budget=%d max_tokens≤%d batch_timeout=%.0fs total_budget=%.0fs)",
            len(items_payload),
            len({s.text for s in gated}),
            len(batches),
            review_max_batch_chars(),
            review_max_tokens(),
            batch_timeout,
            total_budget,
        )

        for batch_idx, batch in enumerate(batches, start=1):
            elapsed = time.monotonic() - started
            if elapsed >= total_budget:
                logger.warning(
                    "STT polish total budget exhausted (%.1fs); skipping remaining %d batches",
                    elapsed,
                    len(batches) - batch_idx + 1,
                )
                break
            batch_ids = {item["id"] for item in batch}
            batch_id_to_text = {i: id_to_text[i] for i in batch_ids}
            try:
                max_tokens = polish_max_tokens_for_batch(batch)
                messages = [
                    {"role": "system", "content": batch_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Language hint: {language}\n"
                            f"Batch {batch_idx}/{len(batches)}. "
                            "Return keep/fix/drop per item. Never summarize.\n"
                            f"Segments JSON:\n{json.dumps(batch, ensure_ascii=False)}"
                        ),
                    },
                ]
                remaining = max(5.0, total_budget - elapsed)
                raw = await self.llm.complete(
                    messages,
                    temperature=0.0,
                    max_tokens=max_tokens,
                    timeout=min(batch_timeout, remaining),
                )
                data = _parse_review_json(raw) or {}
                items = data.get("items") or []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    sid = str(item.get("id") or "")
                    action = str(item.get("action") or "keep").lower()
                    original = batch_id_to_text.get(sid)
                    if original is None:
                        continue
                    orig_score, _ = score_stt_text(original, language=language)
                    fixed, _eff = _apply_llm_edit(
                        original, action, str(item.get("text") or ""),
                        original_score=orig_score,
                    )
                    reviewed_map[original] = fixed
            except Exception as exc:
                logger.warning(
                    "Batch LLM STT review failed (batch %d/%d): %s",
                    batch_idx,
                    len(batches),
                    exc,
                )
                # Keep heuristic text; abort remaining batches on timeout/unreachable LLM.
                msg = str(exc).lower()
                if (
                    "timeout" in msg
                    or "Timeout" in type(exc).__name__
                    or "is unreachable" in str(exc)
                    or "cannot connect" in msg
                    or "timed out" in str(exc)
                ):
                    logger.warning("STT polish stopping early after LLM failure")
                    break
                continue

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
        # Never wipe a non-empty transcript because the LLM dropped everything.
        if not out and gated:
            logger.warning(
                "STT polish dropped all %d segments; keeping heuristic-gated text",
                len(gated),
            )
            return gated
        return out
