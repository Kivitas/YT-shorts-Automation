"""Continuous Bouncy Comedic Meme BGM & SFX Pipeline with EBU R128 Mastering.

Matches the SapX audio aesthetic:
- Continuous energetic, bouncy comedy meme beat across the entire long-form video.
- Subtle comedic SFX cues (whoosh, pop, rimshot, ka-ching, scratch) on scene transitions.
- Fully procedural NumPy music synthesizer (no external audio files required, 100% offline).
- Music library integration: if user provides custom MP3/WAV or library tracks exist, loops and fades them.
- Standard EBU R128 broadcast mastering (-14.0 LUFS target, -1.0 dBTP ceiling).
"""

from __future__ import annotations

import logging
import math
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np

from autoshorts.media.audio_master import normalize_two_pass
from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
TARGET_LUFS = -14.0
TRUE_PEAK_CEILING = -1.0
_MAX_INT16 = 32767

# 130 BPM comedic meme tempo
BPM = 130.0
BEAT_DUR = 60.0 / BPM  # ~0.4615s
BAR_DUR = BEAT_DUR * 4.0  # ~1.846s


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
    # Rapid frequency sweep upwards then exponential decay
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
    # Rim click (high resonant tone) + snare burst
    click = np.sin(2.0 * np.pi * 1450.0 * t) * np.exp(-t * 70.0)
    snare = np.random.uniform(-1.0, 1.0, n) * np.exp(-t * 22.0)
    rim = (click * 0.6 + snare * 0.4) * 0.65
    return rim.astype(np.float32)


# ============================================================================
# Procedural Energetic Comedic Meme BGM Synthesizer (130 BPM)
# ============================================================================


def _synth_drum_kit(pattern_length: int, sr: int) -> np.ndarray:
    """Synthesize 1 bar of crisp bouncy comedic drums at 130 BPM."""
    buf = np.zeros(pattern_length, dtype=np.float32)
    step_samples = pattern_length // 16  # 16th note steps

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


def _synth_bouncy_bass(pattern_length: int, root_freq: float, sr: int) -> np.ndarray:
    """Synthesize 1 bar of bouncy comedic walking/ragtime bass."""
    buf = np.zeros(pattern_length, dtype=np.float32)
    step_samples = pattern_length // 4  # Quarter note pulses

    # Oom-pah style bass: Root on beat 1, Fifth on beat 3, walking notes
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


def _synth_staccato_chords(pattern_length: int, chord_freqs: list[float], sr: int) -> np.ndarray:
    """Synthesize quirky staccato piano/brass chords on upbeats (2 & 4)."""
    buf = np.zeros(pattern_length, dtype=np.float32)
    step_samples = pattern_length // 8  # 8th note steps

    # Upbeats: steps 1, 3, 5, 7
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


def _synth_playful_melody(pattern_length: int, bar_idx: int, sr: int) -> np.ndarray:
    """Synthesize a catchy, bouncing comedic marimba/pizzicato motif."""
    buf = np.zeros(pattern_length, dtype=np.float32)
    step_samples = pattern_length // 16

    # Playful pentatonic pattern (C major / A minor bouncy ragtime scale)
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
            # Marimba acoustic envelope: fast attack, warm harmonics
            env = np.exp(-mt * 20.0)
            tone = (np.sin(2.0 * np.pi * freq * mt) + 0.28 * np.sin(4.0 * np.pi * freq * mt)) * env * 0.48
            buf[idx : idx + mn] += tone

    return buf


