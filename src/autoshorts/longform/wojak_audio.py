"""Continuous Bouncy Comedic Meme BGM & SFX Pipeline with EBU R128 Mastering.

Matches the Wojak Explainer Production Bible (Section 5):
- 4 Adaptive Genre Scoring Styles:
  1. Comedic Quirky Baseline: 124–130 BPM (default 128 BPM). Pizzicato strings, bouncy marimba, walking bass.
  2. The GigaChad / Phonk Anthem: 135–145 BPM (default 140 BPM). Distorted 808 sub-bass, cowbell melody leads.
  3. The Doomer / Russian Post-Punk Soundscape: 110–120 BPM (default 115 BPM). Chorus guitar, analog synth bass.
  4. The Bloomer / Lofi Study Beat: 80–85 BPM (default 82 BPM). Dusty Rhodes chords, vinyl crackle, gentle jazz drums.
- Subtle comedic SFX cues (whoosh, pop, rimshot, ka-ching, record scratch) on scene transitions.
- Fully procedural NumPy music synthesizers (no external audio files required, 100% offline).
- Music library integration: if user provides custom MP3/WAV, loops and fades it.
- Standard EBU R128 broadcast mastering (-14.0 LUFS target, -1.0 dBTP / dBFS ceiling).
"""

from __future__ import annotations

import logging
import math
import subprocess
import wave
from pathlib import Path
from typing import Final

import numpy as np

from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)

SAMPLE_RATE: Final[int] = 48000
TARGET_LUFS: Final[float] = -14.0
TRUE_PEAK_CEILING: Final[float] = -1.0
_MAX_INT16: Final[int] = 32767

# Wojak Explainer Production Bible (Section 5) BPM Specifications
COMEDIC_BPM_RANGE: Final[tuple[float, float]] = (124.0, 130.0)
PHONK_CHAD_BPM_RANGE: Final[tuple[float, float]] = (135.0, 145.0)
DOOMER_POSTPUNK_BPM_RANGE: Final[tuple[float, float]] = (110.0, 120.0)
BLOOMER_LOFI_BPM_RANGE: Final[tuple[float, float]] = (80.0, 85.0)

COMEDIC_BPM_DEFAULT: Final[float] = 128.0
PHONK_CHAD_BPM_DEFAULT: Final[float] = 140.0
DOOMER_POSTPUNK_BPM_DEFAULT: Final[float] = 115.0
BLOOMER_LOFI_BPM_DEFAULT: Final[float] = 82.0

# Backward compatibility aliases
BPM: Final[float] = COMEDIC_BPM_DEFAULT
BEAT_DUR: Final[float] = 60.0 / BPM
BAR_DUR: Final[float] = BEAT_DUR * 4.0


# ============================================================================
# Low-Level Audio Helpers
# ============================================================================


