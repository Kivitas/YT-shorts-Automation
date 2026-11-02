"""Phase 2 visual checks: flat 'kurz' caption preset and theme-driven colours (no rendering)."""

from __future__ import annotations

from autoshorts.core import theme
from autoshorts.media.ass_styler import CAPTION_PRESETS, rgb_to_ass_color
from autoshorts.media.ranking_overlay import ACCENT_COLOR


def _bgr_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}&"


def test_kurz_preset_exists() -> None:
    assert "kurz" in CAPTION_PRESETS


def test_kurz_has_no_stroke_or_glow() -> None:
    kurz = CAPTION_PRESETS["kurz"]
    # BorderStyle=3 draws a box; its Outline value is box padding, not a glyph stroke.
    # The glyph stroke colour is the box colour, and there is no drop shadow or glow.
    assert kurz["border_style"] == 3
    assert kurz["shadow"] == 0
    assert kurz["outline_color"] == kurz["back_color"]
    # The box is semi-transparent: alpha byte between fully clear and fully opaque.
    alpha = int(kurz["back_color"][2:4], 16)
    assert 0 < alpha < 0xFF


def test_kurz_colours_match_theme_tokens() -> None:
    kurz = CAPTION_PRESETS["kurz"]
    assert kurz["primary_color"] == _bgr_hex(theme.TEXT_PRIMARY)
    assert kurz["active_color"] == _bgr_hex(theme.ACCENT_YELLOW)
    assert kurz["emphasis_color"] == _bgr_hex(theme.ACCENT_TEAL)
    assert kurz["back_color"][-6:] == _bgr_hex(theme.BG_DEEP)[-6:] or kurz["back_color"].endswith(
        f"{theme.BG_DEEP[2]:02X}{theme.BG_DEEP[1]:02X}{theme.BG_DEEP[0]:02X}&"
    )


def test_kurz_font_is_medium_weight() -> None:
    assert CAPTION_PRESETS["kurz"]["font_name"] == "Montserrat Medium"


def test_rgb_to_ass_color_is_bgr_with_alpha() -> None:
    assert rgb_to_ass_color((255, 200, 60)) == "&H003CC8FF&"
    assert rgb_to_ass_color((14, 18, 38), 0x4D) == "&H4D26120E&"


def test_ranking_accent_uses_theme_yellow() -> None:
    assert ACCENT_COLOR == rgb_to_ass_color(theme.ACCENT_YELLOW)
    assert ACCENT_COLOR != "&H0000E6FF"  # old hard-coded #FFE600
