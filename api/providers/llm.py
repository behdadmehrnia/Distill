from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from api.providers.http_util import client_session

logger = logging.getLogger(__name__)


def normalize_chat_completions_url(endpoint: str) -> str:
    """Accept either a full chat/completions URL or an OpenAI-style base URL."""
    url = (endpoint or "").strip().rstrip("/")
    if not url:
        return "http://localhost:8000/v1/chat/completions"
    path = (urlparse(url).path or "").rstrip("/")
    if path.endswith("/chat/completions"):
        return url
    # Common bases: /v1, /api/v1, /openai/v1, or bare host
    if path.endswith("/v1") or path == "" or path.endswith("/openai"):
        return f"{url}/chat/completions"
    return f"{url}/chat/completions"


def _connect_timeout_s(total: float) -> float:
    raw = os.getenv("LLM_CONNECT_TIMEOUT_S", "").strip()
    try:
        connect = float(raw) if raw else 8.0
    except ValueError:
        connect = 8.0
    return max(2.0, min(connect, float(total)))


def build_llm_timeout(total: float) -> aiohttp.ClientTimeout:
    """Fail fast on dead hosts; keep total budget for slow model responses."""
    connect = _connect_timeout_s(total)
    return aiohttp.ClientTimeout(
        total=float(total),
        connect=connect,
        sock_connect=connect,
        sock_read=float(total),
    )


def format_llm_failure(endpoint: str, exc: BaseException) -> str:
    """Human-readable error for API responses / UI prompts."""
    host = urlparse(endpoint).netloc or endpoint
    name = type(exc).__name__
    msg = str(exc) or name
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "Timeout" in name:
        return (
            f"زمان پاسخ مدل زبانی (LLM) در {host} به پایان رسید. "
            "اتصال یا سرویس مدل را بررسی کنید."
        )
    if isinstance(exc, aiohttp.ClientConnectorError) or "Cannot connect" in msg:
        return (
            f"سرور مدل زبانی (LLM) در {host} در دسترس نیست. "
            "LLM_ENDPOINT و شبکه/فایروال را بررسی کنید."
        )
    if isinstance(exc, aiohttp.ClientError):
        return f"خطا در ارتباط با مدل زبانی ({host}): {msg}"
    return f"خطای مدل زبانی ({host}): {msg}"


class OpenAICompatibleLLM:
    """OpenAI-compatible chat completions client for Distill insights."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 90.0,
    ):
        raw = (
            endpoint
            or os.getenv("LLM_ENDPOINT")
            or "http://localhost:8000/v1/chat/completions"
        )
        self.endpoint = normalize_chat_completions_url(raw)
        self.api_key = api_key or os.getenv("LLM_API_KEY") or ""
        self.model = (
            model
            or os.getenv("LLM_MODEL_NAME")
            or "gpt-4o-mini"
        )
        self.timeout = build_llm_timeout(timeout)

    def _resolve_timeout(self, override: Any = None) -> aiohttp.ClientTimeout:
        if override is None:
            return self.timeout
        if isinstance(override, aiohttp.ClientTimeout):
            return override
        try:
            return build_llm_timeout(float(override))
        except (TypeError, ValueError):
            return self.timeout

    async def complete(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        **kwargs: Any,
    ) -> str:
        payload = {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req_timeout = self._resolve_timeout(kwargs.get("timeout"))
        logger.info("LLM request to %s model=%s", self.endpoint, payload["model"])
        try:
            async with client_session(timeout=req_timeout) as session:
                async with session.post(
                    self.endpoint, json=payload, headers=headers
                ) as resp:
                    body = await resp.text()
                    if resp.status >= 400:
                        raise RuntimeError(
                            f"LLM error {resp.status}: {body[:500]}"
                        )
                    data = await resp.json(content_type=None)
                    return self._extract_text(data)
        except RuntimeError:
            raise
        except (asyncio.TimeoutError, TimeoutError, aiohttp.ClientError) as exc:
            detail = format_llm_failure(self.endpoint, exc)
            logger.warning("LLM request failed: %s", detail)
            raise RuntimeError(detail) from exc

    @staticmethod
    def _extract_text(data: Dict[str, Any]) -> str:
        # OpenAI-style
        choices = data.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(part.get("text", ""))
                    elif isinstance(part, str):
                        parts.append(part)
                return "".join(parts)
            # some servers use "text"
            if "text" in choices[0]:
                return str(choices[0]["text"])
        if "output_text" in data:
            return str(data["output_text"])
        if "response" in data:
            return str(data["response"])
        raise RuntimeError(f"Unrecognized LLM response shape: {list(data.keys())}")
