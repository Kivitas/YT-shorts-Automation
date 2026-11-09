"""Comprehensive Unit Tests for Wojak Explainer Archetype Catalog & Audio Pipeline.

Validates 100% compliance with `assets/research/wojak_explainer_production_bible.md`:
1. All 10 canonical topics defined in Section 6 exist in ARCHETYPE_TOPICS.
2. Each topic provides 6 to 10 curated archetype segments.
3. Every punchline caption is strictly under 120 characters for fast comedic reading.
4. Demographic diversity: explicit gender and race on every segment.
5. Robust resolve_archetype_topic with canonical keys, aliases, titles, and fuzzy matching.
6. Audio scoring compliance (BPM ranges, -14.0 LUFS target, -1.0 dBFS true peak).
7. Procedural audio synthesis for all 4 soundscape genres (Comedic, Phonk/Chad, Post-punk/Doomer, Lofi/Bloomer).
8. Pipeline integration with resolve_topic_scenes.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from autoshorts.longform.wojak_archetypes import (
    ARCHETYPE_TOPICS,
    ArchetypeSegment,
    get_archetype_segments,
    list_archetype_topics,
    resolve_archetype_topic,
)
from autoshorts.longform.wojak_audio import (
    BLOOMER_LOFI_BPM_DEFAULT,
    BLOOMER_LOFI_BPM_RANGE,
    COMEDIC_BPM_DEFAULT,
    COMEDIC_BPM_RANGE,
    DOOMER_POSTPUNK_BPM_DEFAULT,
    DOOMER_POSTPUNK_BPM_RANGE,
    PHONK_CHAD_BPM_DEFAULT,
    PHONK_CHAD_BPM_RANGE,
    SAMPLE_RATE,
    TARGET_LUFS,
    TRUE_PEAK_CEILING,
    assemble_wojak_audio,
    generate_procedural_wojak_bgm,
    synth_bloomer_lofi_bgm,
    synth_comedic_bgm,
    synth_doomer_postpunk_bgm,
    synth_phonk_chad_bgm,
)
from autoshorts.longform.wojak_pipeline import resolve_topic_scenes

CANONICAL_TOPICS = [
    "introverts",
    "gym",
    "generations",
    "coders",
    "students",
    "drivers",
    "music",
    "dating",
    "workers",
    "gamers",
]

EXPECTED_TITLES = {
    "introverts": "Types of Introverts Be Like",
    "gym": "Types of Gym Members Be Like",
    "generations": "Every Generation In A Nutshell",
    "coders": "Types of Developers Be Like",
    "students": "Types of Students During Exams Be Like",
    "drivers": "Types of Drivers On The Road Be Like",
    "music": "Types of Music Listeners Be Like",
    "dating": "Types of Dating Profiles Be Like",
    "workers": "Types of Office Workers Be Like",
    "gamers": "Types of Gamers Be Like",
}

VALID_ARCHETYPES = {
    "doomer",
    "bloomer",
    "chad",
    "tradwife",
    "soyjak",
    "zoomer",
    "boomer",
    "npc",
    "coomer",
}

VALID_EXPRESSIONS = {
    "tired",
    "happy",
    "smug",
    "shocked",
    "crying",
    "neutral",
    "annoyed",
    "deadpan",
}

VALID_GENDERS = {"male", "female", "tradwife", "nonna"}
VALID_RACES = {
    "fair",
    "olive",
    "east_asian",
    "south_asian",
    "middle_eastern",
    "black",
    "latino",
}


# ============================================================================
# 1. Canonical Topics Completeness Tests
# ============================================================================


def test_all_10_canonical_topics_exist():
    """Verify all 10 topics defined in Production Bible Section 6 exist."""
    assert len(ARCHETYPE_TOPICS) == 10
    for topic in CANONICAL_TOPICS:
        assert topic in ARCHETYPE_TOPICS, f"Missing canonical topic: {topic}"
        assert ARCHETYPE_TOPICS[topic]["title"] == EXPECTED_TITLES[topic]


def test_list_archetype_topics():
    """Verify list_archetype_topics returns all 10 canonical keys."""
    topics = list_archetype_topics()
    assert set(topics) == set(CANONICAL_TOPICS)


# ============================================================================
# 2. Segment Counts & Data Integrity
# ============================================================================


@pytest.mark.parametrize("topic_key", CANONICAL_TOPICS)
def test_topic_segments_count(topic_key: str):
    """Verify each topic has between 6 and 10 curated archetype segments."""
    topic_data = ARCHETYPE_TOPICS[topic_key]
    segments = topic_data["segments"]
    assert 6 <= len(segments) <= 10, (
        f"Topic {topic_key} has {len(segments)} segments; expected 6 to 10."
    )


@pytest.mark.parametrize("topic_key", CANONICAL_TOPICS)
def test_topic_setting_defined(topic_key: str):
    """Verify each topic has an atmospheric setting defined."""
    topic_data = ARCHETYPE_TOPICS[topic_key]
    assert "setting" in topic_data
    assert isinstance(topic_data["setting"], str)
    assert len(topic_data["setting"].strip()) > 0


# ============================================================================
# 3. Punchline Caption 120-Character Constraint & Formatting
# ============================================================================


def test_every_punchline_strictly_under_120_chars():
    """Verify every segment's caption across all topics is < 120 characters."""
    violations = []
    for topic_key, topic_data in ARCHETYPE_TOPICS.items():
        for seg in topic_data["segments"]:
            cap_len = len(seg.caption)
            if cap_len >= 120:
                violations.append(
                    f"[{topic_key}] {seg.archetype_title}: {cap_len} chars -> '{seg.caption}'"
                )
    assert not violations, "Punchlines exceeding 120 characters:\n" + "\n".join(violations)


