"""Tests for the long-form scene decisions (theme, caption preset, sprite placement)."""

from __future__ import annotations

import logging

import pytest

from autoshorts.decider import records
from autoshorts.decider.records import decisions_for_job
from autoshorts.longform import decisions
from autoshorts.longform.background import theme_names
from autoshorts.longform.decisions import SceneDecision, check_decisions, decide_scene


@pytest.fixture(autouse=True)
def _clear_state():
    with records._lock:
        records._records.clear()
    with decisions._lock:
        decisions._last_theme.clear()
    yield
    with records._lock:
        records._records.clear()
    with decisions._lock:
        decisions._last_theme.clear()


SIX_SECTIONS = [
    "Wake up early and open the window to the morning sun.",
    "Your phone is pulling you into a city of endless scrolling.",
    "Sit at your desk and start the work before the email piles up.",
    "Study the science behind habits in the lab of your mind.",
    "Win the day by celebrating the small steps you take.",
    "Go to sleep on time and rest in the dark bed tonight.",
]


def test_decide_scene_returns_valid_theme_and_records_two_decisions():
    decision = decide_scene(0, "Intro", "Wake up with the morning sun.", "host", 6, job_id="job-a")

    assert isinstance(decision, SceneDecision)
    assert decision.theme in theme_names()
    assert decision.caption_preset == "kurz"
    assert decision.sprite_placement_mode == "auto"
    recorded = {r.name: r for r in decisions_for_job("job-a")}
    assert len(decisions_for_job("job-a")) == 2
    assert recorded["scene_theme"].value == decision.theme
    assert recorded["scene_caption"].value == "kurz"


def test_empty_narration_records_default_source():
    decision = decide_scene(2, "", "   ", "host", 6, job_id="job-empty")

    assert decision.theme in theme_names()
    recorded = {r.name: r for r in decisions_for_job("job-empty")}
    assert recorded["scene_theme"].source == "default"
    assert recorded["scene_theme"].reason == "empty narration"
    assert recorded["scene_caption"].source == "default"


def test_check_flags_consecutive_duplicate_themes():
    first = decide_scene(0, "", "Wake up with the morning sun.", "host", 2)
    dup = SceneDecision(
        theme=first.theme,
        layout_mode=first.layout_mode,
        caption_preset=first.caption_preset,
        reasons=dict(first.reasons),
    )

    problems = check_decisions([first, dup])

    assert any("repeats the previous scene" in p for p in problems)


def test_check_flags_unknown_theme(caplog):
    good = decide_scene(0, "", "Wake up with the morning sun.", "host", 1)
    bad = SceneDecision(
        theme="no_such_theme",
        layout_mode=good.layout_mode,
        caption_preset=good.caption_preset,
        reasons=dict(good.reasons),
    )

    with caplog.at_level(logging.WARNING, logger=decisions.logger.name):
        problems = check_decisions([bad])

    assert any("unknown theme 'no_such_theme'" in p for p in problems)
    assert any("unknown theme" in rec.getMessage() for rec in caplog.records)


def test_check_flags_missing_reason():
    good = decide_scene(0, "", "Wake up with the morning sun.", "host", 1)
    bare = SceneDecision(theme=good.theme, layout_mode=good.layout_mode, caption_preset="kurz")

    problems = check_decisions([bare])

    assert any("missing reason for theme" in p for p in problems)


def test_six_section_sequence_has_no_problems():
    job = "job-six"
    sequence = [
        decide_scene(i, "", text, "host", len(SIX_SECTIONS), job_id=job)
        for i, text in enumerate(SIX_SECTIONS)
    ]

    assert check_decisions(sequence) == []
    themes = [d.theme for d in sequence]
    assert all(a != b for a, b in zip(themes, themes[1:], strict=False))
