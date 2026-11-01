"""Fuzzy title matching: a spoken title the server search cannot find is
matched against the library's own title list by spelling and by sound."""
import httpx
import pytest

from app.adapters import emby as emby_mod
from app.adapters.base import AmbiguousMatchError
from app.adapters.emby import (
    EmbyAdapter,
    FUZZY_MAX_CARDS,
    _fuzzy_auto_pick,
    _fuzzy_candidates,
    _sound_key,
    _title_key,
)


def _lib(*names):
    return [{"Id": f"id-{i}", "Name": n, "Type": "Series"} for i, n in enumerate(names)]


LIBRARY = _lib(
    "Carrie", "Cars", "Cary Grant: A Life", "Kerry", "Sheldon", "The Office",
    "Office Space", "Reacher", "House", "House of the Dragon", "Severance",
    "Seven", "Se7en",
)


def _names(cands):
    return [c[0]["Name"] for c in cands]


# ── Pure matching ────────────────────────────────────────────────────


def test_keys_fold_articles_punctuation_and_sound():
    assert _title_key("The Forbidden Planet!") == "forbidden planet"
    assert _sound_key("Carrie") == _sound_key("cary") == _sound_key("Carey")
    assert _sound_key("cars") != _sound_key("Carrie")


def test_phonetic_match_auto_picks_over_longer_same_sound_title():
    cands = _fuzzy_candidates("cary", LIBRARY)
    assert _names(cands)[0] == "Carrie"
    assert "Cars" not in _names(cands)
    assert _fuzzy_auto_pick(cands)["Name"] == "Carrie"


def test_spelling_match_without_phonetic_agreement():
    cands = _fuzzy_candidates("shelton", LIBRARY)
    assert _names(cands) == ["Sheldon"]
    assert _fuzzy_auto_pick(cands)["Name"] == "Sheldon"


def test_phonetic_match_with_weak_spelling():
    cands = _fuzzy_candidates("richer", LIBRARY)
    assert _names(cands) == ["Reacher"]


def test_article_is_ignored_and_near_title_is_not_a_candidate():
    cands = _fuzzy_candidates("the office", LIBRARY)
    assert _names(cands) == ["The Office"]
    assert _fuzzy_auto_pick(cands)["Name"] == "The Office"


def test_clear_ratio_lead_auto_picks():
    cands = _fuzzy_candidates("seven", LIBRARY)
    assert _names(cands) == ["Seven", "Se7en"]
    assert _fuzzy_auto_pick(cands)["Name"] == "Seven"


REMAKES = [
    {"Id": "old", "Name": "Carrie", "Type": "Series", "ProductionYear": 1976},
    {"Id": "new", "Name": "Carrie", "Type": "Series", "ProductionYear": 2013},
    {"Id": "x", "Name": "Cars", "Type": "Series"},
]


def test_close_same_sound_candidates_need_the_user():
    # A remake: two titles with the same name and sound tie exactly.
    cands = _fuzzy_candidates("cary", REMAKES)
    assert [c[0]["Id"] for c in cands] == ["old", "new"]
    assert all(c[2] for c in cands)
    assert _fuzzy_auto_pick(cands) is None


def test_different_sound_and_weak_spelling_is_rejected():
    assert _names(_fuzzy_candidates("cars", _lib("Carrie"))) == []
    assert _names(_fuzzy_candidates("house", _lib("House of the Dragon"))) == []
    assert _fuzzy_candidates("", LIBRARY) == []
    assert _fuzzy_candidates("a", LIBRARY) == []


# ── Adapter integration ─────────────────────────────────────────────


class _Server:
    """Stands in for _api_get: empty search results, a fixed title list."""

    def __init__(self, titles, fail_titles=False):
        self.titles = titles
        self.fail_titles = fail_titles
        self.title_fetches = 0
        self.searches = 0

    async def __call__(self, endpoint):
        if "SearchTerm=" in endpoint:
            self.searches += 1
            return {"Items": []}
        self.title_fetches += 1
        if self.fail_titles:
            raise httpx.ConnectError("down")
        return {"Items": self.titles}


@pytest.fixture(autouse=True)
def _clear_title_cache():
    emby_mod._TITLE_CACHE.clear()
    yield
    emby_mod._TITLE_CACHE.clear()


async def test_series_miss_is_corrected_and_title_list_is_cached(monkeypatch):
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    server = _Server(LIBRARY)
    a._api_get = server
    try:
        assert await a._resolve_series("cary") == ("id-0", "Carrie")
        assert server.title_fetches == 1
        assert await a._resolve_series("shelton") == ("id-4", "Sheldon")
        assert server.title_fetches == 1

        now = emby_mod.time.time()
        monkeypatch.setattr(emby_mod.time, "time",
                            lambda: now + emby_mod.TITLE_CACHE_TTL + 1)
        assert await a._resolve_series("cary") == ("id-0", "Carrie")
        assert server.title_fetches == 2
    finally:
        await a.close()


async def test_title_fetch_failure_keeps_the_original_error():
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    a._api_get = _Server(LIBRARY, fail_titles=True)
    try:
        with pytest.raises(ValueError, match='No series found matching "cary"'):
            await a._resolve_series("cary")
        with pytest.raises(ValueError, match='No results found for "cary"'):
            await a.play_search("cary", "series")
    finally:
        await a.close()


async def test_fuzzy_tie_offers_cards_and_a_true_miss_is_unchanged():
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    a._api_get = _Server(REMAKES)
    try:
        with pytest.raises(AmbiguousMatchError) as exc:
            await a._resolve_series("cary", action="play_next")
        cards = exc.value.candidates
        assert [c["id"] for c in cards] == ["old", "new"]
        assert len(cards) <= FUZZY_MAX_CARDS
        assert cards[0]["subtitle"] == "Series (1976)"
        assert all(c["action"] == "play_next" for c in cards)
        assert exc.value.message == "Did you mean one of these?"

        with pytest.raises(ValueError, match='No series found matching "zorblax"'):
            await a._resolve_series("zorblax")
    finally:
        await a.close()


async def test_hot_path_never_fetches_the_title_list():
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    server = _Server(LIBRARY)

    async def found(endpoint):
        if "SearchTerm=" in endpoint:
            return {"Items": [{"Id": "hit", "Name": "Carrie", "Type": "Series"}]}
        return await server(endpoint)

    a._api_get = found
    try:
        assert await a._resolve_series("carrie") == ("hit", "Carrie")
        assert server.title_fetches == 0
    finally:
        await a.close()
