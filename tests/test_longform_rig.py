"""Tests for the cinematic figure rig (autoshorts.longform.rig)."""

from __future__ import annotations

import inspect

from PIL import Image

from autoshorts.longform.rig import EXPRESSIONS, POSES, draw_rig

W, H = 1920, 1080
ORIGIN = (960.0, 700.0)
SCALE = 1.9
BG = (40, 52, 70)
THIGH_UNITS = 56.0
SHIN_UNITS = 52.0

NEW_POSES = {"sit_chair", "lean_wall", "hug_knees", "fist_up", "reach_up", "fall_back"}
NEW_EXPRESSIONS = {"happy", "worried", "determined", "tired_smile"}


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


def test_new_poses_and_expressions_are_registered() -> None:
    assert NEW_POSES <= set(POSES)
    assert NEW_EXPRESSIONS <= set(EXPRESSIONS)


def test_expressions_define_every_face_parameter() -> None:
    required = set(EXPRESSIONS["neutral"])
    for name, params in EXPRESSIONS.items():
        assert set(params) == required, f"expression {name!r} has mismatched keys"


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


def test_mirror_flips_the_whole_figure() -> None:
    plain = _canvas()
    draw_rig(plain, "point", ORIGIN, SCALE, expression="happy", headband=True)
    mirrored = _canvas()
    draw_rig(mirrored, "point", ORIGIN, SCALE, expression="happy", headband=True, mirror=True)
    assert mirrored.tobytes() != plain.tobytes()
    # The hip sits on the x = 960 boundary, so mirroring is an exact image flip.
    assert mirrored.tobytes() == plain.transpose(Image.Transpose.FLIP_LEFT_RIGHT).tobytes()


def test_mirror_defaults_to_false() -> None:
    sig = inspect.signature(draw_rig)
    assert sig.parameters["mirror"].default is False


def test_public_signature_is_preserved() -> None:
    names = list(inspect.signature(draw_rig).parameters)
    assert names == [
        "img", "pose", "origin", "scale", "expression", "headband", "t", "mirror",
        "head_turn", "hand_lift",
    ]  # fmt: skip


GESTURE_POSES = {
    "gesture_point_up",
    "gesture_open_hands",
    "gesture_shrug",
    "look_left",
    "look_right",
}


def test_gesture_and_look_poses_are_registered_and_draw() -> None:
    assert GESTURE_POSES <= set(POSES)
    for name in GESTURE_POSES:
        img = _canvas()
        draw_rig(img, name, ORIGIN, SCALE)
        assert img.tobytes() != _canvas().tobytes(), f"pose {name!r} drew nothing"


def test_look_poses_shift_the_head_not_the_body() -> None:
    stand = _canvas()
    draw_rig(stand, "stand", ORIGIN, SCALE)
    for name in ("look_left", "look_right"):
        img = _canvas()
        draw_rig(img, name, ORIGIN, SCALE)
        assert img.tobytes() != stand.tobytes(), f"pose {name!r} matches stand"
    assert POSES["look_left"]["torso"] == POSES["stand"]["torso"]


def test_head_turn_zero_matches_default() -> None:
    default = _canvas()
    draw_rig(default, "stand", ORIGIN, SCALE, headband=True)
    explicit = _canvas()
    draw_rig(explicit, "stand", ORIGIN, SCALE, headband=True, head_turn=0.0, hand_lift=0.0)
    assert explicit.tobytes() == default.tobytes()


def test_head_turn_one_changes_pixels() -> None:
    base = _canvas()
    draw_rig(base, "stand", ORIGIN, SCALE)
    turned = _canvas()
    draw_rig(turned, "stand", ORIGIN, SCALE, head_turn=1.0)
    assert turned.tobytes() != base.tobytes()


def test_head_turn_is_clamped_to_unit_range() -> None:
    at_one = _canvas()
    draw_rig(at_one, "stand", ORIGIN, SCALE, head_turn=1.0)
    beyond = _canvas()
    draw_rig(beyond, "stand", ORIGIN, SCALE, head_turn=3.0)
    assert beyond.tobytes() == at_one.tobytes()


def test_hand_lift_changes_pixels_and_zero_is_default() -> None:
    base = _canvas()
    draw_rig(base, "stand", ORIGIN, SCALE)
    zero = _canvas()
    draw_rig(zero, "stand", ORIGIN, SCALE, hand_lift=0.0)
    assert zero.tobytes() == base.tobytes()
    lifted = _canvas()
    draw_rig(lifted, "stand", ORIGIN, SCALE, hand_lift=0.5)
    assert lifted.tobytes() != base.tobytes()


def test_output_size_and_mode_unchanged() -> None:
    rgb = _canvas("RGB")
    draw_rig(rgb, "climb_slope", ORIGIN, SCALE, expression="grit", headband=True)
    assert rgb.size == (W, H)
    assert rgb.mode == "RGB"

    rgba = _canvas("RGBA")
    draw_rig(rgba, "box_punch", ORIGIN, SCALE, expression="shadow_smirk")
    assert rgba.size == (W, H)
    assert rgba.mode == "RGBA"


def test_ground_shadow_is_translucent_under_feet() -> None:
    img = _canvas("RGBA")
    draw_rig(img, "stand", ORIGIN, SCALE)
    # Beside the legs, inside the shadow ellipse on the ground, the background is darkened.
    feet_y = int(ORIGIN[1] + (THIGH_UNITS + SHIN_UNITS) * SCALE)
    r, g, b, _ = img.getpixel((int(ORIGIN[0]) + 100, feet_y + 8))
    bg_r, bg_g, bg_b = BG
    assert r <= bg_r and g <= bg_g and b <= bg_b
    assert (r, g, b) != (bg_r, bg_g, bg_b)
