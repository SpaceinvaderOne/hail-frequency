import json
import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..adapters.base import media_auth_headers
from ..auth import (
    PBKDF2_ITERATIONS,
    hash_password,
    require_admin,
    verify_user_password,
)
from ..config import (
    add_device_alias,
    add_media_server,
    clear_device_aliases,
    clear_learned_phrases,
    count_learned_phrases,
    delete_media_server,
    evict_learned_phrase,
    get_device_aliases,
    get_learned_phrases,
    get_media_server,
    get_media_servers,
    get_setting,
    get_settings_batch,
    get_user_identities,
    redact_api_key,
    set_setting,
    update_media_server,
    update_user_password,
    update_user_theme,
)

# Attach to any route that only the admin may call
ADMIN = [Depends(require_admin)]
from ..logging_config import apply_debug_logging
from ..version import version_info
from ..models import (
    ChangePasswordRequest,
    DebugLoggingRequest,
    DeleteMediaServerRequest,
    LearnedPhraseDeleteRequest,
    LearnedVisibilityRequest,
    OllamaModelsRequest,
    ThemeRequest,
    SetupDeviceAliasesRequest,
    SetupMediaServerRequest,
    SetupOllamaRequest,
    SetupSpeachesRequest,
    SetupTTSRequest,
    TestTTSVoiceRequest,
    TestUrlRequest,
    UpdateMediaServerRequest,
)
from ..services.tts import TTSClient, store_tts_audio
from ..services.urls import clean_url as _clean_url

