import json
from types import SimpleNamespace

import pytest

from autoshorts.longform import script as script_mod
from autoshorts.longform.script import MAX_WORDS, build_script, description_for
from autoshorts.longform.topics import (
    TOPIC_KEYS,
    TOPICS,
    hashtags_for,
    normalize_topic,
    title_for,
)
from autoshorts.longform.types import MAX_VIDEO_SECONDS


def test_normalize_topic():
    assert normalize_topic("Discipline") == "discipline"
    assert normalize_topic("  Self-Improvement ") == "self_improvement"
    assert normalize_topic("self improvement") == "self_improvement"
    with pytest.raises(ValueError):
        normalize_topic("cooking")


@pytest.mark.parametrize("topic", TOPIC_KEYS)
def test_title_and_hashtags(topic):
    spec = TOPICS[topic]
    for subject in ("", "why most people quit", "x" * 200):
        title = title_for(topic, subject)
        assert 0 < len(title) <= 60
        assert any(title.lower().startswith(k.lower()) for k in spec.title_keywords)
    tags = hashtags_for(topic)
    assert 5 <= len(tags) <= 8
    # Hashtags come from the topic's hand-picked list, which is kept on-topic.
    assert all(tag in spec.hashtags for tag in tags)


@pytest.mark.parametrize("topic", TOPIC_KEYS)
def test_curated_script_valid(topic):
    s = build_script(topic, provider="api", config=None)
    words = len(s.narration.split())
    assert 100 < words <= MAX_WORDS
    assert words / 2.6 <= MAX_VIDEO_SECONDS
    assert 4 <= len(s.sections) <= 6
    assert all(x.heading and x.narration for x in s.sections)
    assert spec_note(topic) in description_for(s)


def spec_note(topic):
    return TOPICS[topic].safety_note


def test_safety_notes():
    assert "doctor" in TOPICS["testosterone"].safety_note.lower()
    assert "not financial advice" in TOPICS["money"].safety_note.lower()
    for topic in ("testosterone", "money"):
        text = build_script(topic).narration.lower()
        assert "steroid" not in text and "guaranteed income" not in text


def test_validation_errors():
    with pytest.raises(ValueError):
        build_script("discipline", minutes=25)
    with pytest.raises(ValueError):
        build_script("discipline", minutes=0.5)
    with pytest.raises(ValueError):
        build_script("discipline", provider="cloud")
    with pytest.raises(ValueError):
        build_script("nope")
    with pytest.raises(ValueError):
        build_script("   ")


class _FakeLLM:
    def __init__(self, raw):
        self.raw = raw

    def complete(self, prompt):
        assert "Safety rules" in prompt
        return self.raw


def _good_json():
    narr = " ".join(["word"] * 80)
    return json.dumps(
        {
            "title": "Discipline: build it daily",
            "hook": "Start now.",
            "sections": [{"heading": f"Part {i}", "narration": narr} for i in range(4)],
        }
    )


def test_fake_provider_used(monkeypatch):
    raw = "```json\n" + _good_json() + "\n```"
    monkeypatch.setattr(script_mod, "get_provider", lambda name, settings: _FakeLLM(raw))
    s = build_script("discipline", config=SimpleNamespace())
    assert s.title == "Discipline: build it daily"
    assert s.hook == "Start now."
    assert len(s.sections) == 4
    assert s.sections[0].narration.startswith("Start now.")


@pytest.mark.parametrize("raw", ["not json at all", json.dumps({"hook": "x", "sections": []})])
def test_bad_output_falls_back(monkeypatch, raw):
    monkeypatch.setattr(script_mod, "get_provider", lambda name, settings: _FakeLLM(raw))
    s = build_script("money", config=SimpleNamespace())
    assert s.hook == TOPICS["money"].curated_hook


def test_too_long_falls_back(monkeypatch):
    data = json.loads(_good_json())
    for sec in data["sections"]:
        sec["narration"] = " ".join(["word"] * 300)
    monkeypatch.setattr(
        script_mod, "get_provider", lambda name, settings: _FakeLLM(json.dumps(data))
    )
    s = build_script("science", config=SimpleNamespace())
    assert s.hook == TOPICS["science"].curated_hook


def test_local_without_model_uses_curated(monkeypatch, tmp_path):
    def boom(**kw):
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(script_mod, "get_provider", boom)
    cfg = SimpleNamespace(llm=SimpleNamespace(local_model_path=str(tmp_path / "missing.gguf")))
    s = build_script("discipline", provider="local", config=cfg)
    assert s.provider == "local"
    assert s.hook == TOPICS["discipline"].curated_hook
