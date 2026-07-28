import pytest

from api.app import create_app
from api.config import Settings


@pytest.fixture
async def client(tmp_path, aiohttp_client):
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
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["service"] == "distill"


@pytest.mark.asyncio
async def test_create_meeting_without_start(client):
    resp = await client.post("/meetings", json={"title": "تست", "start": False})
    assert resp.status == 201
    data = await resp.json()
    assert data["title"] == "تست"
    assert "id" in data

    listing = await client.get("/meetings")
    body = await listing.json()
    assert any(m["id"] == data["id"] for m in body["meetings"])


@pytest.mark.asyncio
async def test_transcript_404(client):
    resp = await client.get("/meetings/does-not-exist/transcript")
    assert resp.status == 404
