"""Validate Hugging Face access before attempting gated pyannote downloads."""

from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger("diarize.hf")

# Models required for pyannote/speaker-diarization-3.1
_GATED_REPOS = (
    "pyannote/speaker-diarization-3.1",
    "pyannote/segmentation-3.0",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
)


def resolve_hf_token(explicit: Optional[str] = None) -> str:
    token = (
        (explicit or "").strip()
        or (os.getenv("HF_TOKEN") or "").strip()
        or (os.getenv("HUGGINGFACE_TOKEN") or "").strip()
        or (os.getenv("HUGGING_FACE_HUB_TOKEN") or "").strip()
    )
    return token


def validate_pyannote_access(
    token: Optional[str] = None,
    timeout_s: float = 20.0,
) -> Tuple[bool, str]:
    """
    Return (ok, reason). ok=True only when the token can reach gated pyannote repos.
    Never logs the token value.
    """
    tok = resolve_hf_token(token)
    if not tok:
        return False, "HF_TOKEN not set"

    try:
        import httpx
    except ImportError:
        return False, "httpx not installed"

    hub = (
        os.getenv("HF_ENDPOINT")
        or os.getenv("HUGGINGFACE_HUB_ENDPOINT")
        or "https://huggingface.co"
    ).rstrip("/")

    headers = {"Authorization": f"Bearer {tok}"}
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            # Whoami — invalid tokens fail here.
            who = client.get(f"{hub}/api/whoami-v2", headers=headers)
            if who.status_code in (401, 403):
                return False, f"HF token rejected (HTTP {who.status_code})"
            if who.status_code >= 400:
                return False, f"HF whoami failed (HTTP {who.status_code})"

            # Gated model — 403 usually means terms not accepted / no access.
            for repo in _GATED_REPOS:
                url = f"{hub}/api/models/{repo}"
                resp = client.get(url, headers=headers)
                if resp.status_code in (401, 403):
                    return (
                        False,
                        f"No access to {repo} (HTTP {resp.status_code}). "
                        "Accept model terms on Hugging Face, then retry.",
                    )
                if resp.status_code == 404:
                    return False, f"Model not found at Hub: {repo}"
                if resp.status_code >= 400:
                    return False, f"Hub check failed for {repo} (HTTP {resp.status_code})"
    except Exception as exc:
        return False, f"HF validation error: {exc}"

    return True, "ok"


def should_use_hub_download(token: Optional[str] = None) -> bool:
    ok, reason = validate_pyannote_access(token)
    if ok:
        logger.info("Hugging Face pyannote access verified")
        return True
    logger.warning("Skipping Hugging Face pyannote download: %s", reason)
    return False
