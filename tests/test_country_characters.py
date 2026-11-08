"""Tests for the original country stereotype character generator (no network)."""

from __future__ import annotations

import pytest
from PIL import Image

from autoshorts.longform import country_characters as cc


@pytest.fixture(autouse=True)
def _tmp_characters_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CHARACTERS_DIR", tmp_path)
    return tmp_path


def test_first_call_writes_png_600x1000_rgba(tmp_path):
    path = cc.character_for_country("Canada")
    assert path == tmp_path / "canada.png"
    assert path.exists()
    with Image.open(path) as img:
        assert img.size == (600, 1000)
        assert img.mode == "RGBA"


def test_cached_on_second_call(tmp_path):
    first = cc.character_for_country("New Zealand")
    stamp = first.stat().st_mtime_ns
    assert cc.character_for_country("New Zealand") == first
    assert first.stat().st_mtime_ns == stamp
    assert first.name == "new_zealand.png"


def test_transparent_corners_and_opaque_figure():
    img = cc.draw_character("Canada", "neutral")
    assert img.mode == "RGBA"
    for xy in ((0, 0), (599, 0), (0, 999), (599, 999), (5, 500), (594, 500)):
        assert img.getpixel(xy)[3] == 0
    alpha = img.getchannel("A")
    assert alpha.getbbox() is not None


@pytest.mark.parametrize("country", ["Canada", "New Zealand", "France"])
def test_expressions_produce_different_pixels(country):
    frames = {e: cc.draw_character(country, e).tobytes() for e in cc.EXPRESSIONS}
    assert len(set(frames.values())) == len(cc.EXPRESSIONS)


def test_expression_changes_only_the_face():
    neutral = cc.draw_character("Canada", "neutral").crop((0, 430, 600, 1000))
    sad = cc.draw_character("Canada", "sad").crop((0, 430, 600, 1000))
    assert neutral.tobytes() == sad.tobytes()


def test_same_seed_gives_identical_output():
    assert cc.draw_character("Canada", "smug").tobytes() == (
        cc.draw_character("Canada", "smug").tobytes()
    )
    assert cc._seed_for("canada") == cc._seed_for("canada")
    assert cc._seed_for("canada") != cc._seed_for("new_zealand")


def test_expression_file_naming(tmp_path):
    assert cc.character_for_country("Canada", "sad").name == "canada.sad.png"
    assert cc.character_for_country("Canada", "neutral").name == "canada.png"


def test_unknown_country_uses_fallback(tmp_path):
    assert "france" not in cc._SPECS
    path = cc.character_for_country("France")
    assert path.name == "france.png"
    with Image.open(path) as img:
        assert img.size == (600, 1000)
        assert img.mode == "RGBA"
    assert cc.draw_character("France").tobytes() != cc.draw_character("Canada").tobytes()
    assert cc.draw_character("France").tobytes() != cc.draw_character("New Zealand").tobytes()


def test_draw_character_does_not_write_files(tmp_path):
    cc.draw_character("Canada", "tired")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("country", "slug"),
    [
        ("Canada", "canada"),
        ("CANADA", "canada"),
        ("  New Zealand  ", "new_zealand"),
        ("New   Zealand!!", "new_zealand"),
        ("Côte d'Ivoire", "cote_d_ivoire"),
        ("Bosnia & Herzegovina", "bosnia_herzegovina"),
    ],
)
def test_slug_handling(country, slug):
    assert cc.country_slug(country) == slug


@pytest.mark.parametrize("country", ["", "   ", "!!!", "宇宙"])
def test_slug_rejects_empty_names(country):
    with pytest.raises(ValueError):
        cc.country_slug(country)


def test_same_country_different_spelling_shares_cache(tmp_path):
    assert cc.character_for_country("new zealand") == cc.character_for_country("New Zealand")


def test_unknown_expression_rejected():
    with pytest.raises(ValueError):
        cc.character_for_country("Canada", "furious")
    with pytest.raises(ValueError):
        cc.draw_character("Canada", "furious")
