"""Tests for the Kokoro fallback TTS (no network, no real model)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoshorts.media import kokoro_fallback as kf
from autoshorts.research_agent import moneyprinter as mp


def test_unavailable_when_binary_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(kf.shutil, "which", lambda _n: None)
    monkeypatch.setattr(kf, "_UV_TOOL_PATH", tmp_path / "nope.exe")
    assert kf.kokoro_available() is False


def test_available_with_binary_and_models(monkeypatch, tmp_path):
    for n in kf.MODEL_FILES:
        (tmp_path / n).write_bytes(b"x")
    monkeypatch.setattr(kf, "_model_dir", lambda: tmp_path)
    monkeypatch.setattr(kf.shutil, "which", lambda _n: "kokoro-tts")
    assert kf.kokoro_available() is True
    (tmp_path / kf.MODEL_FILES[0]).unlink()
    assert kf.kokoro_available() is False


def test_synthesize_builds_command(monkeypatch, tmp_path):
    monkeypatch.setattr(kf, "_find_binary", lambda: "kokoro-tts")
    monkeypatch.setattr(kf, "_model_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(kf, "get_work_dir", lambda *a: tmp_path / "work")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        Path(cmd[2]).write_bytes(b"RIFF")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(kf.subprocess, "run", fake_run)
    out = kf.synthesize_kokoro("hello", "en-US-GuyNeural", tmp_path / "o.wav", speed=1.1)
    assert out.is_file()
    cmd = seen["cmd"]
    assert cmd[cmd.index("--voice") + 1] == "am_adam"
    assert cmd[cmd.index("--speed") + 1] == "1.1"
    assert cmd[cmd.index("--format") + 1] == "wav"
    assert seen["kw"]["cwd"] == str(tmp_path / "models")
    assert seen["kw"]["timeout"] == 300


def test_synthesize_failure_and_empty_output(monkeypatch, tmp_path):
    monkeypatch.setattr(kf, "_find_binary", lambda: "kokoro-tts")
    monkeypatch.setattr(kf, "_model_dir", lambda: tmp_path)
    monkeypatch.setattr(kf, "get_work_dir", lambda *a: tmp_path / "work")
    monkeypatch.setattr(
        kf.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stderr="boom")
    )
    with pytest.raises(RuntimeError, match="boom"):
        kf.synthesize_kokoro("x", "unknown", tmp_path / "o.wav")
    monkeypatch.setattr(
        kf.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stderr="")
    )
    with pytest.raises(RuntimeError, match="no output"):
        kf.synthesize_kokoro("x", "unknown", tmp_path / "o.wav")


def test_voice_mapping():
    assert kf.map_voice("en-US-AriaNeural") == "af_sarah"
    assert kf.map_voice("en-US-ChristopherNeural") == "am_michael"
    assert kf.map_voice("fr-FR-Whatever") == "af_sarah"


async def _edge_boom(*a, **k):
    raise ConnectionError("offline")


def test_neural_tts_uses_kokoro_when_edge_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(mp, "_async_synthesize_edge_tts", _edge_boom)
    monkeypatch.setattr(kf, "kokoro_available", lambda: True)

    def fake_synth(text, voice, out, speed=1.0):
        out.write_bytes(b"RIFF")
        return out

    monkeypatch.setattr(kf, "synthesize_kokoro", fake_synth)
    words = [{"word": "hi", "start": 0.0, "end": 0.5, "probability": 1.0}]
    from autoshorts.media import compositor

    monkeypatch.setattr(
        compositor.FasterWhisperTranscriber, "align_words", lambda self, p, text=None: words
    )
    wp = tmp_path / "w.json"
    audio, got, dur = mp.synthesize_neural_tts("hi", "en-US-AriaNeural", tmp_path / "a.mp3", wp)
    assert audio.suffix == ".wav" and got == words and dur == 0.5
    assert json.loads(wp.read_text(encoding="utf-8")) == words


def test_neural_tts_offline_when_both_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(mp, "_async_synthesize_edge_tts", _edge_boom)
    monkeypatch.setattr(kf, "kokoro_available", lambda: True)

    def bad(*a, **k):
        raise RuntimeError("kokoro dead")

    monkeypatch.setattr(kf, "synthesize_kokoro", bad)
    sentinel = (tmp_path / "off.wav", [], 1.0)
    monkeypatch.setattr(mp, "_fallback_synthesize_audio", lambda *a, **k: sentinel)
    assert mp.synthesize_neural_tts("hi", "v", tmp_path / "a.mp3", tmp_path / "w.json") == sentinel