logger = logging.getLogger("hailfrequency.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ── Helpers ────────────────────────────────────────────────────────


def _verify_current_password(request: Request, password: str) -> bool:
    """Verify the requesting user's own (local) password for sensitive ops."""
    user = getattr(request.state, "user", None)
    return verify_user_password(user, password)


# ── Read settings ──────────────────────────────────────────────────


@router.get("")
async def get_settings(request: Request):
    """Settings payload, shaped by role.

    Non-admins get only what the app UI needs: their identity, their theme,
    and the servers they have accounts on (no URLs, keys, or service config).
    """
    user = request.state.user
    if user.get("role") != "admin":
        ident_servers = {i["server_id"] for i in get_user_identities(user["id"])}
        servers = [
            {
                "id": s["id"],
                "name": s["name"],
                "type": s["type"],
                "is_default": bool(s.get("is_default")),
            }
            for s in get_media_servers()
            if s["id"] in ident_servers
        ]
        vis = get_settings_batch(["ollama_engine", "learned_phrases_public"])
        return {
            "username": user["username"],
            "role": "user",
            "local": bool(user.get("password_hash")),
            "ui_theme": user.get("theme") or "lcars",
            "servers": servers,
            "ollama_engine": vis["ollama_engine"] or "unknown",
            "learned_phrases_viewable": vis["learned_phrases_public"] != "false",
            **version_info(),
        }

    servers = []
    for s in get_media_servers():
        aliases = get_device_aliases(s["id"])
        servers.append({
            "id": s["id"],
            "name": s["name"],
            "type": s["type"],
            "url": s["url"],
            "api_key_redacted": redact_api_key(s["api_key"]),
            "user_id": s.get("user_id"),
            "user_name": s.get("user_name"),
            "is_default": bool(s.get("is_default")),
            "aliases": aliases,
        })

    # One connection for all settings — this endpoint is hit on every
    # device-dropdown refresh, not just the Settings tab
    s = get_settings_batch([
        "speaches_url", "speaches_model", "ollama_url", "ollama_model",
        "ollama_keep_alive_enabled", "ollama_keep_alive_value", "debug_logging",
        "tts_enabled", "tts_url", "tts_model", "tts_voice", "ui_theme",
        "ollama_engine", "learned_phrases_public",
    ])
    return {
        "username": user["username"],
        "role": "admin",
        "local": bool(user.get("password_hash")),
        "speaches_url": s["speaches_url"],
        "speaches_model": s["speaches_model"],
        "ollama_url": s["ollama_url"],
        "ollama_model": s["ollama_model"],
        "ollama_keep_alive_enabled": s["ollama_keep_alive_enabled"] == "true",
        "ollama_keep_alive_value": s["ollama_keep_alive_value"] or "30s",
        "servers": servers,
        "debug_logging": s["debug_logging"] == "true",
        "tts_enabled": s["tts_enabled"] == "true",
        "tts_url": s["tts_url"],
        "tts_model": s["tts_model"],
        "tts_voice": s["tts_voice"],
        "ui_theme": user.get("theme") or s["ui_theme"] or "lcars",
        "learned_phrases_count": count_learned_phrases(),
        "ollama_engine": s["ollama_engine"] or "unknown",
        "learned_phrases_public": s["learned_phrases_public"] != "false",
        **version_info(),
    }


# ── Speaches ───────────────────────────────────────────────────────


@router.put("/speaches", dependencies=ADMIN)
async def update_speaches(body: SetupSpeachesRequest):
    url = _clean_url(body.url)
    if not url:
        return JSONResponse({"error": "Invalid URL"}, status_code=400)
    set_setting("speaches_url", url)
    set_setting("speaches_model", body.model)
    return {"success": True}


@router.post("/test-speaches", dependencies=ADMIN)
async def test_speaches(body: TestUrlRequest):
    test_url = _clean_url(body.url) or get_setting("speaches_url")
    if not test_url:
        return JSONResponse({"error": "Not configured"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{test_url}/v1/models")
            resp.raise_for_status()
        return {"connected": True}
    except Exception:
        return {"connected": False}


@router.get("/speaches/models", dependencies=ADMIN)
async def list_speaches_models():
    """Fetch available STT models from Speaches."""
    url = get_setting("speaches_url")
    if not url:
        return JSONResponse({"error": "Not configured"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/v1/models")
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return JSONResponse({"error": "Cannot connect to Speaches"}, status_code=502)

    models = []
    for m in data.get("data", []):
        if m.get("task") == "text-to-speech":
            continue
        models.append({"id": m.get("id", ""), "name": m.get("id", "")})
    return {"models": models}


# ── Ollama ─────────────────────────────────────────────────────────


@router.put("/ollama", dependencies=ADMIN)
async def update_ollama(body: SetupOllamaRequest):
    if not body.url or not body.url.strip():
        # Explicitly disabling the AI tier (no-GPU mode)
        set_setting("ollama_url", "")
        set_setting("ollama_model", "")
        set_setting("ollama_skipped", "true")
        set_setting("ollama_engine", "unknown")
        return {"success": True, "disabled": True}
    url = _clean_url(body.url)
    if not url:
        return JSONResponse({"error": "Invalid URL"}, status_code=400)
    set_setting("ollama_url", url)
    set_setting("ollama_model", body.model)
    set_setting("ollama_keep_alive_enabled", "true" if body.keep_alive_enabled else "false")
    set_setting("ollama_keep_alive_value", body.keep_alive_value)
    set_setting("ollama_skipped", "")
    set_setting("ollama_engine", "unknown")
    return {"success": True}


@router.post("/test-ollama", dependencies=ADMIN)
async def test_ollama(body: TestUrlRequest):
    test_url = _clean_url(body.url) or get_setting("ollama_url")
    if not test_url:
        return JSONResponse({"error": "Not configured"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{test_url}/api/tags")
            resp.raise_for_status()
        return {"connected": True}
    except Exception:
        return {"connected": False}


@router.post("/ollama/models", dependencies=ADMIN)
async def list_ollama_models(body: OllamaModelsRequest):
    """Model list for the settings edit form. Accepts the form's typed URL
    so the list works while nothing is stored (the re-enable path after
    'Disable AI parser'); falls back to the saved setting."""
    url = _clean_url(body.url) or get_setting("ollama_url")
    if not url:
        return JSONResponse({"error": "Not configured"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return JSONResponse({"error": "Cannot connect to Ollama"}, status_code=502)

    models = []
    for m in data.get("models", []):
        models.append({
            "name": m.get("name", ""),
            "size": m.get("size", 0),
        })
    return {"models": models}


@router.post("/learned-phrases/clear", dependencies=ADMIN)
async def clear_learned():
    return {"cleared": clear_learned_phrases()}


@router.get("/learned-phrases")
async def list_learned(request: Request):
    """Learned-phrase cache. Household-global; all users may view unless an
    admin turned learned_phrases_public off. Admins always allowed."""
    user = request.state.user
    if user.get("role") != "admin" \
            and get_setting("learned_phrases_public") == "false":
        return JSONResponse(
            {"error": "Viewing learned phrases is admin-only"},
            status_code=403,
        )
    out = []
    for row in get_learned_phrases():
        try:
            intent = json.loads(row["intent_json"])
            if not isinstance(intent, dict):
                intent = None
        except (TypeError, ValueError):
            intent = None
        out.append({
            "phrase": row["normalized_phrase"],
            "intent": intent,
            "use_count": row["use_count"],
            "last_used_at": row["last_used_at"],
        })
    return {"phrases": out}


@router.post("/learned-phrases/delete", dependencies=ADMIN)
async def delete_learned(body: LearnedPhraseDeleteRequest):
    evict_learned_phrase(body.phrase)
    return {"deleted": True}


@router.put("/learned-phrases/visibility", dependencies=ADMIN)
async def set_learned_visibility(body: LearnedVisibilityRequest):
    set_setting("learned_phrases_public", "true" if body.public else "false")
    return {"success": True}


# ── Media servers ──────────────────────────────────────────────────


@router.get("/test-media/{server_id}", dependencies=ADMIN)
async def test_media(server_id: str):
    server = get_media_server(server_id)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if server["type"] in ("emby", "jellyfin"):
                prefix = "/emby" if server["type"] == "emby" else ""
                resp = await client.get(
                    f"{server['url']}{prefix}/System/Info",
                    headers=media_auth_headers(server["api_key"]),
                )
                resp.raise_for_status()
            return {"connected": True}
    except Exception:
        return {"connected": False}


@router.get("/discover/{server_id}", dependencies=ADMIN)
async def discover_server(server_id: str):
    """Re-discover users and devices for an existing server using stored credentials."""
    server = get_media_server(server_id)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)

    users = []
    devices = []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if server["type"] in ("emby", "jellyfin"):
                prefix = "/emby" if server["type"] == "emby" else ""
                resp = await client.get(
                    f"{server['url']}{prefix}/Users",
                    headers=media_auth_headers(server["api_key"]),
                )
                resp.raise_for_status()
                for u in resp.json():
                    users.append({"id": u.get("Id", ""), "name": u.get("Name", "")})

                resp = await client.get(
                    f"{server['url']}{prefix}/Sessions",
                    headers=media_auth_headers(server["api_key"]),
                )
                resp.raise_for_status()
                for s in resp.json():
                    device_name = s.get("DeviceName", "")
                    if device_name:
                        devices.append({
                            "device_name": device_name,
                            "client": s.get("Client", ""),
                            "user": s.get("UserName", ""),
                        })

    except Exception as e:
        logger.warning("Discover failed for server %s: %s", server_id, e)
        return JSONResponse({"error": "Discovery failed"}, status_code=502)

    return {"success": True, "users": users, "devices": devices}


def _bad_media_url() -> JSONResponse:
    return JSONResponse(
        {"error": "Invalid server URL — it must start with http:// or https://"},
        status_code=400,
    )


@router.post("/test-media-server", dependencies=ADMIN)
async def test_media_server_connection(body: SetupMediaServerRequest):
    """Test media server connection and discover users/devices."""
    url = _clean_url(body.url)
    if not url:
        return _bad_media_url()
    server_type = body.type.lower()

    users = []
    devices = []

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if server_type in ("emby", "jellyfin"):
                prefix = "/emby" if server_type == "emby" else ""
                resp = await client.get(
                    f"{url}{prefix}/System/Info",
                    headers=media_auth_headers(body.api_key),
                )
                resp.raise_for_status()

                resp = await client.get(
                    f"{url}{prefix}/Users",
                    headers=media_auth_headers(body.api_key),
                )
                resp.raise_for_status()
                for u in resp.json():
                    users.append({
                        "id": u.get("Id", ""),
                        "name": u.get("Name", ""),
                    })

                resp = await client.get(
                    f"{url}{prefix}/Sessions",
                    headers=media_auth_headers(body.api_key),
                )
                resp.raise_for_status()
                for s in resp.json():
                    device_name = s.get("DeviceName", "")
                    client_name = s.get("Client", "")
                    if device_name:
                        devices.append({
                            "device_name": device_name,
                            "client": client_name,
                            "user": s.get("UserName", ""),
                        })

            else:
                return JSONResponse(
                    {"error": f"Unknown server type: {server_type}"},
                    status_code=400,
                )

    except httpx.HTTPStatusError as e:
        return JSONResponse(
            {"error": f"Connection failed: HTTP {e.response.status_code}"},
            status_code=400,
        )
    except Exception as e:
        logger.warning("Media server test failed: %s", e)
        return JSONResponse(
            {"error": f"Cannot connect to {server_type} at {url}"},
            status_code=400,
        )

    return {"success": True, "users": users, "devices": devices}


@router.post("/media-server", dependencies=ADMIN)
async def add_server(body: SetupMediaServerRequest):
    """Add a new media server."""
    url = _clean_url(body.url)
    if not url:
        return _bad_media_url()
    server_type = body.type.lower()

    server_id = add_media_server(
        name=body.name,
        server_type=server_type,
        url=url,
        api_key=body.api_key,
        user_id=body.user_id,
        user_name=body.user_name,
        is_default=body.is_default,
    )

    return {"success": True, "server_id": server_id}


@router.put("/media-server/{server_id}", dependencies=ADMIN)
async def update_server(server_id: str, body: UpdateMediaServerRequest, request: Request):
    """Update an existing media server. Requires password if changing API key."""
    server = get_media_server(server_id)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)

    # If API key is being changed, require password
    if body.api_key:
        if not body.current_password:
            return JSONResponse(
                {"error": "Password required to change API key"},
                status_code=403,
            )
        if not _verify_current_password(request, body.current_password):
            return JSONResponse(
                {"error": "Incorrect password"},
                status_code=403,
            )

    url = _clean_url(body.url)
    if not url:
        return _bad_media_url()
    fields = {
        "name": body.name,
        "url": url,
        "user_id": body.user_id,
        "user_name": body.user_name,
    }
    if body.is_default is not None:
        fields["is_default"] = 1 if body.is_default else 0
    if body.api_key:
        fields["api_key"] = body.api_key

    update_media_server(server_id, **fields)
    return {"success": True}


@router.delete("/media-server/{server_id}", dependencies=ADMIN)
async def remove_server(server_id: str, body: DeleteMediaServerRequest, request: Request):
    """Delete a media server. Requires password confirmation."""
    if not _verify_current_password(request, body.current_password):
        return JSONResponse(
            {"error": "Incorrect password"},
            status_code=403,
        )

    if not delete_media_server(server_id):
        return JSONResponse({"error": "Server not found"}, status_code=404)

    return {"success": True}


# ── Device aliases ─────────────────────────────────────────────────


@router.put("/device-aliases/{server_id}", dependencies=ADMIN)
async def update_device_aliases(server_id: str, body: SetupDeviceAliasesRequest):
    """Replace all device aliases for a server. The path server_id is authoritative."""
    server = get_media_server(server_id)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)
    if body.server_id and body.server_id != server_id:
        return JSONResponse(
            {"error": "server_id in body does not match URL"}, status_code=400
        )

    clear_device_aliases(server_id)
    for entry in body.aliases:
        add_device_alias(server_id, entry.alias, entry.pattern)

    return {"success": True, "aliases_saved": len(body.aliases)}


# ── Password ───────────────────────────────────────────────────────


@router.put("/password", dependencies=ADMIN)
async def change_password(body: ChangePasswordRequest, request: Request):
    """Change the LOCAL admin password. Invalidates all existing sessions.

    Delegated accounts (even promoted admins) have no local password —
    theirs belongs to the media server and is never touched here.
    """
    if not request.state.user.get("password_hash"):
        return JSONResponse(
            {"error": "Your password is managed by your media server, not HailFrequency."},
            status_code=400,
        )
    if not _verify_current_password(request, body.current_password):
        return JSONResponse(
            {"error": "Incorrect current password"},
            status_code=403,
        )

    pw_hash, salt = hash_password(body.new_password)
    # New random signing key — invalidates all existing sessions
    hmac_key = secrets.token_hex(32)

    update_user_password(
        request.state.user["id"], pw_hash, salt, PBKDF2_ITERATIONS
    )
    # Keep the settings-table copies in sync (the setup-progress check reads them)
    set_setting("password_hash", pw_hash)
    set_setting("password_salt", salt)
    set_setting("password_iterations", str(PBKDF2_ITERATIONS))
    set_setting("hmac_key", hmac_key)

    return {"success": True}


# ── TTS (Voice Response) ──────────────────────────────────────────


@router.put("/tts", dependencies=ADMIN)
async def update_tts(body: SetupTTSRequest):
    # URL may be empty when TTS is disabled; must be valid when provided
    url = _clean_url(body.url) if body.url else ""
    if body.url and not url:
        return JSONResponse({"error": "Invalid URL"}, status_code=400)
    if body.enabled and not url:
        return JSONResponse({"error": "URL required when TTS is enabled"}, status_code=400)
    set_setting("tts_enabled", "true" if body.enabled else "false")
    set_setting("tts_url", url or "")
    set_setting("tts_model", body.model)
    set_setting("tts_voice", body.voice)
    return {"success": True}


@router.post("/test-tts", dependencies=ADMIN)
async def test_tts(body: TestUrlRequest):
    test_url = _clean_url(body.url) or get_setting("tts_url")
    if not test_url:
        return JSONResponse({"error": "Not configured"}, status_code=400)
    client = TTSClient(test_url, "", "")
    try:
        connected = await client.test_connection()
        return {"connected": connected}
    finally:
        await client.close()


@router.post("/tts/models", dependencies=ADMIN)
async def list_tts_models(body: TestUrlRequest):
    """Fetch available TTS models from the configured service."""
    test_url = _clean_url(body.url) or get_setting("tts_url")
    if not test_url:
        return JSONResponse({"error": "Not configured"}, status_code=400)
    client = TTSClient(test_url, "", "")
    try:
        models = await client.list_models()
        return {"models": [{"id": m, "name": m} for m in models]}
    finally:
        await client.close()


@router.post("/tts/voices", dependencies=ADMIN)
async def list_tts_voices(body: TestUrlRequest):
    """Fetch available voices from the TTS service."""
    test_url = _clean_url(body.url) or get_setting("tts_url")
    if not test_url:
        return JSONResponse({"error": "Not configured"}, status_code=400)
    client = TTSClient(test_url, "", "")
    try:
        voices = await client.list_voices()
        return {"voices": voices}
    finally:
        await client.close()


@router.post("/test-tts-voice", dependencies=ADMIN)
async def test_tts_voice(body: TestTTSVoiceRequest):
    """Generate 'Hailing frequencies open' and return a playable audio URL."""
    url = _clean_url(body.url)
    if not url:
        return JSONResponse({"error": "Invalid URL"}, status_code=400)
    client = TTSClient(url, body.model, body.voice)
    try:
        audio_bytes = await client.synthesize("Hailing frequencies open")
        if not audio_bytes:
            return JSONResponse({"error": "TTS generation failed"}, status_code=502)
        audio_id = store_tts_audio(audio_bytes)
        return {"audio_url": f"/api/tts/audio/{audio_id}"}
    finally:
        await client.close()


# ── Appearance ────────────────────────────────────────────────────


@router.put("/theme")
async def update_theme(body: ThemeRequest, request: Request):
    """Set the requesting user's own UI theme (validated by the model)."""
    update_user_theme(request.state.user["id"], body.theme)
    return {"success": True, "ui_theme": body.theme}


# ── Debug logging ─────────────────────────────────────────────────


@router.put("/debug-logging", dependencies=ADMIN)
async def update_debug_logging(body: DebugLoggingRequest):
    """Toggle debug file logging on or off."""
    value = "true" if body.enabled else "false"
    set_setting("debug_logging", value)
    apply_debug_logging(body.enabled)
    return {"success": True, "debug_logging": body.enabled}
