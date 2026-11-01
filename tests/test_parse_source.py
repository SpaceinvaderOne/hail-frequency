import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import config, database
from app.adapters.base import AmbiguousMatchError
from app.models import HistoryEntry, Intent, VoiceResponse
from app.services.intent import route_intent
from app.services.parse_chain import ParseOutcome


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()
    return database.DB_PATH


def test_history_has_parse_source_column(temp_db):
    cols = {r[1] for r in sqlite3.connect(temp_db).execute(
        "PRAGMA table_info(command_history)")}
    assert "parse_source" in cols


def test_migrate_adds_parse_source_column(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.db"
    db = sqlite3.connect(legacy)
    db.execute("""CREATE TABLE command_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
        transcript TEXT, intent_json TEXT, action TEXT, result TEXT,
        success INTEGER DEFAULT 1)""")
    db.commit(); db.close()
    monkeypatch.setattr(database, "DB_PATH", str(legacy))
    database.init_db()
    cols = {r[1] for r in sqlite3.connect(legacy).execute(
        "PRAGMA table_info(command_history)")}
    assert "parse_source" in cols


def test_log_command_records_parse_source(temp_db):
    config.log_command("play x", None, "play_search", "Playing X.", True,
                       parse_source="fallback")
    row = config.get_command_history(limit=1)[0]
    assert row["parse_source"] == "fallback"


def test_models_carry_parse_source():
    HistoryEntry(id=1, timestamp="t", success=True, parse_source="fast")
    v = VoiceResponse(transcript="t", result="r", success=True,
                      parse_source="ollama", degraded=None)
    assert v.parse_source == "ollama"


async def test_unknown_action_is_failure():
    """An "unknown" verdict is an honest failure, not a successful command."""
    with pytest.raises(ValueError, match="^m$"):
        await route_intent(Intent(action="unknown", message="m"), adapter=None)


def test_cards_logged_as_success(temp_db, monkeypatch):
    """Disambiguation cards are a valid answer to the spoken command: the
    command succeeded, and history records it that way."""
    from app.main import app
    from app.routers import voice

    async def fake_transcribe(self, audio_bytes, content_type):
        return "what is new"

    async def fake_resolve(transcript, cfg):
        return ParseOutcome(
            raw_intent={"action": "recently_added"},
            intent=Intent(action="recently_added"),
            source="fast",
            degraded_reason=None,
        )

    async def fake_route_intent(intent, adapter):
        raise AmbiguousMatchError("Here are 2 items:", [
            {"title": "A", "id": "1", "type": "Movie"},
            {"title": "B", "id": "2", "type": "Movie"},
        ])

    monkeypatch.setattr(voice.SpeachesClient, "transcribe", fake_transcribe)
    monkeypatch.setattr(voice, "resolve", fake_resolve)
    monkeypatch.setattr(voice, "route_intent", fake_route_intent)

    client = TestClient(app)
    r = client.post(
        "/api/setup/auth", json={"username": "admin", "password": "password123"}
    )
    assert r.status_code == 200
    config.set_setting("speaches_url", "http://x")
    config.set_setting("speaches_model", "whisper")
    config.add_media_server(
        name="Stub", server_type="emby", url="http://127.0.0.1:9", api_key="k"
    )
    config.set_setting("setup_complete", "true")
    r = client.post(
        "/api/auth/login", json={"username": "admin", "password": "password123"}
    )
    assert r.status_code == 200

    r = client.post(
        "/api/voice", files={"audio": ("a.wav", b"RIFF....", "audio/wav")}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert len(body["disambiguation"]) == 2

    r = client.get("/api/history?limit=1")
    assert r.status_code == 200
    assert r.json()[0]["success"] is True
