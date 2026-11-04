"""Tests for the pure PIL cinematic helpers in autoshorts.longform.fx."""

from __future__ import annotations

from PIL import Image, ImageDraw

from autoshorts.longform.fx import (
    breath_puff,
    camera_shake_offset,
    chromatic_aberration,
    draw_dust_motes,
    draw_hit_sparks,
    draw_radial_glow,
    draw_sleep_zs,
    draw_speed_lines,
    draw_vignette,
    film_grain,
    floating_dust,
    impact_flash_alpha,
    rain_streaks,
    shatter_text,
    sweat_drops,
    vignette,
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


def test_speed_lines_mutates_and_returns_image() -> None:
    img = _canvas()
    res = draw_speed_lines(img, t=1.0)
    assert res is img
    assert img.size == (1920, 1080)
    assert img.mode == "RGB"


def test_speed_lines_deterministic_for_same_parameters() -> None:
    a = _canvas()
    b = _canvas()
    draw_speed_lines(a, t=1.5, direction="right", count=18, seed=13)
    draw_speed_lines(b, t=1.5, direction="right", count=18, seed=13)
    assert a.tobytes() == b.tobytes()


def test_speed_lines_changes_over_time() -> None:
    a = _canvas()
    b = _canvas()
    draw_speed_lines(a, t=1.0, direction="right", seed=13)
    draw_speed_lines(b, t=2.0, direction="right", seed=13)
    assert a.tobytes() != b.tobytes()


def test_speed_lines_never_modifies_pixel_5_5() -> None:
    for direction in ("right", "left", "diagonal"):
        img = Image.new("RGB", (1920, 1080), (20, 20, 30))
        draw_speed_lines(img, t=1.0, direction=direction, count=30, seed=13)
        assert img.getpixel((5, 5)) == (20, 20, 30)


def test_speed_lines_keeps_center_character_clear() -> None:
    img = Image.new("RGB", (1920, 1080), (20, 20, 30))
    draw_speed_lines(img, t=0.5, direction="right", count=25)
    # Center character region (960, 540)
    assert img.getpixel((960, 540)) == (20, 20, 30)


def test_speed_lines_directions_and_rgba() -> None:
    img_rgba = Image.new("RGBA", (640, 360), (0, 0, 0, 255))
    draw_speed_lines(img_rgba, t=0.8, direction="diagonal", count=10)
    assert img_rgba.mode == "RGBA"
    assert img_rgba.getpixel((5, 5)) == (0, 0, 0, 255)


def test_hit_sparks_outside_window_is_noop() -> None:
    img = _canvas()
    before = img.tobytes()
    draw_hit_sparks(img, center=(960, 540), t=0.9, start=1.0, duration=0.35)
    assert img.tobytes() == before
    draw_hit_sparks(img, center=(960, 540), t=1.4, start=1.0, duration=0.35)
    assert img.tobytes() == before


def test_hit_sparks_inside_window_modifies_canvas() -> None:
    img = _canvas()
    before = img.tobytes()
    res = draw_hit_sparks(img, center=(960, 540), t=1.1, start=1.0, duration=0.35)
    assert res is img
    assert img.tobytes() != before


def test_hit_sparks_radial_expansion() -> None:
    early = _canvas()
    late = _canvas()
    draw_hit_sparks(early, center=(960, 540), t=1.02, start=1.0, duration=0.35)
    draw_hit_sparks(late, center=(960, 540), t=1.30, start=1.0, duration=0.35)
    assert early.tobytes() != late.tobytes()


def test_sleep_zs_drifts_and_mutates() -> None:
    img = _canvas()
    before = img.tobytes()
    res = draw_sleep_zs(img, head_xy=(960, 540), t=1.0)
    assert res is img
    assert img.tobytes() != before


def test_sleep_zs_deterministic_and_dynamic() -> None:
    a = _canvas()
    b = _canvas()
    c = _canvas()
    draw_sleep_zs(a, head_xy=(500, 500), t=2.0)
    draw_sleep_zs(b, head_xy=(500, 500), t=2.0)
    draw_sleep_zs(c, head_xy=(500, 500), t=2.5)
    assert a.tobytes() == b.tobytes()
    assert a.tobytes() != c.tobytes()


def test_sleep_zs_leaves_distant_pixels_untouched() -> None:
    img = _canvas()
    draw_sleep_zs(img, head_xy=(960, 540), t=0.5)
    assert img.getpixel((5, 5)) == (20, 20, 30)


def test_backward_compatibility_fx_helpers() -> None:
    img = _canvas()
    vignette(img, 0.4)
    floating_dust(img, t=1.0)
    rain_streaks(img, t=0.5)
    breath_puff(img, (960, 540), t=0.2, start=0.0, duration=1.0)
    sweat_drops(img, (960, 540), t=0.5)
    chromatic_aberration(img, offset=2)
    film_grain(img, intensity=0.02)
    assert img.size == (1920, 1080)
    assert img.mode == "RGB"
