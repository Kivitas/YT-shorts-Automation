import sys
import types
import wave

import pytest

from autoshorts.longform import tts
from autoshorts.longform.types import LongformScript, Section


def _script():
    return LongformScript(
        topic="discipline",
        title="Discipline",
        hook="Hi.",
        sections=[Section("A", "one two three"), Section("B", "four five six")],
        provider="api",
    )


def _install_fakes(monkeypatch, edge_fails=True, kokoro_fails=False):
    async def fake_edge(text, voice, audio, words):
        if edge_fails:
            raise ConnectionError("offline")
        audio.write_bytes(b"mp3")
        return audio, [{"word": "x", "start": 0.0, "end": 1.0}], 1.0

    from autoshorts.research_agent import moneyprinter

    monkeypatch.setattr(moneyprinter, "_async_synthesize_edge_tts", fake_edge)

    calls = []

    def fake_kokoro(text, voice, out):
        calls.append(voice)
        if kokoro_fails:
            raise RuntimeError("kokoro missing")
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 16000)
        return out

    mod = types.ModuleType("autoshorts.media.kokoro_fallback")
    mod.synthesize_kokoro = fake_kokoro
    monkeypatch.setitem(sys.modules, "autoshorts.media.kokoro_fallback", mod)

    from autoshorts.media import compositor

    class FakeAligner:
        def align_words(self, path, text=None):
            return [{"word": "a", "start": 0.0, "end": 0.5}]

    monkeypatch.setattr(compositor, "FasterWhisperTranscriber", FakeAligner)
    return calls


def test_kokoro_used_when_edge_fails(monkeypatch, tmp_path):
    calls = _install_fakes(monkeypatch)
    out = tts.synthesize_sections(_script(), tmp_path)
    assert [p.audio_path.name for p in out] == ["section_00.wav", "section_01.wav"]
    assert calls == ["en-US-ChristopherNeural", "en-US-ChristopherNeural"]
    assert out[0].words[0]["word"] == "a"
    assert tts.total_duration(out) == pytest.approx(4.0)


def test_secondary_speaker_switches_voice(monkeypatch, tmp_path):
    calls = _install_fakes(monkeypatch)
    s = LongformScript(
        topic="parody",
        title="Parody",
        hook="Hi.",
        sections=[
            Section("A", "one two", speaker="narrator"),
            Section("B", "three four", speaker="skeptic"),
        ],
        provider="api",
        genre="parody",
    )
    tts.synthesize_sections(s, tmp_path)
    assert calls == ["en-US-ChristopherNeural", "en-US-AriaNeural"]


def test_edge_used_when_available(monkeypatch, tmp_path):
    _install_fakes(monkeypatch, edge_fails=False)
    out = tts.synthesize_sections(_script(), tmp_path, voice="en-US-AriaNeural")
    assert out[0].audio_path.name == "section_00.mp3"
    assert tts.total_duration(out) == pytest.approx(2.0)


def test_both_fail_raises(monkeypatch, tmp_path):
    _install_fakes(monkeypatch, kokoro_fails=True)
    with pytest.raises(RuntimeError, match="No TTS available"):
        tts.synthesize_sections(_script(), tmp_path)
