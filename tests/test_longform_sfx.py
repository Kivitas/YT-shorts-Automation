"""Tests for pure-stdlib longform sound effect synthesis."""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from autoshorts.longform.sfx import (
    SAMPLE_RATE,
    mix_events,
    synth_alarm,
    synth_clock_ticks,
    synth_punch,
    synth_whisper,
)


def _read(path: Path) -> tuple[int, int, int, bytes]:
    with wave.open(str(path), "rb") as w:
        return (
            w.getframerate(),
            w.getnchannels(),
            w.getnframes(),
            w.readframes(w.getnframes()),
        )


def _assert_wav(path: Path, duration: float) -> None:
    rate, channels, frames, _ = _read(path)
    assert rate == 48000
    assert channels == 1
    assert frames / rate == pytest.approx(duration, rel=0.01)


def test_alarm_format_and_duration(tmp_path: Path) -> None:
    out = synth_alarm(tmp_path / "alarm.wav", duration=0.6)
    _assert_wav(out, 0.6)


def test_clock_ticks_format_and_duration(tmp_path: Path) -> None:
    out = synth_clock_ticks(tmp_path / "ticks.wav", duration=2.0, rate_hz=4.0)
    _assert_wav(out, 2.0)


def test_punch_format_and_duration(tmp_path: Path) -> None:
    out = synth_punch(tmp_path / "punch.wav", duration=0.35)
    _assert_wav(out, 0.35)


def test_whisper_format_and_duration(tmp_path: Path) -> None:
    out = synth_whisper(tmp_path / "whisper.wav", duration=1.2)
    _assert_wav(out, 1.2)


def test_synthesis_is_deterministic(tmp_path: Path) -> None:
    a = synth_whisper(tmp_path / "a.wav")
    b = synth_whisper(tmp_path / "b.wav")
    assert _read(a)[3] == _read(b)[3]
    p1 = synth_punch(tmp_path / "p1.wav")
    p2 = synth_punch(tmp_path / "p2.wav")
    assert _read(p1)[3] == _read(p2)[3]


def test_whisper_is_quiet(tmp_path: Path) -> None:
    import struct

    out = synth_whisper(tmp_path / "w.wav")
    _, _, frames, raw = _read(out)
    samples = struct.unpack(f"<{frames}h", raw)
    peak = max(abs(s) for s in samples) / 32767
    assert 0.2 <= peak <= 0.3


def test_mix_events_length_and_skip(tmp_path: Path) -> None:
    alarm = synth_alarm(tmp_path / "alarm.wav", duration=0.6)
    punch = synth_punch(tmp_path / "punch.wav", duration=0.35)
    out = tmp_path / "mix.wav"
    mix_events(
        [(alarm, 0.0), (punch, 0.5), (punch, 2.0)],
        out,
        total_seconds=1.5,
    )
    rate, channels, frames, _ = _read(out)
    assert rate == SAMPLE_RATE
    assert channels == 1
    assert frames == round(1.5 * SAMPLE_RATE)


def test_mix_events_clamps_to_int16(tmp_path: Path) -> None:
    import struct

    alarm = synth_alarm(tmp_path / "alarm.wav", duration=0.2)
    out = tmp_path / "loud.wav"
    mix_events([(alarm, 0.0)] * 4, out, total_seconds=0.5)
    _, _, frames, raw = _read(out)
    samples = struct.unpack(f"<{frames}h", raw)
    assert max(abs(s) for s in samples) <= 32767
