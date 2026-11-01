import json
import logging
import re
import sqlite3

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ..adapters.base import (
    TICKS_PER_SECOND,
    AmbiguousMatchError,
    SessionAmbiguousError,
    parse_seek_ticks,
)
from ..adapters.factory import create_adapter, identity_for
from ..config import (
    evict_learned_phrase,
    get_default_server,
    get_media_server,
    get_media_servers,
    get_settings_batch,
    learn_phrase,
    log_command,
    set_setting,
)
from ..models import ITEM_ID_PATTERN, VoiceResponse
from ..services.intent import normalize_transcript, route_intent, validate_intent
from ..services.media_http import fetch_image
from ..services.ollama import OllamaClient, READ_TIMEOUT_CPU
from ..services.parse_chain import OllamaDown, get_ollama_keep_alive, resolve
from ..services.sleep_timer import cancel_timers, set_timer
from ..services.speaches import SpeachesClient
from ..services.tts import get_tts_audio, get_tts_client, store_tts_audio
from ..services.tts_responses import get_tts_text

logger = logging.getLogger("hailfrequency.voice")

router = APIRouter(tags=["voice"])

MAX_AUDIO_SIZE = 10 * 1024 * 1024  # 10MB


def _resolve_target_server(
    server_name: str | None, ui_server_id: str | None = None
) -> dict:
    """Resolve the target server: spoken name > UI selector > default."""
    if server_name:
        for s in get_media_servers():
            if s["name"].lower() == server_name.lower():
                return s
        raise ValueError(f'No server named "{server_name}".')

    if ui_server_id:
        server = get_media_server(ui_server_id)
        if server:
            return server

    server = get_default_server()
    if not server:
        raise ValueError("No media servers configured.")
    return server


@router.post("/api/voice/warmup")
async def warmup_ollama():
    """Pre-load the Ollama model into VRAM. Fire-and-forget from frontend."""
    warmup_cfg = get_settings_batch(
        ["ollama_url", "ollama_model", "ollama_keep_alive_enabled", "ollama_keep_alive_value"]
    )
    ollama_url = warmup_cfg["ollama_url"]
    ollama_model = warmup_cfg["ollama_model"]
    if not ollama_url or not ollama_model:
        return JSONResponse({"ok": False})

    keep_alive = get_ollama_keep_alive(warmup_cfg)
    ollama = OllamaClient(ollama_url, ollama_model, keep_alive=keep_alive,
                          read_timeout=READ_TIMEOUT_CPU)
    try:
        await ollama.warmup()
        engine = await ollama.detect_engine()
        if engine:
            try:
                set_setting("ollama_engine", engine)
            except Exception:
                logger.warning("Could not persist engine detection")
    except Exception:
        logger.warning("Ollama warmup failed (model may not be available)")
        return JSONResponse({"ok": False})
    finally:
        await ollama.close()

    return JSONResponse({"ok": True})


def _learn_from_outcome(outcome, transcript: str, success: bool) -> bool:
    """Phrase learning: successful Ollama parses become instant local
    replays; learned entries that fail at execution are evicted (the phrase
    goes back to Ollama next time). raw_intent is the PRE-device-mutation
    parse — the pure meaning of the spoken words.
    Returns True only when a phrase was actually stored."""
    phrase = normalize_transcript(transcript)
    try:
        if outcome.source == "ollama" and success \
                and outcome.raw_intent.get("action") != "unknown":
            learn_phrase(phrase, json.dumps(outcome.raw_intent))
            return True
        if outcome.source == "learned" and not success:
            evict_learned_phrase(phrase)
    except sqlite3.OperationalError as exc:
        logger.warning("Learned-phrase update failed: DB unavailable (%s)", exc)
    return False


