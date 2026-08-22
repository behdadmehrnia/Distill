"""Pytest configuration for Distill."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def disable_upload_denoise_by_default(monkeypatch):
    """Tests use mock STT/diarization; skip optional ONNX denoise unless opted in."""
    monkeypatch.setenv("AUDIO_DENOISE_UPLOAD", "0")
