"""Tests for the navy atmosphere backdrop in autoshorts.longform.background."""

from __future__ import annotations

import logging

import pytest
from PIL import Image

from autoshorts.longform.background import (
    THEMES,
    draw_background,
    draw_themed_background,
    theme_names,
)


def _render(t: float = 0.0, camera_x: float = 0.0) -> Image.Image:
    img = Image.new("RGB", (1920, 1080), (0, 0, 0))
    draw_background(img, t, camera_x)
    return img


def _render_theme(name: str, t: float = 0.0, camera_x: float = 0.0) -> Image.Image:
    img = Image.new("RGB", (1920, 1080), (0, 0, 0))
    draw_themed_background(img, name, t, camera_x)
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


def test_default_background_still_runs_and_is_navy() -> None:
    img = _render(t=2.0, camera_x=40.0)
    assert img.mode == "RGB"
    assert img.size == (1920, 1080)
    r, _g, b = img.getpixel((10, 1070))
    assert b > r


def test_theme_names_lists_every_theme_with_default_first() -> None:
    names = theme_names()
    assert set(names) == set(THEMES)
    assert names[0] == "navy_night"
    assert {
        "navy_night",
        "sunset",
        "lab_teal",
        "office_warm",
        "forest_dawn",
        "city_neon",
        "candy",
    } <= set(names)


@pytest.mark.parametrize("name", theme_names())
def test_every_theme_draws_rgb_1920x1080(name: str) -> None:
    img = _render_theme(name, t=1.0, camera_x=60.0)
    assert img.mode == "RGB"
    assert img.size == (1920, 1080)
    assert len(img.getcolors(maxcolors=1 << 24) or []) > 1


def test_themes_produce_different_pixels() -> None:
    renders = {name: _render_theme(name).tobytes() for name in theme_names()}
    assert len(set(renders.values())) == len(renders)


def test_navy_night_theme_matches_default_background() -> None:
    themed = _render_theme("navy_night", t=1.5, camera_x=80.0)
    default = _render(t=1.5, camera_x=80.0)
    assert themed.tobytes() == default.tobytes()


def test_unknown_theme_warns_and_falls_back_to_navy_night(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="autoshorts.longform.background"):
        fallback = _render_theme("no_such_theme", t=0.5, camera_x=10.0)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "no_such_theme" in warnings[0].getMessage()
    expected = _render_theme("navy_night", t=0.5, camera_x=10.0)
    assert fallback.tobytes() == expected.tobytes()


def test_known_theme_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="autoshorts.longform.background"):
        _render_theme("sunset")
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_themed_background_rejects_non_rgb() -> None:
    img = Image.new("L", (1920, 1080), 0)
    with pytest.raises(ValueError):
        draw_themed_background(img, "candy", 0.0)
