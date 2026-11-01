import asyncio
import logging
import re

from pydantic import ValidationError

from ..adapters.base import (
    TICKS_PER_SECOND,
    AmbiguousMatchError,
    SessionAmbiguousError,
)
from ..models import Intent

logger = logging.getLogger("hailfrequency.intent")

# Allowed actions whitelist
ALLOWED_ACTIONS = {
    "play_search",
    "play_next",
    "play_episode",
    "play_season",
    "pause",
    "unpause",
    "stop",
    "next_episode",
    "seek",
    "seek_back",
    "seek_forward",
    "smart_play",
    "volume_up",
    "volume_down",
    "set_volume",
    "mute",
    "unmute",
    "subtitles_on",
    "subtitles_off",
    "audio_language",
    "shuffle",
    "play_collection",
    "mark_watched",
    "mark_unwatched",
    "sleep_timer",
    "cancel_sleep_timer",
    "time_left",
    "help",
    "search",
    "now_playing",
    "recently_added",
    "browse_series",
    "browse_person",
    "unknown",
}

# Required and optional fields per action
ACTION_FIELDS = {
    "play_search": {"required": ["query"], "optional": ["type", "device", "server"]},
    "play_next": {"required": ["series"], "optional": ["device", "server"]},
    "play_episode": {"required": ["series", "episode"], "optional": ["season", "device", "server"]},
    "play_season": {"required": ["series"], "optional": ["season", "device", "server"]},
    "pause": {"required": [], "optional": ["device", "server"]},
    "unpause": {"required": [], "optional": ["device", "server"]},
    "stop": {"required": [], "optional": ["device", "server"]},
    "next_episode": {"required": [], "optional": ["device", "server"]},
    "seek": {"required": ["position"], "optional": ["device", "server"]},
    "seek_back": {"required": [], "optional": ["amount", "device", "server"]},
    "seek_forward": {"required": [], "optional": ["amount", "device", "server"]},
    "smart_play": {"required": [], "optional": ["device", "server"]},
    "volume_up": {"required": [], "optional": ["device", "server"]},
    "volume_down": {"required": [], "optional": ["device", "server"]},
    "set_volume": {"required": ["amount"], "optional": ["device", "server"]},
    "mute": {"required": [], "optional": ["device", "server"]},
    "unmute": {"required": [], "optional": ["device", "server"]},
    "subtitles_on": {"required": [], "optional": ["language", "device", "server"]},
    "subtitles_off": {"required": [], "optional": ["device", "server"]},
    "audio_language": {"required": ["language"], "optional": ["device", "server"]},
    "shuffle": {"required": [], "optional": [
        "series", "type", "genre", "unwatched", "decade", "top_rated",
        "device", "server",
    ]},
    "play_collection": {"required": ["query"], "optional": ["type", "shuffled", "device", "server"]},
    "mark_watched": {"required": [], "optional": ["device", "server"]},
    "mark_unwatched": {"required": [], "optional": ["device", "server"]},
    "sleep_timer": {"required": ["amount"], "optional": ["device", "server"]},
    "cancel_sleep_timer": {"required": [], "optional": ["server"]},
    "time_left": {"required": [], "optional": ["device", "server"]},
    "help": {"required": [], "optional": []},
    "search": {"required": ["query"], "optional": ["type"]},
    "now_playing": {"required": [], "optional": ["server"]},
    "recently_added": {"required": [], "optional": ["type", "genre", "server"]},
    "browse_series": {"required": ["series"], "optional": ["season", "server"]},
    "browse_person": {"required": ["person"], "optional": ["type", "server"]},
    "unknown": {"required": [], "optional": ["message"]},
}

# Valid media types
VALID_TYPES = {
    "movie", "series", "episode", "music", "audiobook", "person",
    "collection", "playlist",
}

MAX_STRING_LENGTH = 500

# Intent fields that are NOT strings (everything else is coerced to str)
_INT_FIELDS = ("season", "episode")
_BOOL_FIELDS = ("unwatched", "top_rated", "shuffled")
_TRUE_WORDS = {"true", "yes", "y", "1", "on"}

# Control character pattern (strip anything that isn't printable)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _coerce_bool(value) -> bool:
    """LLM booleans arrive as bools, ints, or words ("true"/"yes")."""
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_WORDS
    return bool(value)

# ── Fast-path for simple commands (bypass LLM) ──────────────────────

