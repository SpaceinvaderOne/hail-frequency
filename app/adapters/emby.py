"""Emby media server adapter."""

import difflib
import logging
import random
import re
import time
import urllib.parse

import httpx

from .base import (
    TICKS_PER_SECOND,
    AmbiguousMatchError,
    MediaServerAdapter,
    SessionAmbiguousError,
    media_auth_headers,
    parse_seek_ticks,
)

logger = logging.getLogger("hailfrequency.emby")


def _normalize(name: str) -> str:
    """Strip punctuation and collapse whitespace for fuzzy name matching."""
    return re.sub(r"\s+", " ", re.sub(r"[:\-.'\"!?,]", " ", name)).strip().lower()


# Spoken device names arrive with articles — "on the shield", "on my bedroom
# tv" — from the fast-path templates and LLM output alike. Aliases and
# session names never carry them, so match on the bare name.
_DEVICE_ARTICLE_RE = re.compile(r"^(?:the|my|our)\s+")


def _device_patterns(device: str, aliases: list[dict]) -> list[str]:
    """Lowercase substrings to match a spoken device against session names.
    An alias wins (with or without the article, in case one was saved that
    way); otherwise the bare spoken name itself."""
    spoken = device.strip().lower()
    bare = _DEVICE_ARTICLE_RE.sub("", spoken)
    alias_patterns = [
        a["pattern"].lower()
        for a in aliases
        if a["alias"].lower() in (spoken, bare)
    ]
    return alias_patterns or [bare]


def _score_match(query: str, item_name: str) -> int:
    """Score how well item_name matches query. Higher = better."""
    if item_name.lower() == query.lower():
        return 100
    if _normalize(item_name) == _normalize(query):
        return 90
    if query.lower() in item_name.lower():
        return 80
    query_words = set(_normalize(query).split())
    name_words = set(_normalize(item_name).split())
    if query_words and query_words <= name_words:
        return 60
    if query_words:
        matched = len(query_words & name_words)
        if matched / len(query_words) > 0.5:
            return 30
    return 0


def _pick_best(query: str, items: list[dict], threshold: int = 20) -> dict | list[dict]:
    """Score items and auto-select if the top result is clearly best.

    Returns a single item dict if auto-selected, or a list sorted by score.
    """
    if not items:
        return []
    if len(items) == 1:
        return items[0]

    scored = [(item, _score_match(query, item.get("Name", ""))) for item in items]
    scored.sort(key=lambda x: x[1], reverse=True)

    best_score = scored[0][1]
    second_score = scored[1][1]

    if best_score - second_score >= threshold:
        return scored[0][0]

    return [item for item, _score in scored]


# ── Fuzzy title matching ────────────────────────────────────────────
#
# The server's SearchTerm is a prefix/substring match, so a spoken title
# that was transcribed with different spelling ("cary" for "Carrie") finds
# nothing at all. When that happens the adapter compares the spoken name
# against the library's own title list, by spelling and by sound. Nothing
# about any particular library is encoded here: the thresholds are fixed,
# the titles come from the server at run time.

# Similarity needed on spelling alone (0..1).
FUZZY_RATIO = 0.80
# Similarity needed when the phonetic keys already agree.
FUZZY_SOUND_RATIO = 0.50
# Lead over the runner-up that lets the top candidate be auto-picked.
FUZZY_AUTO_GAP = 0.15
# Most candidates offered as disambiguation cards.
FUZZY_MAX_CARDS = 5
# How long a fetched title list is reused before being refreshed.
TITLE_CACHE_TTL = 300.0

_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+")


def _title_key(name: str) -> str:
    """Normalised comparison form: punctuation stripped, one leading article
    dropped, so "the forbidden planet" and "Forbidden Planet" compare equal."""
    return _LEADING_ARTICLE_RE.sub("", _normalize(name))


def _sound_word(word: str) -> str:
    """A crude phonetic key for one word: common consonant spellings folded
    together, interior vowels dropped, doubled letters collapsed. It is a
    gate that lets near-homophones through, not a spelling corrector."""
    w = word.lower()
    w = w.replace("ph", "f").replace("ck", "k").replace("qu", "k")
    w = re.sub(r"c(?=[eiy])", "s", w)
    w = w.replace("c", "k").replace("x", "ks").replace("z", "s")
    w = re.sub(r"(?:ie|ey|y)$", "i", w)
    w = w.replace("y", "i")
    if w:
        w = w[0] + re.sub(r"[aeiou]", "", w[1:])
    return re.sub(r"(.)\1+", r"\1", w)


def _sound_key(name: str) -> str:
    return " ".join(_sound_word(w) for w in _title_key(name).split())


def _fuzzy_candidates(
    query: str, titles: list[dict]
) -> list[tuple[dict, float, bool]]:
    """Titles that plausibly are the spoken query, best first.

    Each entry is (item, ratio, same_sound). A title qualifies on spelling
    alone (ratio >= FUZZY_RATIO) or on sound plus a weaker spelling match
    (same phonetic key and ratio >= FUZZY_SOUND_RATIO). Sorted so phonetic
    matches rank above spelling-only ones, then by ratio.
    """
    q_key = _title_key(query)
    if len(q_key) < 2:
        return []
    q_sound = _sound_key(query)
    out: list[tuple[dict, float, bool]] = []
    for item in titles:
        name = item.get("Name") or ""
        if not name:
            continue
        ratio = difflib.SequenceMatcher(None, q_key, _title_key(name)).ratio()
        same_sound = _sound_key(name) == q_sound
        if ratio >= FUZZY_RATIO or (same_sound and ratio >= FUZZY_SOUND_RATIO):
            out.append((item, ratio, same_sound))
    out.sort(key=lambda c: (c[2], c[1]), reverse=True)
    return out


def _fuzzy_auto_pick(cands: list[tuple[dict, float, bool]]) -> dict | None:
    """The single item to play without asking, or None when the user should
    choose: one candidate, the only phonetic match, or a clear ratio lead."""
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0][0]
    (top, top_ratio, top_sound), (_, next_ratio, next_sound) = cands[0], cands[1]
    if top_sound and not next_sound:
        return top
    if top_ratio - next_ratio >= FUZZY_AUTO_GAP:
        return top
    return None


# Fetched title lists, keyed by (server url, user id, item types). Adapters
# are built per request, so the cache has to outlive them; one uvicorn
# worker means one process-wide cache.
_TITLE_CACHE: dict[tuple[str, str, str], tuple[float, list[dict]]] = {}


# Friendly type names → Emby IncludeItemTypes values
TYPE_MAP = {
    "movie": "Movie",
    "movies": "Movie",
    "series": "Series",
    "show": "Series",
    "shows": "Series",
    "episode": "Episode",
    "episodes": "Episode",
    "music": "Audio",
    "song": "Audio",
    "songs": "Audio",
    "audiobook": "AudioBook",
    "audiobooks": "AudioBook",
    "person": "Person",
    "people": "Person",
}


