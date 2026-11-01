"""The voice-command parse ladder.

Order: fast-path regex -> learned replay -> Ollama -> search fallback.
One entry point, resolve(), so the ladder is testable offline and the
voice endpoint stays a thin mapper.

Invariant: a HEALTHY Ollama terminates the ladder — its verdict (including
{"action": "unknown"}) is authoritative. Only transport-level failure or
"never configured" may continue past it.
"""

import httpx
import json
import logging
import sqlite3
from dataclasses import dataclass

from ..config import get_all_device_aliases, get_learned_intent, get_media_servers, evict_learned_phrase, set_setting
from ..models import Intent
from .intent import fallback_play_search, normalize_transcript, try_fast_parse, validate_intent
from .ollama import OllamaClient, read_timeout_for

logger = logging.getLogger("hailfrequency.parse_chain")


class OllamaDown(Exception):
    """Ollama could not produce a parse for transport-level reasons.

    reason: "unconfigured" | "unreachable" | "timeout" | "error"
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class ParseOutcome:
    raw_intent: dict | None      # dict for logging/validation
    intent: Intent | None        # set for locally-parsed tiers; None => validate raw_intent
    source: str                  # "fast" | "learned" | "ollama" | "fallback"
    degraded_reason: str | None  # None when Ollama healthy/not needed


def get_ollama_keep_alive(cfg: dict) -> str | None:
    """Session keep-alive for the Ollama model."""
    if cfg.get("ollama_keep_alive_enabled") == "true":
        return cfg.get("ollama_keep_alive_value") or "30s"
    return None


def _classify(exc: Exception) -> str:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "unreachable"
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return "timeout"
    return "error"


async def _remember_engine(ollama: OllamaClient) -> None:
    """Persist GPU/CPU detection after a successful parse. Advisory only —
    failures are logged and ignored."""
    try:
        engine = await ollama.detect_engine()
        if engine:
            set_setting("ollama_engine", engine)
    except Exception:
        logger.debug("Engine detection failed (ignored)")


async def _ollama_tier(transcript: str, cfg: dict) -> dict:
    """One Ollama call plus one retry on transient transport failure.

    Raises OllamaDown with a precise reason. No retry on HTTP status errors —
    the server answered; retrying won't help.
    """
    ollama_url = cfg.get("ollama_url")
    ollama_model = cfg.get("ollama_model")
    if not ollama_url or not ollama_model:
        raise OllamaDown("unconfigured")

    engine = cfg.get("ollama_engine") or "unknown"
    ollama = OllamaClient(
        ollama_url, ollama_model,
        keep_alive=get_ollama_keep_alive(cfg),
        read_timeout=read_timeout_for(engine),
    )
    try:
        servers = get_media_servers()
        devices = get_all_device_aliases()
        system_prompt = ollama.build_system_prompt(servers, devices)
        last_reason = "error"
        for attempt in (1, 2):
            try:
                raw = await ollama.parse_intent(transcript, system_prompt)
                await _remember_engine(ollama)
                return raw
            except Exception as exc:
                last_reason = _classify(exc)
                # Connect failures retry everywhere; read timeouts only
                # retry off-CPU — a CPU parse already waited minutes.
                retryable = last_reason == "unreachable" or (
                    last_reason == "timeout" and engine != "cpu"
                )
                if retryable and attempt == 1:
                    logger.warning(
                        "Ollama %s (attempt 1) — retrying once", last_reason)
                    continue
                logger.exception("Ollama intent parsing failed")
                raise OllamaDown(last_reason)
        raise OllamaDown(last_reason)  # unreachable; loop always returns/raises
    finally:
        await ollama.close()


async def resolve(transcript: str, cfg: dict) -> ParseOutcome:
    # Tier 1: deterministic fast path — wins even when Ollama is up.
    intent = try_fast_parse(transcript)
    if intent:
        logger.info("Fast-path matched: %s", intent.action)
        return ParseOutcome(
            raw_intent=intent.model_dump(), intent=intent,
            source="fast", degraded_reason=None,
        )

    # Tier 2: learned replay — exact normalized match only (no fuzz; fuzz
    # is Ollama's job). Entries were validated at learn time, but validate
    # again on the way out and self-heal if the schema moved underneath us.
    phrase = normalize_transcript(transcript)
    try:
        stored = get_learned_intent(phrase)
    except sqlite3.OperationalError:
        logger.warning("Learned phrases DB unavailable; skipping tier 2")
        stored = None
    if stored is not None:
        try:
            candidate = validate_intent(json.loads(stored))
        except (ValueError, json.JSONDecodeError):
            candidate = "corrupt"
        if isinstance(candidate, Intent):
            logger.info("Learned phrase matched: %s", candidate.action)
            # model_copy: same never-mutate-the-source rule as _FAST_PATTERNS
            intent = candidate.model_copy(deep=True)
            return ParseOutcome(
                raw_intent=intent.model_dump(), intent=intent,
                source="learned", degraded_reason=None,
            )
        logger.warning("Evicting invalid learned entry for '%s'", phrase)
        try:
            evict_learned_phrase(phrase)
        except sqlite3.OperationalError:
            logger.debug("Could not evict learned entry: DB unavailable")

    # Tier 3: Ollama.
    logger.debug("Fast-path miss, sending to Ollama")
    try:
        raw = await _ollama_tier(transcript, cfg)
    except OllamaDown as down:
        # Tier 4: Ollama can't answer. Play-shaped phrases become a literal
        # search; everything else re-raises for an honest diagnostic.
        intent = fallback_play_search(transcript)
        if intent is None:
            raise
        logger.warning(
            "Ollama %s — falling back to play_search '%s'",
            down.reason, intent.query,
        )
        return ParseOutcome(
            raw_intent=intent.model_dump(), intent=intent,
            source="fallback", degraded_reason=down.reason,
        )
    logger.info("Raw intent: %s", raw)
    return ParseOutcome(
        raw_intent=raw, intent=None, source="ollama", degraded_reason=None,
    )