_FAST_PATTERNS: dict[str, Intent] = {
    # Stop
    "stop": Intent(action="stop"),
    "halt": Intent(action="stop"),
    # Pause
    "pause": Intent(action="pause"),
    # Resume
    "resume": Intent(action="unpause"),
    "unpause": Intent(action="unpause"),
    "continue": Intent(action="unpause"),
    # Smart play (resume if paused, else helpful message)
    "play": Intent(action="smart_play"),
    # Next episode / skip
    "next": Intent(action="next_episode"),
    "skip": Intent(action="next_episode"),
    "next episode": Intent(action="next_episode"),
    # Next episode of whatever is currently playing
    "play the next episode": Intent(action="next_episode"),
    "play next episode": Intent(action="next_episode"),
    # Seek back 10s
    "rewind": Intent(action="seek_back"),
    "go back": Intent(action="seek_back"),
    "skip back": Intent(action="seek_back"),
    # Seek forward 10s
    "fast forward": Intent(action="seek_forward"),
    "skip forward": Intent(action="seek_forward"),
    "go forward": Intent(action="seek_forward"),
    # Volume / mute (immediately audible — no confirmation needed)
    "volume up": Intent(action="volume_up"),
    "turn it up": Intent(action="volume_up"),
    "turn the volume up": Intent(action="volume_up"),
    "louder": Intent(action="volume_up"),
    "volume down": Intent(action="volume_down"),
    "turn it down": Intent(action="volume_down"),
    "turn the volume down": Intent(action="volume_down"),
    "quieter": Intent(action="volume_down"),
    "mute": Intent(action="mute"),
    "mute it": Intent(action="mute"),
    "silence": Intent(action="mute"),
    "unmute": Intent(action="unmute"),
    "unmute it": Intent(action="unmute"),
    "sound on": Intent(action="unmute"),
    # Watched state
    "mark this as watched": Intent(action="mark_watched"),
    "mark as watched": Intent(action="mark_watched"),
    "mark watched": Intent(action="mark_watched"),
    "mark this watched": Intent(action="mark_watched"),
    "ive seen this": Intent(action="mark_watched"),
    "ive watched this": Intent(action="mark_watched"),
    "ive already seen this": Intent(action="mark_watched"),
    "mark this as unwatched": Intent(action="mark_unwatched"),
    "mark as unwatched": Intent(action="mark_unwatched"),
    "mark unwatched": Intent(action="mark_unwatched"),
    "mark this unwatched": Intent(action="mark_unwatched"),
    # Time remaining
    "how long is left": Intent(action="time_left"),
    "how long left": Intent(action="time_left"),
    "how much is left": Intent(action="time_left"),
    "how much left": Intent(action="time_left"),
    "how much longer": Intent(action="time_left"),
    "how much time is left": Intent(action="time_left"),
    "time left": Intent(action="time_left"),
    "time remaining": Intent(action="time_left"),
    # Help
    "help": Intent(action="help"),
    "what can i say": Intent(action="help"),
    "what can you do": Intent(action="help"),
    "what can i ask": Intent(action="help"),
    "what do i say": Intent(action="help"),
    "show commands": Intent(action="help"),
    # What's playing (apostrophes stripped by normalizer)
    "whats playing": Intent(action="now_playing"),
    "now playing": Intent(action="now_playing"),
    "what is playing": Intent(action="now_playing"),
    "whats on": Intent(action="now_playing"),
    # Recently added (no type)
    "whats new": Intent(action="recently_added"),
    "what is new": Intent(action="recently_added"),
    "recently added": Intent(action="recently_added"),
    "anything new": Intent(action="recently_added"),
    "show me whats new": Intent(action="recently_added"),
}


# Media-type words users actually say (order matters: longest first)
_RA_TYPES = r"(?P<rtype>movies?|films?|tv\s*shows?|tv\s*series|shows?|series|tv|telly|music|audiobooks?)"

# Genre words the fast paths recognise (normalizer strips punctuation, so
# "sci-fi" arrives as "scifi"). Keep in sync with _GENRE_ALIASES in
# adapters/emby.py, which maps these onto the server's real genre names.
_GENRE_WORDS = (
    r"(?:sci\s*fi|science\s+fiction|horror|comed(?:y|ies)|action|adventure"
    r"|animation|animated|anime|thrillers?|dramas?|crime|documentar(?:y|ies)"
    r"|fantasy|romance|romantic|myster(?:y|ies)|westerns?|war|history"
    r"|historical|musicals?|family|kids|christmas|superhero(?:es)?)"
)

# "show me the new movies", "what new sci-fi tv shows are there",
# "any new films", "list the latest horror shows please" ...
_RECENTLY_ADDED_RE = re.compile(
    r"^(?:"
    r"(?:show|list)\s+(?:me\s+|us\s+)?(?:the\s+)?"
    r"|what\s+are\s+the\s+"
    r"|whats\s+the\s+"
    r"|whats\s+"
    r"|what\s+"
    r"|are\s+there\s+any\s+"
    r"|got\s+any\s+"
    r"|any\s+"
    r")?"
    r"(?:latest|new|newest|recent|recently\s+added)\s+"
    rf"(?:(?P<genre>{_GENRE_WORDS})\s+)?"
    + _RA_TYPES +
    r"(?:\s+(?:are\s+there|do\s+we\s+have|have\s+we\s+got|out|available))?"
    r"(?:\s+please)?$"
)

# "whats new in movies", "whats new on tv", "whats new with the shows"
_RECENTLY_ADDED_IN_RE = re.compile(
    r"^whats\s+new\s+(?:in|on|with)\s+(?:the\s+)?"
    rf"(?:(?P<genre>{_GENRE_WORDS})\s+)?"
    + _RA_TYPES + r"(?:\s+please)?$"
)

# ── Templated play commands (series + season/episode numbers) ───────
# These have rigid structure around the free-form title, so they skip
# the LLM entirely. Anything that doesn't match falls through to Ollama.

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90, "hundred": 100,
}

_TENS_WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _parse_number(token: str | None) -> int | None:
    """Parse '3', 'three', 'twenty one' or 'twentyone' to an int."""
    if not token:
        return None
    token = token.strip()
    if token.isdigit():
        return int(token)
    words = token.split()
    if len(words) == 1:
        w = words[0]
        if w in _NUM_WORDS:
            return _NUM_WORDS[w]
        # Fused compounds from punctuation stripping ("twenty-one" → "twentyone")
        for tens_word, tens_val in _TENS_WORDS.items():
            if w.startswith(tens_word) and w[len(tens_word):] in _NUM_WORDS:
                unit = _NUM_WORDS[w[len(tens_word):]]
                if unit < 10:
                    return tens_val + unit
        return None
    if len(words) == 2:
        tens, unit = _TENS_WORDS.get(words[0]), _NUM_WORDS.get(words[1])
        if tens and unit and unit < 10:
            return tens + unit
    return None


# One number token: digits, or one/two number words
_NUM = r"(?:\d+|[a-z]+(?:\s+[a-z]+)?)"
# Optional spoken filler ("So play...", "Okay play...") + polite prefix
_VERB = r"(?:(?:so|ok|okay|hey|now|um|uh|and|then)\s+)?(?:please\s+)?(?:play|watch)"
_DEV = r"(?:\s+on\s+(?P<device>.+?))?"
_PLEASE = r"(?:\s+please)?"

