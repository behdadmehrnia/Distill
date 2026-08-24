"""Pytest configuration for Distill."""

from __future__ import annotations

import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from api.app import create_app
from api.config import Settings
from api.db import connect

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DATABASE_URL = "postgresql://distill:distill@127.0.0.1:5432/distill"


def _base_database_url() -> str:
    return Settings.from_env().database_url or DEFAULT_DATABASE_URL


def _with_search_path(database_url: str, schema: str) -> str:
    parsed = urlparse(database_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["options"] = f"-csearch_path={schema}"
    return urlunparse(parsed._replace(query=urlencode(query)))


def create_test_schema(database_url: str | None = None) -> tuple[str, str]:
    """Create an isolated schema and return (url_with_search_path, schema_name)."""
    base = database_url or _base_database_url()
    schema = f"t_{uuid.uuid4().hex[:16]}"
    with connect(base) as conn:
        conn.execute(
            sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema))
        )
        conn.commit()
    return _with_search_path(base, schema), schema


def drop_test_schema(schema: str, database_url: str | None = None) -> None:
    base = database_url or _base_database_url()
    with connect(base) as conn:
        conn.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(schema)
            )
        )
        conn.commit()


@pytest.fixture(autouse=True)
def disable_upload_denoise_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable optional ONNX denoise for faster/stabler tests."""
    monkeypatch.setenv("AUDIO_DENOISE_UPLOAD", "0")


@pytest.fixture
def database_url() -> Iterator[str]:
    url, schema = create_test_schema()
    try:
        yield url
    finally:
        drop_test_schema(schema)


@contextmanager
def make_client(tmp_path, **settings_overrides) -> Iterator[TestClient]:
    base = Settings.from_env()
    url, schema = create_test_schema(base.database_url)
    settings = Settings(
        host="127.0.0.1",
        port=0,
        db_path=tmp_path / "meetings.db",
        database_url=url,
        upload_dir=tmp_path / "uploads",
        audio_dir=tmp_path / "audio",
        stt_cache_dir=tmp_path / "stt_cache",
        web_dir=base.web_dir,
        jwt_secret="test-secret-please-use-longer-key-for-jwt",
        **settings_overrides,
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        drop_test_schema(schema, base.database_url)


def register_user(
    client: TestClient,
    *,
    email: str = "user@test.com",
    password: str = "password123",
    display_name: str = "Test User",
) -> dict:
    resp = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": display_name,
        },
    )
    assert resp.status_code == 201
    return resp.json()["user"]
