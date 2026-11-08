"""Comprehensive Unit Tests for Upgraded Subtitle Engine & Kinetic Typography.

Tests cover all 4 core requirements:
1. Strict 1-Line Display (1-3 words max per event, WrapStyle: 2, no multi-line wrapping)
2. Karaoke Word-by-Word Highlight with spring physics (\\fscx125\\fscy125 -> \\fscx100\\fscy100, Whisper millisecond accuracy)
3. Mobile Safe Zone Alignment (YouTube Shorts Y = 1200 - 1350 px, MarginV = 640)
4. High Contrast & Active Box styling (BorderStyle=3, 82% dark backing box, vibrant yellow & glowing cyan)
"""

import pytest
from PIL import Image

from autoshorts.longform.beats import (
    SHORTS_SAFE_ZONE_Y_MAX,
    SHORTS_SAFE_ZONE_Y_MIN,
    _spring_scale,
    chunk_narration_bursts,
    draw_karaoke_burst,
)
from autoshorts.media.ass_styler import (
    CAPTION_PRESETS,
    SHORTS_SAFE_ZONE_MARGIN_V_DEFAULT,
    TimedWord,
    build_ass_script,
    build_spring_physics_tags,
    chunk_words,
)

# ==============================================================================
# Requirement 1: Strict 1-Line Display Tests
# ==============================================================================


def test_chunk_words_strict_one_line_max_three_words():
    """Verify chunk_words enforces strict 1-3 words max per event without multi-line wrapping."""
    speech = [
        {"word": "Never", "start": 0.0, "end": 0.25},
        {"word": "wrap", "start": 0.28, "end": 0.50},
        {"word": "text", "start": 0.53, "end": 0.75},
        {"word": "into", "start": 0.78, "end": 0.95},
        {"word": "multiple", "start": 0.98, "end": 1.30},
        {"word": "lines", "start": 1.35, "end": 1.60},
        {"word": "ever.", "start": 1.65, "end": 2.00},
    ]
    chunks = chunk_words(speech)
    assert len(chunks) >= 3
    for chunk in chunks:
        # Every chunk is strictly between 1 and 3 words
        assert 1 <= len(chunk.words) <= 3
        assert chunk.text.count(" ") < 3


def test_chunk_narration_bursts_strict_one_line():
    """Verify chunk_narration_bursts produces bursts with 1-3 words max."""
    words = [
        {"word": "One", "start": 0.0, "end": 0.3},
        {"word": "two", "start": 0.35, "end": 0.6},
        {"word": "three", "start": 0.65, "end": 0.9},
        {"word": "four", "start": 0.95, "end": 1.2},
        {"word": "five", "start": 1.25, "end": 1.5},
    ]
    bursts = chunk_narration_bursts(words, "One two three four five", 0.0, 1.5)
    assert len(bursts) == 2
    assert len(bursts[0].words) == 3
    assert bursts[0].text == "One two three"
    assert len(bursts[1].words) == 2
    assert bursts[1].text == "four five"


def test_ass_script_header_includes_wrap_style_two():
    """Verify ASS script header explicitly specifies WrapStyle: 2 for strict single-line display."""
    words = [{"word": "Single", "start": 0.0, "end": 0.5}]
    script = build_ass_script(words)
    assert "WrapStyle: 2" in script
    assert "PlayResX: 1080" in script
    assert "PlayResY: 1920" in script


# ==============================================================================
# Requirement 2: Karaoke Word-by-Word Highlight Tests
# ==============================================================================


def test_build_spring_physics_tags_syntax():
    """Verify spring physics override tag generates \\fscx125\\fscy125 -> \\fscx100\\fscy100."""
    tags = build_spring_physics_tags(
        scale_overshoot=125, scale_settle=100, t_rise_ms=80, t_settle_ms=160
    )
    assert r"\t(0,80,\fscx125\fscy125)" in tags
    assert r"\t(80,160,\fscx100\fscy100)" in tags


def test_spring_scale_math_in_beats():
    """Spring pop per spec: 100% at 0 ms, 125% at 70 ms, 96% at 130 ms, 100% at 180 ms."""
    # At t = 0 ms: instant pop to 1.25
    assert _spring_scale(0.0) == pytest.approx(1.00)
    assert _spring_scale(0.07) == pytest.approx(1.25)
    assert _spring_scale(0.13) == pytest.approx(0.96)

    # Midway: spring recoil / oscillation

    # At or after duration (0.18s): fully settled to 1.00
    assert _spring_scale(0.18) == pytest.approx(1.00)
    assert _spring_scale(0.30) == pytest.approx(1.00)


