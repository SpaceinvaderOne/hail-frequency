"""Single place to construct media-server adapters from a DB server row.

Routers must use this instead of instantiating adapters directly — it is
the plug-in point for new server types.

Plex is not supported: modern official Plex clients accept remote-control
commands from nothing outside Plex's own apps, so a voice controller cannot
reliably start playback on them.
"""

from ..config import get_device_aliases, get_identity
from .emby import EmbyAdapter
from .jellyfin import JellyfinAdapter

_ADAPTERS = {
    "emby": EmbyAdapter,
    "jellyfin": JellyfinAdapter,
}


def identity_for(user: dict | None, server: dict) -> dict | None:
    """Resolve which media-server identity a request runs as.

    Anyone with a linked identity uses their OWN (this includes delegated
    users promoted to admin — their watch state stays theirs). Admins
    without one (the local admin) fall back to the server's configured
    credentials. Regular users without one are refused politely rather
    than leaking the admin's sessions and watch state.
    """
    if not user:
        return None
    ident = get_identity(user["id"], server["id"])
    if ident:
        return ident
    if user.get("role") == "admin":
        return None
    raise ValueError(
        f"You don't have an account on {server['name']}. "
        f"Sign in once with your {server['name']} credentials to link it."
    )


def create_adapter(
    server: dict, identity: dict | None = None, timeout: float = 15.0
):
    """Create the right adapter for a media_servers row.

    With an identity (a user_identities row), the adapter acts AS that
    media-server user: their access token (so the server enforces their
    library permissions) and their user id (sessions + watch state).

    Raises ValueError for unsupported server types, and for an identity
    with no stored token: an identity must never silently run on the
    server's configured credentials, or that user would act with the
    admin's library access.
    """
    adapter_cls = _ADAPTERS.get(server["type"])
    if adapter_cls is None:
        raise ValueError(f"Unsupported server type: {server['type']}")

    api_key = server["api_key"]
    user_id = server.get("user_id", "") or ""
    if identity is not None:
        if not identity.get("media_token"):
            raise ValueError(
                "No media-server token stored for this account — "
                "sign out and back in."
            )
        user_id = identity["media_user_id"]
        api_key = identity["media_token"]

    return adapter_cls(
        url=server["url"],
        api_key=api_key,
        user_id=user_id,
        device_aliases=get_device_aliases(server["id"]),
        timeout=timeout,
    )
