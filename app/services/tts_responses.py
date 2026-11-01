"""Generate concise, natural-sounding text optimised for spoken TTS output."""

import random
import re

# Actions that should NOT trigger TTS (result is immediately visible/audible)
_SILENT_ACTIONS = {
    "pause", "unpause", "stop",
    "volume_up", "volume_down", "set_volume", "mute", "unmute",
}

# Prefixes stripped from adapter result strings before wrapping in templates
_STRIP_PREFIXES = [
    "Playing next: ",
    "Playing ",
    "Now playing ",
    "Next: ",       # play_next: 'Next: Series S01E02 "name"'
    "Resuming ",    # play_next: 'Resuming Series S01E02 "name" (43% watched)'
]

# Trailing "(43% watched)" reads fine on screen but is awkward spoken
_WATCHED_SUFFIX_RE = re.compile(r"\s*\(\d+% watched\)\.?\s*$")

# ── Phrase pools ──────────────────────────────────────────────────────

_PLAY_PHRASES = [
    "Now playing {text}",
    "Playing {text}",
    "{text}, coming right up",
]

_NEXT_EPISODE_PHRASES = [
    "Playing next episode",
    "On to the next episode",
    "Next episode coming up",
]

_SEEK_PHRASES = [
    "Jumped to {position}",
    "Skipped to {position}",
    "Moved to {position}",
]

_SEEK_FORWARD_PHRASES = [
    "Skipped forward",
    "Jumped ahead",
    "Fast forwarded",
]

_SEEK_FORWARD_AMOUNT_PHRASES = [
    "Skipped forward {amount}",
    "Jumped ahead {amount}",
    "Fast forwarded {amount}",
]

_SEEK_BACK_PHRASES = [
    "Skipped back",
    "Jumped back",
    "Rewound",
]

_SEEK_BACK_AMOUNT_PHRASES = [
    "Skipped back {amount}",
    "Jumped back {amount}",
    "Rewound {amount}",
]

_DISAMBIG_PHRASES = [
    "I found a few matches. Pick one from the screen.",
    "Multiple results came up. Choose one from the options.",
    "A few options here. Take a look and pick one.",
]

_RECENTLY_ADDED_PHRASES = [
    "Here are the recently added items",
    "Some new additions to your library",
    "Found some new additions, take a look",
    "Here is what was recently added",
]

_BROWSE_SEASONS_PHRASES = [
    "{count} seasons available",
    "{count} seasons to choose from",
]

_BROWSE_EPISODES_PHRASES = [
    "{count} episodes in this season",
    "{count} episodes, take a look",
]

_BROWSE_PERSON_PHRASES = [
    "Found {count} titles",
    "{count} titles in your library",
]

_SEARCH_PHRASES = [
    "Here are the results",
    "Found some results",
]

_ERROR_NOT_FOUND = [
    "Couldn't find anything matching that",
    "Nothing found for that title",
    "I didn't find any matches for that",
]

_ERROR_NO_SESSION = [
    "No active sessions. Open your media app on a device first.",
    "I can't see any devices. Make sure your media app is open.",
]

_ERROR_UNKNOWN_CMD = [
    "Sorry, I didn't catch that",
    "I didn't quite understand that one",
    "Not sure what you meant. Try again?",
]

_ERROR_GENERAL = [
    "Something went wrong. Please try again.",
    "That didn't work. Give it another try.",
]

# ── Error detection patterns (case-insensitive substring match) ──────

_NOT_FOUND_PATTERNS = ["no results", "not found", "couldn't find", "no items found"]
_NO_SESSION_PATTERNS = [
    "no active session", "no active emby", "no active jellyfin",
    "no session", "no devices", "no playback",
]


def _strip_prefix(text: str) -> str:
    """Strip known result prefixes/suffixes for cleaner TTS phrasing."""
    for prefix in _STRIP_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return _WATCHED_SUFFIX_RE.sub("", text)


def _detect_error_type(result: str) -> str:
    """Classify error result string into an error category."""
    lower = result.lower()
    for pat in _NOT_FOUND_PATTERNS:
        if pat in lower:
            return "not_found"
    for pat in _NO_SESSION_PATTERNS:
        if pat in lower:
            return "no_session"
    if "didn't understand" in lower or "unknown action" in lower:
        return "unknown_cmd"
    return "general"


