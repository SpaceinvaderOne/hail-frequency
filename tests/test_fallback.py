import httpx
import pytest

from app.services import parse_chain
from app.services.intent import fallback_play_search, normalize_transcript
from app.services.parse_chain import OllamaDown, resolve
from tests.test_parse_chain import CFG, NO_OLLAMA_CFG, FakeOllama, patch_env  # noqa: F401


def test_normalize_transcript():
    assert normalize_transcript("  Play STAR WARS: Rogue One!  ") == \
        "play star wars rogue one"


@pytest.mark.parametrize("phrase,query", [
    ("play star wars rogue one", "star wars rogue one"),
    ("Please play The Matrix", "the matrix"),
    ("watch Heat", "heat"),
    ("put on Life on Mars", "life on mars"),
    ("okay play dune please", "dune"),
])
def test_fallback_play_search_shapes(phrase, query):
    intent = fallback_play_search(phrase)
    assert intent is not None
    assert intent.action == "play_search"
    assert intent.query == query


@pytest.mark.parametrize("phrase", [
    "volume up a bit love",
    "what should I watch tonight",
    "play",                       # verb with no title
    "skip to the good part",
])
def test_fallback_rejects_non_play_shapes(phrase):
    assert fallback_play_search(phrase) is None


async def test_ollama_down_playshaped_falls_back():
    async def boom(self, t, s):
        raise httpx.ConnectError("refused")
    FakeOllama.parse_intent = boom
    out = await resolve("play star wars rogue one", CFG)
    assert out.source == "fallback"
    assert out.degraded_reason == "unreachable"
    assert out.intent.action == "play_search"
    assert out.intent.query == "star wars rogue one"


async def test_unconfigured_playshaped_falls_back():
    out = await resolve("play the matrix", NO_OLLAMA_CFG)
    assert out.source == "fallback"
    assert out.degraded_reason == "unconfigured"


async def test_ollama_down_non_playshaped_still_raises():
    async def boom(self, t, s):
        raise httpx.ConnectError("refused")
    FakeOllama.parse_intent = boom
    with pytest.raises(OllamaDown):
        await resolve("volume up a bit love", CFG)


async def test_healthy_ollama_never_falls_back():
    FakeOllama.script = staticmethod(lambda t: {"action": "unknown"})
    async def ok(self, t, s):
        return FakeOllama.script(t)
    FakeOllama.parse_intent = ok
    out = await resolve("play star wars rogue one", CFG)
    assert out.source == "ollama"          # fallback must not hijack
