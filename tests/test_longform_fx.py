"""Tests for the pure PIL cinematic helpers in autoshorts.longform.fx."""

from __future__ import annotations

from PIL import Image, ImageDraw

from autoshorts.longform.fx import (
    camera_shake_offset,
    draw_dust_motes,
    draw_radial_glow,
    draw_vignette,
    impact_flash_alpha,
    shatter_text,
)


def _canvas() -> Image.Image:
    return Image.new("RGB", (1920, 1080), (20, 20, 30))


def test_glow_off_canvas_centre_does_not_raise() -> None:
    img = _canvas()
    draw_radial_glow(img, (-200, -200), 300, (255, 180, 80))
    draw_radial_glow(img, (5000, 5000), 300, (255, 180, 80), intensity=0.9)
    assert img.size == (1920, 1080)
    assert img.mode == "RGB"


def test_glow_brightens_centre() -> None:
    img = _canvas()
    before = img.getpixel((960, 540))
    draw_radial_glow(img, (960, 540), 200, (255, 200, 100))
    assert img.getpixel((960, 540)) != before


def test_vignette_keeps_size_and_mode() -> None:
    img = _canvas()
    draw_vignette(img, strength=0.5)
    assert img.size == (1920, 1080)
    assert img.mode == "RGB"


def test_vignette_darkens_corner_more_than_centre() -> None:
    img = Image.new("RGB", (640, 360), (200, 200, 200))
    draw_vignette(img, strength=0.8)
    assert img.getpixel((0, 0))[0] < img.getpixel((320, 180))[0]


def test_dust_motes_deterministic_for_same_t() -> None:
    box = (0, 0, 1920, 1080)
    a = Image.new("RGB", (1920, 1080), (0, 0, 0))
    b = Image.new("RGB", (1920, 1080), (0, 0, 0))
    draw_dust_motes(ImageDraw.Draw(a), 1.5, box)
    draw_dust_motes(ImageDraw.Draw(b), 1.5, box)
    assert a.tobytes() == b.tobytes()


def test_dust_motes_change_over_time() -> None:
    box = (0, 0, 1920, 1080)
    a = Image.new("RGB", (1920, 1080), (0, 0, 0))
    b = Image.new("RGB", (1920, 1080), (0, 0, 0))
    draw_dust_motes(ImageDraw.Draw(a), 1.0, box)
    draw_dust_motes(ImageDraw.Draw(b), 3.0, box)
    assert a.tobytes() != b.tobytes()


def test_shatter_returns_one_item_per_char() -> None:
    text = "BOOM"
    items = shatter_text(text, 100.0, 200.0, t=0.5, start=0.2)
    assert len(items) == len(text)
    assert [c for c, *_ in items] == list(text)


def test_shatter_before_start_is_original_layout() -> None:
    items = shatter_text("AB", 100.0, 200.0, t=0.0, start=1.0)
    assert all(alpha == 1.0 for *_, alpha in items)
    assert items[0][1:3] == (100.0, 200.0)


def test_shatter_alpha_zero_at_end_and_empty_after() -> None:
    near_end = shatter_text("HI", 0.0, 0.0, t=1.0 + 1.2 - 1e-9, start=1.0, duration=1.2)
    assert near_end
    assert all(alpha < 1e-6 for *_, alpha in near_end)
    assert shatter_text("HI", 0.0, 0.0, t=1.0 + 1.2, start=1.0, duration=1.2) == []
    assert shatter_text("HI", 0.0, 0.0, t=10.0, start=1.0, duration=1.2) == []


def test_flash_on_off_window() -> None:
    # 3 frames at 30 fps -> window [1.0, 1.1)
    assert impact_flash_alpha(0.99, start=1.0) == 0.0
    assert impact_flash_alpha(1.0, start=1.0) == 1.0
    assert impact_flash_alpha(1.05, start=1.0) == 1.0
    assert impact_flash_alpha(1.1, start=1.0) == 0.0


def test_shake_zero_outside_window() -> None:
    assert camera_shake_offset(0.9, start=1.0) == (0, 0)
    assert camera_shake_offset(1.2, start=1.0, duration=0.2) == (0, 0)


def test_shake_nonzero_inside_window() -> None:
    offsets = [camera_shake_offset(1.0 + i * 0.005, start=1.0) for i in range(30)]
    assert any(o != (0, 0) for o in offsets)
