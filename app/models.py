from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# Emby/Jellyfin item IDs are numeric or hex/GUID — used in outbound URLs,
# so reject anything that could smuggle path segments or query params
ITEM_ID_PATTERN = r"^[A-Za-z0-9\-]+$"


# ── Authentication ──────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., max_length=128)
    password: str = Field(..., max_length=256)


class AuthStatus(BaseModel):
    authenticated: bool
    username: str | None = None
    role: str | None = None


# ── Setup ───────────────────────────────────────────────────────────

class SetupStatus(BaseModel):
    setup_complete: bool
    current_step: int = 0


class SetupAuthRequest(BaseModel):
    username: str = Field("admin", max_length=128)
    password: str = Field(..., min_length=8, max_length=256)
    current_password: str | None = Field(None, max_length=256)


class SetupSpeachesRequest(BaseModel):
    url: str = Field(..., max_length=512)
    model: str = Field(..., max_length=256)


class SetupOllamaRequest(BaseModel):
    url: str = Field(..., max_length=512)
    model: str = Field(..., max_length=256)
    keep_alive_enabled: bool = False
    keep_alive_value: str = Field("30s", max_length=32)


class SetupMediaServerRequest(BaseModel):
    name: str = Field(..., max_length=256)
    type: str = Field(..., max_length=32)  # "emby" or "jellyfin"
    url: str = Field(..., max_length=512)
    api_key: str = Field(..., max_length=512)
    user_id: str | None = Field(None, max_length=128)
    user_name: str | None = Field(None, max_length=128)
    is_default: bool = True

    @field_validator("type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"emby", "jellyfin"}:
            raise ValueError("type must be one of: emby, jellyfin")
        return v


class DeviceAliasEntry(BaseModel):
    alias: str
    pattern: str


class SetupDeviceAliasesRequest(BaseModel):
    server_id: str
    aliases: list[DeviceAliasEntry]


# ── Voice ───────────────────────────────────────────────────────────

class NowPlayingInfo(BaseModel):
    item_name: str
    series_name: str | None = None
    episode_info: str | None = None
    device_name: str
    state: str  # "Playing" or "Paused"
    position_ticks: int = 0
    runtime_ticks: int = 0
    item_id: str | None = None
    server_id: str | None = None


class VoiceResponse(BaseModel):
    transcript: str
    intent: dict | None = None
    result: str
    success: bool
    now_playing: NowPlayingInfo | None = None
    disambiguation: list[dict] | None = None
    tts_audio_url: str | None = None
    parse_source: str | None = None
    degraded: str | None = None
    learned: bool = False


# ── Intent (LLM output validation) ─────────────────────────────────

class Intent(BaseModel):
    action: str
    query: str | None = None
    type: str | None = None
    series: str | None = None
    device: str | None = None
    server: str | None = None
    position: str | None = None
    amount: str | None = None
    message: str | None = None
    season: int | None = None
    episode: int | None = None
    person: str | None = None
    language: str | None = None
    genre: str | None = None
    unwatched: bool | None = None
    decade: str | None = None
    top_rated: bool | None = None
    shuffled: bool | None = None


# ── Playback ────────────────────────────────────────────────────────

class PlaybackRequest(BaseModel):
    server_id: str
    session_id: str | None = None
    device: str | None = None


class SeekRequest(PlaybackRequest):
    position_ticks: int


class DisambiguateRequest(BaseModel):
    item_id: str = Field(..., pattern=ITEM_ID_PATTERN)
    action: str  # "play_search" | "play_next" | "play_episode"
    server_id: str
    device: str | None = None
    # play_episode via season card: item_id = season_id, parent_id = series_id
    parent_id: str | None = Field(None, pattern=ITEM_ID_PATTERN)
    episode: int | None = None
    # Continue-watching taps: start from the user's saved position
    resume: bool | None = None


class BrowseRequest(BaseModel):
    action: str  # "browse_season" | "browse_series" | "browse_person"
    item_id: str = Field(..., pattern=ITEM_ID_PATTERN)  # season_id, series_id, or person_id
    parent_id: str | None = Field(None, pattern=ITEM_ID_PATTERN)  # series_id when browsing a season
    server_id: str


# ── Device Aliases ──────────────────────────────────────────────────

class DeviceAlias(BaseModel):
    alias: str
    pattern: str
    server_id: str


# ── Settings ───────────────────────────────────────────────────────

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


class UpdateMediaServerRequest(BaseModel):
    name: str
    url: str
    api_key: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    # None = "leave as-is" (omitting it in an API call must NOT silently
    # promote this server to default — the bundled UI always sends it)
    is_default: bool | None = None
    current_password: str | None = None


class DeleteMediaServerRequest(BaseModel):
    current_password: str


class DebugLoggingRequest(BaseModel):
    enabled: bool


class UserUpdateRequest(BaseModel):
    disabled: bool | None = None
    role: str | None = None

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if v not in {"admin", "user"}:
            raise ValueError("role must be admin or user")
        return v


class ProvisionUserRequest(BaseModel):
    """Pre-set a media account's app state before first sign-in."""
    username: str = Field(..., min_length=1, max_length=100)
    disabled: bool = False
    role: str = "user"

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"admin", "user"}:
            raise ValueError("role must be admin or user")
        return v



class ThemeRequest(BaseModel):
    theme: str

    @field_validator("theme")
    @classmethod
    def _valid_theme(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {
            "lcars", "holo", "nostromo", "discovery",
            "dradis", "2019", "thermal", "1997", "1138",
        }:
            raise ValueError(
                "theme must be one of: lcars, holo, nostromo, discovery, "
                "dradis, 2019, thermal, 1997, 1138"
            )
        return v


class SetupTTSRequest(BaseModel):
    enabled: bool = False
    url: str = Field("", max_length=512)
    model: str = Field("", max_length=256)
    voice: str = Field("af_heart", max_length=128)


class TestTTSVoiceRequest(BaseModel):
    url: str
    model: str
    voice: str


class TestUrlRequest(BaseModel):
    """Optional URL override for service test endpoints (falls back to stored)."""
    url: str | None = None


class OllamaModelsRequest(BaseModel):
    """Optional URL override for the models list (falls back to stored)."""
    url: str = Field("", max_length=512)


class LearnedPhraseDeleteRequest(BaseModel):
    phrase: str


class LearnedVisibilityRequest(BaseModel):
    public: bool


# ── History ─────────────────────────────────────────────────────────

class HistoryEntry(BaseModel):
    id: int
    timestamp: str
    transcript: str | None = None
    action: str | None = None
    result: str | None = None
    success: bool
    username: str | None = None
    parse_source: str | None = None
