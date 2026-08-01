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
