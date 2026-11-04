"""Tests for pure-stdlib longform sound effect synthesis."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from autoshorts.longform import sfx
from autoshorts.longform.sfx import (
    SAMPLE_RATE,
    mix_events,
    synth_alarm,
    synth_clock_ticks,
    synth_glass_crack,
    synth_punch,
    synth_whisper,
    synth_whoosh,
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


def test_sfx_exports_include_new_synthesizers() -> None:
    assert "synth_glass_crack" in sfx.__all__
    assert "synth_whoosh" in sfx.__all__


def test_glass_crack_waveform_shape_and_duration() -> None:
    arr = synth_glass_crack(duration=0.35, sr=SAMPLE_RATE)
    assert isinstance(arr, np.ndarray)
    assert len(arr) == round(0.35 * SAMPLE_RATE)
    peak = float(np.max(np.abs(arr)))
    assert 0.8 <= peak <= 1.0
    assert float(np.max(np.abs(arr[: round(0.005 * SAMPLE_RATE)]))) > 0.3
    tail_rms = float(np.sqrt(np.mean(arr[round(0.25 * SAMPLE_RATE) :] ** 2)))
    head_rms = float(np.sqrt(np.mean(arr[: round(0.05 * SAMPLE_RATE)] ** 2)))
    assert tail_rms < head_rms


def test_glass_crack_deterministic_and_amplitude() -> None:
    a1 = synth_glass_crack(duration=0.25)
    a2 = synth_glass_crack(duration=0.25)
    assert np.array_equal(a1, a2)
    assert float(np.max(np.abs(a1))) <= 1.0


def test_glass_crack_writes_wav(tmp_path: Path) -> None:
    out = tmp_path / "glass.wav"
    res = synth_glass_crack(duration=0.35, path=out)
    assert res == out
    assert out.is_file()
    _assert_wav(out, 0.35)


def test_whoosh_waveform_shape_and_duration() -> None:
    arr = synth_whoosh(duration=0.32, sr=SAMPLE_RATE)
    assert isinstance(arr, np.ndarray)
    assert len(arr) == round(0.32 * SAMPLE_RATE)
    peak = float(np.max(np.abs(arr)))
    assert 0.75 <= peak <= 1.0


def test_whoosh_bell_envelope_and_amplitude() -> None:
    arr = synth_whoosh(duration=0.32, sr=SAMPLE_RATE)
    n = len(arr)
    edge_slice = round(0.02 * SAMPLE_RATE)
    start_rms = float(np.sqrt(np.mean(arr[:edge_slice] ** 2)))
    end_rms = float(np.sqrt(np.mean(arr[-edge_slice:] ** 2)))
    mid_rms = float(np.sqrt(np.mean(arr[n // 3 : 2 * n // 3] ** 2)))
    assert start_rms < mid_rms * 0.2
    assert end_rms < mid_rms * 0.2
    assert mid_rms > 0.1


def test_whoosh_deterministic_and_writes_wav(tmp_path: Path) -> None:
    w1 = synth_whoosh(duration=0.30)
    w2 = synth_whoosh(duration=0.30)
    assert np.array_equal(w1, w2)
    out = tmp_path / "whoosh.wav"
    res = synth_whoosh(out, duration=0.30)
    assert res == out
    assert out.is_file()
    _assert_wav(out, 0.30)


def test_synthesizers_reject_non_positive_duration() -> None:
    with pytest.raises(ValueError, match="duration must be positive"):
        synth_glass_crack(duration=0.0)
    with pytest.raises(ValueError, match="duration must be positive"):
        synth_whoosh(duration=-0.1)
