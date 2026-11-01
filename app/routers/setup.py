import logging
import secrets

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..adapters.base import media_auth_headers
from ..auth import (
    PBKDF2_ITERATIONS,
    create_session_token,
    hash_password,
    set_session_cookie,
    verify_user_password,
)
from ..config import (
    add_device_alias,
    add_media_server,
    get_local_admin,
    get_media_server,
    get_media_servers,
    get_setting,
    is_setup_complete,
    reset_local_admin,
    set_setting,
)
from ..models import (
    SetupAuthRequest,
    SetupDeviceAliasesRequest,
    SetupMediaServerRequest,
    SetupOllamaRequest,
    SetupSpeachesRequest,
    SetupStatus,
)
from ..services.urls import clean_url

logger = logging.getLogger("hailfrequency.setup")

INVALID_URL_ERROR = "Invalid URL — it must start with http:// or https://"

router = APIRouter(prefix="/api/setup", tags=["setup"])


def _current_step() -> int:
    """Determine which setup step we're on based on stored settings."""
    if is_setup_complete():
        return 5
    if get_media_servers():
        return 4
    if get_setting("ollama_url") or get_setting("ollama_skipped") == "true":
        return 3
    if get_setting("speaches_url"):
        return 2
    if get_setting("username"):
        return 1
    return 0


def _check_not_complete():
    """Return a 403 response if setup is already complete, else None."""
    if is_setup_complete():
        return JSONResponse(
            {"error": "Setup is already complete"}, status_code=403
        )
    return None


def _log_setup_call(request: Request, step: str) -> None:
    """Setup endpoints are unauthenticated until setup completes — keep an
    audit trail of who is configuring the instance."""
    ip = request.client.host if request.client else "unknown"
    logger.warning("Setup call '%s' from %s (setup not yet complete)", step, ip)


# ── Status ──────────────────────────────────────────────────────────

@router.get("/status")
async def setup_status():
    return SetupStatus(
        setup_complete=is_setup_complete(),
        current_step=_current_step(),
    )


# ── Step 1: Authentication ──────────────────────────────────────────

@router.post("/auth")
async def setup_auth(body: SetupAuthRequest, request: Request):
    blocked = _check_not_complete()
    if blocked:
        return blocked
    _log_setup_call(request, "auth (set credentials)")

    # Claim-lock: once an admin password exists, re-running step 1 requires
    # it. Stops an anonymous caller hijacking a half-finished setup while
    # keeping legitimate wizard restarts (the owner knows the password).
    existing = get_local_admin()
    if existing and existing.get("password_hash"):
        if not body.current_password or not verify_user_password(
                existing, body.current_password):
            return JSONResponse(
                {"error": "An admin password is already set. Enter the "
                          "existing password to re-run this step.",
                 "claim_required": True},
                status_code=403,
            )

    pw_hash, salt = hash_password(body.password)
    # Random session-signing key — regenerated on password change so old
    # sessions are invalidated, but never derivable from the stored hash
    hmac_key = secrets.token_hex(32)

    # The admin is a local account in the users table; the settings copies
    # exist for the setup-progress check
    reset_local_admin(body.username, pw_hash, salt, PBKDF2_ITERATIONS)
    set_setting("username", body.username)
    set_setting("password_hash", pw_hash)
    set_setting("password_salt", salt)
    set_setting("password_iterations", str(PBKDF2_ITERATIONS))
    set_setting("hmac_key", hmac_key)

    # The remaining setup steps require this admin's session, so sign the
    # caller in now rather than sending them to the login screen mid-wizard
    response = JSONResponse({"success": True, "step": 1})
    set_session_cookie(
        response, create_session_token(body.username, hmac_key), request
    )
    return response


# ── Step 2: Speaches ────────────────────────────────────────────────

