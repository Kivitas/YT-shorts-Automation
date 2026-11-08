"""Tests for the parts-based character composer (no network, no real assets written)."""

from __future__ import annotations

import itertools

import pytest
from PIL import Image

from autoshorts.longform import character_composer as cc

EX_CANADA = {"country": "Canada", "setting": "hospital waiting room", "emotion": "tired",
             "keywords": ["wait", "phone"]}  # fmt: skip
EX_NZ = {"country": "New Zealand", "setting": "clinic", "emotion": "smug", "keywords": ["rugby"]}
EX_ITALY = {"country": "Italy", "setting": "ICU", "emotion": "sad", "keywords": ["pasta"]}


@pytest.fixture(autouse=True)
def _tmp_characters_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "CHARACTERS_DIR", tmp_path)
    return tmp_path


def _pixels(spec) -> bytes:
    return cc.draw_character_from_spec(spec).tobytes()


# --- part libraries -------------------------------------------------------------------------


def test_part_library_sizes_meet_the_brief():
    assert len(cc.HEADS) >= 4
    assert len(cc.HAIRS) >= 6
    assert len(cc.TORSOS) >= 4
    assert len(cc.PROPS) - 1 >= 10  # "none" is not a prop
    assert set(cc.EXPRESSIONS) >= {"neutral", "smug", "annoyed", "tired", "sad", "happy", "shocked"}
    assert len(cc.SKIN_TONES) >= 6
    assert set(cc.GENDERS) == {"female", "male", "androgynous"}
    assert set(cc.HEIGHTS) == {"short", "average", "tall"}
    assert set(cc.BUILDS) == {"slim", "average", "broad"}


# --- spec mapping ---------------------------------------------------------------------------


def test_example_canada_maps_to_phone_toque_and_tired():
    spec = cc.spec_from_context(EX_CANADA)
    assert spec.prop == "phone"
    assert spec.hair == "toque"
    assert spec.head == "round"
    assert spec.expression == "tired"
    assert spec.torso == "lab_coat"  # hospital setting


def test_example_nz_maps_to_rugby_ball_and_jersey():
    spec = cc.spec_from_context(EX_NZ)
    assert spec.prop == "ball"
    assert spec.torso == "jersey"
    assert spec.head == "tall"
    assert spec.expression == "smug"


def test_example_italy_maps_to_pasta_pot_and_sad():
    spec = cc.spec_from_context(EX_ITALY)
    assert spec.prop == "pasta_pot"
    assert spec.torso == "lab_coat"
    assert spec.expression == "sad"
    assert spec.head == "boxy"


@pytest.mark.parametrize(
    ("keyword", "prop"),
    [("bill", "bill"), ("tea", "tea_cup"), ("coffee", "coffee_cup"), ("pasta", "pasta_pot"),
     ("wait", "phone"), ("phone", "phone"), ("pastry", "pastry"), ("bandage", "bandage")],
)  # fmt: skip
def test_keyword_table_maps_to_props(keyword, prop):
    assert cc.spec_from_context({"keywords": [keyword]}).prop == prop


def test_setting_supplies_default_prop_and_keyword_overrides_it():
    assert cc.spec_from_context({"setting": "clinic"}).prop == "stethoscope"
    assert cc.spec_from_context({"setting": "clinic", "keywords": ["tea"]}).prop == "tea_cup"


def test_explicit_hair_keyword_overrides_country_accent():
    assert cc.spec_from_context({"country": "Canada", "keywords": ["curls"]}).hair == "curls"


def test_unknown_inputs_do_not_raise():
    spec = cc.spec_from_context(
        {"country": None, "setting": 12, "emotion": "bewildered",
         "keywords": ["zzz", None, 4], "gender": "robot", "skin_tone": 99}
    )  # fmt: skip
    assert spec.expression == "neutral"
    assert spec.prop == "none"
    assert spec.gender in cc.GENDERS
    assert 0 <= spec.skin_tone < len(cc.SKIN_TONES)
    assert cc.spec_from_context("not a dict") == cc.spec_from_context({})
    assert cc.normalise_spec(cc.CharacterSpec(head="nope", prop="laser")).prop == "none"


# --- body attributes ------------------------------------------------------------------------


def test_explicit_body_attributes_override_seeded_choice():
    base = cc.spec_from_context(EX_CANADA)
    assert base.gender in cc.GENDERS  # sanity: seeded values exist
    forced = cc.spec_from_context(
        {**EX_CANADA, "gender": "male", "skin_tone": 5, "height": "tall", "build": "broad"}
    )  # fmt: skip
    assert (forced.gender, forced.skin_tone, forced.height, forced.build) == (
        "male", 5, "tall", "broad")  # fmt: skip


