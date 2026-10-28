from __future__ import annotations

import json
import random
from types import SimpleNamespace

import pytest

from autoshorts.core.models import JobStatus
from autoshorts.research_agent import story_shorts as ss


def _story_json(words: int = 150, title: str = "T", hook: str = "H") -> str:
    return json.dumps({"title": title, "hook": hook, "narration": " ".join(["word"] * words)})


def test_normalize_mood():
    assert ss.normalize_mood("  SAD ") == "sad"
    assert ss.normalize_mood(None, random.Random(3)) == random.Random(3).choice(ss.STORY_MOODS)
    with pytest.raises(ValueError):
        ss.normalize_mood("joyful")


def test_normalize_provider():
    assert ss.normalize_provider(None) == "api"
    assert ss.normalize_provider(" Local ") == "local"
    with pytest.raises(ValueError):
        ss.normalize_provider("cloud")


def test_prompt_mentions_mood_and_range():
    p = ss.build_story_prompt("angry", "a parking dispute")
    assert "angry" in p and "between 135 and 170" in p and "parking dispute" in p
    assert "title, hook, narration" in p


@pytest.mark.parametrize("n", [134, 171])
def test_parse_rejects_bad_length(n):
    with pytest.raises(ValueError):
        ss.parse_story_response(_story_json(n), "sad", "api")


def test_parse_rejects_missing_title_and_bad_json():
    with pytest.raises(ValueError):
        ss.parse_story_response(_story_json(150, title=" "), "sad", "api")
    with pytest.raises(ValueError):
        ss.parse_story_response("not json", "sad", "api")


def test_parse_ok_with_fences_and_disclaimer():
    d = ss.parse_story_response("```json\n" + _story_json(150) + "\n```", "funny", "api")
    assert d.mood == "funny" and ss.FICTION_DISCLAIMER in d.description
    assert "Background: gameplay footage." in d.description


@pytest.mark.parametrize("mood", ss.STORY_MOODS)
def test_curated_and_config_none(mood):
    n = len(ss.CURATED_STORIES[mood]["narration"].split())
    assert 135 <= n <= 170
    d = ss.generate_story(mood, None, "api", None)
    assert d.narration == ss.CURATED_STORIES[mood]["narration"]
    assert ss.FICTION_DISCLAIMER in d.description


def test_api_path_uses_fake_provider(monkeypatch):
    class Fake:
        def complete(self, prompt):
            return _story_json(150, title="Fake")

    import autoshorts.core.providers as prov

    monkeypatch.setattr(prov, "get_provider", lambda name=None, settings=None, **k: Fake())
    d = ss.generate_story("sad", None, "api", SimpleNamespace())
    assert d.title == "Fake"


def test_api_failure_falls_back(monkeypatch):
    import autoshorts.core.providers as prov

    def boom(**k):
        raise RuntimeError("no key")

    monkeypatch.setattr(prov, "get_provider", boom)
    d = ss.generate_story("angry", None, "api", SimpleNamespace())
    assert d.title == ss.CURATED_STORIES["angry"]["title"]


def test_local_missing_model_falls_back(tmp_path):
    cfg = SimpleNamespace(llm=SimpleNamespace(local_model_path=str(tmp_path / "none.gguf")))
    d = ss.generate_story("funny", None, "local", cfg)
    assert d.title == ss.CURATED_STORIES["funny"]["title"]


def test_voice_api_duration_check(monkeypatch, tmp_path):
    seen = {}

    def fake_tts(text, voice, output_audio_path, output_words_path):
        seen["voice"] = voice
        return output_audio_path, [{"word": "a", "start": 0, "end": 1}], 30.0

    monkeypatch.setattr(ss, "synthesize_neural_tts", fake_tts)
    with pytest.raises(ValueError):
        ss.synthesize_story_voice("x", "funny", "api", tmp_path)
    assert seen["voice"] == "en-US-GuyNeural"


def test_create_story_job(monkeypatch, tmp_path):
    work = tmp_path / "work"
    monkeypatch.setattr(ss, "get_work_dir", lambda: work)
    monkeypatch.setattr(
        ss,
        "synthesize_story_voice",
        lambda narration, mood, provider, work_dir: (work_dir / "v.mp3", [{"word": "a"}], 60.0),
    )
    monkeypatch.setattr(ss, "pick_background_asset", lambda **k: (None, 0.0))
    monkeypatch.setattr(
        ss.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="")
    )
    calls = []
    pipe = SimpleNamespace(
        config=None,
        _run_subtitles_stage=lambda job, use_cached_words: calls.append("subs"),
        _run_compositing_stage=lambda job, background_category: calls.append("comp"),
    )
    saved = []
    state = SimpleNamespace(save_job=lambda j: saved.append(j.id))
    job = ss.create_story_job(mood="sad", topic=None, provider="api", pipe=pipe, state_mgr=state)
    assert job.id.startswith("story_")
    assert job.layout_mode == "full_center_crop"
    assert "Fictional story" in job.video_metadata.description
    assert job.status != JobStatus.SCHEDULED
    assert calls == ["subs", "comp"] and saved
    assert (work / job.id / "aligned_words.json").is_file()
