"""Subtitle plan checks: strict 1-line bursts, spring pop timing, active colours, pill alpha,
mobile safe zone and word-timestamp alignment. No network, no external fonts required."""

from __future__ import annotations

import re

import pytest
from PIL import Image

import autoshorts.longform.beats as beats
from autoshorts.longform.beats import (
    KARAOKE_SPRING_KEYFRAMES,
    chunk_narration_bursts,
    draw_karaoke_burst,
)
from autoshorts.media.ass_styler import (
    CAPTION_PRESETS,
    SHORTS_CLEAR_BOTTOM_PX,
    SHORTS_CLEAR_TOP_PX,
    SHORTS_PILL_ASS,
    SHORTS_SAFE_ZONE_MARGIN_V_DEFAULT,
    SHORTS_SAFE_ZONE_Y_DEFAULT,
    TimedWord,
    build_ass_script,
    build_spring_bounce_tags,
    chunk_words,
    hex_to_ass_color,
    rgba_to_ass_color,
)

SPRING_TAGS = r"\t(0,70,\fscx125\fscy125)\t(70,130,\fscx96\fscy96)\t(130,180,\fscx100\fscy100)"
_OVERRIDE = re.compile(r"\{[^}]*\}")


def _dialogue_texts(script: str) -> list[str]:
    """Return the plain (tag-free) text of every Dialogue line."""
    texts = []
    for line in script.splitlines():
        if line.startswith("Dialogue:"):
            raw = line.split(",", 9)[9]
            texts.append(_OVERRIDE.sub("", raw).strip())
    return texts


def _dialogue_lines(script: str) -> list[str]:
    return [line for line in script.splitlines() if line.startswith("Dialogue:")]


def _long_speech(n: int = 40) -> list[dict]:
    words = []
    for i in range(n):
        text = f"word{i}"
        if i % 7 == 6:
            text += "."
        words.append({"word": text, "start": i * 0.4, "end": i * 0.4 + 0.3})
    return words


# ---------------------------------------------------------------------------
# 1. Strict single line: 1-3 words per burst, no wrapping, no \N
# ---------------------------------------------------------------------------


def test_ass_bursts_are_one_to_three_words_and_never_break_lines() -> None:
    script = build_ass_script(_long_speech(), style_preset="active_box")
    texts = _dialogue_texts(script)
    assert texts, "expected dialogue events"
    for text in texts:
        assert 1 <= len(text.split()) <= 3, text
        assert "\\N" not in text and "\\n" not in text
    assert "\\N" not in script.split("[Events]", 1)[1]
    assert "WrapStyle: 2" in script


@pytest.mark.parametrize("preset", sorted(CAPTION_PRESETS))
def test_every_preset_keeps_bursts_to_three_words(preset: str) -> None:
    script = build_ass_script(_long_speech(20), style_preset=preset)
    assert all(len(t.split()) <= 3 for t in _dialogue_texts(script))


def test_chunk_words_splits_at_sentence_boundary() -> None:
    words = [
        TimedWord("Stop.", 0.0, 0.3),
        TimedWord("Now", 0.4, 0.6),
        TimedWord("go", 0.65, 0.9),
    ]
    chunks = chunk_words(words, words_per_burst=3, min_words=1, max_words=3)
    assert [c.text for c in chunks] == ["Stop.", "Now go"]


def test_chunk_narration_bursts_break_on_pause_and_cap_at_three() -> None:
    words = [
        {"word": "alpha", "start": 0.0, "end": 0.3},
        {"word": "beta", "start": 0.35, "end": 0.6},
        {"word": "gamma", "start": 1.5, "end": 1.8},  # 0.9 s pause before it
        {"word": "delta", "start": 1.85, "end": 2.1},
        {"word": "eps", "start": 2.15, "end": 2.4},
        {"word": "zeta", "start": 2.45, "end": 2.7},
        {"word": "eta", "start": 2.75, "end": 3.0},
    ]
    bursts = chunk_narration_bursts(words, "", 0.0, 3.0)
    assert [b.text for b in bursts] == ["alpha beta", "gamma delta eps", "zeta eta"]
    assert all(1 <= len(b.words) <= 3 for b in bursts)


# ---------------------------------------------------------------------------
# 2. Spring pop: exact keyframes 100 -> 125 (0-70 ms) -> 96 (70-130) -> 100 (130-180)
# ---------------------------------------------------------------------------