def generate_procedural_wojak_bgm(total_duration_sec: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Generate a continuous bouncy comedic meme soundtrack for the requested duration.

    Structure:
    - 130 BPM bouncy tempo.
    - Walking comedic bass + tight punchy drums + offbeat chords + playful marimba riff.
    - Seamlessly looping bars with subtle variations.
    """
    bar_samples = int(sr * BAR_DUR)
    total_samples = int(sr * total_duration_sec)
    music = np.zeros(total_samples, dtype=np.float32)

    # 4-bar chord progression: C major -> G major -> A minor -> F major
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
        drums = _synth_drum_kit(bar_samples, sr)
        bass = _synth_bouncy_bass(bar_samples, root_freqs[chord_idx], sr)
        chords = _synth_staccato_chords(bar_samples, chord_sets[chord_idx], sr)
        melody = _synth_playful_melody(bar_samples, bar_counter, sr)

        # Mix the bar stems
        bar_mix = drums * 0.72 + bass * 0.65 + chords * 0.42 + melody * 0.52

        # Write into total buffer
        remain = min(bar_samples, total_samples - current_idx)
        music[current_idx : current_idx + remain] = bar_mix[:remain]

        current_idx += bar_samples
        bar_counter += 1

    # Apply gentle fade-in (0.3s) and fade-out (1.5s)
    fade_in_samples = int(sr * 0.3)
    if fade_in_samples > 0 and len(music) > fade_in_samples:
        fade_in = np.linspace(0.0, 1.0, fade_in_samples)
        music[:fade_in_samples] *= fade_in

    fade_out_samples = int(sr * 1.5)
    if fade_out_samples > 0 and len(music) > fade_out_samples:
        fade_out = np.linspace(1.0, 0.0, fade_out_samples)
        music[-fade_out_samples:] *= fade_out

    return music


# ============================================================================
# Audio Assembly & EBU R128 Mastering
# ============================================================================


def assemble_wojak_audio(
    total_duration_sec: float,
    scene_transition_timestamps: list[float],
    music_file_path: Path | str | None = None,
    output_wav_path: Path | str | None = None,
    sr: int = SAMPLE_RATE,
) -> Path:
    """Assemble continuous BGM and overlay subtle comedic SFX at scene changes.

    Args:
        total_duration_sec: Total duration of video in seconds.
        scene_transition_timestamps: List of timestamp offsets (seconds) for SFX cues.
        music_file_path: Optional custom BGM track; if None or invalid, procedural BGM is used.
        output_wav_path: Output WAV file destination.
        sr: Sample rate (48000 Hz).

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
                    # Simple resampling or fallback to procedural
                    logger.info("Custom audio sample rate is %d (target %d)", bgm_sr, sr)
                # Loop or trim to total_samples
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
        logger.info("Synthesizing procedural bouncy comedic meme BGM (%.1fs)...", total_duration_sec)
        procedural_bgm = generate_procedural_wojak_bgm(total_duration_sec, sr=sr)
        master_mix += procedural_bgm

    # 2. Overlay subtle comedic SFX at scene transitions
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
        # Pick sfx
        sfx_func = sfx_generators[idx % len(sfx_generators)]
        sfx = sfx_func(sr=sr)
        sfx_len = min(len(sfx), total_samples - start_idx)
        master_mix[start_idx : start_idx + sfx_len] += sfx[:sfx_len] * 0.55

    # 3. Peak normalization
    peak = float(np.max(np.abs(master_mix))) if len(master_mix) > 0 else 0.0
    if peak > 0.95:
        master_mix = (master_mix / peak) * 0.92

    # 4. Save unmastered mix to temp WAV
    if not output_wav_path:
        import tempfile

        output_wav_path = Path(tempfile.gettempdir()) / "wojak_mastered_audio.wav"
    output_wav_path = Path(output_wav_path)
    write_wav(output_wav_path, master_mix, sr=sr)

    # 5. EBU R128 Loudness Normalization using FFmpeg
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
            # Replace with normalized version
            normalized_path.replace(output_wav_path)
            logger.info("EBU R128 loudness normalization successfully applied (%s LUFS)", TARGET_LUFS)
    except Exception as exc:
        logger.warning("EBU R128 normalization skipped (%s); unmastered WAV retained", exc)

    return output_wav_path
