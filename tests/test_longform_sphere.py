"""Tests for the shaded planet helpers in autoshorts.longform.sphere."""

from __future__ import annotations

import math

import pytest
from PIL import Image

from autoshorts.longform.sphere import (
    draw_sphere,
    glow_width,
    orbit_point,
    sphere_frame_cached,
)

W = H = 240
CX, CY = 120, 120
R = 60


def _canvas(mode: str = "RGBA") -> Image.Image:
    fill = (0, 0, 0, 0) if mode == "RGBA" else (0, 0, 0)
    return Image.new(mode, (W, H), fill)


def test_draw_changes_only_disc_and_glow() -> None:
    img = _canvas()
    draw_sphere(img, (CX, CY), R)
    outer = R + glow_width(R)
    px = img.load()
    changed = 0
    for y in range(H):
        for x in range(W):
            d = math.hypot(x + 0.5 - CX, y + 0.5 - CY)
            r, g, b, a = px[x, y]
            if a:
                changed += 1
            if d > outer + 1.5:
                assert (r, g, b, a) == (0, 0, 0, 0), (x, y)
            if d < R - 2:
                assert a == 255, (x, y)
    assert changed > math.pi * R * R * 0.9


def test_two_times_with_spin_give_different_pixels() -> None:
    img_a = _canvas()
    img_b = _canvas()
    draw_sphere(img_a, (CX, CY), R, t=0.0, spin_deg_per_s=12.0)
    draw_sphere(img_b, (CX, CY), R, t=2.0, spin_deg_per_s=12.0)
    assert img_a.tobytes() != img_b.tobytes()


def test_light_side_is_brighter_than_dark_side() -> None:
    img = _canvas()
    draw_sphere(
        img,
        (CX, CY),
        R,
        base=(90, 90, 90),
        land=(90, 90, 90),
        bands=False,
        spin_deg_per_s=0.0,
        light_dir=(-0.5, -0.6, 0.6),
    )
    px = img.load()
    # Same latitude (row), mirrored about the vertical axis; light comes from the left.
    left = px[90, CY]
    right = px[149, CY]
    assert sum(left[:3]) > sum(right[:3])


def test_orbit_point_at_zero_degrees_is_right_of_centre() -> None:
    x, y = orbit_point((100.0, 50.0), 40.0, 0.0)
    assert x == pytest.approx(140.0)
    assert y == pytest.approx(50.0)


def test_orbit_point_tilt_squashes_vertical_extent() -> None:
    _, y = orbit_point((100.0, 50.0), 40.0, 90.0, tilt=0.6)
    assert y == pytest.approx(50.0 + 40.0 * math.cos(0.6))


def test_land_mask_is_cached_per_seed_and_size() -> None:
    sphere_frame_cached.cache_clear()
    first = sphere_frame_cached(3, 64)
    second = sphere_frame_cached(3, 64)
    assert first is second
    assert len(first) == 128 * 64
    assert sphere_frame_cached.cache_info().hits == 1


def test_rgb_canvas_is_supported() -> None:
    img = _canvas("RGB")
    draw_sphere(img, (CX, CY), R)
    assert img.getpixel((CX, CY)) != (0, 0, 0)
    assert img.getpixel((5, 5)) == (0, 0, 0)
