from fastapi.testclient import TestClient

from api.app import create_app
from api.config import Settings
from tests.conftest import (
    create_test_schema,
    drop_test_schema,
    make_client,
    register_user,
)


def test_admin_bootstrap_creates_admin_when_none_exists(tmp_path):
    with make_client(
        tmp_path,
        admin_username="admin@test.com",
        admin_password="adminpass123",
    ) as client:
        login = client.post(
            "/auth/login",
            json={"email": "admin@test.com", "password": "adminpass123"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["role"] == "admin"


def test_admin_bootstrap_promotes_existing_user(tmp_path):
    """A user who already registered with ADMIN_USERNAME's email is promoted
    to admin on the next startup, keeping her own password."""
    base = Settings.from_env()
    url, schema = create_test_schema(base.database_url)
    try:

        def _settings(**overrides) -> Settings:
            return Settings(
                host="127.0.0.1",
                port=0,
                db_path=tmp_path / "meetings.db",
                database_url=url,
                upload_dir=tmp_path / "uploads",
                audio_dir=tmp_path / "audio",
                stt_cache_dir=tmp_path / "stt_cache",
                web_dir=base.web_dir,
                jwt_secret="test-secret-please-use-longer-key-for-jwt",
                **overrides,
            )

        with TestClient(create_app(_settings())) as first_boot:
            register_user(
                first_boot,
                email="alice@test.com",
                password="alice-own-password",
            )

        with TestClient(
            create_app(
                _settings(
                    admin_username="alice@test.com",
                    admin_password="ignored-because-alice-already-exists",
                )
            )
        ) as second_boot:
            login = second_boot.post(
                "/auth/login",
                json={"email": "alice@test.com", "password": "alice-own-password"},
            )
            assert login.status_code == 200
            assert login.json()["user"]["role"] == "admin"
    finally:
        drop_test_schema(schema, base.database_url)


def test_admin_bootstrap_noop_without_credentials(tmp_path):
    with make_client(tmp_path) as client:
        register_user(client, email="first@test.com")
        me = client.get("/auth/me").json()["user"]
        assert me["role"] == "user"


def test_admin_bootstrap_noop_once_admin_exists(tmp_path):
    """Simulates a restart with a *different* ADMIN_USERNAME: since an admin
    already exists, that second email must NOT also become an admin."""
    base = Settings.from_env()
    url, schema = create_test_schema(base.database_url)
    try:

        def _settings(**overrides) -> Settings:
            return Settings(
                host="127.0.0.1",
                port=0,
                db_path=tmp_path / "meetings.db",
                database_url=url,
                upload_dir=tmp_path / "uploads",
                audio_dir=tmp_path / "audio",
                stt_cache_dir=tmp_path / "stt_cache",
                web_dir=base.web_dir,
                jwt_secret="test-secret-please-use-longer-key-for-jwt",
                **overrides,
            )

        with TestClient(
            create_app(
                _settings(
                    admin_username="first-admin@test.com",
                    admin_password="adminpass123",
                )
            )
        ):
            pass  # first boot bootstraps first-admin@test.com

        with TestClient(
            create_app(
                _settings(
                    admin_username="second-admin@test.com",
                    admin_password="whatever123",
                )
            )
        ) as second_boot:
            # second-admin@test.com must not exist / not be an admin.
            login = second_boot.post(
                "/auth/login",
                json={"email": "second-admin@test.com", "password": "whatever123"},
            )
            assert login.status_code == 401

            admin_login = second_boot.post(
                "/auth/login",
                json={"email": "first-admin@test.com", "password": "adminpass123"},
            )
            assert admin_login.status_code == 200
            assert admin_login.json()["user"]["role"] == "admin"
    finally:
        drop_test_schema(schema, base.database_url)


def test_non_admin_cannot_access_admin_routes(tmp_path):
    with make_client(tmp_path) as client:
        register_user(client, email="plain@test.com")
        assert client.get("/admin/users").status_code == 403
        assert client.get("/admin", follow_redirects=False).status_code == 302


def test_anonymous_cannot_access_admin_routes(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/admin/users").status_code == 401
        resp = client.get("/admin", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]


def test_admin_can_list_and_change_roles(tmp_path):
    with make_client(
        tmp_path,
        admin_username="admin@test.com",
        admin_password="adminpass123",
    ) as client:
        client.post(
            "/auth/login",
            json={"email": "admin@test.com", "password": "adminpass123"},
        )
        other = register_user(client, email="member@test.com")
        # register_user leaves the "member" account logged in; log back in as admin.
        client.post(
            "/auth/login",
            json={"email": "admin@test.com", "password": "adminpass123"},
        )

        listing = client.get("/admin/users")
        assert listing.status_code == 200
        emails = {u["email"] for u in listing.json()["users"]}
        assert {"admin@test.com", "member@test.com"} <= emails

        promoted = client.patch(
            f"/admin/users/{other['id']}/role", json={"role": "admin"}
        )
        assert promoted.status_code == 200
        assert promoted.json()["user"]["role"] == "admin"

        demoted = client.patch(
            f"/admin/users/{other['id']}/role", json={"role": "user"}
        )
        assert demoted.status_code == 200
        assert demoted.json()["user"]["role"] == "user"


def test_admin_cannot_demote_the_last_admin(tmp_path):
    with make_client(
        tmp_path,
        admin_username="admin@test.com",
        admin_password="adminpass123",
    ) as client:
        me = client.post(
            "/auth/login",
            json={"email": "admin@test.com", "password": "adminpass123"},
        ).json()["user"]

        resp = client.patch(f"/admin/users/{me['id']}/role", json={"role": "user"})
        assert resp.status_code == 400


def test_admin_cannot_deactivate_self(tmp_path):
    with make_client(
        tmp_path,
        admin_username="admin@test.com",
        admin_password="adminpass123",
    ) as client:
        me = client.post(
            "/auth/login",
            json={"email": "admin@test.com", "password": "adminpass123"},
        ).json()["user"]

        resp = client.patch(
            f"/admin/users/{me['id']}/active", json={"is_active": False}
        )
        assert resp.status_code == 400


def test_admin_can_deactivate_other_user(tmp_path):
    with make_client(
        tmp_path,
        admin_username="admin@test.com",
        admin_password="adminpass123",
    ) as client:
        client.post(
            "/auth/login",
            json={"email": "admin@test.com", "password": "adminpass123"},
        )
        other = register_user(client, email="member@test.com")
        client.post(
            "/auth/login",
            json={"email": "admin@test.com", "password": "adminpass123"},
        )

        resp = client.patch(
            f"/admin/users/{other['id']}/active", json={"is_active": False}
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["is_active"] is False

        blocked = client.post(
            "/auth/login",
            json={"email": "member@test.com", "password": "password123"},
        )
        assert blocked.status_code == 403


def test_admin_page_visible_to_admin(tmp_path):
    with make_client(
        tmp_path,
        admin_username="admin@test.com",
        admin_password="adminpass123",
    ) as client:
        client.post(
            "/auth/login",
            json={"email": "admin@test.com", "password": "adminpass123"},
        )
        resp = client.get("/admin", follow_redirects=False)
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
