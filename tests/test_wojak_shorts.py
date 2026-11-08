"""Tests for the 9:16 Vertical Wojak Shorts Engine (autoshorts.longform.wojak_shorts)."""

from __future__ import annotations

import os
import types
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from autoshorts import cli
from autoshorts.longform.wojak_shorts import (
    CARD_MAX_HEIGHT,
    CARD_Y0,
    COLOR_BLACK,
    COLOR_WHITE,
    DISCLAIMER_DURATION,
    HEADER_HEIGHT,
    SAFE_BOTTOM_OVERLAY,
    SAFE_LEFT,
    SAFE_RIGHT,
    SAFE_WIDTH,
    SHORTS_HEIGHT,
    SHORTS_WIDTH,
    WojakShortsConfig,
    WojakShortsSegment,
    assemble_shorts_audio,
    build_wojak_shorts_script,
    create_soft_drop_shadow,
    draw_country_header_banner,
    fit_punchline,
    make_shorts_thumbnail,
    render_shorts_country_frame,
    render_shorts_disclaimer_frame,
    render_wojak_shorts_video,
    synth_bouncy_meme_bgm,
    synth_cash_register,
    synth_flatline,
    synth_record_scratch,
)


# ============================================================================
# 1. Format & Vertical Geometry Tests
# ============================================================================


def test_shorts_resolution_constants() -> None:
    assert SHORTS_WIDTH == 1080
    assert SHORTS_HEIGHT == 1920
    assert HEADER_HEIGHT == 150
    assert 140 <= HEADER_HEIGHT <= 160


def test_safe_zone_margins() -> None:
    # Ensure safe zone avoids right margin icon column (x > 920)
    assert SAFE_RIGHT <= 920
    right_margin = SHORTS_WIDTH - SAFE_RIGHT
    assert right_margin >= 160  # At least 160px clearance for action buttons

    # Ensure left margin is balanced
    assert SAFE_LEFT >= 80

    # Ensure bottom 20% overlay margin
    bottom_20_percent = int(SHORTS_HEIGHT * 0.80)  # 1536
    assert SAFE_BOTTOM_OVERLAY <= bottom_20_percent


def test_render_disclaimer_frame_dimensions_and_mode() -> None:
    img = render_shorts_disclaimer_frame()
    assert img.size == (1080, 1920)
    assert img.mode == "RGB"


def test_render_country_frame_dimensions_and_mode() -> None:
    frame = render_shorts_country_frame(
        country="USA",
        caption="Free emergency hospital band-aid: $84,200. Ambulance ride: mortgage house.",
        flag="🇺🇸",
        expression="shocked",
    )
    assert frame.size == (1080, 1920)
    assert frame.mode == "RGB"


def test_header_banner_is_white_and_150px() -> None:
    frame = render_shorts_country_frame(
        country="GERMANY",
        caption="Fill out Form 28-B before emergency.",
        flag="🇩🇪",
        expression="deadpan",
    )
    # Header area (top 140px) must have white background around edges
    assert frame.getpixel((10, 10)) == COLOR_WHITE
    assert frame.getpixel((1070, 10)) == COLOR_WHITE
    assert frame.getpixel((50, 140)) == COLOR_WHITE


def test_floating_joke_card_geometry_and_font_fit() -> None:
    short_caption = "Hospital bill: $50,000."
    font_s, lines_s, line_h_s = fit_punchline(short_caption)
    assert len(lines_s) == 1
    # Meets Requirement 1: "bold black font, 40-52px"
    assert font_s.size >= 40

    long_caption = (
        "Your routine MRI appointment is scheduled for spring of 2031. "
        "Doctor apologized 14 times and gave me a maple syrup lozenge."
    )
    font_l, lines_l, line_h_l = fit_punchline(long_caption)
    assert len(lines_l) <= 3
    assert font_l.size >= 38
    assert font_s.size >= font_l.size


def test_floating_card_stays_strictly_in_mobile_safe_zone() -> None:
    card_w = SAFE_WIDTH
    card_x0 = SAFE_LEFT
    card_x1 = card_x0 + card_w
    # Verify card right edge never touches right margin icon stack
    assert card_x1 <= 920

    # Verify vertical position stays in upper-mid safe zone
    assert CARD_Y0 >= 180
    assert CARD_Y0 + CARD_MAX_HEIGHT < 600


# ============================================================================
# 2. Audio & SFX Synthesis Tests
# ============================================================================


def test_synth_cash_register(tmp_path: Path) -> None:
    out = tmp_path / "cash.wav"
    synth_cash_register(out, duration=0.85)
    assert out.is_file()
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 1
        assert w.getnframes() == int(0.85 * 48000)


