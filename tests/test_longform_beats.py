"""Tests for the per-section beat planner and renderer."""

from __future__ import annotations

from dataclasses import replace

from PIL import Image

from autoshorts.longform.beats import (
    PALETTE,
    POSE_CYCLE,
    SPHERE_CENTRE,
    BeatPlan,
    _draw_parallax,
    _entrance_start,
    draw_beat,
    plan_beat,
)
from autoshorts.longform.props import PROP_NAMES
from autoshorts.longform.rig import EXPRESSIONS, POSES

FRAME = (1920, 1080)


def _names(plan: BeatPlan) -> list[str]:
    return [prop.name for prop in plan.props]


def test_excuse_narration_gives_shadow_demon_plan() -> None:
    plan = plan_beat(2, "Excuses", "Every time I make an excuse, the shadow wins.", "host", False)

    assert "shadow_demon" in _names(plan)
    assert plan.mood == PALETTE.red
    assert plan.inner_voice == "Just one more excuse"


def test_calendar_marks_equal_day_words() -> None:
    narration = "Every day you skip is another day lost, and daily habits decide it."
    plan = plan_beat(1, "Habits", narration, "host", False)

    calendar = [prop for prop in plan.props if prop.name == "calendar"]
    assert len(calendar) == 1
    day_count = sum(1 for word in narration.lower().split() if word.strip(".,") in {"day", "daily"})
    assert calendar[0].params["marks"] == day_count == 3


def test_plan_is_deterministic() -> None:
    args = (4, "Heading", "Phone scroll, then the desk and a big number 42.", "host", True)
    assert plan_beat(*args) == plan_beat(*args)


def test_default_window_and_pose_cycle() -> None:
    for index in range(len(POSE_CYCLE) + 1):
        plan = plan_beat(index, "H", "Nothing special here.", "host", False)
        assert _names(plan) == ["window"]
        assert plan.pose == POSE_CYCLE[index % len(POSE_CYCLE)]
        assert plan.mirror == (index % 2 == 1)
        assert plan.expression == ("exhausted" if index % 2 == 0 else "grit")
        assert plan.headband is False


def test_last_section_is_determined_with_headband() -> None:
    plan = plan_beat(3, "End", "The end.", "host", True)

    assert plan.expression == "determined"
    assert plan.headband is True


def test_phone_adds_blue_light_source() -> None:
    plan = plan_beat(0, "H", "Stop the phone scroll.", "host", False)

    assert "phone" in _names(plan)
    assert len(plan.light_sources) == 1
    assert plan.light_sources[0].color == PALETTE.blue


def test_keyword_rules_pick_expected_props_and_poses() -> None:
    couch = plan_beat(0, "H", "I feel tired on the couch.", "host", False)
    assert "couch" in _names(couch) and couch.pose == "slump_couch"

    bag = plan_beat(0, "H", "Time to punch the bag.", "host", False)
    assert "heavy_bag" in _names(bag) and bag.pose == "box_punch"

    climb = plan_beat(0, "H", "We climb the mountain.", "host", False)
    assert "mountain" in _names(climb) and climb.pose == "climb_slope"

    summit = plan_beat(0, "H", "Stand at the top of the mountain.", "host", False)
    assert summit.pose == "summit_stand"


def test_planned_names_and_poses_exist_in_libraries() -> None:
    narration = "bed alarm phone couch desk bag mountain excuse every day"
    plan = plan_beat(0, "H", narration, "host", True)

    assert all(prop.name in PROP_NAMES for prop in plan.props)
    assert plan.pose in POSES
    assert plan.expression in EXPRESSIONS


def test_numbers_become_number_values() -> None:
    plan = plan_beat(0, "H", "Saved 42 dollars in 3 weeks.", "host", False)

    assert plan.number_values == ["42", "3"]


def test_draw_beat_returns_rgb_frame_and_changes_over_time() -> None:
    narration = "Every day you choose the phone or the desk."
    plan = plan_beat(1, "Choices", narration, "host", False)
    words = [
        {"word": word, "start": 0.2 + i * 0.4, "end": 0.6 + i * 0.4}
        for i, word in enumerate(narration.split())
    ]

    first = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(first, plan, 0.5, 0.0, 4.0, narration, words)
    second = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(second, plan, 2.5, 0.0, 4.0, narration, words)

    assert first.mode == "RGB"
    assert first.size == FRAME
    assert first.tobytes() != second.tobytes()


def test_draw_beat_rejects_wrong_frame() -> None:
    plan = plan_beat(0, "H", "Hello.", "host", False)
    try:
        draw_beat(Image.new("RGB", (640, 360)), plan, 0.0, 0.0, 1.0, "Hello.")
    except ValueError:
        return
    raise AssertionError("expected ValueError for a non-1920x1080 frame")


def test_space_topic_draws_planet_pixels() -> None:
    narration = "The planet earth turns in orbit."
    plan = plan_beat(0, "Space", narration, "host", False)
    img = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(img, plan, 1.0, 0.0, 3.0, narration)

    plain = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(plain, plan, 1.0, 0.0, 3.0, "Nothing about that here.")

    assert img.getpixel(SPHERE_CENTRE) != plain.getpixel(SPHERE_CENTRE)
    assert img.getpixel(SPHERE_CENTRE) != PALETTE.void


def test_entrance_after_shot_end_leaves_no_prop_pixels() -> None:
    narration = "Lie in bed."
    plan = plan_beat(0, "H", narration, "host", False)
    assert "bed" in _names(plan)
    words = [{"word": "bed", "start": 3.0, "end": 3.4}]
    without_bed = replace(plan, props=[p for p in plan.props if p.name != "bed"])

    late = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(late, plan, 1.5, 0.0, 2.0, "", words)
    absent = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(absent, without_bed, 1.5, 0.0, 2.0, "", words)
    assert late.tobytes() == absent.tobytes()

    early_words = [{"word": "bed", "start": 0.5, "end": 0.9}]
    shown = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(shown, plan, 1.5, 0.0, 2.0, "", early_words)
    assert shown.tobytes() != absent.tobytes()


def test_entrance_start_follows_naming_word_or_shot_start() -> None:
    words = [
        {"word": "Sleep.", "start": 4.0, "end": 4.3},
        {"word": "bed", "start": 2.5, "end": 2.8},
    ]
    assert _entrance_start("bed", words, 0.0) == 2.5
    assert _entrance_start("phone", words, 0.0) == 0.0
    assert _entrance_start("bed", None, 1.25) == 1.25


def test_parallax_moves_background_shapes() -> None:
    first = Image.new("RGB", FRAME, PALETTE.void)
    _draw_parallax(first, 0.0)
    second = Image.new("RGB", FRAME, PALETTE.void)
    _draw_parallax(second, 2.0)

    assert first.tobytes() != second.tobytes()
