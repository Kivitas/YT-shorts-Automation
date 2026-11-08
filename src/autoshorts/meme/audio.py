"""Dynamic Audio Mood Matching, Procedural Music & SFX Synthesis for Meme Shorts.

Handles:
1. Dynamic Audio Mood Matching Engine (playful, quirky, epic orchestral, suspense, punchline cut, etc.)
2. Deterministic Procedural SFX Synthesis (record scratch, 808 bass drop, cartoon impact, whoosh, rimshot, stinger, awkward tick)
3. Procedural BGM Synthesizer and Asset Loader with sudden comedic punchline mute cuts
4. Per-segment TTS narration (Edge-TTS with Kokoro fallback) and multi-stem audio assembly with EBU R128 mastering.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import wave
from pathlib import Path
from typing import Any

import numpy as np

from autoshorts.media.audio_master import normalize_two_pass
from autoshorts.media.encoder_probe import get_ffmpeg_path
from autoshorts.meme.types import MemeScript, MemeSegment, MemeSegmentAudio

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
_MAX_INT16 = 32767

# Default TTS Voice for Meme Narrations
DEFAULT_MEME_VOICE = "en-US-ChristopherNeural"
MEME_EDGE_RATE = "+12%"

# Comedic timing parameters (in seconds)
DEFAULT_SEGMENT_GAP = 0.28
PUNCHLINE_MUTE_PRE_ROLL = 0.30  # sudden silence duration before punchline SFX hits
PUNCHLINE_SFX_OFFSET = 0.05
DEFAULT_SPEECH_DUCK_GAIN = 0.28
DEFAULT_BGM_FULL_GAIN = 0.72
SFX_MIX_GAIN = 0.85
LOUDNESS_TARGET_LUFS = -14.0

__all__ = [
    "SAMPLE_RATE",
    "MemeSegmentAudio",
    "synth_record_scratch",
    "synth_bass_drop",
    "synth_cartoon_impact",
    "synth_whoosh",
    "synth_rimshot",
    "synth_dramatic_stinger",
    "synth_awkward_tick",
    "generate_meme_sfx_library",
    "resolve_music_mood",
    "resolve_sfx_cue",
    "synth_procedural_bgm",
    "get_or_create_bgm",
    "synthesize_meme_narration",
    "assemble_meme_audio",
    "synthesize_meme_audio",
]


# ============================================================================
# Low-Level Audio Helper Utilities
# ============================================================================


def _write_wav(path: Path | str, samples: np.ndarray, sr: int = SAMPLE_RATE) -> Path:
    """Save float32 samples in [-1.0, 1.0] to a mono 16-bit PCM WAV file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(arr))) if len(arr) > 0 else 0.0
    if peak > 1.0:
        arr = arr / peak
    int_samples = np.clip(np.round(arr * _MAX_INT16), -_MAX_INT16, _MAX_INT16).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(int_samples.tobytes())
    return path


