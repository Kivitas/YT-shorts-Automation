"""Viral-retention rules: hashtag pyramid, hook window, pattern-interrupt cadence, loop seam."""

from __future__ import annotations

import re

import pytest

from autoshorts.longform.retention import (
    PATTERN_INTERRUPT_MAX_SEC,
    PATTERN_INTERRUPT_MIN_SEC,
    hook_in_first_seconds,
    pattern_interrupt_times,
)
from autoshorts.longform.script import build_script, description_for
from autoshorts.longform.topics import TOPIC_KEYS
from autoshorts.uploader.description_builder import (
    HASHTAG_MAX,
    HASHTAG_MIN,
    LONGFORM_BROAD_LAYER,
    SHORTS_BROAD_LAYER,
    build_hashtag_pyramid,
    build_viral_hashtags,
    hashtag_layers,
)

_HASHTAG_RE = re.compile(r"#\w+")


def _hashtags_in(text: str) -> list[str]:
    return _HASHTAG_RE.findall(text)


# --- 1. Hashtag pyramid -------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "hook"),
    [
        ("Why are you freaked out by my robot hand?", "Robot hand"),
        ("", ""),
        ("a b c", ""),
        ("Compound interest explained simply", "Money grows when you wait patiently"),
        ("x" * 300, "Quantum entanglement breaks intuition badly"),
    ],
)
@pytest.mark.parametrize("max_tags", [1, 3, 5, 10, 15])
def test_shorts_pyramid_is_three_to_five_tags_with_layer_shape(title, hook, max_tags):
    broad, niche, specific = hashtag_layers(title, hook, max_tags=max_tags)
    tags = [*broad, *niche, *specific]
    assert HASHTAG_MIN <= len(tags) <= HASHTAG_MAX
    assert 1 <= len(broad) <= 2
    assert 1 <= len(niche) <= 2
    assert len(specific) == 1
    lowered = [t.lower() for t in tags]
    assert len(lowered) == len(set(lowered))
    assert all(t.startswith("#") for t in tags)


def test_shorts_pyramid_uses_shorts_broad_layer_and_hook_specific_tag():
    broad, niche, specific = hashtag_layers("Freaked out robot", "Robot hand reveal")
    assert broad == list(SHORTS_BROAD_LAYER)
    assert specific == ["#Robot"]  # first keyword comes from the hook
    assert "#FYP" in niche


def test_build_viral_hashtags_never_exceeds_five_and_never_empty():
    for title, hook in [("Long title with many distinct keywords here", "Another hook phrase")]:
        tags = build_viral_hashtags(title, hook, max_tags=15)
        assert 1 <= len(tags) <= 5
        assert len(tags) >= HASHTAG_MIN


def test_build_viral_hashtags_with_no_keywords_still_has_three_to_five():
    tags = build_viral_hashtags("", "")
    assert HASHTAG_MIN <= len(tags) <= HASHTAG_MAX
    assert tags[0] == "#Shorts"


def test_longform_pyramid_does_not_claim_shorts_and_keeps_shape():
    tags = build_hashtag_pyramid(
        "Ancient Roman evening journal habit", broad=LONGFORM_BROAD_LAYER, niche_anchor=None
    )
    assert "#Shorts" not in tags
    assert tags[:2] == list(LONGFORM_BROAD_LAYER)
    assert HASHTAG_MIN <= len(tags) <= HASHTAG_MAX


@pytest.mark.parametrize("topic", TOPIC_KEYS)
def test_longform_description_has_three_to_five_hashtags(topic):
    script = build_script(topic, provider="api", config=None)
    desc = description_for(script)
    last_line = desc.splitlines()[-1]
    tags = _hashtags_in(last_line)
    assert HASHTAG_MIN <= len(tags) <= HASHTAG_MAX, tags
    assert len(tags) == len(set(t.lower() for t in tags))


# --- 2. Hook gate -----------------------------------------------------------------


@pytest.mark.parametrize("topic", TOPIC_KEYS)
def test_longform_hook_lands_in_first_three_seconds(topic):
    script = build_script(topic, provider="api", config=None)
    assert hook_in_first_seconds(script.sections[0].narration, script.hook)


def test_hook_gate_rejects_hook_buried_late_in_narration():
    narration = (
        "Set the scene with a long preamble about nothing. " * 3 + "Discipline is not a feeling"
    )
    assert not hook_in_first_seconds(narration, "Discipline is not a feeling")


def test_hook_gate_accepts_hook_at_start_and_empty_hook_fails():
    assert hook_in_first_seconds(
        "Discipline is not a feeling, it is a choice.", "Discipline is not a feeling"
    )
    assert not hook_in_first_seconds("anything", "")


# --- 3. Pattern-interrupt cadence ----------------------------------------------------


@pytest.mark.parametrize("cadence", [2.5, 2.75, 3.0, 3.25, 3.5])
def test_pattern_interrupt_spacing_within_accepted_band(cadence):
    times = pattern_interrupt_times(60.0, cadence)
    assert times, "expected interrupts in a 60 s clip"
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    assert all(
        PATTERN_INTERRUPT_MIN_SEC - 1e-6 <= g <= PATTERN_INTERRUPT_MAX_SEC + 1e-6 for g in gaps
    )
    assert all(0 < t < 60.0 for t in times)


def test_pattern_interrupt_default_cadence_and_hook_owns_first_window():
    times = pattern_interrupt_times(30.0)
    assert times[0] >= PATTERN_INTERRUPT_MIN_SEC
    assert times == sorted(times)


@pytest.mark.parametrize("bad", [2.0, 4.0, 0.0, -1.0])
def test_pattern_interrupt_rejects_cadence_outside_band(bad):
    with pytest.raises(ValueError):
        pattern_interrupt_times(60.0, bad)


@pytest.mark.parametrize("duration", [0.0, -5.0, float("nan")])
def test_pattern_interrupt_empty_for_non_positive_duration(duration):
    assert pattern_interrupt_times(duration) == []


def test_pattern_interrupt_rate_is_bounded_by_cadence():
    # 180 s at 2.5 s cadence is at most 72 events and at least 60.
    times = pattern_interrupt_times(180.0, 2.5)
    assert 60 <= len(times) <= 72


# --- 4. Loop continuity -------------------------------------------------------------


def test_loop_continuity_is_prompt_only_not_enforced_in_code():
    # The loop-back ending is requested in the Shorts brain prompt only. This asserts
    # the prompt still carries the instruction; no renderer-level seam check exists.
    from pathlib import Path

    orchestrator = (
        Path(__file__).resolve().parents[1] / "src" / "autoshorts" / "brain" / "orchestrator.py"
    )
    text = orchestrator.read_text(encoding="utf-8")
    assert "loopable ending" in text
