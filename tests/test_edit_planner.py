"""Tests for AI multimodal Edit Planner: schema validation, clamping, and repair retries."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autoshorts.core.config import EditPlannerConfig
from autoshorts.core.contracts import EditPlanner
from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
from autoshorts.edit_planner.edl_schema import (
    clamp_edl,
    create_empty_edl,
    validate_edl,
)
from autoshorts.edit_planner.multimodal_planner import (
    PRIVACY_NOTICE,
    MultimodalEditPlanner,
)


class FakeTextProvider:
    """Fake text-only LLM provider for testing."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "fake_text"

    def complete(self, prompt: str, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        if self.responses:
            return self.responses.pop(0)
        return "{}"


class FakeMultimodalProvider:
    """Fake multimodal LLM provider supporting video analysis and text completion."""

    def __init__(self, video_response: str | Exception, repair_response: str = "") -> None:
        self.video_response = video_response
        self.repair_response = repair_response
        self.analyzed_paths: list[Path] = []
        self.repair_prompts: list[str] = []

    @property
    def name(self) -> str:
        return "gemini_video"

    def analyze_video(self, video_path: Path, prompt: str, **kwargs: Any) -> str:
        self.analyzed_paths.append(video_path)
        if isinstance(self.video_response, Exception):
            raise self.video_response
        return self.video_response

    def complete(self, prompt: str, **kwargs: Any) -> str:
        self.repair_prompts.append(prompt)
        return self.repair_response


def test_edit_planner_protocol_conformance():
    """Verify MultimodalEditPlanner implements contracts.EditPlanner."""
    planner = MultimodalEditPlanner()
    assert isinstance(planner, EditPlanner)


def test_edl_schema_validation_success():
    """Validate valid JSON and dictionary into EditDecisionList."""
    valid_data = {
        "hook_in_sec": 1.2,
        "hook_out_sec": 42.5,
        "jump_cuts": [{"cut_start": 10.0, "cut_end": 12.0, "reason": "pause"}],
        "zoom_punch_ins": [
            {
                "timestamp": 5.0,
                "scale": 1.15,
                "duration": 0.8,
                "emphasis_word": "critical",
            }
        ],
        "caption_corrections": [{"original": "teh", "corrected": "the"}],
        "recommended_thumbnail_timestamp": 5.0,
        "recommended_title": "The Truth About AI #Shorts",
        "recommended_hook": "Did you know this about neural nets?",
        "background_style_recommendation": "minecraft",
    }

    edl = validate_edl(valid_data)
    assert edl.hook_in_sec == 1.2
    assert edl.hook_out_sec == 42.5
    assert len(edl.jump_cuts) == 1
    assert edl.jump_cuts[0].reason == "pause"
    assert edl.recommended_title == "The Truth About AI #Shorts"

    # Also test from JSON string with markdown fences
    json_str = f"```json\n{json.dumps(valid_data)}\n```"
    edl_from_str = validate_edl(json_str)
    assert edl_from_str.hook_out_sec == 42.5


def test_edl_schema_validation_failure_on_invalid_data():
    """Verify validation fails on missing fields or bad types."""
    # Missing required fields with strict_keys=True
    incomplete = {"hook_in_sec": 0.0}
    with pytest.raises(ValueError, match="Missing required"):
        validate_edl(incomplete, strict_keys=True)

    # Negative hook_in_sec violates ge=0.0 constraint
    with pytest.raises(ValidationError):
        validate_edl({"hook_in_sec": -5.0})

    # Bad scale bounds (scale must be <= 1.25)
    invalid_scale = {
        "hook_in_sec": 0.0,
        "hook_out_sec": 40.0,
        "jump_cuts": [],
        "zoom_punch_ins": [
            {"timestamp": 2.0, "scale": 2.50, "duration": 0.8, "emphasis_word": "huge"}
        ],
        "caption_corrections": [],
        "recommended_thumbnail_timestamp": 1.0,
        "recommended_title": "Title #Shorts",
        "recommended_hook": "Hook",
        "background_style_recommendation": "minecraft",
    }
    with pytest.raises(ValidationError):
        validate_edl(invalid_scale)


def test_edl_clamping_to_clip_duration():
    """Verify timestamps are clamped within physical clip bounds."""
    clip_dur = 45.0
    raw_edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=60.0,  # Exceeds clip duration
        jump_cuts=[],
        zoom_punch_ins=[ZoomPunchIn(timestamp=50.0, scale=1.1, duration=0.8, emphasis_word="out")],
        caption_corrections=[],
        recommended_thumbnail_timestamp=55.0,  # Exceeds clip duration
        recommended_title="Clamping Test #Shorts",
        recommended_hook="Testing clamping",
        background_style_recommendation="minecraft",
    )

    clamped = clamp_edl(raw_edl, clip_duration=clip_dur)
    assert clamped.hook_out_sec <= clip_dur
    assert clamped.recommended_thumbnail_timestamp <= clamped.hook_out_sec
    assert len(clamped.zoom_punch_ins) == 0  # Timestamp was > hook_out, dropped
    assert len(clamped.recommended_title) <= 60
    assert len(clamped.recommended_hook) <= 100


