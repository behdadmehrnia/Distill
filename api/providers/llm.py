from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


class OpenAICompatibleLLM:
    """OpenAI-compatible chat completions client for Distill insights."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.endpoint = (
            endpoint
            or os.getenv("LLM_ENDPOINT")
            or "http://localhost:8000/v1/chat/completions"
        )
        self.api_key = api_key or os.getenv("LLM_API_KEY") or ""
        self.model = (
            model
            or os.getenv("LLM_MODEL_NAME")
            or "gpt-4o-mini"
        )
        self.timeout = aiohttp.ClientTimeout(total=timeout)

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

        logger.info("LLM request to %s model=%s", self.endpoint, payload["model"])
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(self.endpoint, json=payload, headers=headers) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"LLM error {resp.status}: {body[:500]}")
                data = await resp.json(content_type=None)
                return self._extract_text(data)

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
