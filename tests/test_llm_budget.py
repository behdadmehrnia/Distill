from __future__ import annotations

import os

import pytest

from api.meeting.llm_budget import (
    cap_completion_tokens,
    estimate_messages_tokens,
    llm_max_context_tokens,
    transcript_chars_for_context,
)


def test_cap_completion_tokens_shrinks_when_prompt_is_large(monkeypatch):
    monkeypatch.setenv("LLM_MAX_CONTEXT_TOKENS", "4096")
    messages = [
        {"role": "system", "content": "x" * 500},
        {"role": "user", "content": "y" * 3600},
    ]
    capped = cap_completion_tokens(messages, requested=2048)
    input_est = estimate_messages_tokens(messages)
    assert capped < 2048
    assert capped + input_est + 128 <= llm_max_context_tokens()


def test_transcript_chars_for_context_respects_small_window(monkeypatch):
    monkeypatch.setenv("LLM_MAX_CONTEXT_TOKENS", "4096")
    chars = transcript_chars_for_context(
        system_tokens=650,
        wrapper_tokens=120,
        completion_tokens=1536,
    )
    assert chars < 5000


def test_llm_max_context_tokens_default(monkeypatch):
    monkeypatch.delenv("LLM_MAX_CONTEXT_TOKENS", raising=False)
    assert llm_max_context_tokens() == 8192
