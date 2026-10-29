import subprocess
import wave
from pathlib import Path

import pytest

from autoshorts.longform import render
from autoshorts.longform.types import Section, SectionAudio
from autoshorts.media.encoder_probe import get_ffmpeg_path


def _wav(path: Path, rate: int, seconds: float = 0.5) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def test_concat_audio_handles_mixed_sample_rates(tmp_path):
    secs = [
        SectionAudio(Section("a", "x"), _wav(tmp_path / "a.wav", 24000), [], 0.5),
        SectionAudio(Section("b", "y"), _wav(tmp_path / "b.wav", 16000), [], 0.5),
    ]
    out = render._concat_audio(secs, tmp_path)
    assert out.is_file() and out.stat().st_size > 0
    proc = subprocess.run(
        [get_ffmpeg_path(), "-hide_banner", "-i", str(out)], capture_output=True, text=True
    )
    assert "Duration: 00:00:01" in proc.stderr or "Duration: 00:00:00.9" in proc.stderr


def test_concat_audio_rejects_empty(tmp_path):
    with pytest.raises(ValueError):
        render._concat_audio([], tmp_path)
