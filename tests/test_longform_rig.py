"""Tests for the cinematic figure rig (autoshorts.longform.rig)."""

from __future__ import annotations

from PIL import Image

from autoshorts.longform.rig import EXPRESSIONS, POSES, draw_rig

W, H = 1920, 1080
ORIGIN = (960.0, 700.0)
SCALE = 1.9
BG = (40, 52, 70)


def _canvas(mode: str = "RGB") -> Image.Image:
    return Image.new(mode, (W, H), BG if mode == "RGB" else BG + (255,))


def test_every_pose_draws_without_error() -> None:
    for name in POSES:
        img = _canvas()
        draw_rig(img, name, ORIGIN, SCALE)
        assert img.size == (W, H)
        assert img.tobytes() != _canvas().tobytes(), f"pose {name!r} drew nothing"


def test_every_expression_draws_without_error() -> None:
    for name in EXPRESSIONS:
        img = _canvas()
        draw_rig(img, "stand", ORIGIN, SCALE, expression=name, headband=True)
        assert img.size == (W, H)
        assert img.tobytes() != _canvas().tobytes(), f"expression {name!r} drew nothing"


def test_unknown_names_fall_back_to_stand_and_neutral() -> None:
    expected = _canvas()
    draw_rig(expected, "stand", ORIGIN, SCALE, expression="neutral")

    actual = _canvas()
    draw_rig(actual, "no_such_pose", ORIGIN, SCALE, expression="no_such_expression")
    assert actual.tobytes() == expected.tobytes()


def test_headband_changes_pixels() -> None:
    plain = _canvas()
    draw_rig(plain, "stand", ORIGIN, SCALE, headband=False)
    banded = _canvas()
    draw_rig(banded, "stand", ORIGIN, SCALE, headband=True)
    assert plain.tobytes() != banded.tobytes()


def test_headband_ribbons_flutter_with_time() -> None:
    a = _canvas()
    draw_rig(a, "stand", ORIGIN, SCALE, headband=True, t=0.0)
    b = _canvas()
    draw_rig(b, "stand", ORIGIN, SCALE, headband=True, t=0.05)
    assert a.tobytes() != b.tobytes()


def test_output_size_and_mode_unchanged() -> None:
    rgb = _canvas("RGB")
    draw_rig(rgb, "climb_slope", ORIGIN, SCALE, expression="grit", headband=True)
    assert rgb.size == (W, H)
    assert rgb.mode == "RGB"

    rgba = _canvas("RGBA")
    draw_rig(rgba, "box_punch", ORIGIN, SCALE, expression="shadow_smirk")
    assert rgba.size == (W, H)
    assert rgba.mode == "RGBA"
