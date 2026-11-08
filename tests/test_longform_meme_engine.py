"""Unit tests for Countryball & "Types Of..." Meme Engine."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from autoshorts.longform.drawer import FLOOR_Y, HEIGHT, WIDTH
from autoshorts.longform.meme_engine import (
    ESCALATION_FREEZE_S,
    JUMP_STRETCH_MAX,
    LANDING_SQUASH_DX,
    LANDING_SQUASH_DY,
    TYPES_OF_MAX_ARCHETYPES,
    TYPES_OF_MIN_ARCHETYPES,
    CountryballFlag,
    CountryballPhysics,
    EyeMood,
    ProceduralCountryball,
    RPGRadarCard,
    TypesOfMemeOverlay,
    TypesOfMemeVignette,
    VignetteConfig,
    _clip_layer_to_circle,
    _draw_flag_texture,
    clamp_archetype_count,
    clamp_segment_seconds,
    draw_archetype_pill_badge,
    draw_countryball_eyes,
    plan_types_of_timeline,
    slot_at,
)


# ---------------------------------------------------------------------------
# 1. Countryball physics
# ---------------------------------------------------------------------------
def test_landing_squash_constants_match_spec() -> None:
    assert LANDING_SQUASH_DY == pytest.approx(0.20)
    assert LANDING_SQUASH_DX == pytest.approx(0.25)


def test_countryball_physics_squash_and_stretch() -> None:
    physics = CountryballPhysics(base_radius=80.0, floor_y=FLOOR_Y)

    # Peak of bounce: velocity ~0, no stretch, airborne.
    state_peak = physics.simulate_periodic(t=0.375, bounce_period=0.75, bounce_height=180.0)
    assert abs(state_peak.velocity_y) < 1.0
    assert state_peak.altitude > 175.0
    assert state_peak.is_grounded is False
    assert state_peak.radius_x == pytest.approx(80.0, abs=0.01)
    assert state_peak.radius_y == pytest.approx(80.0, abs=0.01)


def test_landing_squash_is_minus_20_percent_y_plus_25_percent_x() -> None:
    physics = CountryballPhysics(base_radius=80.0, floor_y=FLOOR_Y)
    # t=0.0 is the exact moment of full landing squash (k = 1).
    state = physics.simulate_periodic(t=0.0, bounce_period=0.75, bounce_height=180.0)
    assert state.is_grounded is True
    assert state.radius_y == pytest.approx(80.0 * (1.0 - 0.20), abs=1e-6)  # dY = -20%
    assert state.radius_x == pytest.approx(80.0 * (1.0 + 0.25), abs=1e-6)  # dX = +25%
    # Sphere keeps its area at full squash: 0.8 * 1.25 = 1.0.
    assert state.radius_x * state.radius_y == pytest.approx(80.0**2, abs=1e-6)
    # Resting on the floor: bottom edge sits on FLOOR_Y.
    assert state.y + state.radius_y == pytest.approx(FLOOR_Y, abs=1e-6)


def test_landing_squash_eases_back_to_round_mid_arc() -> None:
    physics = CountryballPhysics(base_radius=80.0, floor_y=FLOOR_Y)
    period = 0.75
    window = 0.07 * period
    # Partway through the rebound the squash is between rest and full.
    state = physics.simulate_periodic(t=window * 0.5, bounce_period=period, bounce_height=180.0)
    assert state.is_grounded is True
    assert 80.0 * 0.8 < state.radius_y < 80.0
    assert 80.0 < state.radius_x < 80.0 * 1.25


def test_jump_stretch_is_horizontal_and_capped() -> None:
    physics = CountryballPhysics(base_radius=80.0, floor_y=FLOOR_Y)
    # Shortly after takeoff: fast vertical speed, airborne.
    state = physics.simulate_periodic(t=0.2, bounce_period=0.75, bounce_height=180.0)
    assert state.is_grounded is False
    assert abs(state.velocity_y) > 100.0
    assert state.radius_x > 80.0  # horizontal stretch on jumps
    assert state.radius_y < 80.0
    assert state.radius_x * state.radius_y == pytest.approx(80.0**2, rel=1e-6)
    assert state.radius_x <= 80.0 * (1.0 + JUMP_STRETCH_MAX) + 1e-6


def test_bounce_period_guard_keeps_state_finite() -> None:
    physics = CountryballPhysics(base_radius=80.0, floor_y=FLOOR_Y)
    state = physics.simulate_periodic(t=0.1, bounce_period=0.0, bounce_height=180.0)
    assert np.isfinite(state.radius_x) and np.isfinite(state.radius_y)


# ---------------------------------------------------------------------------
# 2. Flag texture clipped to the ball radius
# ---------------------------------------------------------------------------
ALL_FLAGS = list(CountryballFlag)


def _outside_ellipse_max_radius(arr: np.ndarray, cx: float, cy: float, rx: float, ry: float):
    ys, xs = np.nonzero(arr[..., 3])
    if xs.size == 0:
        return None
    d = np.sqrt(((xs + 0.5 - cx) / rx) ** 2 + ((ys + 0.5 - cy) / ry) ** 2)
    return float(d.max())


@pytest.mark.parametrize("flag", [CountryballFlag.USA, CountryballFlag.UK])
def test_flag_texture_spills_without_clip_and_not_with_clip(flag) -> None:
    size = (400, 400)
    cx, cy, rx, ry = 200.0, 200.0, 80.0, 80.0
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    _draw_flag_texture(ImageDraw.Draw(layer), flag, cx, cy, rx, ry)

    # Raw flag drawing runs past the sphere (bounding-box rectangles and lines).
    raw = np.asarray(layer)
    assert _outside_ellipse_max_radius(raw, cx, cy, rx, ry) > 1.1

    _clip_layer_to_circle(layer, cx, cy, rx, ry)
    clipped = np.asarray(layer)
    tol = 1.5 / min(rx, ry)  # one-pixel rasterisation tolerance
    assert _outside_ellipse_max_radius(clipped, cx, cy, rx, ry) <= 1.0 + tol


@pytest.mark.parametrize("flag", ALL_FLAGS, ids=lambda f: f.value)
def test_ball_draws_no_pixels_outside_circle(flag) -> None:
    size = (400, 400)
    cx, cy, r = 200.0, 200.0, 80.0
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    ProceduralCountryball(flag=flag, base_radius=r, accessories=False).draw_ball(
        img, cx=cx, cy=cy, rx=r, ry=r, mood=EyeMood.NEUTRAL
    )
    arr = np.asarray(img)
    tol = 1.5 / r
    assert _outside_ellipse_max_radius(arr, cx, cy, r, r) <= 1.0 + tol


# ---------------------------------------------------------------------------
# 3. Eye state machine: each mood renders distinctly
# ---------------------------------------------------------------------------
SPEC_MOODS = [
    EyeMood.NEUTRAL,
    EyeMood.ANGRY,
    EyeMood.SHOCKED,
    EyeMood.SMUG,
    EyeMood.DEADPAN,
]
EYE_CX, EYE_CY, EYE_R = 200.0, 200.0, 80.0


def _eye_render(mood: EyeMood) -> np.ndarray:
    img = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    draw_countryball_eyes(
        ImageDraw.Draw(img),
        EYE_CX,
        EYE_CY,
        EYE_R,
        EYE_R,
        mood=mood,
        line_width=4,
    )
    return np.asarray(img).copy()


def _white_mask(arr: np.ndarray) -> np.ndarray:
    return (arr[..., 3] > 0) & (arr[..., 0] == 255) & (arr[..., 1] == 255) & (arr[..., 2] == 255)


def _col_top_bottom(mask: np.ndarray, x: int) -> tuple[int, int] | None:
    rows = np.nonzero(mask[:, x])[0]
    if rows.size == 0:
        return None
    return int(rows.min()), int(rows.max())


def test_each_spec_mood_renders_distinct_eye_region() -> None:
    masks = {m: _white_mask(_eye_render(m)) for m in SPEC_MOODS}
    for m, mask in masks.items():
        assert mask.sum() > 100, f"{m} rendered nothing"
    for i, a in enumerate(SPEC_MOODS):
        for b in SPEC_MOODS[i + 1 :]:
            inter = np.logical_and(masks[a], masks[b]).sum()
            union = np.logical_or(masks[a], masks[b]).sum()
            iou = inter / union
            assert iou < 0.9, f"{a} and {b} look the same (IoU={iou:.2f})"


def test_neutral_is_two_vertical_ovals() -> None:
    mask = _white_mask(_eye_render(EyeMood.NEUTRAL))
    left, right = mask[:, : int(EYE_CX)], mask[:, int(EYE_CX) :]
    for half in (left, right):
        ys, xs = np.nonzero(half)
        assert (ys.max() - ys.min()) > (xs.max() - xs.min())  # taller than wide


def test_deadpan_is_horizontal_slits() -> None:
    mask = _white_mask(_eye_render(EyeMood.DEADPAN))
    left, right = mask[:, : int(EYE_CX)], mask[:, int(EYE_CX) :]
    for half in (left, right):
        ys, xs = np.nonzero(half)
        assert (xs.max() - xs.min()) > 2 * (ys.max() - ys.min())  # much wider than tall


def test_shocked_has_wide_eyes_and_detached_sweat_droplet() -> None:
    arr = _eye_render(EyeMood.SHOCKED)
    white = _white_mask(arr)
    neutral = _white_mask(_eye_render(EyeMood.NEUTRAL))
    assert white.sum() > 2 * neutral.sum()  # enormous circular eyes

    # Sweat droplet is light blue and sits clear of the sphere outline.
    blue = (
        (arr[..., 3] > 0)
        & (np.abs(arr[..., 0].astype(int) - 100) < 4)
        & (np.abs(arr[..., 1].astype(int) - 180) < 4)
        & (np.abs(arr[..., 2].astype(int) - 245) < 4)
    )
    ys, xs = np.nonzero(blue)
    assert xs.size > 10
    dist = np.sqrt((xs + 0.5 - EYE_CX) ** 2 + (ys + 0.5 - EYE_CY) ** 2)
    assert dist.min() > EYE_R  # detached: entirely outside the ball


def test_angry_has_slanted_inward_brows() -> None:
    mask = _white_mask(_eye_render(EyeMood.ANGRY))
    # Left eye: outer (left) edge is higher than inner edge. Right eye: mirrored.
    w = 0.22 * EYE_R
    left_x = int(EYE_CX - 0.18 * EYE_R)
    right_x = int(EYE_CX + 0.18 * EYE_R)
    outer_l = _col_top_bottom(mask, int(left_x - 0.7 * w))
    inner_l = _col_top_bottom(mask, int(left_x + 0.7 * w))
    outer_r = _col_top_bottom(mask, int(right_x + 0.7 * w))
    inner_r = _col_top_bottom(mask, int(right_x - 0.7 * w))
    assert outer_l and inner_l and outer_r and inner_r
    assert outer_l[0] < inner_l[0] - 3
    assert outer_r[0] < inner_r[0] - 3


def test_smug_is_upward_crescent_with_flat_base() -> None:
    mask = _white_mask(_eye_render(EyeMood.SMUG))
    left_x = int(EYE_CX - 0.18 * EYE_R)
    w = 0.22 * EYE_R
    # Columns the outline leaves white (the outer edge is swallowed by the ink stroke).
    profile = {}
    for x in range(int(left_x - 0.9 * w), int(left_x + 0.9 * w) + 1):
        tb = _col_top_bottom(mask, x)
        if tb is not None:
            profile[x] = tb
    assert len(profile) >= 10
    tops = [top for top, _ in profile.values()]
    assert max(tops) - min(tops) >= 6  # curved upper lid (dome, highest at centre)
    centre = [bot for x, (_, bot) in profile.items() if abs(x - left_x) <= 0.45 * w]
    assert len(centre) >= 5
    assert max(centre) - min(centre) <= 2  # flat lower base


# ---------------------------------------------------------------------------
# 4. "Types Of..." vignettes: timing, clamps, badge, radar, impact freeze
# ---------------------------------------------------------------------------
def test_archetype_count_clamped_to_3_to_5() -> None:
    assert (TYPES_OF_MIN_ARCHETYPES, TYPES_OF_MAX_ARCHETYPES) == (3, 5)
    assert clamp_archetype_count(0) == 3
    assert clamp_archetype_count(2) == 3
    assert clamp_archetype_count(4) == 4
    assert clamp_archetype_count(5) == 5
    assert clamp_archetype_count(9) == 5


def test_segment_seconds_clamped_to_6_to_8() -> None:
    assert clamp_segment_seconds(1.0) == 6.0
    assert clamp_segment_seconds(7.0) == 7.0
    assert clamp_segment_seconds(20.0) == 8.0


@pytest.mark.parametrize("requested,expected", [(1, 3), (4, 4), (99, 5)])
def test_timeline_has_clamped_archetype_count_and_contiguous_slots(requested, expected) -> None:
    slots = plan_types_of_timeline(requested, segment_seconds=7.0, start_s=10.0)
    assert len(slots) == expected
    assert slots[0].start == pytest.approx(10.0)
    for prev, nxt in zip(slots, slots[1:], strict=False):
        assert nxt.start == pytest.approx(prev.end)
    for s in slots:
        assert 6.0 <= s.duration <= 8.0


def test_timeline_clamps_segment_length() -> None:
    slots = plan_types_of_timeline(4, segment_seconds=2.0)
    assert all(s.duration == pytest.approx(6.0) for s in slots)


def test_slot_lookup_is_start_inclusive_end_exclusive() -> None:
    slots = plan_types_of_timeline(3, segment_seconds=7.0)
    assert slot_at(slots, 0.0).index == 0
    assert slot_at(slots, 6.999).index == 0
    assert slot_at(slots, 7.0).index == 1
    assert slot_at(slots, 21.0) is None  # after the last segment


def test_vignette_config_clamps_total_duration_and_freeze_is_inside_segment() -> None:
    cfg = VignetteConfig(total_duration=2.0)
    assert cfg.total_duration == 6.0
    assert cfg.freeze_time == pytest.approx(ESCALATION_FREEZE_S)
    # Badge (+0.12s) and radar (+0.35s, 0.65s reveal) must finish inside the segment.
    assert cfg.freeze_time + 0.35 + 0.65 < cfg.total_duration


def test_overlay_clamps_count_and_has_one_vignette_per_slot() -> None:
    overlay = TypesOfMemeOverlay(archetype_count=10)
    assert overlay.archetype_count == 5
    assert len(overlay.slots) == 5
    assert TypesOfMemeOverlay(archetype_count=0).archetype_count == 3


def test_archetype_pill_badge() -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), (14, 18, 38))
    # Before entrance
    draw_archetype_pill_badge(
        img=img,
        type_number=1,
        archetype_title="THE OVERPREPARER",
        t_entrance=1.0,
        current_t=0.5,
    )
    # After entrance
    draw_archetype_pill_badge(
        img=img,
        type_number=1,
        archetype_title="THE OVERTHINKER",
        t_entrance=1.0,
        current_t=1.4,
    )


def test_rpg_radar_card() -> None:
    img = Image.new("RGB", (WIDTH, HEIGHT), (14, 18, 38))
    card = RPGRadarCard(
        stats={
            "Stamina": 12.0,
            "Caffeine": 100.0,
            "Anxiety": 100.0,
            "Speed": 25.0,
        },
        title="STATS",
        display={"Caffeine": "999%", "Anxiety": "Max"},
    )
    # Render with partial and full reveal
    card.draw(img, center_x=1400.0, center_y=500.0, reveal_progress=0.5)
    card.draw(img, center_x=1400.0, center_y=500.0, reveal_progress=1.0)


def test_vignette_impact_flash_at_freeze_only() -> None:
    cfg = VignetteConfig(freeze_time=ESCALATION_FREEZE_S, total_duration=7.0)
    vignette = TypesOfMemeVignette(cfg)
    corner = (5, 5)

    def corner_px(t: float) -> int:
        img = Image.new("RGB", (WIDTH, HEIGHT), (14, 18, 38))
        vignette.draw_frame(img, t=t)
        return sum(img.getpixel(corner))

    at_freeze = corner_px(ESCALATION_FREEZE_S)
    later = corner_px(ESCALATION_FREEZE_S + 0.5)
    assert at_freeze > later  # white impact flash fades out after 0.25 s


def test_types_of_vignette_execution_and_sfx() -> None:
    cfg = VignetteConfig(
        type_number=1,
        archetype_title="THE OVERPREPARER",
        tagline="Brings 4 backup laptops.",
        freeze_time=2.0,
        total_duration=7.0,
    )
    vignette = TypesOfMemeVignette(cfg)
    img = Image.new("RGB", (WIDTH, HEIGHT), (14, 18, 38))

    # Pre-freeze
    vignette.draw_frame(img, t=1.0)
    # Freeze moment
    vignette.draw_frame(img, t=2.0)
    # Post-freeze with badge & radar
    vignette.draw_frame(img, t=3.5)

    with tempfile.TemporaryDirectory() as td:
        events = vignette.generate_sfx_events(Path(td))
        assert len(events) == 4
        # Check event timing: whoosh, freeze crack, badge punch, radar reveal
        assert events[0][1] == 0.05
        assert events[1][1] == 2.0
        assert round(events[2][1], 2) == 2.12
        assert round(events[3][1], 2) == 2.45

        for path, _ in events:
            assert path.is_file()
            assert path.stat().st_size > 0
