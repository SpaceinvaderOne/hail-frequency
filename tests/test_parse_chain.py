import pytest

from app.services import parse_chain
from app.services.parse_chain import OllamaDown, ParseOutcome, resolve

CFG = {
    "ollama_url": "http://ollama:11434",
    "ollama_model": "qwen3",
    "ollama_keep_alive_enabled": "false",
    "ollama_keep_alive_value": None,
}
NO_OLLAMA_CFG = {**CFG, "ollama_url": None, "ollama_model": None}


class FakeOllama:
    """Stands in for OllamaClient. Behaviour driven by `script`."""
    def __init__(self, url, model, keep_alive=None, read_timeout=None):
        self.calls = FakeOllama.instances.append(self) or 0

    instances: list = []
    script = staticmethod(lambda transcript: {"action": "unknown"})

    def build_system_prompt(self, servers, devices):
        return "PROMPT"

    async def parse_intent(self, transcript, system_prompt):
        return FakeOllama.script(transcript)

    async def detect_engine(self):
        return None

    async def close(self):
        pass


# Snapshot defaults so the autouse fixture can undo per-test class-level
# overrides (tests assign FakeOllama.parse_intent/script directly).
FakeOllama._default_parse_intent = FakeOllama.parse_intent
FakeOllama._default_script = FakeOllama.script


@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    FakeOllama.instances = []
    FakeOllama.parse_intent = FakeOllama._default_parse_intent
    FakeOllama.script = FakeOllama._default_script
    monkeypatch.setattr(parse_chain, "OllamaClient", FakeOllama)
    monkeypatch.setattr(parse_chain, "get_media_servers", lambda: [])
    monkeypatch.setattr(parse_chain, "get_all_device_aliases", lambda: [])


async def test_fast_tier_wins_without_touching_ollama():
    out = await resolve("pause", CFG)
    assert out.source == "fast"
    assert out.intent.action == "pause"
    assert out.raw_intent == out.intent.model_dump()
    assert out.degraded_reason is None
    assert FakeOllama.instances == []      # Ollama never constructed


async def test_ollama_tier_returns_raw_dict():
    FakeOllama.script = staticmethod(
        lambda t: {"action": "play_search", "query": "rogue one"})
    out = await resolve("play rogue one", CFG)
    assert out.source == "ollama"
    assert out.intent is None              # the voice endpoint validates raw_intent
    assert out.raw_intent == {"action": "play_search", "query": "rogue one"}


async def test_healthy_unknown_verdict_is_authoritative():
    FakeOllama.script = staticmethod(
        lambda t: {"action": "unknown", "message": "no idea"})
    out = await resolve("play gibberish", CFG)
    assert out.source == "ollama"          # NOT fallback — Ollama answered
    assert out.raw_intent["action"] == "unknown"


async def test_unconfigured_raises_ollamadown():
    # A non-play-shaped phrase: play-shaped ones fall back to play_search
    # instead of raising OllamaDown.
    with pytest.raises(OllamaDown) as exc:
        await resolve("what should I watch tonight", NO_OLLAMA_CFG)
    assert exc.value.reason == "unconfigured"


async def test_transport_error_raises_ollamadown():
    async def boom(self, transcript, system_prompt):
        import httpx
        raise httpx.ConnectError("refused")
    FakeOllama.parse_intent = boom
    # A non-play-shaped phrase: play-shaped ones fall back to play_search
    # instead of raising OllamaDown.
    with pytest.raises(OllamaDown) as exc:
        await resolve("what should I watch tonight", CFG)
    assert exc.value.reason in ("unreachable", "error")


import httpx


def _raising_fake(exc_factory, fail_times=99):
    state = {"n": 0}
    async def parse_intent(self, transcript, system_prompt):
        state["n"] += 1
        if state["n"] <= fail_times:
            raise exc_factory()
        return {"action": "play_search", "query": "ok"}
    return parse_intent, state


@pytest.mark.parametrize("exc,expected", [
    (lambda: httpx.ConnectError("refused"), "unreachable"),
    (lambda: httpx.ConnectTimeout("slow"), "unreachable"),
    (lambda: httpx.ReadTimeout("hung"), "timeout"),
    (lambda: httpx.HTTPStatusError(
        "404", request=httpx.Request("POST", "http://x"),
        response=httpx.Response(404, request=httpx.Request("POST", "http://x"))),
     "error"),
])
async def test_failure_classification(exc, expected):
    FakeOllama.parse_intent, _ = _raising_fake(exc)
    # A non-play-shaped phrase: play-shaped ones fall back to play_search
    # instead of raising OllamaDown.
    with pytest.raises(OllamaDown) as e:
        await resolve("what should I watch tonight", CFG)
    assert e.value.reason == expected


async def test_retry_recovers_transient_connect_failure():
    FakeOllama.parse_intent, state = _raising_fake(
        lambda: httpx.ConnectError("blip"), fail_times=1)
    # "play rogue one" is play-shaped; with fail_times=1 Ollama succeeds on
    # the second attempt so the fallback tier is never reached.
    out = await resolve("play rogue one", CFG)
    assert out.source == "ollama"
    assert state["n"] == 2                 # failed once, retried, succeeded


async def test_no_retry_on_http_status_error():
    def http_err():
        req = httpx.Request("POST", "http://x")
        return httpx.HTTPStatusError("500", request=req,
                                     response=httpx.Response(500, request=req))
    FakeOllama.parse_intent, state = _raising_fake(http_err)
    # A non-play-shaped phrase: play-shaped ones fall back to play_search
    # instead of raising OllamaDown.
    with pytest.raises(OllamaDown):
        await resolve("what should I watch tonight", CFG)
    assert state["n"] == 1                 # server answered — no retry


CPU_CFG = {**CFG, "ollama_engine": "cpu"}


async def test_cpu_engine_selects_long_read_timeout():
    from app.services.ollama import READ_TIMEOUT_CPU, READ_TIMEOUT_GPU
    seen = {}
    orig_init = FakeOllama.__init__

    def spy_init(self, url, model, keep_alive=None, read_timeout=READ_TIMEOUT_GPU):
        seen["read_timeout"] = read_timeout
        orig_init(self, url, model, keep_alive=keep_alive, read_timeout=read_timeout)

    FakeOllama.__init__ = spy_init
    try:
        await resolve("what should I watch tonight", CPU_CFG)
        assert seen["read_timeout"] == READ_TIMEOUT_CPU
        await resolve("what should I watch tonight", CFG)      # no engine key -> unknown
        assert seen["read_timeout"] == READ_TIMEOUT_GPU
    finally:
        FakeOllama.__init__ = orig_init


async def test_cpu_mode_read_timeout_not_retried():
    FakeOllama.parse_intent, state = _raising_fake(
        lambda: httpx.ReadTimeout("slow cpu"))
    with pytest.raises(OllamaDown) as e:
        await resolve("what should I watch tonight", CPU_CFG)
    assert e.value.reason == "timeout"
    assert state["n"] == 1                      # terminal on first attempt


async def test_gpu_mode_read_timeout_still_retried():
    FakeOllama.parse_intent, state = _raising_fake(
        lambda: httpx.ReadTimeout("blip"), fail_times=1)
    out = await resolve("what should I watch tonight", CFG)
    assert out.source == "ollama"
    assert state["n"] == 2                      # retried and recovered


async def test_cpu_mode_connect_error_still_retried():
    FakeOllama.parse_intent, state = _raising_fake(
        lambda: httpx.ConnectError("refused"), fail_times=1)
    out = await resolve("what should I watch tonight", CPU_CFG)
    assert out.source == "ollama"
    assert state["n"] == 2