# Tried in order — most specific first. Device capture ("on X") only on
# series-first forms where it follows the number; "of <series>" forms skip
# it because titles legitimately contain "on" (e.g. Life on Mars).
_PLAY_TEMPLATES = [
    # play the next episode of <series>
    ("play_next", re.compile(
        rf"^{_VERB}\s+(?:the\s+)?next\s+episode\s+of\s+(?P<series>.+?){_PLEASE}$")),
    # play <series> season N episode M [on <device>]
    ("play_episode", re.compile(
        rf"^{_VERB}\s+(?P<series>.+?)\s+season\s+(?P<season>{_NUM})"
        rf"\s+episode\s+(?P<episode>{_NUM}){_DEV}{_PLEASE}$")),
    # play season N episode M of <series>
    ("play_episode", re.compile(
        rf"^{_VERB}\s+season\s+(?P<season>{_NUM})\s+episode\s+(?P<episode>{_NUM})"
        rf"\s+of\s+(?P<series>.+?){_PLEASE}$")),
    # play episode M of <series>
    ("play_episode", re.compile(
        rf"^{_VERB}\s+episode\s+(?P<episode>{_NUM})\s+of\s+(?P<series>.+?){_PLEASE}$")),
    # play season N of <series>
    ("play_season", re.compile(
        rf"^{_VERB}\s+season\s+(?P<season>{_NUM})\s+of\s+(?P<series>.+?){_PLEASE}$")),
    # play <series> episode M [on <device>]
    ("play_episode", re.compile(
        rf"^{_VERB}\s+(?P<series>.+?)\s+episode\s+(?P<episode>{_NUM}){_DEV}{_PLEASE}$")),
    # play <series> season N [on <device>]
    ("play_season", re.compile(
        rf"^{_VERB}\s+(?P<series>.+?)\s+season\s+(?P<season>{_NUM}){_DEV}{_PLEASE}$")),
]


def _try_play_templates(normalized: str) -> Intent | None:
    """Match templated play commands. Returns Intent or None."""
    for action, pattern in _PLAY_TEMPLATES:
        m = pattern.match(normalized)
        if not m:
            continue
        groups = m.groupdict()
        series = (groups.get("series") or "").strip()
        if not series:
            continue
        season = episode = None
        if "season" in groups and groups["season"] is not None:
            season = _parse_number(groups["season"])
            if season is None:
                continue  # captured a non-number — not this template
        if "episode" in groups and groups["episode"] is not None:
            episode = _parse_number(groups["episode"])
            if episode is None:
                continue
        device = (groups.get("device") or "").strip() or None
        logger.debug(
            "try_fast_parse: template %s series='%s' season=%s episode=%s device=%s",
            action, series, season, episode, device,
        )
        return Intent(
            action=action, series=series,
            season=season, episode=episode, device=device,
        )
    return None


# "Restart / go to the beginning" → seek to position 0.
# Anchored to the WHOLE phrase so anything with a title in it
# (e.g. "play the matrix from the beginning") falls through to the LLM.
_RESTART_RE = re.compile(
    r"^(?:lets\s+|let\s+us\s+|please\s+)?(?:"
    r"restart(?:\s+(?:it|this|the\s+(?:movie|show|episode|video)))?"
    r"|start(?:\s+it)?\s+(?:over|again)(?:\s+again)?"
    r"|(?:go|jump|skip|rewind)(?:\s+back)?\s+to\s+the\s+(?:beginning|start)"
    r"|back\s+to\s+the\s+(?:beginning|start)"
    r"|(?:(?:start|play|watch|go)(?:\s+it)?\s+)?from\s+the\s+(?:beginning|start|top)"
    r"|take\s+it\s+from\s+the\s+top"
    r")(?:\s+please)?$"
)

# ── Volume / subtitles / shuffle fast paths ─────────────────────────

_FILLER = r"(?:(?:so|ok|okay|hey|now|um|uh|and|then)\s+)?(?:please\s+)?"

# Fallback gate (Ollama unavailable): ONLY phrases of the shape
# "<filler> play/watch/put on <something>" may become a literal search.
# Anything else must fail honestly rather than search for its words.
# "put on" is fallback-only by design and is deliberately absent from the
# fast-path _VERB templates.
_PLAY_SHAPED_RE = re.compile(
    rf"^{_FILLER}(?:play|watch|put\s+on)\s+(?P<q>.+?){_PLEASE}$"
)


def fallback_play_search(transcript: str) -> "Intent | None":
    """Literal-search intent for a play-shaped phrase, else None.

    Used ONLY when Ollama cannot answer (down/unconfigured). Device names
    spoken in the phrase are NOT parsed out (titles contain "on" — Life on
    Mars); offline device targeting is the UI selector's job.
    """
    m = _PLAY_SHAPED_RE.match(normalize_transcript(transcript))
    if not m:
        return None
    query = m.group("q").strip()
    if not query:
        return None
    logger.debug("fallback_play_search: query='%s'", query)
    return Intent(action="play_search", query=query)


# "set the volume to 40", "volume 50 percent", "turn the volume to sixty"
_SET_VOLUME_RE = re.compile(
    rf"^{_FILLER}(?:set|turn|put)?\s*(?:the\s+)?volume\s+(?:to\s+|at\s+)?"
    rf"(?P<level>{_NUM})\s*(?:percent|per\s+cent)?(?:\s+please)?$"
)

