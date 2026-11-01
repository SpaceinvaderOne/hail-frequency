import json
from types import SimpleNamespace

import pytest

from app import config, database


def _req(role):
    return SimpleNamespace(state=SimpleNamespace(
        user={"id": "u1", "username": "x", "role": role}))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()


def test_get_learned_phrases_order_and_shape(temp_db):
    config.learn_phrase("a", '{"action": "pause"}')
    config.learn_phrase("b", '{"action": "stop"}')
    config.get_learned_intent("a")      # bump a's last_used_at newest
    rows = config.get_learned_phrases()
    assert [r["normalized_phrase"] for r in rows][0] == "a"
    assert {"normalized_phrase", "intent_json", "use_count",
            "last_used_at"} <= set(rows[0].keys())


async def test_list_endpoint_visibility_toggle(temp_db):
    from app.routers.settings import list_learned
    config.learn_phrase("play rogue one",
                        '{"action": "play_search", "query": "rogue one"}')
    config.learn_phrase("corrupt", "not json{")

    body = await list_learned(_req("user"))          # default: public
    phrases = body["phrases"]
    assert phrases[0]["phrase"] in ("play rogue one", "corrupt")
    corrupt = [p for p in phrases if p["phrase"] == "corrupt"][0]
    assert corrupt["intent"] is None                 # corrupt row survives
    good = [p for p in phrases if p["phrase"] == "play rogue one"][0]
    assert good["intent"]["action"] == "play_search"

    config.set_setting("learned_phrases_public", "false")
    resp = await list_learned(_req("user"))
    assert getattr(resp, "status_code", 200) == 403  # non-admin blocked
    body = await list_learned(_req("admin"))
    assert len(body["phrases"]) == 2                 # admin always allowed


async def test_delete_endpoint_removes_one_row(temp_db):
    from app.models import LearnedPhraseDeleteRequest
    from app.routers.settings import delete_learned
    config.learn_phrase("a", '{"action": "pause"}')
    config.learn_phrase("b", '{"action": "stop"}')
    out = await delete_learned(LearnedPhraseDeleteRequest(phrase="a"))
    assert out["deleted"] is True
    assert config.get_learned_intent("a") is None
    assert config.get_learned_intent("b") is not None


async def test_visibility_endpoint_sets_setting(temp_db):
    from app.models import LearnedVisibilityRequest
    from app.routers.settings import set_learned_visibility
    await set_learned_visibility(LearnedVisibilityRequest(public=False))
    assert config.get_setting("learned_phrases_public") == "false"
    await set_learned_visibility(LearnedVisibilityRequest(public=True))
    assert config.get_setting("learned_phrases_public") == "true"
