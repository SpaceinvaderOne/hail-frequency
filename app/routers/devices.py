from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ..auth import require_admin
from ..config import (
    add_device_alias,
    get_all_device_aliases,
    get_media_server,
    remove_device_alias,
)
from ..models import DeviceAlias

router = APIRouter(tags=["devices"])

ADMIN = [Depends(require_admin)]


@router.get("/api/devices", dependencies=ADMIN)
async def list_devices():
    return get_all_device_aliases()


@router.post("/api/devices", dependencies=ADMIN)
async def add_device(body: DeviceAlias):
    if not get_media_server(body.server_id):
        return JSONResponse({"error": "Server not found"}, status_code=404)
    alias_id = add_device_alias(body.server_id, body.alias, body.pattern)
    return {"id": alias_id, "alias": body.alias, "pattern": body.pattern}


@router.delete("/api/devices/{alias}", dependencies=ADMIN)
async def delete_device(alias: str):
    removed = remove_device_alias(alias)
    if not removed:
        return JSONResponse({"error": "Alias not found"}, status_code=404)
    return {"removed": alias}