def test_rejection_of_overlapping_jump_cuts():
    """Reject overlapping jump cuts to prevent invalid audio/video splicing."""
    clip_dur = 50.0
    raw_edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=50.0,
        jump_cuts=[
            JumpCut(cut_start=5.0, cut_end=10.0, reason="pause 1"),
            JumpCut(cut_start=8.0, cut_end=12.0, reason="overlapping pause 2"),  # Overlaps!
            JumpCut(cut_start=15.0, cut_end=17.0, reason="pause 3"),
        ],
        zoom_punch_ins=[],
        caption_corrections=[],
        recommended_thumbnail_timestamp=2.0,
        recommended_title="Clean Cuts #Shorts",
        recommended_hook="Watch smooth cuts",
        background_style_recommendation="minecraft",
    )

    clamped = clamp_edl(raw_edl, clip_duration=clip_dur)
    # The overlapping cut (8.0 - 12.0) must be rejected
    assert len(clamped.jump_cuts) == 2
    assert clamped.jump_cuts[0].cut_start == 5.0
    assert clamped.jump_cuts[0].cut_end == 10.0
    assert clamped.jump_cuts[1].cut_start == 15.0
    assert clamped.jump_cuts[1].cut_end == 17.0


def test_rejection_of_absurd_jump_cuts():
    """Reject cuts with inverted timestamps or cuts that excise the entire video."""
    clip_dur = 40.0
    raw_edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=40.0,
        jump_cuts=[
            JumpCut(cut_start=10.0, cut_end=5.0, reason="inverted"),  # Inverted!
            JumpCut(cut_start=1.0, cut_end=39.0, reason="deletes everything"),  # > 80%!
        ],
        zoom_punch_ins=[],
        caption_corrections=[],
        recommended_thumbnail_timestamp=2.0,
        recommended_title="Test #Shorts",
        recommended_hook="Hook",
        background_style_recommendation="subway",
    )

    clamped = clamp_edl(raw_edl, clip_duration=clip_dur)
    assert len(clamped.jump_cuts) == 0


