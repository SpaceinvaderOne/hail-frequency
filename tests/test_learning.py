import json

import pytest

from app import config, database
from app.services import parse_chain
from app.services.parse_chain import resolve
from tests.test_parse_chain import CFG, NO_OLLAMA_CFG, FakeOllama, patch_env  # noqa: F401


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()


def test_learn_and_lookup(temp_db):
    config.learn_phrase("play rogue one",
                        json.dumps({"action": "play_search", "query": "rogue one"}))
    stored = config.get_learned_intent("play rogue one")
    assert json.loads(stored)["query"] == "rogue one"
    assert config.count_learned_phrases() == 1


def test_evict_and_clear(temp_db):
    config.learn_phrase("a", '{"action": "pause"}')
    config.learn_phrase("b", '{"action": "pause"}')
    config.evict_learned_phrase("a")
    assert config.get_learned_intent("a") is None
    assert config.clear_learned_phrases() == 1
    assert config.count_learned_phrases() == 0


def test_prune_keeps_cap(temp_db):
    for i in range(database.LEARNED_MAX_ROWS + 20):
        config.learn_phrase(f"phrase {i}", '{"action": "pause"}')
    database.init_db()   # startup prune
    assert config.count_learned_phrases() <= database.LEARNED_MAX_ROWS


async def test_learned_tier_replays_without_ollama(temp_db):
    config.learn_phrase("play rogue one",
                        json.dumps({"action": "play_search", "query": "rogue one"}))
    out = await resolve("Play Rogue One!", NO_OLLAMA_CFG)   # normalizer matches
    assert out.source == "learned"
    assert out.intent.action == "play_search"
    assert out.degraded_reason is None
    assert FakeOllama.instances == []


async def test_learned_replay_is_a_copy(temp_db):
    config.learn_phrase("play rogue one",
                        json.dumps({"action": "play_search", "query": "rogue one"}))
    a = await resolve("play rogue one", NO_OLLAMA_CFG)
    a.intent.device = "mutated!"
    b = await resolve("play rogue one", NO_OLLAMA_CFG)
    assert b.intent.device is None


async def test_invalid_stored_intent_evicted_and_skipped(temp_db):
    config.learn_phrase("play rogue one", '{"action": "play_search"}')  # missing query
    out = await resolve("play rogue one", NO_OLLAMA_CFG)   # falls through to fallback
    assert out.source == "fallback"
    assert config.get_learned_intent("play rogue one") is None
