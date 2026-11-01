"""Sleep timers: "stop playing in 30 minutes".

In-process asyncio timers — safe ONLY because the app runs a single
uvicorn worker (same invariant as the TTS cache and login throttle).
Keyed per server+device so a bedroom timer doesn't kill the lounge.
"""

import asyncio
import logging

from ..adapters.factory import create_adapter

logger = logging.getLogger("hailfrequency.sleep_timer")

# key -> (task, human description)
_timers: dict[str, tuple[asyncio.Task, str]] = {}

MIN_SECONDS = 60
MAX_SECONDS = 12 * 3600


def _key(server_id: str, device: str | None) -> str:
    return f"{server_id}:{(device or '').lower()}"


def _describe(seconds: int) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " and ".join(parts) or f"{seconds} seconds"


async def _fire(server: dict, identity: dict | None, device: str | None, key: str):
    adapter = create_adapter(server, identity)
    try:
        result = await adapter.stop(device=device)
        logger.info("Sleep timer fired (%s): %s", key, result)
    except Exception as e:
        logger.warning("Sleep timer stop failed (%s): %s", key, e)
    finally:
        await adapter.close()
        # Only clear the slot if it still holds THIS task — a timer that
        # replaced us mid-fire must not be evicted (would leave it untracked)
        current = asyncio.current_task()
        if _timers.get(key, (None,))[0] is current:
            _timers.pop(key, None)


def set_timer(
    server: dict,
    identity: dict | None,
    device: str | None,
    seconds: int,
) -> str:
    """Schedule a stop. Replaces any existing timer for the same target.

    Raises ValueError for out-of-range durations so the caller reports a
    failure (not a spoken/logged success)."""
    if seconds < MIN_SECONDS:
        raise ValueError("That's too soon for a sleep timer — one minute at least.")
    if seconds > MAX_SECONDS:
        raise ValueError("Sleep timers max out at 12 hours.")
    key = _key(server["id"], device)
    existing = _timers.pop(key, None)
    if existing:
        existing[0].cancel()

    desc = _describe(seconds)

    async def _wait_then_fire():
        await asyncio.sleep(seconds)
        await _fire(server, identity, device, key)

    task = asyncio.get_running_loop().create_task(_wait_then_fire())
    _timers[key] = (task, desc)
    where = f" on {device}" if device else ""
    return f"Okay — I'll stop playback{where} in {desc}."


def cancel_timers(server_id: str | None = None, device: str | None = None) -> str:
    """Cancel pending sleep timer(s). Scoped to a server+device when given so
    one household member's 'cancel sleep timer' doesn't kill another's."""
    if server_id is not None:
        key = _key(server_id, device)
        existing = _timers.pop(key, None)
        if not existing:
            return "There's no sleep timer running here."
        existing[0].cancel()
        return "Sleep timer cancelled."
    # Unscoped fallback (no server context)
    if not _timers:
        return "There's no sleep timer running."
    count = len(_timers)
    for task, _desc in _timers.values():
        task.cancel()
    _timers.clear()
    return "Sleep timer cancelled." if count == 1 else f"Cancelled {count} sleep timers."
