"""Phase 1 voice tuning: Edge rates, story mood rates, Kokoro speed, narration concat graph.

Build-only checks: no synthesis, no network, no ffmpeg run.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest

from autoshorts.longform import render, tts
from autoshorts.media import kokoro_fallback
from autoshorts.research_agent import moneyprinter, story_shorts


def test_shorts_edge_rate_constant_and_defaults() -> None:
    assert moneyprinter.SHORTS_EDGE_RATE == "+12%"
    sig = inspect.signature(moneyprinter.synthesize_neural_tts)
    assert sig.parameters["rate"].default == "+12%"
    sig_async = inspect.signature(moneyprinter._async_synthesize_edge_tts)
    assert sig_async.parameters["rate"].default == "+12%"


def test_longform_edge_rate_constant() -> None:
    assert tts.LONGFORM_EDGE_RATE == "+10%"
    # Voices are unchanged by the rate tuning.
    assert tts.NARRATOR_VOICE == "en-US-ChristopherNeural"
    assert tts.SECONDARY_VOICE == "en-US-AriaNeural"


def test_longform_section_passes_rate_to_edge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    async def fake_edge(text, voice, audio, words_path, rate="+0%", pitch="+0Hz"):
        captured["rate"] = rate
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"x" * 16)
        return audio, [], 1.0

    monkeypatch.setattr(moneyprinter, "_async_synthesize_edge_tts", fake_edge)
    words, duration = tts._edge(
        "hello",
        "en-US-AriaNeural",
        tmp_path / "s.mp3",
        tmp_path / "w.json",
        rate=tts.LONGFORM_EDGE_RATE,
    )
    assert captured["rate"] == "+10%"
    assert duration == 1.0


@pytest.mark.parametrize("mood", ["sad", "heartbreak", "scary", "emotional"])
def test_story_drag_moods_raised_to_minus_three(mood: str) -> None:
    assert story_shorts.MOOD_VOICE_PROFILES[mood]["rate"] == "-3%"


@pytest.mark.parametrize(
    ("mood", "rate"),
    [("angry", "+15%"), ("funny", "+25%"), ("advice", "+10%"), ("wholesome", "+0%")],
)
def test_other_story_moods_unchanged(mood: str, rate: str) -> None:
    assert story_shorts.MOOD_VOICE_PROFILES[mood]["rate"] == rate


def test_kokoro_default_speed() -> None:
    assert kokoro_fallback.KOKORO_SPEED == 1.1
    sig = inspect.signature(kokoro_fallback.synthesize_kokoro)
    assert sig.parameters["speed"].default == 1.1


def test_narration_graph_three_sections() -> None:
    graph = render._narration_filter_graph(3)
    # 3 sections + 2 gaps = 5 concat segments
    assert "concat=n=5:v=0:a=1[cat]" in graph
    assert graph.count("anullsrc=") == 2
    assert f"atrim=0:{render.SECTION_GAP_SECONDS:.2f}[g0]" in graph
    assert "[g1]" in graph
    # Sections are not trimmed: trimming would shift the word timings used for captions.
    assert "silenceremove=" not in graph
    assert "areverse" not in graph
    assert graph.endswith(f";[cat]{render.NARRATION_COMPRESSOR}[outc]")
    assert "[outa]" not in graph


def test_narration_graph_single_section_has_no_gap() -> None:
    graph = render._narration_filter_graph(1)
    assert "concat=n=1:v=0:a=1[cat]" in graph
    assert "anullsrc" not in graph
    assert graph.endswith("[outc]")


def test_narration_graph_compressor_settings() -> None:
    assert render.NARRATION_COMPRESSOR == "acompressor=threshold=0.1:ratio=3:attack=20:release=250"


def test_narration_graph_rejects_empty() -> None:
    with pytest.raises(ValueError):
        render._narration_filter_graph(0)


def test_robotic_fallback_logs_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def failing_edge(*args, **kwargs):
        raise ConnectionError("no network")

    def fake_fallback(text, audio_path, words_path):
        return audio_path, [], 1.0

    monkeypatch.setattr(moneyprinter, "_try_eachlabs_tts", lambda *a, **k: None)
    monkeypatch.setattr(moneyprinter, "_async_synthesize_edge_tts", failing_edge)
    monkeypatch.setattr(moneyprinter, "_try_kokoro_tts", lambda *a, **k: None)
    monkeypatch.setattr(moneyprinter, "_fallback_synthesize_audio", fake_fallback)

    with caplog.at_level(logging.ERROR, logger=moneyprinter.logger.name):
        moneyprinter.synthesize_neural_tts(
            "hello world", "en-US-AriaNeural", tmp_path / "v.mp3", tmp_path / "w.json"
        )

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "expected an ERROR record when the robotic fallback is used"
    message = errors[0].getMessage()
    assert "ROBOTIC FALLBACK VOICE" in message
    assert "no network" in message
