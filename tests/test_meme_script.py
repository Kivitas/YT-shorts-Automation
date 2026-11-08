"""Unit tests for the Country Meme and Types Of... Meme Generation Engine."""

from pathlib import Path

from autoshorts.meme.script import (
    ANIMATION_TYPES,
    BACKGROUNDS,
    EXPRESSIONS,
    MUSIC_MOODS,
    POSES,
    SFX_CUES,
    build_description,
    build_meme_script,
    match_audio_and_sfx,
)
from autoshorts.meme.topics import (
    COUNTRY_MEME_TOPICS,
    HASHTAG_LIBRARY,
    TYPES_OF_TOPICS,
    VALID_CATEGORIES,
    generate_topic_idea,
    normalize_category,
    resolve_topic_and_category,
    select_hashtags,
)
from autoshorts.meme.types import (
    MemeConfig,
    MemeResult,
    MemeSegment,
)


def test_hashtag_library_contents():
    """Verify official 25-hashtag library."""
    assert len(HASHTAG_LIBRARY) == 25
    expected_tags = [
        "#CountryMemes",
        "#CountryComparison",
        "#EveryCountry",
        "#CountryBeLike",
        "#FunnyMemes",
        "#RelatableMemes",
        "#AnimeMemes",
        "#AnimeEdit",
        "#MemeCompilation",
        "#WorldMemes",
        "#CultureMemes",
        "#POVMemes",
        "#FunnyVideos",
        "#ComedyVideos",
        "#GeographyMemes",
        "#Countries",
        "#AroundTheWorld",
        "#DailyMemes",
        "#InternetMemes",
        "#ViralMemes",
        "#FunnyMoments",
        "#YouTubeComedy",
        "#Relatable",
        "#MemeCulture",
        "#Shorts",
    ]
    for tag in expected_tags:
        assert tag in HASHTAG_LIBRARY


def test_select_hashtags_smart_filtering():
    """Verify hashtag selection selects 3-5 tags tailored to topic and mode."""
    country_tags = select_hashtags(
        mode="country_meme", category="countries", topic="Winter", count=4
    )
    assert 3 <= len(country_tags) <= 5
    assert "#CountryMemes" in country_tags or "#EveryCountry" in country_tags

    types_tags = select_hashtags(
        mode="types_of", category="relatable", topic="Types of Gamers", count=4
    )
    assert 3 <= len(types_tags) <= 5
    assert any(t in types_tags for t in ("#RelatableMemes", "#POVMemes", "#Relatable"))

    anime_tags = select_hashtags(mode="types_of", category="anime", topic="Anime Heroes", count=4)
    assert "#AnimeMemes" in anime_tags


def test_normalize_category():
    """Verify category normalization logic."""
    assert normalize_category("Countries") == "countries"
    assert normalize_category("School Life") == "school"
    assert normalize_category("Food & Cooking") == "food"
    assert normalize_category("Dark Comedy") == "dark_comedy"
    assert normalize_category("Anime Memes") == "anime"
    assert normalize_category("Something Unknown") == "custom"
    assert normalize_category(None) == "countries"


def test_resolve_topic_and_category():
    """Verify topic resolution and category fallback."""
    topic, cat = resolve_topic_and_category(mode="country_meme")
    assert topic in COUNTRY_MEME_TOPICS
    assert cat == "countries"

    topic, cat = resolve_topic_and_category(mode="types_of")
    assert topic in TYPES_OF_TOPICS
    assert cat == "relatable"

    topic, cat = resolve_topic_and_category(topic="Every Country's Food Be Like")
    assert topic == "Every Country's Food Be Like"
    assert cat == "countries"


def test_generate_topic_idea():
    """Verify dynamic creative topic ideation."""
    topic, cat = generate_topic_idea(category="school")
    assert cat == "school"
    assert len(topic) > 5

    topic, cat = generate_topic_idea(category="countries")
    assert cat == "countries"
    assert "Country" in topic

    topic, cat = generate_topic_idea(mode="types_of")
    assert "Types of" in topic or cat in VALID_CATEGORIES