def test_schema_repair_retry_once_success(tmp_path: Path):
    """Initial broken output triggers 1-shot schema repair and succeeds."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_text("fake video bytes")

    broken_response = '{"hook_in_sec": "not_a_number", "missing_everything": true}'

    valid_response = json.dumps(
        {
            "hook_in_sec": 0.5,
            "hook_out_sec": 40.0,
            "jump_cuts": [],
            "zoom_punch_ins": [],
            "caption_corrections": [],
            "recommended_thumbnail_timestamp": 2.0,
            "recommended_title": "Repaired Title #Shorts",
            "recommended_hook": "Repaired Hook",
            "background_style_recommendation": "minecraft",
        }
    )

    fake_provider = FakeTextProvider(responses=[broken_response, valid_response])
    planner = MultimodalEditPlanner(provider=fake_provider, default_duration=45.0)

    edl = planner.generate_edl(video_path, transcript_text="Hello world")
    assert edl.recommended_title == "Repaired Title #Shorts"
    assert len(fake_provider.prompts) == 2  # 1 initial + 1 repair


def test_schema_repair_failure_falls_back_to_empty_edl(tmp_path: Path):
    """When repair retry also fails, fall back gracefully to deterministic empty EDL."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_text("fake video bytes")

    broken_1 = "Sorry, as an AI I cannot produce JSON."
    broken_2 = "Still not valid JSON: {"

    fake_provider = FakeTextProvider(responses=[broken_1, broken_2])
    planner = MultimodalEditPlanner(provider=fake_provider, default_duration=45.0)

    edl = planner.generate_edl(video_path)
    assert isinstance(edl, EditDecisionList)
    assert edl.hook_in_sec == 0.0
    assert edl.hook_out_sec == 45.0
    assert edl.jump_cuts == []
    assert len(fake_provider.prompts) == 2


def test_transcript_only_fallback(tmp_path: Path):
    """When video multimodal provider fails, fall back to transcript completion."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_text("fake video bytes")

    valid_json = json.dumps(
        {
            "hook_in_sec": 0.0,
            "hook_out_sec": 35.0,
            "jump_cuts": [],
            "zoom_punch_ins": [],
            "caption_corrections": [],
            "recommended_thumbnail_timestamp": 1.0,
            "recommended_title": "Transcript Title #Shorts",
            "recommended_hook": "Transcript Hook",
            "background_style_recommendation": "subway",
        }
    )

    # Provider analyze_video fails with an exception, but complete() works
    provider = FakeMultimodalProvider(
        video_response=RuntimeError("Files API unavailable"),
        repair_response=valid_json,
    )
    planner = MultimodalEditPlanner(provider=provider, default_duration=45.0)

    edl = planner.generate_edl(video_path, transcript_text="Some speech")
    assert edl.recommended_title == "Transcript Title #Shorts"
    assert edl.background_style_recommendation == "subway"


def test_privacy_warning_logged_when_video_leaves_device(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Verify privacy warning is logged whenever video leaves the device."""
    video_path = tmp_path / "clip.mp4"
    video_path.write_text("fake video")

    valid_json = json.dumps(
        {
            "hook_in_sec": 0.0,
            "hook_out_sec": 30.0,
            "jump_cuts": [],
            "zoom_punch_ins": [],
            "caption_corrections": [],
            "recommended_thumbnail_timestamp": 1.0,
            "recommended_title": "Title #Shorts",
            "recommended_hook": "Hook",
            "background_style_recommendation": "minecraft",
        }
    )

    provider = FakeMultimodalProvider(video_response=valid_json)
    planner = MultimodalEditPlanner(provider=provider, default_duration=30.0)

    with caplog.at_level(logging.WARNING):
        planner.generate_edl(video_path)

    assert "PRIVACY NOTICE" in caplog.text
    assert "Google Gemini API" in caplog.text
    assert planner.last_privacy_warning == PRIVACY_NOTICE


def test_zero_hardcoded_model_ids():
    """Verify edit planner config defaults to model=None."""
    cfg = EditPlannerConfig()
    assert cfg.model is None


def test_empty_edl_properties():
    """Verify create_empty_edl properties."""
    edl = create_empty_edl(clip_duration=42.0, title="Test", hook="Look")
    assert edl.hook_in_sec == 0.0
    assert edl.hook_out_sec == 42.0
    assert edl.jump_cuts == []
    assert edl.zoom_punch_ins == []
    assert edl.recommended_title == "Test"
    assert edl.recommended_hook == "Look"
    assert edl.background_style_recommendation == "minecraft"