def test_spring_bounce_tags_match_spec_exactly() -> None:
    assert build_spring_bounce_tags(rot_tilt=0) == SPRING_TAGS


def test_spring_keyframes_in_beats_match_ass_tags() -> None:
    expected = [(0.0, 1.00), (0.070, 1.25), (0.130, 0.96), (0.180, 1.00)]
    assert list(KARAOKE_SPRING_KEYFRAMES) == pytest.approx(expected)
    for (t, scale), tag_t in zip(expected[1:], (70, 130, 180), strict=True):
        assert round(t * 1000) == tag_t
        assert f"\\fscx{round(scale * 100)}\\fscy{round(scale * 100)}" in SPRING_TAGS


def test_ass_active_word_carries_spring_tags() -> None:
    script = build_ass_script(
        [TimedWord("Flash", 1.35, 1.7), TimedWord("bang", 1.75, 2.1)],
        style_preset="active_box",
    )
    events = [line for line in _dialogue_lines(script) if SPRING_TAGS in line]
    assert len(events) == 2  # one spring-tagged event per spoken word


def test_beats_spring_scale_is_piecewise_linear_on_spec_points() -> None:
    scale = beats._spring_scale
    assert scale(0.0) == pytest.approx(1.00)
    assert scale(0.035) == pytest.approx(1.125)
    assert scale(0.070) == pytest.approx(1.25)
    assert scale(0.100) == pytest.approx(1.105)  # halfway down the rebound
    assert scale(0.130) == pytest.approx(0.96)
    assert scale(0.155) == pytest.approx(0.98)
    assert scale(0.180) == pytest.approx(1.00)
    assert scale(0.500) == pytest.approx(1.00)
    assert scale(-0.2) == pytest.approx(1.00)


# ---------------------------------------------------------------------------
# 3. Active word colours and the dark translucent pill
# ---------------------------------------------------------------------------


def test_supernova_cyan_and_solar_yellow_bgr_strings() -> None:
    assert hex_to_ass_color("#00F0FF") == "&H00FFF000"  # Supernova Cyan
    assert hex_to_ass_color("#FFE600") == "&H0000E6FF"  # Solar Yellow
    assert CAPTION_PRESETS["active_box"]["active_color"] == "&H0000E6FF"
    assert CAPTION_PRESETS["active_box"]["emphasis_color"] == "&H00FFF000"
    assert beats.KARAOKE_ACTIVE_COLOR == (255, 230, 0)
    assert beats.KARAOKE_EMPHASIS_COLOR == (0, 240, 255)


def test_pill_alpha_is_inverted_for_ass() -> None:
    # opacity 0.85 -> transparency byte round(0.15 * 255) = 38 = 0x26
    assert rgba_to_ass_color((11, 14, 27), 0.85) == "&H261B0E0B"
    assert SHORTS_PILL_ASS == "&H261B0E0B"
    assert rgba_to_ass_color((11, 14, 27), 1.0) == "&H001B0E0B"  # fully opaque
    assert rgba_to_ass_color((11, 14, 27), 0.0) == "&HFF1B0E0B"  # fully transparent


def test_active_box_preset_uses_pill_colour_and_box_style() -> None:
    preset = CAPTION_PRESETS["active_box"]
    assert preset["border_style"] == 3
    assert preset["outline_color"] == SHORTS_PILL_ASS
    assert preset["back_color"] == SHORTS_PILL_ASS
    script = build_ass_script([TimedWord("Wow!", 0.0, 0.5)], style_preset="active_box")
    assert (
        f"DopamineYellow,Montserrat ExtraBold,76,&H00FFFFFF,&H0000E6FF,{SHORTS_PILL_ASS}," in script
    )
    # Emphasis word ("Wow!") is Supernova Cyan in the active event.
    assert "\\c&H00FFF000&" in script


def test_beats_pill_fill_is_rgba_11_14_27_085() -> None:
    r, g, b, a = beats.KARAOKE_BOX_FILL
    assert (r, g, b) == (11, 14, 27)
    assert a == round(0.85 * 255)


# ---------------------------------------------------------------------------
# 4. Mobile safe zone: anchor Y=1250, clearance >= 450 bottom, >= 280 top
# ---------------------------------------------------------------------------


