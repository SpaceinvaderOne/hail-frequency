import logging

import httpx

logger = logging.getLogger("hailfrequency.speaches")

# Content type → file extension mapping.
# Whisper uses the file extension to determine the audio codec.
# A mismatch here causes silent transcription failures.
CONTENT_TYPE_TO_EXT = {
    "audio/webm": ".webm",
    "audio/webm;codecs=opus": ".webm",
    "audio/ogg": ".ogg",
    "audio/ogg;codecs=opus": ".ogg",
    "audio/mp4": ".m4a",
    "audio/aac": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/mp4a-latm": ".m4a",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/x-wav": ".wav",
}


def _ext_for_content_type(content_type: str) -> str:
    """Map audio content type to file extension for Whisper."""
    # Normalize: lowercase, strip whitespace
    ct = content_type.lower().strip()

    # Try exact match first
    if ct in CONTENT_TYPE_TO_EXT:
        return CONTENT_TYPE_TO_EXT[ct]

    # Try without parameters (e.g. "audio/webm;codecs=opus" -> "audio/webm")
    base = ct.split(";")[0].strip()
    if base in CONTENT_TYPE_TO_EXT:
        return CONTENT_TYPE_TO_EXT[base]

    logger.warning("Unknown audio content type '%s', defaulting to .wav", content_type)
    return ".wav"


class SpeachesClient:
    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = httpx.AsyncClient(timeout=30.0)

    async def transcribe(self, audio_data: bytes, content_type: str) -> str:
        """Send audio to Speaches for transcription. Returns transcript text."""
        ext = _ext_for_content_type(content_type)
        filename = f"recording{ext}"
        logger.debug("Speaches request: %d bytes, model=%s, filename=%s", len(audio_data), self.model, filename)

        resp = await self.client.post(
            f"{self.base_url}/v1/audio/transcriptions",
            files={"file": (filename, audio_data, content_type)},
            data={
                "model": self.model,
                "response_format": "text",
            },
        )
        resp.raise_for_status()
        transcript = resp.text.strip()
        logger.debug("Speaches response: status=%d, transcript_len=%d", resp.status_code, len(transcript))
        return transcript

    async def test_connection(self) -> bool:
        """Test connectivity by hitting /v1/models."""
        try:
            resp = await self.client.get(f"{self.base_url}/v1/models")
            resp.raise_for_status()
            return True
        except Exception:
            return False

    async def list_models(self) -> list[dict]:
        """List available models from /v1/models."""
        resp = await self.client.get(f"{self.base_url}/v1/models")
        resp.raise_for_status()
        data = resp.json()
        return [{"id": m.get("id", "")} for m in data.get("data", [])]

    async def close(self):
        await self.client.aclose()
