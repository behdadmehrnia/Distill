"""Runtime-tunable sensitivities for Distill (testable from the UI)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


DEFAULT_TUNING: Dict[str, Any] = {
    # Longer sequential chunks → better ASR context; hop==window → no overlap fights
    "window_ms": 10000,
    "hop_ms": 8500,
    "diarize_every_ms": 0,
    "min_speech_rms": 0.008,
    "stt_workers": 1,
    "stt_retry_count": 3,
    "energy_threshold": 0.01,
    "min_speakers": 1,
    "max_speakers": 2,
    "merge_short_ms": 400,
    "min_overlap_ms": 1200,
    "dedupe_similarity": 0.45,
    "dedupe_time_overlap": 0.35,
    "stt_language": "en",
    # Whisper quality: heuristic always (unless off); LLM polish on finalize by default
    "stt_review_mode": "finalize",
    "stt_min_quality": 0.35,
}

TUNING_SCHEMA: List[Dict[str, Any]] = [
    {
        "key": "window_ms",
        "group": "Recording / STT",
        "label": "STT window length",
        "unit": "ms",
        "type": "number",
        "min": 5000,
        "max": 30000,
        "step": 1000,
        "apply": "next_session",
        "help": "How many milliseconds of audio go to STT per chunk — longer chunks are usually more accurate (new meetings only)",
    },
    {
        "key": "hop_ms",
        "group": "Recording / STT",
        "label": "Window hop",
        "unit": "ms",
        "type": "number",
        "min": 2000,
        "max": 12000,
        "step": 500,
        "apply": "next_session",
        "help": "Spacing between window starts — keep it equal to the window length for no overlap",
    },
    {
        "key": "stt_workers",
        "group": "Recording / STT",
        "label": "Parallel STT workers",
        "unit": "",
        "type": "number",
        "min": 1,
        "max": 5,
        "step": 1,
        "apply": "next_session",
        "help": "How many STT requests run at once — 1 is recommended for accuracy and stable ordering",
    },
    {
        "key": "stt_retry_count",
        "group": "Recording / STT",
        "label": "STT retries",
        "unit": "",
        "type": "number",
        "min": 0,
        "max": 5,
        "step": 1,
        "apply": "live",
        "help": "How many times to retry with backoff after an error",
    },
    {
        "key": "min_speech_rms",
        "group": "Recording / STT",
        "label": "Speech energy threshold (RMS)",
        "unit": "",
        "type": "number",
        "min": 0.002,
        "max": 0.05,
        "step": 0.001,
        "apply": "live",
        "help": "Windows quieter than this are not sent to STT",
    },
    {
        "key": "stt_language",
        "group": "Recording / STT",
        "label": "STT language",
        "unit": "",
        "type": "text",
        "apply": "live",
        "help": "e.g. en or fa",
    },
    {
        "key": "stt_review_mode",
        "group": "Whisper quality",
        "label": "Review agent mode",
        "unit": "",
        "type": "select",
        "options": ["off", "heuristic", "finalize", "live"],
        "apply": "live",
        "help": "off | heuristic | finalize | live — default is finalize",
    },
    {
        "key": "stt_min_quality",
        "group": "Whisper quality",
        "label": "Minimum STT quality score",
        "unit": "0–1",
        "type": "number",
        "min": 0.1,
        "max": 0.8,
        "step": 0.05,
        "apply": "live",
        "help": "Text scoring below this is dropped as hallucination (e.g. \"very very very…\")",
    },
    {
        "key": "diarize_every_ms",
        "group": "Speaker detection",
        "label": "Live diarization interval",
        "unit": "ms",
        "type": "number",
        "min": 0,
        "max": 60000,
        "step": 1000,
        "apply": "live",
        "hidden": True,
        "help": "0 = only on the final recording after stopping. >0 runs periodic diarization while recording (disabled in the UI)",
    },
    {
        "key": "energy_threshold",
        "group": "Speaker detection",
        "label": "Diarization silence threshold",
        "unit": "",
        "type": "number",
        "min": 0.001,
        "max": 0.05,
        "step": 0.001,
        "apply": "live",
        "help": "Frames quieter than this are ignored during clustering",
    },
    {
        "key": "min_speakers",
        "group": "Speaker detection",
        "label": "Minimum speakers",
        "unit": "",
        "type": "number",
        "min": 1,
        "max": 8,
        "step": 1,
        "apply": "live",
    },
    {
        "key": "max_speakers",
        "group": "Speaker detection",
        "label": "Maximum speakers",
        "unit": "",
        "type": "number",
        "min": 1,
        "max": 8,
        "step": 1,
        "apply": "live",
    },
    {
        "key": "merge_short_ms",
        "group": "Speaker detection",
        "label": "Merge short segments",
        "unit": "ms",
        "type": "number",
        "min": 100,
        "max": 2000,
        "step": 50,
        "apply": "live",
        "help": "Intervals shorter than this are merged into the previous speaker",
    },
    {
        "key": "min_overlap_ms",
        "group": "Overlap / transcript",
        "label": "Minimum overlap length",
        "unit": "ms",
        "type": "number",
        "min": 500,
        "max": 3000,
        "step": 100,
        "apply": "live",
        "help": "Overlap shorter than this is not reported (keep it high for a single shared mic)",
    },
    {
        "key": "dedupe_similarity",
        "group": "Overlap / transcript",
        "label": "Text similarity for dedupe",
        "unit": "0–1",
        "type": "number",
        "min": 0.2,
        "max": 0.8,
        "step": 0.05,
        "apply": "live",
        "help": "Hop windows with similar text are collapsed into one",
    },
    {
        "key": "dedupe_time_overlap",
        "group": "Overlap / transcript",
        "label": "Dedupe time-overlap ratio",
        "unit": "0–1",
        "type": "number",
        "min": 0.1,
        "max": 0.95,
        "step": 0.05,
        "apply": "live",
    },
]

_INT_KEYS = {
    "window_ms",
    "hop_ms",
    "diarize_every_ms",
    "min_speakers",
    "max_speakers",
    "merge_short_ms",
    "min_overlap_ms",
    "stt_workers",
    "stt_retry_count",
}


def make_tuning(overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    data = deepcopy(DEFAULT_TUNING)
    if overrides:
        data.update(_sanitize(overrides))
    return data


def _sanitize(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for item in TUNING_SCHEMA:
        key = item["key"]
        if key not in raw:
            continue
        val = raw[key]
        if item["type"] == "number":
            try:
                num = float(val)
            except (TypeError, ValueError):
                continue
            if key in _INT_KEYS:
                num = int(round(num))
            lo, hi = item.get("min"), item.get("max")
            if lo is not None:
                num = max(lo, num)
            if hi is not None:
                num = min(hi, num)
            out[key] = num
        elif item["type"] == "select":
            text = str(val).strip().lower()
            options = [str(o).lower() for o in item.get("options") or []]
            if text not in options:
                text = str(DEFAULT_TUNING.get(key, "")).lower()
            out[key] = text
        else:
            text = str(val).strip() or str(DEFAULT_TUNING[key])
            if key == "stt_review_mode":
                text = text.lower()
                if text not in {"off", "heuristic", "finalize", "live"}:
                    text = str(DEFAULT_TUNING[key])
            out[key] = text
    if "min_speakers" in out and "max_speakers" in out:
        if out["min_speakers"] > out["max_speakers"]:
            out["min_speakers"], out["max_speakers"] = (
                out["max_speakers"],
                out["min_speakers"],
            )
    if "window_ms" in out and "hop_ms" in out and out["hop_ms"] > out["window_ms"]:
        out["hop_ms"] = out["window_ms"]
    return out


def public_tuning_payload(tuning: Dict[str, Any]) -> Dict[str, Any]:
    return {"values": tuning, "schema": TUNING_SCHEMA, "defaults": DEFAULT_TUNING}
