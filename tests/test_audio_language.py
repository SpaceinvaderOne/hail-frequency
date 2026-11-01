"""Switching the audio track of what is playing by spoken language."""
import pytest

from app.adapters.emby import EmbyAdapter
from app.adapters.jellyfin import JellyfinAdapter
from app.services.intent import try_fast_parse, validate_intent

FRENCH = {"Index": 1, "Type": "Audio", "Language": "fra",
          "DisplayTitle": "French - Dolby Digital+ - 5.1 - Default", "IsDefault": True}
ENGLISH = {"Index": 2, "Type": "Audio", "Language": "eng",
           "DisplayTitle": "VO - English - Dolby Digital Plus - 5.1"}
ENGLISH_AD = {"Index": 3, "Type": "Audio", "Language": "eng",
              "DisplayTitle": "Descriptive (VO) - English - Dolby Digital+ - 5.1"}
SUBS = {"Index": 4, "Type": "Subtitle", "Language": "eng"}


def _session(streams, supported=None):
    s = {"Id": "s1", "DeviceName": "Lounge TV", "Client": "Emby TV",
         "SupportsRemoteControl": True,
         "NowPlayingItem": {"Id": "i1", "Name": "Film", "MediaStreams": streams}}
    if supported is not None:
        s["SupportedCommands"] = supported
    return s


def _wire(adapter, session):
    sent = []

    async def pick(device=None):
        return session

    async def send(session_id, name, args=None):
        sent.append((session_id, name, args))

    adapter._pick_session_full = pick
    adapter._send_command = send
    return sent


@pytest.mark.parametrize("phrase,lang", [
    ("Change language to English.", "english"),
    ("switch the audio to french", "french"),
    ("French audio", "french"),
    ("play it in japanese", "japanese"),
    ("english audio track", "english"),
    ("switch to the english audio", "english"),
    ("put the audio track into german please", "german"),
])
def test_fast_path_audio_language(phrase, lang):
    intent = try_fast_parse(phrase)
    assert intent is not None
    assert intent.action == "audio_language" and intent.language == lang


@pytest.mark.parametrize("phrase,action", [
    ("english subtitles on", "subtitles_on"),
    ("turn on english subtitles", "subtitles_on"),
    ("play the english patient", "play_search"),
])
def test_audio_phrasing_does_not_steal_other_commands(phrase, action):
    intent = try_fast_parse(phrase)
    assert intent is None or intent.action == action


def test_validate_requires_a_language():
    assert isinstance(validate_intent({"action": "audio_language"}), str)
    ok = validate_intent({"action": "audio_language", "language": "english"})
    assert not isinstance(ok, str) and ok.language == "english"


async def test_prefers_plain_track_over_descriptive_in_same_language():
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    sent = _wire(a, _session([FRENCH, ENGLISH, ENGLISH_AD, SUBS]))
    try:
        assert await a.set_audio_language("english") == "Audio — English."
        assert sent == [("s1", "SetAudioStreamIndex", {"Index": "2"})]
    finally:
        await a.close()


async def test_default_flag_breaks_ties_and_jellyfin_shares_the_code():
    a = JellyfinAdapter("http://x", "k", "u", device_aliases=[])
    other_french = {"Index": 5, "Type": "Audio", "Language": "fra",
                    "DisplayTitle": "French - AAC - Stereo"}
    sent = _wire(a, _session([other_french, FRENCH, ENGLISH]))
    try:
        assert await a.set_audio_language("french") == "Audio — French."
        assert sent[0][1:] == ("SetAudioStreamIndex", {"Index": "1"})
    finally:
        await a.close()


async def test_missing_language_lists_what_is_available():
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    sent = _wire(a, _session([FRENCH, ENGLISH, ENGLISH_AD]))
    try:
        with pytest.raises(ValueError, match="No german audio here. Available: English, French."):
            await a.set_audio_language("german")
        assert sent == []
    finally:
        await a.close()


async def test_nothing_playing_and_unsupported_client():
    a = EmbyAdapter("http://x", "k", "u", device_aliases=[])
    idle = {"Id": "s1", "DeviceName": "Lounge TV", "SupportsRemoteControl": True}
    sent = _wire(a, idle)
    try:
        assert await a.set_audio_language("english") == "Nothing is currently playing."
        sent2 = _wire(a, _session([FRENCH, ENGLISH], supported=["Pause", "Unpause"]))
        with pytest.raises(ValueError, match="doesn't support switching audio tracks"):
            await a.set_audio_language("english")
        assert sent == [] and sent2 == []
    finally:
        await a.close()
