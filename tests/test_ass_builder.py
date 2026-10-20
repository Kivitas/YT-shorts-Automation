"""Tests for the High-Dopamine ASS subtitle generator and word chunker."""

from pathlib import Path

from autoshorts.core.config import SubtitlesConfig
from autoshorts.core.models import JumpCut
from autoshorts.media.ass_styler import (
    TimedWord,
    build_ass_script,
    chunk_words,
    format_ass_timestamp,
    format_emojis_for_ass,
    hex_to_ass_color,
    is_emphasis_word,
    remap_words_for_jump_cuts,
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


def test_remap_words_for_jump_cuts_multiple():
    """Verify that remap_words_for_jump_cuts removes words inside cuts and shifts subsequent timestamps."""
    words = [
        TimedWord(word="First", start=0.0, end=1.0),
        TimedWord(word="point", start=1.2, end=1.8),
        # Cut 1: 2.0 to 3.0 (dur 1.0)
        TimedWord(word="cut_out_1", start=2.2, end=2.8),
        TimedWord(word="second", start=3.2, end=3.8),
        # Cut 2: 4.0 to 5.5 (dur 1.5)
        TimedWord(word="cut_out_2", start=4.5, end=5.2),
        TimedWord(word="third", start=6.0, end=6.8),
    ]

    cuts = [
        JumpCut(cut_start=2.0, cut_end=3.0, reason="silence 1"),
        JumpCut(cut_start=4.0, cut_end=5.5, reason="silence 2"),
    ]

    remapped = remap_words_for_jump_cuts(words, cuts)

    # Words falling inside cuts should be removed
    words_text = [w.word for w in remapped]
    assert words_text == ["First", "point", "second", "third"]

    # First and point are before Cut 1: unchanged
    assert remapped[0].start == 0.0
    assert remapped[0].end == 1.0
    assert remapped[1].start == 1.2
    assert remapped[1].end == 1.8

    # second is after Cut 1 (1.0s excised): 3.2 - 1.0 = 2.2, 3.8 - 1.0 = 2.8
    assert remapped[2].start == 2.2
    assert remapped[2].end == 2.8

    # third is after Cut 1 and Cut 2 (1.0 + 1.5 = 2.5s excised): 6.0 - 2.5 = 3.5, 6.8 - 2.5 = 4.3
    assert remapped[3].start == 3.5
    assert remapped[3].end == 4.3


def test_build_ass_script_with_jump_cuts():
    """Verify that build_ass_script integrates remap_words_for_jump_cuts to keep subtitles synchronized."""
    words = [
        {"word": "Before", "start": 0.0, "end": 1.0},
        {"word": "SilenceWord", "start": 2.2, "end": 2.8},
        {"word": "After", "start": 3.5, "end": 4.5},
    ]
    cuts = [JumpCut(cut_start=2.0, cut_end=3.0, reason="dead air")]

    script = build_ass_script(words, jump_cuts=cuts)

    # SilenceWord must not be in dialogue events
    assert "SILENCEWORD" not in script
    assert "BEFORE" in script
    assert "AFTER" in script

    # "After" was originally at 3.5s, shifted earlier by 1.0s cut to 2.5s -> 0:00:02.50
    assert "0:00:02.50" in script


def test_caption_style_presets():
    """Verify subtitle presets: neon, minimal, and pill."""
    words = [{"word": "Hello", "start": 0.0, "end": 1.0}]

    # 1. Neon preset: Yellow #FFE600 active (&H0000E6FF), Cyan #00F0FF emphasis (&H00FFF000), heavy outline
    neon_script = build_ass_script(words, style_preset="neon")
    assert "&H0000E6FF" in neon_script
    assert ",1,8,4,2," in neon_script  # BorderStyle=1, Outline=8, Shadow=4

    # 2. Minimal preset: Emerald #00FF66 active (&H0066FF00), subtle shadow=1
    min_script = build_ass_script(words, style_preset="minimal")
    assert "&H0066FF00" in min_script
    assert ",1,2,1,2," in min_script  # BorderStyle=1, Outline=2, Shadow=1

    # 3. Pill preset: BorderStyle=3 with opaque backing box (&HA0000000)
    pill_script = build_ass_script(words, style_preset="pill")
    assert ",3,6,0,2," in pill_script  # BorderStyle=3, Outline=6, Shadow=0
    assert "&HA0000000" in pill_script  # Backing box color


def test_hex_to_ass_color():
    """Verify conversion of hex colors (#RRGGBB, #AARRGGBB, #RGB) to ASS (&HAABBGGRR / &H00BBGGRR)."""
    assert hex_to_ass_color("#FFE600") == "&H0000E6FF"
    assert hex_to_ass_color("#00FF55") == "&H0055FF00"
    assert hex_to_ass_color("#FFB800") == "&H0000B8FF"
    assert hex_to_ass_color("#00F0FF") == "&H00FFF000"
    assert hex_to_ass_color("&H0000E6FF") == "&H0000E6FF"
    assert hex_to_ass_color("&h0066ff00") == "&H0066FF00"
    assert hex_to_ass_color("#D4AF37") == "&H0037AFD4"
    assert hex_to_ass_color("#C4912B") == "&H002B91C4"
    assert hex_to_ass_color("#FF007F") == "&H007F00FF"
    assert hex_to_ass_color("#FFF") == "&H00FFFFFF"


def test_all_seven_caption_presets():
    """Verify all 7 studio caption presets generate expected fonts, sizes, and styling."""
    words = [{"word": "Viral", "start": 0.0, "end": 0.5}]

    # 1. neon
    s_neon = build_ass_script(words, style_preset="neon")
    assert "Montserrat ExtraBold,76" in s_neon
    assert "&H0000E6FF" in s_neon
    assert ",1,8,4,2," in s_neon

    # 2. minimal
    s_min = build_ass_script(words, style_preset="minimal")
    assert "Inter SemiBold,64" in s_min
    assert "&H0066FF00" in s_min
    assert ",1,2,1,2," in s_min

    # 3. pill
    s_pill = build_ass_script(words, style_preset="pill")
    assert "Montserrat Bold,68" in s_pill
    assert "&HA0000000" in s_pill
    assert ",3,6,0,2," in s_pill

    # 4. hormozi
    s_hormozi = build_ass_script(words, style_preset="hormozi")
    assert "Impact,86" in s_hormozi
    assert "&H0055FF00" in s_hormozi
    assert ",1,12,5,2," in s_hormozi
    assert r"\fscx130\fscy130" in s_hormozi

    # 5. mrbeast
    s_mrbeast = build_ass_script(words, style_preset="mrbeast")
    assert "Montserrat ExtraBold,82" in s_mrbeast
    assert "&H0000B8FF" in s_mrbeast
    assert ",1,10,4,2," in s_mrbeast
    assert r"\frz-3" in s_mrbeast
    assert r"\frz3" in s_mrbeast

    # 6. cyberpunk
    s_cyberpunk = build_ass_script(words, style_preset="cyberpunk")
    assert "Orbitron,74" in s_cyberpunk
    assert "&H00FFF000" in s_cyberpunk  # primary Cyan
    assert "&H007F00FF" in s_cyberpunk  # active Magenta
    assert "&H00330011" in s_cyberpunk  # Dark violet outline
    assert ",1,8,6,2," in s_cyberpunk

    # 7. cinematic
    s_cinematic = build_ass_script(words, style_preset="cinematic")
    assert "Georgia,58" in s_cinematic
    assert "&H00F0F5F5" in s_cinematic  # Off-white
    assert "&H0037AFD4" in s_cinematic  # Gold
    assert "&H00141E28" in s_cinematic  # Umber outline
    assert ",1,2,3,2," in s_cinematic


def test_typography_text_transforms():
    """Verify uppercase, title, and none text transforms."""
    words = [{"word": "heLLo wORld", "start": 0.0, "end": 1.0}]

    s_upper = build_ass_script(words, text_transform="uppercase")
    assert "HELLO WORLD" in s_upper

    s_title = build_ass_script(words, text_transform="title")
    assert "Hello World" in s_title

    s_none = build_ass_script(words, text_transform="none")
    assert "heLLo wORld" in s_none


def test_subtitle_positions():
    """Verify center, bottom_safe, and top_safe subtitle vertical margins."""
    words = [{"word": "SafeZone", "start": 0.0, "end": 1.0}]

    s_bottom = build_ass_script(words, subtitle_position="bottom_safe")
    assert ",80,150,420,1" in s_bottom

    s_top = build_ass_script(words, subtitle_position="top_safe")
    assert ",80,150,1450,1" in s_top

    s_center = build_ass_script(words, subtitle_position="center")
    assert ",80,150,780,1" in s_center


def test_hook_banner_styles():
    """Verify hook banner styles (neon_box, pill, minimal, none)."""
    words = [{"word": "Word", "start": 0.0, "end": 1.0}]

    # none: skip HookCard event
    s_none = build_ass_script(words, hook_title="Epic Hook", hook_banner_style="none")
    assert "Dialogue: 1," not in s_none

    # pill: BorderStyle=3
    s_pill = build_ass_script(words, hook_title="Epic Hook", hook_banner_style="pill")
    assert "Dialogue: 1," in s_pill
    assert "Style: HookCard,Montserrat Black,64," in s_pill
    assert ",3,8,0,8,80,150,220,1" in s_pill

    # minimal: BorderStyle=1, Outline=2
    s_min = build_ass_script(words, hook_title="Epic Hook", hook_banner_style="minimal")
    assert "Dialogue: 1," in s_min
    assert ",1,2,1,8,80,150,220,1" in s_min

    # neon_box: default BorderStyle=1, Outline=6
    s_neon = build_ass_script(words, hook_title="Epic Hook", hook_banner_style="neon_box")
    assert "Dialogue: 1," in s_neon
    assert ",1,6,3,8,80,150,220,1" in s_neon


def test_enable_emojis_toggle():
    """Verify enabling and disabling emoji rendering."""
    words = [{"word": "Fire 🔥 Alert ⚡", "start": 0.0, "end": 1.0}]

    # When True: wraps with Segoe UI Emoji
    s_on = build_ass_script(words, enable_emojis=True)
    assert r"{\fnSegoe UI Emoji}🔥" in s_on
    assert r"{\fnSegoe UI Emoji}⚡" in s_on

    # When False: strips emojis cleanly
    s_off = build_ass_script(words, enable_emojis=False)
    assert r"{\fnSegoe UI Emoji}" not in s_off
    assert "🔥" not in s_off
    assert "⚡" not in s_off
    assert "FIRE ALERT" in s_off


def test_custom_color_overrides_hex():
    """Verify custom hex color overrides are converted to ASS format."""
    words = [{"word": "Custom", "start": 0.0, "end": 1.0}]

    s = build_ass_script(
        words,
        primary_color="#E0E0E0",  # BGR &H00E0E0E0
        active_color="#FF3300",  # BGR &H000033FF
        emphasis_color="#00FFCC",  # BGR &H00CCFF00
        outline_color="#201005",  # BGR &H00051020
    )
    assert "&H00E0E0E0" in s
    assert "&H000033FF" in s
    assert "&H00051020" in s


def test_active_word_micro_tilt_and_scale_animation():
    """Verify active word pop animation includes micro-tilt bounce \\frz-2 to \\frz0 and \\fscx118."""
    words = [
        TimedWord(word="Regular", start=0.0, end=0.8),
        TimedWord(word="INSANE", start=0.85, end=1.5),  # High-emphasis keyword
    ]

    script = build_ass_script(words, style_preset="neon")

    # Regular word pops to 118% with \frz-2 to \frz0 micro-tilt
    assert r"\frz-2\t(0,80,\fscx118\fscy118)\t(80,160,\fscx100\fscy100)\t(80,160,\frz0)" in script

    # High-emphasis word pops to 125% with cyan emphasis color & micro-tilt
    assert (
        r"\frz-2\t(0,80,\fscx125\fscy125)\t(80,160,\fscx100\fscy100)\t(80,160,\frz0)\c&H00FFF000&"
        in script
    )


def test_emoji_font_wrapping_windows():
    """Verify that Unicode emojis are wrapped with inline Segoe UI Emoji font override."""
    # Direct function test
    wrapped = format_emojis_for_ass("FIRE 🔥 ALERT", base_font="Montserrat ExtraBold")
    assert wrapped == "FIRE {\\fnSegoe UI Emoji}🔥{\\fnMontserrat ExtraBold} ALERT"

    # Multi-emoji test
    multi = format_emojis_for_ass("🚀 WOW 😱", base_font="Montserrat Black")
    assert "{\\fnSegoe UI Emoji}🚀{\\fnMontserrat Black}" in multi
    assert "{\\fnSegoe UI Emoji}😱{\\fnMontserrat Black}" in multi

    # Integration test inside build_ass_script
    words = [{"word": "Shocking 🔥", "start": 0.0, "end": 1.0}]
    script = build_ass_script(words, hook_title="Warning ⚡")
    assert r"{\fnSegoe UI Emoji}🔥{\fnMontserrat ExtraBold}" in script
    assert r"{\fnSegoe UI Emoji}⚡{\fnMontserrat Black}" in script
