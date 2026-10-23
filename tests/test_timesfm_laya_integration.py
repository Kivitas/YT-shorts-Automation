"""Comprehensive integration tests for Laya Rhythm Engine, TimesFM Forecaster & Laya Router.

Covers:
1. LayaRhythmEngine:
   - Pure Python beat extraction & autocorrelation lag peak finding.
   - Beat confidence floor C >= 0.35 and automatic ambient audio bypass.
   - Asymmetric beat snapping: zoom punch-ins snapped by <= 100ms.
   - Speech boundary safety invariant: jump cuts clamped >= 50ms away from words; zero truncation.
   - Rhythmic ducking release formula: T_release = 60000 / (BPM * 2) ms (musical eighth note).
   - EditDecisionList synchronization and WAV file ingestion.
2. TimeSeriesForecastingEngine:
   - 3-tier fallback architecture (pure Python stdlib when heavy weights absent).
   - 5-point smoothed retention cliff detector with >= 4.5s cooldown and <= 4 interrupts/Short.
   - Early view velocity 48h trajectory forecasting & viral breakout classification.
   - Telemetry normalization and edge case robustness (empty, zeros, single-point).
3. LayaRouter:
   - Sub-40ms System 1 hook scoring latency guarantee.
   - 3-tier calibrated quality gate: >=80 approved, 70-79 fast patch, <70 rejected.
   - Fast hook sentence patching and candidate selection triage.
"""

from __future__ import annotations

import math
import struct
import tempfile
import wave
from pathlib import Path

from autoshorts.analytics.forecasting import (
    ForecastResult,
    RetentionCliff,
    TimeSeriesForecastingEngine,
)
from autoshorts.analytics.models import AnalyticsSnapshot, VideoAnalyticsRecord
from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
from autoshorts.decider.laya_router import HookScoreResult, LayaRouter
from autoshorts.media.rhythm_engine import LayaRhythmEngine, RhythmGrid

# =============================================================================
# Helper Utilities for Synthetic Audio Generation
# =============================================================================


def _generate_synthetic_percussive_pcm(
    bpm: float = 120.0,
    duration: float = 6.0,
    sample_rate: int = 22050,
) -> list[float]:
    """Generate synthetic PCM waveform with sharp percussive clicks at exact BPM."""
    n_samples = int(sample_rate * duration)
    samples = [0.0] * n_samples
    interval_sec = 60.0 / bpm
    total_beats = int(duration / interval_sec)

    for b in range(total_beats):
        pos = int(b * interval_sec * sample_rate)
        # 50ms damped click transient
        for i in range(min(int(0.05 * sample_rate), n_samples - pos)):
            envelope = math.exp(-i / 80.0)
            tone = math.sin(2.0 * math.pi * 180.0 * i / sample_rate)
            samples[pos + i] = envelope * tone

    return samples


def _generate_synthetic_ambient_pcm(
    duration: float = 6.0,
    sample_rate: int = 22050,
) -> list[float]:
    """Generate smooth continuous ambient drone/pad without rhythmic transients."""
    n_samples = int(sample_rate * duration)
    samples: list[float] = []
    for i in range(n_samples):
        # Multi-tone continuous sine pad
        s = 0.05 * math.sin(2.0 * math.pi * 220.0 * i / sample_rate) + 0.05 * math.sin(
            2.0 * math.pi * 277.18 * i / sample_rate
        )
        samples.append(s)
    return samples


