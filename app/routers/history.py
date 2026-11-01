from fastapi import APIRouter, Query, Request

from ..config import get_command_history
from ..models import HistoryEntry

router = APIRouter(tags=["history"])


@router.get("/api/history", response_model=list[HistoryEntry])
async def history(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Own history for regular users; everyone's (with usernames) for admins."""
    user = getattr(request.state, "user", None)
    scope = None if (user and user.get("role") == "admin") else (user["id"] if user else "-")
    return get_command_history(limit=limit, offset=offset, user_id=scope)
