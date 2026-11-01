import pytest

from app.services.ollama import (
    READ_TIMEOUT_CPU,
    READ_TIMEOUT_GPU,
    OllamaClient,
    engine_from_ps,
    read_timeout_for,
)


@pytest.mark.parametrize("models,model,expected", [
    ([{"name": "gemma3:12b", "size_vram": 9_000_000_000}], "gemma3:12b", "gpu"),
    ([{"name": "gemma3:12b", "size_vram": 0}], "gemma3:12b", "cpu"),
    ([{"model": "gemma3:12b", "size_vram": 123}], "gemma3:12b", "gpu"),  # match on "model" key too
    ([{"name": "other:3b", "size_vram": 5}], "gemma3:12b", None),        # configured model not loaded
    ([], "gemma3:12b", None),
    ([{"name": "gemma3:12b"}], "gemma3:12b", "cpu"),                     # size_vram absent -> 0 -> cpu
])
def test_engine_from_ps(models, model, expected):
    assert engine_from_ps(models, model) == expected


@pytest.mark.parametrize("engine,expected", [
    ("gpu", READ_TIMEOUT_GPU),
    ("unknown", READ_TIMEOUT_GPU),
    (None, READ_TIMEOUT_GPU),
    ("cpu", READ_TIMEOUT_CPU),
])
def test_read_timeout_for(engine, expected):
    assert read_timeout_for(engine) == expected


def test_client_stores_read_timeout():
    c = OllamaClient("http://x:11434", "m", read_timeout=READ_TIMEOUT_CPU)
    assert c.read_timeout == READ_TIMEOUT_CPU


async def test_detect_engine_maps_ps(monkeypatch):
    c = OllamaClient("http://x:11434", "gemma3:12b")

    class FakeResp:
        def raise_for_status(self):
            pass
        def json(self):
            return {"models": [{"name": "gemma3:12b", "size_vram": 0}]}

    async def fake_get(url):
        assert url.endswith("/api/ps")
        return FakeResp()

    monkeypatch.setattr(c.client, "get", fake_get)
    assert await c.detect_engine() == "cpu"


async def test_detect_engine_swallows_errors(monkeypatch):
    c = OllamaClient("http://x:11434", "gemma3:12b")

    async def boom(url):
        raise RuntimeError("down")

    monkeypatch.setattr(c.client, "get", boom)
    assert await c.detect_engine() is None
