"""Tests for the style decider (layout_mode / style_preset resolution)."""

from __future__ import annotations

import pytest

from autoshorts.decider import records
from autoshorts.decider.records import decisions_for_job
from autoshorts.decider.style import SHORT_CLIP_SEC, StyleChoice, choose_style


@pytest.fixture(autouse=True)
def _clear_records():
    with records._lock:
        records._records.clear()
    yield
    with records._lock:
        records._records.clear()


def _ctx(**overrides):
    base = {
        "job_id": "job-style-1",
        "duration_sec": 60.0,
        "job_layout_mode": "split_50_50",
        "job_style_preset": "neon",
        "config_layout_mode": "split_50_50",
        "config_style_preset": "neon",
    }
    base.update(overrides)
    return base


def test_user_set_values_are_kept_and_recorded_as_user():
    choice = choose_style(
        _ctx(
            duration_sec=20.0,
            job_layout_mode="full_blur_bg",
            job_style_preset="hormozi",
            source_kind="talking_head",
        )
    )

    assert isinstance(choice, StyleChoice)
    assert choice.layout_mode == "full_blur_bg"
    assert choice.style_preset == "hormozi"
    by_name = {r.name: r for r in decisions_for_job("job-style-1")}
    assert by_name["layout_mode"].source == "user"
    assert by_name["layout_mode"].value == "full_blur_bg"
    assert by_name["style_preset"].source == "user"
    assert by_name["style_preset"].value == "hormozi"


def test_short_clip_without_user_preset_gets_kurz():
    choice = choose_style(_ctx(duration_sec=SHORT_CLIP_SEC - 5))

    assert choice.style_preset == "kurz"
    record = next(r for r in decisions_for_job("job-style-1") if r.name == "style_preset")
    assert record.value == "kurz"
    assert record.source == "heuristic"


def test_long_clip_without_user_preset_keeps_config_default():
    choice = choose_style(_ctx(duration_sec=60.0, config_style_preset="minimal"))

    assert choice.style_preset == "minimal"
    record = next(r for r in decisions_for_job("job-style-1") if r.name == "style_preset")
    assert record.source == "config"


def test_talking_head_source_gets_split_layout():
    choice = choose_style(_ctx(source_kind="talking_head", config_layout_mode="full_center_crop"))

    assert choice.layout_mode == "split_50_50"
    record = next(r for r in decisions_for_job("job-style-1") if r.name == "layout_mode")
    assert record.source == "heuristic"


def test_ranking_clip_is_not_forced_to_split_layout():
    choice = choose_style(
        _ctx(
            source_kind="talking_head",
            format_kind="ranking",
            config_layout_mode="full_center_crop",
        )
    )

    assert choice.layout_mode == "full_center_crop"


def test_defaults_fall_through_unchanged():
    choice = choose_style(
        _ctx(
            duration_sec=90.0,
            config_layout_mode="full_blur_bg",
            config_style_preset="cinematic",
        )
    )

    assert choice.layout_mode == "full_blur_bg"
    assert choice.style_preset == "cinematic"
    sources = {r.name: r.source for r in decisions_for_job("job-style-1")}
    assert sources == {"layout_mode": "config", "style_preset": "config"}


def test_recipe_value_is_used_and_recorded_as_recipe():
    choice = choose_style(
        _ctx(duration_sec=20.0, recipe={"layout_mode": "pip_gameplay", "style_preset": "mrbeast"})
    )

    assert choice.layout_mode == "pip_gameplay"
    assert choice.style_preset == "mrbeast"
    sources = {r.name: r.source for r in decisions_for_job("job-style-1")}
    assert sources == {"layout_mode": "recipe", "style_preset": "recipe"}


def test_each_decision_is_retrievable_by_job_id():
    choose_style(_ctx(job_id="job-a", duration_sec=10.0))
    choose_style(_ctx(job_id="job-b", duration_sec=90.0))

    job_a = decisions_for_job("job-a")
    job_b = decisions_for_job("job-b")
    assert {r.name for r in job_a} == {"layout_mode", "style_preset"}
    assert {r.name for r in job_b} == {"layout_mode", "style_preset"}
    assert next(r.value for r in job_a if r.name == "style_preset") == "kurz"
    assert next(r.value for r in job_b if r.name == "style_preset") == "neon"


def test_missing_fields_never_raise():
    choice = choose_style({})

    assert choice.layout_mode == "split_50_50"
    assert choice.style_preset == "neon"
    assert choice.background_category == "none"


@pytest.mark.parametrize("context", [None, "not-a-mapping", {"duration_sec": "abc"}])
def test_malformed_context_never_raises(context):
    choice = choose_style(context)

    assert choice.layout_mode
    assert choice.style_preset


def test_nan_and_bool_durations_are_ignored():
    assert choose_style(_ctx(duration_sec=float("nan"))).style_preset == "neon"
    assert choose_style(_ctx(duration_sec=True)).style_preset == "neon"