def test_captions_have_substantial_content():
    """Verify captions are not trivial or empty (>= 20 chars)."""
    for topic_key, topic_data in ARCHETYPE_TOPICS.items():
        for seg in topic_data["segments"]:
            assert len(seg.caption) >= 20, (
                f"[{topic_key}] {seg.archetype_title} caption too short: '{seg.caption}'"
            )


# ============================================================================
# 4. Demographic Diversity & Spec Validity
# ============================================================================


def test_segment_demographic_and_character_specs():
    """Verify gender, race, character_archetype, and expression on every segment."""
    for topic_key, topic_data in ARCHETYPE_TOPICS.items():
        for seg in topic_data["segments"]:
            assert isinstance(seg, ArchetypeSegment)
            assert seg.character_archetype in VALID_ARCHETYPES, (
                f"[{topic_key}] Invalid archetype: {seg.character_archetype}"
            )
            assert seg.expression in VALID_EXPRESSIONS, (
                f"[{topic_key}] Invalid expression: {seg.expression}"
            )
            assert seg.gender in VALID_GENDERS, f"[{topic_key}] Invalid gender: {seg.gender}"
            assert seg.race in VALID_RACES, f"[{topic_key}] Invalid race: {seg.race}"
            assert seg.prop and len(seg.prop.strip()) > 0, (
                f"[{topic_key}] Missing prop on {seg.archetype_title}"
            )
            assert seg.setting and len(seg.setting.strip()) > 0, (
                f"[{topic_key}] Missing setting on {seg.archetype_title}"
            )


def test_overall_demographic_diversity():
    """Ensure catalog features multiple genders and multiple ethnicities across segments."""
    all_genders = set()
    all_races = set()
    all_archetypes = set()

    for topic_data in ARCHETYPE_TOPICS.values():
        for seg in topic_data["segments"]:
            all_genders.add(seg.gender)
            all_races.add(seg.race)
            all_archetypes.add(seg.character_archetype)

    # Must feature at least male, female, tradwife
    assert {"male", "female"}.issubset(all_genders)
    # Must feature diverse ethnic representations
    assert len(all_races) >= 5
    # Must cover majority of canonical Wojak pantheon
    assert len(all_archetypes) >= 7


# ============================================================================
# 5. Topic Resolution (`resolve_archetype_topic`)
# ============================================================================