@router.post("/api/voice")
async def process_voice(
    request: Request,
    audio: UploadFile = File(...),
    device: str | None = Form(None),
    server_id: str | None = Form(None),
):
    """Complete voice command pipeline: audio → STT → LLM → action."""
    user = getattr(request.state, "user", None)
    uid = user["id"] if user else None

    # 1. Validate upload — reject oversized files before reading into memory
    content_type = audio.content_type or "audio/webm"
    if audio.size and audio.size > MAX_AUDIO_SIZE:
        raise HTTPException(400, "Audio file too large (max 10MB)")

    audio_bytes = await audio.read()

    if len(audio_bytes) > MAX_AUDIO_SIZE:
        raise HTTPException(400, "Audio file too large (max 10MB)")

    if len(audio_bytes) == 0:
        raise HTTPException(400, "Empty audio file")

    logger.debug("Audio received: %d bytes, content_type=%s", len(audio_bytes), content_type)

    # One DB round-trip for all service settings used in this request
    cfg = get_settings_batch([
        "speaches_url",
        "speaches_model",
        "ollama_url",
        "ollama_model",
        "ollama_keep_alive_enabled",
        "ollama_keep_alive_value",
        "ollama_engine",
    ])

    # 2. Transcribe via Speaches
    speaches_url = cfg["speaches_url"]
    speaches_model = cfg["speaches_model"]
    if not speaches_url or not speaches_model:
        raise HTTPException(500, "Speaches not configured")

    speaches = SpeachesClient(speaches_url, speaches_model)
    try:
        transcript = await speaches.transcribe(audio_bytes, content_type)
    except Exception:
        logger.exception("Speaches transcription failed")
        log_command(None, None, None, "Transcription failed", False, user_id=uid)
        return VoiceResponse(
            transcript="",
            result="Transcription failed. Please try again.",
            success=False,
        )
    finally:
        await speaches.close()

    if not transcript or not transcript.strip():
        log_command("", None, None, "Empty transcript", False, user_id=uid)
        return VoiceResponse(
            transcript="",
            result="I didn't catch that. Please try again.",
            success=False,
        )

    logger.info("Transcript: %s", transcript)

    # 3. Parse ladder: fast-path -> Ollama (see services/parse_chain.py)
    try:
        outcome = await resolve(transcript, cfg)
    except OllamaDown as e:
        # Ollama can't answer and the phrase wasn't play-shaped. Name the
        # actual problem — a generic message hides whether Ollama is down
        # or simply not configured.
        if e.reason == "unconfigured":
            msg = ('The AI parser (Ollama) is not configured. Pattern '
                   'commands and "play <title>" still work; add Ollama in '
                   'Settings for full understanding.')
        else:
            msg = ('The AI parser (Ollama) is unreachable. Pattern commands '
                   'and "play <title>" still work.')
        log_command(transcript, None, None, msg, False, user_id=uid)
        return VoiceResponse(transcript=transcript, result=msg, success=False)

    raw_intent = outcome.raw_intent
    intent = outcome.intent
    if intent is None:
        # 4. Validate LLM output (locally-parsed tiers are pre-validated)
        intent = validate_intent(raw_intent)
        if isinstance(intent, str):
            log_command(transcript, json.dumps(raw_intent), None, intent,
                        False, user_id=uid, parse_source=outcome.source)
            return VoiceResponse(
                transcript=transcript,
                intent=raw_intent,
                result=intent,
                success=False,
                parse_source=outcome.source,
                degraded=outcome.degraded_reason,
            )

    logger.debug("Validated intent: action=%s device=%s query=%s", intent.action, intent.device, getattr(intent, "query", None))

    # 4b. Device targeting precedence: SPOKEN device > UI selector > Auto.
    # A device spoken in the command ("play Bosch on the shield") must win —
    # only fall back to the UI-selected device when none was spoken, and never
    # when a server was spoken (the UI device belongs to the UI server).
    if device and not intent.device and not intent.server:
        logger.info("Device from UI selector: %s (server_id=%s)", device, server_id)
        intent.device = device

    # 5. Resolve target server + this user's identity on it, create adapter
    try:
        server = _resolve_target_server(intent.server, server_id)
        identity = identity_for(user, server)
    except ValueError as e:
        result = str(e)
        # Evicts on identity/server-resolution failures too — any execution
        # failure evicts a learned phrase; re-learning self-heals.
        _learn_from_outcome(outcome, transcript, False)
        log_command(transcript, json.dumps(intent.model_dump()), intent.action, result, False, user_id=uid,
                    parse_source=outcome.source)
        return VoiceResponse(
            transcript=transcript,
            intent=intent.model_dump(),
            result=result,
            success=False,
            parse_source=outcome.source,
            degraded=outcome.degraded_reason,
        )

    # Sleep timers are scheduled here, not in route_intent — they outlive
    # the request and need the server row + identity to build their own
    # adapter when they fire (this request's adapter will be long closed)
    if intent.action in ("sleep_timer", "cancel_sleep_timer"):
        success = True
        if intent.action == "cancel_sleep_timer":
            result = cancel_timers(server["id"], intent.device)
        else:
            try:
                seconds = parse_seek_ticks(intent.amount or "") // TICKS_PER_SECOND
                result = set_timer(server, identity, intent.device, seconds)
            except ValueError as e:
                # Unparseable amount vs. out-of-range: set_timer gives a
                # specific message; a raw parse failure gets the generic hint
                result = str(e) if str(e) and "parse" not in str(e).lower() \
                    else 'How long? Try "stop playing in 30 minutes".'
                success = False
        tts_url = await _maybe_tts(intent.action, result, success)
        learned = _learn_from_outcome(outcome, transcript, success)
        log_command(transcript, json.dumps(intent.model_dump()), intent.action, result, success, user_id=uid,
                    parse_source=outcome.source)
        return VoiceResponse(
            transcript=transcript,
            intent=intent.model_dump(),
            result=result,
            success=success,
            tts_audio_url=tts_url,
            parse_source=outcome.source,
            degraded=outcome.degraded_reason,
            learned=learned,
        )

    logger.debug("Routing to server '%s' (type=%s)", server["name"], server["type"])
    adapter = create_adapter(server, identity)
    try:
        # 6. Route intent to adapter
        try:
            result = await route_intent(intent, adapter)
            success = True
            logger.debug("Intent result: success=%s result='%s'", success, result[:200] if result else "")
        except SessionAmbiguousError as e:
            result = e.message
            success = False
            # Return structured clarification response
            log_command(transcript, json.dumps(intent.model_dump()), intent.action, result, False, user_id=uid,
                        parse_source=outcome.source)
            return VoiceResponse(
                transcript=transcript,
                intent={
                    **intent.model_dump(),
                    "clarify": True,
                    "options": e.options,
                },
                result=result,
                success=False,
                parse_source=outcome.source,
                degraded=outcome.degraded_reason,
            )
        except AmbiguousMatchError as e:
            # Fill in server_id on each candidate
            for c in e.candidates:
                c["server_id"] = server["id"]
            log_command(transcript, json.dumps(intent.model_dump()), intent.action, e.message, True, user_id=uid,
                        parse_source=outcome.source)
            tts_url = await _maybe_tts(intent.action, e.message, False, disambiguation=e.candidates)
            return VoiceResponse(
                transcript=transcript,
                intent=intent.model_dump(),
                result=e.message,
                success=True,
                disambiguation=e.candidates,
                tts_audio_url=tts_url,
                parse_source=outcome.source,
                degraded=outcome.degraded_reason,
            )
        except ValueError as e:
            # Honest failure from the adapter (no matching session, missing
            # subtitle language, ...) — user-facing message, marked failed
            result = str(e)
            success = False
            logger.debug("Intent failed: %s", result[:200])

        # 7. Generate TTS confirmation. The now-playing card refreshes via the
        # frontend poller right after the command — no artificial delay here.
        tts_audio_url = await _maybe_tts(intent.action, result, success)

        # Degraded-mode transparency (shown, not spoken — computed post-TTS).
        # Unconfigured installs run this way permanently; don't nag them.
        if success and outcome.source == "fallback" \
                and outcome.degraded_reason != "unconfigured":
            result = f"{result} (AI parser offline — matched by search)"

        # 8. Log command
        learned = _learn_from_outcome(outcome, transcript, success)
        log_command(
            transcript,
            json.dumps(intent.model_dump()),
            intent.action,
            result,
            success,
            user_id=uid,
            parse_source=outcome.source,
        )

        return VoiceResponse(
            transcript=transcript,
            intent=intent.model_dump(),
            result=result,
            success=success,
            now_playing=None,
            tts_audio_url=tts_audio_url,
            parse_source=outcome.source,
            degraded=outcome.degraded_reason,
            learned=learned,
        )

    finally:
        await adapter.close()