def test_meme_segment_dataclass_and_serialization():
    """Verify MemeSegment initialization and serialization."""
    segment = MemeSegment(
        id="japan_1",
        name="Japan",
        heading="JAPAN 🇯🇵",
        narration="In Japan, the bullet train arrived twelve seconds early.",
        scenario="Conductor apologizes for being early.",
        character_description="Anime salaryman with spectacles and glowing watch.",
        pose="bowing_polite",
        expression="shocked",
        background_theme="train_station",
        animation_type="zoom_in",
        music_mood="playful",
        sfx_cue="cartoon_impact",
        subtitle_text="In Japan, the bullet train arrived twelve seconds early.",
        speech_bubble_text="Gomen nasai!",
    )
    d = segment.to_dict()
    restored = MemeSegment.from_dict(d)
    assert restored.id == segment.id
    assert restored.name == segment.name
    assert restored.speech_bubble_text == "Gomen nasai!"


def test_meme_script_generation_country_meme():
    """Verify building country meme script."""
    config = MemeConfig(
        mode="country_meme",
        category="countries",
        topic="Every Country's Public Transport Be Like",
        segment_count=6,
    )
    script = build_meme_script(config, use_llm=False)

    assert script.mode == "country_meme"
    assert len(script.segments) == 6
    assert script.topic == "Every Country's Public Transport Be Like"
    assert script.estimated_duration > 30.0
    assert 3 <= len(script.hashtags) <= 5

    for seg in script.segments:
        assert seg.pose in POSES
        assert seg.expression in EXPRESSIONS
        assert seg.background_theme in BACKGROUNDS
        assert seg.animation_type in ANIMATION_TYPES
        assert seg.music_mood in MUSIC_MOODS
        assert seg.sfx_cue in SFX_CUES
        assert len(seg.narration) > 0


def test_meme_script_generation_types_of():
    """Verify building Types Of... meme script."""
    config = MemeConfig(
        mode="types_of",
        category="school",
        topic="Types of Students in Every Classroom",
        segment_count=5,
    )
    script = build_meme_script(config, use_llm=False)

    assert script.mode == "types_of"
    assert len(script.segments) == 5
    assert "Types of Students" in script.title
    assert (
        script.hook.startswith("There are strictly")
        or "students" in script.hook.lower()
        or len(script.hook) > 0
    )
    assert script.ending != ""


def test_audio_mood_matching_rules():
    """Verify comedic scene mood matching logic."""
    # Punchline cut
    mood, sfx, anim = match_audio_and_sfx("final scene", "shocked", "facepalm", is_punchline=True)
    assert mood == "punchline_cut"
    assert sfx == "rimshot"

    # Unexpected reveal
    mood, sfx, anim = match_audio_and_sfx(
        "a secret plot twist appeared", "smug", "smug_crossed_arms", is_reveal=True
    )
    assert mood == "unexpected_reveal"
    assert sfx == "bass_drop"

    # Overconfident
    mood, sfx, anim = match_audio_and_sfx(
        "chad sigma billionaire genius", "smug", "dramatic_heroic"
    )
    assert mood == "epic_orchestral"

    # Disaster
    mood, sfx, anim = match_audio_and_sfx(
        "massive explosion panic run", "panicked", "screaming_run"
    )
    assert mood == "suspense"

    # Awkward
    mood, sfx, anim = match_audio_and_sfx(
        "awkward silent cringe moment", "deadpan", "hiding_corner"
    )
    assert mood == "quirky"
    assert sfx == "record_scratch"


def test_build_description_formatter():
    """Verify YouTube description formatter."""
    config = MemeConfig(mode="country_meme", segment_count=5)
    script = build_meme_script(config, use_llm=False)
    desc = build_description(script)

    assert script.title in desc
    assert script.hook in desc
    assert "Timestamps / Featured:" in desc
    for seg in script.segments:
        assert seg.heading in desc
    for tag in script.hashtags:
        assert tag in desc


def test_meme_result_structure():
    """Verify MemeResult container properties."""
    config = MemeConfig(mode="types_of", segment_count=5)
    script = build_meme_script(config, use_llm=False)
    res = MemeResult(
        script=script,
        video_path=Path("output/meme.mp4"),
        thumbnail_path=Path("output/thumb.png"),
        title=script.title,
        description=build_description(script),
        hashtags=script.hashtags,
        duration=52.5,
        aspect_ratio="9:16",
        qc_passed=True,
        youtube_url=None,
    )
    assert res.aspect_ratio == "9:16"
    assert res.qc_passed is True
    assert res.youtube_url is None
