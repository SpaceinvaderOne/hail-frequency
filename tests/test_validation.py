"""LLM output validation and the phrase-learning decision."""
from types import SimpleNamespace

import pytest

from app import config, database
from app.models import Intent, VoiceResponse
from app.services.intent import validate_intent


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()


@pytest.mark.parametrize("raw", [
    {"action": "play_search", "query": 1899},
    {"action": "play_season", "series": 1923, "season": 1},
    {"action": "play_search", "query": "x", "type": 5},
    {"action": "pause", "device": 7},
    {"action": "seek", "position": 90},
    {"action": "shuffle", "type": "movie", "unwatched": "sure"},
    {"action": "shuffle", "type": "movie", "top_rated": 1},
])
def test_scalars_are_coerced(raw):
    out = validate_intent(raw)
    assert isinstance(out, Intent), out


def test_numeric_title_becomes_string():
    assert validate_intent({"action": "play_search", "query": 1899}).query == "1899"
    out = validate_intent({"action": "shuffle", "type": "movie", "unwatched": "false"})
    assert out.unwatched is False


def test_schema_garbage_returns_message_not_exception():
    out = validate_intent({"action": "play_search", "query": {"nested": True}})
    assert isinstance(out, (Intent, str))


def test_unknown_action_and_missing_fields_rejected():
    assert isinstance(validate_intent({"action": "delete_everything"}), str)
    assert isinstance(validate_intent({"action": "play_search"}), str)
    assert isinstance(validate_intent("not a dict"), str)


def test_voice_response_learned_field():
    v = VoiceResponse(transcript="t", result="r", success=True, learned=True)
    assert v.learned is True
    assert VoiceResponse(transcript="t", result="r", success=True).learned is False


def test_learn_from_outcome_returns_bool(temp_db):
    from app.routers.voice import _learn_from_outcome
    ollama_ok = SimpleNamespace(source="ollama",
                                raw_intent={"action": "play_search", "query": "x"})
    unknown = SimpleNamespace(source="ollama", raw_intent={"action": "unknown"})
    fast = SimpleNamespace(source="fast", raw_intent={"action": "pause"})

    assert _learn_from_outcome(ollama_ok, "play x", True) is True
    assert config.get_learned_intent("play x") is not None
    assert _learn_from_outcome(unknown, "gibberish", True) is False
    assert _learn_from_outcome(fast, "pause", True) is False
    assert _learn_from_outcome(ollama_ok, "play x", False) is False
