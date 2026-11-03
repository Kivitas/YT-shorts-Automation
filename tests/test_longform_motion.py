"""Tests for the easing and animation helpers (autoshorts.longform.motion) and the
animated rig hooks in autoshorts.longform.rig."""

from __future__ import annotations

import pytest
from PIL import Image

from autoshorts.longform.motion import (
    BLINK_DURATION,
    anticipation,
    blink,
    breathe,
    draw_on,
    drop_in,
    ease_in_out_sine,
    ease_out_back,
    ease_out_bounce,
    ease_out_elastic,
    parallax_offset,
    pop_in,
    squash_stretch,
)
from autoshorts.longform.rig import animate_pose, draw_rig

W, H = 1920, 1080
ORIGIN = (960.0, 700.0)
SCALE = 1.9
BG = (40, 52, 70)


def _canvas() -> Image.Image:
    return Image.new("RGB", (W, H), BG)


@pytest.mark.parametrize(
    "fn",
    [ease_out_back, ease_out_elastic, ease_out_bounce, ease_in_out_sine, anticipation],
)
def test_easing_endpoints(fn) -> None:
    assert fn(0.0) == pytest.approx(0.0, abs=1e-9)
    assert fn(1.0) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize(
    "fn",
    [ease_out_back, ease_out_elastic, ease_out_bounce, ease_in_out_sine, anticipation],
)
def test_easing_clamps_progress(fn) -> None:
    assert fn(-0.5) == pytest.approx(fn(0.0), abs=1e-9)
    assert fn(1.5) == pytest.approx(fn(1.0), abs=1e-9)


def test_ease_out_back_overshoots_past_one_mid_way() -> None:
    assert max(ease_out_back(k / 100) for k in range(101)) > 1.0
    assert ease_out_back(0.6) > 1.0


def test_ease_out_bounce_stays_in_unit_range() -> None:
    for k in range(101):
        assert -1e-9 <= ease_out_bounce(k / 100) <= 1.0 + 1e-9


def test_anticipation_dips_below_zero_then_goes_up() -> None:
    assert min(anticipation(k / 100) for k in range(101)) < 0.0
    assert anticipation(0.3) < 0.0
    assert anticipation(0.9) > 0.5


def test_pop_in_is_zero_before_start_and_one_after() -> None:
    assert pop_in(0.9, start=1.0) == 0.0
    assert pop_in(1.0 + 0.35, start=1.0, dur=0.35) == pytest.approx(1.0)
    assert pop_in(5.0, start=1.0) == pytest.approx(1.0)


def test_drop_in_is_height_before_start_and_zero_after_landing() -> None:
    assert drop_in(0.0, start=1.0, height=120.0) == 120.0
    assert drop_in(1.0 + 0.5, start=1.0, dur=0.5) == pytest.approx(0.0, abs=1e-9)
    assert drop_in(9.0, start=1.0, dur=0.5) == pytest.approx(0.0, abs=1e-9)


def test_draw_on_runs_from_zero_to_one() -> None:
    assert draw_on(0.5, start=1.0, dur=2.0) == 0.0
    assert draw_on(2.0, start=1.0, dur=2.0) == pytest.approx(0.5)
    assert draw_on(10.0, start=1.0, dur=2.0) == 1.0


def test_blink_closes_at_blink_instant_and_is_open_far_away() -> None:
    period = 3.4
    closed = 0.12
    instant = period * 0.62
    assert blink(instant, period=period, closed=closed) == pytest.approx(closed)
    assert blink(0.0, period=period, closed=closed) == 1.0
    # Same phase in a later period blinks again.
    assert blink(instant + 2 * period, period=period, closed=closed) == pytest.approx(closed)
    # The blink is short: well outside its window the eye is fully open.
    assert blink(instant + BLINK_DURATION, period=period) == pytest.approx(1.0)


def test_breathe_stays_within_amplitude() -> None:
    amp = 0.012
    values = [breathe(k * 0.01, amp=amp) for k in range(1000)]
    assert all(1.0 - amp - 1e-9 <= v <= 1.0 + amp + 1e-9 for v in values)
    assert max(values) > 1.0 + amp * 0.99
    assert min(values) < 1.0 - amp * 0.99


def test_squash_stretch_preserves_area_within_five_percent() -> None:
    base_w, base_h = 200.0, 300.0
    for velocity in (0.0, 5.0, 20.0, 100.0):
        w, h = squash_stretch(base_w, base_h, velocity)
        assert w * h == pytest.approx(base_w * base_h, rel=0.05)
    assert squash_stretch(base_w, base_h, 0.0) == (base_w, base_h)
    w, h = squash_stretch(base_w, base_h, 20.0)
    assert h > base_h and w < base_w


def test_parallax_offset_scales_with_depth() -> None:
    assert parallax_offset(100.0, 0.0) == 0.0
    assert parallax_offset(100.0, 0.25) == pytest.approx(25.0)
    assert parallax_offset(100.0, 1.0) == pytest.approx(100.0)


def test_animate_pose_endpoints_match_the_two_poses() -> None:
    from autoshorts.longform.rig import _resolve_pose

    a = animate_pose("stand", "fist_up", 0.0)
    b = animate_pose("stand", "fist_up", 1.0)
    assert a == pytest.approx(_resolve_pose("stand"))
    assert b == pytest.approx(_resolve_pose("fist_up"))


def test_animate_pose_midpoint_is_between_the_poses() -> None:
    mid = animate_pose("stand", "fist_up", 0.5)
    lo = min(_stand_r(), _fist_r())
    hi = max(_stand_r(), _fist_r())
    assert lo <= mid["r_upper"] <= hi


def _stand_r() -> float:
    from autoshorts.longform.rig import POSES

    return float(POSES["stand"]["r_upper"])


def _fist_r() -> float:
    from autoshorts.longform.rig import POSES

    return float(POSES["fist_up"]["r_upper"])


def test_draw_rig_accepts_animated_joint_dict() -> None:
    img = _canvas()
    draw_rig(img, animate_pose("stand", "reach_up", 0.5), ORIGIN, SCALE)
    assert img.tobytes() != _canvas().tobytes()


def test_draw_rig_at_t_zero_is_unchanged_by_blink_and_breathe() -> None:
    # t=0.0 must give the same frame as before the animation hooks existed.
    plain = _canvas()
    draw_rig(plain, "stand", ORIGIN, SCALE, expression="happy", headband=True)
    again = _canvas()
    draw_rig(again, "stand", ORIGIN, SCALE, expression="happy", headband=True, t=0.0)
    assert plain.tobytes() == again.tobytes()


def test_draw_rig_blinks_when_time_is_given() -> None:
    open_eyes = _canvas()
    draw_rig(open_eyes, "stand", ORIGIN, SCALE, expression="neutral", t=0.5)
    closed_eyes = _canvas()
    draw_rig(closed_eyes, "stand", ORIGIN, SCALE, expression="neutral", t=3.4 * 0.62)
    assert open_eyes.tobytes() != closed_eyes.tobytes()