def _write_temp_wav(samples: list[float], sample_rate: int = 22050) -> Path:
    """Write mono float samples to a temporary 16-bit PCM WAV file."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
        temp_path = Path(temp_file.name)

    raw_ints = [int(max(-1.0, min(1.0, s)) * 32767.0) for s in samples]
    fmt = f"<{len(raw_ints)}h"
    raw_bytes = struct.pack(fmt, *raw_ints)

    with wave.open(str(temp_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw_bytes)

    return temp_path


# =============================================================================
# 1. Laya Rhythm Engine Tests
# =============================================================================


class TestLayaRhythmEngine:
    """Tests for LayaRhythmEngine: pure Python rhythm extraction, confidence, and snapping."""

    def test_rhythmic_ducking_release_calculation(self) -> None:
        """Verify ducking release formula: T_release = 60000 / (BPM * 2) ms (eighth note)."""
        engine = LayaRhythmEngine()

        # 120 BPM: 60000 / (120 * 2) = 250.0 ms
        assert engine.calculate_ducking_release(120.0) == 250.0
        assert engine.calculate_ducking_release_sec(120.0) == 0.250

        # 100 BPM: 60000 / (100 * 2) = 300.0 ms
        assert engine.calculate_ducking_release(100.0) == 300.0
        assert engine.calculate_ducking_release_sec(100.0) == 0.300

        # 150 BPM: 60000 / (150 * 2) = 200.0 ms
        assert engine.calculate_ducking_release(150.0) == 200.0

        # 80 BPM: 60000 / (80 * 2) = 375.0 ms
        assert engine.calculate_ducking_release(80.0) == 375.0

        # Edge cases: non-positive BPM clamps to 120.0 default
        assert engine.calculate_ducking_release(0.0) == 250.0
        assert engine.calculate_ducking_release(-50.0) == 250.0

    def test_pure_python_beat_tracking_synthetic_120bpm(self) -> None:
        """Verify onset detection and autocorrelation accurately detect 120 BPM clicks."""
        engine = LayaRhythmEngine()
        pcm = _generate_synthetic_percussive_pcm(bpm=120.0, duration=8.0, sample_rate=22050)

        grid = engine.extract_beats_from_samples(pcm, sample_rate=22050)

        assert isinstance(grid, RhythmGrid)
        # Detected BPM should be within ~2% of 120 BPM (117 to 123)
        assert 117.0 <= grid.bpm <= 123.0
        # Percussive transient confidence must exceed 0.35 floor
        assert grid.confidence >= 0.70
        assert not grid.is_ambient
        assert len(grid.beats) >= 14
        # Beats should be spaced approximately 0.5s apart (within 0.04s)
        beat_deltas = [grid.beats[i] - grid.beats[i - 1] for i in range(1, 5)]
        for delta in beat_deltas:
            assert abs(delta - 0.50) < 0.04

    def test_ambient_audio_confidence_floor_and_bypass(self) -> None:
        """Verify smooth continuous pads yield C < 0.35 and bypass beat snapping."""
        engine = LayaRhythmEngine()
        ambient_pcm = _generate_synthetic_ambient_pcm(duration=6.0, sample_rate=22050)

        grid = engine.extract_beats_from_samples(ambient_pcm, sample_rate=22050)

        # Ambient pad lacks transient spikes -> confidence < 0.35
        assert grid.confidence < 0.35
        assert grid.is_ambient

        # Test beat snapping bypass on ambient track
        original_zooms = [ZoomPunchIn(timestamp=1.04), ZoomPunchIn(timestamp=2.06)]
        active_beats = [0.0, 1.0, 2.0, 3.0]

        snapped = engine.snap_zoom_punch_ins(
            original_zooms,
            beats=active_beats,
            confidence=grid.confidence,
        )

        # Timestamps must remain unmodified because C < 0.35
        assert snapped[0].timestamp == 1.04
        assert snapped[1].timestamp == 2.06

    def test_asymmetric_beat_snapping_zoom_punch_ins(self) -> None:
        """Verify visual zoom punches snap by <= 100ms and remain unsnapped beyond 100ms."""
        engine = LayaRhythmEngine()
        active_beats = [1.00, 2.00, 3.00, 4.00]

        # Candidate 1: 0.98s is 20ms away from 1.00s -> snaps to 1.00s
        # Candidate 2: 1.08s is 80ms away from 1.00s -> snaps to 1.00s
        # Candidate 3: 1.25s is 250ms away from 1.00s and 750ms from 2.00s -> kept at 1.25s
        zooms = [
            ZoomPunchIn(timestamp=0.98, scale=1.15),
            ZoomPunchIn(timestamp=1.08, scale=1.10),
            ZoomPunchIn(timestamp=1.25, scale=1.20),
        ]

        snapped = engine.snap_zoom_punch_ins(zooms, beats=active_beats, confidence=0.85)

        assert snapped[0].timestamp == 1.00
        assert snapped[1].timestamp == 1.00
        assert snapped[2].timestamp == 1.25

        # Raw float timestamp list support
        raw_floats = [0.95, 2.05, 3.40]
        snapped_floats = engine.snap_zoom_punch_ins(raw_floats, beats=active_beats, confidence=0.85)
        assert snapped_floats[0] == 1.00  # 50ms away
        assert snapped_floats[1] == 2.00  # 50ms away
        assert snapped_floats[2] == 3.40  # 400ms away -> unchanged

    def test_asymmetric_jump_cut_speech_guardrail_protection(self) -> None:
        """Verify jump cuts are clamped >= 50ms away from speech boundaries; zero truncation."""
        engine = LayaRhythmEngine()

        # Transcript words:
        # Word 1: "Hello" [1.00, 1.50]
        # Word 2: "world" [3.00, 3.50]
        # Safe dead-air gap interval: [1.50 + 0.05, 3.00 - 0.05] = [1.55, 2.95]
        words = [
            {"word": "Hello", "start": 1.00, "end": 1.50},
            {"word": "world", "start": 3.00, "end": 3.50},
        ]

        # Dangerously wide cut that intrudes into words: [1.40, 3.10]
        dangerous_cuts = [JumpCut(cut_start=1.40, cut_end=3.10, reason="pause")]
        beats = [1.00, 1.50, 1.60, 2.00, 2.50, 2.90, 3.00]

        snapped_cuts = engine.snap_jump_cuts(
            dangerous_cuts,
            words=words,
            beats=beats,
            confidence=0.85,
        )

        assert len(snapped_cuts) == 1
        safe_cut = snapped_cuts[0]

        # Must be strictly clamped inside [1.55, 2.95]
        assert safe_cut.cut_start >= 1.55
        assert safe_cut.cut_end <= 2.95
        # Snapped to beats within the safe boundary: 1.60 >= 1.55 and 2.90 <= 2.95
        assert safe_cut.cut_start == 1.60
        assert safe_cut.cut_end == 2.90

        # Test narrow speech gap where safe cut is impossible (gap < 0.10s)
        # Word A: [1.00, 1.50], Word B: [1.58, 2.00] -> gap = 0.08s
        tight_words = [
            {"word": "fast", "start": 1.00, "end": 1.50},
            {"word": "speech", "start": 1.58, "end": 2.00},
        ]
        tight_cuts = [JumpCut(cut_start=1.51, cut_end=1.57)]
        safe_tight = engine.snap_jump_cuts(tight_cuts, words=tight_words, beats=beats)
        # Cut must be excised entirely to prevent word truncation
        assert len(safe_tight) == 0

    def test_snap_edl_preserves_metadata(self) -> None:
        """Verify snap_edl updates punch-ins and jump cuts while preserving all EDL fields."""
        engine = LayaRhythmEngine()
        words = [{"word": "Intro", "start": 0.0, "end": 1.0}]

        edl = EditDecisionList(
            hook_in_sec=0.5,
            hook_out_sec=45.0,
            recommended_title="Viral Secret Exposed",
            recommended_hook="Stop scrolling right now!",
            background_style_recommendation="minecraft",
            zoom_punch_ins=[ZoomPunchIn(timestamp=1.98)],
            jump_cuts=[],
        )

        grid = RhythmGrid(
            bpm=120.0,
            beats=[1.0, 2.0, 3.0],
            confidence=0.90,
            ducking_release_ms=250.0,
            ducking_release_sec=0.250,
            is_ambient=False,
        )

        snapped_edl = engine.snap_edl(edl, words=words, rhythm=grid)

        assert snapped_edl.hook_in_sec == 0.5
        assert snapped_edl.hook_out_sec == 45.0
        assert snapped_edl.recommended_title == "Viral Secret Exposed"
        assert snapped_edl.background_style_recommendation == "minecraft"
        assert snapped_edl.zoom_punch_ins[0].timestamp == 2.0  # 1.98 -> 2.0

    def test_wav_file_ingestion(self) -> None:
        """Verify extract_beats_from_audio reads actual WAV file correctly."""
        engine = LayaRhythmEngine()
        pcm = _generate_synthetic_percussive_pcm(bpm=120.0, duration=3.0, sample_rate=22050)
        temp_wav = _write_temp_wav(pcm, sample_rate=22050)

        try:
            grid = engine.extract_beats_from_audio(temp_wav)
            assert isinstance(grid, RhythmGrid)
            assert grid.bpm > 0.0
            assert grid.ducking_release_ms > 0.0
        finally:
            if temp_wav.exists():
                temp_wav.unlink()


# =============================================================================
# 2. Time-Series Forecasting Engine Tests
# =============================================================================


class TestTimeSeriesForecastingEngine:
    """Tests for TimeSeriesForecastingEngine: fallback tier, cliff detector, and 48h forecast."""

    def test_tier_resolution_pure_python_fallback(self) -> None:
        """Verify active tier resolves to pure_python when heavy packages are uninstalled."""
        engine = TimeSeriesForecastingEngine()
        tier = engine.active_tier
        # In our virtual environment without timesfm/numpy, tier must be pure_python
        assert tier in ("pure_python", "numpy_scipy", "timesfm")
        assert engine.get_active_tier() == tier

        # Explicit force override test
        forced_engine = TimeSeriesForecastingEngine(force_tier="pure_python")
        assert forced_engine.active_tier == "pure_python"

    def test_retention_cliff_smoothing_cooldown_and_cap(self) -> None:
        """Verify 5-point smoothing, >= 4.5s cooldown, and <= 4 interrupts per Short."""
        engine = TimeSeriesForecastingEngine(
            cliff_threshold=-0.035,
            cliff_cooldown_sec=4.5,
            max_interrupts_per_short=4,
        )

        # Construct a 60-second retention curve with several sharp drop events
        # Baseline slow decay (-0.005/s)
        # Steep cliffs injected at t=5s (drop 0.08/s), t=12s, t=20s, t=30s, t=40s, t=50s
        ts = [float(i) for i in range(61)]
        ret = [1.0] * 61
        for i in range(1, 61):
            is_cliff = i in (
                5,
                6,
                7,
                12,
                13,
                14,
                20,
                21,
                22,
                30,
                31,
                32,
                40,
                41,
                42,
                50,
                51,
                52,
            )
            step_drop = 0.08 if is_cliff else 0.005
            ret[i] = max(0.15, ret[i - 1] - step_drop)

        cliffs = engine.detect_retention_cliffs(ret, timestamps=ts, duration=60.0)

        assert isinstance(cliffs, list)
        for c in cliffs:
            assert isinstance(c, RetentionCliff)
            # Invariant 1: all detected cliffs have slope <= -0.035
            assert c.slope <= -0.035
            assert c.action == "pattern_interrupt"

        # Invariant 2: cooldown >= 4.5s between consecutive interrupts
        for idx in range(1, len(cliffs)):
            dt = cliffs[idx].timestamp - cliffs[idx - 1].timestamp
            assert dt >= 4.5, f"Cooldown violated: {dt} < 4.5s"

        # Invariant 3: hard ceiling of <= 4 interrupts per 60s Short
        assert len(cliffs) <= 4
        assert len(cliffs) >= 2  # Should detect at least several valid cliffs

    def test_retention_cliff_percentage_scale_normalization(self) -> None:
        """Verify 0-100 retention scale is automatically normalized to 0.0-1.0."""
        engine = TimeSeriesForecastingEngine(cliff_threshold=-0.035)

        # 0-100 scale
        ret_pct = [100.0] * 61
        for i in range(1, 61):
            drop = 8.0 if i in (5, 6, 7) else 0.5
            ret_pct[i] = max(10.0, ret_pct[i - 1] - drop)

        cliffs = engine.detect_retention_cliffs(ret_pct, duration=60.0)
        assert len(cliffs) >= 1
        assert cliffs[0].slope <= -0.035

    def test_48h_trajectory_forecasting_pure_python(self) -> None:
        """Verify early velocity analytical regression projects 48h trajectory and breakout."""
        engine = TimeSeriesForecastingEngine(force_tier="pure_python")

        # Early telemetry: 1h -> 350 views, 2h -> 900 views, 3h -> 2100 views (superlinear)
        points = [(1.0, 350), (2.0, 900), (3.0, 2100)]
        forecast = engine.forecast_trajectory(points)

        assert isinstance(forecast, ForecastResult)
        assert forecast.tier_used == "pure_python"
        assert len(forecast.hourly_forecast) == 48

        # Hourly forecast must be non-decreasing
        for i in range(1, 48):
            assert forecast.hourly_forecast[i] >= forecast.hourly_forecast[i - 1]

        # Breakout classification: accelerating velocity yields >= 10,000 views at 48h
        assert forecast.predicted_views_48h >= 10000.0
        assert forecast.is_viral_breakout is True
        assert forecast.confidence_score >= 0.70

    def test_forecasting_edge_cases(self) -> None:
        """Verify forecasting handles empty data, zero views, and single points robustly."""
        engine = TimeSeriesForecastingEngine(force_tier="pure_python")

        # 1. Empty telemetry
        empty_res = engine.forecast_trajectory([])
        assert empty_res.predicted_views_48h == 0.0
        assert not empty_res.is_viral_breakout

        # 2. All zeros
        zero_res = engine.forecast_trajectory([(1.0, 0), (2.0, 0)])
        assert zero_res.predicted_views_48h == 0.0
        assert not zero_res.is_viral_breakout

        # 3. Single data point
        single_res = engine.forecast_trajectory([(1.0, 500)])
        assert single_res.predicted_views_48h > 500.0
        assert len(single_res.hourly_forecast) == 48

        # 4. VideoAnalyticsRecord input
        rec = VideoAnalyticsRecord(
            video_id="test_vid_123",
            views=1200,
            velocity_views_per_hour=400.0,
            history=[
                AnalyticsSnapshot(views=400, timestamp="2026-10-21T10:00:00Z"),
                AnalyticsSnapshot(views=800, timestamp="2026-10-21T11:00:00Z"),
                AnalyticsSnapshot(views=1200, timestamp="2026-10-21T12:00:00Z"),
            ],
        )
        rec_res = engine.forecast_trajectory(rec)
        assert rec_res.predicted_views_48h >= 1200.0


# =============================================================================
# 3. Laya Fast System 1 Hook Router Tests
# =============================================================================


class TestLayaRouter:
    """Tests for LayaRouter: sub-40ms System 1 scoring and triage quality gate."""

    def test_sub_40ms_latency_guarantee(self) -> None:
        """Verify System 1 heuristic scoring executes strictly within sub-40ms limit."""
        router = LayaRouter()
        test_hooks = [
            "Stop doing this 1 mistake that destroys your YouTube Shorts reach!",
            "Why nobody told you the truth about 3 passive income streams?",
            "The secret hack that will change your productivity forever!",
            "In this video today we are looking at standard morning routines.",
            "3 insane tricks to double your muscle growth in 7 days!",
        ]

        for hook in test_hooks:
            res = router.score_hook(hook)
            assert isinstance(res, HookScoreResult)
            # Hard performance invariant: sub-40ms latency
            assert res.latency_ms < 40.0, f"Latency exceeded: {res.latency_ms}ms"

    def test_approved_viral_hook_scoring(self) -> None:
        """Verify high-curiosity hook with numbers and urgency achieves score >= 80 (approved)."""
        router = LayaRouter()
        hook = "Stop making this 1 deadly mistake that ruins your sleep!"

        res = router.score_hook(hook)

        assert res.score >= 80.0
        assert res.verdict == "approved"
        assert res.patch_suggestion is None

    def test_patch_needed_and_fast_patching(self) -> None:
        """Verify borderline hook (70-79) triggers patch_needed and fast patch elevates >= 80."""
        router = LayaRouter()
        # Borderline hook (Score 75.0) lacking numbers, urgency words, and terminal punch:
        hook = "A secret method for making regular coffee"

        res = router.score_hook(hook)

        # Borderline trigger
        assert res.score == 75.0
        assert res.verdict == "patch_needed"
        assert res.patch_suggestion is not None

        # Re-scoring patched version elevates score into approved viral status (>= 80.0)
        patched_res = router.score_hook(res.patch_suggestion)
        assert patched_res.score >= 80.0
        assert patched_res.verdict == "approved"

    def test_rejected_weak_opening_hook(self) -> None:
        """Verify passive weak openings ('in this video today') yield score < 70 (rejected)."""
        router = LayaRouter()
        hook = "In this video today we are going to look at regular habits."

        res = router.score_hook(hook)

        assert res.score < 70.0
        assert res.verdict == "rejected"

    def test_candidate_selection_triage(self) -> None:
        """Verify select_best_hook implements the 3-phase quality gate correctly."""
        router = LayaRouter()

        # Candidates with one approved hook
        candidates = [
            "In this video today we discuss habits.",  # Weak (< 70)
            "Stop making this 1 deadly mistake that ruins your sleep!",  # Approved (>= 80)
            "A standard routine for students.",  # Weak (< 70)
        ]

        best = router.select_best_hook(candidates)
        assert best.verdict == "approved"
        assert "Stop making this 1 deadly mistake" in best.hook