def load_audio_as_mono_48k(path: Path) -> np.ndarray:
    """Load any audio file (MP3, WAV, etc.) as a float32 mono array at 48000 Hz."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return np.zeros(0, dtype=np.float32)

    # If it is already a 48kHz mono 16-bit WAV, read directly
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                nch = w.getnchannels()
                width = w.getsampwidth()
                rate = w.getframerate()
                nframes = w.getnframes()
                data = w.readframes(nframes)
            if width == 2 and rate == SAMPLE_RATE:
                raw = np.frombuffer(data, dtype=np.int16).astype(np.float32) / _MAX_INT16
                if nch > 1:
                    raw = raw.reshape(-1, nch).mean(axis=1)
                return raw
        except Exception:
            pass

    # Use ffmpeg to convert to raw PCM float32 or 16-bit
    ffmpeg_bin = get_ffmpeg_path()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-ar",
        str(SAMPLE_RATE),
        "-ac",
        "1",
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=True)
        return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / _MAX_INT16
    except Exception as exc:
        logger.warning("ffmpeg audio decode failed for %s: %s", path, exc)
        return np.zeros(0, dtype=np.float32)


# ============================================================================
# Comedic SFX Synthesis (Pure Math / Numpy, 48kHz 16-bit Mono)
# ============================================================================


def synth_record_scratch(path: Path, duration: float = 0.45) -> Path:
    """High frequency pitch slide with filtered noise burst and vinyl drag.

    Simulates a DJ scratching the record back and forth with needle friction,
    direction reversal flick, and needle lift release.
    """
    sr = SAMPLE_RATE
    n = max(1, int(round(duration * sr)))
    t = np.linspace(0.0, duration, n, endpoint=False)

    # Trajectory: forward flick (0.0 - 0.20s), sharp reverse (0.20 - 0.38s), lift (0.38 - 0.45s)
    freq = np.zeros(n, dtype=np.float32)
    p1 = t < 0.20
    freq[p1] = 900.0 + 2600.0 * (t[p1] / 0.20) ** 1.8
    p2 = (t >= 0.20) & (t < 0.38)
    freq[p2] = 3500.0 * np.exp(-12.0 * (t[p2] - 0.20)) + 300.0
    p3 = t >= 0.38
    freq[p3] = 250.0 * np.exp(-16.0 * (t[p3] - 0.38))

    phase = 2.0 * np.pi * np.cumsum(freq) / sr
    carrier = np.sin(phase) + 0.35 * np.sin(2.0 * phase)

    # Resonant vinyl friction noise modulated by carrier frequency
    rng = np.random.RandomState(42)
    raw_noise = rng.uniform(-1.0, 1.0, n).astype(np.float32)
    friction = raw_noise * np.sin(phase)

    # Vinyl needle crackle clicks
    crackle = np.zeros(n, dtype=np.float32)
    clicks = rng.choice(n, size=min(n, int(duration * 90)), replace=False)
    crackle[clicks] = rng.uniform(-0.5, 0.5, size=len(clicks))

    # Envelope
    env = np.ones(n, dtype=np.float32)
    att_len = int(0.015 * sr)
    if att_len > 0:
        env[:att_len] = np.linspace(0.0, 1.0, att_len)
    tail_start = int(0.38 * sr)
    if tail_start < n:
        env[tail_start:] = np.maximum(0.0, 1.0 - (t[tail_start:] - 0.38) / 0.07)

    mix = (0.50 * carrier + 0.40 * friction + 0.10 * crackle) * env
    signal = np.tanh(1.8 * mix)
    return _write_wav(path, signal, sr)


def synth_bass_drop(path: Path, duration: float = 1.3) -> Path:
    """Resonant 808 sub-bass drop sweeping from 140 Hz down to 36 Hz with punchy transient.

    Features a punchy transient click, exponential sub-bass frequency sweep,
    warm harmonic saturation, and smooth sub decay.
    """
    sr = SAMPLE_RATE
    dur = float(duration)
    n = max(1, int(round(dur * sr)))
    t = np.linspace(0.0, dur, n, endpoint=False)

    f0, f_end = 140.0, 36.0
    decay_rate = 5.0
    # Integral of f(tau) dtau = f_end * t + (f0 - f_end) * dur / decay_rate * (1 - exp(-decay_rate * t / dur))
    int_f = f_end * t + ((f0 - f_end) * dur / decay_rate) * (1.0 - np.exp(-decay_rate * t / dur))
    phase = 2.0 * np.pi * int_f

    sub = np.sin(phase) + 0.28 * np.sin(2.0 * phase) + 0.12 * np.sin(3.0 * phase)

    # Punchy transient click at start (beater strike)
    trans_decay = np.exp(-t / 0.007)
    transient = trans_decay * np.sin(2.0 * np.pi * (260.0 - 160.0 * np.minimum(1.0, t / 0.03)) * t)

    # Amplitude envelope: 2ms attack, warm exponential decay
    att_samples = int(0.003 * sr)
    env = np.exp(-2.3 * t / dur)
    if att_samples > 0:
        env[:att_samples] *= np.linspace(0.0, 1.0, att_samples)

    raw = (sub + 0.55 * transient) * env
    dist = np.tanh(1.85 * raw)
    return _write_wav(path, dist, sr)


def synth_cartoon_impact(path: Path, duration: float = 0.45) -> Path:
    """Punchy transient pop + pitched cartoon bonk / spring sound.

    Generates a crisp transient smack followed by a rapid FM spring boing
    with harmonic overtone resonances.
    """
    sr = SAMPLE_RATE
    dur = float(duration)
    n = max(1, int(round(dur * sr)))
    t = np.linspace(0.0, dur, n, endpoint=False)

    # Transient pop
    trans_env = np.exp(-t / 0.006)
    rng = np.random.RandomState(88)
    trans = trans_env * (np.sin(2.0 * np.pi * 320.0 * t) + 0.4 * rng.uniform(-1.0, 1.0, n))

    # Spring cartoon boing
    # Pitch bend from 520 Hz to 220 Hz
    int_f = 220.0 * t + (300.0 * dur / 7.0) * (1.0 - np.exp(-7.0 * t / dur))
    # Spring wobble LFO at 28 Hz
    lfo_depth = 4.2 * np.exp(-5.5 * t / dur)
    phase = 2.0 * np.pi * int_f + lfo_depth * np.sin(2.0 * np.pi * 28.0 * t)

    boing = np.sin(phase) + 0.35 * np.sin(2.0 * phase) + 0.15 * np.sin(3.2 * phase)
    boing_env = np.exp(-4.8 * t / dur)
    att_samples = int(0.004 * sr)
    if att_samples > 0:
        boing_env[:att_samples] *= np.linspace(0.0, 1.0, att_samples)

    signal = 0.45 * trans + 0.70 * boing * boing_env
    signal = np.tanh(1.5 * signal)
    return _write_wav(path, signal, sr)


def synth_whoosh(path: Path, duration: float = 0.35) -> Path:
    """Bandpassed noise sweep for rapid anime motion cuts and speed transitions.

    Resonant swept filter from 220 Hz to 880 Hz and back with a smooth bell envelope.
    """
    sr = SAMPLE_RATE
    dur = max(0.05, float(duration))
    n = max(1, int(round(dur * sr)))
    t = np.linspace(0.0, dur, n, endpoint=False)

    env = (np.maximum(0.0, np.sin(np.pi * t / dur))) ** 1.6
    f_center = 220.0 + 660.0 * (np.maximum(0.0, np.sin(np.pi * t / dur)) ** 1.2)
    phase = 2.0 * np.pi * np.cumsum(f_center) / sr

    rng = np.random.RandomState(101)
    noise = rng.uniform(-1.0, 1.0, n).astype(np.float32)

    signal = (0.72 * noise * np.sin(phase) + 0.28 * np.sin(phase)) * env
    signal = np.tanh(1.4 * signal)
    return _write_wav(path, signal, sr)


def synth_rimshot(path: Path, duration: float = 0.85) -> Path:
    """Classic stand-up comedy ba-dum-tss punchline sting.

    Comprises:
    1. 'Ba' high tom / snare hit at t=0.0s
    2. 'Dum' resonant low tom at t=0.20s
    3. 'Tss' high-pass crash/splash cymbal at t=0.38s
    """
    sr = SAMPLE_RATE
    dur = float(duration)
    n = max(1, int(round(dur * sr)))
    t = np.linspace(0.0, dur, n, endpoint=False)
    sig = np.zeros(n, dtype=np.float32)
    rng = np.random.RandomState(77)

    # 1. "Ba" at t=0.0s
    m1 = t < 0.18
    t1 = t[m1]
    if len(t1) > 0:
        drum1 = np.sin(2.0 * np.pi * (180.0 - 60.0 * (t1 / 0.18)) * t1) * np.exp(-22.0 * t1)
        click1 = rng.uniform(-1.0, 1.0, len(t1)) * np.exp(-t1 / 0.005)
        sig[m1] += 0.65 * drum1 + 0.35 * click1

    # 2. "Dum" at t=0.20s
    m2 = (t >= 0.20) & (t < 0.38)
    t2 = t[m2] - 0.20
    if len(t2) > 0:
        drum2 = np.sin(2.0 * np.pi * (135.0 - 45.0 * (t2 / 0.18)) * t2) * np.exp(-18.0 * t2)
        click2 = rng.uniform(-1.0, 1.0, len(t2)) * np.exp(-t2 / 0.005)
        sig[m2] += 0.70 * drum2 + 0.30 * click2

    # 3. "Tss" at t=0.38s
    m3 = t >= 0.38
    t3 = t[m3] - 0.38
    if len(t3) > 0:
        raw_noise = rng.uniform(-1.0, 1.0, len(t3))
        # High-pass difference
        cym_noise = np.diff(np.pad(raw_noise, (1, 0)))
        metallic = (
            0.20 * np.sin(2.0 * np.pi * 5400.0 * t3)
            + 0.15 * np.sin(2.0 * np.pi * 7800.0 * t3)
            + 0.10 * np.sin(2.0 * np.pi * 9200.0 * t3)
        )
        cym_env = np.exp(-6.2 * t3)
        sig[m3] += (0.75 * cym_noise + 0.25 * metallic) * cym_env * 0.95

    sig = np.tanh(1.5 * sig)
    return _write_wav(path, sig, sr)


def synth_dramatic_stinger(path: Path, duration: float = 1.8) -> Path:
    """Suspenseful dark chord stinger (Inception / anime shock brass).

    Low minor cluster with detuned unison sawtooth brass, low-pass filter sweep,
    and resonant sub-bass thump.
    """
    sr = SAMPLE_RATE
    dur = float(duration)
    n = max(1, int(round(dur * sr)))
    t = np.linspace(0.0, dur, n, endpoint=False)

    # Frequencies: C1 (32.7 Hz), C2 (65.4 Hz), Eb2 (77.8 Hz), G2 (98.0 Hz)
    freqs = [32.7, 65.4, 77.78, 98.0]
    weights = [0.45, 0.50, 0.40, 0.35]

    brass = np.zeros(n, dtype=np.float32)
    for f, w in zip(freqs, weights, strict=False):
        for h in range(1, 9):
            amp = (1.0 / h) * np.exp(-0.25 * h)
            detune = 1.0 + 0.003 * (h % 3 - 1)
            brass += w * amp * np.sin(2.0 * np.pi * (f * h * detune) * t)

    # Sub impact transient
    trans = np.sin(2.0 * np.pi * (110.0 - 55.0 * np.minimum(1.0, t / 0.08)) * t) * np.exp(-t / 0.05)

    # Brass swell attack + exponential decay
    env = np.ones(n, dtype=np.float32)
    att = int(0.035 * sr)
    if att > 0:
        env[:att] = np.linspace(0.0, 1.0, att)
    env[att:] = np.exp(-1.8 * (t[att:] - 0.035) / dur)

    # Filter sweep: low-pass simulation
    filter_mod = 1.0 + 1.2 * np.exp(-3.0 * t / dur)
    signal = np.tanh(2.0 * (brass * filter_mod * env + 0.6 * trans))
    return _write_wav(path, signal, sr)


def synth_awkward_tick(path: Path, duration: float = 0.9) -> Path:
    """Awkward clock tick / cricket chirp silence breaker.

    Generates a dry resonant clock tick, an awkward pause, and a faint second click / chirp.
    """
    sr = SAMPLE_RATE
    dur = float(duration)
    n = max(1, int(round(dur * sr)))
    t = np.linspace(0.0, dur, n, endpoint=False)
    sig = np.zeros(n, dtype=np.float32)

    # Tick 1 at t=0.05s
    m1 = (t >= 0.05) & (t < 0.15)
    t1 = t[m1] - 0.05
    if len(t1) > 0:
        click = np.sin(2.0 * np.pi * 2400.0 * t1) + 0.5 * np.sin(2.0 * np.pi * 4800.0 * t1)
        sig[m1] += click * np.exp(-t1 / 0.006)

    # Second softer tick / cricket chirp at t=0.45s
    m2 = (t >= 0.45) & (t < 0.65)
    t2 = t[m2] - 0.45
    if len(t2) > 0:
        chirp = np.sin(2.0 * np.pi * 4500.0 * t2) * (0.5 + 0.5 * np.sin(2.0 * np.pi * 40.0 * t2))
        sig[m2] += chirp * np.exp(-t2 / 0.04) * 0.45

    sig = np.clip(sig, -1.0, 1.0)
    return _write_wav(path, sig, sr)


def generate_meme_sfx_library(out_dir: Path) -> dict[str, Path]:
    """Generate all standard comedic meme sound effects into `out_dir`.

    Returns:
        Mapping of cue name -> generated WAV file path.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    library: dict[str, Path] = {
        "record_scratch": synth_record_scratch(out_dir / "record_scratch.wav"),
        "bass_drop": synth_bass_drop(out_dir / "bass_drop.wav"),
        "cartoon_impact": synth_cartoon_impact(out_dir / "cartoon_impact.wav"),
        "whoosh": synth_whoosh(out_dir / "whoosh.wav"),
        "rimshot": synth_rimshot(out_dir / "rimshot.wav"),
        "dramatic_stinger": synth_dramatic_stinger(out_dir / "dramatic_stinger.wav"),
        "stinger": out_dir / "dramatic_stinger.wav",
        "awkward_tick": synth_awkward_tick(out_dir / "awkward_tick.wav"),
        "tick": out_dir / "awkward_tick.wav",
    }
    return library


