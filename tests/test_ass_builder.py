"""Tests for the High-Dopamine ASS subtitle generator and word chunker."""

from pathlib import Path

from autoshorts.core.config import SubtitlesConfig
from autoshorts.media.ass_styler import (
    TimedWord,
    build_ass_script,
    chunk_words,
    format_ass_timestamp,
    is_emphasis_word,
    write_ass_file,
)


def test_format_ass_timestamp():
    """Verify ASS timestamp formatting to H:MM:SS.cs."""
    assert format_ass_timestamp(0.0) == "0:00:00.00"
    assert format_ass_timestamp(1.25) == "0:00:01.25"
    assert format_ass_timestamp(65.5) == "0:01:05.50"
    assert format_ass_timestamp(3661.08) == "1:01:01.08"
    assert format_ass_timestamp(-5.0) == "0:00:00.00"


def test_chunk_words_empty():
    """Verify chunk_words handles empty inputs gracefully."""
    assert chunk_words([]) == []


def test_chunk_words_burst_sizing():
    """Verify words are grouped into bursts of 2-4 words (default 3)."""
    words = [
        {"word": "This", "start": 0.0, "end": 0.3},
        {"word": "is", "start": 0.35, "end": 0.5},
        {"word": "a", "start": 0.55, "end": 0.7},
        {"word": "test", "start": 0.75, "end": 1.0},
        {"word": "of", "start": 1.05, "end": 1.2},
        {"word": "chunking", "start": 1.25, "end": 1.6},
        {"word": "algorithm", "start": 1.65, "end": 2.1},
    ]

    chunks = chunk_words(words, words_per_burst=3, min_words=2, max_words=4)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert 1 <= len(chunk.words) <= 4
        assert chunk.start == chunk.words[0].start
        assert chunk.end == chunk.words[-1].end


def test_chunk_words_punctuation_and_pause_snapping():
    """Verify chunks snap at sentence boundaries and pauses."""
    words = [
        {"word": "Stop.", "start": 0.0, "end": 0.4},
        {"word": "Look", "start": 0.5, "end": 0.8},
        {"word": "at", "start": 0.85, "end": 1.0},
        {"word": "this", "start": 1.05, "end": 1.3},
        # Big pause of 1.2s
        {"word": "secret", "start": 2.5, "end": 2.9},
        {"word": "now!", "start": 2.95, "end": 3.3},
    ]

    chunks = chunk_words(words, words_per_burst=3, min_words=1, max_words=4)
    assert len(chunks) == 3
    assert chunks[0].text == "Stop."
    assert chunks[1].text == "Look at this"
    assert chunks[2].text == "secret now!"


def test_is_emphasis_word():
    """Verify detection of high-emphasis viral keywords and emojis."""
    assert is_emphasis_word("SECRET") is True
    assert is_emphasis_word("insane") is True
    assert is_emphasis_word("warning!") is True
    assert is_emphasis_word("🔥") is True
    assert is_emphasis_word("the") is False
    assert is_emphasis_word("table") is False


def test_build_ass_script_structure():
    """Verify ASS script header, styles, safe-zone positioning, and hook card."""
    words = [
        TimedWord(word="The", start=1.0, end=1.3),
        TimedWord(word="secret", start=1.35, end=1.7),
        TimedWord(word="revealed", start=1.75, end=2.2),
    ]

    config = SubtitlesConfig(
        font_name="Montserrat ExtraBold",
        font_size=76,
        safe_zone_margin_v=780,
    )

    script = build_ass_script(
        words=words,
        hook_title="How It Works",
        hook_duration_sec=2.8,
        config=config,
        onscreen_credit="youtube.com/@Example",
        total_duration_sec=10.0,
    )

    # Header assertions
    assert "[Script Info]" in script
    assert "PlayResX: 1080" in script
    assert "PlayResY: 1920" in script

    # Style assertions
    assert "Style: DopamineYellow,Montserrat ExtraBold,76" in script
    assert "780" in script  # Safe-zone marginV
    assert "Style: HookCard,Montserrat Black,64" in script
    assert "Style: Watermark" in script

    # Events assertions
    assert "[Events]" in script
    assert "HOW IT WORKS" in script  # Hook title in upper
    assert r"{\fad(200,300)}" in script

    # Active word pop animation tags
    assert r"\t(0,80,\fscx" in script
    assert r"\t(80,160,\fscx100\fscy100)" in script

    # Watermark in final seconds
    assert "Source: youtube.com/@Example" in script


def test_write_ass_file_atomic(tmp_path: Path):
    """Verify ASS file is written atomically to disk."""
    out_file = tmp_path / "subtitles.ass"
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.6, "end": 1.0},
    ]

    written = write_ass_file(out_file, words, hook_title="Hello World")
    assert written.is_file()
    content = written.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "HELLO" in content


def test_offset_words_for_hook():
    """Verify subtitle words before hook_in are discarded and remaining words shifted."""
    from autoshorts.media.ass_styler import offset_words_for_hook

    words = [
        TimedWord(word="Intro", start=0.0, end=1.5),
        TimedWord(word="chatter", start=1.6, end=2.4),
        TimedWord(word="Secret", start=2.5, end=3.2),
        TimedWord(word="revealed", start=3.3, end=4.0),
    ]

    # hook_in_sec = 2.5 trims out the first 2.5 seconds
    offset = offset_words_for_hook(words, hook_in_sec=2.5)
    assert len(offset) == 2
    assert offset[0].word == "Secret"
    assert offset[0].start == 0.0
    assert offset[0].end == 0.7
    assert offset[1].word == "revealed"
    assert offset[1].start == 0.8
    assert offset[1].end == 1.5


def test_build_ass_script_with_hook_in_offset():
    """Verify that build_ass_script offsets word timestamps by hook_in_sec."""
    words = [
        {"word": "Discarded", "start": 0.0, "end": 1.0},
        {"word": "Action", "start": 2.0, "end": 3.0},
    ]
    script = build_ass_script(words, hook_in_sec=1.5)
    assert "DISCARDED" not in script
    assert "ACTION" in script
    # Event should start at 0.5s (2.0 - 1.5)
    assert "0:00:00.50" in script


def test_shorts_mobile_safe_zone_margins():
    """Verify subtitle styles enforce mobile Shorts safe zones (clear of bottom 350px and right 150px)."""
    words = [{"word": "Test", "start": 0.0, "end": 1.0}]

    # Even if configured with dangerously low margin_v (e.g. 100), safe zone clamps to >= 350
    config_low = SubtitlesConfig(safe_zone_margin_v=100)
    script_low = build_ass_script(words, config=config_low)
    # DopamineYellow style format: MarginL=80, MarginR=150, MarginV=350
    assert ",80,150,350,1" in script_low

    # When configured near mid-seam or split boundary (e.g. 420 or 780), margin is respected
    config_mid = SubtitlesConfig(safe_zone_margin_v=420)
    script_mid = build_ass_script(words, config=config_mid)
    assert ",80,150,420,1" in script_mid
