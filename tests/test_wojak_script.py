"""Tests for Wojak long-form cultural parody scripts & topic catalogs (SapX style)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from autoshorts.longform import character_composer
from autoshorts.longform.wojak_script import (
    VALID_ATTIRES,
    VALID_EXPRESSIONS,
    VALID_GENDERS,
    VALID_PROPS,
    VALID_SKIN_TONES,
    WojakLongformScript,
    WojakSegment,
    build_wojak_script,
    procedural_wojak_script,
)
from autoshorts.longform.wojak_topics import (
    ASPECT_RATIO,
    CANONICAL_TOPICS,
    COUNTRY_CATALOG,
    DISCLAIMER_DURATION_SECONDS,
    LANDSCAPE_HEIGHT,
    LANDSCAPE_WIDTH,
    MANDATORY_DISCLAIMER_BODY,
    MANDATORY_DISCLAIMER_HEADING,
    MAX_COUNTRIES_PER_VIDEO,
    MAX_COUNTRY_DURATION,
    MAX_TOTAL_DURATION,
    MIN_COUNTRIES_PER_VIDEO,
    MIN_COUNTRY_DURATION,
    MIN_TOTAL_DURATION,
    get_country_meta,
    list_available_countries,
    list_canonical_topics,
    normalize_wojak_topic,
)


# ==============================================================================
# 1. Video Structure & Pacing Constraints Tests
# ==============================================================================

def test_mandatory_disclaimer_card():
    script = build_wojak_script("hospital")
    assert script.disclaimer_heading == "⚠️ PARODY DISCLAIMER"
    assert script.disclaimer_heading == MANDATORY_DISCLAIMER_HEADING
    assert script.disclaimer_text == MANDATORY_DISCLAIMER_BODY
    assert "satirical comedy parody" in script.disclaimer_text
    assert "Regional stereotypes" in script.disclaimer_text
    assert script.disclaimer_duration == 3.5
    assert script.disclaimer_duration == DISCLAIMER_DURATION_SECONDS


def test_video_format_and_duration_bounds():
    script = build_wojak_script("hospital")
    assert script.format == "16:9"
    assert script.format == ASPECT_RATIO
    assert script.width == 1920
    assert script.width == LANDSCAPE_WIDTH
    assert script.height == 1080
    assert script.height == LANDSCAPE_HEIGHT

    # Duration must be 2.5 to 3.0 minutes (150.0 to 180.0 seconds)
    assert 150.0 <= script.total_duration <= 180.0
    assert MIN_TOTAL_DURATION <= script.total_duration <= MAX_TOTAL_DURATION
    # Number of countries: 25 to 35
    assert 25 <= len(script.segments) <= 35
    assert MIN_COUNTRIES_PER_VIDEO <= len(script.segments) <= MAX_COUNTRIES_PER_VIDEO

    # Pacing: exactly 4.0 to 4.5 seconds per country
    for seg in script.segments:
        assert 4.0 <= seg.duration <= 4.5
        assert MIN_COUNTRY_DURATION <= seg.duration <= MAX_COUNTRY_DURATION


# ==============================================================================
# 2. Canonical Topics & Curated Joke Catalogs
# ==============================================================================

@pytest.mark.parametrize("topic_key", CANONICAL_TOPICS)
def test_all_canonical_topics_generate_valid_scripts(topic_key):
    script = build_wojak_script(topic_key)
    assert script.topic == topic_key
    assert len(script.segments) >= 25
    assert 150.0 <= script.total_duration <= 180.0
    assert script.validate_pacing(strict=True) == []


def test_sapx_viral_hospital_punchlines():
    script = build_wojak_script("hospital")
    seg_by_heading = {s.heading: s for s in script.segments}

    # Verify key countries modeled after viral SapX style
    assert "USA" in seg_by_heading
    assert "$38 for one aspirin" in seg_by_heading["USA"].joke_text
    assert "5-star" in seg_by_heading["USA"].joke_text

    assert "UK" in seg_by_heading
    assert "14 hours" in seg_by_heading["UK"].joke_text
    assert "lovely nurses" in seg_by_heading["UK"].joke_text

    assert "Sweden" in seg_by_heading
    assert "112 in the phone queue" in seg_by_heading["Sweden"].joke_text
    assert "Fika" in seg_by_heading["Sweden"].joke_text

    assert "India" in seg_by_heading
    assert "World-class surgery for 10x less" in seg_by_heading["India"].joke_text

    assert "South Korea" in seg_by_heading
    assert "jawline" in seg_by_heading["South Korea"].joke_text

    assert "Netherlands" in seg_by_heading
    assert "paracetamol" in seg_by_heading["Netherlands"].joke_text

    assert "Germany" in seg_by_heading
    assert "42 compliance forms" in seg_by_heading["Germany"].joke_text

    assert "Italy" in seg_by_heading
    assert "lasagna" in seg_by_heading["Italy"].joke_text
    assert "ICU" in seg_by_heading["Italy"].joke_text

    assert "Canada" in seg_by_heading
    assert "2029" in seg_by_heading["Canada"].joke_text

    assert "France" in seg_by_heading
    assert "strike" in seg_by_heading["France"].joke_text

    assert "Turkey" in seg_by_heading
    assert "hair follicles" in seg_by_heading["Turkey"].joke_text

    assert "Japan" in seg_by_heading
    assert "bows 18 times" in seg_by_heading["Japan"].joke_text

    assert "Australia" in seg_by_heading
    assert "huntsman spider" in seg_by_heading["Australia"].joke_text

    assert "Switzerland" in seg_by_heading
    assert "Rolex" in seg_by_heading["Switzerland"].joke_text


# ==============================================================================
# 3. Procedural Generation for Arbitrary Topics
# ==============================================================================

def test_procedural_generation_for_custom_topics():
    custom_topics = [
        "Every Country's Coffee Culture Be Like",
        "gym_culture",
        "weddings",
        "office_meetings",
    ]
    for topic in custom_topics:
        script = build_wojak_script(topic)
        assert len(script.segments) >= 25
        assert 150.0 <= script.total_duration <= 180.0
        assert script.validate_pacing(strict=True) == []
        for s in script.segments:
            assert s.joke_text
            assert s.setting
            assert s.country
            assert s.country_code
            assert s.character_context["expression"] in VALID_EXPRESSIONS
            assert s.character_context["prop"] in VALID_PROPS


# ==============================================================================
# 4. Structured Data Models & Interoperability
# ==============================================================================

def test_wojak_segment_model():
    seg = WojakSegment(
        country="Germany",
        country_code="DE",
        heading="Germany",
        joke_text="Please sign 42 compliance forms in triplicate.",
        setting="administrative admissions desk",
        character_context={
            "age": 42,
            "gender": "male",
            "race": "fair",
            "expression": "neutral",
            "prop": "clipboard",
            "attire": "jacket",
        },
        duration=4.5,
    )
    assert seg.country == "Germany"
    assert seg.country_code == "DE"
    assert seg.heading == "Germany"
    assert seg.duration == 4.5

    # Test serialization round-trip
    d = seg.to_dict()
    assert d["country"] == "Germany"
    assert d["country_code"] == "DE"
    assert d["character_context"]["prop"] == "clipboard"

    restored = WojakSegment.from_dict(d)
    assert restored.country == seg.country
    assert restored.country_code == seg.country_code
    assert restored.character_context == seg.character_context
    assert restored.duration == seg.duration


def test_character_composer_integration():
    """Verify that WojakSegment can directly feed into character_composer."""
    script = build_wojak_script("hospital")
    for s in script.segments[:5]:
        ctx = s.to_character_composer_context()
        assert "country" in ctx
        assert "setting" in ctx
        assert "emotion" in ctx
        assert "prop" in ctx
        assert "attire" in ctx

        # Call character_composer.spec_from_context to verify zero errors
        spec = character_composer.spec_from_context(ctx)
        assert spec.head in character_composer.HEADS
        assert spec.torso in character_composer.TORSOS
        assert spec.expression in character_composer.EXPRESSIONS
        assert 0 <= spec.skin_tone < len(character_composer.SKIN_TONES)


def test_wojak_longform_script_json_roundtrip():
    script = build_wojak_script("public_transport")
    json_str = script.to_json()
    assert isinstance(json_str, str)
    assert "Every Country's Public Transport Be Like" in json_str

    restored = WojakLongformScript.from_json(json_str)
    assert restored.topic == script.topic
    assert restored.title == script.title
    assert restored.total_duration == script.total_duration
    assert len(restored.segments) == len(script.segments)
    assert restored.disclaimer_heading == MANDATORY_DISCLAIMER_HEADING
    assert restored.disclaimer_text == MANDATORY_DISCLAIMER_BODY
    assert restored.validate_pacing(strict=True) == []


def test_summary_output():
    script = build_wojak_script("hospital")
    summary = script.summary()
    assert "Every Country's Hospital Be Like" in summary
    assert "Total Duration:" in summary
    assert "Country Segments: 33" in summary
    assert "USA" in summary


# ==============================================================================
# 5. Validation & Pacing Enforcement
# ==============================================================================

def test_validation_fails_on_out_of_bounds_duration():
    script = build_wojak_script("hospital")
    # Artificially alter segments duration to break bounds
    for s in script.segments:
        s.duration = 2.0  # too short
    script.recalculate_duration()

    with pytest.raises(ValueError, match="Total video duration"):
        script.validate_pacing(strict=True)


def test_validation_fails_on_altered_disclaimer():
    script = build_wojak_script("hospital")
    script.disclaimer_text = "Some random text"
    with pytest.raises(ValueError, match="Mandatory disclaimer"):
        script.validate_pacing(strict=True)


def test_validation_fails_on_too_few_countries():
    script = build_wojak_script("hospital")
    script.segments = script.segments[:10]  # Only 10 countries
    script.recalculate_duration()
    with pytest.raises(ValueError, match="Segment count"):
        script.validate_pacing(strict=True)


# ==============================================================================
# 6. LLM Generation & Resilience Fallback Tests
# ==============================================================================

class _MockLLMProvider:
    def __init__(self, response_text: str):
        self.response_text = response_text
        self.calls = 0

    def complete(self, prompt: str, **kwargs: Any) -> str:
        self.calls += 1
        return self.response_text


def test_llm_generation_success(monkeypatch):
    sample_segments = [
        {
            "country": "United States",
            "country_code": "US",
            "heading": "USA",
            "joke_text": "VIP hospital suite costs more than buying a house.",
            "setting": "luxury lobby",
            "character_context": {
                "age": 35,
                "gender": "male",
                "race": "fair",
                "expression": "shocked",
                "prop": "bill",
                "attire": "jacket",
            },
            "duration": 4.5,
        }
    ]
    # Replicate 33 segments for pacing
    countries = list_available_countries()[:33]
    full_segments = []
    for c in countries:
        cm = get_country_meta(c)
        full_segments.append(
            {
                "country": cm.name,
                "country_code": cm.country_code,
                "heading": cm.heading,
                "joke_text": f"Joke for {cm.heading}",
                "setting": "city hospital",
                "character_context": {
                    "age": 30,
                    "gender": "female",
                    "race": cm.default_race,
                    "expression": "smug",
                    "prop": "clipboard",
                    "attire": "lab_coat",
                },
                "duration": 4.5,
            }
        )

    mock_json = {
        "title": "Every Country's Hospital Be Like (LLM Edition)",
        "disclaimer_heading": MANDATORY_DISCLAIMER_HEADING,
        "disclaimer_text": MANDATORY_DISCLAIMER_BODY,
        "disclaimer_duration": 3.5,
        "hashtags": ["#wojak", "#memes", "#healthcare"],
        "segments": full_segments,
    }
    raw_response = "```json\n" + json.dumps(mock_json) + "\n```"

    mock_llm = _MockLLMProvider(raw_response)
    monkeypatch.setattr(
        "autoshorts.core.providers.get_provider",
        lambda name, settings: mock_llm,
    )

    script = build_wojak_script("hospital", provider="api", config=SimpleNamespace())
    assert script.provider == "llm"
    assert script.title == "Every Country's Hospital Be Like (LLM Edition)"
    assert len(script.segments) == 33
    assert 150.0 <= script.total_duration <= 180.0
    assert mock_llm.calls == 1


def test_llm_failure_falls_back_to_procedural(monkeypatch):
    class _FailingLLMProvider:
        def complete(self, prompt: str, **kwargs: Any) -> str:
            raise RuntimeError("API quota exceeded or network failure")

    monkeypatch.setattr(
        "autoshorts.core.providers.get_provider",
        lambda name, settings: _FailingLLMProvider(),
    )

    # Must seamlessly return procedural fallback without throwing
    script = build_wojak_script("hospital", provider="api", config=SimpleNamespace())
    assert script.provider == "procedural"
    assert len(script.segments) == 33
    assert "USA" in [s.heading for s in script.segments]
    assert 150.0 <= script.total_duration <= 180.0