@pytest.mark.parametrize("canonical_key", CANONICAL_TOPICS)
def test_resolve_by_canonical_key(canonical_key: str):
    """Direct canonical key lookup succeeds."""
    res = resolve_archetype_topic(canonical_key)
    assert res is not None
    assert res["title"] == EXPECTED_TITLES[canonical_key]


@pytest.mark.parametrize(
    "query,expected_key",
    [
        ("types_of_introverts", "introverts"),
        ("types_of_gym_goers", "gym"),
        ("every_generation", "generations"),
        ("types_of_coders", "coders"),
        ("types_of_students", "students"),
        ("types_of_drivers", "drivers"),
        ("types_of_music_listeners", "music"),
        ("types_of_dating_profiles", "dating"),
        ("types_of_workers", "workers"),
        ("types_of_gamers", "gamers"),
    ],
)
def test_resolve_by_production_bible_format(query: str, expected_key: str):
    """Production Bible snake_case name resolution succeeds."""
    res = resolve_archetype_topic(query)
    assert res is not None
    assert res["title"] == EXPECTED_TITLES[expected_key]


@pytest.mark.parametrize("canonical_key", CANONICAL_TOPICS)
def test_resolve_by_exact_title(canonical_key: str):
    """Exact title lookup succeeds."""
    title = EXPECTED_TITLES[canonical_key]
    res = resolve_archetype_topic(title)
    assert res is not None
    assert res["title"] == title


@pytest.mark.parametrize(
    "query,expected_key",
    [
        ("developers", "coders"),
        ("programmers", "coders"),
        ("software engineers", "coders"),
        ("fitness", "gym"),
        ("workout", "gym"),
        ("gym members", "gym"),
        ("social introvert", "introverts"),
        ("shy", "introverts"),
        ("boomer", "generations"),
        ("gen z", "generations"),
        ("exam", "students"),
        ("studying", "students"),
        ("traffic", "drivers"),
        ("highway", "drivers"),
        ("driving", "drivers"),
        ("spotify", "music"),
        ("playlist", "music"),
        ("tinder", "dating"),
        ("dating app", "dating"),
        ("office workers", "workers"),
        ("corporate", "workers"),
        ("video games", "gamers"),
        ("steam", "gamers"),
        ("esports", "gamers"),
    ],
)
def test_resolve_by_keyword_aliases(query: str, expected_key: str):
    """Common synonyms and subculture keywords resolve correctly."""
    res = resolve_archetype_topic(query)
    assert res is not None
    assert res["title"] == EXPECTED_TITLES[expected_key]


def test_resolve_case_and_punctuation_insensitivity():
    """Queries with extra spaces, punctuation, or mixed casing resolve properly."""
    queries = [
        ("  TYPES OF GAMERS!!  ", "gamers"),
        ("Every-Generation-In-A-Nutshell", "generations"),
        ("types of drivers on the road be like???", "drivers"),
        ("CODERS", "coders"),
    ]
    for q, exp_key in queries:
        res = resolve_archetype_topic(q)
        assert res is not None
        assert res["title"] == EXPECTED_TITLES[exp_key]


def test_resolve_fuzzy_matching():
    """Minor typos resolve via fuzzy matching."""
    res1 = resolve_archetype_topic("introverst")
    assert res1 is not None and res1["title"] == EXPECTED_TITLES["introverts"]

    res2 = resolve_archetype_topic("programer")
    assert res2 is not None and res2["title"] == EXPECTED_TITLES["coders"]

    res3 = resolve_archetype_topic("studnets")
    assert res3 is not None and res3["title"] == EXPECTED_TITLES["students"]


def test_resolve_unknown_topic():
    """Completely unknown random query returns None."""
    assert resolve_archetype_topic("quantum astrophysics quantum entanglement") is None
    assert resolve_archetype_topic("") is None


def test_get_archetype_segments_helper():
    """Verify get_archetype_segments returns segment list or raises KeyError."""
    segs = get_archetype_segments("coders")
    assert len(segs) >= 6
    assert all(isinstance(s, ArchetypeSegment) for s in segs)

    with pytest.raises(KeyError):
        get_archetype_segments("nonexistent_unknown_topic_12345")


# ============================================================================
# 6. Audio Scoring & Production Bible Alignment Tests
# ============================================================================


