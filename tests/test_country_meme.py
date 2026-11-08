"""Tests for the country meme PIL compositor (no network, synthetic photo only)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from PIL import Image

from autoshorts.longform import country_meme
from autoshorts.longform.country_meme import (
    BLACK,
    WHITE,
    compute_layout,
    render_country_meme_frame,
)

PORTRAIT = (1080, 1920)
LANDSCAPE = (1920, 1080)
CAPTION = "Free hospital. Your MRI appointment: spring of 2028. Sorry for the wait."


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    """A synthetic horizontal-plus-vertical gradient photo."""
    base = Image.linear_gradient("L").resize((800, 600)).convert("RGB")
    overlay = Image.linear_gradient("L").resize((800, 600)).rotate(90).convert("RGB")
    img = Image.blend(base, overlay, 0.5)
    path = tmp_path / "photo.png"
    img.save(path)
    return path


def _dark_pixels(img: Image.Image, box: tuple[int, int, int, int]) -> int:
    region = img.crop(box).convert("L")
    return sum(1 for value in region.tobytes() if value < 90)


@pytest.mark.parametrize("size", [PORTRAIT, LANDSCAPE])
def test_output_size_and_mode(photo: Path, size: tuple[int, int]) -> None:
    img = render_country_meme_frame(size, "Canada", photo, CAPTION)
    assert img.size == size
    assert img.mode == "RGB"


def test_title_bar_is_white_with_rule(photo: Path) -> None:
    layout = compute_layout(PORTRAIT, "Canada", CAPTION)
    img = render_country_meme_frame(PORTRAIT, "Canada", photo, CAPTION)

    for x, y in [(2, 2), (PORTRAIT[0] - 3, 2), (2, layout.bar_height - 3)]:
        assert img.getpixel((x, y)) == WHITE

    rule_row = [img.getpixel((x, layout.rule_y)) for x in range(PORTRAIT[0])]
    assert all(px == BLACK for px in rule_row)
    assert layout.photo_top == layout.rule_y + 1


def test_title_text_is_drawn_in_bar(photo: Path) -> None:
    layout = compute_layout(PORTRAIT, "Canada", CAPTION)
    img = render_country_meme_frame(PORTRAIT, "Canada", photo, CAPTION)
    assert _dark_pixels(img, (0, 0, PORTRAIT[0], layout.bar_height)) > 200


def test_caption_box_is_white_with_centred_text(photo: Path) -> None:
    layout = compute_layout(PORTRAIT, "Canada", CAPTION)
    assert layout.caption_box is not None
    x0, y0, x1, y1 = layout.caption_box
    img = render_country_meme_frame(PORTRAIT, "Canada", photo, CAPTION)

    mid_y = (y0 + y1) // 2
    assert img.getpixel((x0 + 6, mid_y)) == WHITE
    assert img.getpixel((x1 - 7, mid_y)) == WHITE
    centre_x = (x0 + x1) // 2
    assert abs(centre_x - PORTRAIT[0] // 2) <= 1
    assert _dark_pixels(img, (x0, y0, x1, y1)) > 200


def test_caption_fits_in_two_lines_and_auto_sizes(photo: Path) -> None:
    short = compute_layout(PORTRAIT, "Canada", "Hi.")
    long_text = "Lorem ipsum dolor sit amet " * 40
    long = compute_layout(PORTRAIT, "Canada", long_text)
    assert len(long.caption_lines) <= 2
    assert long.caption_font_px >= country_meme.CAPTION_MIN_FONT_PX
    assert short.caption_font_px > long.caption_font_px

    huge_word = compute_layout(PORTRAIT, "Canada", "A" * 400)
    assert len(huge_word.caption_lines) <= 2
    assert huge_word.caption_font_px == country_meme.CAPTION_MIN_FONT_PX

    rendered = render_country_meme_frame(PORTRAIT, "Canada", photo, long_text)
    assert rendered.size == PORTRAIT


def test_character_is_pasted_bottom_left(photo: Path) -> None:
    character = Image.new("RGBA", (300, 300), (255, 0, 0, 255))
    without = render_country_meme_frame(PORTRAIT, "Canada", photo, CAPTION)
    with_char = render_country_meme_frame(
        PORTRAIT, "Canada", photo, CAPTION, character_img=character
    )
    assert with_char.getpixel((10, PORTRAIT[1] - 10)) == (255, 0, 0)
    assert without.getpixel((10, PORTRAIT[1] - 10)) != (255, 0, 0)

    region = (0, PORTRAIT[1] // 2, 300, PORTRAIT[1])
    assert with_char.crop(region).tobytes() != without.crop(region).tobytes()


def test_character_transparent_pixels_do_not_paint(photo: Path) -> None:
    character = Image.new("RGBA", (300, 300), (255, 0, 0, 0))
    without = render_country_meme_frame(PORTRAIT, "Canada", photo, CAPTION)
    with_char = render_country_meme_frame(
        PORTRAIT, "Canada", photo, CAPTION, character_img=character
    )
    assert with_char.tobytes() == without.tobytes()


def test_missing_font_falls_back_without_raising(
    photo: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(country_meme, "FONT_CANDIDATES", ("/nonexistent/none.ttf",))
    monkeypatch.setattr(country_meme, "_fallback_warned", False)
    with caplog.at_level(logging.WARNING, logger=country_meme.logger.name):
        img = render_country_meme_frame(PORTRAIT, "Canada", photo, CAPTION)
    assert img.size == PORTRAIT
    assert any("PIL default font" in rec.getMessage() for rec in caplog.records)


def test_photo_cache_is_reused(photo: Path) -> None:
    render_country_meme_frame(PORTRAIT, "Canada", photo, CAPTION)
    before = country_meme._cover_photo.cache_info().hits
    render_country_meme_frame(PORTRAIT, "Canada", photo, CAPTION)
    assert country_meme._cover_photo.cache_info().hits > before
