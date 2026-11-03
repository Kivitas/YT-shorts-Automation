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

NEW_POSES = {
    "sit_chair", "sit_desk_focus", "phone_hold", "lie_bed_prone", "slump", "lean_wall",
    "hug_knees", "fist_up", "reach_up", "fall_back",
}  # fmt: skip
NEW_EXPRESSIONS = {"happy", "worried", "determined", "tired_smile", "tired", "focus"}


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


def test_body_positions_match_their_names() -> None:
    # Lying: torso horizontal, legs close to horizontal.
    for name in ("sleep_side", "sleep_bed", "lie_bed_prone"):
        pose = POSES[name]
        assert abs(abs(pose["torso"]) - 90) < 1
        assert abs(abs(pose["l_thigh"]) - 90) <= 15 and abs(abs(pose["r_thigh"]) - 90) <= 15
    # Bent knees: the shin folds away from the thigh line.
    for name in ("sleep_side", "sleep_bed"):
        pose = POSES[name]
        assert abs(pose["l_shin"] - pose["l_thigh"]) >= 10, name
    # Arms: sleep_bed arms hang near straight down with a ~15 degree elbow bend, so they do
    # not cross the torso. Prone elbows point outward. Side-lying bends the elbow ~100 degrees.
    bed = POSES["sleep_bed"]
    for side in ("l", "r"):
        assert abs(bed[f"{side}_upper"]) <= 20
        assert 10 <= abs(bed[f"{side}_lower"] - bed[f"{side}_upper"]) <= 20
    prone = POSES["lie_bed_prone"]
    assert abs(prone["l_upper"]) >= 25 and abs(prone["r_upper"]) >= 25
    side_pose = POSES["sleep_side"]
    assert abs((side_pose["l_lower"] - side_pose["l_upper"]) - (-100)) <= 5
    # Seated: thighs horizontal, shins hanging, hips lowered to seat height.
    for name in ("sit_chair", "sit_desk_focus"):
        pose = POSES[name]
        assert pose["l_thigh"] == pose["r_thigh"] == 90
        assert pose["l_shin"] == pose["r_shin"] == 0
        assert pose["drop"] > 0
    assert POSES["sit_desk_focus"]["torso"] == 15
    assert POSES["slump"]["torso"] == 20
    assert POSES["lean_wall"]["torso"] == 8


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


NEW_BODY_POSES = {
    "stretch_wake", "typing", "reading", "thumbs_up", "facepalm", "dance_side",
    "run", "hug_self", "point_side", "hands_head", "cook_stir",
}  # fmt: skip
NEW_FACE_EXPRESSIONS = {
    "sad", "surprised", "angry", "sleepy", "excited", "smug", "confused", "nervous",
}  # fmt: skip


def _drawn(pose: str, expression: str = "neutral") -> bytes:
    img = _canvas()
    draw_rig(img, pose, ORIGIN, SCALE, expression=expression, headband=True)
    return img.tobytes()


def test_new_body_poses_are_registered_and_differ_from_stand() -> None:
    assert NEW_BODY_POSES <= set(POSES)
    stand = _drawn("stand")
    for name in NEW_BODY_POSES:
        assert _drawn(name) != stand, f"pose {name!r} matches stand"


def test_new_body_poses_sample_are_distinct() -> None:
    sample = ["typing", "run", "hands_head"]
    images = {name: _drawn(name) for name in sample}
    assert len(set(images.values())) == len(sample)


def test_new_face_expressions_are_registered_and_differ_from_neutral() -> None:
    assert NEW_FACE_EXPRESSIONS <= set(EXPRESSIONS)
    neutral = _drawn("stand", "neutral")
    for name in NEW_FACE_EXPRESSIONS:
        assert _drawn("stand", name) != neutral, f"expression {name!r} matches neutral"


def test_new_face_expressions_sample_are_distinct() -> None:
    sample = ["surprised", "angry", "nervous"]
    images = {name: _drawn("stand", name) for name in sample}
    assert len(set(images.values())) == len(sample)


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


EIGHT_MORE_POSES = {
    "sit_floor_cross", "squat", "kneel", "lean_forward_table",
    "celebrate_jump", "walk_mid", "hold_mug", "wave_hand",
}  # fmt: skip


def test_eight_more_poses_are_registered_and_differ_from_stand() -> None:
    assert EIGHT_MORE_POSES <= set(POSES)
    stand = _drawn("stand")
    for name in EIGHT_MORE_POSES:
        assert _drawn(name) != stand, f"pose {name!r} matches stand"


def test_eight_more_poses_sample_are_distinct() -> None:
    sample = ["squat", "celebrate_jump", "walk_mid", "wave_hand"]
    images = {name: _drawn(name) for name in sample}
    assert len(set(images.values())) == len(sample)


def test_eight_more_poses_match_their_names() -> None:
    # Floor sitting: thighs out about 60 degrees, one on each side.
    floor = POSES["sit_floor_cross"]
    assert abs(abs(floor["l_thigh"]) - 60) <= 5 and abs(abs(floor["r_thigh"]) - 60) <= 5
    assert floor["l_thigh"] < 0 < floor["r_thigh"]
    # Squat and kneel bend the knees, so the thighs leave the vertical.
    assert abs(POSES["squat"]["l_thigh"]) >= 55 and abs(POSES["squat"]["r_thigh"]) >= 55
    assert POSES["squat"]["torso"] == 15
    assert POSES["kneel"]["l_shin"] <= -80 and POSES["kneel"]["r_shin"] >= 60
    assert POSES["lean_forward_table"]["torso"] == 35
    # Celebration: both arms above the shoulders in a V.
    jump = POSES["celebrate_jump"]
    assert jump["l_upper"] <= -140 and jump["r_upper"] >= 140
    assert jump["torso"] == 0
    # Walking: legs split with opposite signs, and arms opposite the legs.
    walk = POSES["walk_mid"]
    assert walk["l_thigh"] > 0 > walk["r_thigh"]
    assert walk["r_upper"] > 0 > walk["l_upper"]