def test_audio_bpm_and_mastering_constants():
    """Verify BPM ranges and mastering specs match Production Bible Section 5."""
    # Comedic Baseline: 124–130 BPM
    assert COMEDIC_BPM_RANGE == (124.0, 130.0)
    assert 124.0 <= COMEDIC_BPM_DEFAULT <= 130.0

    # Phonk / Chad: 135–145 BPM
    assert PHONK_CHAD_BPM_RANGE == (135.0, 145.0)
    assert 135.0 <= PHONK_CHAD_BPM_DEFAULT <= 145.0

    # Post-punk / Doomer: 110–120 BPM
    assert DOOMER_POSTPUNK_BPM_RANGE == (110.0, 120.0)
    assert 110.0 <= DOOMER_POSTPUNK_BPM_DEFAULT <= 120.0

    # Bloomer / Lofi: 80–85 BPM
    assert BLOOMER_LOFI_BPM_RANGE == (80.0, 85.0)
    assert 80.0 <= BLOOMER_LOFI_BPM_DEFAULT <= 85.0

    # Mastering: -14.0 LUFS integrated loudness, -1.0 dBFS true peak
    assert TARGET_LUFS == -14.0
    assert TRUE_PEAK_CEILING == -1.0


@pytest.mark.parametrize(
    "synth_func,expected_bpm",
    [
        (synth_comedic_bgm, COMEDIC_BPM_DEFAULT),
        (synth_phonk_chad_bgm, PHONK_CHAD_BPM_DEFAULT),
        (synth_doomer_postpunk_bgm, DOOMER_POSTPUNK_BPM_DEFAULT),
        (synth_bloomer_lofi_bgm, BLOOMER_LOFI_BPM_DEFAULT),
    ],
)
def test_procedural_genre_synthesizers(synth_func, expected_bpm):
    """Verify all 4 genre synthesizers generate clean, non-silent audio without NaN/Inf."""
    duration = 2.0
    audio = synth_func(duration, sr=SAMPLE_RATE, bpm=expected_bpm)
    expected_samples = int(duration * SAMPLE_RATE)

    assert len(audio) == expected_samples
    assert not np.isnan(audio).any()
    assert not np.isinf(audio).any()
    assert float(np.max(np.abs(audio))) > 0.05
    assert float(np.max(np.abs(audio))) <= 1.0


@pytest.mark.parametrize(
    "mood",
    ["comedic", "phonk", "chad", "doomer", "postpunk", "bloomer", "lofi"],
)
def test_generate_procedural_wojak_bgm_moods(mood: str):
    """Verify generate_procedural_wojak_bgm dispatches correctly to all moods."""
    duration = 1.5
    audio = generate_procedural_wojak_bgm(duration, sr=SAMPLE_RATE, mood=mood)
    assert len(audio) == int(duration * SAMPLE_RATE)
    assert not np.isnan(audio).any()
    assert float(np.max(np.abs(audio))) > 0.05


def test_assemble_wojak_audio_with_mood(tmp_path: Path):
    """Verify assemble_wojak_audio successfully assembles and masters with specified mood."""
    out_wav = tmp_path / "mastered_chad.wav"
    res = assemble_wojak_audio(
        total_duration_sec=2.0,
        scene_transition_timestamps=[0.0, 1.0],
        output_wav_path=out_wav,
        mood="chad",
    )
    assert res.is_file()
    assert res.stat().st_size > 1000


# ============================================================================
# 7. Pipeline Integration Tests
# ============================================================================


@pytest.mark.parametrize("canonical_key", CANONICAL_TOPICS)
def test_resolve_topic_scenes_integration(canonical_key: str):
    """Verify resolve_topic_scenes in wojak_pipeline handles all 10 topics seamlessly."""
    title, scenes = resolve_topic_scenes(canonical_key)
    assert title == EXPECTED_TITLES[canonical_key]
    assert 6 <= len(scenes) <= 10

    for scene in scenes:
        assert scene.country  # Archetype title
        assert len(scene.caption) < 120
        assert scene.gender in VALID_GENDERS
        assert scene.race in VALID_RACES
