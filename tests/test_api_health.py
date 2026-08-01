from fastapi.testclient import TestClient

from api.app import create_app
from api.config import Settings


def _make_client(tmp_path) -> TestClient:
    base = Settings.from_env()
    settings = Settings(
        host="127.0.0.1",
        port=0,
        db_path=tmp_path / "meetings.db",
        upload_dir=tmp_path / "uploads",
        audio_dir=tmp_path / "audio",
        stt_cache_dir=tmp_path / "stt_cache",
        web_dir=base.web_dir,
    )
    app = create_app(settings)
    return TestClient(app)


def test_health(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "distill"
        assert data["status"] == "ok"
        assert "diarization_backend" in data
        assert data["diarization_backend"] in {
            "unloaded",
            "loading",
            "fallback",
            "pyannote",
        }
        assert "diarization_ready" in data
        assert data["diarization_quality"] in {"high", "fallback"}


def test_create_meeting_without_start(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.post("/meetings", json={"title": "تست", "start": False})
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "تست"
        assert "id" in data

        listing = client.get("/meetings")
        body = listing.json()
        assert any(m["id"] == data["id"] for m in body["meetings"])


def test_transcript_404(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.get("/meetings/does-not-exist/transcript")
        assert resp.status_code == 404


def test_meeting_debug_endpoint(tmp_path):
    with _make_client(tmp_path) as client:
        created = client.post("/meetings", json={"title": "دیباگ", "start": False})
        assert created.status_code == 201
        meeting_id = created.json()["id"]
        resp = client.get(f"/meetings/{meeting_id}/debug")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meeting_id"] == meeting_id
        assert "stt_calls" in data
        assert "tuning" in data
        assert "diarization_backend" in data


def test_speaker_map_patch(tmp_path):
    with _make_client(tmp_path) as client:
        created = client.post("/meetings", json={"title": "نام‌گذاری", "start": False})
        meeting_id = created.json()["id"]
        resp = client.patch(
            f"/meetings/{meeting_id}/speakers",
            json={"SPEAKER_00": "علی"},
        )
        assert resp.status_code == 200
        assert resp.json()["speaker_map"]["SPEAKER_00"] == "علی"
        again = client.get(f"/meetings/{meeting_id}")
        assert again.json()["speaker_map"]["SPEAKER_00"] == "علی"


def test_recording_endpoint_and_restart(tmp_path):
    with _make_client(tmp_path) as client:
        created = client.post("/meetings", json={"title": "ضبط", "start": False})
        meeting_id = created.json()["id"]

        missing = client.get(f"/meetings/{meeting_id}/recording")
        assert missing.status_code == 404

        meta = client.get(f"/meetings/{meeting_id}")
        assert meta.json()["has_recording"] is False

        audio_dir = tmp_path / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        wav_path = audio_dir / f"{meeting_id}.wav"
        wav_path.write_bytes(
            b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
            b"\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00"
            b"\x02\x00\x10\x00data\x00\x00\x00\x00"
        )

        meeting = client.app.state.manager.store.get_meeting(meeting_id)
        meeting.audio_path = str(wav_path)
        client.app.state.manager.store.save_meeting(meeting)

        again = client.get(f"/meetings/{meeting_id}")
        assert again.json()["has_recording"] is True

        recording = client.get(f"/meetings/{meeting_id}/recording")
        assert recording.status_code == 200
        assert recording.headers["content-type"].startswith("audio/")

        blocked = client.post(f"/meetings/{meeting_id}/start", json={})
        assert blocked.status_code == 409

        started = client.post(
            f"/meetings/{meeting_id}/start", json={"reset": True}
        )
        assert started.status_code == 200
        body = started.json()
        assert body["status"] == "recording"
        assert body["has_recording"] is False
        assert not wav_path.exists()

        stopped = client.post(f"/meetings/{meeting_id}/stop")
        assert stopped.status_code == 200

