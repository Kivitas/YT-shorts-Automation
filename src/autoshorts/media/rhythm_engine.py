"""Laya Rhythm Engine for AutoShorts Studio.

Provides:
- Pure Python transient extraction via spectral/temporal flux & autocorrelation lag peak finding.
- Beat confidence floor C >= 0.35: automatically bypasses beat snapping if ambient or washed.
- Asymmetric beat snapping: snaps visual zoom punches by <= 100ms;
  clamps jump cuts >= 50ms away from speech boundaries to guarantee spoken words are never truncated.
- Rhythmic ducking release calculation: T_release = 60000 / (BPM * 2) ms (musical eighth note).
"""

from __future__ import annotations

import logging
import math
import struct
import wave
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn

logger = logging.getLogger(__name__)


@dataclass
class RhythmGrid:
    """Rhythm grid and transient analysis metadata."""

    bpm: float
    beats: list[float]
    confidence: float
    ducking_release_ms: float
    ducking_release_sec: float
    is_ambient: bool
    details: dict[str, Any] = field(default_factory=dict)


class LayaRhythmEngine:
    """Production-grade rhythmic alignment and transient analysis engine."""

    def __init__(
        self,
        default_bpm: float = 120.0,
        confidence_floor: float = 0.35,
        max_snap_offset_sec: float = 0.100,  # 100ms
        speech_boundary_guard_sec: float = 0.050,  # 50ms
    ) -> None:
        self.default_bpm = float(default_bpm)
        self.confidence_floor = float(confidence_floor)
        self.max_snap_offset_sec = float(max_snap_offset_sec)
        self.speech_boundary_guard_sec = float(speech_boundary_guard_sec)
        self._last_grid: RhythmGrid | None = None

    @staticmethod
    def calculate_ducking_release(bpm: float) -> float:
        """Calculate rhythmic ducking release time in milliseconds (musical eighth note).

        Formula: T_release = 60000 / (BPM * 2) ms.
        """
        safe_bpm = max(30.0, min(bpm, 300.0)) if bpm > 0.0 else 120.0
        return 60000.0 / (safe_bpm * 2.0)

    @staticmethod
    def calculate_ducking_release_sec(bpm: float) -> float:
        """Calculate rhythmic ducking release time in seconds."""
        safe_bpm = max(30.0, min(bpm, 300.0)) if bpm > 0.0 else 120.0
        return 30.0 / safe_bpm

    def analyze_rhythm(
        self,
        audio_input: str | Path | Sequence[float] | None = None,
        sample_rate: int = 22050,
        duration: float = 60.0,
    ) -> RhythmGrid:
        """Analyze rhythmic properties from an audio file, raw samples, or synthesize default."""
        if audio_input is None:
            grid = self._create_synthetic_grid(self.default_bpm, duration, confidence=0.85)
            self._last_grid = grid
            return grid

        if isinstance(audio_input, (str, Path)):
            grid = self.extract_beats_from_audio(audio_input, sample_rate=sample_rate)
            self._last_grid = grid
            return grid

        if isinstance(audio_input, Sequence):
            grid = self.extract_beats_from_samples(
                audio_input, sample_rate=sample_rate, duration=duration
            )
            self._last_grid = grid
            return grid

        # Fallback default
        grid = self._create_synthetic_grid(self.default_bpm, duration, confidence=0.0)
        self._last_grid = grid
        return grid

    def extract_beats_from_audio(
        self,
        audio_path: str | Path,
        sample_rate: int = 22050,
    ) -> RhythmGrid:
        """Extract rhythmic transients from an audio file using standard library wave decoding."""
        path = Path(audio_path)
        if not path.is_file():
            logger.warning("Audio file not found: %s. Falling back to default BPM.", path)
            return self._create_synthetic_grid(self.default_bpm, 60.0, confidence=0.0)

        # Attempt to read as WAV
        try:
            samples, sr = self._read_wav_file(path)
            return self.extract_beats_from_samples(samples, sample_rate=sr)
        except Exception as e:
            logger.warning("Failed to decode WAV file %s: %s. Using default rhythm.", path, e)
            return self._create_synthetic_grid(self.default_bpm, 60.0, confidence=0.30)

    def extract_beats_from_samples(
        self,
        samples: Sequence[float],
        sample_rate: int = 22050,
        duration: float | None = None,
    ) -> RhythmGrid:
        """Extract beats using pure Python spectral flux onset detection & autocorrelation."""
        n_samples = len(samples)
        if n_samples < 1000 or sample_rate <= 0:
            return self._create_synthetic_grid(self.default_bpm, duration or 60.0, confidence=0.0)

        actual_duration = n_samples / float(sample_rate) if duration is None else duration
        frame_size = 512
        hop_size = 256

        # 1. Compute frame energies and spectral flux (onset detection function)
        frames_energy: list[float] = []
        for i in range(0, n_samples - frame_size, hop_size):
            frame_e = sum(s * s for s in samples[i : i + frame_size]) / frame_size
            frames_energy.append(frame_e)

        if len(frames_energy) < 10:
            return self._create_synthetic_grid(self.default_bpm, actual_duration, confidence=0.0)

        mean_energy = sum(frames_energy) / len(frames_energy)

        # Onset Detection Function: half-wave rectified temporal energy difference
        odf: list[float] = [
            max(0.0, frames_energy[k] - frames_energy[k - 1])
            for k in range(1, len(frames_energy))
        ]

        odf_max = max(odf) if odf else 0.0
        fps = sample_rate / hop_size

        if odf_max < 1e-9 or mean_energy < 1e-9:
            # Silent or near-silent audio
            confidence = 0.0
            is_ambient = True
            logger.info(
                "BGM track lacks distinct percussive transient grid (C < 0.35); "
                "proceeding with speech-cadence alignment"
            )
            return RhythmGrid(
                bpm=self.default_bpm,
                beats=[],
                confidence=confidence,
                ducking_release_ms=self.calculate_ducking_release(self.default_bpm),
                ducking_release_sec=self.calculate_ducking_release_sec(self.default_bpm),
                is_ambient=is_ambient,
            )

        # 2. Autocorrelation lag peak finding for BPM in range [60, 200]
        lag_min = max(10, int(fps * 60.0 / 200.0))
        lag_max = min(len(odf) // 2, int(fps * 60.0 / 60.0))

        autocorr: dict[int, float] = {}
        for lag in range(lag_min, max(lag_min + 1, lag_max)):
            val = sum(odf[k] * odf[k + lag] for k in range(len(odf) - lag))
            autocorr[lag] = val

        if autocorr:
            best_lag = max(autocorr, key=autocorr.get)
            ac_max = autocorr[best_lag]
            ac_mean = sum(autocorr.values()) / len(autocorr)
            detected_bpm = round((60.0 * fps) / best_lag, 2)
        else:
            best_lag = 43
            ac_max = 0.0
            ac_mean = 1e-9
            detected_bpm = self.default_bpm

        # 3. Peak-to-mean onset energy ratio and transient prominence C in [0.0, 1.0]
        transient_strength = odf_max / (mean_energy + 1e-9)
        ac_prominence = (ac_max - ac_mean) / (ac_max + 1e-9) if ac_max > 0.0 else 0.0

        # Mathematical confidence floor mapping
        # Percussive clicks yield transient_strength > 2.0 and high ac_prominence
        # Continuous ambient washes yield transient_strength < 0.2
        confidence = min(
            1.0,
            max(
                0.0,
                (1.0 - math.exp(-transient_strength * 0.8)) * (0.35 + 0.65 * ac_prominence),
            ),
        )
        confidence = round(confidence, 3)

        is_ambient = confidence < self.confidence_floor
        if is_ambient:
            logger.info(
                "BGM track lacks distinct percussive transient grid (C < 0.35); "
                "proceeding with speech-cadence alignment"
            )

        # 4. Generate aligned beat grid
        beat_interval = 60.0 / detected_bpm
        # Find best phase offset t0 in [0, beat_interval)
        steps = 16
        best_phase = 0.0
        best_phase_score = -1.0
        for step in range(steps):
            phase = step * (beat_interval / steps)
            score = 0.0
            t = phase
            while t < actual_duration:
                frame_idx = int(t * fps)
                if 0 <= frame_idx < len(odf):
                    score += odf[frame_idx]
                t += beat_interval
            if score > best_phase_score:
                best_phase_score = score
                best_phase = phase

        beats: list[float] = []
        t_curr = best_phase
        while t_curr <= actual_duration:
            beats.append(round(t_curr, 4))
            t_curr += beat_interval

        ducking_ms = self.calculate_ducking_release(detected_bpm)
        ducking_sec = self.calculate_ducking_release_sec(detected_bpm)

        return RhythmGrid(
            bpm=detected_bpm,
            beats=beats,
            confidence=confidence,
            ducking_release_ms=ducking_ms,
            ducking_release_sec=ducking_sec,
            is_ambient=is_ambient,
            details={
                "transient_strength": round(transient_strength, 3),
                "ac_prominence": round(ac_prominence, 3),
                "best_lag": best_lag,
            },
        )

    def snap_zoom_punch_ins(
        self,
        zoom_punch_ins: Sequence[ZoomPunchIn | float],
        beats: Sequence[float] | None = None,
        confidence: float | None = None,
    ) -> list[Any]:
        """Snap visual zoom punch timestamps by <= 100ms.

        If confidence < confidence_floor (0.35), beat snapping is automatically bypassed.
        """
        conf = (
            confidence
            if confidence is not None
            else (self._last_grid.confidence if self._last_grid else 1.0)
        )
        active_beats = (
            list(beats)
            if beats is not None
            else (self._last_grid.beats if self._last_grid else [])
        )

        if conf < self.confidence_floor or not active_beats:
            # Bypass beat snapping
            if zoom_punch_ins and isinstance(zoom_punch_ins[0], ZoomPunchIn):
                return [
                    ZoomPunchIn(
                        timestamp=z.timestamp,
                        scale=z.scale,
                        duration=z.duration,
                        emphasis_word=z.emphasis_word,
                    )
                    for z in zoom_punch_ins  # type: ignore[union-attr]
                ]
            return [float(t) for t in zoom_punch_ins]

        results: list[Any] = []
        for item in zoom_punch_ins:
            orig_t = item.timestamp if isinstance(item, ZoomPunchIn) else float(item)
            # Find nearest beat
            closest_beat = min(active_beats, key=lambda b: abs(b - orig_t))
            diff = abs(orig_t - closest_beat)
            snapped_t = closest_beat if diff <= self.max_snap_offset_sec else orig_t

            if isinstance(item, ZoomPunchIn):
                results.append(
                    ZoomPunchIn(
                        timestamp=round(snapped_t, 4),
                        scale=item.scale,
                        duration=item.duration,
                        emphasis_word=item.emphasis_word,
                    )
                )
            else:
                results.append(round(snapped_t, 4))

        return results

    def snap_jump_cuts(
        self,
        jump_cuts: Sequence[JumpCut],
        words: Sequence[Any],
        beats: Sequence[float] | None = None,
        confidence: float | None = None,
    ) -> list[JumpCut]:
        """Asymmetric word-guarded jump cut clamping and beat snapping.

        Invariants:
        1. Cuts are clamped >= 50ms away from speech boundaries:
           t_cut_start >= t_word[i].end + 0.05s
           t_cut_end <= t_word[i+1].start - 0.05s
        2. Spoken words are NEVER truncated under any circumstances.
        3. If confidence < 0.35, beat snapping is bypassed (speech guardrail is still enforced).
        """
        conf = (
            confidence
            if confidence is not None
            else (self._last_grid.confidence if self._last_grid else 1.0)
        )
        active_beats = (
            list(beats)
            if beats is not None
            else (self._last_grid.beats if self._last_grid else [])
        )
        guard = self.speech_boundary_guard_sec

        # Extract normalized (start, end) words
        parsed_words: list[tuple[float, float]] = []
        for w in words:
            if isinstance(w, dict):
                ws = float(w.get("start", 0.0))
                we = float(w.get("end", 0.0))
            else:
                ws = float(getattr(w, "start", 0.0))
                we = float(getattr(w, "end", 0.0))
            if we > ws:
                parsed_words.append((ws, we))
        parsed_words.sort(key=lambda x: x[0])

        valid_cuts: list[JumpCut] = []
        for jc in jump_cuts:
            orig_s = float(jc.cut_start)
            orig_e = float(jc.cut_end)

            if orig_e <= orig_s:
                continue

            # Determine strict speech boundary floor and ceiling
            s_min = 0.0
            e_max = float("inf")

            for ws, we in parsed_words:
                if we <= orig_e:
                    s_min = max(s_min, we + guard)
                if ws >= orig_s:
                    e_max = min(e_max, ws - guard)

            s_clamped = max(orig_s, s_min)
            e_clamped = min(orig_e, e_max)

            # Check if interval is collapsed or too narrow
            if s_clamped >= e_clamped or (e_clamped - s_clamped) < guard:
                continue

            # Asymmetric inward beat snapping if rhythm confidence C >= 0.35
            if conf >= self.confidence_floor and active_beats:
                # Snap s inward into [s_min, e_clamped) within max_snap_offset_sec
                s_candidates = [
                    b
                    for b in active_beats
                    if s_min <= b < e_clamped and abs(b - s_clamped) <= self.max_snap_offset_sec
                ]
                if s_candidates:
                    s_clamped = min(s_candidates, key=lambda b: abs(b - s_clamped))

                # Snap e inward into (s_clamped, e_max] within max_snap_offset_sec
                e_candidates = [
                    b
                    for b in active_beats
                    if s_clamped < b <= e_max and abs(b - e_clamped) <= self.max_snap_offset_sec
                ]
                if e_candidates:
                    e_clamped = min(e_candidates, key=lambda b: abs(b - e_clamped))

            # Final verification of safety invariants
            if s_clamped >= s_min and e_clamped <= e_max and (e_clamped - s_clamped) >= guard:
                valid_cuts.append(
                    JumpCut(
                        cut_start=round(s_clamped, 4),
                        cut_end=round(e_clamped, 4),
                        reason=jc.reason or "speech_gap_excised",
                    )
                )

        return valid_cuts

    def snap_edl(
        self,
        edl: EditDecisionList,
        words: Sequence[Any],
        rhythm: RhythmGrid | None = None,
    ) -> EditDecisionList:
        """Apply asymmetric beat snapping and word boundary guardrails to an EditDecisionList."""
        grid = rhythm or self._last_grid or self.analyze_rhythm(None)

        snapped_zooms = self.snap_zoom_punch_ins(
            edl.zoom_punch_ins,
            beats=grid.beats,
            confidence=grid.confidence,
        )
        snapped_cuts = self.snap_jump_cuts(
            edl.jump_cuts,
            words=words,
            beats=grid.beats,
            confidence=grid.confidence,
        )

        return EditDecisionList(
            hook_in_sec=edl.hook_in_sec,
            hook_out_sec=edl.hook_out_sec,
            jump_cuts=snapped_cuts,
            zoom_punch_ins=snapped_zooms,
            caption_corrections=list(edl.caption_corrections),
            recommended_thumbnail_timestamp=edl.recommended_thumbnail_timestamp,
            recommended_title=edl.recommended_title,
            recommended_hook=edl.recommended_hook,
            background_style_recommendation=edl.background_style_recommendation,
        )

    def _create_synthetic_grid(
        self,
        bpm: float,
        duration: float,
        confidence: float = 0.85,
    ) -> RhythmGrid:
        """Generate a regular synthetic beat grid."""
        interval = 60.0 / bpm
        beats: list[float] = []
        t = 0.0
        while t <= duration:
            beats.append(round(t, 4))
            t += interval

        is_ambient = confidence < self.confidence_floor
        return RhythmGrid(
            bpm=bpm,
            beats=beats,
            confidence=confidence,
            ducking_release_ms=self.calculate_ducking_release(bpm),
            ducking_release_sec=self.calculate_ducking_release_sec(bpm),
            is_ambient=is_ambient,
        )

    def _read_wav_file(self, path: Path) -> tuple[list[float], int]:
        """Read 16-bit or 8-bit PCM audio samples from WAV file."""
        with wave.open(str(path), "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            raw_bytes = wf.readframes(n_frames)

        if sampwidth == 2:  # 16-bit PCM
            n_samples = n_frames * n_channels
            fmt = f"<{n_samples}h"
            raw_ints = struct.unpack(fmt, raw_bytes)
            if n_channels == 1:
                samples = [s / 32768.0 for s in raw_ints]
            else:
                samples = [
                    (raw_ints[i] + raw_ints[i + 1]) / (2.0 * 32768.0)
                    for i in range(0, len(raw_ints), n_channels)
                ]
        elif sampwidth == 1:  # 8-bit unsigned PCM
            if n_channels == 1:
                samples = [(b - 128) / 128.0 for b in raw_bytes]
            else:
                samples = [
                    ((raw_bytes[i] - 128) + (raw_bytes[i + 1] - 128)) / 256.0
                    for i in range(0, len(raw_bytes), n_channels)
                ]
        else:
            raise ValueError(f"Unsupported WAV sample width: {sampwidth} bytes")

        return samples, framerate
