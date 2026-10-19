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
