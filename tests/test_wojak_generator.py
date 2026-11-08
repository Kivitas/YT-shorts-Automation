"""Comprehensive tests for procedural Wojak generator."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image

from autoshorts.longform import wojak_generator as wg


@pytest.fixture(autouse=True)
def _tmp_wojak_cache(tmp_path, monkeypatch):
    """Isolate disk cache during tests."""
    monkeypatch.setattr(wg, "CHARACTERS_CACHE_DIR", tmp_path / "wojaks")
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Canonical Options & Constant Definitions
# ---------------------------------------------------------------------------


def test_canonical_constant_sizes_meet_requirements():
    """Verify all required parameter dimensions are covered."""
    assert len(wg.AGES) >= 4
    assert set(wg.AGES) >= {"youth", "young_adult", "middle_aged", "elderly"}

    assert len(wg.HEIGHTS) >= 3
    assert set(wg.HEIGHTS) >= {"short", "average", "tall"}

    assert len(wg.BUILDS) >= 4
    assert set(wg.BUILDS) >= {"slim", "average", "broad", "buff"}

    assert len(wg.RACES) >= 7
    assert set(wg.RACES) >= {
        "fair",
        "olive",
        "east_asian",
        "south_asian",
        "middle_eastern",
        "black",
        "latino",
    }

    assert len(wg.GENDERS) >= 4
    assert set(wg.GENDERS) >= {"male", "female", "nonna", "tradwife"}

    assert len(wg.EXPRESSIONS) >= 8
    assert set(wg.EXPRESSIONS) >= {
        "crying",
        "npc",
        "chad",
        "doomer",
        "smug",
        "shocked",
        "bandaged",
        "happy",
    }

    assert len(wg.HAIR_STYLES) >= 10
    assert set(wg.HAIR_STYLES) >= {
        "bald",
        "messy_dark_hair",
        "blonde_parted",
        "ginger_curls_beard",
        "grey_bun",
        "headscarf",
        "beret",
        "doctor_surgical_cap",
        "nurse_cap",
        "cosmetic_bandages",
        "headband",
    }

    assert len(wg.CLOTHINGS) >= 7
    assert set(wg.CLOTHINGS) >= {
        "hospital_gown",
        "doctor_coat",
        "nurse_scrubs",
        "business_suit",
        "casual_hoodie",
        "apron",
        "trench_coat",
    }

    assert len(wg.PROPS) >= 16
    assert "usa_bill" in wg.PROPS
    assert "uk_trolley_tea" in wg.PROPS
    assert "sweden_phone_queue" in wg.PROPS
    assert "india_stethoscope" in wg.PROPS
    assert "korea_bandages" in wg.PROPS
    assert "portugal_tart_beret" in wg.PROPS
    assert "italy_pasta_pot" in wg.PROPS
    assert "netherlands_paracetamol" in wg.PROPS
    assert "germany_fax_clipboard" in wg.PROPS
    assert "canada_calendar" in wg.PROPS
    assert "ireland_bed_thumbsup" in wg.PROPS
    assert "france_coffee_cigarette" in wg.PROPS
    assert "turkey_hair_transplant" in wg.PROPS


# ---------------------------------------------------------------------------
# 2. Country & Scenario Mapping Logic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("country", "setting", "scenario", "exp_expr", "exp_prop"),
    [
        ("USA", "hospital ER", "expensive bills", "crying", "usa_bill"),
        ("United States", "clinic", "debt crisis", "crying", "usa_bill"),
        ("UK", "hospital hallway", "trolley queue 18h", "npc", "uk_trolley_tea"),
        ("Britain", "A&E ward", "waiting for tea", "npc", "uk_trolley_tea"),
        ("Sweden", "clinic", "calling 1177 hold queue", "doomer", "sweden_phone_queue"),
        ("India", "hospital surgery", "chad surgeon", "chad", "india_stethoscope"),
        ("South Korea", "clinic", "cosmetic surgery", "bandaged", "korea_bandages"),
        ("Portugal", "street", "eating custard tart", "smug", "portugal_tart_beret"),
        ("Italy", "kitchen", "cooking pasta", "happy", "italy_pasta_pot"),
        ("Netherlands", "clinic", "take paracetamol and bike", "smug", "netherlands_paracetamol"),
        ("Germany", "office", "din fax forms in triplicate", "npc", "germany_fax_clipboard"),
        ("Canada", "hospital", "waiting for mri in 2029", "doomer", "canada_calendar"),
        ("Ireland", "hospital bed", "cheerful thumbs up", "happy", "ireland_bed_thumbsup"),
        ("France", "cafe", "coffee and strike", "smug", "france_coffee_cigarette"),
        ("Turkey", "clinic", "hair transplant clinic", "bandaged", "turkey_hair_transplant"),
    ],
)
def test_spec_from_country_and_context_archetypes(country, setting, scenario, exp_expr, exp_prop):
    spec = wg.spec_from_country_and_context(country, setting, scenario)
    assert spec.expression == exp_expr
    assert spec.prop == exp_prop


def test_explicit_overrides_take_priority():
    """Explicit parameters should override country archetype defaults."""
    spec = wg.spec_from_country_and_context(
        country="USA",
        setting="hospital",
        scenario="bill",
        age="elderly",
        gender="nonna",
        race="black",
        build="buff",
        height="tall",
        expression="happy",
        clothing="apron",
        prop="italy_pasta_pot",
        hair="headscarf",
    )
    assert spec.age == "elderly"
    assert spec.gender == "nonna"
    assert spec.race == "black"
    assert spec.build == "buff"
    assert spec.height == "tall"
    assert spec.expression == "happy"
    assert spec.clothing == "apron"
    assert spec.prop == "italy_pasta_pot"
    assert spec.hair == "headscarf"


def test_resilience_to_malformed_and_unknown_inputs():
    """Test that weird casing, whitespace, and invalid values gracefully normalize."""
    spec = wg.spec_from_country_and_context(
        country="   u.S.A.!!!  ",
        setting="HOSPITAL WARD",
        scenario="DEBT",
        age="very_old_grandpa",
        gender="trad_wife",
        race="caucasian_pale",
        build="jacked_muscular",
        height="huge_tall",
        expression="screaming_panic",
    )
    assert spec.age == "elderly"
    assert spec.gender == "tradwife"
    assert spec.race == "fair"
    assert spec.build == "buff"
    assert spec.height == "tall"
    assert spec.expression == "shocked"


# ---------------------------------------------------------------------------
# 3. Procedural Drawing Tests
# ---------------------------------------------------------------------------


def test_draw_wojak_produces_valid_rgba_image():
    spec = wg.WojakSpec(
        country="USA",
        expression="crying",
        clothing="hospital_gown",
        prop="usa_bill",
    )
    img = wg.draw_wojak(spec)
    assert isinstance(img, Image.Image)
    assert img.size == (800, 1200)
    assert img.mode == "RGBA"

    # Verify image has non-trivial transparent and opaque regions
    alpha_channel = img.split()[3]
    bbox = alpha_channel.getbbox()
    assert bbox is not None
    assert bbox[0] < 300  # reaches left side
    assert bbox[2] > 500  # reaches right side
    assert bbox[3] > 1000  # reaches lower torso/props


def test_crying_expression_has_cyan_tears():
    """Verify that crying spec draws cyan tear pixels."""
    spec = wg.WojakSpec(expression="crying", prop="none")
    img = wg.draw_wojak(spec)

    # Count pixels matching bright cyan tear color
    pixels = img.load()
    cyan_count = 0
    for y in range(350, 650):
        for x in range(300, 500):
            r, g, b, a = pixels[x, y]
            # Cyan tear RGB is around (56, 189, 248)
            if b > 200 and g > 150 and r < 100:
                cyan_count += 1
    assert cyan_count > 50, f"Expected tear pixels, found {cyan_count}"


def test_all_expressions_render_cleanly():
    """Ensure every canonical expression renders without exceptions."""
    for expr in wg.EXPRESSIONS:
        spec = wg.WojakSpec(expression=expr)
        img = wg.draw_wojak(spec)
        assert img.size == (800, 1200)


def test_all_clothings_render_cleanly():
    """Ensure every canonical clothing renders without exceptions."""
    for cloth in wg.CLOTHINGS:
        spec = wg.WojakSpec(clothing=cloth)
        img = wg.draw_wojak(spec)
        assert img.size == (800, 1200)


def test_all_props_render_cleanly():
    """Ensure every canonical prop renders without exceptions."""
    for prop in wg.PROPS:
        spec = wg.WojakSpec(prop=prop)
        img = wg.draw_wojak(spec)
        assert img.size == (800, 1200)


def test_all_hair_styles_render_cleanly():
    """Ensure every canonical hair style renders without exceptions."""
    for hair in wg.HAIR_STYLES:
        spec = wg.WojakSpec(hair=hair)
        img = wg.draw_wojak(spec)
        assert img.size == (800, 1200)


def test_all_skin_tones_render_cleanly():
    """Ensure all racial skin tones render properly."""
    for race in wg.RACES:
        spec = wg.WojakSpec(race=race)
        img = wg.draw_wojak(spec)
        assert img.size == (800, 1200)


def test_height_and_build_variations():
    """Ensure short, tall, slim, buff variations render cleanly."""
    for height in wg.HEIGHTS:
        for build in wg.BUILDS:
            spec = wg.WojakSpec(height=height, build=build)
            img = wg.draw_wojak(spec)
            assert img.size == (800, 1200)


# ---------------------------------------------------------------------------
# 4. Performance & Caching Tests
# ---------------------------------------------------------------------------


def test_rendering_performance_sub_100ms():
    """Verify procedural generation is blazing fast (sub-100ms per character)."""
    countries = ["USA", "UK", "India", "South Korea", "Italy", "Netherlands", "Germany"]
    times = []

    for c in countries:
        spec = wg.spec_from_country_and_context(c, "hospital", "satirical parody")
        t0 = time.perf_counter()
        img = wg.draw_wojak(spec)
        dt = (time.perf_counter() - t0) * 1000
        times.append(dt)
        assert img.size == (800, 1200)

    avg_time = sum(times) / len(times)
    assert avg_time < 50.0, f"Average rendering time {avg_time:.2f}ms is above 50ms!"


def test_spec_hash_determinism():
    """Same spec should generate identical hashes and identical pixel bytes."""
    spec_a = wg.spec_from_country_and_context("USA", "hospital", "bill")
    spec_b = wg.spec_from_country_and_context("USA", "hospital", "bill")

    assert wg.spec_hash(spec_a) == wg.spec_hash(spec_b)
    img_a = wg.draw_wojak(spec_a)
    img_b = wg.draw_wojak(spec_b)
    assert img_a.tobytes() == img_b.tobytes()


def test_wojak_for_context_caching(tmp_path, monkeypatch):
    """Verify disk caching properly persists and retrieves rendered PNG."""
    monkeypatch.setattr(wg, "CHARACTERS_CACHE_DIR", tmp_path)

    path1 = wg.wojak_for_context("India", "clinic", "chad doctor")
    assert path1.exists()
    assert path1.suffix == ".png"
    mtime1 = path1.stat().st_mtime_ns

    # Second call should retrieve from cache without recreating
    path2 = wg.wojak_for_context("India", "clinic", "chad doctor")
    assert path1 == path2
    assert path2.stat().st_mtime_ns == mtime1
