"""Settings endpoints: service URLs and model discovery."""
import pytest

from app import config, database
from app.models import OllamaModelsRequest, SetupMediaServerRequest


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    database.init_db()


async def test_ollama_models_accepts_body_url(temp_db, monkeypatch):
    """With no stored URL (AI tier disabled), the model list is fetched from
    the URL typed into the edit form."""
    import app.routers.settings as settings_router

    class FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"models": [{"name": "gemma3:12b", "size": 1}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            assert url.startswith("http://ollama-host:11434")
            return FakeResp()

    monkeypatch.setattr(settings_router.httpx, "AsyncClient", FakeClient)
    out = await settings_router.list_ollama_models(
        OllamaModelsRequest(url="http://ollama-host:11434"))
    assert out["models"][0]["name"] == "gemma3:12b"


async def test_ollama_models_unconfigured_is_400(temp_db):
    import app.routers.settings as settings_router
    resp = await settings_router.list_ollama_models(OllamaModelsRequest())
    assert getattr(resp, "status_code", 200) == 400


async def test_media_server_url_must_have_scheme(temp_db):
    import app.routers.settings as settings_router
    bad = SetupMediaServerRequest(
        name="x", type="emby", url="media-host:8096", api_key="k")
    assert (await settings_router.add_server(bad)).status_code == 400
    assert (await settings_router.test_media_server_connection(bad)).status_code == 400
    assert config.get_media_servers() == []
