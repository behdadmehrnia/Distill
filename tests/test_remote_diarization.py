"""Remote diarization client (mocked HTTP)."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from api.meeting.diarization import SpeakerDiarizer


def test_remote_diarize_uses_http_endpoint():
    sr = 16000
    audio = (np.random.randn(sr) * 0.05).astype(np.float32)
    diarizer = SpeakerDiarizer(
        sample_rate=sr,
        remote_endpoint="http://127.0.0.1:8090",
        hf_token=None,
        allow_fallback=True,
    )

    health = MagicMock()
    health.status_code = 200
    health.content = b'{"ready":true,"backend":"pyannote"}'
    health.json.return_value = {"ready": True, "backend": "pyannote"}

    diarize = MagicMock()
    diarize.status_code = 200
    diarize.json.return_value = {
        "intervals": [
            {
                "speaker_id": "SPEAKER_00",
                "start_ms": 0,
                "end_ms": 900,
                "is_overlap": False,
            }
        ],
        "backend": "pyannote",
    }
    diarize.raise_for_status = MagicMock()

    client = MagicMock()
    client.get.return_value = health
    client.post.return_value = diarize
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("httpx.Client", return_value=client):
        intervals = diarizer.diarize(audio, sr)

    assert diarizer.backend == "pyannote"
    assert diarizer._load["mode"] == "remote"
    assert len(intervals) == 1
    assert intervals[0].speaker_id == "SPEAKER_00"
    assert intervals[0].end_ms == 900
    client.post.assert_called()
    assert "v1/diarize" in client.post.call_args.args[0]


def test_remote_endpoint_makes_pyannote_enabled_without_hf_token():
    d = SpeakerDiarizer(remote_endpoint="http://localhost:8090", hf_token=None)
    assert d.pyannote_enabled is True


def test_remote_nemo_backend_reported():
    sr = 16000
    audio = (np.random.randn(sr) * 0.05).astype(np.float32)
    diarizer = SpeakerDiarizer(
        sample_rate=sr,
        remote_endpoint="http://127.0.0.1:8090",
        allow_fallback=False,
    )

    health = MagicMock()
    health.status_code = 200
    health.content = b'{"ready":true,"backend":"nemo"}'
    health.json.return_value = {"ready": True, "backend": "nemo"}

    diarize = MagicMock()
    diarize.status_code = 200
    diarize.json.return_value = {
        "intervals": [
            {
                "speaker_id": "SPEAKER_00",
                "start_ms": 0,
                "end_ms": 500,
                "is_overlap": False,
            },
            {
                "speaker_id": "SPEAKER_01",
                "start_ms": 500,
                "end_ms": 1000,
                "is_overlap": False,
            },
        ],
        "backend": "nemo",
    }
    diarize.raise_for_status = MagicMock()

    client = MagicMock()
    client.get.return_value = health
    client.post.return_value = diarize
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("httpx.Client", return_value=client):
        intervals = diarizer.diarize(audio, sr)

    assert diarizer.backend == "nemo"
    assert len(intervals) == 2


def test_remote_empty_intervals_are_valid():
    """Silence / no-speech must not trigger heuristic fallback."""
    sr = 16000
    audio = (np.random.randn(sr) * 0.05).astype(np.float32)
    diarizer = SpeakerDiarizer(
        sample_rate=sr,
        remote_endpoint="http://127.0.0.1:8090",
        allow_fallback=False,
    )

    health = MagicMock()
    health.status_code = 200
    health.content = b'{"ready":true,"backend":"pyannote"}'
    health.json.return_value = {"ready": True, "backend": "pyannote"}

    diarize = MagicMock()
    diarize.status_code = 200
    diarize.json.return_value = {"intervals": [], "backend": "pyannote"}
    diarize.raise_for_status = MagicMock()

    client = MagicMock()
    client.get.return_value = health
    client.post.return_value = diarize
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("httpx.Client", return_value=client):
        intervals = diarizer.diarize(audio, sr)

    assert intervals == []
    assert diarizer.backend == "pyannote"


def test_remote_retries_on_503_then_succeeds():
    sr = 16000
    audio = (np.random.randn(sr) * 0.05).astype(np.float32)
    diarizer = SpeakerDiarizer(
        sample_rate=sr,
        remote_endpoint="http://127.0.0.1:8090",
        allow_fallback=False,
    )

    health = MagicMock()
    health.status_code = 200
    health.content = b'{"ready":true,"backend":"nemo"}'
    health.json.return_value = {"ready": True, "backend": "nemo"}

    loading = MagicMock()
    loading.status_code = 503
    loading.text = "model not ready"

    ok = MagicMock()
    ok.status_code = 200
    ok.json.return_value = {
        "intervals": [
            {
                "speaker_id": "SPEAKER_00",
                "start_ms": 0,
                "end_ms": 400,
                "is_overlap": False,
            }
        ],
        "backend": "nemo",
    }
    ok.raise_for_status = MagicMock()

    client = MagicMock()
    client.get.return_value = health
    client.post.side_effect = [loading, ok]
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("httpx.Client", return_value=client):
        with patch("api.meeting.diarization.time.sleep"):
            intervals = diarizer.diarize(audio, sr)

    assert len(intervals) == 1
    assert diarizer.backend == "nemo"
    assert client.post.call_count == 2


def test_allow_fallback_false_raises_when_remote_fails():
    sr = 16000
    audio = (np.random.randn(sr) * 0.05).astype(np.float32)
    diarizer = SpeakerDiarizer(
        sample_rate=sr,
        remote_endpoint="http://127.0.0.1:8090",
        allow_fallback=False,
    )

    health = MagicMock()
    health.status_code = 200
    health.content = b'{"ready":true,"backend":"pyannote"}'
    health.json.return_value = {"ready": True, "backend": "pyannote"}

    client = MagicMock()
    client.get.return_value = health
    client.post.side_effect = RuntimeError("sidecar down")
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("httpx.Client", return_value=client):
        with pytest.raises(RuntimeError, match="DIARIZATION_ALLOW_FALLBACK"):
            diarizer.diarize(audio, sr)
