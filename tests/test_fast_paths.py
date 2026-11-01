"""Fast-path parsing: exact phrases, templated commands, and phrases that
must fall through to the LLM."""
import pytest

from app.services.intent import try_fast_parse


@pytest.mark.parametrize("phrase,action", [
    ("pause", "pause"),
    ("Stop!", "stop"),
    ("volume up", "volume_up"),
    ("turn it down", "volume_down"),
    ("mute", "mute"),
    ("next episode", "next_episode"),
    ("what's playing", "now_playing"),
    ("mark this as watched", "mark_watched"),
    ("help", "help"),
])
def test_dict_phrases(phrase, action):
    intent = try_fast_parse(phrase)
    assert intent is not None and intent.action == action


def test_play_episode_template():
    intent = try_fast_parse("play Foundation season 2 episode 3")
    assert intent.action == "play_episode"
    assert intent.series == "foundation"
    assert intent.season == 2 and intent.episode == 3


def test_play_next_template():
    intent = try_fast_parse("play the next episode of Severance")
    assert intent.action == "play_next"
    assert intent.series == "severance"


def test_shuffle_series():
    intent = try_fast_parse("shuffle Frasier")
    assert intent.action == "shuffle"
    assert intent.series == "frasier"


def test_random_movie_genre():
    intent = try_fast_parse("play a random sci-fi movie")
    assert intent.action == "shuffle"
    assert intent.type == "movie"


@pytest.mark.parametrize("phrase", [
    "play star wars rogue one",   # bare movie title → LLM's job
    "play breaking bad",          # bare series title → LLM's job too
    "play that tom hanks volleyball movie",
    "volume up a bit love",
])
def test_bare_titles_and_fuzz_miss(phrase):
    assert try_fast_parse(phrase) is None


def test_returned_intent_is_a_copy():
    a = try_fast_parse("pause")
    a.device = "mutated!"
    b = try_fast_parse("pause")
    assert b.device is None
