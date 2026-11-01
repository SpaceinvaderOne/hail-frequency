import re
from abc import ABC, abstractmethod

TICKS_PER_SECOND = 10_000_000


def media_auth_headers(token: str) -> dict[str, str]:
    """Request headers that authenticate to Emby and Jellyfin.

    Jellyfin accepts only the ``Authorization: MediaBrowser`` scheme; Emby
    accepts both it and ``X-Emby-Token``. Sending both covers every supported
    server with one header set.
    """
    return {
        "Authorization": (
            f'MediaBrowser Token="{token}", Client="HailFrequency", '
            f'Device="HailFrequency", DeviceId="hailfrequency", Version="1.0"'
        ),
        "X-Emby-Token": token,
    }


def parse_seek_ticks(position: str) -> int:
    """Parse a human-friendly position ("30m", "1h20m", "1:30:00", "90",
    "0m") to ticks (10M/second). Shared by all adapters; each converts to
    its server's native unit internally.

    Raises ValueError if unparseable. Zero is valid (= the beginning).
    """
    pos = position.strip()

    # HH:MM:SS
    m = re.match(r"^(\d+):(\d{2}):(\d{2})$", pos)
    if m:
        secs = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        return secs * TICKS_PER_SECOND

    # MM:SS
    m = re.match(r"^(\d+):(\d{2})$", pos)
    if m:
        secs = int(m[1]) * 60 + int(m[2])
        return secs * TICKS_PER_SECOND

    # Xh Ym Zs combinations
    total = 0
    matched = False
    for val, unit in re.findall(r"(\d+)\s*(h|m|s)", pos, re.I):
        matched = True
        v = int(val)
        if unit.lower() == "h":
            total += v * 3600
        elif unit.lower() == "m":
            total += v * 60
        else:
            total += v
    # matched-but-zero (e.g. "0m" = "go to the beginning") is valid — seek to 0
    if matched:
        return total * TICKS_PER_SECOND

    # Bare seconds (includes "0")
    if pos.isdigit():
        return int(pos) * TICKS_PER_SECOND

    raise ValueError(f'Cannot parse seek position: "{position}"')


class SessionAmbiguousError(Exception):
    """Raised when multiple sessions match and disambiguation is needed."""

    def __init__(self, message: str, options: list[str]):
        super().__init__(message)
        self.message = message
        self.options = options


class AmbiguousMatchError(Exception):
    """Raised when multiple media items match and disambiguation is needed."""

    def __init__(self, message: str, candidates: list[dict]):
        super().__init__(message)
        self.message = message
        self.candidates = candidates


class MediaServerAdapter(ABC):
    """Abstract interface for media server adapters."""

    @abstractmethod
    async def test_connection(self) -> bool: ...

    @abstractmethod
    async def search(self, query: str, media_type: str | None = None) -> str: ...

    @abstractmethod
    async def play_search(
        self, query: str, media_type: str | None = None, device: str | None = None
    ) -> str: ...

    @abstractmethod
    async def play_next(self, series_name: str, device: str | None = None) -> str: ...

    @abstractmethod
    async def play_episode(
        self,
        series_name: str,
        episode: int,
        season: int | None = None,
        device: str | None = None,
    ) -> str: ...

    @abstractmethod
    async def play_season(
        self,
        series_name: str,
        season: int | None = None,
        device: str | None = None,
    ) -> str: ...

    @abstractmethod
    async def pause(self, device: str | None = None) -> str: ...

    @abstractmethod
    async def unpause(self, device: str | None = None) -> str: ...

    @abstractmethod
    async def stop(self, device: str | None = None) -> str: ...

    @abstractmethod
    async def seek(self, position: str, device: str | None = None) -> str: ...

    @abstractmethod
    async def seek_relative(self, offset_ticks: int, device: str | None = None) -> str: ...

    @abstractmethod
    async def seek_ticks(self, position_ticks: int, device: str | None = None) -> str: ...

    @abstractmethod
    def image_url(self, item_id: str) -> str: ...

    @abstractmethod
    async def next_episode(self, device: str | None = None) -> str: ...

    @abstractmethod
    async def get_sessions(self) -> list[dict]: ...

    @abstractmethod
    async def get_now_playing(self) -> str: ...

    @abstractmethod
    async def get_recently_added(self, media_type: str | None = None) -> str: ...

    @abstractmethod
    async def get_playback_state(self, device: str | None = None) -> dict | None: ...

    @abstractmethod
    async def previous_track(self, device: str | None = None) -> str: ...

    @abstractmethod
    async def set_volume(self, level: int, device: str | None = None) -> str: ...

    @abstractmethod
    async def volume_step(self, up: bool, device: str | None = None) -> str: ...

    @abstractmethod
    async def set_mute(self, muted: bool, device: str | None = None) -> str: ...

    @abstractmethod
    async def set_subtitles(
        self, enabled: bool, language: str | None = None, device: str | None = None
    ) -> str: ...

    @abstractmethod
    async def set_audio_language(
        self, language: str, device: str | None = None
    ) -> str: ...

    @abstractmethod
    async def shuffle(
        self,
        series_name: str | None = None,
        media_type: str | None = None,
        genre: str | None = None,
        device: str | None = None,
        unwatched: bool | None = None,
        decade: str | None = None,
        top_rated: bool | None = None,
    ) -> str: ...

    @abstractmethod
    async def set_watched(self, watched: bool, device: str | None = None) -> str: ...

    @abstractmethod
    async def play_collection(
        self,
        query: str,
        kind: str | None = None,
        shuffled: bool | None = None,
        device: str | None = None,
    ) -> str: ...

    @abstractmethod
    async def play_item_by_id(
        self, item_id: str, device: str | None = None, resume: bool = False
    ) -> str: ...

    @abstractmethod
    async def play_next_by_id(self, series_id: str, device: str | None = None) -> str: ...

    @abstractmethod
    async def close(self) -> None: ...
