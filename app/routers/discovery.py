import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..adapters.base import media_auth_headers
from ..auth import require_admin
from ..config import get_identity, get_media_server, get_setting

logger = logging.getLogger("hailfrequency.discovery")

router = APIRouter(tags=["discovery"])

ADMIN = [Depends(require_admin)]


@router.get("/api/speaches/models", dependencies=ADMIN)
async def list_speaches_models():
    url = get_setting("speaches_url")
    if not url:
        return JSONResponse({"error": "Speaches not configured"}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/v1/models")
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return JSONResponse({"error": "Cannot connect to Speaches"}, status_code=502)

    # Only show STT models, not TTS
    models = [
        {"id": m.get("id", "")}
        for m in data.get("data", [])
        if m.get("task") != "text-to-speech"
    ]
    return {"models": models}


@router.get("/api/ollama/models", dependencies=ADMIN)
async def list_ollama_models():
    url = get_setting("ollama_url")
    if not url:
        return JSONResponse({"error": "Ollama not configured"}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return JSONResponse({"error": "Cannot connect to Ollama"}, status_code=502)

    models = [{"name": m.get("name", ""), "size": m.get("size", 0)} for m in data.get("models", [])]
    return {"models": models}


@router.get("/api/media/{server_id}/devices")
async def list_media_devices(server_id: str, request: Request):
    server = get_media_server(server_id)
    if not server:
        return JSONResponse({"error": "Server not found"}, status_code=404)

    # Whose sessions to show: anyone with a linked identity sees their own
    # (incl. delegated admins); the local admin falls back to the server's
    # configured user; regular users without an identity see nothing
    user = getattr(request.state, "user", None)
    filter_uid = server.get("user_id")
    if user:
        ident = get_identity(user["id"], server_id)
        if ident:
            filter_uid = ident["media_user_id"]
        elif user.get("role") != "admin":
            return {"devices": []}

    devices = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if server["type"] in ("emby", "jellyfin"):
                prefix = "/emby" if server["type"] == "emby" else ""
                resp = await client.get(
                    f"{server['url']}{prefix}/Sessions",
                    headers=media_auth_headers(server["api_key"]),
                )
                resp.raise_for_status()
                for s in resp.json():
                    # Only show sessions for the scoped user if one is set
                    if filter_uid and s.get("UserId") != filter_uid:
                        continue
                    # Hide sessions commands can't reach: ghosts of powered-off
                    # devices (same filter as _pick_session_full), and the app's
                    # OWN delegated-login sessions — offering "HailFrequency"
                    # as a target device means commanding ourselves
                    if not s.get("SupportsRemoteControl"):
                        continue
                    if (s.get("Client") or "").lower() == "hailfrequency":
                        continue
                    device_name = s.get("DeviceName", "")
                    if device_name:
                        np = s.get("NowPlayingItem", {})
                        devices.append({
                            "device_name": device_name,
                            "client": s.get("Client", ""),
                            "now_playing": np.get("Name") if np else None,
                        })

    except Exception as e:
        logger.warning("Failed to fetch devices from %s: %s", server["name"], e)
        return JSONResponse(
            {"error": f"Cannot connect to {server['name']}"}, status_code=502
        )

    return {"devices": devices}