def test_synth_flatline(tmp_path: Path) -> None:
    out = tmp_path / "flatline.wav"
    synth_flatline(out, duration=1.0)
    assert out.is_file()
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 1
        assert w.getnframes() == 48000


def test_synth_record_scratch(tmp_path: Path) -> None:
    out = tmp_path / "scratch.wav"
    synth_record_scratch(out, duration=0.45)
    assert out.is_file()
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 1


def test_synth_bouncy_meme_bgm(tmp_path: Path) -> None:
    out = tmp_path / "bgm.wav"
    synth_bouncy_meme_bgm(out, duration=3.0, bpm=126.0)
    assert out.is_file()
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 1
        assert w.getnframes() == 3 * 48000


def test_assemble_shorts_audio(tmp_path: Path) -> None:
    segs = [
        WojakShortsSegment(country="USA", caption="Bill: $100K", sfx_cue="cash_register", duration=4.0),
        WojakShortsSegment(country="UK", caption="Wait: 5 years", sfx_cue="flatline", duration=4.0),
    ]
    total_dur = DISCLAIMER_DURATION + 8.0
    out = assemble_shorts_audio(segs, total_dur, tmp_path)
    assert out.is_file()
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 48000
        assert abs(w.getnframes() - int(total_dur * 48000)) <= 100


# ============================================================================
# 3. Script & Thumbnail Tests
# ============================================================================


def test_build_wojak_shorts_script_healthcare() -> None:
    segs = build_wojak_shorts_script("Every Country's Hospital Be Like", segment_count=6)
    assert len(segs) == 6
    assert any(s.country == "USA" for s in segs)
    assert any(s.country == "UK" for s in segs)
    assert all(s.flag != "" for s in segs)
    assert all(s.caption != "" for s in segs)


def test_build_wojak_shorts_script_transport() -> None:
    segs = build_wojak_shorts_script("Every Country's Public Transport", segment_count=5)
    assert len(segs) == 5
    assert any(s.country == "JAPAN" for s in segs)


def test_make_shorts_thumbnail(tmp_path: Path) -> None:
    thumb = tmp_path / "thumb.png"
    out = make_shorts_thumbnail("Every Country's Hospital", top_country="USA", out_path=thumb)
    assert out.is_file()
    with Image.open(out) as img:
        assert img.size == (1080, 1920)
        assert img.mode == "RGB"


# ============================================================================
# 4. CLI Argument Parsing & Routing Tests
# ============================================================================


def test_parser_accepts_aspect_ratio_and_shorts_flags() -> None:
    parser = cli.build_parser()
    args1 = parser.parse_args(["longform", "--aspect-ratio", "9:16"])
    assert args1.aspect_ratio == "9:16"
    assert args1.shorts is False

    args2 = parser.parse_args(["longform", "--shorts"])
    assert args2.shorts is True
    assert args2.aspect_ratio == "16:9"  # default flag before check

    args3 = parser.parse_args(["longform", "--shorts", "--segments", "8"])
    assert args3.shorts is True
    assert args3.segments == 8


def test_cmd_longform_routes_to_wojak_shorts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    mock_render = MagicMock()
    mock_render.return_value = types.SimpleNamespace(
        title="Test Shorts Title",
        video_path=Path("out/shorts_video.mp4"),
        thumbnail_path=Path("out/shorts_thumb.png"),
        duration=32.5,
    )
    monkeypatch.setattr("autoshorts.longform.wojak_shorts.render_wojak_shorts_video", mock_render)

    args = cli.build_parser().parse_args(["longform", "--topic", "hospital", "--shorts"])
    rc = cli.cmd_longform(args, MagicMock(), MagicMock())

    assert rc == 0
    mock_render.assert_called_once()
    out = capsys.readouterr().out
    assert "Test Shorts Title" in out
    assert "Aspect Ratio: 9:16" in out
    assert "Duration: 32.5s" in out


def test_cmd_longform_default_maintains_16_9(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    mock_render_lf = MagicMock()
    mock_render_lf.return_value = types.SimpleNamespace(
        title="Standard Longform",
        video_path=Path("out/longform.mp4"),
        thumbnail_path=Path("out/thumb.png"),
        duration=150.0,
    )
    monkeypatch.setattr("autoshorts.longform.render.render_longform", mock_render_lf)

    args = cli.build_parser().parse_args(["longform", "--topic", "science"])
    rc = cli.cmd_longform(args, MagicMock(), MagicMock())

    assert rc == 0
    mock_render_lf.assert_called_once()
    out = capsys.readouterr().out
    assert "Standard Longform" in out
    assert "Aspect Ratio: 9:16" not in out