# ============================================================================
# Dynamic Mood Matching Engine
# ============================================================================

_VALID_MOODS = {
    "playful",
    "quirky",
    "epic_orchestral",
    "suspense",
    "punchline_cut",
    "unexpected_reveal",
    "lofi_chill",
}

_VALID_SFX = {
    "record_scratch",
    "bass_drop",
    "cartoon_impact",
    "whoosh",
    "rimshot",
    "dramatic_stinger",
    "stinger",
    "awkward_tick",
    "none",
}


def resolve_music_mood(segment: MemeSegment) -> str:
    """Infer the most comedic background music mood for a segment.

    Analyzes explicit fields, character expressions, actions, and narrative scenario.
    If music_mood is set to a specific non-default mood, respects it; otherwise
    performs intelligent thematic inference.
    """
    explicit = (getattr(segment, "music_mood", "") or "").strip().lower()
    # If explicitly set to something other than generic 'playful', respect it immediately
    if explicit in _VALID_MOODS and explicit != "playful":
        return explicit

    text = f"{segment.heading} {segment.narration} {segment.scenario} {segment.character_description}".lower()
    expression = (getattr(segment, "expression", "") or "").lower()
    pose = (getattr(segment, "pose", "") or "").lower()

    # 1. Punchline cut (highest comedy priority)
    if any(
        k in text
        for k in [
            "punchline",
            "wait what",
            "hold up",
            "plot twist",
            "realize",
            "suddenly",
            "turns out",
            "wrong house",
        ]
    ):
        return "punchline_cut"

    # 2. Awkward / Cringe / Silence / Nervous
    if any(
        k in text or k in expression
        for k in [
            "awkward",
            "cringe",
            "nervous",
            "embarrass",
            "silence",
            "pause",
            "sweat",
            "deadpan",
            "stare",
            "hesitant",
        ]
    ):
        return "quirky"

    # 3. Disaster / Panic / Collapse
    if any(
        k in text or k in expression
        for k in [
            "disaster",
            "panic",
            "explosion",
            "fire",
            "screaming",
            "caught",
            "ruined",
            "failed",
            "crash",
            "chaos",
        ]
    ):
        return "suspense"

    # 4. Lo-fi chill / Cozy / Sleeping
    if any(
        k in text or k in pose
        for k in [
            "chill",
            "sleeping",
            "cozy",
            "relax",
            "peaceful",
            "lofi",
            "vibing",
            "bed",
            "couch",
        ]
    ):
        return "lofi_chill"

    # 5. Unexpected reveal
    if any(k in text for k in ["reveal", "secret", "unexpected", "shocking", "truth", "unveils"]):
        return "unexpected_reveal"

    # 6. Overconfident / Gigachad / Sigma / Epic flex
    if any(
        k in text
        for k in [
            "gigachad",
            "sigma",
            "heroic",
            "epic",
            "alpha",
            "invincible",
            "flex",
            "overconfident",
            "chad",
        ]
    ) or ("smug" in expression and "awkward" not in text and "nervous" not in text):
        return "epic_orchestral"

    # Default: playful upbeat comedic music
    return "playful"


