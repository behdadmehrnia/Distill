from __future__ import annotations

import importlib.util
import os
from unittest.mock import patch

import numpy as np
import pytest

from api.audio.denoise import DenoiseResult, _align_length, denoise_upload_audio
from api.meeting.store import TranscriptStore


@pytest.fixture
def store(database_url):
    return TranscriptStore(database_url=database_url)


def test_align_length_trim_and_pad():
    src = np.arange(10, dtype=np.float32)
    assert len(_align_length(src, 8)) == 8
    out = _align_length(src[:5], 8)
    assert len(out) == 8
    assert out[5] == 0.0


def test_denoise_disabled_returns_original():
    audio = np.linspace(-0.1, 0.1, 1600, dtype=np.float32)
    result = denoise_upload_audio(audio, 16000, enabled=False)
    assert result.applied is False
    assert result.backend == "disabled"
    np.testing.assert_array_equal(result.audio, audio)


def test_denoise_fail_open_on_backend_error():
    audio = np.linspace(-0.1, 0.1, 1600, dtype=np.float32)
    with patch(
        "api.audio.denoise._denoise_deepfilter_stream",
        side_effect=RuntimeError("model missing"),
    ):
        result = denoise_upload_audio(audio, 16000, enabled=True)
    assert result.applied is False
    assert result.backend == "unavailable"
    np.testing.assert_array_equal(result.audio, audio)


@pytest.mark.skipif(
    not os.getenv("DEEPFILTER_STREAM_MODEL_DIR")
    or importlib.util.find_spec("deepfilter_stream") is None,
    reason="Install deepfilter-stream and set DEEPFILTER_STREAM_MODEL_DIR",
)
def test_denoise_integration_when_model_present():
    sr = 16000
    t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
    audio = (
        0.25 * np.sin(2 * np.pi * 220 * t)
        + 0.12 * np.random.default_rng(0).standard_normal(len(t))
    ).astype(np.float32)
    result = denoise_upload_audio(audio, sr, enabled=True)
    assert result.applied is True
    assert result.backend == "deepfilter-stream"
    assert len(result.audio) == len(audio)


@pytest.mark.asyncio
async def test_upload_pipeline_calls_denoise(store, tmp_path):
    from api.meeting.diarization import SpeakerDiarizer
    from api.meeting.models import MeetingRecord
    from api.meeting.session import MeetingSession
    from api.tuning import make_tuning
    from tests.test_pipeline import MockSTT, _sine, _write_wav

    sr = 16000
    audio = _sine(sr, 10.0, 200.0, amp=0.12)
    wav_path = _write_wav(tmp_path / "upload.wav", audio, sr)

    cleaned = (audio * 0.5).astype(np.float32)
    mock_result = DenoiseResult(
        audio=cleaned, applied=True, backend="mock", elapsed_ms=1.0
    )

    stt = MockSTT(text="سلام این یک جلسه آزمایشی است")
    record = MeetingRecord.create("upload-denoise-test")
    session = MeetingSession(
        record=record,
        store=store,
        stt_provider=stt,
        diarizer=SpeakerDiarizer(max_speakers=1, energy_threshold=0.01),
        review_agent=None,
        audio_dir=str(tmp_path / "audio"),
        upload_denoise_enabled=True,
        tuning=make_tuning(
            {
                "window_ms": 8000,
                "hop_ms": 6000,
                "stt_workers": 2,
                "stt_review_mode": "heuristic",
                "stt_retry_count": 1,
            }
        ),
    )

    with patch(
        "api.audio.denoise.denoise_upload_audio", return_value=mock_result
    ) as denoise_mock:
        segments = await session.process_uploaded_file(str(wav_path))

    assert segments
    denoise_mock.assert_called_once()
    np.testing.assert_allclose(session.ingest.get_buffer(), cleaned, rtol=0, atol=1e-6)
