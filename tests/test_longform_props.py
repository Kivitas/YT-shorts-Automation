"""Tests for the flat-illustrated longform prop library."""

from __future__ import annotations

import pytest
from PIL import Image, ImageChops

from autoshorts.longform.props import PROP_NAMES, draw_prop

CANVAS = (1920, 1080)
BACKGROUND = (8, 8, 12)


def _canvas(mode: str = "RGB") -> Image.Image:
    if mode == "RGBA":
        return Image.new("RGBA", CANVAS, (*BACKGROUND, 255))
    return Image.new("RGB", CANVAS, BACKGROUND)


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
@pytest.mark.parametrize("scale", [1.0, 0.5])
@pytest.mark.parametrize("name", PROP_NAMES)
def test_every_prop_draws(name: str, scale: float, mode: str) -> None:
    img = _canvas(mode)
    before = img.convert("RGB")
    draw_prop(img, name, (200, 150), scale=scale)

    assert img.size == CANVAS
    assert img.mode == mode
    diff = ImageChops.difference(img.convert("RGB"), before)
    assert diff.getbbox() is not None, f"{name} drew no pixels"


def test_prop_names_are_unique_and_complete() -> None:
    assert isinstance(PROP_NAMES, tuple)
    assert len(PROP_NAMES) == len(set(PROP_NAMES)) == 19
    assert "alarm_clock_7seg" in PROP_NAMES
    assert "shadow_demon" in PROP_NAMES


def test_unknown_name_raises_value_error() -> None:
    with pytest.raises(ValueError):
        draw_prop(_canvas(), "toaster", (0, 0))


def test_non_positive_scale_raises_value_error() -> None:
    with pytest.raises(ValueError):
        draw_prop(_canvas(), "bed", (0, 0), scale=0)


def test_image_size_unchanged_when_partly_off_canvas() -> None:
    for origin in ((-60, -60), (1900, 1040), (1800, -30)):
        img = _canvas()
        draw_prop(img, "fridge", origin)
        assert img.size == CANVAS


def test_calendar_marks_change_pixels() -> None:
    blank = _canvas()
    marked = _canvas()
    draw_prop(blank, "calendar", (100, 100), marks=0)
    draw_prop(marked, "calendar", (100, 100), marks=5)
    assert blank.tobytes() != marked.tobytes()


def test_speech_bubble_accepts_text() -> None:
    short = _canvas()
    long = _canvas()
    draw_prop(short, "speech_bubble", (100, 100), text="Hi")
    draw_prop(long, "speech_bubble", (100, 100), text="A much longer sentence here")
    assert short.tobytes() != long.tobytes()


def test_bar_chart_highlight_changes_pixels() -> None:
    plain = _canvas()
    highlighted = _canvas()
    draw_prop(plain, "bar_chart", (100, 100))
    draw_prop(highlighted, "bar_chart", (100, 100), highlight=1)
    assert plain.tobytes() != highlighted.tobytes()


def test_bar_chart_bad_highlight_raises() -> None:
    with pytest.raises(ValueError):
        draw_prop(_canvas(), "bar_chart", (0, 0), highlight=7)


def test_animation_time_changes_glowing_props() -> None:
    a = _canvas()
    b = _canvas()
    draw_prop(a, "shadow_demon", (100, 100), t=0.0)
    draw_prop(b, "shadow_demon", (100, 100), t=0.25)
    assert a.tobytes() != b.tobytes()
