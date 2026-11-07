"""Pure standard-library sound effect synthesis for longform videos.

Every function writes a mono, 16-bit, 48 kHz WAV file using only ``math``, ``random``,
``struct`` and ``wave``. All generators use fixed seeds so output is deterministic, and
all samples are kept within [-32767, 32767].
"""

from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path
from typing import Any

import numpy as np

__all__ = [
    "SAMPLE_RATE",
    "mix_events",
    "synth_alarm",
    "synth_clock_ticks",
    "synth_glass_crack",
    "synth_punch",
    "synth_whisper",
    "synth_whoosh",
]

SAMPLE_RATE = 48000
_MAX = 32767


def _n_samples(duration: float) -> int:
    if duration <= 0:
        raise ValueError("duration must be positive")
    return max(1, int(round(duration * SAMPLE_RATE)))


def _clamp(value: float) -> int:
    iv = int(round(value))
    if iv > _MAX:
        return _MAX
    if iv < -_MAX:
        return -_MAX
    return iv


def _write_wav(path: Path, samples: list[float] | np.ndarray) -> Path:
    """Write float samples (full-scale = 32767) as a mono 16-bit 48 kHz WAV."""
    path = Path(path)
    if isinstance(samples, np.ndarray):
        arr = samples
        peak = float(np.max(np.abs(arr))) if len(arr) > 0 else 0.0
        if peak <= 1.05 and peak > 0:
            int_samples = np.clip(np.round(arr * _MAX), -_MAX, _MAX).astype(np.int16)
        else:
            int_samples = np.clip(np.round(arr), -_MAX, _MAX).astype(np.int16)
        data = int_samples.tobytes()
    else:
        peak = max((abs(s) for s in samples), default=0.0)
        scale = _MAX if 0 < peak <= 1.05 else 1.0
        data = struct.pack(f"<{len(samples)}h", *(_clamp(s * scale) for s in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(data)
    return path


def synth_alarm(path: Path, duration: float = 0.6, freq: float = 800.0) -> Path:
    """Square-wave beep with a short fade in and out."""
    n = _n_samples(duration)
    fade = min(n // 2, int(0.01 * SAMPLE_RATE))
    samples: list[float] = []
    for i in range(n):
        square = 1.0 if math.sin(2.0 * math.pi * freq * i / SAMPLE_RATE) >= 0 else -1.0
        env = 1.0
        if fade and i < fade:
            env = i / fade
        elif fade and i >= n - fade:
            env = (n - 1 - i) / fade
        samples.append(0.5 * _MAX * env * square)
    return _write_wav(path, samples)


def synth_clock_ticks(path: Path, duration: float = 2.0, rate_hz: float = 4.0) -> Path:
    """Short 3 ms decaying noise clicks at ``rate_hz`` clicks per second."""
    if rate_hz <= 0:
        raise ValueError("rate_hz must be positive")
    n = _n_samples(duration)
    rng = random.Random(11)
    burst = max(1, int(0.003 * SAMPLE_RATE))
    tau = burst / 5.0
    samples = [0.0] * n
    period = SAMPLE_RATE / rate_hz
    k = 0
    while True:
        start = int(round(k * period))
        if start >= n:
            break
        for j in range(burst):
            idx = start + j
            if idx >= n:
                break
            env = math.exp(-j / tau)
            samples[idx] += 0.6 * _MAX * env * rng.uniform(-1.0, 1.0)
        k += 1
    return _write_wav(path, samples)


def synth_punch(path: Path, duration: float = 0.35) -> Path:
    """Low thump: exponentially decaying sine sweep 120 Hz -> 40 Hz plus a noise transient."""
    n = _n_samples(duration)
    rng = random.Random(23)
    f0, f1 = 120.0, 40.0
    dt = 1.0 / SAMPLE_RATE
    phase = 0.0
    samples: list[float] = []
    for i in range(n):
        t = i / SAMPLE_RATE
        freq = f0 * (f1 / f0) ** (t / duration)
        phase += 2.0 * math.pi * freq * dt
        body = 0.8 * math.exp(-6.0 * t / duration) * math.sin(phase)
        noise = 0.3 * math.exp(-t / 0.003) * rng.uniform(-1.0, 1.0)
        samples.append(_MAX * (body + noise))
    return _write_wav(path, samples)


def synth_whisper(path: Path, duration: float = 1.2, seed: int = 5) -> Path:
    """Crude whisper: band-passed noise (one-pole LP then HP) with 3 Hz amplitude modulation.

    Normalised to a peak of about 0.25 of full scale so it stays quiet under narration.
    """
    n = _n_samples(duration)
    rng = random.Random(seed)
    a_lp = 1.0 - math.exp(-2.0 * math.pi * 3000.0 / SAMPLE_RATE)
    a_hp = math.exp(-2.0 * math.pi * 300.0 / SAMPLE_RATE)
    lp = 0.0
    hp = 0.0
    x_prev = 0.0
    raw: list[float] = []
    for i in range(n):
        x = rng.uniform(-1.0, 1.0)
        lp += a_lp * (x - lp)
        hp = a_hp * (hp + lp - x_prev)
        x_prev = lp
        t = i / SAMPLE_RATE
        env = 0.5 * (1.0 + math.sin(2.0 * math.pi * 3.0 * t))
        raw.append(hp * env)
    peak = max((abs(v) for v in raw), default=0.0) or 1.0
    scale = 0.25 * _MAX / peak
    return _write_wav(path, [v * scale for v in raw])


def mix_events(events: list[tuple[Path, float]], out: Path, total_seconds: float) -> Path:
    """Mix mono WAV files at second offsets into one WAV of exactly ``total_seconds``.

    Events starting at or after ``total_seconds`` are skipped; sums are clamped to int16 range.
    """
    total_n = _n_samples(total_seconds)
    mix = [0.0] * total_n
    for path, offset in events:
        if offset < 0:
            raise ValueError("event offset must be non-negative")
        if offset >= total_seconds:
            continue
        with wave.open(str(path), "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != SAMPLE_RATE:
                raise ValueError(f"{path} must be mono 16-bit {SAMPLE_RATE} Hz WAV")
            frames = w.readframes(w.getnframes())
        data = struct.unpack(f"<{len(frames) // 2}h", frames)
        start = int(round(offset * SAMPLE_RATE))
        for j, value in enumerate(data):
            idx = start + j
            if idx >= total_n:
                break
            mix[idx] += value
    return _write_wav(Path(out), mix)


def synth_glass_crack(
    *args: Any,
    duration: float | Path | str | None = None,
    sr: int = SAMPLE_RATE,
    path: Path | str | None = None,
) -> np.ndarray | Path:
    """High-frequency crystalline fracture snap for mirror cracks and shattering text.

    Sharp initial click at t=0 + 3 staggered micro-crackle bursts at 1800 Hz, 3200 Hz,
    and 4800 Hz + decaying filtered high-pass noise.
    """
    if args:
        if isinstance(args[0], (Path, str)):
            path = Path(args[0])
            if len(args) > 1 and duration is None:
                duration = args[1]
        elif isinstance(args[0], (int, float)):
            if duration is None:
                duration = args[0]
            if len(args) > 1:
                sr = args[1]

    if duration is None:
        duration = 0.35
    elif isinstance(duration, (Path, str)):
        path = Path(duration)
        duration = 0.35

    dur = float(duration)
    if dur <= 0:
        raise ValueError("duration must be positive")
    sr = int(sr)
    n = max(1, int(round(dur * sr)))
    t = np.linspace(0.0, dur, n, endpoint=False)

    # 1. Sharp initial click at t=0
    click = np.exp(-t / 0.0012) * np.sin(2.0 * np.pi * 5000.0 * t)

    # 2. Three staggered micro-crackle bursts at 1800 Hz, 3200 Hz, and 4800 Hz
    t1, t2, t3 = 0.006, 0.020, 0.042
    dt1 = np.maximum(0.0, t - t1)
    burst1 = np.where(t >= t1, np.exp(-dt1 / 0.015) * np.sin(2.0 * np.pi * 1800.0 * dt1), 0.0)

    dt2 = np.maximum(0.0, t - t2)
    burst2 = np.where(t >= t2, np.exp(-dt2 / 0.011) * np.sin(2.0 * np.pi * 3200.0 * dt2), 0.0)

    dt3 = np.maximum(0.0, t - t3)
    burst3 = np.where(t >= t3, np.exp(-dt3 / 0.008) * np.sin(2.0 * np.pi * 4800.0 * dt3), 0.0)

    # 3. Decaying filtered high-pass noise
    rng = np.random.RandomState(42)
    raw_noise = rng.uniform(-1.0, 1.0, n)
    alpha = float(np.exp(-2.0 * np.pi * 2800.0 / sr))
    hp_noise = np.zeros(n, dtype=np.float64)
    hp = 0.0
    x_prev = 0.0
    for i in range(n):
        x = float(raw_noise[i])
        hp = alpha * (hp + x - x_prev)
        x_prev = x
        hp_noise[i] = hp

    noise_env = np.exp(-t / (dur * 0.35))
    filtered_noise = hp_noise * noise_env

    signal = 0.55 * click + 0.30 * burst1 + 0.30 * burst2 + 0.30 * burst3 + 0.40 * filtered_noise
    peak = float(np.max(np.abs(signal))) if len(signal) > 0 else 0.0
    if peak > 0:
        signal = 0.90 * (signal / peak)

    if path is not None:
        return _write_wav(Path(path), signal)
    return signal


def synth_whoosh(
    *args: Any,
    duration: float | Path | str | None = None,
    sr: int = SAMPLE_RATE,
    path: Path | str | None = None,
) -> np.ndarray | Path:
    """Fast frequency-swept air whoosh for anime speed-line cuts and punches.

    Band-shaped noise modulated by a smooth bell envelope sin(pi * t / duration)**1.5
    and pitch sweep from 220 Hz to 680 Hz.
    """
    if args:
        if isinstance(args[0], (Path, str)):
            path = Path(args[0])
            if len(args) > 1 and duration is None:
                duration = args[1]
        elif isinstance(args[0], (int, float)):
            if duration is None:
                duration = args[0]
            if len(args) > 1:
                sr = args[1]

    if duration is None:
        duration = 0.32
    elif isinstance(duration, (Path, str)):
        path = Path(duration)
        duration = 0.32

    dur = float(duration)
    if dur <= 0:
        raise ValueError("duration must be positive")
    sr = int(sr)
    n = max(1, int(round(dur * sr)))
    t = np.linspace(0.0, dur, n, endpoint=False)

    f0, f1 = 220.0, 680.0
    phase = 2.0 * np.pi * (f0 * t + 0.5 * (f1 - f0) / dur * (t**2))
    carrier = np.sin(phase)

    rng = np.random.RandomState(101)
    noise = rng.uniform(-1.0, 1.0, n)
    band_noise = noise * carrier

    env = (np.maximum(0.0, np.sin(np.pi * t / dur))) ** 1.5
    signal = (0.6 * band_noise + 0.4 * carrier) * env
    peak = float(np.max(np.abs(signal))) if len(signal) > 0 else 0.0
    if peak > 0:
        signal = 0.85 * (signal / peak)

    if path is not None:
        return _write_wav(Path(path), signal)
    return signal