def test_same_context_always_gives_same_attributes():
    first = cc.spec_from_context(EX_NZ)
    assert cc.spec_from_context(dict(EX_NZ)) == first
    assert cc.spec_hash(cc.spec_from_context(dict(EX_NZ))) == cc.spec_hash(first)


def test_country_change_keeps_body_attributes_when_not_given_explicitly():
    a = cc.spec_from_context({"country": "Canada", "setting": "clinic", "emotion": "sad",
                              "keywords": ["tea"]})  # fmt: skip
    b = cc.spec_from_context({"country": "Japan", "setting": "clinic", "emotion": "sad",
                              "keywords": ["tea"]})  # fmt: skip
    for name in ("gender", "skin_tone", "height", "build", "legwear"):
        assert getattr(a, name) == getattr(b, name)


def test_body_attributes_are_in_the_spec_hash():
    base = cc.CharacterSpec()
    assert cc.spec_hash(base) != cc.spec_hash(cc.CharacterSpec(skin_tone=0))
    assert cc.spec_hash(base) != cc.spec_hash(cc.CharacterSpec(height="tall"))
    assert cc.spec_hash(base) != cc.spec_hash(cc.CharacterSpec(build="broad"))
    assert cc.spec_hash(base) != cc.spec_hash(cc.CharacterSpec(gender="female"))


# --- rendering ------------------------------------------------------------------------------


def test_render_is_600x1000_rgba_with_transparent_corners():
    img = cc.draw_character_from_spec(cc.spec_from_context(EX_ITALY))
    assert img.size == (600, 1000)
    assert img.mode == "RGBA"
    for xy in ((0, 0), (599, 0), (0, 999), (599, 999)):
        assert img.getpixel(xy)[3] == 0


def test_same_spec_renders_identical_pixels():
    spec = cc.spec_from_context(EX_CANADA)
    assert _pixels(spec) == _pixels(spec)


def test_each_expression_renders_differently():
    images = {expr: _pixels(cc.CharacterSpec(expression=expr)) for expr in cc.EXPRESSIONS}
    assert len(set(images.values())) == len(cc.EXPRESSIONS)


def test_each_part_renders_without_error():
    specs = [cc.CharacterSpec(head=v) for v in cc.HEADS]
    specs += [cc.CharacterSpec(hair=v) for v in cc.HAIRS]
    specs += [cc.CharacterSpec(torso=v) for v in cc.TORSOS]
    specs += [cc.CharacterSpec(prop=v) for v in cc.PROPS]
    specs += [cc.CharacterSpec(legwear="skirt", gender="female")]
    specs += [cc.CharacterSpec(skin_tone=t, height=h, build=b)
              for t, h, b in ((0, "short", "slim"), (5, "tall", "broad"))]  # fmt: skip
    for spec in specs:
        assert cc.draw_character_from_spec(spec).size == (600, 1000)


def test_three_example_contexts_render_differently():
    images = [_pixels(cc.spec_from_context(ctx)) for ctx in (EX_CANADA, EX_NZ, EX_ITALY)]
    assert len(set(images)) == 3


# --- cache ----------------------------------------------------------------------------------


def test_cache_writes_once_and_second_call_does_not_draw(monkeypatch, tmp_path):
    calls = []
    original = cc.draw_character_from_spec

    def counting(spec):
        calls.append(spec)
        return original(spec)

    monkeypatch.setattr(cc, "draw_character_from_spec", counting)
    first = cc.character_for_context(EX_NZ)
    second = cc.character_for_context(dict(EX_NZ))
    assert first == second
    assert first.parent == tmp_path
    assert first.name.isascii() and first.suffix == ".png"
    assert len(calls) == 1
    with Image.open(first) as img:
        assert img.size == (600, 1000)
    assert not list(tmp_path.glob("*.tmp"))


# --- variety --------------------------------------------------------------------------------


def test_at_least_forty_distinct_specs_from_a_context_grid():
    countries = ["Canada", "New Zealand", "Italy", "Japan", "Brazil", "Nigeria", "France"]
    settings = ["hospital waiting room", "clinic", "office", "kitchen", "stadium", "street",
                "wedding", "cafe", ""]  # fmt: skip
    keyword_sets = [[], ["bill"], ["phone"], ["tea"], ["pasta"], ["rugby"], ["coffee"],
                    ["wait"], ["bandage"], ["bag"], ["sign"], ["pastry"], ["helmet"],
                    ["curls"], ["bun"], ["ears"], ["cap"], ["flag"]]  # fmt: skip
    hashes = set()
    for country, setting, keywords, emotion in itertools.product(
        countries, settings, keyword_sets, cc.EXPRESSIONS
    ):  # fmt: skip
        ctx = {"country": country, "setting": setting, "keywords": keywords,
               "emotion": emotion}  # fmt: skip
        hashes.add(cc.spec_hash(cc.spec_from_context(ctx)))
    assert len(hashes) >= 40
