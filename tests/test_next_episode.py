"""Skipping forward: "next episode" plays the episode after the one on
screen, in season/episode order, whatever its watched state.

"Play the next episode of <series>" is a different command: continue
watching, which resumes an in-progress episode or starts the first
unwatched one. The two must not share a finder, or skipping from an
episode that isn't yet marked watched lands straight back on it.
"""

import pytest

from app.adapters.emby import EmbyAdapter
from app.adapters.jellyfin import JellyfinAdapter


def _ep(id_, season, episode, name, *, played=False, position=0):
    return {
        "Id": id_,
        "ParentIndexNumber": season,
        "IndexNumber": episode,
        "Name": name,
        "SeriesName": "Show",
        "UserData": {"Played": played, "PlaybackPositionTicks": position,
                     "PlayedPercentage": 24 if position else 0},
    }


# Two seasons, the viewer is midway through S01E02 (unwatched, some
# progress) and has not watched anything after it.
LIBRARY = [
    _ep("e1", 1, 1, "Pilot", played=True),
    _ep("e2", 1, 2, "Second", position=1000),
    _ep("e3", 1, 3, "Third"),
    _ep("e4", 2, 1, "Opener"),
]


def _wire(adapter, playing, library=LIBRARY, device_name="TV"):
    """Stub the server: one controllable session with `playing` on screen,
    and a /Shows/{id}/Episodes listing of `library` (returned unsorted so
    the sort is exercised)."""
    calls = {"played": None, "listed": False}

    async def pick(device=None):
        return {"Id": "sess", "DeviceName": device_name,
                "NowPlayingItem": playing, "SupportsRemoteControl": True}

    async def api_get(path):
        assert "/Shows/series-1/Episodes" in path
        calls["listed"] = True
        return {"Items": list(reversed(library))}

    async def send_play(session_id, ids, **kw):
        calls["played"] = ids

    adapter._pick_session_full = pick
    adapter._api_get = api_get
    adapter._send_play = send_play
    return calls


def _now_playing(ep, **overrides):
    item = {"Id": ep["Id"], "SeriesId": "series-1", "SeriesName": "Show",
            "ParentIndexNumber": ep["ParentIndexNumber"],
            "IndexNumber": ep["IndexNumber"], "Type": "Episode"}
    item.update(overrides)
    return item


@pytest.mark.parametrize("cls", [EmbyAdapter, JellyfinAdapter])
async def test_next_episode_steps_past_the_playing_one(cls):
    a = cls("http://x", "k", "u", device_aliases=[])
    calls = _wire(a, _now_playing(LIBRARY[1]))
    msg = await a.next_episode()
    assert calls["played"] == ["e3"]
    assert msg == 'Next: Show S01E03 "Third"'


async def test_next_episode_ignores_watched_state():
    """Even when the playing episode is already marked Played and later
    ones are too, skip means the following episode, not the first
    unwatched one."""
    lib = [_ep("e1", 1, 1, "A", played=True), _ep("e2", 1, 2, "B", played=True),
           _ep("e3", 1, 3, "C", played=True), _ep("e4", 1, 4, "D")]
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    calls = _wire(a, _now_playing(lib[1]), library=lib)
    await a.next_episode()
    assert calls["played"] == ["e3"]


async def test_next_episode_crosses_a_season_boundary():
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    calls = _wire(a, _now_playing(LIBRARY[2]))
    msg = await a.next_episode()
    assert calls["played"] == ["e4"]
    assert "S02E01" in msg


async def test_next_episode_falls_back_to_numbers_when_id_differs():
    """The session can report a different item Id from the library listing
    (merged versions); season and episode numbers still locate it."""
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    calls = _wire(a, _now_playing(LIBRARY[1], Id="other-version-id"))
    await a.next_episode()
    assert calls["played"] == ["e3"]


async def test_next_episode_on_the_last_episode_says_so():
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    calls = _wire(a, _now_playing(LIBRARY[3]))
    with pytest.raises(ValueError, match="last episode"):
        await a.next_episode()
    assert calls["played"] is None


async def test_next_episode_with_nothing_playing():
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    calls = _wire(a, None)
    assert await a.next_episode() == "Nothing is currently playing."
    assert calls["played"] is None and calls["listed"] is False


async def test_next_episode_on_a_movie():
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    calls = _wire(a, {"Id": "m1", "Type": "Movie", "Name": "Film"})
    assert await a.next_episode() == "Current item is not a series episode."
    assert calls["listed"] is False


async def test_play_next_still_means_continue_watching():
    """The series-named command keeps its resume-or-first-unwatched rule:
    with S01E02 part watched, that is what it picks, not S01E03."""
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    calls = _wire(a, _now_playing(LIBRARY[1]))

    async def resolve(name, action="play_next"):
        return "series-1", "Show"

    async def pick_id(device=None):
        return "sess"

    a._resolve_series = resolve
    a._pick_session = pick_id
    msg = await a.play_next("show")
    assert calls["played"] == ["e2"]
    assert msg.startswith("Resuming Show S01E02")
