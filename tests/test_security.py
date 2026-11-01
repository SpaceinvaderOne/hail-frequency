from types import SimpleNamespace
import json

import pytest
from pydantic import ValidationError

from app import config, database
from app.models import LoginRequest, SetupAuthRequest


def _req():
    return SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"), headers={},
        url=SimpleNamespace(scheme="http"))


def _ok(resp) -> bool:
    """setup_auth answers with a JSONResponse carrying the session cookie."""
    return json.loads(resp.body).get("success") is True


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()


def test_login_password_length_capped():
    with pytest.raises(ValidationError):
        LoginRequest(username="a", password="x" * 10_000)
    with pytest.raises(ValidationError):
        LoginRequest(username="u" * 10_000, password="pw")
    LoginRequest(username="admin", password="x" * 256)   # boundary OK


def test_setup_password_length_capped():
    with pytest.raises(ValidationError):
        SetupAuthRequest(password="x" * 10_000)
    SetupAuthRequest(password="goodpass")                 # min 8 still OK


async def test_setup_auth_claim_lock(temp_db):
    from app.routers.setup import setup_auth

    r1 = await setup_auth(
        SetupAuthRequest(username="admin", password="firstpass1"), _req())
    assert _ok(r1)

    # Second anonymous attempt without the existing password -> 403
    r2 = await setup_auth(
        SetupAuthRequest(username="intruder", password="attacker1"), _req())
    assert getattr(r2, "status_code", 200) == 403
    r2_body = json.loads(r2.body)
    assert r2_body.get("claim_required") is True

    # Wrong current_password -> 403
    r3 = await setup_auth(
        SetupAuthRequest(username="admin2", password="secondpass1",
                         current_password="wrongwrong"), _req())
    assert getattr(r3, "status_code", 200) == 403
    r3_body = json.loads(r3.body)
    assert r3_body.get("claim_required") is True

    # Correct current_password -> allowed (owner re-running step 1)
    r4 = await setup_auth(
        SetupAuthRequest(username="admin2", password="secondpass1",
                         current_password="firstpass1"), _req())
    assert _ok(r4)

    # And the new password is the one that now unlocks re-runs
    r5 = await setup_auth(
        SetupAuthRequest(username="admin3", password="thirdpass1",
                         current_password="secondpass1"), _req())
    assert _ok(r5)


def test_env_password_reset_signs_everyone_out(temp_db, monkeypatch):
    from app.auth import (
        PBKDF2_ITERATIONS, hash_password, maybe_reset_admin_password,
        verify_user_password,
    )
    h, s = hash_password("oldpassword")
    config.reset_local_admin("admin", h, s, PBKDF2_ITERATIONS)
    config.set_setting("hmac_key", "old-key")
    monkeypatch.setenv("HF_ADMIN_RESET_PASSWORD", "newpassword1")
    maybe_reset_admin_password()
    assert config.get_setting("hmac_key") != "old-key"
    assert verify_user_password(config.get_local_admin(), "newpassword1")


# ── Proxy-header trust and login throttling ─────────────────────────


def _xff_request(xff: str, peer: str = "testclient"):
    """A bare Starlette Request carrying X-Forwarded-For from the given peer."""
    from starlette.requests import Request

    return Request({
        "type": "http",
        "headers": [(b"x-forwarded-for", xff.encode())],
        "client": (peer, 1234),
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "scheme": "http",
        "server": ("h", 80),
    })


@pytest.fixture
def clean_login_failures(monkeypatch):
    from app.routers import auth as auth_router

    monkeypatch.setattr(auth_router, "_login_failures", {})


def test_xff_ignored_without_trusted_proxy(monkeypatch):
    """X-Forwarded-For from an untrusted peer must not override the peer IP."""
    from app import auth as auth_module
    from app.routers.auth import _client_ip

    monkeypatch.setattr(auth_module, "TRUSTED_PROXIES", frozenset())
    req = _xff_request("1.2.3.4")
    assert _client_ip(req) == req.client.host == "testclient"


def test_xff_honoured_from_trusted_proxy(monkeypatch):
    """A peer listed in HF_TRUSTED_PROXIES may supply the real client IP."""
    from app import auth as auth_module
    from app.routers.auth import _client_ip

    monkeypatch.setattr(auth_module, "TRUSTED_PROXIES", frozenset({"testclient"}))
    assert _client_ip(_xff_request("1.2.3.4")) == "1.2.3.4"


