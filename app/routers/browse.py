"""Browse tab: a deliberately small visual companion to voice control.

One combined /home payload (continue watching, next up, recently added,
genre chips, now-playing strip) plus genre and search drill-ins. Taps are
played through the existing /api/playback/disambiguate endpoint — this
router only READS. Scope is intentional: this is not a web-UI rebuild.
"""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..adapters.factory import create_adapter, identity_for
from ..config import get_default_server, get_media_server

logger = logging.getLogger("hailfrequency.browse")

router = APIRouter(prefix="/api/browse", tags=["browse"])

MAX_QUERY_LEN = 100


def _resolve(server_id: str | None, user: dict | None):
    server = get_media_server(server_id) if server_id else get_default_server()
    if not server:
        raise ValueError("No media server configured")
    identity = identity_for(user, server)
    return server, create_adapter(server, identity)


def _stamp(cards: list[dict], server_id: str) -> list[dict]:
    for c in cards:
        c["server_id"] = server_id
    return cards


@router.get("/home")
async def browse_home(request: Request, server_id: str | None = None):
    user = getattr(request.state, "user", None)
    try:
        server, adapter = _resolve(server_id, user)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        results = await asyncio.gather(
            adapter.get_browse_sessions(),
            adapter.get_resume_items(),
            adapter.get_next_up(),
            adapter.get_browse_latest(),
            adapter.get_genre_chips(),
            return_exceptions=True,
        )
        sessions, resume, next_up, latest, chips = (
            [] if isinstance(r, Exception) else r for r in results
        )
        for r in results:
            if isinstance(r, Exception):
                logger.warning("browse/home partial failure: %s", r)
        return {
            "server_id": server["id"],
            "server_name": server["name"],
            "sessions": sessions,
            "continue_watching": _stamp(resume, server["id"]),
            "next_up": _stamp(next_up, server["id"]),
            "recently_added": _stamp(latest, server["id"]),
            "genres": chips,
        }
    finally:
        await adapter.close()


@router.get("/genre")
async def browse_genre(request: Request, genre: str, server_id: str | None = None):
    genre = genre.strip()[:MAX_QUERY_LEN]
    if not genre:
        return JSONResponse({"error": "genre required"}, status_code=400)
    user = getattr(request.state, "user", None)
    try:
        server, adapter = _resolve(server_id, user)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        items = await adapter.get_browse_genre_items(genre, limit=24)
        return {"items": _stamp(items, server["id"])}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    except Exception as exc:
        logger.warning("browse/genre failed for %s: %s", server["name"], exc)
        return JSONResponse(
            {"error": f"Cannot reach {server['name']}"}, status_code=502
        )
    finally:
        await adapter.close()


@router.get("/search")
async def browse_search(request: Request, q: str, server_id: str | None = None):
    q = q.strip()[:MAX_QUERY_LEN]
    if not q:
        return JSONResponse({"error": "q required"}, status_code=400)
    user = getattr(request.state, "user", None)
    try:
        server, adapter = _resolve(server_id, user)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        items = await adapter.get_browse_search(q, limit=12)
        return {"items": _stamp(items, server["id"])}
    except Exception as exc:
        logger.warning("browse/search failed for %s: %s", server["name"], exc)
        return JSONResponse(
            {"error": f"Cannot reach {server['name']}"}, status_code=502
        )
    finally:
        await adapter.close()
