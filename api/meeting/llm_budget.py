"""Keep LLM requests within the configured context window."""

from __future__ import annotations

import os
from typing import Dict, List, Sequence

DEFAULT_LLM_MAX_CONTEXT = 8192
DEFAULT_COMPLETION_RESERVE = 128
MIN_COMPLETION_TOKENS = 256


def llm_max_context_tokens() -> int:
    """Match runtime VLLM_MAX_MODEL_LEN via root .env LLM_MAX_CONTEXT_TOKENS."""
    raw = os.getenv("LLM_MAX_CONTEXT_TOKENS", "").strip()
    if not raw:
        return DEFAULT_LLM_MAX_CONTEXT
    try:
        return max(1024, int(raw))
    except ValueError:
        return DEFAULT_LLM_MAX_CONTEXT


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for Persian/mixed transcript text."""
    if not text:
        return 0
    # Slightly pessimistic — avoids overshooting a small vLLM context.
    return max(1, (len(text) + 1) // 2)


def estimate_messages_tokens(messages: Sequence[Dict[str, str]]) -> int:
    total = 0
    for msg in messages:
        total += estimate_tokens(str(msg.get("content") or ""))
        total += 4  # role / formatting overhead per message
    return total


def cap_completion_tokens(
    messages: Sequence[Dict[str, str]],
    requested: int,
    *,
    reserve: int = DEFAULT_COMPLETION_RESERVE,
    min_tokens: int = MIN_COMPLETION_TOKENS,
) -> int:
    """Shrink max_tokens so prompt + completion fits the context window."""
    limit = llm_max_context_tokens()
    input_est = estimate_messages_tokens(messages)
    available = limit - input_est - reserve
    capped = min(requested, available)
    return max(min_tokens, capped)


def transcript_chars_for_context(
    *,
    system_tokens: int,
    wrapper_tokens: int,
    completion_tokens: int,
    reserve: int = DEFAULT_COMPLETION_RESERVE,
) -> int:
    """Max transcript characters that fit one LLM call."""
    ctx = llm_max_context_tokens()
    transcript_tokens = ctx - system_tokens - wrapper_tokens - completion_tokens - reserve
    transcript_tokens = max(256, transcript_tokens)
    return transcript_tokens * 2