def resolve_sfx_cue(segment: MemeSegment, mood: str | None = None) -> str:
    """Determine the optimal sound effect cue for the segment."""
    explicit = (getattr(segment, "sfx_cue", "") or "").strip().lower()
    if explicit in _VALID_SFX and explicit not in ("cartoon_impact", ""):
        return explicit

    text = f"{segment.heading} {segment.narration} {segment.scenario} {segment.character_description}".lower()
    anim = (getattr(segment, "animation_type", "") or "").lower()

    if any(
        k in text
        for k in ["record scratch", "freeze", "hold up", "wait a minute", "hol up", "wait what"]
    ):
        return "record_scratch"
    if any(
        k in text for k in ["boom", "vine boom", "gigachad", "bass drop", "earthquake", "sigma"]
    ) or anim in ("shake", "screen_shake", "earthquake"):
        return "bass_drop"
    if any(k in text for k in ["bonk", "slap", "hit", "punch", "smack", "pop"]) or anim in (
        "impact_pop",
        "bounce",
        "drop",
    ):
        return "cartoon_impact"
    if any(
        k in text for k in ["whoosh", "dash", "disappear", "speed", "fly by", "flash"]
    ) or anim in ("whip_pan", "fast_zoom", "whoosh"):
        return "whoosh"
    if any(k in text for k in ["joke", "pun", "badum", "tss", "haha", "punchline"]):
        return "rimshot"
    if any(
        k in text
        for k in [
            "shock",
            "horror",
            "dark",
            "scary",
            "stinger",
            "braam",
            "disaster",
            "fire",
            "explosion",
        ]
    ):
        return "dramatic_stinger"
    if any(k in text for k in ["tick", "cricket", "awkward", "silence", "..."]):
        return "awkward_tick"

    # Mood-guided defaults
    active_mood = mood or resolve_music_mood(segment)
    if active_mood == "punchline_cut":
        return "rimshot"
    if active_mood in ("unexpected_reveal", "epic_orchestral"):
        return "bass_drop"
    if active_mood == "suspense":
        return "dramatic_stinger"
    if active_mood == "quirky":
        return "cartoon_impact"

    return "cartoon_impact"


# ============================================================================
# Procedural BGM Synthesizer and Asset Loader
# ============================================================================


def _synth_pulse_wave(phase: np.ndarray, duty: float = 0.35) -> np.ndarray:
    """Generate a pulse waveform with given duty cycle."""
    p = (phase / (2.0 * np.pi)) % 1.0
    return np.where(p < duty, 1.0, -1.0)


