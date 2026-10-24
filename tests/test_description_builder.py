"""Unit tests for DescriptionBuilder, attribution injection, and YouTube compliance limits.

Tests template rendering, title <= 100 chars, description <= 5000 bytes,
mandatory disclaimer presence, hashtag normalization, and < > sanitization.
Zero network access.
"""

from __future__ import annotations

from autoshorts.uploader.description_builder import (
    DescriptionBuilder,
    build_short_description,
    build_short_title,
    format_timestamp_display,
    normalize_hashtags,
    sanitize_youtube_text,
)


def test_sanitize_youtube_text():
    """Verify angle brackets are stripped to prevent YouTube API 400 rejection."""
    dirty_title = "<Epic> Podcasting <Session> #1"
    clean_title = sanitize_youtube_text(dirty_title)
    assert "<" not in clean_title
    assert ">" not in clean_title
    assert clean_title == "Epic Podcasting Session #1"

    assert sanitize_youtube_text("") == ""
    assert sanitize_youtube_text("<script>alert('test')</script>") == "scriptalert('test')/script"


def test_timestamp_formatting():
    """Verify timestamp formatting into MM:SS and HH:MM:SS."""
    assert format_timestamp_display(0) == "00:00"
    assert format_timestamp_display(45) == "00:45"
    assert format_timestamp_display(65.5) == "01:05"
    assert format_timestamp_display(3665) == "01:01:05"
    assert format_timestamp_display("invalid") == "invalid"


def test_normalize_hashtags():
    """Verify hashtag normalization and guaranteed presence of #Shorts."""
    # List input without #Shorts
    tags1 = normalize_hashtags(["gaming", "podcasts"])
    assert "#Shorts" in tags1
    assert "#gaming" in tags1
    assert "#podcasts" in tags1

    # String input with comma
    tags2 = normalize_hashtags("gaming, viral, #shorts")
    assert tags2.lower().count("#shorts") == 1
    assert "#gaming" in tags2
    assert "#viral" in tags2

    # None or empty input
    tags3 = normalize_hashtags(None)
    assert tags3 == "#Shorts"


def test_build_title_standard():
    """Verify title generation within normal bounds."""
    builder = DescriptionBuilder()
    title = builder.build_title(
        hook="Why Sleep Matters",
        channel="Dr. Huberman",
    )
    assert "Why Sleep Matters" in title
    assert "Dr. Huberman" in title
    assert "#Shorts" in title
    assert len(title) <= 100


def test_build_title_length_limit():
    """Verify long titles are strictly capped to <= 100 characters and preserve #Shorts."""
    builder = DescriptionBuilder()
    huge_hook = (
        "This is an extraordinarily long hook statement designed specifically to test the hard upper bound of title lengths"
        * 2
    )
    channel = "The Long Channel Name Podcast"

    title = builder.build_title(
        hook=huge_hook,
        channel=channel,
    )
    assert len(title) <= 100
    assert "#Shorts" in title
    assert "<" not in title and ">" not in title


def test_build_title_sanitizes_angle_brackets():
    """Verify titles containing angle brackets are sanitized."""
    builder = DescriptionBuilder()
    title = builder.build_title(
        hook="<Shocking> Scientific Discovery!",
        channel="<Science Hub>",
    )
    assert "<" not in title
    assert ">" not in title
    assert "Shocking Scientific Discovery!" in title
    assert "Science Hub" in title


def test_build_description_standard():
    """Verify description renders all template variables and includes required disclaimer."""
    builder = DescriptionBuilder()
    desc = builder.build_description(
        title="Huberman Lab Episode 42",
        channel="Andrew Huberman",
        channel_url="https://www.youtube.com/@hubermanlab",
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        hook="The best morning routine revealed in 30 seconds.",
        start=65.0,
        end=110.0,
        hashtags=["#Health", "#Science"],
    )

    # Check variables
    assert "The best morning routine revealed in 30 seconds." in desc
    assert "Huberman Lab Episode 42" in desc
    assert "Andrew Huberman" in desc
    assert "https://www.youtube.com/@hubermanlab" in desc
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in desc
    assert "#Shorts" in desc
    assert "#Health" in desc
    assert "#Science" in desc

    # Check disclaimer elements per requirements
    assert "This video is a clip from" in desc
    assert "All rights belong to" in desc
    assert "Original video:" in desc


def test_build_description_byte_length_limit():
    """Verify descriptions exceeding 5000 bytes are truncated without exceeding byte limit."""
    builder = DescriptionBuilder()
    huge_hook = (
        "Paragraph of filler text repeating for several thousand words to trigger 5000 byte cap. "
        * 300
    )

    desc = builder.build_description(
        title="Source Video Title",
        channel="Test Channel",
        channel_url="https://youtube.com/@channel",
        video_url="https://youtube.com/watch?v=12345678901",
        hook=huge_hook,
        hashtags="#Test",
    )

    encoded = desc.encode("utf-8")
    assert len(encoded) <= 5000
    assert "All rights belong to" in desc
    assert "#Shorts" in desc


def test_custom_template_missing_disclaimer_injected():
    """Verify that if a custom template lacks the disclaimer, it is automatically appended."""
    builder = DescriptionBuilder()
    custom_tpl = "Custom Hook: {hook}\n\nCheck out: {video_url}"
    desc = builder.build_description(
        title="Special Video",
        channel="MyChannel",
        channel_url="https://youtube.com/@mychan",
        video_url="https://youtube.com/watch?v=12345678901",
        hook="Amazing insight",
        template=custom_tpl,
    )

    assert "Amazing insight" in desc
    assert "This video is a clip from" in desc
    assert "All rights belong to MyChannel" in desc
    assert "Original video: https://youtube.com/watch?v=12345678901" in desc
    assert "#Shorts" in desc


def test_convenience_functions():
    """Verify module-level convenience helpers build_short_title and build_short_description."""
    title = build_short_title("Quick Hook", "Channel A")
    assert "Quick Hook" in title
    assert "Channel A" in title

    desc = build_short_description(
        title="My Title",
        channel="My Channel",
        channel_url="https://youtube.com/@chan",
        video_url="https://youtube.com/watch?v=abc",
        hook="Short hook",
    )
    assert "My Title" in desc
    assert "This video is a clip from" in desc


def test_viral_hashtags_are_unique_capped_and_lead_with_shorts():
    from autoshorts.uploader.description_builder import MAX_HASHTAGS, build_viral_hashtags

    tags = build_viral_hashtags(
        "Why are you freaked out by my robot hand? Robot Robot hand Shorts", "Robot hand"
    )
    assert tags[0] == "#Shorts"
    assert len(tags) <= MAX_HASHTAGS
    lowered = [t.lower() for t in tags]
    assert len(lowered) == len(set(lowered))
    assert "#Viral" in tags and "#FYP" in tags
