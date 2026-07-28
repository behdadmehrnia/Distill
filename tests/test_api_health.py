import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app
from api.config import Settings


@pytest.fixture
async def client(tmp_path):
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
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "distill"


@pytest.mark.asyncio
async def test_create_meeting_without_start(client):
    resp = await client.post("/meetings", json={"title": "تست", "start": False})
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "تست"
    assert "id" in data

    listing = await client.get("/meetings")
    body = listing.json()
    assert any(m["id"] == data["id"] for m in body["meetings"])


@pytest.mark.asyncio
async def test_transcript_404(client):
    resp = await client.get("/meetings/does-not-exist/transcript")
    assert resp.status_code == 404
