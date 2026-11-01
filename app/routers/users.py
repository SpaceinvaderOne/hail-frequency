"""Admin user management: list, disable/enable, role, block, delete.

Delegated users CAN be promoted to admin (household reality; same model as
Jellyseerr/Overseerr) — but note the tradeoff the UI warns about: a promoted
account's admin access is protected only by its media-server password.

The bootstrap LOCAL admin is untouchable here: it can't be demoted,
disabled, or deleted — it is the recovery path.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..auth import require_admin
from ..config import (
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    set_user_disabled,
    set_user_role,
)
from ..models import ProvisionUserRequest, UserUpdateRequest

router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(require_admin)],
)


def _guard_target(request: Request, user_id: str):
    """Common guards: target must exist, and must not be yourself or a
    local admin account. Returns (target, error_response)."""
    if request.state.user["id"] == user_id:
        return None, JSONResponse(
            {"error": "You can't modify your own account here"}, status_code=400
        )
    target = get_user_by_id(user_id)
    if not target:
        return None, JSONResponse({"error": "User not found"}, status_code=404)
    if target.get("password_hash"):
        return None, JSONResponse(
            {"error": "Local admin accounts can't be modified here"},
            status_code=400,
        )
    return target, None


@router.get("")
async def get_users():
    return {"users": list_users()}


@router.post("/provision")
async def provision_user(body: ProvisionUserRequest, request: Request):
    """Set a media account's app state by username, creating the app user
    if they've never signed in. Never touches the media server itself."""
    username = body.username.strip()
    if username.lower() == request.state.user["username"].lower():
        return JSONResponse(
            {"error": "You can't modify your own account here"}, status_code=400
        )
    existing = get_user_by_username(username)
    if existing and existing.get("password_hash"):
        return JSONResponse(
            {"error": "Local admin accounts can't be modified here"},
            status_code=400,
        )
    if not existing:
        existing = create_user(username=username, role=body.role)
    else:
        set_user_role(existing["id"], body.role)
    set_user_disabled(existing["id"], body.disabled)
    return {"success": True}


@router.put("/{user_id}")
async def update_user(user_id: str, body: UserUpdateRequest, request: Request):
    target, err = _guard_target(request, user_id)
    if err:
        return err
    if body.disabled is not None:
        set_user_disabled(user_id, body.disabled)
    if body.role is not None:
        set_user_role(user_id, body.role)
    return {"success": True}


@router.delete("/{user_id}")
async def remove_user(user_id: str, request: Request):
    target, err = _guard_target(request, user_id)
    if err:
        return err
    delete_user(user_id)
    return {"success": True}
