"""Runtime-tunable sensitivities for Distill (testable from the UI)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List


# Defaults match current production-ish behavior
DEFAULT_TUNING: Dict[str, Any] = {
    "window_ms": 8000,
    "hop_ms": 6000,
    "diarize_every_ms": 20000,
    "min_speech_rms": 0.008,
    "stt_workers": 2,
    "stt_retry_count": 3,
    "energy_threshold": 0.01,
    "min_speakers": 1,
    "max_speakers": 2,
    "merge_short_ms": 400,
    "min_overlap_ms": 1200,
    "dedupe_similarity": 0.45,
    "dedupe_time_overlap": 0.35,
    "stt_language": "fa",
    # Whisper quality: heuristic always (unless off); LLM polish on finalize by default
    "stt_review_mode": "finalize",
    "stt_min_quality": 0.35,
}

# Metadata for the settings panel (Persian labels)
TUNING_SCHEMA: List[Dict[str, Any]] = [
    {
        "key": "window_ms",
        "group": "ضبط / STT",
        "label": "طول پنجره STT",
        "unit": "ms",
        "type": "number",
        "min": 4000,
        "max": 15000,
        "step": 500,
        "apply": "next_session",
        "help": "هر تکه صوت چند میلی‌ثانیه به STT برود — فقط روی جلسات جدید اعمال می‌شود",
    },
    {
        "key": "hop_ms",
        "group": "ضبط / STT",
        "label": "گام پنجره (hop)",
        "unit": "ms",
        "type": "number",
        "min": 2000,
        "max": 12000,
        "step": 500,
        "apply": "next_session",
        "help": "فاصله شروع پنجره‌ها (~۲ثانیه هم‌پوشانی برای Whisper) — فقط جلسات جدید",
    },
    {
        "key": "stt_workers",
        "group": "ضبط / STT",
        "label": "تعداد worker موازی STT",
        "unit": "",
        "type": "number",
        "min": 1,
        "max": 5,
        "step": 1,
        "apply": "next_session",
        "help": "چند درخواست STT هم‌زمان — فقط روی جلسات جدید",
    },
    {
        "key": "stt_retry_count",
        "group": "ضبط / STT",
        "label": "تعداد تلاش مجدد STT",
        "unit": "",
        "type": "number",
        "min": 0,
        "max": 5,
        "step": 1,
        "apply": "live",
        "help": "در صورت خطا، چند بار با backoff دوباره امتحان شود",
    },
    {
        "key": "min_speech_rms",
        "group": "ضبط / STT",
        "label": "آستانه انرژی گفتار (RMS)",
        "unit": "",
        "type": "number",
        "min": 0.002,
        "max": 0.05,
        "step": 0.001,
        "apply": "live",
        "help": "زیر این انرژی، پنجره به STT نمی‌رود",
    },
    {
        "key": "stt_language",
        "group": "ضبط / STT",
        "label": "زبان STT",
        "unit": "",
        "type": "text",
        "apply": "live",
        "help": "مثلاً fa یا en",
    },
    {
        "key": "stt_review_mode",
        "group": "کیفیت Whisper",
        "label": "حالت Review Agent",
        "unit": "",
        "type": "select",
        "options": ["off", "heuristic", "finalize", "live"],
        "apply": "live",
        "help": "off | heuristic | finalize | live — پیش‌فرض finalize",
    },
    {
        "key": "stt_min_quality",
        "group": "کیفیت Whisper",
        "label": "حداقل نمره کیفیت STT",
        "unit": "0–1",
        "type": "number",
        "min": 0.1,
        "max": 0.8,
        "step": 0.05,
        "apply": "live",
        "help": "زیر این نمره، متن hallucination حذف می‌شود (تکرار خیلی خیلی…)",
    },
    {
        "key": "diarize_every_ms",
        "group": "تشخیص گوینده",
        "label": "فاصله diarization زنده",
        "unit": "ms",
        "type": "number",
        "min": 10000,
        "max": 60000,
        "step": 1000,
        "apply": "live",
        "help": "هر چند ms یک‌بار گوینده‌ها به‌روز شوند",
    },
    {
        "key": "energy_threshold",
        "group": "تشخیص گوینده",
        "label": "آستانه سکوت diarization",
        "unit": "",
        "type": "number",
        "min": 0.001,
        "max": 0.05,
        "step": 0.001,
        "apply": "live",
        "help": "فریم‌های آرام‌تر از این در clustering نادیده گرفته می‌شوند",
    },
    {
        "key": "min_speakers",
        "group": "تشخیص گوینده",
        "label": "حداقل گوینده",
        "unit": "",
        "type": "number",
        "min": 1,
        "max": 8,
        "step": 1,
        "apply": "live",
    },
    {
        "key": "max_speakers",
        "group": "تشخیص گوینده",
        "label": "حداکثر گوینده",
        "unit": "",
        "type": "number",
        "min": 1,
        "max": 8,
        "step": 1,
        "apply": "live",
    },
    {
        "key": "merge_short_ms",
        "group": "تشخیص گوینده",
        "label": "ادغام قطعات کوتاه",
        "unit": "ms",
        "type": "number",
        "min": 100,
        "max": 2000,
        "step": 50,
        "apply": "live",
        "help": "بازه کوتاه‌تر از این در گوینده قبلی ادغام می‌شود",
    },
    {
        "key": "min_overlap_ms",
        "group": "هم‌صحبتی / متن",
        "label": "حداقل طول هم‌صحبتی",
        "unit": "ms",
        "type": "number",
        "min": 500,
        "max": 3000,
        "step": 100,
        "apply": "live",
        "help": "زیر این مقدار، هم‌صحبتی اعلام نمی‌شود (برای میک تکی بهتر است بالا باشد)",
    },
    {
        "key": "dedupe_similarity",
        "group": "هم‌صحبتی / متن",
        "label": "شباهت متن برای dedupe",
        "unit": "0–1",
        "type": "number",
        "min": 0.2,
        "max": 0.8,
        "step": 0.05,
        "apply": "live",
        "help": "پنجره‌های hop با متن شبیه، یکی می‌شوند",
    },
    {
        "key": "dedupe_time_overlap",
        "group": "هم‌صحبتی / متن",
        "label": "نسبت هم‌پوشانی زمانی dedupe",
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