# Language names the subtitle fast path recognises.
# Keep in sync with _SUBTITLE_LANGS in adapters/emby.py.
_LANG_WORDS = (
    r"(?:english|french|german|spanish|italian|dutch|portuguese|japanese"
    r"|chinese|korean|russian|arabic|hindi|swedish|norwegian|danish|finnish"
    r"|polish|turkish|greek|hebrew|czech|hungarian|romanian|thai|vietnamese"
    r"|indonesian)"
)

# "turn on subtitles", "enable french subtitles", "put the captions on"
_SUBS_ON_RE = re.compile(
    rf"^{_FILLER}(?:turn\s+on|switch\s+on|enable|put\s+on)\s+(?:the\s+)?"
    rf"(?:(?P<lang>{_LANG_WORDS})\s+)?(?:subtitles|subs|captions)(?:\s+please)?$"
)
# "subtitles on", "turn the subtitles on", "french subtitles on"
_SUBS_ON_TRAIL_RE = re.compile(
    rf"^{_FILLER}(?:(?:turn|switch|put)\s+)?(?:the\s+)?"
    rf"(?:(?P<lang>{_LANG_WORDS})\s+)?(?:subtitles|subs|captions)\s+on(?:\s+please)?$"
)
# "turn off subtitles", "disable captions", "no subtitles"
_SUBS_OFF_RE = re.compile(
    rf"^{_FILLER}(?:(?:turn\s+off|switch\s+off|disable|remove|no)\s+(?:the\s+)?"
    rf"(?:subtitles|subs|captions)"
    r"|(?:(?:turn|switch)\s+)?(?:the\s+)?(?:subtitles|subs|captions)\s+off"
    r")(?:\s+please)?$"
)

# Audio track by language: "switch the audio to french", "change language to
# english", "french audio", "play it in english", "english audio track".
_AUDIO_LANG_RE = re.compile(
    rf"^{_FILLER}(?:"
    rf"(?:switch|change|set|put)\s+(?:the\s+)?(?:audio|language|audio\s+track|"
    rf"audio\s+language|sound|dub)\s+(?:to|into)\s+(?P<lang1>{_LANG_WORDS})"
    rf"|(?:(?:switch|change|put|play)\s+(?:it\s+|this\s+)?(?:to|in|into)\s+)?"
    rf"(?:the\s+)?(?P<lang2>{_LANG_WORDS})\s+(?:audio|dub|audio\s+track|language|sound)"
    rf"|(?:audio|sound|dub)\s+in\s+(?P<lang4>{_LANG_WORDS})"
    rf"|(?:play|watch)\s+(?:it|this)\s+in\s+(?P<lang3>{_LANG_WORDS})"
    rf")(?:\s+please)?$"
)

# Decade words → starting year (post-normalization: "80's" arrives as "80s")
_DECADES = {
    "50s": 1950, "1950s": 1950, "fifties": 1950,
    "60s": 1960, "1960s": 1960, "sixties": 1960,
    "70s": 1970, "1970s": 1970, "seventies": 1970,
    "80s": 1980, "1980s": 1980, "eighties": 1980,
    "90s": 1990, "1990s": 1990, "nineties": 1990,
    "2000s": 2000, "noughties": 2000,
    "2010s": 2010, "2020s": 2020,
}
_DECADE_WORDS = r"(?:" + "|".join(_DECADES) + r")"

_QUALITY_WORDS = r"(?:good|great|decent|top\s+rated|highly\s+rated|best)"

# "shuffle sci-fi tv shows", "shuffle unwatched horror movies", "shuffle
# some random comedy series" (checked BEFORE _SHUFFLE_RE, which would
# swallow the genre as a title)
_SHUFFLE_GENRE_RE = re.compile(
    rf"^{_FILLER}shuffle\s+(?:some\s+)?(?:random\s+)?(?:(?P<unw>unwatched)\s+)?"
    rf"(?P<genre>{_GENRE_WORDS})"
    r"\s+(?P<what>tv\s+shows?|tv\s+series|shows?|series|episodes?|tv"
    r"|movies?|films?)(?:\s+please)?$"
)
# "play my james bond collection", "play the chill playlist shuffled",
# "shuffle my christmas playlist" (also before _SHUFFLE_RE)
# Lone articles/determiners that mean a lazy .+? query captured nothing real
_STOP_QUERIES = {"the", "a", "an", "my", "some", "this", "that"}

_KIND_WORDS = r"(?P<kind>collection|box\s*set|playlist)"
_COLLECTION_RE = re.compile(
    rf"^{_FILLER}(?:play|watch|put\s+on)\s+(?:my\s+|the\s+)?(?P<q>.+?)\s+"
    rf"{_KIND_WORDS}(?:\s+(?P<shuf>shuffled|on\s+shuffle))?(?:\s+please)?$"
)
_SHUFFLE_COLLECTION_RE = re.compile(
    rf"^{_FILLER}shuffle\s+(?:my\s+|the\s+)?(?P<q>.+?)\s+"
    rf"{_KIND_WORDS}(?:\s+please)?$"
)
# "shuffle the unwatched episodes of frasier", "shuffle unwatched frasier"
_SHUFFLE_UNWATCHED_RE = re.compile(
    rf"^{_FILLER}shuffle\s+(?:the\s+)?unwatched\s+(?:episodes\s+of\s+)?"
    r"(?P<series>.+?)(?:\s+please)?$"
)
# "shuffle <series>"
_SHUFFLE_RE = re.compile(
    rf"^{_FILLER}shuffle\s+(?P<series>.+?)(?:\s+please)?$"
)
_ARTICLE = r"(?:a\s+|an\s+|any\s+|some\s+)?"
# Qualifier soup: one or more of random/unwatched/quality/decade/genre in
# any order ("a good 80s action movie", "an unwatched random horror film").
# Repeated named groups keep the LAST match, which is fine — each word class
# appears at most once in natural speech.
_QUALIFIERS = (
    r"(?:(?:random|(?P<unw>unwatched)|(?P<qual>" + _QUALITY_WORDS + r")"
    rf"|(?P<decade>{_DECADE_WORDS})|(?P<genre>{_GENRE_WORDS}))\s+)+"
)
_UNSEEN_TAIL = (
    r"(?:\s+(?P<unw2>(?:that\s+)?(?:i\s+|we\s+)?(?:havent|have\s+not)\s+"
    r"(?:seen|watched)(?:\s+yet)?))?"
)

