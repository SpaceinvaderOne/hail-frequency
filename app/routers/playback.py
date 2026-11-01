import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..adapters.base import SessionAmbiguousError
from ..adapters.emby import EmbyAdapter
from ..adapters.factory import create_adapter, identity_for
from ..config import get_media_server
from ..models import BrowseRequest, DisambiguateRequest, PlaybackRequest, SeekRequest
from ..services.tts import get_tts_client, store_tts_audio
from ..services.tts_responses import get_tts_text

logger = logging.getLogger("hailfrequency.playback")

router = APIRouter(prefix="/api/playback", tags=["playback"])


def _get_adapter(server_id: str, user: dict | None = None) -> EmbyAdapter:
    server = get_media_server(server_id)
    if not server:
        raise ValueError("Server not found")
    return create_adapter(server, identity_for(user, server))


async def _run_transport(req: PlaybackRequest, method_name: str, user: dict | None):
    """Run a simple transport command with uniform error handling and cleanup."""
    try:
        adapter = _get_adapter(req.server_id, user)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        result = await getattr(adapter, method_name)(device=req.device)
        return {"result": result}
    except SessionAmbiguousError as e:
        return JSONResponse(
            {"error": e.message, "options": e.options}, status_code=409
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        await adapter.close()


@router.post("/pause")
async def pause(req: PlaybackRequest, request: Request):
    return await _run_transport(req, "pause", getattr(request.state, "user", None))


@router.post("/unpause")
async def unpause(req: PlaybackRequest, request: Request):
    return await _run_transport(req, "unpause", getattr(request.state, "user", None))


@router.post("/stop")
async def stop(req: PlaybackRequest, request: Request):
    return await _run_transport(req, "stop", getattr(request.state, "user", None))


@router.post("/next")
async def next_track(req: PlaybackRequest, request: Request):
    return await _run_transport(req, "next_episode", getattr(request.state, "user", None))


@router.post("/previous")
async def previous_track(req: PlaybackRequest, request: Request):
    return await _run_transport(req, "previous_track", getattr(request.state, "user", None))


@router.post("/seek")
async def seek(req: SeekRequest, request: Request):
    try:
        adapter = _get_adapter(req.server_id, getattr(request.state, "user", None))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        result = await adapter.seek_ticks(req.position_ticks, req.device)
        return {"result": result}
    except SessionAmbiguousError as e:
        return JSONResponse(
            {"error": e.message, "options": e.options}, status_code=409
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        await adapter.close()


@router.post("/disambiguate")
async def disambiguate(req: DisambiguateRequest, request: Request):
    """Execute a disambiguated selection — play a specific item by ID."""
    try:
        adapter = _get_adapter(req.server_id, getattr(request.state, "user", None))
    except ValueError as e:
        return JSONResponse({"error": str(e), "success": False}, status_code=400)
    try:
        if req.action == "play_next":
            result = await adapter.play_next_by_id(req.item_id, req.device)
        elif req.action == "play_search":
            result = await adapter.play_item_by_id(
                req.item_id, req.device, resume=bool(req.resume)
            )
        elif req.action == "play_episode":
            # Season card tap: item_id = season_id, parent_id = series_id.
            # episode 0 is valid (specials use IndexNumber 0) — check None.
            if not req.parent_id or req.episode is None:
                return JSONResponse(
                    {"error": "parent_id and episode required for play_episode",
                     "success": False},
                    status_code=400,
                )
            result = await adapter.play_episode_in_season(
                req.parent_id, req.item_id, req.episode, req.device
            )
        elif req.action == "play_season":
            # Season card tap: item_id = season_id, parent_id = series_id
            if not req.parent_id:
                return JSONResponse(
                    {"error": "parent_id required for play_season",
                     "success": False},
                    status_code=400,
                )
            result = await adapter.play_season_by_id(
                req.parent_id, req.item_id, req.device
            )
        else:
            return JSONResponse(
                {"error": f"Unsupported action: {req.action}", "success": False},
                status_code=400,
            )

        # Generate TTS confirmation. The now-playing card updates via the
        # frontend poller — no artificial delay here.
        tts_audio_url = None
        tts_client = await get_tts_client()
        if tts_client:
            try:
                tts_text = get_tts_text(req.action, result, True)
                if tts_text:
                    audio = await tts_client.synthesize(tts_text)
                    if audio:
                        tts_audio_url = f"/api/tts/audio/{store_tts_audio(audio)}"
            except Exception:
                logger.warning("TTS failed after disambiguate")
            finally:
                await tts_client.close()

        return {
            "result": result,
            "success": True,
            "now_playing": None,
            "tts_audio_url": tts_audio_url,
        }
    except SessionAmbiguousError as e:
        return JSONResponse(
            {"error": e.message, "success": False, "options": e.options},
            status_code=409,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e), "success": False}, status_code=400)
    finally:
        await adapter.close()


@router.post("/browse")
async def browse(req: BrowseRequest, request: Request):
    """Drill-down browse — fetch seasons, episodes, or person filmography."""
    try:
        adapter = _get_adapter(req.server_id, getattr(request.state, "user", None))
    except ValueError as e:
        return JSONResponse({"error": str(e), "success": False}, status_code=400)
    try:
        if req.action == "browse_season":
            if not req.parent_id:
                return JSONResponse(
                    {"error": "parent_id required for browse_season", "success": False},
                    status_code=400,
                )
            items = await adapter.get_season_episodes(req.parent_id, req.item_id)
            result_text = f"{len(items)} episodes:"
        elif req.action == "browse_series":
            items = await adapter.get_seasons(req.item_id)
            result_text = f"{len(items)} seasons:"
        elif req.action == "browse_person":
            items = await adapter.get_person_filmography(req.item_id)
            result_text = f"{len(items)} titles:"
        else:
            return JSONResponse(
                {"error": f"Unknown browse action: {req.action}", "success": False},
                status_code=400,
            )

        for c in items:
            c["server_id"] = req.server_id

        # Generate TTS
        tts_audio_url = None
        tts_client = await get_tts_client()
        if tts_client:
            try:
                tts_text = get_tts_text(
                    req.action, result_text, True, disambiguation=items
                )
                if tts_text:
                    audio = await tts_client.synthesize(tts_text)
                    if audio:
                        tts_audio_url = f"/api/tts/audio/{store_tts_audio(audio)}"
            except Exception:
                logger.warning("TTS failed for browse action")
            finally:
                await tts_client.close()

        return {
            "result": result_text,
            "success": True,
            "disambiguation": items,
            "tts_audio_url": tts_audio_url,
            "parent_id": req.parent_id or req.item_id,
        }
    except ValueError as e:
        return JSONResponse({"error": str(e), "success": False}, status_code=400)
    finally:
        await adapter.close()