def test_detect_dead_air_cuts_gaps_and_padding():
    """Verify detect_dead_air_cuts detects leading, internal, and trailing silence gaps with padding."""
    from autoshorts.edit_planner.planner import detect_dead_air_cuts
    from autoshorts.media.ass_styler import TimedWord

    words = [
        TimedWord(word="Hello", start=1.0, end=1.5),  # Leading silence 0.0 -> 1.0 (1.0s > 0.48s)
        TimedWord(word="world", start=1.7, end=2.2),  # Gap 0.2s <= 0.48s (ignored)
        {"word": "big", "start": 3.2, "end": 3.6},  # Gap 2.2 -> 3.2 (1.0s > 0.48s)
        {"word": "finish", "start": 3.8, "end": 4.2},  # Trailing 4.2 -> 10.0 (5.8s > 0.48s)
    ]
    cuts = detect_dead_air_cuts(
        words=words,
        clip_duration=10.0,
        min_gap_sec=0.48,
        padding_sec=0.08,
        max_cuts=12,
    )
    assert len(cuts) == 3
    # Leading cut: 0.0 + 0.08 = 0.08 to 1.0 - 0.08 = 0.92
    assert cuts[0].cut_start == pytest.approx(0.08, abs=0.02)
    assert cuts[0].cut_end == pytest.approx(0.92, abs=0.02)
    assert cuts[0].start == cuts[0].cut_start
    assert cuts[0].end == cuts[0].cut_end
    # Internal cut: 2.2 + 0.08 = 2.28 to 3.2 - 0.08 = 3.12
    assert cuts[1].cut_start == pytest.approx(2.28, abs=0.02)
    assert cuts[1].cut_end == pytest.approx(3.12, abs=0.02)
    # Active video must remain >= 3.0s
    total_excised = sum(c.cut_end - c.cut_start for c in cuts)
    assert (10.0 - total_excised) >= 3.0


def test_generate_smart_zoom_punches_spacing_and_scales():
    """Verify generate_smart_zoom_punches places 1.12x-1.18x punches on emphasis words >= 3.5s apart."""
    from autoshorts.edit_planner.planner import generate_smart_zoom_punches

    words = [
        {"word": "the", "start": 0.2, "end": 0.4},
        {"word": "SECRET!", "start": 0.5, "end": 1.0},  # Emphasis @ 0.5s
        {"word": "CRAZY", "start": 2.0, "end": 2.4},  # Too close (< 3.5s from 0.5s), skipped!
        {"word": "1000", "start": 4.5, "end": 5.0},  # Emphasis @ 4.5s (>= 4.0s)
        {"word": "extraordinary", "start": 9.0, "end": 9.6},  # Long word @ 9.0s (>= 8.0s)
    ]
    punches = generate_smart_zoom_punches(words=words, clip_duration=15.0, max_punches=5)
    assert len(punches) == 3
    assert punches[0].emphasis_word == "SECRET!"
    assert punches[1].emphasis_word == "1000"
    assert punches[2].emphasis_word == "extraordinary"
    for p in punches:
        assert 1.12 <= p.scale <= 1.18
    assert punches[1].timestamp - punches[0].timestamp >= 3.5
    assert punches[2].timestamp - punches[1].timestamp >= 3.5


def test_create_edl_auto_trim_silence_merges_cuts_when_llm_returns_zero(tmp_path: Path):
    """Verify MultimodalEditPlanner.create_edl merges dead-air cuts when auto_trim_silence=True and LLM has 0 cuts."""
    from autoshorts.edit_planner.planner import MultimodalEditPlanner as EnhancedPlanner

    video_path = tmp_path / "clip.mp4"
    video_path.write_text("fake video")

    valid_json_no_cuts = json.dumps(
        {
            "hook_in_sec": 0.0,
            "hook_out_sec": 20.0,
            "jump_cuts": [],
            "zoom_punch_ins": [],
            "caption_corrections": [],
            "recommended_thumbnail_timestamp": 1.0,
            "recommended_title": "Auto Trim #Shorts",
            "recommended_hook": "Watch this",
            "background_style_recommendation": "minecraft",
        }
    )
    provider = FakeTextProvider(responses=[valid_json_no_cuts])
    planner = EnhancedPlanner(provider=provider, default_duration=20.0)

    words = [
        {"word": "First", "start": 0.1, "end": 0.6},
        {"word": "Second", "start": 2.0, "end": 2.5},  # 1.4s silence gap
        {"word": "Third", "start": 2.7, "end": 19.8},
    ]

    edl = planner.create_edl(
        video_clip_path=video_path,
        transcript_text="First Second Third",
        words=words,
        auto_trim_silence=True,
    )
    assert len(edl.jump_cuts) == 1
    assert edl.jump_cuts[0].cut_start == pytest.approx(0.68, abs=0.02)
    assert edl.jump_cuts[0].cut_end == pytest.approx(1.92, abs=0.02)


