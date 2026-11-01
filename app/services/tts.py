import logging
import secrets
import time

import httpx

from ..config import get_settings_batch

logger = logging.getLogger("hailfrequency.tts")

# ── In-memory TTS audio cache ────────────────────────────────────────

_tts_cache: dict[str, tuple[bytes, float]] = {}
TTS_CACHE_TTL = 60  # seconds
TTS_CACHE_MAX_ENTRIES = 50  # hard cap safety net


def store_tts_audio(audio_bytes: bytes) -> str:
    """Store audio bytes, return unique ID. Auto-expires after 60 seconds."""
    audio_id = secrets.token_urlsafe(16)
    _tts_cache[audio_id] = (audio_bytes, time.time())
    # Cleanup expired entries
    now = time.time()
    for key in [k for k, (_, t) in _tts_cache.items() if now - t > TTS_CACHE_TTL]:
        del _tts_cache[key]
    # Hard bound regardless of TTL — evict oldest first
    while len(_tts_cache) > TTS_CACHE_MAX_ENTRIES:
        oldest = min(_tts_cache, key=lambda k: _tts_cache[k][1])
        del _tts_cache[oldest]
    return audio_id


def get_tts_audio(audio_id: str) -> bytes | None:
    """Retrieve audio bytes. Returns None if expired or not found."""
    entry = _tts_cache.get(audio_id)
    if entry is None:
        return None
    audio_bytes, created = entry
    if time.time() - created > TTS_CACHE_TTL:
        del _tts_cache[audio_id]
        return None
    return audio_bytes


# ── TTS Client ────────────────────────────────────────────────────────


class TTSClient:
    def __init__(self, url: str, model: str, voice: str):
        self.url = url.rstrip("/")
        self.model = model
        self.voice = voice
        self._client = httpx.AsyncClient(timeout=30.0)

    async def synthesize(self, text: str) -> bytes | None:
        """Generate speech audio from text. Returns MP3 bytes or None on failure."""
        try:
            resp = await self._client.post(
                f"{self.url}/v1/audio/speech",
                json={
                    "model": self.model,
                    "input": text,
                    "voice": self.voice,
                    "response_format": "mp3",
                },
            )
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            logger.warning("TTS synthesis failed: %s", e)
            return None

    async def test_connection(self) -> bool:
        """Test TTS service connectivity. Tries /health then /v1/models."""
        try:
            resp = await self._client.get(f"{self.url}/health", timeout=2.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        try:
            resp = await self._client.get(f"{self.url}/v1/models", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def list_voices(self) -> list[dict]:
        """Fetch available voices from the service via /v1/voices."""
        try:
            resp = await self._client.get(f"{self.url}/v1/voices", timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            return [
                {"id": v.get("id", ""), "name": v.get("name", v.get("id", ""))}
                for v in data.get("voices", [])
                if v.get("id")
            ]
        except Exception as e:
            logger.warning("Failed to list TTS voices: %s", e)
            return []

    async def list_models(self) -> list[str]:
        """Fetch available TTS models from the service."""
        try:
            resp = await self._client.get(f"{self.url}/v1/models", timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
        except Exception as e:
            logger.warning("Failed to list TTS models: %s", e)
            return []

    async def close(self) -> None:
        await self._client.aclose()


# ── Helper ────────────────────────────────────────────────────────────


async def get_tts_client() -> TTSClient | None:
    """Load TTS config from settings. Returns None if TTS is disabled or not configured."""
    cfg = get_settings_batch(["tts_enabled", "tts_url", "tts_model", "tts_voice"])
    if cfg["tts_enabled"] != "true":
        return None
    url = cfg["tts_url"]
    model = cfg["tts_model"]
    voice = cfg["tts_voice"]
    if not url or not model or not voice:
        return None
    return TTSClient(url, model, voice)