async def _maybe_tts(
    action: str, result: str, success: bool, disambiguation: list | None = None
) -> str | None:
    """Generate TTS audio if enabled. Returns audio URL or None."""
    tts_client = await get_tts_client()
    if not tts_client:
        return None
    try:
        tts_text = get_tts_text(action, result, success, disambiguation=disambiguation)
        if not tts_text:
            return None
        audio_bytes = await tts_client.synthesize(tts_text)
        if audio_bytes:
            audio_id = store_tts_audio(audio_bytes)
            return f"/api/tts/audio/{audio_id}"
    except Exception as e:
        logger.warning("TTS generation failed: %s", e)
    finally:
        await tts_client.close()
    return None


# ── TTS audio serving ────────────────────────────────────────────────


@router.get("/api/tts/audio/{audio_id}")
async def serve_tts_audio(audio_id: str):
    """Serve cached TTS audio bytes."""
    audio_bytes = get_tts_audio(audio_id)
    if not audio_bytes:
        raise HTTPException(404, "Audio not found or expired")
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-store"},
    )


# ── Image proxy ─────────────────────────────────────────────────────

@router.get("/api/media/{server_id}/image/{item_id}")
async def proxy_image(server_id: str, item_id: str, request: Request):
    """Proxy media server images to avoid CORS/auth issues.

    Scoped like every other read path: the item id is pattern-validated (it
    lands in the outbound URL) and the adapter runs as the caller's identity,
    NOT the server's admin credentials — so a delegated user can't fetch from
    a server they have no account on, or smuggle query syntax into the id.
    """
    if not re.fullmatch(ITEM_ID_PATTERN, item_id):
        return Response(status_code=404)
    server = get_media_server(server_id)
    if not server:
        return Response(status_code=404)
    user = getattr(request.state, "user", None)
    try:
        identity = identity_for(user, server)
        # Pooled fetch — keep-alive connection reused across the Browse
        # grid's many poster requests instead of a fresh client per image
        result = await fetch_image(server, identity, item_id)
    except ValueError:
        return Response(status_code=404)
    if result is None:
        return Response(status_code=404)
    content, content_type = result
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ── Now-playing poll ────────────────────────────────────────────────

@router.get("/api/now-playing")
async def now_playing(
    request: Request, device: str | None = None, server_id: str | None = None
):
    """Get current playback state for the now-playing card."""
    server = get_media_server(server_id) if server_id else None
    if not server:
        server = get_default_server()
    if not server:
        return {"now_playing": None}
    try:
        user = getattr(request.state, "user", None)
        adapter = create_adapter(server, identity_for(user, server), timeout=5.0)
    except ValueError:
        # Unsupported type, or the user has no identity on this server
        return {"now_playing": None}
    try:
        ps = await adapter.get_playback_state(device=device)
        if ps:
            ps["server_id"] = server["id"]
            return {"now_playing": ps}
        return {"now_playing": None}
    except Exception as exc:
        # The card polls continuously; an unreachable or erroring media
        # server is a normal condition, not a 500
        logger.warning("now-playing poll failed for %s: %s", server["name"], exc)
        return {"now_playing": None}
    finally:
        await adapter.close()
