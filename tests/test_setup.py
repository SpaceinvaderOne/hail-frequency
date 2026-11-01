"""Setup-step progression, including the Ollama skip sentinel, plus the
setup endpoints' URL validation, connection check and admin-session lock."""
import pytest
from fastapi.testclient import TestClient

from app import config, database

INVALID_URL = "Invalid URL — it must start with http:// or https://"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()


def _step1(client: TestClient):
    r = client.post(
        "/api/setup/auth", json={"username": "admin", "password": "password123"}
    )
    assert r.status_code == 200
    return r


def test_skip_sentinel_advances_step(temp_db):
    from app.routers.setup import _current_step
    config.set_setting("username", "admin")
    config.set_setting("speaches_url", "http://x:8000")
    assert _current_step() == 2
    config.set_setting("ollama_skipped", "true")
    assert _current_step() == 3


def test_ollama_url_still_advances_step(temp_db):
    from app.routers.setup import _current_step
    config.set_setting("username", "admin")
    config.set_setting("speaches_url", "http://x:8000")
    config.set_setting("ollama_url", "http://x:11434")
    assert _current_step() == 3


def test_setup_rejects_bad_urls(temp_db):
    """Every setup step that takes a service URL demands an http(s) scheme."""
    from app.main import app

    client = TestClient(app)
    _step1(client)

    r = client.post("/api/setup/speaches", json={"url": "not a url", "model": "x"})
    assert r.status_code == 400
    assert r.json() == {"error": INVALID_URL}

    r = client.post("/api/setup/ollama", json={"url": "not a url", "model": "x"})
    assert r.status_code == 400
    assert r.json() == {"error": INVALID_URL}

    r = client.post(
        "/api/setup/media-server",
        json={"name": "A", "type": "emby", "url": "not a url", "api_key": "k"},
    )
    assert r.status_code == 400
    assert r.json() == {"error": INVALID_URL}


def test_setup_media_server_requires_reachable_server(temp_db):
    """A media server is saved only after it answers; an unreachable one is
    rejected and nothing is persisted."""
    from app.main import app

    client = TestClient(app)
    _step1(client)

    r = client.post(
        "/api/setup/media-server",
        json={"name": "A", "type": "emby", "url": "http://127.0.0.1:9", "api_key": "k"},
    )
    assert r.status_code == 400
    assert r.json()["error"].startswith("Cannot connect")
    assert config.get_media_servers() == []


def test_setup_steps_locked_after_admin(temp_db):
    """Once the admin account exists, steps 2-5 require its session: a client
    without the cookie gets 401, the client that ran step 1 carries on."""
    from app.main import app

    client = TestClient(app)
    r = _step1(client)
    assert "hf_session=" in r.headers.get("set-cookie", "")

    body = {"url": "http://127.0.0.1:9", "model": "x"}
    anonymous = TestClient(app)
    r = anonymous.post("/api/setup/speaches", json=body)
    assert r.status_code == 401

    r = client.post("/api/setup/speaches", json=body)
    assert r.status_code != 401


def test_setup_auth_sets_cookie_with_admin_role(temp_db):
    """The cookie set by step 1 is a full admin session."""
    from app.main import app

    client = TestClient(app)
    _step1(client)

    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_first_media_server_is_default(temp_db, monkeypatch):
    """The first media server becomes the default even when not asked for."""
    from app.main import app
    from app.routers import setup as setup_router

    async def fake_test(url, server_type, api_key):
        return [], []

    monkeypatch.setattr(setup_router, "_test_media_connection", fake_test)

    client = TestClient(app)
    _step1(client)

    r = client.post(
        "/api/setup/media-server",
        json={
            "name": "A",
            "type": "emby",
            "url": "http://127.0.0.1:9",
            "api_key": "k",
            "is_default": False,
        },
    )
    assert r.status_code == 200
    assert config.get_default_server()["name"] == "A"


async def test_wizard_device_list_hides_unreachable_and_own_sessions(monkeypatch):
    """The wizard's device list offers only sessions a command can reach:
    powered-off ghosts (SupportsRemoteControl false) and the app's own
    API-key session (Client HailFrequency) are left out, matching the
    devices endpoint used after setup."""
    import httpx

    from app.routers import setup as setup_router

    sessions = [
        {"DeviceName": "Living Room TV", "Client": "Emby for Android TV",
         "UserName": "ed", "SupportsRemoteControl": True},
        {"DeviceName": "Old Tablet", "Client": "Emby Mobile",
         "UserName": "ed", "SupportsRemoteControl": False},
        {"DeviceName": "HailFrequency", "Client": "HailFrequency",
         "UserName": "", "SupportsRemoteControl": True},
        {"DeviceName": "HailFrequency", "Client": "test",
         "UserName": "", "SupportsRemoteControl": False},
    ]

    def handler(request):
        if request.url.path.endswith("/Users"):
            return httpx.Response(200, json=[{"Id": "u1", "Name": "ed"}])
        if request.url.path.endswith("/Sessions"):
            return httpx.Response(200, json=sessions)
        return httpx.Response(200, json={})

    real_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(setup_router.httpx, "AsyncClient", patched_client)

    users, devices = await setup_router._test_media_connection(
        "http://x", "emby", "k"
    )
    assert [u["name"] for u in users] == ["ed"]
    assert [d["device_name"] for d in devices] == ["Living Room TV"]
