"""The yellow base sits under standing figures only, as in the reference image."""

from __future__ import annotations

from dataclasses import replace

from PIL import Image

from autoshorts.longform.beats import (
    FIGURE_BASE_DROP_PX,
    FIGURE_BASE_YELLOW,
    FIGURE_SPRITE_SCALE,
    FRAME_H,
    FRAME_W,
    RIG_ORIGIN,
    _draw_figure_base,
    _feet_y,
    plan_beat,
)


def _base_pixel(pose: str) -> tuple[int, int, int]:
    plan = replace(plan_beat(0, "Heading", "Look here.", "narrator", False), pose=pose)
    img = Image.new("RGB", (FRAME_W, FRAME_H), (0, 0, 0))
    _draw_figure_base(img, plan, {}, RIG_ORIGIN, FIGURE_SPRITE_SCALE)
    feet_y = _feet_y({}, RIG_ORIGIN[1], FIGURE_SPRITE_SCALE)
    return img.getpixel((round(RIG_ORIGIN[0]), round(feet_y + FIGURE_BASE_DROP_PX)))


def test_standing_figure_stands_on_a_yellow_base() -> None:
    assert _base_pixel("stand") == FIGURE_BASE_YELLOW


def test_seated_figure_gets_no_base() -> None:
    assert _base_pixel("sit_chair") == (0, 0, 0)