def test_ip_lockout_not_extended_while_locked(clean_login_failures):
    """Failures during an IP lockout must not push its release time out."""
    from app.routers import auth as auth_router

    for _ in range(auth_router.MAX_FAILURES):
        auth_router._record_failure("ip:a")
    count, locked_until = auth_router._login_failures["ip:a"]
    assert count == auth_router.MAX_FAILURES
    assert locked_until > 0
    assert auth_router._is_locked_out("ip:a")

    for _ in range(3):
        auth_router._record_failure("ip:a")
    assert auth_router._login_failures["ip:a"][1] == locked_until


def test_username_failures_do_not_lock(temp_db, clean_login_failures):
    """Repeated wrong passwords for one username slow it down but never lock
    it: the real owner can still sign in with the correct password."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import auth as auth_router

    client = TestClient(app)
    r = client.post(
        "/api/setup/auth", json={"username": "admin", "password": "password123"}
    )
    assert r.status_code == 200
    config.set_setting("setup_complete", "true")

    for _ in range(4):
        r = client.post(
            "/api/auth/login", json={"username": "admin", "password": "wrong-pass"}
        )
        assert r.status_code == 401

    # Fifth failure: the client IP reaches MAX_FAILURES and locks, which is
    # expected for the IP key; clear that one so only the username key remains
    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong-pass"}
    )
    assert r.status_code == 401
    auth_router._login_failures.pop("ip:testclient", None)
    assert auth_router._login_failures["user:admin"][0] == 5
    assert not auth_router._is_locked_out("user:admin")

    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "password123"}
    )
    assert r.status_code == 200


# ── Authorisation ───────────────────────────────────────────────────


def _admin_client():
    """A TestClient signed in as a freshly created local admin."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    r = client.post(
        "/api/setup/auth", json={"username": "admin", "password": "password123"}
    )
    assert r.status_code == 200
    config.set_setting("setup_complete", "true")
    return client


def _sign_in_as(client, username: str) -> None:
    from app.auth import COOKIE_NAME, create_session_token

    client.cookies.clear()
    client.cookies.set(
        COOKIE_NAME, create_session_token(username, config.get_setting("hmac_key"))
    )


def test_non_admin_cannot_list_service_models(temp_db):
    """Service-model listings and device aliases are admin reads: a standard
    user gets 403, the admin gets through (200, or 400/502 when the service
    is unconfigured or unreachable)."""
    paths = ["/api/speaches/models", "/api/ollama/models", "/api/devices"]
    client = _admin_client()
    for path in paths:
        assert client.get(path).status_code != 403, path

    config.create_user(username="bob", role="user")
    _sign_in_as(client, "bob")
    for path in paths:
        assert client.get(path).status_code == 403, path


def test_disabled_user_wrong_password_is_invalid_credentials(
    temp_db, clean_login_failures
):
    """A disabled account answers exactly like any other to a wrong password;
    only valid credentials reveal that it is disabled, and that answer is
    neither a throttle failure nor a session."""
    from app.auth import PBKDF2_ITERATIONS, hash_password
    from app.routers import auth as auth_router

    client = _admin_client()
    h, s = hash_password("davepass1")
    dave = config.create_user(
        username="dave", role="user", password_hash=h, password_salt=s,
        password_iterations=PBKDF2_ITERATIONS,
    )
    config.set_user_disabled(dave["id"], True)
    client.cookies.clear()

    r = client.post(
        "/api/auth/login", json={"username": "dave", "password": "wrong-pass"}
    )
    assert r.status_code == 401
    assert r.json() == {"error": "Invalid credentials"}
    assert auth_router._login_failures["user:dave"][0] == 1

    r = client.post(
        "/api/auth/login", json={"username": "dave", "password": "davepass1"}
    )
    assert r.status_code == 403
    assert r.json() == {"error": "Account disabled"}
    assert "set-cookie" not in r.headers
    assert auth_router._login_failures["user:dave"][0] == 1


async def test_delegated_probe_requires_access_token(temp_db, monkeypatch):
    """A media server that returns a user but no AccessToken has not given
    the app anything to act as that user with, so it is not an acceptance."""
    import httpx

    from app.routers.auth import _delegated_login

    config.add_media_server(
        name="Emby", server_type="emby", url="http://127.0.0.1:9", api_key="k"
    )

    async def fake_post(self, *args, **kwargs):
        return httpx.Response(200, json={"User": {"Id": "u1", "Name": "bob"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    assert await _delegated_login("bob", "pw") == []