def synth_procedural_bgm(mood: str, duration: float, path: Path) -> Path:
    """Synthesize deterministic, offline procedural BGM loops with pure numpy waveforms.

    Produces tailored musical textures for every mood:
    - playful: 128 BPM bouncy 8-bit/synth pentatonic arpeggio & walking bass
    - quirky: 115 BPM pizzicato staccato plucks & chromatic walking bass
    - epic_orchestral: 130 BPM brass-like chords & driving timpani pulse
    - suspense: 70 BPM dark drone, 48Hz pulsing sub-bass & tense dissonant trill
    - lofi_chill: 80 BPM warm electric piano 7th chords with vinyl flutter
    - unexpected_reveal: dramatic crescendo riser leading to abrupt silence
    - punchline_cut: bouncy comedic motif with abrupt punchline cutoff
    """
    path = Path(path)
    sr = SAMPLE_RATE
    dur = max(0.5, float(duration))
    n = max(1, int(round(dur * sr)))
    t = np.linspace(0.0, dur, n, endpoint=False)

    mood = mood.lower().strip()
    if mood not in _VALID_MOODS:
        mood = "playful"

    signal = np.zeros(n, dtype=np.float32)

    if mood == "playful":
        # 128 BPM: beat = 60/128 = 0.46875s; 16th note = 0.1171875s
        bpm = 128.0
        sec_per_16th = (60.0 / bpm) / 4.0
        # Pentatonic notes: C4, E4, G4, A4, C5, A4, G4, E4
        notes = [261.63, 329.63, 392.00, 440.00, 523.25, 440.00, 392.00, 329.63]
        total_16ths = int(np.ceil(dur / sec_per_16th))
        for step in range(total_16ths):
            t_start = step * sec_per_16th
            t_end = min(dur, (step + 1) * sec_per_16th)
            idx = (t >= t_start) & (t < t_end)
            if not np.any(idx):
                continue
            t_rel = t[idx] - t_start
            f = notes[step % len(notes)]
            phase = 2.0 * np.pi * f * t_rel
            # Chiptune pulse wave with soft decay envelope
            pulse = _synth_pulse_wave(phase, duty=0.30)
            sine = np.sin(phase)
            env = np.exp(-14.0 * t_rel)
            signal[idx] += 0.45 * (0.6 * pulse + 0.4 * sine) * env

        # Walking bassline on quarter notes
        sec_per_beat = 60.0 / bpm
        bass_notes = [130.81, 164.81, 196.00, 174.61]  # C3, E3, G3, F3
        total_beats = int(np.ceil(dur / sec_per_beat))
        for beat in range(total_beats):
            t_start = beat * sec_per_beat
            t_end = min(dur, (beat + 1) * sec_per_beat)
            idx = (t >= t_start) & (t < t_end)
            if not np.any(idx):
                continue
            t_rel = t[idx] - t_start
            bf = bass_notes[beat % len(bass_notes)]
            b_phase = 2.0 * np.pi * bf * t_rel
            b_wave = np.sin(b_phase) + 0.3 * np.sin(2.0 * b_phase)
            b_env = np.exp(-4.5 * t_rel)
            signal[idx] += 0.40 * b_wave * b_env

    elif mood == "quirky":
        # 115 BPM: Pizzicato plucks & comedy sneaking bass
        bpm = 115.0
        sec_per_beat = 60.0 / bpm
        # Chromatic hopping bass: C3, Eb3, E3, F3, F#3, G3, Bb3, B3
        bass_freqs = [130.81, 155.56, 164.81, 174.61, 185.00, 196.00, 233.08, 246.94]
        total_beats = int(np.ceil(dur / sec_per_beat))
        for beat in range(total_beats):
            t_start = beat * sec_per_beat
            t_end = min(dur, (beat + 1) * sec_per_beat)
            idx = (t >= t_start) & (t < t_end)
            if not np.any(idx):
                continue
            t_rel = t[idx] - t_start
            f = bass_freqs[beat % len(bass_freqs)]
            # Pizzicato tone: short sine + triangle overtone with fast damping
            pluck = np.sin(2.0 * np.pi * f * t_rel) + 0.4 * np.sin(2.0 * np.pi * 2.0 * f * t_rel)
            env = np.exp(-12.0 * t_rel)
            signal[idx] += 0.55 * pluck * env

            # Offbeat woodblock click
            t_wood = t_start + 0.5 * sec_per_beat
            if t_wood < dur:
                w_idx = (t >= t_wood) & (t < min(dur, t_wood + 0.12))
                if np.any(w_idx):
                    w_rel = t[w_idx] - t_wood
                    wood = np.sin(2.0 * np.pi * 920.0 * w_rel) * np.exp(-35.0 * w_rel)
                    signal[w_idx] += 0.35 * wood

    elif mood == "epic_orchestral":
        # 130 BPM: Heroic brass chords and driving timpani roll
        # Progression: Dm (D3, F3, A3) -> F (F3, A3, C4) -> C (C3, E3, G3) -> G (G2, B2, D3)
        chords = [
            [146.83, 174.61, 220.00],  # Dm
            [174.61, 220.00, 261.63],  # F
            [130.81, 164.81, 196.00],  # C
            [98.00, 123.47, 146.83],  # G
        ]
        bar_len = 4.0 * (60.0 / 130.0)
        total_bars = int(np.ceil(dur / bar_len))
        for bar in range(total_bars):
            t_bar_start = bar * bar_len
            chord = chords[bar % len(chords)]
            for note_f in chord:
                # Rich brass simulation
                phase = 2.0 * np.pi * note_f * (t - t_bar_start)
                brass_voice = (
                    0.5 * np.sin(phase) + 0.3 * np.sin(2.0 * phase) + 0.2 * np.sin(3.0 * phase)
                )
                signal += 0.22 * brass_voice

        # Driving timpani hits on quarter notes
        sec_beat = 60.0 / 130.0
        for beat in range(int(np.ceil(dur / sec_beat))):
            t_b = beat * sec_beat
            idx = (t >= t_b) & (t < min(dur, t_b + 0.35))
            if np.any(idx):
                tr = t[idx] - t_b
                timp = np.sin(2.0 * np.pi * (65.0 - 20.0 * (tr / 0.35)) * tr) * np.exp(-9.0 * tr)
                signal[idx] += 0.45 * timp

    elif mood == "suspense":
        # 48 Hz ominous sub drone with 1.2 Hz heartbeat pulse
        sub_drone = np.sin(2.0 * np.pi * 48.0 * t) + 0.3 * np.sin(2.0 * np.pi * 48.7 * t)
        # Tension minor-second clash: 880 Hz & 932 Hz
        dissonance = 0.12 * (np.sin(2.0 * np.pi * 880.0 * t) + np.sin(2.0 * np.pi * 932.0 * t))
        dissonance *= 0.5 + 0.5 * np.sin(2.0 * np.pi * 3.0 * t)  # tremolo

        # Heartbeat pulse every 1.0s
        heartbeat = np.zeros(n, dtype=np.float32)
        total_beats = int(np.ceil(dur / 1.0))
        for b in range(total_beats):
            tb = b * 1.0
            idx = (t >= tb) & (t < min(dur, tb + 0.25))
            if np.any(idx):
                tr = t[idx] - tb
                heartbeat[idx] += np.sin(2.0 * np.pi * 55.0 * tr) * np.exp(-12.0 * tr)
        signal = 0.50 * sub_drone + dissonance + 0.40 * heartbeat

    elif mood == "lofi_chill":
        # 80 BPM warm electric piano 7th chords with tape flutter
        # Dm7 (D3, F3, A3, C4) -> G7 (G2, B2, D3, F3) -> Cmaj7 (C3, E3, G3, B3) -> Am7 (A2, C3, E3, G3)
        chords = [
            [146.83, 174.61, 220.00, 261.63],
            [98.00, 123.47, 146.83, 174.61],
            [130.81, 164.81, 196.00, 246.94],
            [110.00, 130.81, 164.81, 196.00],
        ]
        bar_len = 4.0 * (60.0 / 80.0)
        total_bars = int(np.ceil(dur / bar_len))
        for bar in range(total_bars):
            t_bar_start = bar * bar_len
            chord = chords[bar % len(chords)]
            for note_f in chord:
                # Soft organ/Rhodes tone with warm 4Hz tremolo
                phase = 2.0 * np.pi * note_f * (t - t_bar_start)
                tremolo = 0.85 + 0.15 * np.sin(2.0 * np.pi * 4.0 * t)
                voice = (0.7 * np.sin(phase) + 0.3 * np.sin(2.0 * phase)) * tremolo
                signal += 0.18 * voice

        # Vinyl noise floor
        rng = np.random.RandomState(99)
        vinyl = rng.uniform(-0.06, 0.06, n).astype(np.float32)
        signal += vinyl

    elif mood == "unexpected_reveal":
        # Rising tension sweep crescendo up to abrupt cutoff
        f_rise = 60.0 + 540.0 * (t / dur) ** 2.0
        phase = 2.0 * np.pi * np.cumsum(f_rise) / sr
        crescendo = (t / dur) ** 1.5
        riser = np.sin(phase) * crescendo
        # Sudden silence at the end (last 0.30s)
        mute_start = max(0.0, dur - PUNCHLINE_MUTE_PRE_ROLL)
        riser[t >= mute_start] = 0.0
        signal = 0.70 * riser

    elif mood == "punchline_cut":
        # Playful comedic beat that HARD-CUTS to complete silence 0.35s before end
        synth_temp = synth_procedural_bgm("playful", dur, path.with_suffix(".tmp.wav"))
        samples = load_audio_as_mono_48k(synth_temp)
        if synth_temp.is_file():
            synth_temp.unlink(missing_ok=True)
        mute_start = max(0.0, dur - PUNCHLINE_MUTE_PRE_ROLL)
        mute_idx = int(round(mute_start * sr))
        samples[mute_idx:] = 0.0
        signal = samples[:n]

    # Global soft limiting & normalization
    peak = float(np.max(np.abs(signal))) if len(signal) > 0 else 0.0
    if peak > 0:
        signal = 0.85 * (signal / peak)

    return _write_wav(path, signal, sr)


