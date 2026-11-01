"""Media-server authentication headers and poll-path error handling."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, database
from app.adapters.base import media_auth_headers
from app.adapters.emby import EmbyAdapter

APP_DIR = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()


def test_media_auth_headers_shape():
    headers = media_auth_headers("abc")
    assert set(headers.keys()) == {"Authorization", "X-Emby-Token"}
    assert headers["Authorization"].startswith('MediaBrowser Token="abc", ')
    assert headers["X-Emby-Token"] == "abc"


def test_adapter_client_sends_authorization():
    adapter = EmbyAdapter(url="http://x", api_key="k", user_id="", device_aliases=[])
    assert adapter.client.headers["Authorization"].startswith('MediaBrowser Token="k"')
    assert adapter.client.headers["X-Emby-Token"] == "k"


def test_no_bare_x_emby_token_in_source():
    """Every outbound media-server request must go through media_auth_headers,
    so the header name appears in exactly one source file."""
    hits = sorted(
        str(p.relative_to(APP_DIR.parent))
        for p in APP_DIR.rglob("*.py")
        if "X-Emby-Token" in p.read_text(encoding="utf-8")
    )
    assert hits == ["app/adapters/base.py"]


def test_now_playing_swallows_adapter_errors(temp_db):
    """An unreachable media server is a normal condition for the continuously
    polling now-playing card: HTTP 200 with a null payload, never a 500."""
    from app.main import app

    client = TestClient(app)
    r = client.post(
        "/api/setup/auth", json={"username": "admin", "password": "password123"}
    )
    assert r.status_code == 200
    config.set_setting("speaches_url", "http://127.0.0.1:9")
    config.set_setting("ollama_skipped", "true")
    config.add_media_server(
        name="Dead", server_type="emby", url="http://127.0.0.1:9", api_key="k"
    )
    config.set_setting("setup_complete", "true")

    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "password123"}
    )
    assert r.status_code == 200

    r = client.get("/api/now-playing")
    assert r.status_code == 200
    assert r.json() == {"now_playing": None}
