from fastapi.testclient import TestClient

from tests.conftest import make_client, register_user


def test_register_login_me_logout(tmp_path):
    with make_client(tmp_path) as client:
        reg = client.post(
            "/auth/register",
            json={
                "email": "alice@test.com",
                "password": "password123",
                "display_name": "Alice",
            },
        )
        assert reg.status_code == 201
        assert reg.json()["user"]["email"] == "alice@test.com"
        assert "access_token" in reg.cookies

        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["display_name"] == "Alice"

        client.post("/auth/logout")
        assert client.get("/auth/me").status_code == 401

        login = client.post(
            "/auth/login",
            json={"email": "alice@test.com", "password": "password123"},
        )
        assert login.status_code == 200
        assert login.json()["user"]["email"] == "alice@test.com"


def test_dashboard_pages_use_optional_auth(tmp_path):
    with make_client(tmp_path) as client:
        anon = client.get("/dashboard", follow_redirects=False)
        assert anon.status_code == 302
        assert "/login" in anon.headers["location"]

        register_user(client)
        ok = client.get("/dashboard", follow_redirects=False)
        assert ok.status_code == 200
        assert "text/html" in ok.headers["content-type"]


def test_login_invalid_credentials(tmp_path):
    with make_client(tmp_path) as client:
        register_user(client, email="bob@test.com")
        bad = client.post(
            "/auth/login",
            json={"email": "bob@test.com", "password": "wrong-password"},
        )
        assert bad.status_code == 401


def test_meetings_require_auth(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/meetings").status_code == 401
        assert client.post("/meetings", json={"start": False}).status_code == 401
        assert client.get("/meetings/x/transcript").status_code == 401
        assert client.get("/meetings/x/debug").status_code == 401
        assert client.post("/meetings/x/start").status_code == 401
        assert client.get("/meetings/x/recording").status_code == 401
        assert client.get("/tuning").status_code == 401
        assert client.put("/tuning", json={"values": {}}).status_code == 401
        assert client.post("/tuning/reset").status_code == 401


def test_swagger_declares_auth_schemes(tmp_path):
    with make_client(tmp_path) as client:
        spec = client.get("/openapi.json").json()
        schemes = spec["components"]["securitySchemes"]
        cookie = next(
            (
                s
                for s in schemes.values()
                if s.get("type") == "apiKey" and s.get("name") == "access_token"
            ),
            None,
        )
        assert cookie is not None
        bearer = next(
            (
                s
                for s in schemes.values()
                if s.get("type") == "http" and s.get("scheme") == "bearer"
            ),
            None,
        )
        assert bearer is not None
        for path, method in (
            ("/tuning", "get"),
            ("/meetings", "get"),
            ("/meetings/{meeting_id}/transcript", "get"),
        ):
            op = spec["paths"][path][method]
            assert op.get("security"), path


def test_user_scoped_meetings(tmp_path):
    with make_client(tmp_path) as client:
        register_user(client, email="owner@test.com")
        created = client.post("/meetings", json={"title": "mine", "start": False})
        assert created.status_code == 201
        meeting_id = created.json()["id"]
        assert created.json()["user_id"] is not None

        listing = client.get("/meetings")
        assert len(listing.json()["meetings"]) == 1

        deleted = client.delete(f"/meetings/{meeting_id}")
        assert deleted.status_code == 204
        assert client.get("/meetings").json()["meetings"] == []


def test_meeting_not_visible_to_other_user(tmp_path):
    with make_client(tmp_path) as client:
        register_user(client, email="a@test.com")
        meeting_id = client.post(
            "/meetings", json={"title": "private", "start": False}
        ).json()["id"]

        client.post("/auth/logout")
        register_user(client, email="b@test.com", password="password456")

        assert client.get(f"/meetings/{meeting_id}").status_code == 404


def test_oauth_sso_stubs_not_implemented(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/auth/oauth/google").status_code == 501
        assert client.get("/auth/oauth/google/callback").status_code == 501
        assert client.get("/auth/sso/login").status_code == 501
        assert client.post("/auth/sso/callback").status_code == 501
