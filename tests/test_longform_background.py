"""Tests for the navy atmosphere backdrop in autoshorts.longform.background."""

from __future__ import annotations

from PIL import Image

from autoshorts.longform.background import draw_background


def _render(t: float = 0.0, camera_x: float = 0.0) -> Image.Image:
    img = Image.new("RGB", (1920, 1080), (0, 0, 0))
    draw_background(img, t, camera_x)
    return img


def test_output_is_rgb_1920x1080() -> None:
    img = _render()
    assert img.mode == "RGB"
    assert img.size == (1920, 1080)


def test_no_pure_black_pixels() -> None:
    img = _render(t=3.0, camera_x=120.0)
    for band_min, _band_max in img.getextrema():
        assert band_min > 0


def test_frame_is_navy_not_black() -> None:
    img = _render()
    r, _g, b = img.getpixel((10, 1070))
    assert b > r


def test_parallax_changes_pixels() -> None:
    still = _render(camera_x=0.0)
    panned = _render(camera_x=200.0)
    assert still.tobytes() != panned.tobytes()


def test_deterministic_for_same_inputs() -> None:
    first = _render(t=1.5, camera_x=80.0)
    second = _render(t=1.5, camera_x=80.0)
    assert first.tobytes() == second.tobytes()
