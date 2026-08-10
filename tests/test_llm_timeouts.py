from __future__ import annotations

from api.providers.llm import build_llm_timeout, format_llm_failure, normalize_chat_completions_url


def test_normalize_chat_completions_url():
    assert normalize_chat_completions_url("http://host/v1").endswith("/chat/completions")
    assert (
        normalize_chat_completions_url("http://host/api/v1/chat/completions")
        == "http://host/api/v1/chat/completions"
    )


def test_build_llm_timeout_fail_fast_connect():
    t = build_llm_timeout(60)
    assert t.total == 60
    assert t.sock_connect == 8.0
    assert t.connect == 8.0


def test_format_llm_failure_connector():
    class FakeConn(Exception):
        pass

    # Simulate message shape from aiohttp
    exc = Exception("Cannot connect to host 81.29.248.136:80 ssl:default")
    msg = format_llm_failure("http://81.29.248.136/api/v1/chat/completions", exc)
    assert "81.29.248.136" in msg
    assert "در دسترس نیست" in msg
