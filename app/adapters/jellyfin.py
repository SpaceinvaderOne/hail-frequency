"""Jellyfin media server adapter.

Jellyfin is a fork of Emby and keeps most of the API shape: identical
endpoints, field names, tick units (10M/s). The dialect differences that
matter (Jellyfin 10.11 and 12):

- No ``/emby`` URL path prefix.
- Play/playstate commands take QUERY PARAMETERS, not a JSON body —
  the body form Emby accepts returns 400 on Jellyfin.
- ``SearchTerm`` matching is much stricter than Emby's: "Star Wars
  Rogue One" does NOT match "Rogue One: A Star Wars Story". When the
  full-phrase search misses, retry per-word and score the merged
  results against the full query.
- Auth: only the ``Authorization: MediaBrowser Token="…"`` scheme (see
  ``media_auth_headers``).

Everything else is inherited from EmbyAdapter.
"""

import re

from .emby import EmbyAdapter, _score_match


class JellyfinAdapter(EmbyAdapter):
    API_PREFIX = ""
    SERVER_LABEL = "Jellyfin"

    async def _send_play(
        self, session_id: str, item_ids: list[str],
        start_ticks: int | None = None,
    ) -> None:
        """Jellyfin wants play parameters in the query string."""
        ids = ",".join(item_ids)
        endpoint = (
            f"/Sessions/{session_id}/Playing"
            f"?PlayCommand=PlayNow&ItemIds={ids}"
        )
        if start_ticks:
            endpoint += f"&StartPositionTicks={start_ticks}"
        await self._api_post(endpoint)

    async def _send_playstate(
        self, session_id: str, command: str, ticks: int | None = None
    ) -> None:
        endpoint = f"/Sessions/{session_id}/Playing/{command}"
        if ticks is not None:
            endpoint += f"?SeekPositionTicks={ticks}"
        await self._api_post(endpoint)

    async def _search_items_raw(
        self, query: str, type_param: str = "", limit: int = 5
    ) -> list[dict]:
        """Full-phrase search first; per-word fallback for strict matching."""
        items = await super()._search_items_raw(query, type_param, limit)
        if items:
            return items

        words = [w for w in re.split(r"\s+", query.strip()) if len(w) > 2]
        if len(words) < 2:
            return []

        seen: set = set()
        merged: list[dict] = []
        for word in words[:4]:
            for item in await super()._search_items_raw(word, type_param, limit):
                item_id = item.get("Id")
                if item_id and item_id not in seen:
                    seen.add(item_id)
                    merged.append(item)

        # Keep only items that genuinely relate to the full query, best first
        scored = [
            (item, _score_match(query, item.get("Name", "")))
            for item in merged
        ]
        scored = [(i, s) for i, s in scored if s > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [i for i, _ in scored[:limit]]
