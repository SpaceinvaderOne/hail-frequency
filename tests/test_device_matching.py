"""Resolving a spoken device name to a media-server session."""
import pytest

from app.adapters.emby import EmbyAdapter
from app.adapters.jellyfin import JellyfinAdapter
from app.services.intent import try_fast_parse
from app.services.tts_responses import _detect_error_type

SHIELD = {"Id": "s1", "DeviceName": "SHIELD Android TV", "Client": "Emby TV",
          "SupportsRemoteControl": True}
BEDROOM = {"Id": "s2", "DeviceName": "Bedroom TV", "Client": "Emby Web",
           "SupportsRemoteControl": True}


def _fake_sessions(adapter, sessions):
    async def _get():
        return sessions
    adapter._get_user_sessions = _get


async def test_leading_articles_are_ignored():
    a = EmbyAdapter("http://x", "k", "u",
                    device_aliases=[{"alias": "shield", "pattern": "SHIELD"}])
    _fake_sessions(a, [SHIELD, BEDROOM])
    try:
        for spoken, want in [
            ("the shield", "s1"), ("shield", "s1"), ("The Shield", "s1"),
            ("my bedroom tv", "s2"), ("the bedroom tv", "s2"),
        ]:
            assert (await a._pick_session_full(spoken))["Id"] == want, spoken
        with pytest.raises(ValueError, match='No session matching "the attic"'):
            await a._pick_session_full("the attic")
    finally:
        await a.close()


async def test_fast_path_device_reaches_the_session():
    intent = try_fast_parse("play bosch season 2 episode 5 on the shield")
    assert intent.action == "play_episode" and intent.device
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    _fake_sessions(a, [SHIELD])
    try:
        assert (await a._pick_session_full(intent.device))["Id"] == "s1"
    finally:
        await a.close()


async def test_no_session_message_names_the_server_type():
    for cls, label in ((EmbyAdapter, "Emby"), (JellyfinAdapter, "Jellyfin")):
        a = cls("http://x", "k", "u", [])
        _fake_sessions(a, [])
        try:
            with pytest.raises(ValueError) as exc:
                await a._pick_session_full(None)
        finally:
            await a.close()
        msg = str(exc.value)
        assert label in msg and "Emby" not in msg.replace(label, "")
        assert _detect_error_type(msg) == "no_session"