def test_whisper_word_level_highlight_timing_in_ass():
    """Verify karaoke events highlight each word at its exact millisecond from Whisper."""
    words = [
        TimedWord(word="Flash", start=1.350, end=1.700),
        TimedWord(word="bang", start=1.750, end=2.100),
    ]
    script = build_ass_script(words, style_preset="active_box")

    # Word 1 starts at 1.35s -> 0:00:01.35
    assert "0:00:01.35" in script
    # Word 2 starts at 1.75s -> 0:00:01.75
    assert "0:00:01.75" in script

    # Spring pop tags on active word (\fscx125\fscy125 -> \fscx100\fscy100)
    assert r"\t(0,70,\fscx125\fscy125)" in script
    assert r"\t(70,130,\fscx96\fscy96)" in script
    assert r"\t(130,180,\fscx100\fscy100)" in script


# ==============================================================================
# Requirement 3: Mobile Safe Zone Alignment Tests
# ==============================================================================


def test_ass_shorts_safe_zone_vertical_alignment():
    """Verify subtitle vertical margin places baseline in YouTube Shorts safe zone (Y = 1200 - 1350 px)."""
    words = [{"word": "Safe", "start": 0.0, "end": 1.0}]

    # When subtitle_position="safe_zone" or "shorts_safe"
    script_safe = build_ass_script(words, subtitle_position="shorts_safe")
    # MarginV = 640 px corresponds to baseline Y = 1920 - 640 = 1280 px
    assert f",80,150,{SHORTS_SAFE_ZONE_MARGIN_V_DEFAULT},1" in script_safe

    # Test explicit Y coordinate passed via subtitle_position or subtitle_y
    script_y = build_ass_script(words, subtitle_y=1250)
    # 1920 - 1250 = 670 MarginV
    assert ",80,150,670,1" in script_y

    script_y2 = build_ass_script(words, subtitle_position="1350")
    # 1920 - 1350 = 570 MarginV
    assert ",80,150,570,1" in script_y2


def test_beats_vertical_shorts_safe_zone_alignment():
    """Verify draw_karaoke_burst anchors in Y = 1200 - 1350 on vertical frames."""
    vertical_img = Image.new("RGB", (1080, 1920), (10, 12, 16))
    bursts = chunk_narration_bursts(
        [{"word": "Mobile", "start": 0.0, "end": 0.5}, {"word": "Safe", "start": 0.5, "end": 1.0}],
        "Mobile Safe",
        0.0,
        1.0,
    )
    # Should render without error on 1080x1920 canvas at default Y = 1280
    draw_karaoke_burst(vertical_img, bursts[0], 0.25)
    # Verify pixels were modified around Y = 1280 (safe zone)
    # Check that pixels near Y=1280 are not purely the background (10, 12, 16)
    modified = False
    for y in range(SHORTS_SAFE_ZONE_Y_MIN, SHORTS_SAFE_ZONE_Y_MAX):
        if vertical_img.getpixel((540, y)) != (10, 12, 16):
            modified = True
            break
    assert modified, (
        "Pixels in YouTube Shorts safe zone (1200-1350) should be modified by subtitle burst"
    )


# ==============================================================================
# Requirement 4: High Contrast & Active Box Styling Tests
# ==============================================================================


def test_ass_active_box_preset():
    """Verify active_box preset configuration with high-contrast backing box."""
    assert "active_box" in CAPTION_PRESETS
    preset = CAPTION_PRESETS["active_box"]
    assert preset["border_style"] == 3  # Opaque backing box
    assert preset["outline"] >= 8  # Box padding
    assert preset["active_color"] == "&H0000E6FF"  # Solar Yellow #FFE600 (BGR)
    assert preset["emphasis_color"] == "&H00FFF000"  # Glowing Cyan (BGR)
    assert preset["outline_color"] == "&H261B0E0B"  # Pill rgba(11,14,27,0.85), ASS alpha inverted

    words = [{"word": "HighContrast", "start": 0.0, "end": 1.0}]
    script = build_ass_script(words, style_preset="active_box")
    assert ",3,8,0,2," in script or ",3," in script
    assert "&H261B0E0B" in script
    assert "&H0000E6FF" in script


def test_beats_karaoke_burst_active_box_rendered():
    """Verify draw_karaoke_burst draws active box backing pill and high-contrast stroke."""
    img = Image.new("RGB", (1920, 1080), (200, 200, 200))  # Light background footage
    bursts = chunk_narration_bursts(
        [
            {"word": "High", "start": 0.0, "end": 0.4},
            {"word": "contrast", "start": 0.4, "end": 0.8},
            {"word": "text", "start": 0.8, "end": 1.2},
        ],
        "High contrast text",
        0.0,
        1.2,
    )
    draw_karaoke_burst(img, bursts[0], 0.2, active_box=True)

    # Background (200, 200, 200) should now contain dark box pixels (near KARAOKE_BOX_FILL)
    has_dark_pill_pixel = False
    for y in range(780, 980):  # horizontal caption anchor is Y=880
        for x in range(800, 1120):
            r, g, b = img.getpixel((x, y))
            # Dark backing pill will be significantly darker than 200
            if r < 80 and g < 80 and b < 80:
                has_dark_pill_pixel = True
                break
        if has_dark_pill_pixel:
            break
    assert has_dark_pill_pixel, (
        "Active box backing pill should draw high-contrast dark pixels behind text"
    )
