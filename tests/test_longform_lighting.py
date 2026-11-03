"""Tests for the coloured-light helpers in autoshorts.longform.lighting."""

from __future__ import annotations

import pytest
from PIL import Image

from autoshorts.longform.lighting import (
    add_bloom,
    colour_wash,
    light_cone,
    rim_light,
    source_glow,
)

W, H = 1920, 1080
DARK = (12, 12, 20)


def _dark() -> Image.Image:
    return Image.new("RGB", (W, H), DARK)


def test_bloom_returns_new_rgb_image_of_same_size() -> None:
    src = _dark()
    out = add_bloom(src)
    assert out is not src
    assert out.size == (W, H)
    assert out.mode == "RGB"
    assert src.getpixel((0, 0)) == DARK


def test_bloom_bleeds_bright_area_outward() -> None:
    src = _dark()
    src.paste((255, 255, 255), (900, 500, 1020, 580))
    out = add_bloom(src, threshold=180, radius=24, strength=0.6)
    before = src.getpixel((1030, 540))
    after = out.getpixel((1030, 540))
    assert sum(after) > sum(before)


def test_bloom_zero_strength_is_identity() -> None:
    src = Image.new("RGB", (64, 48), (200, 90, 30))
    out = add_bloom(src, threshold=100, radius=8, strength=0.0)
    assert out.tobytes() == src.tobytes()


def test_bloom_accepts_rgba_and_returns_rgb() -> None:
    src = Image.new("RGBA", (320, 180), (255, 255, 255, 255))
    out = add_bloom(src, radius=8)
    assert out.mode == "RGB"
    assert out.size == (320, 180)


def test_light_cone_lights_inside_cone_and_keeps_format() -> None:
    img = _dark()
    light_cone(img, (960, 0), (960, 900), 40, (255, 180, 80), intensity=0.6)
    assert img.size == (W, H)
    assert img.mode == "RGB"
    assert img.getpixel((960, 300)) != DARK
    assert img.getpixel((50, 300)) == DARK


def test_light_cone_off_canvas_does_not_raise() -> None:
    img = _dark()
    light_cone(img, (-400, -400), (-100, 500), 30, (255, 180, 80))
    light_cone(img, (5000, 5000), (6000, 6000), 30, (255, 180, 80))
    assert img.size == (W, H)
    assert img.getpixel((5, 5)) == DARK


def test_rim_light_glows_outside_box_edge() -> None:
    img = _dark()
    rim_light(img, (700, 300, 1200, 800), (80, 200, 255), width=6, intensity=0.8)
    assert img.size == (W, H)
    assert img.mode == "RGB"
    assert img.getpixel((1202, 550)) != DARK
    assert img.getpixel((960, 550)) == DARK


def test_rim_light_off_canvas_box_does_not_raise() -> None:
    img = _dark()
    rim_light(img, (-500, -500, -100, -100), (80, 200, 255))
    rim_light(img, (1900, 1000, 3000, 2000), (80, 200, 255))
    assert img.size == (W, H)


def test_colour_wash_zero_intensity_leaves_pixels_unchanged() -> None:
    img = _dark()
    img.paste((200, 100, 50), (0, 0, 400, 300))
    before = img.tobytes()
    colour_wash(img, (255, 0, 0), intensity=0.0)
    assert img.tobytes() == before


def test_colour_wash_tints_frame() -> None:
    img = _dark()
    colour_wash(img, (255, 0, 0), intensity=0.2)
    assert img.size == (W, H)
    r, g, b = img.getpixel((5, 5))
    assert r > DARK[0]
    assert (g, b) == (DARK[1], DARK[2])


def test_source_glow_off_canvas_centre_does_not_raise() -> None:
    img = _dark()
    source_glow(img, (-300, -300), 200, (255, 160, 60), 1.0)
    source_glow(img, (5000, 5000), 200, (255, 160, 60), 1.0)
    source_glow(img, (W + 50, H / 2), 300, (255, 160, 60), 1.0)
    assert img.size == (W, H)
    assert img.mode == "RGB"


def test_source_glow_brightens_centre_with_hot_core() -> None:
    img = _dark()
    source_glow(img, (960, 540), 300, (255, 120, 40), 0.9)
    r, g, b = img.getpixel((960, 540))
    assert r > 200 and g > 200 and b > 150
    assert img.getpixel((0, 0)) == DARK


def test_inplace_helpers_keep_rgba_alpha() -> None:
    img = Image.new("RGBA", (640, 360), (12, 12, 20, 128))
    light_cone(img, (320, 0), (320, 300), 40, (255, 180, 80), 0.6)
    rim_light(img, (200, 100, 440, 300), (80, 200, 255))
    colour_wash(img, (255, 0, 0), 0.2)
    source_glow(img, (320, 180), 120, (255, 160, 60), 0.8)
    assert img.mode == "RGBA"
    assert img.size == (640, 360)
    assert img.getchannel("A").getextrema() == (128, 128)


def test_unsupported_mode_raises_value_error() -> None:
    with pytest.raises(ValueError):
        colour_wash(Image.new("L", (10, 10)), (255, 0, 0))
