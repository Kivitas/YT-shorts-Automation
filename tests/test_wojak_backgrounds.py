"""Unit tests for Wojak backgrounds module."""

from pathlib import Path
from PIL import Image

from autoshorts.longform.wojak_backgrounds import (
    HEIGHT,
    WIDTH,
    get_wojak_background,
    render_procedural_hospital,
    render_procedural_school,
    render_procedural_transport,
)


def test_procedural_hospital_dimensions():
    bg = render_procedural_hospital("USA", width=WIDTH, height=HEIGHT)
    assert bg.size == (WIDTH, HEIGHT)
    assert bg.mode == "RGB"


def test_procedural_school_dimensions():
    bg = render_procedural_school("Japan", width=WIDTH, height=HEIGHT)
    assert bg.size == (WIDTH, HEIGHT)
    assert bg.mode == "RGB"


def test_procedural_transport_dimensions():
    bg = render_procedural_transport("UK", width=WIDTH, height=HEIGHT)
    assert bg.size == (WIDTH, HEIGHT)
    assert bg.mode == "RGB"


def test_get_wojak_background_various_countries():
    countries = ["USA", "UK", "Sweden", "South Korea", "India", "Italy", "Germany"]
    for c in countries:
        bg = get_wojak_background(c, setting="hospital")
        assert bg.size == (WIDTH, HEIGHT)
        assert bg.mode == "RGB"


def test_custom_photo_fallback(tmp_path: Path):
    custom_img_path = tmp_path / "custom_bg.jpg"
    test_img = Image.new("RGB", (800, 600), (120, 140, 160))
    test_img.save(custom_img_path)

    bg = get_wojak_background("CustomCountry", custom_photo_path=custom_img_path)
    assert bg.size == (WIDTH, HEIGHT)
