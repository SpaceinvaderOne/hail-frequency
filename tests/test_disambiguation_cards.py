"""Disambiguation cards: when two matches would read identically, as
happens when one show is scanned from two library folders, the episode
count is appended so the user can tell them apart. Cards that already
differ are left exactly as they were.
"""

from app.adapters.emby import EmbyAdapter


def _series(id_, name, year, count=None):
    item = {"Id": id_, "Name": name, "ProductionYear": year, "Type": "Series"}
    if count is not None:
        item["RecursiveItemCount"] = count
    return item


def _adapter():
    return EmbyAdapter("http://x", "k", "u", device_aliases=[])


def test_identical_series_get_episode_counts():
    cards = _adapter()._build_candidates(
        [_series("a", "Star Trek: Voyager", 1995, 168),
         _series("b", "Star Trek: Voyager", 1995, 80)],
        "play_next",
    )
    assert [c["subtitle"] for c in cards] == [
        "Series (1995), 168 episodes",
        "Series (1995), 80 episodes",
    ]
    assert all("_count" not in c for c in cards)


def test_distinct_series_are_untouched():
    cards = _adapter()._build_candidates(
        [_series("a", "Star Trek: Voyager", 1995, 168),
         _series("b", "Star Trek: Discovery", 2017, 106)],
        "play_next",
    )
    assert [c["subtitle"] for c in cards] == ["Series (1995)", "Series (2017)"]


def test_same_name_different_year_is_already_distinct():
    cards = _adapter()._build_candidates(
        [_series("a", "Battlestar Galactica", 1978, 24),
         _series("b", "Battlestar Galactica", 2004, 75)],
        "play_next",
    )
    assert [c["subtitle"] for c in cards] == ["Series (1978)", "Series (2004)"]


def test_collision_without_counts_stays_plain():
    """A server that omits the count gets a plain card rather than a
    broken one."""
    cards = _adapter()._build_candidates(
        [_series("a", "Show", 2000), _series("b", "Show", 2000)], "play_next"
    )
    assert [c["subtitle"] for c in cards] == ["Series (2000)", "Series (2000)"]


def test_singular_and_non_series_wording():
    a = _adapter()
    cards = a._build_candidates(
        [_series("a", "Show", 2000, 1), _series("b", "Show", 2000, 1)], "play_next"
    )
    assert cards[0]["subtitle"] == "Series (2000), 1 episode"
    sets = a._build_candidates(
        [{"Id": "a", "Name": "Trek", "Type": "BoxSet", "RecursiveItemCount": 13},
         {"Id": "b", "Name": "Trek", "Type": "BoxSet", "RecursiveItemCount": 4}],
        "play_search",
    )
    assert [c["subtitle"] for c in sets] == ["BoxSet, 13 items", "BoxSet, 4 items"]


def test_card_shape_is_unchanged():
    (card,) = _adapter()._build_candidates([_series("a", "Show", 2000, 5)], "browse_series")
    assert set(card) == {"id", "title", "subtitle", "type", "action", "server_id"}
    assert card["action"] == "browse_series" and card["server_id"] == ""