def test_compute_adaptive_silence_threshold_fast_vs_slow_cadence():
    """Verify adaptive threshold adjusts dynamically between 0.40s and 0.60s based on cadence."""
    from autoshorts.edit_planner.planner import compute_adaptive_silence_threshold
    from autoshorts.media.ass_styler import TimedWord

    # Fast speaker: 10 words in 2.5 seconds = 4.0 words/sec (> 3.0 WPS)
    fast_words = [TimedWord(word=f"word{i}", start=i * 0.25, end=i * 0.25 + 0.2) for i in range(10)]
    fast_thresh = compute_adaptive_silence_threshold(fast_words, clip_duration=3.0)
    assert fast_thresh == 0.40

    # Slow deliberate speaker: 3 words in 3.0 seconds = 1.0 word/sec (< 1.4 WPS)
    slow_words = [
        TimedWord(word="slow", start=0.0, end=0.8),
        TimedWord(word="deliberate", start=1.5, end=2.2),
        TimedWord(word="speech", start=2.5, end=3.0),
    ]
    slow_thresh = compute_adaptive_silence_threshold(slow_words, clip_duration=3.5)
    assert slow_thresh == 0.60

    # Conversational speaker: 8 words in 3.6 seconds = 2.22 words/sec
    conv_words = [TimedWord(word=f"w{i}", start=i * 0.45, end=i * 0.45 + 0.35) for i in range(8)]
    conv_thresh = compute_adaptive_silence_threshold(conv_words, clip_duration=4.0)
    assert 0.45 <= conv_thresh <= 0.55


def test_detect_dead_air_cuts_adaptive_threshold():
    """Verify detect_dead_air_cuts uses adaptive threshold when min_gap_sec is None."""
    from autoshorts.edit_planner.planner import detect_dead_air_cuts
    from autoshorts.media.ass_styler import TimedWord

    # Fast speaker with 0.44s pause:
    # 10 words fast before pause, then 0.44s pause, then 10 words fast
    words = []
    t = 0.0
    for i in range(10):
        words.append(TimedWord(word=f"fast{i}", start=round(t, 2), end=round(t + 0.20, 2)))
        t += 0.22

    last_end = words[-1].end
    # Gap is exactly 0.44s (> fast speaker 0.40s threshold, < 0.48s default)
    pause_start = last_end
    t = last_end + 0.44

    for i in range(10):
        words.append(TimedWord(word=f"nxt{i}", start=round(t, 2), end=round(t + 0.20, 2)))
        t += 0.22

    # Adaptive mode detects the 0.44s pause because speaker is fast (threshold 0.40s)
    adaptive_cuts = detect_dead_air_cuts(
        words=words, clip_duration=t + 1.0, min_gap_sec=None, adaptive=True
    )
    assert len(adaptive_cuts) >= 1
    assert any(c.cut_start >= pause_start - 0.1 for c in adaptive_cuts)

    # Static 0.48s mode ignores the 0.44s pause
    static_cuts = detect_dead_air_cuts(
        words=words, clip_duration=t + 1.0, min_gap_sec=0.48, adaptive=False
    )
    assert not any(
        c.cut_start >= pause_start - 0.1 and c.cut_end <= pause_start + 0.5 for c in static_cuts
    )
