"""Remote diarization client (mocked HTTP)."""

from unittest.mock import MagicMock, patch

import numpy as np

from api.meeting.diarization import SpeakerDiarizer


def test_remote_diarize_uses_http_endpoint():
    sr = 16000
    audio = (np.random.randn(sr) * 0.05).astype(np.float32)
    diarizer = SpeakerDiarizer(
        sample_rate=sr,
        remote_endpoint="http://127.0.0.1:8090",
        hf_token=None,
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