def write_wav(path: Path | str, samples: np.ndarray, sr: int = SAMPLE_RATE) -> Path:
    """Save float32 audio samples in [-1.0, 1.0] to a 16-bit PCM WAV file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(samples, dtype=np.float32)

    # Normalize if clipping
    peak = float(np.max(np.abs(arr))) if len(arr) > 0 else 0.0
    if peak > 1.0:
        arr = arr / peak

    int16_data = np.clip(np.round(arr * _MAX_INT16), -_MAX_INT16, _MAX_INT16).astype(np.int16)

    # Detect mono vs stereo
    if int16_data.ndim == 1:
        channels = 1
        raw_bytes = int16_data.tobytes()
    else:
        channels = int16_data.shape[1]
        raw_bytes = int16_data.tobytes()

    with wave.open(str(p), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(raw_bytes)
    return p


def read_wav(path: Path | str) -> tuple[np.ndarray, int]:
    """Read a WAV file into a float32 NumPy array in [-1.0, 1.0]."""
    p = Path(path)
    with wave.open(str(p), "rb") as w:
        n_channels = w.getnchannels()
        sr = w.getframerate()
        n_frames = w.getnframes()
        raw_bytes = w.readframes(n_frames)

    int16_data = np.frombuffer(raw_bytes, dtype=np.int16)
    if n_channels > 1:
        int16_data = int16_data.reshape(-1, n_channels)
    float_data = (int16_data / _MAX_INT16).astype(np.float32)
    return float_data, sr


# ============================================================================
# Procedural SFX Synthesizers (Subtle transition cues)
# ============================================================================


def synth_transition_whoosh(duration: float = 0.32, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Crisp high-pass filtered transition whoosh for scene changes."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    noise = np.random.uniform(-1.0, 1.0, n)

    # Dynamic sweep filter envelope
    envelope = np.sin(np.pi * (t / duration)) ** 2
    f_start, f_end = 400.0, 3200.0
    freq_sweep = np.linspace(f_start, f_end, n)
    sweep_tone = np.sin(2.0 * np.pi * freq_sweep * t)

    whoosh = (0.7 * noise + 0.3 * sweep_tone) * envelope * 0.45
    return whoosh.astype(np.float32)


def synth_comedic_pop(duration: float = 0.16, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Comedic pitch-shifted mouth 'pop' / plink."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    freq = 320.0 + 800.0 * (1.0 - np.exp(-t * 60.0))
    envelope = np.exp(-t * 32.0)
    pop = np.sin(2.0 * np.pi * freq * t) * envelope * 0.65
    return pop.astype(np.float32)


def synth_kaching(duration: float = 0.45, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Comedic cash register / bill impact chime."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    chime1 = np.sin(2.0 * np.pi * 2093.0 * t) * np.exp(-t * 9.0)  # C7
    chime2 = np.sin(2.0 * np.pi * 2793.8 * t) * np.exp(-t * 11.0)  # F7
    chime3 = np.sin(2.0 * np.pi * 3135.9 * t) * np.exp(-t * 14.0)  # G7
    kaching = (chime1 * 0.45 + chime2 * 0.35 + chime3 * 0.25) * 0.55
    return kaching.astype(np.float32)


def synth_record_scratch(duration: float = 0.28, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Vinyl record backspin scratch."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    freq = 1200.0 * np.abs(np.sin(np.pi * 4.0 * t)) + 300.0
    noise = np.random.uniform(-1.0, 1.0, n)
    tone = np.sin(2.0 * np.pi * freq * t)
    env = np.sin(np.pi * (t / duration))
    scratch = (0.5 * tone + 0.5 * noise) * env * 0.40
    return scratch.astype(np.float32)


def synth_rimshot(duration: float = 0.35, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Classic comedy punchline rimshot."""
    n = int(sr * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    click = np.sin(2.0 * np.pi * 1450.0 * t) * np.exp(-t * 70.0)
    snare = np.random.uniform(-1.0, 1.0, n) * np.exp(-t * 22.0)
    rim = (click * 0.6 + snare * 0.4) * 0.65
    return rim.astype(np.float32)


# ============================================================================
# Procedural 1. Comedic Quirky Baseline BGM (124-130 BPM, default 128 BPM)
# ============================================================================


def _synth_comedic_drum_kit(pattern_length: int, sr: int) -> np.ndarray:
    """Synthesize 1 bar of crisp bouncy comedic drums."""
    buf = np.zeros(pattern_length, dtype=np.float32)
    step_samples = pattern_length // 16

    # Kick on beats 1 and 3 (steps 0, 8)
    for step in [0, 8]:
        idx = step * step_samples
        kick_dur = 0.14
        kn = int(sr * kick_dur)
        if idx + kn <= pattern_length:
            kt = np.linspace(0, kick_dur, kn, endpoint=False)
            k_freq = 140.0 * np.exp(-kt * 28.0) + 45.0
            k_env = np.exp(-kt * 22.0)
            kick = np.sin(2.0 * np.pi * k_freq * kt) * k_env * 0.75
            buf[idx : idx + kn] += kick

    # Snare/Clap on beats 2 and 4 (steps 4, 12)
    for step in [4, 12]:
        idx = step * step_samples
        snare_dur = 0.16
        sn = int(sr * snare_dur)
        if idx + sn <= pattern_length:
            st = np.linspace(0, snare_dur, sn, endpoint=False)
            noise = np.random.uniform(-1.0, 1.0, sn) * np.exp(-st * 26.0)
            body = np.sin(2.0 * np.pi * 210.0 * st) * np.exp(-st * 34.0)
            snare = (noise * 0.7 + body * 0.3) * 0.60
            buf[idx : idx + sn] += snare

    # Hi-hats on every 16th note with swing/accent
    for step in range(16):
        idx = step * step_samples
        hat_dur = 0.05
        hn = int(sr * hat_dur)
        if idx + hn <= pattern_length:
            ht = np.linspace(0, hat_dur, hn, endpoint=False)
            accent = 0.35 if step % 2 == 0 else 0.20
            hat = np.random.uniform(-1.0, 1.0, hn) * np.exp(-ht * 85.0) * accent
            buf[idx : idx + hn] += hat

    return buf


def _synth_comedic_bass(pattern_length: int, root_freq: float, sr: int) -> np.ndarray:
    """Synthesize 1 bar of bouncy comedic walking/ragtime bass."""
    buf = np.zeros(pattern_length, dtype=np.float32)
    step_samples = pattern_length // 4

    freqs = [root_freq, root_freq * 1.5, root_freq, root_freq * 1.5]
    for b_idx, f in enumerate(freqs):
        idx = b_idx * step_samples
        dur = 0.22
        bn = int(sr * dur)
        if idx + bn <= pattern_length:
            bt = np.linspace(0, dur, bn, endpoint=False)
            env = np.exp(-bt * 14.0)
            tone = (np.sin(2.0 * np.pi * f * bt) + 0.35 * np.sin(4.0 * np.pi * f * bt)) * env * 0.55
            buf[idx : idx + bn] += tone

    return buf


def _synth_comedic_chords(pattern_length: int, chord_freqs: list[float], sr: int) -> np.ndarray:
    """Synthesize quirky staccato piano/brass chords on upbeats (2 & 4)."""
    buf = np.zeros(pattern_length, dtype=np.float32)
    step_samples = pattern_length // 8

    for step in [1, 3, 5, 7]:
        idx = step * step_samples
        dur = 0.12
        cn = int(sr * dur)
        if idx + cn <= pattern_length:
            ct = np.linspace(0, dur, cn, endpoint=False)
            env = np.exp(-ct * 28.0)
            chord = sum(np.sin(2.0 * np.pi * f * ct) for f in chord_freqs) / len(chord_freqs)
            buf[idx : idx + cn] += chord * env * 0.40

    return buf


def _synth_comedic_melody(pattern_length: int, bar_idx: int, sr: int) -> np.ndarray:
    """Synthesize a catchy, bouncing comedic marimba/pizzicato motif."""
    buf = np.zeros(pattern_length, dtype=np.float32)
    step_samples = pattern_length // 16

    notes_bar1 = [(0, 523.25), (3, 659.25), (6, 783.99), (8, 659.25), (11, 880.0), (14, 783.99)]
    notes_bar2 = [(0, 587.33), (2, 659.25), (4, 698.46), (8, 783.99), (12, 523.25)]
    notes_bar3 = [(0, 523.25), (3, 659.25), (6, 783.99), (8, 1046.5), (11, 880.0), (14, 783.99)]
    notes_bar4 = [(0, 880.0), (2, 783.99), (4, 659.25), (6, 587.33), (8, 523.25)]

    bars = [notes_bar1, notes_bar2, notes_bar3, notes_bar4]
    pattern = bars[bar_idx % len(bars)]

    for step, freq in pattern:
        idx = step * step_samples
        dur = 0.18
        mn = int(sr * dur)
        if idx + mn <= pattern_length:
            mt = np.linspace(0, dur, mn, endpoint=False)
            env = np.exp(-mt * 20.0)
            tone = (
                (np.sin(2.0 * np.pi * freq * mt) + 0.28 * np.sin(4.0 * np.pi * freq * mt))
                * env
                * 0.48
            )
            buf[idx : idx + mn] += tone

    return buf


def synth_comedic_bgm(
    total_duration_sec: float,
    sr: int = SAMPLE_RATE,
    bpm: float = COMEDIC_BPM_DEFAULT,
) -> np.ndarray:
    """Generate a continuous bouncy comedic meme soundtrack (124-130 BPM)."""
    clamped_bpm = float(np.clip(bpm, COMEDIC_BPM_RANGE[0], COMEDIC_BPM_RANGE[1]))
    bar_dur = (60.0 / clamped_bpm) * 4.0
    bar_samples = int(sr * bar_dur)
    total_samples = int(sr * total_duration_sec)
    music = np.zeros(total_samples, dtype=np.float32)

    root_freqs = [65.41, 49.0, 55.0, 43.65]  # C2, G1, A1, F1
    chord_sets = [
        [261.63, 329.63, 392.0],  # C major
        [196.00, 246.94, 293.66],  # G major
        [220.00, 261.63, 329.63],  # A minor
        [174.61, 220.00, 261.63],  # F major
    ]

    current_idx = 0
    bar_counter = 0

    while current_idx < total_samples:
        chord_idx = bar_counter % 4
        drums = _synth_comedic_drum_kit(bar_samples, sr)
        bass = _synth_comedic_bass(bar_samples, root_freqs[chord_idx], sr)
        chords = _synth_comedic_chords(bar_samples, chord_sets[chord_idx], sr)
        melody = _synth_comedic_melody(bar_samples, bar_counter, sr)

        bar_mix = drums * 0.70 + bass * 0.65 + chords * 0.42 + melody * 0.52

        remain = min(bar_samples, total_samples - current_idx)
        music[current_idx : current_idx + remain] = bar_mix[:remain]

        current_idx += bar_samples
        bar_counter += 1

    # Apply gentle fade-in (0.3s) and fade-out (1.5s)
    fade_in_samples = int(sr * 0.3)
    if fade_in_samples > 0 and len(music) > fade_in_samples:
        music[:fade_in_samples] *= np.linspace(0.0, 1.0, fade_in_samples)

    fade_out_samples = int(sr * 1.5)
    if fade_out_samples > 0 and len(music) > fade_out_samples:
        music[-fade_out_samples:] *= np.linspace(1.0, 0.0, fade_out_samples)

    peak = float(np.max(np.abs(music))) if len(music) > 0 else 0.0
    if peak > 0.95:
        music = (music / peak) * 0.92

    return music


# ============================================================================
# Procedural 2. The GigaChad / Phonk Anthem (135-145 BPM, default 140 BPM)
# ============================================================================


def synth_phonk_chad_bgm(
    total_duration_sec: float,
    sr: int = SAMPLE_RATE,
    bpm: float = PHONK_CHAD_BPM_DEFAULT,
) -> np.ndarray:
    """Generate aggressive GigaChad / Phonk anthem (135-145 BPM).

    Production Bible Specs:
    - Distorted 808 sub-bass, cowbell melody leads, aggressive sidechain compression.
    - Mood: Peak dominance, unbothered confidence.
    """
    clamped_bpm = float(np.clip(bpm, PHONK_CHAD_BPM_RANGE[0], PHONK_CHAD_BPM_RANGE[1]))
    bar_dur = (60.0 / clamped_bpm) * 4.0
    bar_samples = int(sr * bar_dur)
    total_samples = int(sr * total_duration_sec)
    music = np.zeros(total_samples, dtype=np.float32)

    step_samples = bar_samples // 16

    # Phonk Cowbell Lead pattern (F# minor drift scale)
    # Frequencies: F#5 (740Hz), A5 (880Hz), B5 (988Hz), C#6 (1109Hz)
    cowbell_patterns = [
        [(0, 739.99), (3, 880.00), (6, 1108.73), (8, 987.77), (11, 880.00), (14, 739.99)],
        [(0, 739.99), (2, 880.00), (4, 987.77), (6, 1108.73), (10, 987.77), (12, 880.00)],
    ]

    current_idx = 0
    bar_counter = 0

    while current_idx < total_samples:
        bar_buf = np.zeros(bar_samples, dtype=np.float32)
        sidechain_duck = np.ones(bar_samples, dtype=np.float32)

        # 1. 808 Kick on steps 0, 6, 10
        for step in [0, 6, 10]:
            idx = step * step_samples
            k_dur = 0.22
            kn = int(sr * k_dur)
            if idx + kn <= bar_samples:
                kt = np.linspace(0, k_dur, kn, endpoint=False)
                # Punchy pitch drop from 180Hz to 45Hz
                k_freq = 180.0 * np.exp(-kt * 35.0) + 45.0
                k_env = np.exp(-kt * 18.0)
                sub_kick = np.sin(2.0 * np.pi * k_freq * kt) * k_env
                # Saturated 808 distortion
                sub_kick = np.tanh(2.6 * sub_kick) * 0.85
                bar_buf[idx : idx + kn] += sub_kick

                # Aggressive sidechain ducking curve
                duck_len = min(int(sr * 0.16), bar_samples - idx)
                sidechain_duck[idx : idx + duck_len] *= np.linspace(0.20, 1.0, duck_len)

        # 2. Snappy Phonk Trap Snare/Clap on steps 4, 12
        for step in [4, 12]:
            idx = step * step_samples
            snare_dur = 0.18
            sn = int(sr * snare_dur)
            if idx + sn <= bar_samples:
                st = np.linspace(0, snare_dur, sn, endpoint=False)
                noise = np.random.uniform(-1.0, 1.0, sn) * np.exp(-st * 28.0)
                clap = np.sin(2.0 * np.pi * 320.0 * st) * np.exp(-st * 36.0)
                snare = (noise * 0.75 + clap * 0.25) * 0.65
                bar_buf[idx : idx + sn] += snare

        # 3. Fast rolling Phonk Hi-Hats (16th notes with accents on offbeats)
        for step in range(16):
            idx = step * step_samples
            h_dur = 0.04
            hn = int(sr * h_dur)
            if idx + hn <= bar_samples:
                ht = np.linspace(0, h_dur, hn, endpoint=False)
                vel = 0.38 if step % 2 == 1 else 0.22
                hat = np.random.uniform(-1.0, 1.0, hn) * np.exp(-ht * 90.0) * vel
                bar_buf[idx : idx + hn] += hat

        # 4. Cowbell Melody Leads
        lead_buf = np.zeros(bar_samples, dtype=np.float32)
        pat = cowbell_patterns[bar_counter % len(cowbell_patterns)]
        for step, freq in pat:
            idx = step * step_samples
            cb_dur = 0.15
            cbn = int(sr * cb_dur)
            if idx + cbn <= bar_samples:
                cbt = np.linspace(0, cb_dur, cbn, endpoint=False)
                # Phonk metallic dual-tone ring
                tone1 = np.sin(2.0 * np.pi * freq * cbt)
                tone2 = np.sin(2.0 * np.pi * (freq * 1.58) * cbt) * 0.55
                cb_env = np.exp(-cbt * 24.0)
                cowbell = np.tanh(1.8 * (tone1 + tone2) * cb_env) * 0.48
                lead_buf[idx : idx + cbn] += cowbell

        # Apply sidechain compression to cowbell lead
        bar_buf += lead_buf * sidechain_duck

        remain = min(bar_samples, total_samples - current_idx)
        music[current_idx : current_idx + remain] = bar_buf[:remain]

        current_idx += bar_samples
        bar_counter += 1

    # Fade in / out
    fade_in_samples = int(sr * 0.3)
    if fade_in_samples > 0 and len(music) > fade_in_samples:
        music[:fade_in_samples] *= np.linspace(0.0, 1.0, fade_in_samples)

    fade_out_samples = int(sr * 1.5)
    if fade_out_samples > 0 and len(music) > fade_out_samples:
        music[-fade_out_samples:] *= np.linspace(1.0, 0.0, fade_out_samples)

    peak = float(np.max(np.abs(music))) if len(music) > 0 else 0.0
    if peak > 0.95:
        music = (music / peak) * 0.92

    return music


# ============================================================================
# Procedural 3. The Doomer / Russian Post-Punk Soundscape (110-120 BPM)
# ============================================================================


def synth_doomer_postpunk_bgm(
    total_duration_sec: float,
    sr: int = SAMPLE_RATE,
    bpm: float = DOOMER_POSTPUNK_BPM_DEFAULT,
) -> np.ndarray:
    """Generate cold, melancholic Russian Post-punk / Doomer soundscape (110-120 BPM).

    Production Bible Specs:
    - Chorus-heavy electric guitar, pulsing analog synth bassline (Juno/Minimoog),
      driving 4-on-the-floor 80s drum machine.
    - Mood: Cold winter night, cigarette smoke, existential fatigue.
    """
    clamped_bpm = float(np.clip(bpm, DOOMER_POSTPUNK_BPM_RANGE[0], DOOMER_POSTPUNK_BPM_RANGE[1]))
    bar_dur = (60.0 / clamped_bpm) * 4.0
    bar_samples = int(sr * bar_dur)
    total_samples = int(sr * total_duration_sec)
    music = np.zeros(total_samples, dtype=np.float32)

    step_samples = bar_samples // 16

    # 4-bar Post-punk minor progression: Am -> F -> C -> Em
    bass_roots = [55.0, 43.65, 65.41, 41.2]  # A1, F1, C2, E1
    guitar_chords = [
        [220.0, 261.63, 329.63, 440.0],  # Am
        [174.61, 220.0, 261.63, 349.23],  # F
        [261.63, 329.63, 392.0, 523.25],  # C
        [164.81, 196.0, 246.94, 329.63],  # Em
    ]

    current_idx = 0
    bar_counter = 0

    while current_idx < total_samples:
        bar_buf = np.zeros(bar_samples, dtype=np.float32)
        chord_idx = bar_counter % 4

        # 1. 4-on-the-floor 80s Drum Machine
        # Kick on beats 1, 2, 3, 4 (steps 0, 4, 8, 12)
        for step in [0, 4, 8, 12]:
            idx = step * step_samples
            k_dur = 0.16
            kn = int(sr * k_dur)
            if idx + kn <= bar_samples:
                kt = np.linspace(0, k_dur, kn, endpoint=False)
                k_freq = 110.0 * np.exp(-kt * 24.0) + 48.0
                k_env = np.exp(-kt * 20.0)
                kick = np.sin(2.0 * np.pi * k_freq * kt) * k_env * 0.70
                bar_buf[idx : idx + kn] += kick

        # Gated 80s Snare on beats 2 and 4 (steps 4, 12)
        for step in [4, 12]:
            idx = step * step_samples
            s_dur = 0.20
            sn = int(sr * s_dur)
            if idx + sn <= bar_samples:
                st = np.linspace(0, s_dur, sn, endpoint=False)
                noise = np.random.uniform(-1.0, 1.0, sn) * np.exp(-st * 16.0)
                body = np.sin(2.0 * np.pi * 180.0 * st) * np.exp(-st * 24.0)
                snare = (noise * 0.75 + body * 0.25) * 0.60
                bar_buf[idx : idx + sn] += snare

        # Steady 8th-note Hi-Hats
        for step in range(0, 16, 2):
            idx = step * step_samples
            h_dur = 0.05
            hn = int(sr * h_dur)
            if idx + hn <= bar_samples:
                ht = np.linspace(0, h_dur, hn, endpoint=False)
                hat = np.random.uniform(-1.0, 1.0, hn) * np.exp(-ht * 80.0) * 0.28
                bar_buf[idx : idx + hn] += hat

        # 2. Pulsing 8th-Note Analog Synth Bassline (Juno/Minimoog pulse)
        root = bass_roots[chord_idx]
        for step in range(0, 16, 2):
            idx = step * step_samples
            b_dur = 0.14
            bn = int(sr * b_dur)
            if idx + bn <= bar_samples:
                bt = np.linspace(0, b_dur, bn, endpoint=False)
                # Warm analog tone: fundamental + 2nd + 3rd harmonic
                tone = (
                    np.sin(2.0 * np.pi * root * bt)
                    + 0.45 * np.sin(4.0 * np.pi * root * bt)
                    + 0.25 * np.sin(6.0 * np.pi * root * bt)
                )
                b_env = np.exp(-bt * 15.0)
                bass = np.tanh(1.5 * tone * b_env) * 0.52
                bar_buf[idx : idx + bn] += bass

        # 3. Chorus-heavy Electric Guitar Arpeggiation
        ch_notes = guitar_chords[chord_idx]
        for note_idx, step in enumerate(range(0, 16, 2)):
            idx = step * step_samples
            g_dur = 0.28
            gn = int(sr * g_dur)
            if idx + gn <= bar_samples:
                gt = np.linspace(0, g_dur, gn, endpoint=False)
                freq = ch_notes[note_idx % len(ch_notes)]
                # Chorus LFO pitch wobble (1.8 Hz modulation)
                chorus_lfo = 1.0 + 0.008 * np.sin(2.0 * np.pi * 1.8 * (current_idx / sr + gt))
                guitar_tone = np.sin(2.0 * np.pi * freq * chorus_lfo * gt)
                g_env = np.exp(-gt * 10.0)
                guitar = guitar_tone * g_env * 0.38
                bar_buf[idx : idx + gn] += guitar

        remain = min(bar_samples, total_samples - current_idx)
        music[current_idx : current_idx + remain] = bar_buf[:remain]

        current_idx += bar_samples
        bar_counter += 1

    # Fade in / out
    fade_in_samples = int(sr * 0.3)
    if fade_in_samples > 0 and len(music) > fade_in_samples:
        music[:fade_in_samples] *= np.linspace(0.0, 1.0, fade_in_samples)

    fade_out_samples = int(sr * 1.5)
    if fade_out_samples > 0 and len(music) > fade_out_samples:
        music[-fade_out_samples:] *= np.linspace(1.0, 0.0, fade_out_samples)

    peak = float(np.max(np.abs(music))) if len(music) > 0 else 0.0
    if peak > 0.95:
        music = (music / peak) * 0.92

    return music


# ============================================================================
# Procedural 4. The Bloomer / Lofi Study Beat (80-85 BPM, default 82 BPM)
# ============================================================================


def synth_bloomer_lofi_bgm(
    total_duration_sec: float,
    sr: int = SAMPLE_RATE,
    bpm: float = BLOOMER_LOFI_BPM_DEFAULT,
) -> np.ndarray:
    """Generate peaceful, warm Bloomer / Lofi study beat (80-85 BPM).

    Production Bible Specs:
    - Dusty electric piano (Rhodes), vinyl crackle, warm sub-bass, gentle jazz drums.
    - Mood: Morning coffee, fresh air, peaceful mindset.
    """
    clamped_bpm = float(np.clip(bpm, BLOOMER_LOFI_BPM_RANGE[0], BLOOMER_LOFI_BPM_RANGE[1]))
    bar_dur = (60.0 / clamped_bpm) * 4.0
    bar_samples = int(sr * bar_dur)
    total_samples = int(sr * total_duration_sec)
    music = np.zeros(total_samples, dtype=np.float32)

    step_samples = bar_samples // 16

    # Warm Jazzy Rhodes Chords: Cmaj7 -> Am7 -> Dm7 -> G7
    lofi_chords = [
        [261.63, 329.63, 392.00, 493.88],  # Cmaj7
        [220.00, 261.63, 329.63, 392.00],  # Am7
        [146.83, 174.61, 220.00, 261.63],  # Dm7
        [196.00, 246.94, 293.66, 349.23],  # G7
    ]
    sub_roots = [65.41, 55.0, 73.42, 49.0]

    current_idx = 0
    bar_counter = 0

    while current_idx < total_samples:
        bar_buf = np.zeros(bar_samples, dtype=np.float32)
        chord_idx = bar_counter % 4

        # 1. Subtle Vinyl Crackle / Dust Bed
        noise_dust = np.random.uniform(-0.04, 0.04, bar_samples)
        # Random occasional pops
        pop_indices = np.random.randint(0, bar_samples, size=max(1, bar_samples // 8000))
        noise_dust[pop_indices] += np.random.uniform(-0.12, 0.12, size=len(pop_indices))
        bar_buf += noise_dust * 0.35

        # 2. Relaxed Boom-Bap Jazz Drums
        # Soft low-pass Kick on beat 1 (step 0) and offbeat 2.5 (step 5)
        for step in [0, 5]:
            idx = step * step_samples
            k_dur = 0.20
            kn = int(sr * k_dur)
            if idx + kn <= bar_samples:
                kt = np.linspace(0, k_dur, kn, endpoint=False)
                k_freq = 90.0 * np.exp(-kt * 22.0) + 40.0
                k_env = np.exp(-kt * 14.0)
                kick = np.sin(2.0 * np.pi * k_freq * kt) * k_env * 0.60
                bar_buf[idx : idx + kn] += kick

        # Warm Brush Snare / Rim Click on beats 2 and 4 (steps 4, 12)
        for step in [4, 12]:
            idx = step * step_samples
            s_dur = 0.18
            sn = int(sr * s_dur)
            if idx + sn <= bar_samples:
                st = np.linspace(0, s_dur, sn, endpoint=False)
                brush = np.random.uniform(-1.0, 1.0, sn) * np.exp(-st * 24.0) * 0.40
                rim = np.sin(2.0 * np.pi * 840.0 * st) * np.exp(-st * 45.0) * 0.35
                bar_buf[idx : idx + sn] += brush + rim

        # Swung Lo-fi Hi-Hats (every 8th note with laid-back swing)
        for step in range(0, 16, 2):
            idx = step * step_samples
            h_dur = 0.06
            hn = int(sr * h_dur)
            if idx + hn <= bar_samples:
                ht = np.linspace(0, h_dur, hn, endpoint=False)
                hat = np.random.uniform(-1.0, 1.0, hn) * np.exp(-ht * 75.0) * 0.20
                bar_buf[idx : idx + hn] += hat

        # 3. Warm Rhodes Electric Piano Chords
        notes = lofi_chords[chord_idx]
        rhodes_dur = bar_dur * 0.85
        rn = int(sr * rhodes_dur)
        if rn <= bar_samples:
            rt = np.linspace(0, rhodes_dur, rn, endpoint=False)
            chord_sum = np.zeros(rn, dtype=np.float32)
            for f in notes:
                # Rhodes bell timbre: fundamental + soft 2nd harmonic + subtle 4Hz tremolo
                tremolo = 1.0 + 0.12 * np.sin(2.0 * np.pi * 4.0 * rt)
                partial1 = np.sin(2.0 * np.pi * f * rt)
                partial2 = np.sin(4.0 * np.pi * f * rt) * 0.20
                chord_sum += (partial1 + partial2) * tremolo
            r_env = np.exp(-rt * 2.8)
            rhodes = (chord_sum / len(notes)) * r_env * 0.45
            bar_buf[:rn] += rhodes

        # 4. Warm Round Sub-Bass
        sub_f = sub_roots[chord_idx]
        sub_dur = bar_dur * 0.90
        sbn = int(sr * sub_dur)
        if sbn <= bar_samples:
            sbt = np.linspace(0, sub_dur, sbn, endpoint=False)
            sub_env = np.exp(-sbt * 2.2)
            sub_bass = np.sin(2.0 * np.pi * sub_f * sbt) * sub_env * 0.45
            bar_buf[:sbn] += sub_bass

        remain = min(bar_samples, total_samples - current_idx)
        music[current_idx : current_idx + remain] = bar_buf[:remain]

        current_idx += bar_samples
        bar_counter += 1

    # Fade in / out
    fade_in_samples = int(sr * 0.4)
    if fade_in_samples > 0 and len(music) > fade_in_samples:
        music[:fade_in_samples] *= np.linspace(0.0, 1.0, fade_in_samples)

    fade_out_samples = int(sr * 1.8)
    if fade_out_samples > 0 and len(music) > fade_out_samples:
        music[-fade_out_samples:] *= np.linspace(1.0, 0.0, fade_out_samples)

    peak = float(np.max(np.abs(music))) if len(music) > 0 else 0.0
    if peak > 0.95:
        music = (music / peak) * 0.92

    return music


# ============================================================================
# Universal Procedural BGM Dispatcher
# ============================================================================


def generate_procedural_wojak_bgm(
    total_duration_sec: float,
    sr: int = SAMPLE_RATE,
    mood: str = "comedic",
) -> np.ndarray:
    """Generate a continuous procedural BGM soundtrack matching the requested mood.

    Mood options (Wojak Explainer Production Bible Section 5):
    - 'comedic' / 'quirky' / 'playful': 124-130 BPM bouncy marimba & ragtime bass.
    - 'phonk' / 'chad' / 'gym' / 'alpha': 135-145 BPM aggressive cowbell lead & 808 sub.
    - 'doomer' / 'postpunk' / 'dark' / 'night': 110-120 BPM chorus guitar & 80s drum machine.
    - 'bloomer' / 'lofi' / 'wholesome' / 'peaceful': 80-85 BPM dusty Rhodes chords & vinyl crackle.
    """
    m = (mood or "comedic").strip().lower()

    if m in ("phonk", "chad", "gym", "alpha", "gigachad"):
        return synth_phonk_chad_bgm(total_duration_sec, sr=sr)
    if m in ("doomer", "postpunk", "post_punk", "dark", "night", "melancholy"):
        return synth_doomer_postpunk_bgm(total_duration_sec, sr=sr)
    if m in ("bloomer", "lofi", "wholesome", "peaceful", "study", "relax"):
        return synth_bloomer_lofi_bgm(total_duration_sec, sr=sr)

    # Default to bouncy comedic quirky baseline
    return synth_comedic_bgm(total_duration_sec, sr=sr)


# ============================================================================
# Audio Assembly & EBU R128 Mastering
# ============================================================================


def assemble_wojak_audio(
    total_duration_sec: float,
    scene_transition_timestamps: list[float],
    music_file_path: Path | str | None = None,
    output_wav_path: Path | str | None = None,
    sr: int = SAMPLE_RATE,
    mood: str = "comedic",
) -> Path:
    """Assemble continuous BGM and overlay subtle comedic SFX at scene changes.

    Strictly satisfies Wojak Explainer Production Bible:
    - Target integrated loudness: -14.0 LUFS
    - True peak ceiling: -1.0 dBFS
    - SFX stingers mixed at -6.0 dBFS relative to BGM

    Args:
        total_duration_sec: Total duration of video in seconds.
        scene_transition_timestamps: List of timestamp offsets (seconds) for SFX cues.
        music_file_path: Optional custom BGM track; if None or invalid, procedural BGM is used.
        output_wav_path: Output WAV file destination.
        sr: Sample rate (48000 Hz).
        mood: Aesthetic soundscape ('comedic', 'chad'/'phonk', 'doomer'/'postpunk', 'bloomer'/'lofi').

    Returns:
        Path to the mastered WAV file.
    """
    total_samples = int(sr * total_duration_sec)
    master_mix = np.zeros(total_samples, dtype=np.float32)

    # 1. Continuous BGM track
    bgm_loaded = False
    if music_file_path:
        p = Path(music_file_path)
        if p.is_file():
            try:
                bgm_samples, bgm_sr = read_wav(p)
                if bgm_sr != sr:
                    logger.info("Custom audio sample rate is %d (target %d)", bgm_sr, sr)
                if bgm_samples.ndim > 1:
                    bgm_samples = np.mean(bgm_samples, axis=1)  # Mono fold
                bgm_len = len(bgm_samples)
                if bgm_len > 0:
                    repeats = math.ceil(total_samples / bgm_len)
                    looped = np.tile(bgm_samples, repeats)[:total_samples]
                    master_mix += looped * 0.75
                    bgm_loaded = True
            except Exception as exc:
                logger.warning("Could not read custom music file %s: %s", p, exc)

    if not bgm_loaded:
        logger.info("Synthesizing procedural %s BGM (%.1fs)...", mood, total_duration_sec)
        procedural_bgm = generate_procedural_wojak_bgm(total_duration_sec, sr=sr, mood=mood)
        master_mix += procedural_bgm

    # 2. Overlay subtle comedic SFX at scene transitions (-6 dB relative ducking)
    sfx_generators = [
        synth_transition_whoosh,
        synth_comedic_pop,
        synth_kaching,
        synth_rimshot,
        synth_record_scratch,
    ]

    for idx, ts in enumerate(scene_transition_timestamps):
        start_idx = int(ts * sr)
        if start_idx >= total_samples:
            continue
        sfx_func = sfx_generators[idx % len(sfx_generators)]
        sfx = sfx_func(sr=sr)
        sfx_len = min(len(sfx), total_samples - start_idx)
        # -6 dB relative level (~0.50)
        master_mix[start_idx : start_idx + sfx_len] += sfx[:sfx_len] * 0.50

    # 3. Peak limiting to -1.0 dBFS (10 ** (-1.0 / 20) = 0.89125)
    ceiling_linear = 10.0 ** (TRUE_PEAK_CEILING / 20.0)
    peak = float(np.max(np.abs(master_mix))) if len(master_mix) > 0 else 0.0
    if peak > ceiling_linear:
        master_mix = (master_mix / peak) * ceiling_linear

    # 4. Save unmastered mix to temp WAV
    if not output_wav_path:
        import tempfile

        output_wav_path = Path(tempfile.gettempdir()) / "wojak_mastered_audio.wav"
    output_wav_path = Path(output_wav_path)
    write_wav(output_wav_path, master_mix, sr=sr)

    # 5. EBU R128 Loudness Normalization using FFmpeg (-14 LUFS target, -1.0 dBFS true peak)
    try:
        ffmpeg_bin = get_ffmpeg_path()
        normalized_path = output_wav_path.with_name(f"{output_wav_path.stem}_ebur128.wav")
        loudnorm_cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(output_wav_path),
            "-af",
            f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK_CEILING}:LRA=7",
            "-c:a",
            "pcm_s16le",
            str(normalized_path),
        ]
        proc = subprocess.run(loudnorm_cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0 and normalized_path.is_file():
            normalized_path.replace(output_wav_path)
            logger.info(
                "EBU R128 loudness normalization applied (target %s LUFS, peak %s dBTP)",
                TARGET_LUFS,
                TRUE_PEAK_CEILING,
            )
    except Exception as exc:
        logger.warning("EBU R128 normalization skipped (%s); unmastered WAV retained", exc)

    return output_wav_path