@router.post("/speaches")
async def setup_speaches(body: SetupSpeachesRequest, request: Request):
    blocked = _check_not_complete()
    if blocked:
        return blocked
    _log_setup_call(request, "speaches")

    url = clean_url(body.url)
    if url is None:
        return JSONResponse({"error": INVALID_URL_ERROR}, status_code=400)

    # Test connection
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/v1/models")
            resp.raise_for_status()
            models_data = resp.json()
    except Exception as e:
        logger.warning("Speaches connection failed: %s", e)
        return JSONResponse(
            {"error": f"Cannot connect to Speaches at {url}"},
            status_code=400,
        )

    set_setting("speaches_url", url)
    # The wizard calls this once to discover models (model="placeholder") and
    # again with the real pick — never persist the sentinel
    if body.model and body.model != "placeholder":
        set_setting("speaches_model", body.model)

    models = []
    for m in models_data.get("data", []):
        # Only show STT models, not TTS
        if m.get("task") == "text-to-speech":
            continue
        models.append({"id": m.get("id", ""), "name": m.get("id", "")})

    return {"success": True, "step": 2, "models": models}


# ── Step 3: Ollama ──────────────────────────────────────────────────

@router.post("/ollama")
async def setup_ollama(body: SetupOllamaRequest, request: Request):
    blocked = _check_not_complete()
    if blocked:
        return blocked
    _log_setup_call(request, "ollama")

    url = clean_url(body.url)
    if url is None:
        return JSONResponse({"error": INVALID_URL_ERROR}, status_code=400)

    # Test connection
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/api/tags")
            resp.raise_for_status()
            models_data = resp.json()
    except Exception as e:
        logger.warning("Ollama connection failed: %s", e)
        return JSONResponse(
            {"error": f"Cannot connect to Ollama at {url}"},
            status_code=400,
        )

    set_setting("ollama_url", url)
    set_setting("ollama_engine", "unknown")
    if body.model and body.model != "placeholder":
        set_setting("ollama_model", body.model)

    models = []
    for m in models_data.get("models", []):
        models.append({
            "name": m.get("name", ""),
            "size": m.get("size", 0),
        })

    return {"success": True, "step": 3, "models": models}


@router.post("/ollama/skip")
async def setup_ollama_skip(request: Request):
    """No-GPU path: proceed without an LLM. Pattern commands and
    play-by-title (search fallback) work; conversational phrases and
    spoken device targeting need Ollama."""
    blocked = _check_not_complete()
    if blocked:
        return blocked
    _log_setup_call(request, "ollama (skipped - no GPU)")
    set_setting("ollama_skipped", "true")
    set_setting("ollama_engine", "unknown")
    return {"success": True, "step": 3}


# ── Step 4: Media Server ────────────────────────────────────────────


async def _test_media_connection(url: str, server_type: str, api_key: str):
    """Test connection and discover users/devices. Returns (users, devices) or raises."""
    users = []
    devices = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        if server_type in ("emby", "jellyfin"):
            prefix = "/emby" if server_type == "emby" else ""
            resp = await client.get(
                f"{url}{prefix}/System/Info",
                headers=media_auth_headers(api_key),
            )
            resp.raise_for_status()

            resp = await client.get(
                f"{url}{prefix}/Users",
                headers=media_auth_headers(api_key),
            )
            resp.raise_for_status()
            for u in resp.json():
                users.append({
                    "id": u.get("Id", ""),
                    "name": u.get("Name", ""),
                })

            resp = await client.get(
                f"{url}{prefix}/Sessions",
                headers=media_auth_headers(api_key),
            )
            resp.raise_for_status()
            for s in resp.json():
                # Same filter as the devices endpoint: skip sessions commands
                # cannot reach (powered-off ghosts) and the app's own API-key
                # session, which would otherwise be offered as a device.
                if not s.get("SupportsRemoteControl"):
                    continue
                if (s.get("Client") or "").lower() == "hailfrequency":
                    continue
                device_name = s.get("DeviceName", "")
                client_name = s.get("Client", "")
                if device_name:
                    devices.append({
                        "device_name": device_name,
                        "client": client_name,
                        "user": s.get("UserName", ""),
                    })

        else:
            raise ValueError(f"Unknown server type: {server_type}")

    return users, devices