class EmbyAdapter(MediaServerAdapter):
    # URL path prefix for API endpoints — Jellyfin (a fork) uses none
    API_PREFIX = "/emby"
    # Product name in user-facing messages
    SERVER_LABEL = "Emby"

    def __init__(
        self,
        url: str,
        api_key: str,
        user_id: str,
        device_aliases: list[dict],
        timeout: float = 15.0,
    ):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.user_id = user_id
        self.device_aliases = device_aliases  # [{"alias": "shield", "pattern": "SHIELD"}, ...]
        # API key goes in a header, not the URL, so it never lands in
        # server/proxy logs or exception messages
        self.client = httpx.AsyncClient(
            timeout=timeout, headers=media_auth_headers(api_key)
        )
        # Query param fragment for UserId — empty string if no user_id configured
        self._user_q = f"&UserId={user_id}" if user_id else ""

    # ── HTTP helpers ────────────────────────────────────────────────

    async def _api_get(self, endpoint: str) -> dict | list:
        logger.debug("%s GET %s", type(self).__name__, endpoint.split("?")[0])
        resp = await self.client.get(f"{self.url}{self.API_PREFIX}{endpoint}")
        resp.raise_for_status()
        return resp.json()

    async def _api_post(self, endpoint: str, data: dict | None = None) -> None:
        logger.debug("%s POST %s", type(self).__name__, endpoint.split("?")[0])
        resp = await self.client.post(
            f"{self.url}{self.API_PREFIX}{endpoint}", json=data or {}
        )
        resp.raise_for_status()

    async def _api_delete(self, endpoint: str) -> None:
        logger.debug("%s DELETE %s", type(self).__name__, endpoint.split("?")[0])
        resp = await self.client.delete(f"{self.url}{self.API_PREFIX}{endpoint}")
        resp.raise_for_status()

    def image_url(self, item_id: str) -> str:
        """Primary-image URL for an item (fetch with this adapter's client)."""
        return f"{self.url}{self.API_PREFIX}/Items/{item_id}/Images/Primary"

    # ── Dialect seams (Jellyfin overrides these) ────────────────────

    async def _send_play(
        self, session_id: str, item_ids: list[str],
        start_ticks: int | None = None,
    ) -> None:
        """Send a play command. Emby accepts the JSON-body form."""
        body = {"ItemIds": item_ids, "PlayCommand": "PlayNow"}
        if start_ticks:
            body["StartPositionTicks"] = start_ticks
        await self._api_post(f"/Sessions/{session_id}/Playing", body)

    async def _send_playstate(
        self, session_id: str, command: str, ticks: int | None = None
    ) -> None:
        """Send a playstate command (Pause/Unpause/Stop/Seek/...)."""
        data = {"SeekPositionTicks": ticks} if ticks is not None else None
        await self._api_post(f"/Sessions/{session_id}/Playing/{command}", data)

    async def _search_items_raw(
        self, query: str, type_param: str = "", limit: int = 5
    ) -> list[dict]:
        """Raw item search. Emby's SearchTerm matches loosely across words."""
        encoded = urllib.parse.quote(query)
        # RecursiveItemCount is only sent when asked for; it lets identical
        # series cards (one show in two library folders) be told apart.
        data = await self._api_get(
            f"/Items?SearchTerm={encoded}&Recursive=true&Limit={limit}"
            f"&Fields=RecursiveItemCount{self._user_q}{type_param}"
        )
        return data.get("Items", [])

    async def _library_titles(self, types: str) -> list[dict]:
        """Every item of the given types as {Id, Name, Type, ProductionYear},
        cached per server/user for TITLE_CACHE_TTL. Returns [] when the
        server cannot be read: the caller then reports its ordinary miss."""
        key = (self.url, self.user_id or "", types)
        cached = _TITLE_CACHE.get(key)
        now = time.time()
        if cached and now - cached[0] < TITLE_CACHE_TTL:
            return cached[1]
        try:
            data = await self._api_get(
                f"/Items?IncludeItemTypes={types}&Recursive=true"
                f"&Fields=ProductionYear&EnableImages=false"
                f"&EnableTotalRecordCount=false{self._user_q}"
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("Title list unavailable from %s: %s", self.url, exc)
            return []
        titles = [
            {k: item.get(k) for k in ("Id", "Name", "Type", "ProductionYear")}
            for item in data.get("Items", [])
            if item.get("Id") and item.get("Name")
        ]
        _TITLE_CACHE[key] = (now, titles)
        logger.info("Title list fetched: %d %s items", len(titles), types)
        return titles

    async def _fuzzy_lookup(
        self, query: str, types: str, action: str, miss: str
    ) -> dict:
        """Resolve a query the server search found nothing for, against the
        library title list. Returns the item to use, raises
        AmbiguousMatchError when several titles are plausible, or ValueError
        with the caller's own miss message when none is."""
        cands = _fuzzy_candidates(query, await self._library_titles(types))
        if not cands:
            raise ValueError(miss)
        pick = _fuzzy_auto_pick(cands)
        if pick is not None:
            logger.info(
                'Fuzzy title match: "%s" -> "%s" (ratio=%.2f, sound=%s)',
                query, pick["Name"], cands[0][1], cands[0][2],
            )
            return pick
        logger.info('Fuzzy title candidates for "%s": %d', query, len(cands))
        raise AmbiguousMatchError(
            message="Did you mean one of these?",
            candidates=self._build_candidates(
                [c[0] for c in cands[:FUZZY_MAX_CARDS]], action
            ),
        )

    # ── Type mapping ────────────────────────────────────────────────

    def _emby_type(self, media_type: str | None) -> str | None:
        if not media_type:
            return None
        return TYPE_MAP.get(media_type.lower(), media_type)

    # ── Session scoping ─────────────────────────────────────────────

    async def _get_user_sessions(self) -> list[dict]:
        """Get sessions for the configured user, or all sessions if no user_id."""
        sessions = await self._api_get("/Sessions")
        if not self.user_id:
            return sessions
        return [s for s in sessions if s.get("UserId") == self.user_id]

    # ── Device matching ─────────────────────────────────────────────

    async def _pick_session(self, device: str | None = None) -> str:
        """Resolve a device alias to a session ID."""
        session = await self._pick_session_full(device)
        return session["Id"]

    async def _pick_session_full(self, device: str | None = None) -> dict:
        """Resolve a device alias to a full session dict (single /Sessions fetch).

        Raises ValueError if no sessions found.
        Raises SessionAmbiguousError if multiple sessions match.
        """
        all_sessions = await self._get_user_sessions()
        # Powered-off devices leave ghost sessions behind that report
        # SupportsRemoteControl=false — commands to them "succeed" into the
        # void. Web players also flip to false when their websocket drops
        # (backgrounded tab) even while playing. Only match sessions that
        # can actually be controlled, but explain the playing-but-deaf case.
        sessions = [s for s in all_sessions if s.get("SupportsRemoteControl")]
        logger.debug("_pick_session: device=%s, session_count=%d", device, len(sessions))
        if not sessions:
            playing = [s for s in all_sessions if s.get("NowPlayingItem")]
            if playing:
                names = ", ".join(s.get("DeviceName", "?") for s in playing)
                raise ValueError(
                    f"{names} is playing but not accepting remote commands "
                    f"right now. Refresh or reopen the app on that device, "
                    f"then try again."
                )
            raise ValueError(
                f"No active {self.SERVER_LABEL} sessions. "
                f"Open {self.SERVER_LABEL} on a device first."
            )

        if device:
            patterns = _device_patterns(device, self.device_aliases)

            # Match against DeviceName and Client fields (case-insensitive substring)
            matched = []
            for s in sessions:
                dn = (s.get("DeviceName") or "").lower()
                cl = (s.get("Client") or "").lower()
                for p in patterns:
                    if p in dn or p in cl:
                        matched.append(s)
                        break

            if not matched:
                # Does the name match a session we filtered for being
                # uncontrollable? Say so — "no session" would be misleading.
                for s in all_sessions:
                    dn = (s.get("DeviceName") or "").lower()
                    cl = (s.get("Client") or "").lower()
                    if any(p in dn or p in cl for p in patterns):
                        raise ValueError(
                            f'{s.get("DeviceName", device)} isn\'t accepting '
                            f"remote commands right now. Refresh or reopen "
                            f"the app on that device, then try again."
                        )
                names = [s.get("DeviceName", "?") for s in sessions]
                raise ValueError(
                    f'No session matching "{device}". Active devices: {", ".join(names)}'
                )
            if len(matched) == 1:
                logger.debug("_pick_session: matched session=%s device=%s", matched[0]["Id"], matched[0].get("DeviceName"))
                return matched[0]

            # Multiple matches — check if they're the same device
            # (e.g. multiple browser tabs, stale sessions)
            unique_devices = {
                (s.get("DeviceName") or "").lower() for s in matched
            }
            if len(unique_devices) == 1:
                # Same device, multiple sessions — prefer one with active playback
                for s in matched:
                    if s.get("NowPlayingItem"):
                        return s
                return matched[0]

            sessions = matched  # genuinely different devices

        if len(sessions) == 1:
            return sessions[0]

        # Multiple sessions — deduplicate by DeviceName before disambiguation
        seen = {}
        for s in sessions:
            dn = (s.get("DeviceName") or "?").lower()
            if dn not in seen or s.get("NowPlayingItem"):
                seen[dn] = s
        if len(seen) == 1:
            return next(iter(seen.values()))

        options = [s.get("DeviceName", "?") for s in seen.values()]
        raise SessionAmbiguousError(
            message="Multiple active sessions. Which device?",
            options=options,
        )

    # ── Series resolution ───────────────────────────────────────────

    def _build_candidates(self, items: list[dict], action: str) -> list[dict]:
        """Build structured disambiguation candidates from Emby items.

        When two candidates would read identically (the same show scanned
        from two library folders), the episode count is added so the user
        can tell them apart. Cards that are already distinct are left alone.
        """
        candidates = []
        for item in items[:5]:
            name = item.get("Name", "?")
            year = item.get("ProductionYear", "")
            item_type = item.get("Type", "")
            subtitle = f"{item_type} ({year})" if year else item_type
            candidates.append({
                "id": item["Id"],
                "title": name,
                "subtitle": subtitle,
                "type": item_type,
                "action": action,
                "server_id": "",
                "_count": item.get("RecursiveItemCount"),
            })

        seen: dict[tuple[str, str], int] = {}
        for c in candidates:
            key = (c["title"], c["subtitle"])
            seen[key] = seen.get(key, 0) + 1
        for c in candidates:
            count = c.pop("_count")
            if seen[(c["title"], c["subtitle"])] > 1 and count is not None:
                noun = "episode" if c["type"] == "Series" else "item"
                c["subtitle"] += f", {count} {noun}{'s' if count != 1 else ''}"
        return candidates

    async def _resolve_series(
        self, query: str, action: str = "play_next"
    ) -> tuple[str, str]:
        """Resolve a series name to (series_id, series_name).

        Raises ValueError if not found.
        Raises AmbiguousMatchError if multiple close matches.
        The action param controls what happens when a user taps a disambiguation card.
        """
        items = await self._search_items_raw(
            query, "&IncludeItemTypes=Series", limit=5
        )
        if not items:
            item = await self._fuzzy_lookup(
                query, "Series", action, f'No series found matching "{query}".'
            )
            return item["Id"], item["Name"]

        result = _pick_best(query, items)

        if isinstance(result, dict):
            return result["Id"], result["Name"]

        # Multiple close matches — structured disambiguation
        candidates = self._build_candidates(result, action)
        raise AmbiguousMatchError(
            message="Multiple series found. Which one?",
            candidates=candidates,
        )

    # ── Find next episode ───────────────────────────────────────────

    async def _find_next_episode(self, series_id: str) -> tuple[str, str]:
        """Find the next episode to play.

        Returns (episode_id, human_description).
        Raises ValueError if all caught up or no episodes.

        Priority:
        1. In-progress episode (PlaybackPositionTicks > 0, not Played)
           — take the latest by season/episode order
        2. First unplayed episode
        3. All caught up
        """
        data = await self._api_get(
            f"/Shows/{series_id}/Episodes?Fields=UserData&Limit=1000"
            f"{self._user_q}"
        )
        items = data.get("Items", [])

        if not items:
            raise ValueError("No episodes found for this series.")

        # Sort by season then episode number. NOTE: servers return null (not
        # missing) index numbers on specials/extras — `or 0`, never get(k, 0).
        items.sort(
            key=lambda e: (
                e.get("ParentIndexNumber") or 0,
                e.get("IndexNumber") or 0,
            )
        )

        # Priority 1: in-progress (take the latest one by season/ep order)
        in_progress = None
        for ep in items:
            ud = ep.get("UserData", {})
            if ud.get("PlaybackPositionTicks", 0) > 0 and not ud.get("Played", False):
                in_progress = ep

        if in_progress:
            s = in_progress.get("ParentIndexNumber") or 0
            e = in_progress.get("IndexNumber") or 0
            name = in_progress.get("Name", "?")
            series = in_progress.get("SeriesName", "?")
            pct = in_progress.get("UserData", {}).get("PlayedPercentage") or 0
            desc = f'Resuming {series} S{s:02d}E{e:02d} "{name}" ({pct:.0f}% watched)'
            return in_progress["Id"], desc

        # Priority 2: first unplayed
        for ep in items:
            ud = ep.get("UserData", {})
            if not ud.get("Played", False):
                s = ep.get("ParentIndexNumber") or 0
                e_num = ep.get("IndexNumber") or 0
                name = ep.get("Name", "?")
                series = ep.get("SeriesName", "?")
                desc = f'Next: {series} S{s:02d}E{e_num:02d} "{name}"'
                return ep["Id"], desc

        raise ValueError("All caught up — every episode is watched!")

    async def _find_episode_after(
        self, series_id: str, current: dict
    ) -> tuple[str, str]:
        """Find the episode that follows `current` (a NowPlayingItem) in
        season/episode order, regardless of watched state.

        This is "skip", not "continue watching": the playing episode is
        rarely marked Played yet, so the unwatched-first rule used by
        _find_next_episode would hand back the same episode.

        Returns (episode_id, human_description).
        Raises ValueError when the series has no episodes or the current
        one is the last.
        """
        data = await self._api_get(
            f"/Shows/{series_id}/Episodes?Fields=UserData&Limit=1000"
            f"{self._user_q}"
        )
        items = data.get("Items", [])
        if not items:
            raise ValueError("No episodes found for this series.")

        items.sort(
            key=lambda e: (
                e.get("ParentIndexNumber") or 0,
                e.get("IndexNumber") or 0,
            )
        )

        # Locate the playing episode by Id; fall back to its season and
        # episode numbers (the session's item can be a different Id from
        # the library listing when the server merges versions).
        idx = next(
            (i for i, ep in enumerate(items) if ep.get("Id") == current.get("Id")),
            None,
        )
        if idx is None:
            key = (
                current.get("ParentIndexNumber") or 0,
                current.get("IndexNumber") or 0,
            )
            idx = next(
                (
                    i for i, ep in enumerate(items)
                    if (ep.get("ParentIndexNumber") or 0, ep.get("IndexNumber") or 0) == key
                ),
                None,
            )
        if idx is None:
            raise ValueError("Couldn't find the playing episode in this series.")
        if idx + 1 >= len(items):
            raise ValueError("That was the last episode.")

        ep = items[idx + 1]
        s = ep.get("ParentIndexNumber") or 0
        e_num = ep.get("IndexNumber") or 0
        name = ep.get("Name", "?")
        series = ep.get("SeriesName") or current.get("SeriesName") or "?"
        return ep["Id"], f'Next: {series} S{s:02d}E{e_num:02d} "{name}"'

    # ── Seek parsing ────────────────────────────────────────────────

    def _parse_seek_ticks(self, position: str) -> int:
        """Parse human-friendly seek position to ticks (shared impl in base)."""
        return parse_seek_ticks(position)

    # ── Format helpers ──────────────────────────────────────────────

    def _format_item(self, item: dict) -> str:
        """Format a media item for display."""
        name = item.get("Name", "?")
        series = item.get("SeriesName")
        year = item.get("ProductionYear", "")
        if series:
            s = item.get("ParentIndexNumber") or 0
            e = item.get("IndexNumber") or 0
            return f'{series} S{s:02d}E{e:02d} "{name}"'
        if year:
            return f"{name} ({year})"
        return name

    # ── Public action methods ───────────────────────────────────────

    async def play_search(
        self, query: str, media_type: str | None = None, device: str | None = None
    ) -> str:
        type_param = ""
        if media_type:
            emby_t = self._emby_type(media_type)
            if emby_t:
                type_param = f"&IncludeItemTypes={emby_t}"

        items = await self._search_items_raw(query, type_param, limit=5)
        logger.debug("play_search: query='%s', results=%d", query, len(items))
        if items:
            result = _pick_best(query, items)
        else:
            # A miss raises (never returns) so the voice endpoint marks it
            # failed — otherwise TTS speaks "Now playing No results found".
            # Only Movie/Series titles are worth a fuzzy pass; other types
            # (people, music) keep the plain miss.
            fuzzy_types = {"Movie": "Movie", "Series": "Series", "": "Movie,Series"}
            emby_t = type_param.removeprefix("&IncludeItemTypes=")
            if emby_t not in fuzzy_types:
                raise ValueError(f'No results found for "{query}".')
            result = await self._fuzzy_lookup(
                query, fuzzy_types[emby_t], "play_search",
                f'No results found for "{query}".',
            )

        if isinstance(result, list):
            candidates = self._build_candidates(result, "play_search")
            raise AmbiguousMatchError(
                message="Multiple matches found. Which one?",
                candidates=candidates,
            )

        item = result
        item_id = item["Id"]
        display = self._format_item(item)

        session_id = await self._pick_session(device)
        await self._send_play(session_id, [item_id])

        return f"Playing {display}."

    async def play_next(self, series_name: str, device: str | None = None) -> str:
        series_id, _ = await self._resolve_series(series_name)
        episode_id, description = await self._find_next_episode(series_id)
        session_id = await self._pick_session(device)
        await self._send_play(session_id, [episode_id])
        return description

    # play_episode: play a specific numbered episode of a series
    async def play_episode(
        self,
        series_name: str,
        episode: int,
        season: int | None = None,
        device: str | None = None,
    ) -> str:
        """Play episode N of a series.

        Season rules:
        - season given → play that season's episode N directly
        - no season, series has exactly one real season → use it
        - no season, multiple seasons → AmbiguousMatchError listing the
          seasons; each card carries the episode number so one tap plays it
        """
        series_id, series_display = await self._resolve_series(
            series_name, action="browse_series"
        )

        if season is None:
            seasons = await self._get_seasons_raw(series_id)
            # Count ALL season entries except explicit "Specials" (IndexNumber
            # 0). Seasons with no IndexNumber at all (unparsed folder names)
            # still count — when metadata is messy, ask, don't guess.
            entries = [s for s in seasons if s.get("IndexNumber") != 0]
            if not entries:
                season = 1  # nothing listed at all — try numeric season 1
            elif len(entries) == 1:
                # Single season — play by its ID (works even unnumbered)
                return await self.play_episode_in_season(
                    series_id, entries[0]["Id"], episode, device,
                    where=series_display,
                )
            else:
                raise AmbiguousMatchError(
                    message=(
                        f"{series_display} has {len(entries)} seasons — "
                        f"which one for episode {episode}?"
                    ),
                    candidates=self._build_season_cards(
                        entries, series_id, "play_episode",
                        f"Play episode {episode}", episode=episode,
                    ),
                )

        data = await self._api_get(
            f"/Shows/{series_id}/Episodes?Fields=UserData"
            f"&Season={season}{self._user_q}"
        )
        items = data.get("Items", [])
        if not items:
            # Numbered query found nothing (unnumbered season folders) —
            # match the requested number against trailing digits in names
            seasons = await self._get_seasons_raw(series_id)
            entries = [s for s in seasons if s.get("IndexNumber") != 0]
            matched = self._match_seasons_by_number(entries, season)
            if len(matched) == 1:
                return await self.play_episode_in_season(
                    series_id, matched[0]["Id"], episode, device,
                    where=f"{series_display} — {matched[0].get('Name', '?')}",
                )
            if matched or entries:
                ask = matched or entries
                raise AmbiguousMatchError(
                    message=(
                        f"Couldn't find {series_display} season {season} by "
                        f"number — which one for episode {episode}?"
                    ),
                    candidates=self._build_season_cards(
                        ask, series_id, "play_episode",
                        f"Play episode {episode}", episode=episode,
                    ),
                )
        return await self._play_episode_from_items(
            items, episode, device,
            f"{series_display} season {season}",
        )

    async def play_episode_in_season(
        self,
        series_id: str,
        season_id: str,
        episode: int,
        device: str | None = None,
        where: str = "that season",
    ) -> str:
        """Play episode N of a specific season (by season ID)."""
        data = await self._api_get(
            f"/Shows/{series_id}/Episodes?Fields=UserData"
            f"&SeasonId={season_id}{self._user_q}"
        )
        return await self._play_episode_from_items(
            data.get("Items", []), episode, device, where
        )

    # play_season: play a whole season from episode 1, queueing the rest
    async def play_season(
        self,
        series_name: str,
        season: int | None = None,
        device: str | None = None,
    ) -> str:
        """Play a season from episode 1 with the rest of it queued.

        Same season rules as play_episode: explicit number → direct; one
        season → use it; multiple/unnumbered → ask via season cards.
        """
        series_id, series_display = await self._resolve_series(
            series_name, action="browse_series"
        )

        if season is not None:
            data = await self._api_get(
                f"/Shows/{series_id}/Episodes?Fields=UserData"
                f"&Season={season}{self._user_q}"
            )
            items = data.get("Items", [])
            if items:
                return await self._play_season_from_items(
                    items, device, f"{series_display} season {season}"
                )

        seasons = await self._get_seasons_raw(series_id)
        entries = [s for s in seasons if s.get("IndexNumber") != 0]
        if not entries:
            raise ValueError(f"No seasons found for {series_display}.")

        if season is not None:
            # Numbered query found nothing (unnumbered season folders) —
            # match the requested number against trailing digits in names
            matched = self._match_seasons_by_number(entries, season)
            if len(matched) == 1:
                return await self.play_season_by_id(
                    series_id, matched[0]["Id"], device,
                    where=f"{series_display} — {matched[0].get('Name', '?')}",
                )
            if matched:
                # Duplicates (e.g. "Season 1" AND "(2022)1") — ask which
                raise AmbiguousMatchError(
                    message=(
                        f"{series_display} has {len(matched)} entries for "
                        f"season {season} — which one?"
                    ),
                    candidates=self._build_season_cards(
                        matched, series_id, "play_season", "Play this season"
                    ),
                )
            # No match by number either — fall through to the full ask

        if len(entries) == 1:
            return await self.play_season_by_id(
                series_id, entries[0]["Id"], device, where=series_display
            )
        raise AmbiguousMatchError(
            message=f"{series_display} — which season?",
            candidates=self._build_season_cards(
                entries, series_id, "play_season", "Play this season"
            ),
        )

    async def play_season_by_id(
        self,
        series_id: str,
        season_id: str,
        device: str | None = None,
        where: str = "that season",
    ) -> str:
        """Play a season (by ID) from episode 1, queueing the rest."""
        data = await self._api_get(
            f"/Shows/{series_id}/Episodes?Fields=UserData"
            f"&SeasonId={season_id}{self._user_q}"
        )
        return await self._play_season_from_items(
            data.get("Items", []), device, where
        )

    async def _play_season_from_items(
        self, items: list[dict], device: str | None, where: str
    ) -> str:
        """Play a season's episodes in order from the first."""
        if not items:
            raise ValueError(f"No episodes found in {where}.")
        items.sort(
            key=lambda e: (
                e.get("ParentIndexNumber") or 0,
                e.get("IndexNumber") or 0,
            )
        )
        session_id = await self._pick_session(device)
        await self._send_play(session_id, [e["Id"] for e in items])
        first = self._format_item(items[0])
        return f"Playing {first} — {len(items)} episodes queued."

    async def _play_episode_from_items(
        self,
        items: list[dict],
        episode: int,
        device: str | None,
        where: str,
    ) -> str:
        """Find episode N in a season's episode list and play it."""
        target = next(
            (e for e in items if e.get("IndexNumber") == episode), None
        )
        if not target:
            raise ValueError(f"Couldn't find episode {episode} of {where}.")
        session_id = await self._pick_session(device)
        await self._send_play(session_id, [target["Id"]])
        return f"Playing {self._format_item(target)}."

    async def pause(self, device: str | None = None) -> str:
        session_id = await self._pick_session(device)
        await self._send_playstate(session_id, "Pause")
        return "Paused."

    async def unpause(self, device: str | None = None) -> str:
        session_id = await self._pick_session(device)
        await self._send_playstate(session_id, "Unpause")
        return "Resumed."

    async def stop(self, device: str | None = None) -> str:
        session_id = await self._pick_session(device)
        await self._send_playstate(session_id, "Stop")
        return "Stopped."

    async def seek(self, position: str, device: str | None = None) -> str:
        session_id = await self._pick_session(device)
        ticks = self._parse_seek_ticks(position)
        await self._send_playstate(session_id, "Seek", ticks)
        if ticks == 0:
            return "Starting from the beginning."
        return f"Seeked to {position}."

    async def seek_ticks(self, position_ticks: int, device: str | None = None) -> str:
        """Seek to an absolute tick position (used by the transport API)."""
        session_id = await self._pick_session(device)
        await self._send_playstate(session_id, "Seek", position_ticks)
        return "Seeked."

    # seek_relative: seek forward/back relative to current position
    async def seek_relative(self, offset_ticks: int, device: str | None = None) -> str:
        """Seek relative to current position. Positive = forward, negative = back."""
        session = await self._pick_session_full(device)
        session_id = session["Id"]

        if not session.get("NowPlayingItem"):
            # Raise so callers that ignore the return value (seek_back/forward
            # with an amount) don't report a phantom "Skipped forward 5m"
            raise ValueError("Nothing is currently playing.")

        play_state = session.get("PlayState", {})
        current_ticks = play_state.get("PositionTicks", 0)
        runtime_ticks = session["NowPlayingItem"].get("RunTimeTicks", 0)

        logger.debug("seek_relative: current=%d, offset=%d, new=%d", current_ticks, offset_ticks, current_ticks + offset_ticks)
        new_ticks = current_ticks + offset_ticks
        new_ticks = max(0, new_ticks)
        if runtime_ticks:
            new_ticks = min(new_ticks, runtime_ticks)

        await self._send_playstate(session_id, "Seek", new_ticks)

        seconds = abs(offset_ticks) // TICKS_PER_SECOND
        direction = "forward" if offset_ticks > 0 else "back"
        return f"Seeked {direction} {seconds} seconds."

    async def next_episode(self, device: str | None = None) -> str:
        """Play next episode of whatever is currently playing."""
        current = await self._pick_session_full(device)
        session_id = current["Id"]

        if not current.get("NowPlayingItem"):
            return "Nothing is currently playing."

        playing = current["NowPlayingItem"]
        series_id = playing.get("SeriesId")
        if not series_id:
            return "Current item is not a series episode."

        episode_id, description = await self._find_episode_after(series_id, playing)
        await self._send_play(session_id, [episode_id])
        return description

    # ── GeneralCommands: volume, mute, subtitles ─────────────────────

    async def _send_command(
        self, session_id: str, name: str, args: dict | None = None
    ) -> None:
        """Send a GeneralCommand to a session. Argument values must be
        strings — Emby rejects bare ints."""
        await self._api_post(
            f"/Sessions/{session_id}/Command",
            {"Name": name, "Arguments": args or {}},
        )

    @staticmethod
    def _supports_command(session: dict, name: str) -> bool:
        """True unless the session advertises a command list that lacks it —
        commands sent anyway would 'succeed' into the void (same failure
        mode as ghost sessions)."""
        supported = session.get("SupportedCommands")
        return not supported or name in supported

    @staticmethod
    def _device_label(session: dict) -> str:
        return session.get("DeviceName") or "That device"

    async def set_volume(self, level: int, device: str | None = None) -> str:
        session = await self._pick_session_full(device)
        if not self._supports_command(session, "SetVolume"):
            raise ValueError(
                f"{self._device_label(session)} doesn't support volume control."
            )
        level = max(0, min(100, level))
        await self._send_command(session["Id"], "SetVolume", {"Volume": str(level)})
        return f"Volume set to {level} percent."

    async def volume_step(self, up: bool, device: str | None = None) -> str:
        """Volume up/down. Prefer a 10-point SetVolume from the reported
        level; fall back to the client's own VolumeUp/VolumeDown step."""
        session = await self._pick_session_full(device)
        current = session.get("PlayState", {}).get("VolumeLevel")
        if current is not None and self._supports_command(session, "SetVolume"):
            level = max(0, min(100, current + (10 if up else -10)))
            await self._send_command(
                session["Id"], "SetVolume", {"Volume": str(level)}
            )
            return f"Volume {'up' if up else 'down'} — now {level} percent."
        name = "VolumeUp" if up else "VolumeDown"
        if not self._supports_command(session, name):
            raise ValueError(
                f"{self._device_label(session)} doesn't support volume control."
            )
        await self._send_command(session["Id"], name)
        return "Volume up." if up else "Volume down."

    async def set_mute(self, muted: bool, device: str | None = None) -> str:
        session = await self._pick_session_full(device)
        name = "Mute" if muted else "Unmute"
        if not self._supports_command(session, name):
            raise ValueError(
                f"{self._device_label(session)} doesn't support muting."
            )
        await self._send_command(session["Id"], name)
        return "Muted." if muted else "Unmuted."

    # Spoken language names → ISO 639 codes as they appear in MediaStreams.
    # Keep in sync with _LANG_WORDS in services/intent.py.
    _SUBTITLE_LANGS = {
        "english": {"eng", "en"},
        "french": {"fre", "fra", "fr"},
        "german": {"ger", "deu", "de"},
        "spanish": {"spa", "es"},
        "italian": {"ita", "it"},
        "dutch": {"dut", "nld", "nl"},
        "portuguese": {"por", "pt"},
        "japanese": {"jpn", "ja"},
        "chinese": {"chi", "zho", "zh"},
        "korean": {"kor", "ko"},
        "russian": {"rus", "ru"},
        "arabic": {"ara", "ar"},
        "hindi": {"hin", "hi"},
        "swedish": {"swe", "sv"},
        "norwegian": {"nor", "no"},
        "danish": {"dan", "da"},
        "finnish": {"fin", "fi"},
        "polish": {"pol", "pl"},
        "turkish": {"tur", "tr"},
        "greek": {"gre", "ell", "el"},
        "hebrew": {"heb", "he"},
        "czech": {"cze", "ces", "cs"},
        "hungarian": {"hun", "hu"},
        "romanian": {"rum", "ron", "ro"},
        "thai": {"tha", "th"},
        "vietnamese": {"vie", "vi"},
        "indonesian": {"ind", "id"},
    }

    async def set_subtitles(
        self, enabled: bool, language: str | None = None, device: str | None = None
    ) -> str:
        session = await self._pick_session_full(device)
        np = session.get("NowPlayingItem")
        if not np:
            return "Nothing is currently playing."
        if not enabled:
            await self._send_command(
                session["Id"], "SetSubtitleStreamIndex", {"Index": "-1"}
            )
            return "Subtitles off."

        streams = np.get("MediaStreams")
        if streams is None:
            streams = await self._fetch_media_streams(np.get("Id"))
        subs = [s for s in streams or [] if s.get("Type") == "Subtitle"]
        if not subs:
            return "This video has no subtitle tracks."

        chosen = await self._choose_subtitle(subs, language)
        await self._send_command(
            session["Id"],
            "SetSubtitleStreamIndex",
            {"Index": str(chosen.get("Index", 0))},
        )
        label = (
            chosen.get("DisplayLanguage")
            or (chosen.get("Language") or "").title()
            or ""
        )
        return f"Subtitles on — {label}." if label else "Subtitles on."

    # Audio tracks a listener does not mean by "switch to English": audio
    # description for the visually impaired and commentary tracks.
    _AUDIO_SPECIAL_RE = re.compile(
        r"descriptive|description|commentary|visually impaired|\bAD\b", re.I
    )

    async def set_audio_language(
        self, language: str, device: str | None = None
    ) -> str:
        """Switch the playing item's audio track to the spoken language.
        Among several tracks in that language the plain (non-descriptive,
        non-commentary) one is preferred, then the server's default flag."""
        if not language or not language.strip():
            raise ValueError("Which language? Say e.g. \"switch the audio to French\".")
        session = await self._pick_session_full(device)
        np = session.get("NowPlayingItem")
        if not np:
            return "Nothing is currently playing."
        if not self._supports_command(session, "SetAudioStreamIndex"):
            raise ValueError(
                f"{self._device_label(session)} doesn't support switching audio tracks."
            )

        streams = np.get("MediaStreams")
        if streams is None:
            streams = await self._fetch_media_streams(np.get("Id"))
        audio = [s for s in streams or [] if s.get("Type") == "Audio"]
        if not audio:
            return "This video has no audio tracks to choose from."

        matches = [s for s in audio if self._lang_matches(s, language)]
        if not matches:
            have = sorted({self._audio_lang_label(s) for s in audio})
            raise ValueError(
                f"No {language} audio here. Available: {', '.join(have)}."
            )
        chosen = self._choose_audio(matches)
        await self._send_command(
            session["Id"],
            "SetAudioStreamIndex",
            {"Index": str(chosen.get("Index", 0))},
        )
        label = chosen.get("DisplayLanguage") or language.strip().title()
        return f"Audio — {label}."

    def _audio_lang_label(self, stream: dict) -> str:
        """Human name for a stream's language: the server's DisplayLanguage,
        else the spoken name whose ISO codes include the stream's code,
        else the raw code."""
        if stream.get("DisplayLanguage"):
            return stream["DisplayLanguage"]
        code = (stream.get("Language") or "").lower()
        for name, codes in self._SUBTITLE_LANGS.items():
            if code in codes:
                return name.title()
        return code.title() or "Unknown"

    def _choose_audio(self, matches: list[dict]) -> dict:
        plain = [
            s for s in matches
            if not self._AUDIO_SPECIAL_RE.search(s.get("DisplayTitle") or "")
            and not self._AUDIO_SPECIAL_RE.search(s.get("Title") or "")
        ] or matches
        return next((s for s in plain if s.get("IsDefault")), plain[0])

    async def _fetch_media_streams(self, item_id: str | None) -> list[dict]:
        """Fallback when the session's NowPlayingItem omits MediaStreams."""
        if not item_id:
            return []
        try:
            data = await self._api_get(
                f"/Items?Ids={item_id}&Fields=MediaStreams{self._user_q}"
            )
            items = data.get("Items", [])
            return items[0].get("MediaStreams", []) if items else []
        except Exception:
            return []

    def _lang_matches(self, stream: dict, spoken: str) -> bool:
        spoken = spoken.strip().lower()
        codes = self._SUBTITLE_LANGS.get(spoken, {spoken})
        if (stream.get("Language") or "").lower() in codes:
            return True
        display = (
            f"{stream.get('DisplayLanguage') or ''} "
            f"{stream.get('DisplayTitle') or ''}"
        ).lower()
        return spoken in display

    async def _user_subtitle_pref(self) -> str | None:
        """The identity's server-side preferred subtitle language (ISO code)."""
        if not self.user_id:
            return None
        try:
            user = await self._api_get(f"/Users/{self.user_id}")
            pref = user.get("Configuration", {}).get("SubtitleLanguagePreference")
            return (pref or "").lower() or None
        except Exception:
            return None

    async def _choose_subtitle(self, subs: list[dict], language: str | None) -> dict:
        """Pick a subtitle stream: spoken language > user's server-side
        preference > server's default flag > first full track. Forced
        tracks (foreign-dialogue-only) never satisfy 'subtitles on'."""
        if language:
            matches = [s for s in subs if self._lang_matches(s, language)]
            if not matches:
                have = sorted({
                    (s.get("DisplayLanguage") or s.get("Language") or "unknown").title()
                    for s in subs
                })
                raise ValueError(
                    f"No {language} subtitles here. Available: {', '.join(have)}."
                )
            return next((s for s in matches if not s.get("IsForced")), matches[0])

        full = [s for s in subs if not s.get("IsForced")] or subs
        pref = await self._user_subtitle_pref()
        if pref:
            for s in full:
                if (s.get("Language") or "").lower() == pref:
                    return s
        for s in full:
            if s.get("IsDefault"):
                return s
        return full[0]

    # ── Genres ───────────────────────────────────────────────────────

    # Spoken genre words → substrings to match against the server's genre
    # names (metadata providers differ: movies say "Science Fiction", TV
    # says "Sci-Fi & Fantasy"). Keys are post-normalization (lowercase,
    # punctuation stripped). Keep in sync with _GENRE_WORDS in intent.py.
    _GENRE_ALIASES = {
        "scifi": ["science fiction", "scifi", "sci fi"],
        "sci fi": ["science fiction", "scifi", "sci fi"],
        "science fiction": ["science fiction", "scifi", "sci fi"],
        "comedies": ["comedy"],
        "documentaries": ["documentary"],
        "romantic": ["romance"],
        "animated": ["animation"],
        "kids": ["kids", "children"],
        "historical": ["history"],
        "superheroes": ["superhero"],
    }

    @staticmethod
    def _norm_genre(name: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", name.lower())).strip()

    async def _resolve_genre(self, spoken: str) -> tuple[list[str], str]:
        """Match a spoken genre word against the server's actual genre list.

        Returns (genre_ids, display_label) — usually several ids, because
        movie and TV metadata name the same genre differently.
        Raises ValueError if nothing in the library matches.
        """
        data = await self._api_get(
            f"/Genres?Recursive=true&Limit=2000{self._user_q}"
        )
        genres = data.get("Items", [])
        spoken_n = self._norm_genre(spoken)
        wanted = self._GENRE_ALIASES.get(spoken_n) or [
            spoken_n, spoken_n.rstrip("s")
        ]
        ids, names = [], []
        for g in genres:
            gid, gname = g.get("Id"), g.get("Name", "")
            if not gid:
                continue
            gname_n = self._norm_genre(gname)
            if any(w and w in gname_n for w in wanted):
                ids.append(str(gid))
                names.append(gname)
        if not ids:
            raise ValueError(
                f'I couldn\'t find a "{spoken}" genre in this library.'
            )
        logger.debug("_resolve_genre: '%s' -> %s", spoken, names)
        return ids, spoken

    async def _genre_items(
        self,
        genre_ids: list[str],
        include_types: str,
        sort_by: str,
        limit: int,
        extra_q: str = "",
    ) -> list[dict]:
        q = (
            f"/Items?Recursive=true&GenreIds={','.join(genre_ids)}"
            f"&SortBy={sort_by}&Limit={limit}{extra_q}{self._user_q}"
        )
        if include_types:
            q += f"&IncludeItemTypes={include_types}"
        if sort_by == "DateCreated":
            q += "&SortOrder=Descending"
        data = await self._api_get(q)
        return data.get("Items", [])

    @staticmethod
    def _filter_q(
        unwatched: bool | None = None,
        decade: str | None = None,
        top_rated: bool | None = None,
    ) -> str:
        """Query fragment for the optional random/shuffle refinements."""
        q = ""
        if unwatched:
            q += "&Filters=IsUnplayed"
        if decade and decade.isdigit():
            start = int(decade)
            q += "&Years=" + ",".join(str(y) for y in range(start, start + 10))
        if top_rated:
            q += "&MinCommunityRating=7"
        return q

    # ── Shuffle / random ─────────────────────────────────────────────

    async def shuffle(
        self,
        series_name: str | None = None,
        media_type: str | None = None,
        genre: str | None = None,
        device: str | None = None,
        unwatched: bool | None = None,
        decade: str | None = None,
        top_rated: bool | None = None,
    ) -> str:
        """Shuffle a series' episodes, a genre, or play a random movie —
        optionally restricted to unwatched / a decade / well-rated."""
        filter_q = self._filter_q(unwatched, decade, top_rated)
        if series_name:
            return await self._shuffle_series(series_name, device, unwatched)
        if genre:
            return await self._shuffle_genre(
                genre, media_type, device, filter_q, unwatched
            )
        if media_type == "movie" or decade or top_rated:
            return await self._play_random_movie(device, filter_q=filter_q)
        if media_type == "episode":
            data = await self._api_get(
                f"/Items?IncludeItemTypes=Episode&Recursive=true"
                f"&SortBy=Random&Limit=1{filter_q}{self._user_q}"
            )
            items = data.get("Items", [])
            if not items:
                return "No episodes found in your library."
            session_id = await self._pick_session(device)
            await self._send_play(session_id, [items[0]["Id"]])
            return f"Playing {self._format_item(items[0])}."
        return "Tell me what to shuffle — a series, a genre, or say 'play a random movie'."

    async def _shuffle_series(
        self, series_name: str, device: str | None,
        unwatched: bool | None = None,
    ) -> str:
        series_id, resolved = await self._resolve_series(
            series_name, action="play_search"
        )
        return await self._shuffle_series_by_id(
            series_id, resolved, device, unwatched
        )

    async def _shuffle_series_by_id(
        self, series_id: str, series_label: str, device: str | None,
        unwatched: bool | None = None,
    ) -> str:
        data = await self._api_get(
            f"/Shows/{series_id}/Episodes?Fields=UserData&Limit=1000{self._user_q}"
        )
        # Skip Specials (season 0) but keep unnumbered seasons (None)
        episodes = [
            e for e in data.get("Items", [])
            if e.get("ParentIndexNumber") != 0
        ] or data.get("Items", [])
        if unwatched:
            episodes = [
                e for e in episodes
                if not e.get("UserData", {}).get("Played")
            ]
            if not episodes:
                raise ValueError(
                    f"Every episode of {series_label} is watched — nothing "
                    f"unwatched to shuffle!"
                )
        if not episodes:
            raise ValueError(f'No episodes found for "{series_label}".')

        session_id = await self._pick_session(device)
        random.shuffle(episodes)
        # Jellyfin takes ItemIds in the URL — keep the queue bounded
        queue = episodes[:30]
        await self._send_play(session_id, [e["Id"] for e in queue])
        first = queue[0]
        s = first.get("ParentIndexNumber") or 0
        e_num = first.get("IndexNumber") or 0
        return (
            f"Shuffling {series_label} — starting with "
            f'S{s:02d}E{e_num:02d} "{first.get("Name", "?")}".'
        )

    async def _shuffle_genre(
        self, genre: str, media_type: str | None, device: str | None,
        filter_q: str = "", unwatched: bool | None = None,
    ) -> str:
        """Genre-scoped randomness: a random movie, a single random episode,
        or a shuffled cross-series episode queue."""
        genre_ids, label = await self._resolve_genre(genre)

        if media_type == "movie":
            return await self._play_random_movie(
                device, genre_ids=genre_ids, filter_q=filter_q
            )

        single = media_type == "episode"
        # Episodes often inherit genres from their series, so try the direct
        # query first; some metadata setups only tag the Series item, in
        # which case fall back to picking a random series in the genre.
        episodes = await self._genre_items(
            genre_ids, "Episode", "Random", 1 if single else 30,
            extra_q=filter_q,
        )
        if episodes:
            session_id = await self._pick_session(device)
            await self._send_play(session_id, [e["Id"] for e in episodes])
            first = episodes[0]
            if single:
                return f"Playing {self._format_item(first)}."
            return (
                f"Shuffling {label} — starting with "
                f"{self._format_item(first)}."
            )

        series = await self._genre_items(genre_ids, "Series", "Random", 5)
        for candidate in series:
            if single:
                data = await self._api_get(
                    f"/Shows/{candidate['Id']}/Episodes?Fields=UserData"
                    f"&Limit=1000{self._user_q}"
                )
                eps = [
                    e for e in data.get("Items", [])
                    if e.get("ParentIndexNumber") != 0
                ] or data.get("Items", [])
                if unwatched:
                    eps = [
                        e for e in eps
                        if not e.get("UserData", {}).get("Played")
                    ]
                if not eps:
                    continue
                session_id = await self._pick_session(device)
                pick = random.choice(eps)
                await self._send_play(session_id, [pick["Id"]])
                return f"Playing {self._format_item(pick)}."
            try:
                return await self._shuffle_series_by_id(
                    candidate["Id"], candidate.get("Name", label), device,
                    unwatched,
                )
            except ValueError:
                continue
        raise ValueError(f"No {label} episodes found in this library.")

    async def _play_random_movie(
        self, device: str | None, genre_ids: list[str] | None = None,
        filter_q: str = "",
    ) -> str:
        genre_q = f"&GenreIds={','.join(genre_ids)}" if genre_ids else ""
        data = await self._api_get(
            f"/Items?IncludeItemTypes=Movie&Recursive=true"
            f"&SortBy=Random&Limit=1{genre_q}{filter_q}{self._user_q}"
        )
        items = data.get("Items", [])
        if not items:
            if genre_ids or filter_q:
                return "No movies match that here — try loosening it a little."
            return "No movies found in your library."
        session_id = await self._pick_session(device)
        movie = items[0]
        await self._send_play(session_id, [movie["Id"]])
        return f"Playing {self._format_item(movie)}."

    # ── Watched state ────────────────────────────────────────────────

    async def set_watched(self, watched: bool, device: str | None = None) -> str:
        """Mark whatever is playing on the session as watched/unwatched."""
        if not self.user_id:
            return "I don't know which user to mark this for."
        session = await self._pick_session_full(device)
        np = session.get("NowPlayingItem")
        if not np or not np.get("Id"):
            return "Nothing is currently playing."
        endpoint = f"/Users/{self.user_id}/PlayedItems/{np['Id']}"
        if watched:
            await self._api_post(endpoint)
        else:
            await self._api_delete(endpoint)
        label = self._format_item(np)
        return f"Marked {label} as {'watched' if watched else 'unwatched'}."

    # ── Collections & playlists ──────────────────────────────────────

    async def play_collection(
        self,
        query: str,
        kind: str | None = None,
        shuffled: bool | None = None,
        device: str | None = None,
    ) -> str:
        """Play a BoxSet (collection) or Playlist by name, in order or
        shuffled. kind narrows the search when the user said which."""
        types = {
            "collection": "BoxSet",
            "playlist": "Playlist",
        }.get(kind or "", "BoxSet,Playlist")
        items = await self._search_items_raw(
            query, f"&IncludeItemTypes={types}", limit=5
        )
        if not items:
            what = kind or "collection or playlist"
            raise ValueError(f'No {what} found matching "{query}".')
        result = _pick_best(query, items)
        if isinstance(result, list):
            result = result[0]  # same-name box set + playlist: prefer top score
        name = result.get("Name", query)

        if result.get("Type") == "Playlist":
            data = await self._api_get(
                f"/Playlists/{result['Id']}/Items?Limit=200{self._user_q}"
            )
        else:
            data = await self._api_get(
                f"/Items?ParentId={result['Id']}"
                f"&SortBy=PremiereDate,SortName&Limit=200{self._user_q}"
            )
        children = [c for c in data.get("Items", []) if c.get("Id")]
        if not children:
            raise ValueError(f'"{name}" is empty.')

        if shuffled:
            random.shuffle(children)
        session_id = await self._pick_session(device)
        # Jellyfin takes ItemIds in the URL — keep the queue bounded
        queue = children[:30]
        await self._send_play(session_id, [c["Id"] for c in queue])
        first = self._format_item(queue[0])
        mode = "shuffled" if shuffled else f"{len(queue)} items"
        return f"Playing {name} ({mode}) — starting with {first}."

    async def search(self, query: str, media_type: str | None = None) -> str:
        type_param = ""
        if media_type:
            emby_t = self._emby_type(media_type)
            if emby_t:
                type_param = f"&IncludeItemTypes={emby_t}"

        items = await self._search_items_raw(query, type_param, limit=10)
        if not items:
            return f'No results found for "{query}".'

        lines = []
        for item in items:
            display = self._format_item(item)
            itype = item.get("Type", "?")
            lines.append(f"{display} [{itype}]")

        lines.append(f"\n{len(items)} results.")
        return "\n".join(lines)

    async def get_now_playing(self) -> str:
        sessions = await self._get_user_sessions()
        playing = [s for s in sessions if s.get("NowPlayingItem")]

        if not playing:
            return "Nothing is currently playing."

        lines = []
        for s in playing:
            device = s.get("DeviceName", "?")
            np = s["NowPlayingItem"]
            display = self._format_item(np)
            state = "Paused" if s.get("PlayState", {}).get("IsPaused") else "Playing"
            lines.append(f"[{state}] {display} on {device}")
        return "\n".join(lines)

    def _latest_type_param(self, media_type: str | None) -> str:
        """Type filter for the Latest endpoint.

        Neither Emby nor Jellyfin honors IncludeItemTypes=Series on Latest
        (Emby ignores it, Jellyfin hangs) — but both accept Episode and
        auto-group new episodes into Series entries.
        """
        if not media_type:
            return ""
        emby_t = self._emby_type(media_type)
        if not emby_t:
            return ""
        if emby_t == "Series":
            emby_t = "Episode"
        return f"&IncludeItemTypes={emby_t}"

    async def get_recently_added(self, media_type: str | None = None) -> str:
        type_param = self._latest_type_param(media_type)

        items = await self._get_latest_items(10, type_param)
        if not items:
            return "No recently added media."

        lines = []
        for item in items:
            display = self._format_item(item)
            itype = item.get("Type", "?")
            lines.append(f"{display} [{itype}]")

        lines.append(f"\n{len(items)} recently added items.")
        return "\n".join(lines)

    async def get_recently_added_items(
        self, media_type: str | None = None, limit: int = 5,
        genre: str | None = None,
    ) -> list[dict]:
        """Return recently added items as structured disambiguation candidates."""
        if genre:
            # Genre filtering needs the /Items query, not /Latest — and
            # unlike /Latest, /Items handles IncludeItemTypes=Series fine
            genre_ids, _label = await self._resolve_genre(genre)
            types = {
                "movie": "Movie",
                "series": "Series",
            }.get(media_type or "", "Movie,Series")
            items = await self._genre_items(genre_ids, types, "DateCreated", limit)
            return self._build_candidates(items[:limit], "play_search")

        type_param = self._latest_type_param(media_type)
        items = await self._get_latest_items(limit, type_param)
        if not items:
            return []
        return self._build_candidates(items[:limit], "play_search")

    async def _get_latest_items(self, limit: int, type_param: str) -> list[dict]:
        """Fetch recently added items, working with or without a configured user."""
        if self.user_id:
            items = await self._api_get(
                f"/Users/{self.user_id}/Items/Latest?Limit={limit}{type_param}"
            )
            return items or []
        # No user configured — fall back to a plain date-sorted query
        data = await self._api_get(
            f"/Items?SortBy=DateCreated&SortOrder=Descending"
            f"&Recursive=true&Limit={limit}{type_param}"
        )
        return data.get("Items", [])

    # ── Browse tab ───────────────────────────────────────────────────
    # Everything here returns "browse cards": the disambiguation-card
    # shape plus image_id / watch_state / resume_ticks, uncapped.

    # Chips offered in the Browse tab, in display order — intersected
    # with the genres the library actually has
    _BROWSE_GENRE_CHIPS = [
        "Sci-Fi", "Horror", "Comedy", "Action", "Thriller", "Drama",
        "Documentary", "Animation", "Fantasy", "Crime", "Western",
        "Romance", "Family", "Mystery", "War", "Musical",
    ]

    def _browse_cards(self, items: list[dict], action: str = "play_search") -> list[dict]:
        cards = []
        for item in items:
            if not item.get("Id"):
                continue
            itype = item.get("Type", "")
            ud = item.get("UserData") or {}
            if ud.get("Played"):
                watch = "watched"
            elif ud.get("PlaybackPositionTicks", 0) > 0:
                watch = "in_progress"
            else:
                watch = "unwatched"
            if itype == "Episode":
                s = item.get("ParentIndexNumber") or 0
                e = item.get("IndexNumber") or 0
                subtitle = f"{item.get('SeriesName', '')} S{s:02d}E{e:02d}".strip()
            elif item.get("ProductionYear"):
                subtitle = f"{itype} ({item['ProductionYear']})"
            else:
                subtitle = itype
            card = {
                "id": item["Id"],
                "title": item.get("Name", "?"),
                "subtitle": subtitle,
                "type": itype,
                "action": "browse_series" if itype == "Series" else action,
                "server_id": "",
                "watch_state": watch,
                # Episode primary images are landscape stills — the series
                # poster keeps the grid uniform
                "image_id": item.get("SeriesId") if itype == "Episode" and item.get("SeriesId") else item["Id"],
            }
            if watch == "in_progress":
                card["resume_ticks"] = ud.get("PlaybackPositionTicks", 0)
                pct = ud.get("PlayedPercentage")
                if pct:
                    card["progress"] = round(pct)
            cards.append(card)
        return cards

    async def get_resume_items(self, limit: int = 12) -> list[dict]:
        """Continue-watching row (needs a user scope; empty without one)."""
        if not self.user_id:
            return []
        data = await self._api_get(
            f"/Users/{self.user_id}/Items/Resume?Recursive=true"
            f"&Limit={limit}&MediaTypes=Video&Fields=UserData"
        )
        return self._browse_cards(data.get("Items", []))

    async def get_next_up(self, limit: int = 12) -> list[dict]:
        """Next-unwatched-episode-per-show row."""
        if not self.user_id:
            return []
        data = await self._api_get(
            f"/Shows/NextUp?UserId={self.user_id}&Limit={limit}&Fields=UserData"
        )
        return self._browse_cards(data.get("Items", []))

    async def get_browse_latest(self, limit: int = 12) -> list[dict]:
        items = await self._get_latest_items(limit, "")
        return self._browse_cards(items)

    async def get_genre_chips(self) -> list[str]:
        """The curated chips that exist in this library, display order."""
        try:
            data = await self._api_get(
                f"/Genres?Recursive=true&Limit=2000{self._user_q}"
            )
        except Exception:
            return []
        have = {
            self._norm_genre(g.get("Name", ""))
            for g in data.get("Items", [])
        }
        chips = []
        for chip in self._BROWSE_GENRE_CHIPS:
            n = self._norm_genre(chip)
            wanted = self._GENRE_ALIASES.get(n) or [n]
            if any(w in h for w in wanted for h in have):
                chips.append(chip)
        return chips

    async def get_browse_genre_items(self, genre: str, limit: int = 24) -> list[dict]:
        genre_ids, _label = await self._resolve_genre(genre)
        items = await self._genre_items(
            genre_ids, "Movie,Series", "DateCreated", limit,
            extra_q="&Fields=UserData",
        )
        return self._browse_cards(items)

    async def get_browse_search(self, query: str, limit: int = 12) -> list[dict]:
        items = await self._search_items_raw(
            query, "&IncludeItemTypes=Movie,Series,Episode&Fields=UserData", limit
        )
        return self._browse_cards(items)

    async def get_browse_sessions(self) -> list[dict]:
        """Now-playing-around-the-house strip (controllable sessions only)."""
        sessions = await self._get_user_sessions()
        out = []
        for s in sessions:
            if not s.get("SupportsRemoteControl"):
                continue
            if (s.get("Client") or "").lower() == "hailfrequency":
                continue
            np = s.get("NowPlayingItem")
            out.append({
                "device_name": s.get("DeviceName", "?"),
                "client": s.get("Client", ""),
                "item": self._format_item(np) if np else None,
                "state": "Paused" if s.get("PlayState", {}).get("IsPaused")
                         else ("Playing" if np else "Idle"),
            })
        # Playing first, idle last
        out.sort(key=lambda d: (d["item"] is None, d["device_name"].lower()))
        return out

    # ── Playback state (for now-playing card) ───────────────────────

    async def previous_track(self, device: str | None = None) -> str:
        """Go to previous track / episode."""
        session_id = await self._pick_session(device)
        await self._send_playstate(session_id, "PreviousTrack")
        return "Skipped to previous."

    async def get_playback_state(self, device: str | None = None) -> dict | None:
        """Get structured playback state for the now-playing card."""
        sessions = await self._get_user_sessions()
        playing = [s for s in sessions if s.get("NowPlayingItem")]

        if not playing:
            return None

        # Prefer session matching the requested device
        s = playing[0]
        if device:
            patterns = _device_patterns(device, self.device_aliases)
            for sess in playing:
                dn = (sess.get("DeviceName") or "").lower()
                if any(p in dn for p in patterns):
                    s = sess
                    break
        np = s["NowPlayingItem"]
        play_state = s.get("PlayState", {})

        result = {
            "item_name": np.get("Name", "?"),
            "device_name": s.get("DeviceName", "?"),
            "state": "Paused" if play_state.get("IsPaused") else "Playing",
            "position_ticks": play_state.get("PositionTicks", 0),
            "runtime_ticks": np.get("RunTimeTicks", 0),
            "item_id": np.get("Id"),
        }

        # Add series info if applicable
        series = np.get("SeriesName")
        if series:
            s_num = np.get("ParentIndexNumber") or 0
            e_num = np.get("IndexNumber") or 0
            result["series_name"] = series
            result["episode_info"] = f"S{s_num:02d}E{e_num:02d}"

        return result

    # ── Connection test ─────────────────────────────────────────────

    async def test_connection(self) -> bool:
        try:
            await self._api_get("/System/Info")
            return True
        except Exception:
            return False

    # ── Direct-play methods (for disambiguation) ────────────────────

    async def play_item_by_id(
        self, item_id: str, device: str | None = None, resume: bool = False
    ) -> str:
        """Play a specific item by its ID, skipping search. With resume=True,
        start from the user's saved position (Continue Watching taps)."""
        if self.user_id:
            data = await self._api_get(f"/Users/{self.user_id}/Items/{item_id}")
        else:
            result = await self._api_get(f"/Items?Ids={item_id}")
            items = result.get("Items", [])
            if not items:
                raise ValueError("Item not found.")
            data = items[0]
        # A series card (e.g. from recently-added) → play its next episode,
        # not the raw series node (client behavior for that is undefined)
        if data.get("Type") == "Series":
            return await self.play_next_by_id(item_id, device)
        display = self._format_item(data)
        start_ticks = None
        if resume:
            ud = data.get("UserData") or {}
            start_ticks = ud.get("PlaybackPositionTicks") or None
        session_id = await self._pick_session(device)
        await self._send_play(session_id, [item_id], start_ticks)
        if start_ticks:
            return f"Resuming {display}."
        return f"Playing {display}."

    async def play_next_by_id(self, series_id: str, device: str | None = None) -> str:
        """Play the next episode of a series identified by ID."""
        episode_id, description = await self._find_next_episode(series_id)
        session_id = await self._pick_session(device)
        await self._send_play(session_id, [episode_id])
        return description

    # ── Series browsing ────────────────────────────────────────────

    @staticmethod
    def _season_number_from_name(name: str) -> int | None:
        """Extract a trailing season number from a folder-style season name.

        Handles unnumbered seasons like "House of the Dragon (2022)3" → 3.
        """
        m = re.search(r"(\d+)\s*$", name or "")
        if m:
            n = int(m.group(1))
            if 0 < n < 200:  # sanity guard against years etc.
                return n
        return None

    def _match_seasons_by_number(
        self, entries: list[dict], season: int
    ) -> list[dict]:
        """Find season entries matching a number — by IndexNumber, or by a
        trailing number in the Name for unnumbered folder-style seasons."""
        matched = []
        for e in entries:
            if e.get("IndexNumber") == season:
                matched.append(e)
            elif (
                e.get("IndexNumber") is None
                and self._season_number_from_name(e.get("Name", "")) == season
            ):
                matched.append(e)
        return matched

    def _build_season_cards(
        self,
        entries: list[dict],
        series_id: str,
        action: str,
        subtitle: str,
        episode: int | None = None,
    ) -> list[dict]:
        """Build disambiguation cards for season entries."""
        cards = []
        for item in entries:
            card = {
                "id": item["Id"],
                "title": item.get("Name", "?"),
                "subtitle": subtitle,
                "type": "Season",
                "action": action,
                "server_id": "",
                "parent_id": series_id,
            }
            if episode is not None:
                card["episode"] = episode
            cards.append(card)
        return cards

    async def _get_seasons_raw(self, series_id: str) -> list[dict]:
        """Fetch raw season items for a series."""
        endpoint = f"/Shows/{series_id}/Seasons"
        if self.user_id:
            endpoint += f"?UserId={self.user_id}"
        data = await self._api_get(endpoint)
        return data.get("Items", [])

    async def get_seasons(self, series_id: str) -> list[dict]:
        """Return seasons for a series as disambiguation candidates."""
        items = await self._get_seasons_raw(series_id)
        candidates = []
        for item in items:
            ep_count = item.get("ChildCount", 0)
            candidates.append({
                "id": item["Id"],
                "title": item.get("Name", "?"),
                "subtitle": f"{ep_count} episodes" if ep_count else "",
                "type": "Season",
                "action": "browse_season",
                "server_id": "",
                "parent_id": series_id,
            })
        return candidates

    async def get_season_episodes(
        self, series_id: str, season: int | str
    ) -> list[dict]:
        """Return episodes for a season as disambiguation candidates with watch state.

        season can be an int (season number from voice) or str (season ID from card tap).
        """
        if isinstance(season, int):
            params = f"&Season={season}"
        else:
            params = f"&SeasonId={season}"

        data = await self._api_get(
            f"/Shows/{series_id}/Episodes?Fields=UserData{params}{self._user_q}"
        )
        items = data.get("Items", [])
        candidates = []
        for ep in items:
            s = ep.get("ParentIndexNumber") or 0
            e = ep.get("IndexNumber") or 0
            name = ep.get("Name", "?")
            ud = ep.get("UserData", {})

            if ud.get("Played", False):
                watch_state = "watched"
            elif ud.get("PlaybackPositionTicks", 0) > 0:
                watch_state = "in_progress"
            else:
                watch_state = "unwatched"

            candidates.append({
                "id": ep["Id"],
                "title": f"S{s:02d}E{e:02d} {name}",
                "subtitle": "",
                "type": "Episode",
                "action": "play_search",
                "server_id": "",
                "watch_state": watch_state,
            })
        return candidates

    # ── Person search ────────────────────────────────────────────

    async def search_person(self, query: str) -> dict | None:
        """Search for a person (actor/director). Returns {id, name} or None."""
        items = await self._search_items_raw(
            query, "&IncludeItemTypes=Person", limit=1
        )
        if not items:
            return None
        return {"id": items[0]["Id"], "name": items[0].get("Name", query)}

    async def get_person_filmography(
        self,
        person_id: str,
        media_type: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Return a person's filmography as disambiguation candidates."""
        type_param = ""
        if media_type:
            emby_t = self._emby_type(media_type)
            if emby_t:
                type_param = f"&IncludeItemTypes={emby_t}"
        data = await self._api_get(
            f"/Items?PersonIds={person_id}&Recursive=true"
            f"&SortBy=ProductionYear&SortOrder=Descending"
            f"&Limit={limit}{self._user_q}{type_param}"
        )
        items = data.get("Items", [])
        candidates = []
        for item in items:
            item_type = item.get("Type", "")
            action = "browse_series" if item_type == "Series" else "play_search"
            year = item.get("ProductionYear", "")
            title = f"{item.get('Name', '?')} ({year})" if year else item.get("Name", "?")
            candidates.append({
                "id": item["Id"],
                "title": title,
                "subtitle": item_type,
                "type": item_type,
                "action": action,
                "server_id": "",
            })
        return candidates

    async def get_sessions(self) -> list[dict]:
        return await self._get_user_sessions()

    async def close(self) -> None:
        await self.client.aclose()