def get_or_create_bgm(
    mood: str,
    duration: float,
    work_dir: Path,
    custom_music_dir: Path | None = None,
) -> Path:
    """Load asset background music or synthesize procedural BGM loop.

    1. Checks user-configured custom directory or `assets/music/` for pre-existing audio.
    2. If found, loops or trims using ffmpeg to the target duration.
    3. If missing or on any failure, synthesizes deterministic procedural BGM waveforms.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    dst_wav = work_dir / f"bgm_{mood}_{int(duration * 100):05d}.wav"

    # Search for external assets
    search_dirs: list[Path] = []
    if custom_music_dir and Path(custom_music_dir).is_dir():
        search_dirs.append(Path(custom_music_dir))
    assets_dir = Path("assets/music")
    if assets_dir.is_dir():
        search_dirs.append(assets_dir)

    for sdir in search_dirs:
        for ext in [".wav", ".mp3", ".m4a", ".ogg"]:
            candidate = sdir / f"{mood}{ext}"
            if not candidate.is_file():
                candidate = sdir / f"bgm_{mood}{ext}"
            if candidate.is_file():
                try:
                    # Loop or trim with ffmpeg
                    ffmpeg_bin = get_ffmpeg_path()
                    cmd = [
                        ffmpeg_bin,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-stream_loop",
                        "-1",
                        "-i",
                        str(candidate),
                        "-t",
                        f"{duration:.3f}",
                        "-ar",
                        str(SAMPLE_RATE),
                        "-ac",
                        "1",
                        str(dst_wav),
                    ]
                    subprocess.run(cmd, check=True)
                    if dst_wav.is_file() and dst_wav.stat().st_size > 0:
                        return dst_wav
                except Exception as exc:
                    logger.warning(
                        "Failed to loop asset BGM %s: %s; falling back to synthesis", candidate, exc
                    )

    # Deterministic procedural synthesis fallback
    return synth_procedural_bgm(mood, duration, dst_wav)


# ============================================================================
# TTS & Per-Segment Narration
# ============================================================================


def _edge_tts(
    text: str,
    voice: str,
    audio_path: Path,
    words_path: Path,
    rate: str = MEME_EDGE_RATE,
) -> tuple[list[dict[str, Any]], float]:
    """Synthesize narration text using Edge TTS with word-level timestamps."""
    from autoshorts.research_agent import moneyprinter

    _path, words, duration = asyncio.run(
        moneyprinter._async_synthesize_edge_tts(text, voice, audio_path, words_path, rate=rate)
    )
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError("Edge TTS produced empty audio file")
    return list(words), float(duration)


def _kokoro_tts(text: str, voice: str, wav_path: Path) -> tuple[list[dict[str, Any]], float]:
    """Fallback local Kokoro TTS engine with FasterWhisper alignment."""
    from autoshorts.media.compositor import FasterWhisperTranscriber
    from autoshorts.media.kokoro_fallback import synthesize_kokoro

    out = Path(synthesize_kokoro(text, voice, wav_path))
    words = list(FasterWhisperTranscriber().align_words(out))
    try:
        with wave.open(str(out), "rb") as w:
            dur = w.getnframes() / float(w.getframerate())
    except Exception:
        dur = float(words[-1]["end"]) if words else 3.0
    return words, dur


def _procedural_mock_tts(text: str, wav_path: Path) -> tuple[list[dict[str, Any]], float]:
    """Ultra-resilient offline synthetic speech simulation for isolated testing environments."""
    words_list = text.split()
    sec_per_word = 0.32
    duration = max(1.2, len(words_list) * sec_per_word)
    sr = SAMPLE_RATE
    n = max(1, int(round(duration * sr)))
    t = np.linspace(0.0, duration, n, endpoint=False)

    # Formant-like pulsed speech tone
    signal = np.sin(2.0 * np.pi * 180.0 * t) * (0.6 + 0.4 * np.sin(2.0 * np.pi * 4.0 * t))
    _write_wav(wav_path, signal * 0.7, sr)

    words_meta = []
    curr = 0.05
    for w in words_list:
        words_meta.append({"word": w, "start": round(curr, 2), "end": round(curr + 0.28, 2)})
        curr += sec_per_word
    return words_meta, duration


def synthesize_meme_narration(
    script: MemeScript,
    work_dir: Path,
    voice: str | None = None,
) -> list[MemeSegmentAudio]:
    """Synthesize per-segment narration audio files and construct timing metadata."""
    work_dir = Path(work_dir)
    narration_dir = work_dir / "narration"
    narration_dir.mkdir(parents=True, exist_ok=True)

    chosen_voice = voice or DEFAULT_MEME_VOICE
    segment_audios: list[MemeSegmentAudio] = []
    cumulative_time = 0.0

    # Build full sequence: Intro (if present), Segments, Ending (if present)
    all_segments: list[MemeSegment] = []
    if (script.hook or script.intro) and not any(s.id == "intro" for s in script.segments):
        intro_text = f"{script.hook} {script.intro}".strip()
        all_segments.append(
            MemeSegment(
                id="intro",
                name="INTRO",
                heading=script.topic or script.title,
                narration=intro_text,
                scenario="Opening video hook",
                character_description="Excited host",
                pose="dramatic_heroic",
                expression="sparkling_eyes",
                background_theme=script.segments[0].background_theme
                if script.segments
                else "classroom",
                animation_type="zoom_in",
                music_mood="playful",
                sfx_cue="whoosh",
                subtitle_text=intro_text,
                speech_bubble_text=script.hook or "ARE YOU READY?!",
            )
        )
    all_segments.extend(script.segments)
    if script.ending and not any(s.id == "ending" for s in script.segments):
        all_segments.append(
            MemeSegment(
                id="ending",
                name="WHICH ONE ARE YOU?",
                heading="WHICH ONE ARE YOU?",
                narration=script.ending,
                scenario="Video closing & engagement CTA",
                character_description="Laughing character",
                pose="peace_sign",
                expression="crying_laughing",
                background_theme=script.segments[-1].background_theme
                if script.segments
                else "classroom",
                animation_type="zoom_out",
                music_mood="playful",
                sfx_cue="cartoon_impact",
                subtitle_text=script.ending,
                speech_bubble_text="DROP A COMMENT!",
            )
        )

    for i, seg in enumerate(all_segments):
        text = seg.narration.strip() or seg.heading.strip()
        base_name = f"seg_{i:02d}_{seg.id or 'meme'}"
        mp3_path = narration_dir / f"{base_name}.mp3"
        words_json = narration_dir / f"{base_name}_words.json"
        wav_path = narration_dir / f"{base_name}.wav"

        # 1. Edge TTS
        words: list[dict[str, Any]] = []
        duration = 0.0
        try:
            words, duration = _edge_tts(text, chosen_voice, mp3_path, words_json)
            # Convert to 48kHz mono WAV for exact sample assembly
            arr = load_audio_as_mono_48k(mp3_path)
            _write_wav(wav_path, arr)
        except Exception as edge_err:
            logger.warning(
                "Edge TTS failed for segment %d (%s); attempting Kokoro fallback.", i, edge_err
            )
            try:
                words, duration = _kokoro_tts(text, chosen_voice, wav_path)
            except Exception as kokoro_err:
                logger.warning(
                    "Kokoro fallback failed (%s); generating procedural speech dummy.", kokoro_err
                )
                words, duration = _procedural_mock_tts(text, wav_path)

        if duration <= 0:
            duration = float(words[-1]["end"]) if words else 3.0

        mood = resolve_music_mood(seg)
        sfx_cue = resolve_sfx_cue(seg, mood=mood)

        # Compute SFX trigger timestamp on master timeline
        start_time = cumulative_time
        if sfx_cue in ("record_scratch", "rimshot") or mood == "punchline_cut":
            # Hits right at or near punchline conclusion
            sfx_time = max(start_time, start_time + duration - 0.25)
        elif sfx_cue in ("whoosh", "bass_drop", "dramatic_stinger"):
            # Hits at scene entrance / impact
            sfx_time = start_time + 0.05
        elif sfx_cue == "awkward_tick":
            sfx_time = max(start_time, start_time + duration - 0.35)
        else:
            # cartoon_impact / general
            sfx_time = start_time + min(0.30, duration * 0.5)

        seg_audio = MemeSegmentAudio(
            segment=seg,
            audio_path=wav_path,
            words=words,
            start_time=round(start_time, 3),
            duration=round(duration, 3),
            music_mood=mood,
            sfx_cue=sfx_cue,
            sfx_time=round(sfx_time, 3),
        )
        segment_audios.append(seg_audio)

        # Advance cumulative time: add natural inter-segment comedic breathing gap
        gap = DEFAULT_SEGMENT_GAP
        if mood == "punchline_cut":
            gap += 0.20  # extra comedic pause after punchline
        cumulative_time += duration + gap

    return segment_audios


# ============================================================================
# Multi-Stem Audio Assembly & Loudness Mastering
# ============================================================================


def assemble_meme_audio(
    segment_audios: list[MemeSegmentAudio],
    total_duration: float,
    work_dir: Path,
    custom_music_dir: Path | None = None,
) -> Path:
    """Assemble Narration, Dynamic Ducked BGM, and SFX stems into a mastered track.

    - Narration track placed at unity 1.0 gain
    - BGM ducked under speech (0.28 gain), full during gaps (0.72 gain), abruptly muted on punchline cuts
    - SFX placed at exact visual cue moments
    - Masters uncompressed mix with EBU R128 (-14.0 LUFS target).
    """
    work_dir = Path(work_dir)
    sfx_dir = work_dir / "sfx"
    bgm_dir = work_dir / "bgm"
    sfx_library = generate_meme_sfx_library(sfx_dir)

    sr = SAMPLE_RATE
    total_samples = max(1, int(round(total_duration * sr)))

    narration_stem = np.zeros(total_samples, dtype=np.float32)
    sfx_stem = np.zeros(total_samples, dtype=np.float32)
    bgm_stem = np.zeros(total_samples, dtype=np.float32)
    bgm_envelope = np.full(total_samples, DEFAULT_BGM_FULL_GAIN, dtype=np.float32)

    # 1. Populate Narration Stem and Ducking Envelope
    for sa in segment_audios:
        start_samp = int(round(sa.start_time * sr))
        narr_arr = load_audio_as_mono_48k(sa.audio_path)
        end_samp = min(total_samples, start_samp + len(narr_arr))
        valid_len = end_samp - start_samp
        if valid_len > 0:
            narration_stem[start_samp:end_samp] += narr_arr[:valid_len]

            # Duck BGM gain during speech span with smooth ramp
            duck_start = start_samp
            duck_end = end_samp
            ramp_len = min(int(0.04 * sr), valid_len // 4)
            if ramp_len > 0:
                bgm_envelope[duck_start : duck_start + ramp_len] = np.linspace(
                    DEFAULT_BGM_FULL_GAIN, DEFAULT_SPEECH_DUCK_GAIN, ramp_len
                )
                bgm_envelope[duck_start + ramp_len : duck_end - ramp_len] = DEFAULT_SPEECH_DUCK_GAIN
                bgm_envelope[duck_end - ramp_len : duck_end] = np.linspace(
                    DEFAULT_SPEECH_DUCK_GAIN, DEFAULT_BGM_FULL_GAIN, ramp_len
                )
            else:
                bgm_envelope[duck_start:duck_end] = DEFAULT_SPEECH_DUCK_GAIN

        # Sudden music cuts: mute BGM abruptly on punchline beats (0.3s silence before punchline)
        if sa.music_mood == "punchline_cut" or sa.sfx_cue in ("rimshot", "record_scratch"):
            sfx_samp = int(round(sa.sfx_time * sr))
            cut_start = max(0, sfx_samp - int(round(PUNCHLINE_MUTE_PRE_ROLL * sr)))
            cut_end = min(total_samples, sfx_samp + int(round(0.12 * sr)))
            bgm_envelope[cut_start:cut_end] = 0.0

    # 2. Populate SFX Stem
    for sa in segment_audios:
        cue = sa.sfx_cue.lower().strip()
        if cue and cue != "none":
            sfx_file = sfx_library.get(cue) or sfx_library.get("cartoon_impact")
            if sfx_file and sfx_file.is_file():
                sfx_arr = load_audio_as_mono_48k(sfx_file)
                sfx_start = int(round(sa.sfx_time * sr))
                sfx_end = min(total_samples, sfx_start + len(sfx_arr))
                vlen = sfx_end - sfx_start
                if vlen > 0:
                    sfx_stem[sfx_start:sfx_end] += sfx_arr[:vlen] * SFX_MIX_GAIN

    # 3. Populate BGM Stem per segment mood
    for i, sa in enumerate(segment_audios):
        seg_start = sa.start_time
        seg_end = (
            segment_audios[i + 1].start_time if i + 1 < len(segment_audios) else total_duration
        )
        span_dur = max(0.5, seg_end - seg_start)
        bgm_file = get_or_create_bgm(
            sa.music_mood, span_dur, bgm_dir, custom_music_dir=custom_music_dir
        )
        bgm_arr = load_audio_as_mono_48k(bgm_file)
        start_samp = int(round(seg_start * sr))
        end_samp = min(total_samples, start_samp + len(bgm_arr))
        vlen = end_samp - start_samp
        if vlen > 0:
            bgm_stem[start_samp:end_samp] += bgm_arr[:vlen]

    # Apply dynamic ducking & mute envelope to BGM
    ducked_bgm = bgm_stem * bgm_envelope

    # 4. Multi-Stem Mixdown
    mixed = narration_stem * 1.0 + ducked_bgm + sfx_stem
    # Soft peak saturation to prevent clipping
    peak = float(np.max(np.abs(mixed))) if len(mixed) > 0 else 0.0
    if peak > 0.95:
        mixed = np.tanh(mixed / 0.95) * 0.95

    unmastered_path = work_dir / "unmastered_meme_audio.wav"
    _write_wav(unmastered_path, mixed, sr)

    # 5. EBU R128 Loudness Normalization (-14 LUFS target)
    mastered_path = work_dir / "meme_master_audio.wav"
    try:
        normalize_two_pass(unmastered_path, mastered_path, target_lufs=LOUDNESS_TARGET_LUFS)
        if mastered_path.is_file() and mastered_path.stat().st_size > 0:
            return mastered_path
    except Exception as exc:
        logger.warning("Two-pass loudness mastering failed (%s); keeping unmastered mix.", exc)

    return unmastered_path


def synthesize_meme_audio(
    script: MemeScript,
    work_dir: Path,
    voice: str | None = None,
    custom_music_dir: Path | None = None,
) -> tuple[Path, list[MemeSegmentAudio]]:
    """Synthesize complete multi-stem audio timeline for a meme script.

    Args:
        script: Full MemeScript dataclass instance with segment definitions.
        work_dir: Workspace directory for intermediate audio stems and outputs.
        voice: Optional Edge TTS voice string identifier.
        custom_music_dir: Optional directory containing custom mood music tracks.

    Returns:
        Tuple of (master_audio_path, segment_audios).
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    if not script.segments:
        raise ValueError("Cannot synthesize meme audio: MemeScript contains no segments")

    # 1. Synthesize narration and word alignment per segment
    segment_audios = synthesize_meme_narration(script, work_dir, voice=voice)

    # 2. Compute total timeline span
    last = segment_audios[-1]
    total_duration = last.start_time + last.duration + 0.40

    # 3. Assemble and master audio stems
    master_path = assemble_meme_audio(
        segment_audios=segment_audios,
        total_duration=total_duration,
        work_dir=work_dir,
        custom_music_dir=custom_music_dir,
    )

    return master_path, segment_audios