# "stop playing in 30 minutes", "sleep timer for an hour", "turn it off in
# twenty minutes", "stop in half an hour"
_SLEEP_TIMER_RE = re.compile(
    rf"^{_FILLER}(?:"
    r"(?:set\s+(?:a\s+)?)?sleep\s+timer\s+(?:for\s+|of\s+)?"
    r"|(?:stop(?:\s+playing)?(?:\s+it)?|turn\s+(?:it\s+)?off|go\s+to\s+sleep)\s+in\s+"
    r")"
    rf"(?:(?P<half>half\s+an\s+hour)|an\s+(?P<anhour>hour)"
    rf"|(?P<num>{_NUM})\s*(?P<unit>minutes?|mins?|hours?))"
    r"(?:\s+please)?$"
)
_CANCEL_SLEEP_RE = re.compile(
    rf"^{_FILLER}(?:cancel|stop|clear|turn\s+off|remove)\s+(?:the\s+)?"
    r"sleep\s+timer(?:\s+please)?$"
)

# "how long is left in this episode", "how much time is remaining"
_TIME_LEFT_RE = re.compile(
    rf"^{_FILLER}how\s+(?:long|much)(?:\s+time)?(?:\s+is)?\s+"
    r"(?:left|remaining)"
    r"(?:\s+(?:in|of|on)\s+(?:this|the)\s+(?:episode|movie|film|show|video))?"
    r"(?:\s+please)?$"
)
# "play a random episode of <series>"
_RANDOM_EP_RE = re.compile(
    rf"^{_FILLER}(?:play|watch|put\s+on)\s+{_ARTICLE}random\s+episode"
    rf"\s+of\s+(?P<series>.+?)(?:\s+please)?$"
)
# "play a random sci-fi episode", "play an unwatched horror episode"
_RANDOM_GENRE_EP_RE = re.compile(
    rf"^{_FILLER}(?:play|watch|put\s+on)\s+{_ARTICLE}{_QUALIFIERS}"
    rf"episode{_UNSEEN_TAIL}(?:\s+please)?$"
)
# "play a random movie", "play an 80s action film", "play a good sci-fi
# movie I haven't seen" — at least one qualifier required, so plain
# "play a movie" and real titles fall through
_RANDOM_MOVIE_RE = re.compile(
    rf"^{_FILLER}(?:play|watch|put\s+on)\s+{_ARTICLE}{_QUALIFIERS}"
    rf"(?:movie|film){_UNSEEN_TAIL}(?:\s+please)?$"
)

_TYPE_ALIASES = {
    "movie": "movie", "movies": "movie",
    "film": "movie", "films": "movie",
    "show": "series", "shows": "series",
    "tv show": "series", "tv shows": "series",
    "tvshow": "series", "tvshows": "series",
    "tv series": "series", "tvseries": "series",
    "tv": "series", "telly": "series",
    "series": "series",
    "episode": "episode", "episodes": "episode",
    "music": "music", "song": "music", "songs": "music",
    "audiobook": "audiobook", "audiobooks": "audiobook",
}