def test_shorts_anchor_and_margin_v() -> None:
    assert SHORTS_SAFE_ZONE_Y_DEFAULT == 1250
    assert SHORTS_SAFE_ZONE_MARGIN_V_DEFAULT == 670
    assert 1920 - SHORTS_SAFE_ZONE_MARGIN_V_DEFAULT == 1250
    words = [{"word": "Safe", "start": 0.0, "end": 1.0}]
    assert ",80,150,670,1" in build_ass_script(words, subtitle_position="shorts_safe")
    assert ",80,150,670,1" in build_ass_script(words, subtitle_y=1250)
    assert ",80,150,670,1" in build_ass_script(words, subtitle_position="1250")


def test_ass_bottom_clearance_is_at_least_450() -> None:
    clearance = 1920 - SHORTS_SAFE_ZONE_Y_DEFAULT
    assert clearance >= SHORTS_CLEAR_BOTTOM_PX
    # Also holds for the whole 1200-1350 zone the caption may use.
    assert 1920 - 1350 >= SHORTS_CLEAR_BOTTOM_PX


def test_beats_vertical_burst_ink_stays_inside_safe_box() -> None:
    img = Image.new("RGB", (1080, 1920), (0, 0, 0))
    burst = chunk_narration_bursts(
        [
            {"word": "Clear", "start": 0.0, "end": 0.5},
            {"word": "edges", "start": 0.5, "end": 1.0},
        ],
        "",
        0.0,
        1.0,
    )[0]
    draw_karaoke_burst(img, burst, 0.25)
    bbox = img.getbbox()
    assert bbox is not None
    x0, y0, x1, y1 = bbox
    assert y0 >= SHORTS_CLEAR_TOP_PX
    assert y1 <= 1920 - SHORTS_CLEAR_BOTTOM_PX
    assert y0 <= SHORTS_SAFE_ZONE_Y_DEFAULT <= y1
    assert x0 >= 0 and x1 <= 1080


def test_beats_horizontal_burst_is_anchored_at_y_880() -> None:
    img = Image.new("RGB", (1920, 1080), (0, 0, 0))
    burst = chunk_narration_bursts(
        [{"word": "Wide", "start": 0.0, "end": 0.5}, {"word": "screen", "start": 0.5, "end": 1.0}],
        "",
        0.0,
        1.0,
    )[0]
    draw_karaoke_burst(img, burst, 0.25)
    bbox = img.getbbox()
    assert bbox is not None
    _, y0, _, y1 = bbox
    centre = (y0 + y1) / 2
    assert abs(centre - beats.HORIZONTAL_SAFE_ZONE_Y - 44) <= 20  # line box centre near 880+44


# ---------------------------------------------------------------------------
# 5. Word timestamps: highlight starts at the word's own Whisper start
# ---------------------------------------------------------------------------


def test_ass_event_starts_at_whisper_word_start_without_offset() -> None:
    script = build_ass_script(
        [TimedWord("Flash", 1.350, 1.700), TimedWord("bang", 1.750, 2.100)],
        style_preset="active_box",
    )
    lines = _dialogue_lines(script)
    flash = [line for line in lines if line.startswith("Dialogue: 0,0:00:01.35,")]
    bang = [line for line in lines if line.startswith("Dialogue: 0,0:00:01.75,")]
    assert len(flash) == 1 and len(bang) == 1
    # The spring tags are relative to the event start, so the pop lands on the word's start.
    assert f"{{{SPRING_TAGS}" in flash[0]
    assert f"{{{SPRING_TAGS}" in bang[0]
    assert "BANG" in bang[0]


def test_beats_burst_timing_and_active_word_follow_whisper_starts() -> None:
    words = [
        {"word": "Flash", "start": 1.35, "end": 1.70},
        {"word": "bang", "start": 1.75, "end": 2.10},
    ]
    burst = chunk_narration_bursts(words, "", 1.0, 3.0)[0]
    assert burst.start == pytest.approx(1.35)
    assert burst.words[0]["start"] == pytest.approx(1.35)
    assert beats._active_word_index(burst, 1.34) == 0
    assert beats._active_word_index(burst, 1.35) == 0
    assert beats._active_word_index(burst, 1.76) == 1
    # Spring starts exactly at the word start: no pop before 1.35 s, full pop at 1.42 s.
    assert beats._spring_scale(1.35 - 1.35) == pytest.approx(1.00)
    assert beats._spring_scale(1.42 - 1.35) == pytest.approx(1.25)
    # 10 ms into the pop: 1.00 + 0.25 * 10 / 70
    assert beats._spring_scale(1.76 - 1.75) == pytest.approx(1.0 + 0.25 * 10 / 70)
