"""Smoke tests for the flat-vector longform visuals: transitions, characters and fonts."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from autoshorts.core.theme import FONT_DISPLAY, lerp_rgb
from autoshorts.longform import drawer
from autoshorts.longform.drawer import (
    ACCENT_GOLD,
    DAY_MILESTONES,
    HEIGHT,
    POSES,
    TRANSITIONS,
    WIDTH,
    _camera_zoom,
    _draw_chrome,
    _opener_zoom,
    _push_in_zoom,
    _resolve_day_label,
    _scene_background,
    _shot_camera,
    _transition,
    _zoom_centre,
    draw_character,
    font_candidates,
    render_beat_frame,
    render_scene_frame,
    render_scene_frames,
)
from autoshorts.longform.shots import Shot
from autoshorts.longform.types import Section, SectionAudio

SIZE = (1920, 1080)


def _frame(scene: str) -> Image.Image:
    return _scene_background(scene).copy()


LEGACY_TRANSITIONS = ("slide", "zoom", "iris", "wipe", "crossfade", "push")


def test_transition_list_is_hard_cuts_dips_and_flashes() -> None:
    assert TRANSITIONS == ("cut", "dip", "flash", "cut", "dip", "cut")


def test_cut_returns_next_frame_unchanged() -> None:
    prev = _frame("bedroom")
    cur = _frame("street")
    assert _transition("cut", prev, cur, 0.5) is cur


def test_dip_passes_through_black_midway() -> None:
    prev = Image.new("RGB", SIZE, (200, 200, 200))
    cur = Image.new("RGB", SIZE, (200, 200, 200))
    assert _transition("dip", prev, cur, 0.5).getpixel((10, 10)) == (0, 0, 0)


@pytest.mark.parametrize("kind", tuple(dict.fromkeys(TRANSITIONS + LEGACY_TRANSITIONS)))
@pytest.mark.parametrize("k", [0.0, 0.5, 1.0])
def test_transition_renders_full_hd_frame(kind: str, k: float, tmp_path: Path) -> None:
    prev = _frame("bedroom")
    cur = _frame("street")
    out = _transition(kind, prev, cur, k)
    assert isinstance(out, Image.Image)
    assert out.size == SIZE
    if k == 0.5:
        out.save(tmp_path / f"transition_{kind}.png")
        assert (tmp_path / f"transition_{kind}.png").exists()


def test_render_scene_frames_yields_full_hd_frames_with_expected_count() -> None:
    words = [
        {"word": "Not", "start": 0.1, "end": 0.4},
        {"word": "lazy", "start": 0.5, "end": 0.9},
        {"word": "today.", "start": 1.0, "end": 1.4},
    ]
    sections = [
        SectionAudio(
            section=Section(heading="Why not", narration="Not lazy today."),
            audio_path=Path("unused.wav"),
            words=words,
            duration=2.0,
        ),
        SectionAudio(
            section=Section(heading="Next", narration="Keep going."),
            audio_path=Path("unused.wav"),
            words=[],
            duration=1.0,
        ),
    ]
    frames = list(render_scene_frames(sections, "Demo", scenes=("bedroom", "street")))
    expected = round(2.0 * drawer.FPS) + round(1.0 * drawer.FPS)
    assert len(frames) == expected
    assert len(frames[0]) == WIDTH * HEIGHT * 3


def test_render_scene_frames_caps_each_section_and_yields_full_hd_bytes() -> None:
    impact_words = [
        {"word": "Not", "start": 0.1, "end": 0.4},
        {"word": "lazy", "start": 0.5, "end": 0.9},
        {"word": "every", "start": 1.0, "end": 1.3},
        {"word": "day.", "start": 1.4, "end": 1.8},
    ]
    sections = [
        SectionAudio(
            section=Section(heading="Not again", narration="Not lazy. Every day."),
            audio_path=Path("unused.wav"),
            words=impact_words,
            duration=2.5,
        ),
        SectionAudio(
            section=Section(heading="Keep going", narration="Keep going."),
            audio_path=Path("unused.wav"),
            words=[],
            duration=2.0,
        ),
    ]
    frames = list(render_scene_frames(sections, "Demo", max_seconds_per_section=1.0))
    assert len(frames) == 2 * round(1.0 * drawer.FPS)
    assert all(len(frame) == WIDTH * HEIGHT * 3 for frame in frames)


def test_push_in_zoom_runs_from_1_0_to_1_06_across_a_section() -> None:
    frame_count = round(3.0 * drawer.FPS)
    assert _push_in_zoom(0, frame_count) == pytest.approx(1.0)
    assert _push_in_zoom(frame_count - 1, frame_count) == pytest.approx(1.06)
    mid = _push_in_zoom(frame_count // 2, frame_count)
    assert 1.0 < mid < 1.06


def test_camera_zoom_middle_section_ends_at_1_06() -> None:
    frame_count = round(2.0 * drawer.FPS)
    assert _camera_zoom(2, frame_count - 1, frame_count) == pytest.approx(1.06)
    assert _camera_zoom(2, 0, frame_count) == pytest.approx(1.0)


def test_opener_pulls_back_from_wide_over_first_two_seconds() -> None:
    assert _opener_zoom(0) == pytest.approx(1.12)
    assert _opener_zoom(round(drawer.OPENER_SECONDS * drawer.FPS)) == pytest.approx(1.0)
    # Only section 0 carries the opener.
    frame_count = round(2.0 * drawer.FPS)
    assert _camera_zoom(0, 0, frame_count) == pytest.approx(1.12)
    assert _camera_zoom(1, 0, frame_count) == pytest.approx(1.0)


def test_push_in_keeps_frame_count_per_section() -> None:
    sections = [
        SectionAudio(
            section=Section(heading="Open", narration="One."),
            audio_path=Path("unused.wav"),
            words=[],
            duration=1.5,
        ),
        SectionAudio(
            section=Section(heading="Middle", narration="Two."),
            audio_path=Path("unused.wav"),
            words=[],
            duration=1.0,
        ),
    ]
    frames = list(render_scene_frames(sections, "Demo", scenes=("bedroom", "street")))
    assert len(frames) == round(1.5 * drawer.FPS) + round(1.0 * drawer.FPS)


def test_character_frame_renders_at_full_hd(tmp_path: Path) -> None:
    img = _frame("office")
    draw = ImageDraw.Draw(img)
    draw_character(
        draw,
        POSES["point"],
        (520, 640),
        shirt=drawer.SHIRT_BLUE,
        mood="smug",
        viseme="AI",
        pointer_stick=True,
        is_speaking=True,
    )
    assert img.size == (WIDTH, HEIGHT)
    img.save(tmp_path / "character.png")
    assert (tmp_path / "character.png").exists()


def test_font_candidates_put_theme_family_first() -> None:
    assert font_candidates(True)[0].startswith(FONT_DISPLAY[0])
    # Arial remains in the fallback chain.
    assert any("arial" in c.lower() for c in font_candidates(True))
    assert any("arial" in c.lower() for c in font_candidates(False))


def test_lerp_gradient_helper_endpoints() -> None:
    img = Image.new("RGB", (40, 40), (0, 0, 0))
    drawer._gradient(img, (0, 0, 40, 40), (0, 0, 0), (200, 100, 50))
    assert img.getpixel((0, 0)) == lerp_rgb((0, 0, 0), (200, 100, 50), 0.0)
    assert img.getpixel((0, 39)) == (200, 100, 50)


def test_resolve_day_label_explicit_text() -> None:
    assert _resolve_day_label(0, heading="Day 45: The Grind") == "DAY 45"
    assert _resolve_day_label(2, narration="It was day 100.") == "DAY 100"
    assert _resolve_day_label(0, heading="DAY 7") == "DAY 7"
    assert _resolve_day_label(5, heading="day 64") == "DAY 64"
    assert (
        _resolve_day_label(1, heading="No day here", narration="On day 365 we made it") == "DAY 365"
    )


def test_resolve_day_label_milestone_table() -> None:
    expected_milestones = (1, 3, 12, 30, 64, 90, 150, 270, 365)
    assert DAY_MILESTONES == expected_milestones
    for idx, expected in enumerate(expected_milestones):
        assert _resolve_day_label(idx) == f"DAY {expected}"

    # Clamping below 0 and above max index
    assert _resolve_day_label(-5) == "DAY 1"
    assert _resolve_day_label(99) == "DAY 365"


def test_draw_chrome_renders_day_label_and_gold_accent_tick() -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), (40, 40, 40))
    draw = ImageDraw.Draw(img)

    # 3-arg call without narration
    _draw_chrome(draw, 0, "The Awakening")

    # Letterbox bars are pure black
    assert img.getpixel((500, 10)) == (0, 0, 0)
    assert img.getpixel((500, 1050)) == (0, 0, 0)

    # Gold/yellow left accent tick (3px wide in ACCENT_GOLD) drawn at pill left edge x=72..74
    tick_pixel = img.getpixel((73, 25))
    assert tick_pixel == ACCENT_GOLD

    # Test with explicit day in narration
    img2 = Image.new("RGB", (WIDTH, HEIGHT), (40, 40, 40))
    draw2 = ImageDraw.Draw(img2)
    _draw_chrome(draw2, 1, "The Grind", narration="On day 90 everything changed")
    assert img2.getpixel((73, 25)) == ACCENT_GOLD


def test_shot_camera_without_focus_xy_preserves_center_zoom() -> None:
    base = Image.new("RGB", (WIDTH, HEIGHT), (10, 20, 30))
    # Place an asymmetric marker
    base.putpixel((500, 500), (255, 0, 0))

    shot = Shot(0.0, 1.0, "ecu")
    res_none = _shot_camera(base, shot, 0.0, camera_zoom=1.0, focus_xy=None)
    expected = _zoom_centre(base, drawer.SHOT_ZOOM["ecu"])

    assert res_none.tobytes() == expected.tobytes()


def test_shot_camera_with_focus_xy_shifts_crop_center() -> None:
    base = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    # Create a gradient so pixel shifts produce distinct values
    draw = ImageDraw.Draw(base)
    for x in range(0, WIDTH, 20):
        draw.line([(x, 0), (x, HEIGHT)], fill=(x % 256, 128, 128))

    shot_ecu = Shot(0.0, 1.0, "ecu")
    # focus toward top-left
    res_focused = _shot_camera(base, shot_ecu, 0.0, camera_zoom=1.0, focus_xy=(200.0, 150.0))
    res_center = _shot_camera(base, shot_ecu, 0.0, camera_zoom=1.0, focus_xy=None)

    # Focused image should differ from center crop
    assert res_focused.tobytes() != res_center.tobytes()

    # When shot is "wide" (not in ecu/insert/close), focus_xy has no effect
    shot_wide = Shot(0.0, 1.0, "wide")
    res_wide_focused = _shot_camera(base, shot_wide, 0.0, camera_zoom=1.0, focus_xy=(200.0, 150.0))
    res_wide_center = _shot_camera(base, shot_wide, 0.0, camera_zoom=1.0, focus_xy=None)
    assert res_wide_focused.tobytes() == res_wide_center.tobytes()


def test_render_scene_frame_and_beat_frame_single_frame() -> None:
    sa = SectionAudio(
        section=Section(heading="Day 14 - Breakthrough", narration="We broke through on day 14"),
        audio_path=Path("unused.wav"),
        words=[],
        duration=1.0,
    )
    frame = render_scene_frame(sa, title="Discipline", index=0, t=0.1)
    assert isinstance(frame, Image.Image)
    assert frame.size == (WIDTH, HEIGHT)

    # render_beat_frame is an alias to render_scene_frame
    frame_beat = render_beat_frame(sa, title="Discipline", index=0, t=0.1)
    assert isinstance(frame_beat, Image.Image)
    assert frame_beat.size == (WIDTH, HEIGHT)