def normalize_transcript(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — the canonical
    form used by the fast-path dict, the learned-phrase table, and the
    fallback gate. All exact-match tiers MUST share this."""
    normalized = re.sub(r"[^\w\s]", "", text.strip().lower()).strip()
    return re.sub(r"\s+", " ", normalized)


def try_fast_parse(transcript: str) -> Intent | None:
    """Try to match simple commands without LLM. Returns Intent or None."""
    normalized = normalize_transcript(transcript)
    result = _FAST_PATTERNS.get(normalized)
    if result:
        logger.debug("try_fast_parse: normalized='%s' matched=%s", normalized, result.action)
        # Return a copy — the shared pattern Intents must never be mutated
        # (the voice endpoint sets .device on the returned object)
        return result.model_copy(deep=True)

    # Recently added with type (e.g. "show latest movies", "new tv shows",
    # "what new films are there", "whats new on tv")
    m = _RECENTLY_ADDED_RE.match(normalized) or _RECENTLY_ADDED_IN_RE.match(normalized)
    if m:
        raw_type = re.sub(r"\s+", " ", m.group("rtype").strip())
        media_type = _TYPE_ALIASES.get(raw_type)
        genre = m.group("genre")
        logger.debug(
            "try_fast_parse: recently_added type='%s' -> '%s' genre=%s",
            raw_type, media_type, genre,
        )
        return Intent(action="recently_added", type=media_type, genre=genre)

    # Restart / start over / go to the beginning → seek to 0 (resumes after)
    if _RESTART_RE.match(normalized):
        logger.debug("try_fast_parse: restart matched, seeking to 0")
        return Intent(action="seek", position="0")

    # Set volume to an absolute level
    m = _SET_VOLUME_RE.match(normalized)
    if m:
        level = _parse_number(m.group("level"))
        if level is not None and 0 <= level <= 100:
            logger.debug("try_fast_parse: set_volume %d", level)
            return Intent(action="set_volume", amount=str(level))

    # Subtitles on/off (optionally with a language)
    if _SUBS_OFF_RE.match(normalized):
        logger.debug("try_fast_parse: subtitles_off")
        return Intent(action="subtitles_off")
    m = _SUBS_ON_RE.match(normalized) or _SUBS_ON_TRAIL_RE.match(normalized)
    if m:
        logger.debug("try_fast_parse: subtitles_on lang=%s", m.group("lang"))
        return Intent(action="subtitles_on", language=m.group("lang"))

    # Audio track language
    m = _AUDIO_LANG_RE.match(normalized)
    if m:
        lang = next(g for g in m.groups() if g)
        logger.debug("try_fast_parse: audio_language lang=%s", lang)
        return Intent(action="audio_language", language=lang)

    # Time remaining (suffixed variants; short forms are in the dict)
    if _TIME_LEFT_RE.match(normalized):
        logger.debug("try_fast_parse: time_left")
        return Intent(action="time_left")

    # Sleep timer
    if _CANCEL_SLEEP_RE.match(normalized):
        logger.debug("try_fast_parse: cancel_sleep_timer")
        return Intent(action="cancel_sleep_timer")
    m = _SLEEP_TIMER_RE.match(normalized)
    if m:
        amount = None
        if m.group("half"):
            amount = "30m"
        elif m.group("anhour"):
            amount = "1h"
        else:
            n = _parse_number(m.group("num"))
            if n is not None:
                unit = "h" if m.group("unit").startswith("h") else "m"
                amount = f"{n}{unit}"
        if amount:
            logger.debug("try_fast_parse: sleep_timer %s", amount)
            return Intent(action="sleep_timer", amount=amount)

    # Collections / playlists (before the bare shuffle form). A lone article
    # captured as the query ("play the box set" → q="the") means the article
    # slot wasn't consumed — let it fall through rather than search for "the".
    m = _COLLECTION_RE.match(normalized)
    if m and m.group("q").strip() not in _STOP_QUERIES:
        kind = "collection" if m.group("kind").startswith(("collection", "box")) else "playlist"
        shuffled = bool(m.group("shuf"))
        logger.debug("try_fast_parse: play_collection '%s' kind=%s", m.group("q"), kind)
        return Intent(
            action="play_collection", query=m.group("q").strip(),
            type=kind, shuffled=shuffled or None,
        )
    m = _SHUFFLE_COLLECTION_RE.match(normalized)
    if m and m.group("q").strip() not in _STOP_QUERIES:
        kind = "collection" if m.group("kind").startswith(("collection", "box")) else "playlist"
        logger.debug("try_fast_parse: shuffle collection '%s'", m.group("q"))
        return Intent(
            action="play_collection", query=m.group("q").strip(),
            type=kind, shuffled=True,
        )

    # Shuffle / random (genre and unwatched forms before the bare-series
    # form, which would otherwise swallow the qualifiers as a title)
    m = _RANDOM_MOVIE_RE.match(normalized)
    if m:
        g = m.groupdict()
        decade = _DECADES.get(g.get("decade") or "")
        logger.debug("try_fast_parse: random movie %s", g)
        return Intent(
            action="shuffle", type="movie", genre=g.get("genre"),
            unwatched=bool(g.get("unw") or g.get("unw2")) or None,
            decade=str(decade) if decade else None,
            top_rated=bool(g.get("qual")) or None,
        )
    m = _RANDOM_GENRE_EP_RE.match(normalized)
    if m:
        g = m.groupdict()
        logger.debug("try_fast_parse: random episode %s", g)
        return Intent(
            action="shuffle", type="episode", genre=g.get("genre"),
            unwatched=bool(g.get("unw") or g.get("unw2")) or None,
        )
    m = _SHUFFLE_GENRE_RE.match(normalized)
    if m:
        what = m.group("what")
        mtype = "movie" if what.startswith(("movie", "film")) else "series"
        logger.debug("try_fast_parse: shuffle genre=%s type=%s", m.group("genre"), mtype)
        return Intent(
            action="shuffle", type=mtype, genre=m.group("genre"),
            unwatched=bool(m.group("unw")) or None,
        )
    m = _SHUFFLE_UNWATCHED_RE.match(normalized)
    if m:
        logger.debug("try_fast_parse: shuffle unwatched series='%s'", m.group("series"))
        return Intent(action="shuffle", series=m.group("series").strip(), unwatched=True)
    m = _RANDOM_EP_RE.match(normalized) or _SHUFFLE_RE.match(normalized)
    if m:
        series = m.group("series").strip()
        if series:
            logger.debug("try_fast_parse: shuffle series='%s'", series)
            return Intent(action="shuffle", series=series)

    # Templated play commands (series + season/episode numbers)
    intent = _try_play_templates(normalized)
    if intent:
        return intent

    logger.debug("try_fast_parse: normalized='%s' no match", normalized)
    return None


def _sanitize_string(value: str) -> str:
    """Strip control characters and limit length."""
    cleaned = _CONTROL_CHARS.sub("", value)
    return cleaned[:MAX_STRING_LENGTH]


def validate_intent(raw_json: dict) -> Intent | str:
    """Validate LLM output against schema.

    Returns a validated Intent on success, or an error string on failure.
    """
    if not isinstance(raw_json, dict):
        return "Invalid response format from assistant."

    action = raw_json.get("action")
    if not action or action not in ALLOWED_ACTIONS:
        # Echoes LLM output into result/TTS/logs — sanitize and truncate
        return f"Unknown action: {_sanitize_string(str(action))[:50]}"

    fields = ACTION_FIELDS.get(action, {"required": [], "optional": []})

    # Check required fields
    for field in fields["required"]:
        if not raw_json.get(field):
            return f"Missing required field '{field}' for action '{action}'."

    # Build validated intent dict
    intent_data = {"action": action}
    all_fields = fields["required"] + fields["optional"]

    for field in all_fields:
        value = raw_json.get(field)
        if value is None:
            continue
        if field in _INT_FIELDS:
            pass  # coerced below
        elif field in _BOOL_FIELDS:
            value = _coerce_bool(value)
        else:
            # Every other Intent field is a string. Models emit bare numbers
            # for numeric-looking values ({"query": 1899}, {"amount": 40})
            # and pydantic rejects int-for-str, so coerce first.
            value = _sanitize_string(str(value))
        intent_data[field] = value

    # Coerce season/episode to int if present
    for num_field in _INT_FIELDS:
        if num_field in intent_data and intent_data[num_field] is not None:
            try:
                intent_data[num_field] = int(intent_data[num_field])
            except (ValueError, TypeError):
                intent_data.pop(num_field, None)

    # Normalize decade ("80s"/"eighties"/1980) to a starting year string
    if intent_data.get("decade") is not None:
        d = str(intent_data["decade"]).lower().strip()
        year = _DECADES.get(d) or (int(d) if d.isdigit() and len(d) == 4 else None)
        intent_data["decade"] = str(year) if year else None

    # play_episode needs a usable episode number after coercion
    if action == "play_episode" and not intent_data.get("episode"):
        return "I didn't catch which episode number to play."

    # Normalize the type field: LLMs say "tv", "film", "shows" etc. —
    # alias-map before validating rather than silently dropping the filter
    if "type" in intent_data and intent_data["type"]:
        t = intent_data["type"].lower().strip()
        t = _TYPE_ALIASES.get(t, t)
        intent_data["type"] = t if t in VALID_TYPES else None

    # For unknown action, include the message
    if action == "unknown" and "message" not in intent_data:
        intent_data["message"] = raw_json.get("message", "Could not understand the command")

    # Last line of defence: model output is untrusted, and a schema
    # rejection must surface as an honest failure, never a 500
    try:
        return Intent(**intent_data)
    except ValidationError as exc:
        logger.warning("Intent failed schema validation: %s", exc)
        return "Invalid response format from assistant."


HELP_TEXT = """\
Here's a taste of what you can say:

PLAY
• "Play The Expanse on the Shield"
• "Play Voyager season 3 episode 4"
• "Play the next episode of Bosch"
• "Play my James Bond collection"

RANDOM & SHUFFLE
• "Play a random sci-fi movie I haven't seen"
• "Play an 80s action film"
• "Shuffle Star Trek Enterprise"
• "Shuffle unwatched episodes of Frasier"

CONTROL
• "Pause" · "Stop" · "Volume up" · "Mute"
• "Turn on subtitles" · "French subtitles on" · "Switch the audio to English"
• "Skip forward 5 minutes" · "Start from the beginning"
• "Stop playing in 30 minutes"

BROWSE & MORE
• "What's new?" · "Latest sci-fi shows"
• "Show me movies with Tom Hanks"
• "Mark this as watched\""""


async def _ensure_playing(adapter, device: str | None) -> None:
    """Verify-and-retry unpause after a seek.

    The server returns 200 on /Playing/Unpause even when a client silently
    drops the command because it arrived too close to a Seek (NVIDIA Shield
    does this). One short state check plus a single retry handles the race
    without adding latency to the common case.
    """
    for _ in range(2):
        try:
            await asyncio.sleep(0.3)
            state = await adapter.get_playback_state(device=device)
        except Exception:
            return
        if not state or state.get("state") != "Paused":
            return
        try:
            await adapter.unpause(device=device)
        except Exception:
            return


async def route_intent(intent: Intent, adapter) -> str:
    """Execute the intent against the media server adapter.

    Returns a human-readable result string.
    Raises SessionAmbiguousError if device disambiguation is needed.
    """
    logger.debug("route_intent: action=%s", intent.action)
    try:
        match intent.action:
            case "play_search":
                return await adapter.play_search(
                    query=intent.query,
                    media_type=intent.type,
                    device=intent.device,
                )
            case "play_next":
                return await adapter.play_next(
                    series_name=intent.series,
                    device=intent.device,
                )
            case "play_episode":
                return await adapter.play_episode(
                    series_name=intent.series,
                    episode=intent.episode,
                    season=intent.season,
                    device=intent.device,
                )
            case "play_season":
                return await adapter.play_season(
                    series_name=intent.series,
                    season=intent.season,
                    device=intent.device,
                )
            case "pause":
                return await adapter.pause(device=intent.device)
            case "unpause":
                return await adapter.unpause(device=intent.device)
            case "stop":
                return await adapter.stop(device=intent.device)
            case "next_episode":
                return await adapter.next_episode(device=intent.device)
            case "seek":
                result = await adapter.seek(
                    position=intent.position,
                    device=intent.device,
                )
                # Resume playback after the seek (user was auto-paused by the
                # mic press). Some clients drop an unpause that arrives too
                # close to the seek — verify and retry if needed.
                try:
                    await adapter.unpause(device=intent.device)
                except Exception:
                    logger.debug("Initial post-seek unpause failed")
                await _ensure_playing(adapter, intent.device)
                return result
            case "seek_back":
                if intent.amount:
                    try:
                        ticks = adapter._parse_seek_ticks(intent.amount)
                    except ValueError:
                        raise ValueError(
                            f'Could not parse seek amount: "{intent.amount}".'
                        )
                    await adapter.seek_relative(-ticks, device=intent.device)
                    result = f"Skipped back {intent.amount}."
                else:
                    result = await adapter.seek_relative(
                        -100_000_000, device=intent.device
                    )
                try:
                    await adapter.unpause(device=intent.device)
                except Exception:
                    logger.debug("Initial post-seek_back unpause failed")
                await _ensure_playing(adapter, intent.device)
                return result
            case "seek_forward":
                if intent.amount:
                    try:
                        ticks = adapter._parse_seek_ticks(intent.amount)
                    except ValueError:
                        raise ValueError(
                            f'Could not parse seek amount: "{intent.amount}".'
                        )
                    await adapter.seek_relative(ticks, device=intent.device)
                    result = f"Skipped forward {intent.amount}."
                else:
                    result = await adapter.seek_relative(
                        100_000_000, device=intent.device
                    )
                try:
                    await adapter.unpause(device=intent.device)
                except Exception:
                    logger.debug("Initial post-seek_forward unpause failed")
                await _ensure_playing(adapter, intent.device)
                return result
            case "smart_play":
                state = await adapter.get_playback_state(device=intent.device)
                if not state:
                    return "Nothing is playing. What would you like to play?"
                if state.get("state") == "Paused":
                    return await adapter.unpause(device=intent.device)
                return "Already playing."
            case "volume_up":
                return await adapter.volume_step(True, device=intent.device)
            case "volume_down":
                return await adapter.volume_step(False, device=intent.device)
            case "set_volume":
                digits = re.sub(r"[^\d]", "", intent.amount or "")
                if not digits:
                    return "What volume level? Say a number from 0 to 100."
                level = int(digits)
                if level > 100:
                    return "Volume goes from 0 to 100."
                return await adapter.set_volume(level, device=intent.device)
            case "mute":
                return await adapter.set_mute(True, device=intent.device)
            case "unmute":
                return await adapter.set_mute(False, device=intent.device)
            case "subtitles_on":
                return await adapter.set_subtitles(
                    True, language=intent.language, device=intent.device
                )
            case "subtitles_off":
                return await adapter.set_subtitles(False, device=intent.device)
            case "audio_language":
                return await adapter.set_audio_language(
                    intent.language or "", device=intent.device
                )
            case "shuffle":
                return await adapter.shuffle(
                    series_name=intent.series,
                    media_type=intent.type,
                    genre=intent.genre,
                    device=intent.device,
                    unwatched=intent.unwatched,
                    decade=intent.decade,
                    top_rated=intent.top_rated,
                )
            case "play_collection":
                kind = intent.type if intent.type in ("collection", "playlist") else None
                return await adapter.play_collection(
                    query=intent.query,
                    kind=kind,
                    shuffled=intent.shuffled,
                    device=intent.device,
                )
            case "mark_watched":
                return await adapter.set_watched(True, device=intent.device)
            case "mark_unwatched":
                return await adapter.set_watched(False, device=intent.device)
            case "time_left":
                state = await adapter.get_playback_state(device=intent.device)
                if not state:
                    return "Nothing is currently playing."
                runtime = state.get("runtime_ticks") or 0
                if not runtime:
                    return "This item doesn't report a runtime, sorry."
                remain_secs = max(
                    0, (runtime - (state.get("position_ticks") or 0))
                ) // TICKS_PER_SECOND
                mins = -(-remain_secs // 60)  # ceil
                if mins >= 60:
                    h, m = divmod(mins, 60)
                    spoken = f"{h} hour{'s' if h != 1 else ''}"
                    if m:
                        spoken += f" {m} minute{'s' if m != 1 else ''}"
                else:
                    spoken = f"{mins} minute{'s' if mins != 1 else ''}"
                name = state.get("series_name") or state.get("item_name", "this")
                return f"About {spoken} left in {name}."
            case "help":
                return HELP_TEXT
            case "search":
                return await adapter.search(
                    query=intent.query,
                    media_type=intent.type,
                )
            case "now_playing":
                return await adapter.get_now_playing()
            case "recently_added":
                items = await adapter.get_recently_added_items(
                    media_type=intent.type,
                    limit=8 if intent.genre else 5,
                    genre=intent.genre,
                )
                if not items:
                    if intent.genre:
                        return f"Nothing found for {intent.genre} in this library."
                    return "No recently added media found."
                type_label = intent.type or "items"
                if intent.genre:
                    type_label = f"{intent.genre} {type_label}"
                raise AmbiguousMatchError(
                    message=f"Here are {len(items)} recently added {type_label}:",
                    candidates=items,
                )
            case "browse_series":
                series_id, series_name = await adapter._resolve_series(
                    intent.series, action="browse_series"
                )
                if intent.season is not None:
                    episodes = await adapter.get_season_episodes(
                        series_id, intent.season
                    )
                    if not episodes:
                        return f"No episodes found for {series_name} season {intent.season}."
                    raise AmbiguousMatchError(
                        message=f"{series_name} — Season {intent.season}:",
                        candidates=episodes,
                    )
                else:
                    seasons = await adapter.get_seasons(series_id)
                    if not seasons:
                        return f"No seasons found for {series_name}."
                    raise AmbiguousMatchError(
                        message=f"{series_name} — {len(seasons)} seasons:",
                        candidates=seasons,
                    )
            case "browse_person":
                person = await adapter.search_person(intent.person)
                if not person:
                    return f'No person found matching "{intent.person}".'
                items = await adapter.get_person_filmography(
                    person["id"], media_type=intent.type, limit=10
                )
                if not items:
                    return f'Found {person["name"]} but no titles in your library.'
                raise AmbiguousMatchError(
                    message=f'{person["name"]} — {len(items)} titles:',
                    candidates=items,
                )
            case "unknown":
                raise ValueError(intent.message or "I didn't understand that command.")
            case _:
                return f"Unknown action: {intent.action}"

    except SessionAmbiguousError:
        raise  # Let the voice endpoint handle this
    except AmbiguousMatchError:
        raise  # Let the voice endpoint handle this
    except ValueError:
        # Honest failures ("no session matching X", "no French subtitles") —
        # re-raise so the voice endpoint marks them success=False instead of
        # logging and displaying them as successes
        raise
    except Exception:
        logger.exception("Error executing intent %s", intent.action)
        # Raise (not return) so the voice endpoint marks it failed — a media
        # server 500 or timeout shouldn't be logged/spoken as a success
        raise ValueError("Something went wrong executing that command.")
