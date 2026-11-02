"""Smoke tests for the flat-vector longform visuals: transitions, characters and fonts."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from autoshorts.core.theme import FONT_DISPLAY, lerp_rgb
from autoshorts.longform import drawer
from autoshorts.longform.drawer import (
    HEIGHT,
    POSES,
    TRANSITIONS,
    WIDTH,
    _scene_background,
    _transition,
    draw_character,
    font_candidates,
)

SIZE = (1920, 1080)


def _frame(scene: str) -> Image.Image:
    return _scene_background(scene).copy()


def test_transition_list_includes_new_kinds() -> None:
    assert {"slide", "zoom", "iris", "wipe", "crossfade", "push"} <= set(TRANSITIONS)


@pytest.mark.parametrize("kind", TRANSITIONS)
@pytest.mark.parametrize("k", [0.0, 0.5, 1.0])
def test_transition_renders_full_hd_frame(kind: str, k: float, tmp_path: Path) -> None:
    prev = _frame("bedroom")
    cur = _frame("street")
    out = _transition(kind, prev, cur, k)
    assert isinstance(out, Image.Image)
    assert out.size == SIZE
    if k == 0.5:
        out.save(tmp_path / f"transition_{kind}.png")
        assert (tmp_path / f"transition_{kind}.png").exists()


def test_character_frame_renders_at_full_hd(tmp_path: Path) -> None:
    img = _frame("office")
    draw = ImageDraw.Draw(img)
    draw_character(
        draw,
        POSES["point"],
        (520, 640),
        shirt=drawer.SHIRT_BLUE,
        mood="smug",
        viseme="AI",
        pointer_stick=True,
        is_speaking=True,
    )
    assert img.size == (WIDTH, HEIGHT)
    img.save(tmp_path / "character.png")
    assert (tmp_path / "character.png").exists()


def test_font_candidates_put_theme_family_first() -> None:
    assert font_candidates(True)[0].startswith(FONT_DISPLAY[0])
    # Arial remains in the fallback chain.
    assert any("arial" in c.lower() for c in font_candidates(True))
    assert any("arial" in c.lower() for c in font_candidates(False))


def test_lerp_gradient_helper_endpoints() -> None:
    img = Image.new("RGB", (40, 40), (0, 0, 0))
    drawer._gradient(img, (0, 0, 40, 40), (0, 0, 0), (200, 100, 50))
    assert img.getpixel((0, 0)) == lerp_rgb((0, 0, 0), (200, 100, 50), 0.0)
    assert img.getpixel((0, 39)) == (200, 100, 50)
