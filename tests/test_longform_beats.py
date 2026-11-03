"""Tests for the per-section beat planner and renderer."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

import autoshorts.longform.background as background
import autoshorts.longform.beats as beats
import autoshorts.longform.sprites as sprites
from autoshorts.core.theme import ACCENT_CORAL, ACCENT_TEAL, NAVY_LIGHT, OFFWHITE, YELLOW_SOFT
from autoshorts.longform.beats import (
    FIGURE_SPRITE_SCALE,
    LYING_POSES,
    NARRATION_BAND,
    PALETTE,
    POSE_CYCLE,
    RIG_ORIGIN,
    SPHERE_CENTRE,
    BeatPlan,
    _choose_gesture,
    _entrance_start,
    _figure_joints,
    _head_turn,
    _highest_figure_y,
    _lift_origin,
    _split_sentences,
    draw_beat,
    plan_beat,
)
from autoshorts.longform.props import PROP_NAMES
from autoshorts.longform.rig import EXPRESSIONS, POSES
from autoshorts.longform.sprite_rig import wrist_offset

FRAME = (1920, 1080)


def _names(plan: BeatPlan) -> list[str]:
    return [prop.name for prop in plan.props]


def _has_colour(img: Image.Image, colour: tuple[int, int, int]) -> bool:
    colours = img.getcolors(maxcolors=FRAME[0] * FRAME[1]) or []
    return any(rgb == colour for _count, rgb in colours)


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


def test_win_the_day_adds_no_calendar_prop() -> None:
    plan = plan_beat(0, "H", "Win the day and celebrate with fun!", "host", False)

    assert "calendar" not in _names(plan)
    assert plan.pose == "thumbs_up"


def test_calendar_needs_a_scheduling_word_or_every_day() -> None:
    assert "calendar" in _names(plan_beat(0, "H", "Check your schedule.", "host", False))
    assert "calendar" in _names(plan_beat(0, "H", "Mark the deadline.", "host", False))
    assert "calendar" in _names(plan_beat(0, "H", "Do it every day.", "host", False))
    assert "calendar" not in _names(plan_beat(0, "H", "Every time counts.", "host", False))


def test_time_alone_does_not_add_a_clock() -> None:
    assert "clock_wall" not in _names(plan_beat(0, "H", "Time to punch the bag.", "host", False))
    assert "clock_wall" in _names(plan_beat(0, "H", "Look at the clock.", "host", False))


def test_plan_is_deterministic() -> None:
    args = (4, "Heading", "Phone scroll, then the desk and a big number 42.", "host", True)
    assert plan_beat(*args) == plan_beat(*args)


def test_default_window_and_pose_cycle() -> None:
    for index in range(len(POSE_CYCLE) + 1):
        plan = plan_beat(index, "H", "Nothing special here.", "host", False)
        assert _names(plan) == ["window"]
        assert plan.pose == POSE_CYCLE[index % len(POSE_CYCLE)]
        assert plan.gesture is None
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


def test_mood_rules_pick_body_position_and_expression() -> None:
    bed = plan_beat(0, "H", "I went to bed late.", "host", False)
    assert bed.pose == "sleep_bed" and bed.expression == "tired"

    prone = plan_beat(0, "H", "Sleep on your stomach.", "host", False)
    assert prone.pose == "lie_bed_prone" and prone.expression == "tired"

    phone = plan_beat(0, "H", "Stop the phone scroll.", "host", False)
    assert phone.pose == "phone_hold" and phone.expression == "tired"

    desk = plan_beat(0, "H", "Deep work at the desk.", "host", False)
    assert desk.pose == "typing" and desk.expression == "focus"

    chair = plan_beat(0, "H", "Sit down in the chair.", "host", False)
    assert chair.pose == "sit_chair"

    fail = plan_beat(0, "H", "You will fail again.", "host", False)
    assert fail.pose == "facepalm" and fail.expression == "worried"

    sad = plan_beat(0, "H", "It is not fair to you.", "host", False)
    assert sad.pose == "slump" and sad.expression == "sad"

    win = plan_beat(0, "H", "A better new habit wins.", "host", False)
    assert win.pose == "thumbs_up" and win.expression == "excited"

    start = plan_beat(0, "H", "Start today.", "host", False)
    assert start.pose == "fist_up" and start.expression == "excited"

    anger = plan_beat(0, "H", "This is unfair and I am angry.", "host", False)
    assert anger.pose == "hug_self" and anger.expression == "angry"

    surprise = plan_beat(0, "H", "Wait, what just happened?", "host", False)
    assert surprise.pose == "point_side" and surprise.expression == "surprised"


def test_keyword_rules_pick_expected_props_and_poses() -> None:
    couch = plan_beat(0, "H", "I feel tired on the couch.", "host", False)
    assert "couch" in _names(couch) and couch.pose == "slump_couch"
    assert couch.expression == "tired"

    bag = plan_beat(0, "H", "Time to punch the bag.", "host", False)
    assert "heavy_bag" in _names(bag) and bag.pose == "box_punch"

    climb = plan_beat(0, "H", "We climb the mountain.", "host", False)
    assert "mountain" in _names(climb) and climb.pose == "climb_slope"

    summit = plan_beat(0, "H", "Stand at the top of the mountain.", "host", False)
    assert summit.pose == "summit_stand"


def test_planned_names_and_poses_exist_in_libraries() -> None:
    narration = "bed alarm phone couch desk bag mountain excuse every day look you maybe"
    plan = plan_beat(0, "H", narration, "host", True)

    assert all(prop.name in PROP_NAMES for prop in plan.props)
    assert plan.pose in POSES
    assert plan.gesture is None or plan.gesture in POSES
    assert plan.expression in EXPRESSIONS


def test_numbers_become_number_values() -> None:
    plan = plan_beat(0, "H", "Saved 42 dollars in 3 weeks.", "host", False)

    assert plan.number_values == ["42", "3"]


def test_look_narration_gets_point_up_gesture() -> None:
    plan = plan_beat(0, "H", "Look at the window.", "host", False)

    assert plan.gesture == "gesture_point_up"
    assert plan.pose in ("look_left", "look_right")


def test_gesture_rules_follow_keywords_in_priority_order() -> None:
    # Index 0 is the stand pose, which takes gestures.
    assert plan_beat(0, "H", "You can do this.", "host", False).gesture == "gesture_open_hands"
    assert plan_beat(0, "H", "Maybe the rain stops.", "host", False).gesture == "gesture_shrug"
    # "not" sets the slump mood, so these two sentences only reach the shrug rule directly.
    assert _choose_gesture({"maybe"}, "maybe") == "gesture_shrug"
    assert _choose_gesture({"i", "am", "not", "sure"}, "i am not sure.") == "gesture_shrug"
    # "look" outranks "you" because the point rule is checked first.
    assert plan_beat(0, "H", "Look, you can see it.", "host", False).gesture == "gesture_point_up"
    plain = plan_beat(0, "H", "Nothing special here.", "host", False)
    assert plain.gesture is None and plain.pose == POSE_CYCLE[0]


def test_gesture_does_not_replace_seated_lying_or_desk_poses() -> None:
    # Words that would pick a gesture must not stand a seated, lying or desk figure up.
    bed = plan_beat(0, "H", "You will sleep in bed tonight.", "host", False)
    assert bed.pose == "sleep_bed" and bed.gesture is None
    chair = plan_beat(0, "H", "Look, you can sit in the chair.", "host", False)
    assert chair.pose == "sit_chair" and chair.gesture is None
    desk = plan_beat(0, "H", "You should focus at the desk.", "host", False)
    assert desk.pose == "typing" and desk.gesture is None
    phone = plan_beat(0, "H", "You scroll the phone.", "host", False)
    assert phone.pose == "phone_hold" and phone.gesture is None


def test_consecutive_sections_without_keywords_get_different_themes() -> None:
    themes = [plan_beat(i, "H", "Nothing special here.", "host", False).theme for i in range(10)]
    assert all(a != b for a, b in zip(themes, themes[1:], strict=False))
    assert set(themes) <= set(background.theme_names())


def test_sleep_narration_is_navy_night_with_sleep_bed() -> None:
    plan = plan_beat(3, "H", "It is time to sleep.", "host", False)

    assert plan.theme == "navy_night"
    assert plan.pose == "sleep_bed" and plan.expression in ("sleepy", "tired")
    assert "bed" in _names(plan) and "lamp" in _names(plan)


def test_typing_desk_narration_is_office_warm_and_typing() -> None:
    plan = plan_beat(2, "H", "Typing at my desk all day.", "host", False)

    assert plan.theme == "office_warm"
    assert plan.pose == "typing" and plan.expression == "focus"


def test_reading_and_study_use_reading_pose_with_bookshelf() -> None:
    plan = plan_beat(1, "H", "Read a book to study.", "host", False)

    assert plan.pose == "reading" and plan.expression == "focus"
    assert "bookshelf" in _names(plan) and "lamp" in _names(plan)


def test_scene_props_are_capped_at_four() -> None:
    narration = (
        "Sleep in bed with the phone, the couch, the desk, a plant, the tv, "
        "the trash and the clock, at night, every day."
    )
    plan = plan_beat(0, "H", narration, "host", False)

    assert len(plan.props) <= beats.MAX_PROPS
    assert all(prop.name in PROP_NAMES for prop in plan.props)


def test_every_theme_draws_rgb_frame() -> None:
    narration = "Every day you choose."
    plan = plan_beat(0, "H", narration, "host", False)
    for name in background.theme_names():
        img = Image.new("RGB", FRAME, PALETTE.void)
        draw_beat(img, replace(plan, theme=name), 1.0, 0.0, 4.0, narration)
        assert img.mode == "RGB" and img.size == FRAME


def test_head_turn_helper() -> None:
    assert _head_turn("Light from the window.") == 0.5
    assert _head_turn("Still in bed.") == 0.5
    assert _head_turn("Nothing about that here.") == -0.5


def test_standing_head_clears_narration_band_at_default_scale() -> None:
    narration = "Nothing special here."
    plan = plan_beat(0, "H", narration, "host", False)
    assert plan.pose == "stand"
    joints = _figure_joints(plan, 0.0, narration, 5.0)

    top = _highest_figure_y(joints, RIG_ORIGIN[1], FIGURE_SPRITE_SCALE)
    assert NARRATION_BAND[1] < top
    # Feet already stand on the floor, so the lift never moves the figure.
    assert _lift_origin(joints, RIG_ORIGIN, FIGURE_SPRITE_SCALE) == RIG_ORIGIN


def test_win_narration_draws_rgb_frame() -> None:
    narration = "Win the day and celebrate with fun!"
    plan = plan_beat(0, "H", narration, "host", False)
    img = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(img, plan, 3.0, 0.0, 4.0, narration)

    assert plan.pose == "thumbs_up"
    assert img.mode == "RGB" and img.size == FRAME


def test_figure_joints_raise_no_arm_by_default() -> None:
    from autoshorts.longform.beats import _figure_joints

    plan = plan_beat(0, "H", "Stand tall.", "host", False)
    joints = _figure_joints(plan, 0.0, "Stand tall.", 5.0)
    assert joints["r_upper"] == POSES["stand"]["r_upper"]
    assert joints["r_lower"] == POSES["stand"]["r_lower"]


def test_bed_lying_figure_is_over_the_bed_and_desk_monitor_is_at_the_hand() -> None:
    from autoshorts.longform.beats import (
        BED_SCALE,
        BED_XY,
        DESK_MONITOR_SCALE,
        FIGURE_SPRITE_SCALE,
        LYING_HIP,
    )

    plan = plan_beat(0, "H", "I went to bed late.", "host", False)
    assert plan.pose in LYING_POSES and plan.mirror is False
    bed = next(prop for prop in plan.props if prop.name == "bed")
    assert (bed.x, bed.y, bed.scale) == (*BED_XY, BED_SCALE)
    # The pillow is at the bed's left end; the lying hip sits to its right, so the head lands on it.
    assert LYING_HIP[0] > BED_XY[0] + 100 and LYING_HIP[1] > BED_XY[1] + 80

    desk = plan_beat(0, "H", "Deep work at the desk.", "host", False)
    monitor = next(prop for prop in desk.props if prop.name == "desk_monitor")
    hand_dx, hand_dy = wrist_offset(POSES["typing"], FIGURE_SPRITE_SCALE, "l")
    hand = (RIG_ORIGIN[0] + hand_dx, RIG_ORIGIN[1] + hand_dy)
    assert monitor.scale == DESK_MONITOR_SCALE
    assert ((monitor.x - hand[0]) ** 2 + (monitor.y - hand[1]) ** 2) ** 0.5 <= 120.0


def test_sentence_split_keeps_order() -> None:
    assert _split_sentences("First one. Second one! Third?") == [
        "First one.",
        "Second one!",
        "Third?",
    ]
    assert _split_sentences("   ") == [""]


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


def test_rim_light_box_is_gone() -> None:
    assert not hasattr(beats, "rim_light")
    assert not hasattr(beats, "RIG_BOX")


def test_narration_highlight_uses_yellow_soft() -> None:
    narration = "You choose the desk."
    plan = plan_beat(0, "H", narration, "host", False)
    img = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(img, plan, 3.0, 0.0, 4.0, narration)

    assert _has_colour(img, YELLOW_SOFT)


def test_narration_without_highlight_words_has_no_yellow_text() -> None:
    narration = "Nothing about that here."
    plan = plan_beat(0, "H", narration, "host", False)
    img = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(img, plan, 3.0, 0.0, 4.0, narration)

    assert not _has_colour(img, YELLOW_SOFT)


def test_palette_uses_theme_tokens() -> None:
    assert PALETTE.floor == NAVY_LIGHT
    assert PALETTE.text == OFFWHITE
    assert PALETTE.highlight == YELLOW_SOFT
    assert PALETTE.positive == ACCENT_TEAL
    assert PALETTE.negative == ACCENT_CORAL
    assert PALETTE.inner_voice == ACCENT_CORAL


@pytest.fixture
def use_sprite_dir(monkeypatch: pytest.MonkeyPatch):
    """Point the sprite loader at a chosen folder and reset its cache around the test."""

    def use(folder: Path) -> None:
        monkeypatch.setattr(sprites, "SPRITE_DIR", folder)
        sprites.load_sprite.cache_clear()

    yield use
    sprites.load_sprite.cache_clear()


def _write_solid(
    folder: Path, name: str, size: tuple[int, int], colour: tuple[int, int, int]
) -> None:
    Image.new("RGBA", size, (*colour, 255)).save(folder / f"{name}.png")


def _write_sprites(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    _write_solid(folder, "navy_gradient", (1920, 1080), (30, 60, 120))
    _write_solid(folder, "figure", (400, 800), (240, 220, 200))
    for name in ("window", "phone", "alarm_clock", "mountain", "heavy_bag"):
        _write_solid(folder, name, (120, 120), (200, 50, 50))
    return folder


def _render(plan: BeatPlan, narration: str, t: float = 1.5) -> Image.Image:
    img = Image.new("RGB", FRAME, PALETTE.void)
    draw_beat(img, plan, t, 0.0, 4.0, narration)
    return img


def test_draw_beat_renders_without_sprites(tmp_path: Path, use_sprite_dir) -> None:
    use_sprite_dir(tmp_path)  # empty folder: every sprite is missing
    narration = "Stop the phone scroll, then the desk."
    plan = plan_beat(0, "H", narration, "host", False)
    img = _render(plan, narration)
    assert img.mode == "RGB"
    assert img.size == FRAME

    figure_plan = replace(plan_beat(0, "H", "Stand tall.", "host", False), expression="neutral")
    assert figure_plan.pose == "stand" and figure_plan.gesture is None
    figure = _render(figure_plan, "Stand tall.")
    assert figure.mode == "RGB"
    assert figure.size == FRAME


def test_sprites_change_frame_compared_with_fallback(tmp_path: Path, use_sprite_dir) -> None:
    narration = "Stop the phone scroll."
    # The navy default is the only theme that uses the backdrop sprite.
    plan = replace(
        plan_beat(0, "H", narration, "host", False), expression="neutral", theme="navy_night"
    )
    assert "phone" in _names(plan)

    empty = tmp_path / "empty"
    empty.mkdir()
    use_sprite_dir(empty)
    fallback = _render(plan, narration)

    use_sprite_dir(_write_sprites(tmp_path / "full"))
    with_sprites = _render(plan, narration)

    assert with_sprites.tobytes() != fallback.tobytes()
    # Backdrop sprite is pasted over the whole frame; the corner is its navy colour.
    assert with_sprites.getpixel((5, 5)) == (30, 60, 120)
