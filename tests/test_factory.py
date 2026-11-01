"""Adapter construction: which media-server credentials a request runs as."""

import pytest

from app import database
from app.adapters.factory import create_adapter

SERVER = {
    "id": "s1",
    "name": "Test",
    "type": "emby",
    "url": "http://127.0.0.1:9",
    "api_key": "server-key",
    "user_id": "admin-uid",
}


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()


def test_identity_without_token_raises(temp_db):
    """An identity with no stored token must be refused, never quietly run
    on the server's configured credentials."""
    for ident in (
        {"media_user_id": "u1", "media_token": None},
        {"media_user_id": "u1", "media_token": ""},
        {"media_user_id": "u1"},
    ):
        with pytest.raises(ValueError, match="sign out and back in"):
            create_adapter(SERVER, ident)


def test_identity_with_token_uses_it(temp_db):
    adapter = create_adapter(
        SERVER, {"media_user_id": "u1", "media_token": "user-token"}
    )
    assert adapter.api_key == "user-token"
    assert adapter.user_id == "u1"
    assert adapter.client.headers["X-Emby-Token"] == "user-token"


def test_local_admin_without_identity_uses_server_key(temp_db):
    adapter = create_adapter(SERVER, None)
    assert adapter.api_key == "server-key"
    assert adapter.user_id == "admin-uid"
