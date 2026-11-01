"""Pooled HTTP for high-frequency media reads (image proxy).

The Browse grid fetches a poster per card — one image proxy request each.
One keep-alive client per media host, with the caller's token passed per
request, lets a grid of posters share a handful of pooled connections
instead of opening a TCP+TLS handshake per image. Closed on app shutdown
via close_all().

Only used for the image proxy — the voice and browse endpoints reuse a
single adapter (and its client) per request.
"""

import httpx

from ..adapters.base import media_auth_headers

# Emby serves its API under /emby; Jellyfin (a fork) uses no prefix.
_API_PREFIX = {"emby": "/emby", "jellyfin": ""}

_limits = httpx.Limits(max_keepalive_connections=20, max_connections=40)
_clients: dict[str, httpx.AsyncClient] = {}


def _client_for(base_url: str) -> httpx.AsyncClient:
    c = _clients.get(base_url)
    if c is None or c.is_closed:
        # No default auth header — the token is per-request (per identity)
        c = httpx.AsyncClient(timeout=15.0, limits=_limits)
        _clients[base_url] = c
    return c


def _token(server: dict, identity: dict | None) -> str:
    """Token for the request: the identity's own, or the server's configured
    key only when there is no identity at all. An identity without a token
    is refused rather than quietly promoted to the server's credentials."""
    if identity is None:
        return server["api_key"]
    if not identity.get("media_token"):
        raise ValueError(
            "No media-server token stored for this account — "
            "sign out and back in."
        )
    return identity["media_token"]


async def fetch_image(
    server: dict, identity: dict | None, item_id: str
) -> tuple[bytes, str] | None:
    """Fetch an item's primary image via the pooled client. Returns
    (content, content_type) or None on any failure. item_id MUST already be
    validated by the caller (it lands in the URL). Raises ValueError for an
    identity with no stored token (see _token)."""
    base = server["url"].rstrip("/")
    prefix = _API_PREFIX.get(server["type"], "")
    url = f"{base}{prefix}/Items/{item_id}/Images/Primary"
    token = _token(server, identity)
    try:
        resp = await _client_for(base).get(
            url,
            params={"maxHeight": 300, "maxWidth": 200},
            headers=media_auth_headers(token),
        )
        if resp.status_code != 200:
            return None
        return resp.content, resp.headers.get("content-type", "image/jpeg")
    except Exception:
        return None


async def close_all() -> None:
    for c in _clients.values():
        try:
            await c.aclose()
        except Exception:
            pass
    _clients.clear()
