"""Unit tests for pure-PIL 16:9 Wojak Compositor (SapX style)."""

import pytest
from PIL import Image

from autoshorts.longform.wojak_compositor import (
    FRAME_HEIGHT,
    FRAME_WIDTH,
    HEADER_HEIGHT,
    create_soft_drop_shadow,
    fit_punchline,
    fit_title,
    get_font,
    render_disclaimer_frame,
    render_wojak_scene_frame,
    wrap_text,
)


def test_font_retrieval():
    font = get_font(36, bold=True)
    assert font is not None


def test_text_wrapping():
    font = get_font(32, bold=True)
    text = "This is a long sentence meant to test wrapping across multiple lines cleanly"
    lines = wrap_text(font, text, max_width=300)
    assert len(lines) >= 2
    assert " ".join(" ".join(lines).split()) == text


def test_fit_title():
    font, size = fit_title("UNITED STATES OF AMERICA", max_w=800, max_h=80, initial_size=58)
    assert size >= 24
    assert font.getlength("UNITED STATES OF AMERICA") <= 800


def test_fit_punchline():
    font, lines, line_h = fit_punchline(
        "Tylenol: $4,500. Ambulance ride: $18,000. Total: Bankruptcy.",
        max_w=600,
        max_h=200,
        max_lines=3,
        initial_size=42,
    )
    assert 1 <= len(lines) <= 3
    assert line_h > 0


def test_soft_drop_shadow():
    shadow, px, py = create_soft_drop_shadow(400, 200, radius=16, blur=18, offset_y=10)
    assert shadow.size[0] == 400 + px * 2
    assert shadow.size[1] == 200 + py * 2
    assert shadow.mode == "RGBA"


def test_render_disclaimer_frame():
    img = render_disclaimer_frame(size=(FRAME_WIDTH, FRAME_HEIGHT))
    assert img.size == (FRAME_WIDTH, FRAME_HEIGHT)
    assert img.mode == "RGB"


def test_render_wojak_scene_frame_left():
    bg = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT - HEADER_HEIGHT), (200, 210, 220))
    char = Image.new("RGBA", (300, 500), (255, 0, 0, 255))
    frame = render_wojak_scene_frame(
        country="USA",
        caption="1x Tylenol: $4,500. Total: Bankruptcy.",
        background_img=bg,
        character_img=char,
        character_side="left",
    )
    assert frame.size == (FRAME_WIDTH, FRAME_HEIGHT)
    assert frame.mode == "RGB"
    # Header bar should be white
    assert frame.getpixel((FRAME_WIDTH // 2, 20)) == (255, 255, 255)


def test_render_wojak_scene_frame_right():
    bg = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT - HEADER_HEIGHT), (220, 230, 240))
    char = Image.new("RGBA", (300, 500), (0, 255, 0, 255))
    frame = render_wojak_scene_frame(
        country="SWEDEN",
        caption="Visit cost $12. Doctor gave me tea.",
        background_img=bg,
        character_img=char,
        character_side="right",
    )
    assert frame.size == (FRAME_WIDTH, FRAME_HEIGHT)
    assert frame.mode == "RGB"