def _extract_relative_amount(result: str) -> str | None:
    """Extract the amount from a relative-seek result.

    Handles both LLM-driven ("Skipped forward 30m.") and fast-path
    ("Seeked forward 10 seconds.") adapter outputs. Returns None if no
    recognisable amount segment is found.
    """
    m = re.search(r"(?:forward|back)\s+(.+?)\.?\s*$", result.strip(), re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_seek_position(result: str) -> str:
    """Extract the raw position string from a seek result.

    Adapter returns "Seeked to {position}." — grab everything between "to " and
    the trailing period. Falls back to the full string if the pattern is missing.
    """
    match = re.search(r"\bto\s+(.+?)\.?\s*$", result.strip())
    return match.group(1).strip() if match else result


def format_time_for_speech(time_str: str) -> str:
    """Convert compact time formats to spoken-word English.

    Accepts "30m", "1h30m", "1:30:00", "45", etc. Normalises overflow by summing
    components (e.g. "3h60m" → "4 hours"), handles singular/plural, and renders
    zero-duration inputs as "the beginning" so phrases like "Jumped to X" flow.
    """
    s = (time_str or "").strip()
    if not s:
        return time_str

    total_seconds: int | None = None

    # HH:MM:SS
    m = re.match(r"^(\d+):(\d{2}):(\d{2})$", s)
    if m:
        total_seconds = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])

    # MM:SS
    if total_seconds is None:
        m = re.match(r"^(\d+):(\d{2})$", s)
        if m:
            total_seconds = int(m[1]) * 60 + int(m[2])

    # Xh Ym Zs combinations (m must not be followed by 's' to avoid 'ms')
    if total_seconds is None:
        total = 0
        matched = False
        for val, unit in re.findall(r"(\d+)\s*([hms])", s, re.IGNORECASE):
            matched = True
            v = int(val)
            u = unit.lower()
            if u == "h":
                total += v * 3600
            elif u == "m":
                total += v * 60
            else:
                total += v
        if matched:
            total_seconds = total

    # Bare digits → seconds (matches _parse_seek_ticks behavior)
    if total_seconds is None and s.isdigit():
        total_seconds = int(s)

    if total_seconds is None:
        return time_str

    if total_seconds == 0:
        return "the beginning"

    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)

    parts: list[str] = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if seconds:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")

    return " ".join(parts)


def get_tts_text(
    action: str,
    result: str,
    success: bool,
    disambiguation: list | None = None,
) -> str | None:
    """Generate text to speak. Returns None if nothing should be spoken."""

    # Skip TTS for actions whose result is immediately obvious
    if action in _SILENT_ACTIONS:
        return None

    # Disambiguation response
    if disambiguation:
        count = len(disambiguation)
        if action in ("play_episode", "play_season"):
            return "Which season? Pick one from the screen."
        if action == "recently_added":
            return random.choice(_RECENTLY_ADDED_PHRASES)
        if action and action.startswith("browse_"):
            first_type = disambiguation[0].get("type", "") if disambiguation else ""
            if first_type == "Season":
                return random.choice(_BROWSE_SEASONS_PHRASES).format(count=count)
            if first_type == "Episode":
                return random.choice(_BROWSE_EPISODES_PHRASES).format(count=count)
            return random.choice(_BROWSE_PERSON_PHRASES).format(count=count)
        return random.choice(_DISAMBIG_PHRASES)

    # Successful actions
    if success:
        # Help text is a screen full of examples — don't read it all out
        if action == "help":
            return "Here are some things you can try."

        if action in ("play_search", "play_next", "play_episode", "play_season"):
            stripped = _strip_prefix(result)
            return random.choice(_PLAY_PHRASES).format(text=stripped)

        if action == "next_episode":
            return random.choice(_NEXT_EPISODE_PHRASES)

        if action == "seek":
            # Zero-seek ("go to the beginning") already reads naturally
            if "beginning" in result.lower():
                return "Starting from the beginning"
            position = _extract_seek_position(result)
            spoken = format_time_for_speech(position)
            return random.choice(_SEEK_PHRASES).format(position=spoken)

        if action == "seek_forward":
            amt = _extract_relative_amount(result)
            spoken = format_time_for_speech(amt) if amt else None
            if spoken:
                return random.choice(_SEEK_FORWARD_AMOUNT_PHRASES).format(amount=spoken)
            return random.choice(_SEEK_FORWARD_PHRASES)

        if action == "seek_back":
            amt = _extract_relative_amount(result)
            spoken = format_time_for_speech(amt) if amt else None
            if spoken:
                return random.choice(_SEEK_BACK_AMOUNT_PHRASES).format(amount=spoken)
            return random.choice(_SEEK_BACK_PHRASES)

        if action == "now_playing":
            # Result is already human-readable (e.g. "Currently playing X on Y")
            return result

        if action == "recently_added":
            return random.choice(_RECENTLY_ADDED_PHRASES)

        if action == "search":
            return random.choice(_SEARCH_PHRASES)

        if action == "smart_play":
            stripped = _strip_prefix(result)
            if "already playing" in result.lower():
                return "Already playing"
            if "nothing is playing" in result.lower():
                return result
            return random.choice(_PLAY_PHRASES).format(text=stripped)

        # Default: speak the result as-is for unrecognised successful actions
        return result

    # Error responses
    error_type = _detect_error_type(result)
    if error_type == "not_found":
        return random.choice(_ERROR_NOT_FOUND)
    if error_type == "no_session":
        return random.choice(_ERROR_NO_SESSION)
    if error_type == "unknown_cmd":
        return random.choice(_ERROR_UNKNOWN_CMD)
    return random.choice(_ERROR_GENERAL)
