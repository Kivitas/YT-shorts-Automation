"""Tests for the PNG sprite loader and compositor in autoshorts.longform.sprites."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageChops

import autoshorts.longform.sprites as sprites


@pytest.fixture
def sprite_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point SPRITE_DIR at a temp folder and reset the load cache around the test."""
    monkeypatch.setattr(sprites, "SPRITE_DIR", tmp_path)
    sprites.load_sprite.cache_clear()
    yield tmp_path
    sprites.load_sprite.cache_clear()


def _save(folder: Path, name: str, img: Image.Image) -> None:
    img.save(folder / f"{name}.png")


def _solid(size: tuple[int, int], colour: tuple[int, int, int, int]) -> Image.Image:
    return Image.new("RGBA", size, colour)


def _split_sprite() -> Image.Image:
    """Left half red, right half blue, both fully opaque (asymmetric under flip)."""
    img = Image.new("RGBA", (20, 10), (255, 0, 0, 255))
    img.paste((0, 0, 255, 255), (10, 0, 20, 10))
    return img


def test_missing_sprite_returns_none_and_has_sprite_false(sprite_dir: Path) -> None:
    assert sprites.load_sprite("does_not_exist") is None
    assert sprites.has_sprite("does_not_exist") is False
    assert sprites.sprite_size("does_not_exist", 1.0) is None


def test_paste_missing_sprite_returns_false_without_raising(sprite_dir: Path) -> None:
    img = Image.new("RGB", (64, 64), (10, 20, 30))
    before = img.copy()
    assert sprites.paste_sprite(img, "does_not_exist", (32, 32)) is False
    assert ImageChops.difference(img, before).getbbox() is None


def test_loaded_sprite_is_rgba(sprite_dir: Path) -> None:
    _save(sprite_dir, "solid", _solid((8, 6), (0, 255, 0, 255)))
    loaded = sprites.load_sprite("solid")
    assert loaded is not None
    assert loaded.mode == "RGBA"
    assert loaded.size == (8, 6)
    assert sprites.has_sprite("solid") is True


def test_sprite_size_applies_scale(sprite_dir: Path) -> None:
    _save(sprite_dir, "solid", _solid((10, 4), (0, 0, 0, 255)))
    assert sprites.sprite_size("solid", 1.0) == (10, 4)
    assert sprites.sprite_size("solid", 2.5) == (25, 10)


def test_paste_draws_pixels_at_centre(sprite_dir: Path) -> None:
    _save(sprite_dir, "red", _solid((20, 20), (255, 0, 0, 255)))
    img = Image.new("RGB", (100, 100), (0, 0, 0))
    assert sprites.paste_sprite(img, "red", (50, 50)) is True
    assert img.getpixel((50, 50)) == (255, 0, 0)
    assert img.getpixel((2, 2)) == (0, 0, 0)


def test_paste_into_rgba_canvas(sprite_dir: Path) -> None:
    _save(sprite_dir, "red", _solid((10, 10), (255, 0, 0, 255)))
    img = Image.new("RGBA", (50, 50), (0, 0, 0, 0))
    assert sprites.paste_sprite(img, "red", (25, 25)) is True
    assert img.getpixel((25, 25)) == (255, 0, 0, 255)
    assert img.getpixel((0, 0))[3] == 0


def test_flip_changes_pixels(sprite_dir: Path) -> None:
    _save(sprite_dir, "split", _split_sprite())
    plain = Image.new("RGB", (80, 40), (0, 0, 0))
    flipped = Image.new("RGB", (80, 40), (0, 0, 0))
    sprites.paste_sprite(plain, "split", (40, 20))
    sprites.paste_sprite(flipped, "split", (40, 20), flip=True)
    assert ImageChops.difference(plain, flipped).getbbox() is not None
    # Unflipped: red on the left half of the sprite; flipped: blue on the left.
    assert plain.getpixel((32, 20)) == (255, 0, 0)
    assert flipped.getpixel((32, 20)) == (0, 0, 255)


def test_alpha_zero_leaves_image_unchanged(sprite_dir: Path) -> None:
    _save(sprite_dir, "red", _solid((20, 20), (255, 0, 0, 255)))
    img = Image.new("RGB", (60, 60), (12, 34, 56))
    before = img.copy()
    assert sprites.paste_sprite(img, "red", (30, 30), alpha=0.0) is True
    assert ImageChops.difference(img, before).getbbox() is None


def test_half_alpha_blends_with_background(sprite_dir: Path) -> None:
    _save(sprite_dir, "white", _solid((10, 10), (255, 255, 255, 255)))
    img = Image.new("RGB", (40, 40), (0, 0, 0))
    sprites.paste_sprite(img, "white", (20, 20), alpha=0.5)
    r, g, b = img.getpixel((20, 20))
    assert 120 <= r <= 135
    assert r == g == b


def test_rotation_expands_canvas_without_error(sprite_dir: Path) -> None:
    _save(sprite_dir, "bar", _solid((40, 4), (0, 0, 255, 255)))
    img = Image.new("RGB", (200, 200), (0, 0, 0))
    assert sprites.paste_sprite(img, "bar", (100, 100), rotation_deg=90.0) is True
    # Rotated 90 degrees the bar is vertical, so the pixel above the centre is blue.
    assert img.getpixel((100, 80)) == (0, 0, 255)
    assert img.getpixel((80, 100)) == (0, 0, 0)


def test_partially_off_canvas_is_clipped(sprite_dir: Path) -> None:
    _save(sprite_dir, "red", _solid((20, 20), (255, 0, 0, 255)))
    img = Image.new("RGB", (30, 30), (0, 0, 0))
    assert sprites.paste_sprite(img, "red", (0, 0)) is True
    assert img.getpixel((0, 0)) == (255, 0, 0)
    assert img.getpixel((29, 29)) == (0, 0, 0)


def test_cache_returns_same_object(sprite_dir: Path) -> None:
    _save(sprite_dir, "solid", _solid((4, 4), (1, 2, 3, 255)))
    assert sprites.load_sprite("solid") is sprites.load_sprite("solid")


def test_missing_sprite_warns_once(sprite_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger=sprites.logger.name):
        sprites.load_sprite("ghost")
        sprites.load_sprite("ghost")
        sprites.paste_sprite(Image.new("RGB", (8, 8)), "ghost", (4, 4))
    warnings = [r for r in caplog.records if "ghost" in r.getMessage()]
    assert len(warnings) == 1