@router.post("/test-media-server")
async def test_media_server(body: SetupMediaServerRequest, request: Request):
    """Test connection only — does NOT save to DB."""
    blocked = _check_not_complete()
    if blocked:
        return blocked
    _log_setup_call(request, "test-media-server")

    url = clean_url(body.url)
    if url is None:
        return JSONResponse({"error": INVALID_URL_ERROR}, status_code=400)
    server_type = body.type.lower()

    try:
        users, devices = await _test_media_connection(url, server_type, body.api_key)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except httpx.HTTPStatusError as e:
        logger.warning("Media server connection failed (HTTP %s): %s", e.response.status_code, e)
        return JSONResponse(
            {"error": f"Connection failed: HTTP {e.response.status_code}"},
            status_code=400,
        )
    except Exception as e:
        logger.warning("Media server connection failed: %s", e)
        return JSONResponse(
            {"error": f"Cannot connect to {server_type} at {url}"},
            status_code=400,
        )

    return {"success": True, "users": users, "devices": devices}


@router.post("/media-server")
async def setup_media_server(body: SetupMediaServerRequest, request: Request):
    """Save media server to DB (called after user selects a user account)."""
    blocked = _check_not_complete()
    if blocked:
        return blocked
    _log_setup_call(request, "media-server")

    url = clean_url(body.url)
    if url is None:
        return JSONResponse({"error": INVALID_URL_ERROR}, status_code=400)
    server_type = body.type.lower()

    # Prove the server answers before persisting it — an unreachable server
    # saved here would leave the wizard "complete" with nothing to control
    try:
        await _test_media_connection(url, server_type, body.api_key)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except httpx.HTTPStatusError as e:
        logger.warning("Media server connection failed (HTTP %s): %s", e.response.status_code, e)
        return JSONResponse(
            {"error": f"Connection failed: HTTP {e.response.status_code}"},
            status_code=400,
        )
    except Exception as e:
        logger.warning("Media server connection failed: %s", e)
        return JSONResponse(
            {"error": f"Cannot connect to {server_type} at {url}"},
            status_code=400,
        )

    server_id = add_media_server(
        name=body.name,
        server_type=server_type,
        url=url,
        api_key=body.api_key,
        user_id=body.user_id,
        user_name=body.user_name,
        # The first server is always the default; later ones only when asked
        is_default=body.is_default or not get_media_servers(),
    )

    return {
        "success": True,
        "step": 4,
        "server_id": server_id,
    }


# ── Device Aliases (part of Step 4) ─────────────────────────────────

@router.post("/device-aliases")
async def setup_device_aliases(body: SetupDeviceAliasesRequest, request: Request):
    blocked = _check_not_complete()
    if blocked:
        return blocked
    _log_setup_call(request, "device-aliases")

    server = get_media_server(body.server_id)
    if not server:
        return JSONResponse(
            {"error": "Server not found"}, status_code=404
        )

    for entry in body.aliases:
        add_device_alias(body.server_id, entry.alias, entry.pattern)

    return {"success": True, "aliases_added": len(body.aliases)}


# ── Step 5: Complete ────────────────────────────────────────────────

@router.post("/complete")
async def setup_complete(request: Request):
    blocked = _check_not_complete()
    if blocked:
        return blocked
    _log_setup_call(request, "complete")

    # Verify all required settings exist
    missing = []
    if not get_setting("username"):
        missing.append("authentication")
    if not get_setting("speaches_url"):
        missing.append("Speaches")
    if not get_setting("ollama_url") \
            and get_setting("ollama_skipped") != "true":
        missing.append("Ollama")
    if not get_media_servers():
        missing.append("media server")

    if missing:
        return JSONResponse(
            {"error": f"Setup incomplete. Missing: {', '.join(missing)}"},
            status_code=400,
        )

    set_setting("setup_complete", "true")
    return {"success": True, "step": 5}


