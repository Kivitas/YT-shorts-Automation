"""Tests for the in-scene kinetic typography helpers in autoshorts.longform.kinetic_text."""

from __future__ import annotations

import pytest
from PIL import Image, ImageChops

from autoshorts.longform.kinetic_text import (
    _chunk_lines,
    _font,
    draw_inner_voice,
    draw_kinetic_line,
    draw_narration,
    draw_number_pop,
    draw_shatter_text,
    wrap_lines,
)

W, H = 1920, 1080


def _canvas() -> Image.Image:
    return Image.new("RGB", (W, H), (20, 20, 30))


def _diff_bbox(before: Image.Image, after: Image.Image) -> tuple[int, int, int, int] | None:
    return ImageChops.difference(before, after).getbbox()


def _pixels(img: Image.Image, box: tuple[int, int, int, int]) -> list[tuple[int, ...]]:
    region = img.crop(box)
    flat = getattr(region, "get_flattened_data", None)
    return list(flat() if flat else region.getdata())


def _red_pixels(img: Image.Image, box: tuple[int, int, int, int]) -> int:
    return sum(1 for px in _pixels(img, box) if px[0] > px[1] + 40)


# --- wrap_lines -------------------------------------------------------------


def test_wrap_lines_respects_max_width() -> None:
    font = _font(64)
    text = "Every single rep you log is a small proof that the plan works today"
    max_w = 520
    lines = wrap_lines(text, max_w, font)
    assert len(lines) > 1
    for line in lines:
        if " " in line:
            assert font.getlength(line) <= max_w


def test_wrap_lines_keeps_every_word_in_order() -> None:
    font = _font(64)
    text = "one two three four five six seven eight nine ten"
    lines = wrap_lines(text, 300, font)
    assert " ".join(lines).split() == text.split()


def test_wrap_lines_long_word_gets_own_line() -> None:
    font = _font(64)
    lines = wrap_lines("tiny Supercalifragilisticexpialidocious tiny", 200, font)
    assert "Supercalifragilisticexpialidocious" in lines


def test_wrap_lines_empty_text() -> None:
    assert wrap_lines("   ", 500, _font(64)) == []


# --- draw_narration ---------------------------------------------------------


@pytest.mark.parametrize("t", [0.0, 1.0, 2.0, 3.0, 3.9, 4.0, 5.0])
def test_narration_runs_at_several_t(t: float) -> None:
    img = _canvas()
    draw_narration(img, "Every rep counts toward the goal", t, 1.0, 4.0, highlight="goal")
    assert img.size == (W, H)


def test_narration_changes_pixels_inside_band() -> None:
    img = _canvas()
    before = img.copy()
    draw_narration(img, "Every rep counts", 2.5, 1.0, 4.0, y=240, fade=0.5)
    bbox = _diff_bbox(before, img)
    assert bbox is not None
    assert bbox[1] >= 230 and bbox[3] <= 240 + 120


def test_narration_outside_window_is_noop() -> None:
    img = _canvas()
    before = img.copy()
    draw_narration(img, "Every rep counts", 0.5, 1.0, 4.0)
    draw_narration(img, "Every rep counts", 4.5, 1.0, 4.0)
    assert _diff_bbox(before, img) is None


def test_narration_reveals_more_words_over_time() -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta"
    early = _canvas()
    late = _canvas()
    draw_narration(early, text, 1.5, 1.0, 5.0, fade=0.0)
    draw_narration(late, text, 4.9, 1.0, 5.0, fade=0.0)
    early_ink = _diff_bbox(_canvas(), early)
    late_ink = _diff_bbox(_canvas(), late)
    assert early_ink is not None and late_ink is not None
    assert (late_ink[2] - late_ink[0]) > (early_ink[2] - early_ink[0])


def test_narration_highlight_uses_highlight_colour() -> None:
    img = _canvas()
    draw_narration(
        img,
        "Count every single rep",
        4.9,
        1.0,
        5.0,
        fade=0.0,
        highlight="rep!",
        highlight_color=(255, 200, 60),
    )
    bbox = _diff_bbox(_canvas(), img)
    assert bbox is not None
    gold = sum(1 for px in _pixels(img, bbox) if px[0] > 200 and px[1] > 150 and px[2] < 120)
    assert gold > 0


# --- draw_inner_voice -------------------------------------------------------


@pytest.mark.parametrize("t", [0.0, 0.3, 1.0, 1.7, 2.0, 2.5])
def test_inner_voice_runs_at_several_t(t: float) -> None:
    img = _canvas()
    draw_inner_voice(img, "maybe I can't do this", (300, 500), t, 0.5, 2.0)
    assert img.size == (W, H)


def test_inner_voice_changes_pixels_with_red_glow() -> None:
    img = _canvas()
    before = img.copy()
    draw_inner_voice(img, "maybe I can't", (300, 500), 1.0, 0.5, 2.0)
    bbox = _diff_bbox(before, img)
    assert bbox is not None
    assert _red_pixels(img, bbox) > 0


def test_inner_voice_outside_window_is_noop() -> None:
    img = _canvas()
    before = img.copy()
    draw_inner_voice(img, "maybe I can't", (300, 500), 3.0, 0.5, 2.0)
    assert _diff_bbox(before, img) is None


def test_inner_voice_clips_off_canvas_anchor() -> None:
    img = _canvas()
    draw_inner_voice(img, "edge", (-400, -300), 1.0, 0.5, 2.0)
    draw_inner_voice(img, "edge", (W + 200, H + 200), 1.0, 0.5, 2.0)
    assert img.size == (W, H)


# --- draw_number_pop --------------------------------------------------------


def test_number_pop_keeps_canvas_size_and_settles() -> None:
    img = _canvas()
    draw_number_pop(img, 7, (960, 540), 2.1, 2.0, (234, 230, 223))
    assert img.size == (W, H)

    settled_a = _canvas()
    settled_b = _canvas()
    draw_number_pop(settled_a, 7, (960, 540), 2.2, 2.0, (234, 230, 223))
    draw_number_pop(settled_b, 7, (960, 540), 5.0, 2.0, (234, 230, 223))
    assert _diff_bbox(settled_a, settled_b) is None


def test_number_pop_starts_larger_than_settled() -> None:
    start_img = _canvas()
    settled_img = _canvas()
    draw_number_pop(start_img, 12, (960, 540), 2.0, 2.0, (234, 230, 223))
    draw_number_pop(settled_img, 12, (960, 540), 3.0, 2.0, (234, 230, 223))
    pop_box = _diff_bbox(_canvas(), start_img)
    settled_box = _diff_bbox(_canvas(), settled_img)
    assert pop_box is not None and settled_box is not None
    pop_w = pop_box[2] - pop_box[0]
    settled_w = settled_box[2] - settled_box[0]
    assert pop_w > settled_w


def test_number_pop_before_start_is_noop() -> None:
    img = _canvas()
    before = img.copy()
    draw_number_pop(img, 3, (960, 540), 1.0, 2.0, (234, 230, 223))
    assert _diff_bbox(before, img) is None


# --- draw_shatter_text ------------------------------------------------------


@pytest.mark.parametrize("t", [0.9, 1.0, 1.2, 1.5, 2.0, 2.2])
def test_shatter_runs_at_several_t(t: float) -> None:
    img = _canvas()
    draw_shatter_text(img, "BREAK", (960, 540), t, 1.0, duration=1.2)
    assert img.size == (W, H)


def test_shatter_changes_pixels_during_effect() -> None:
    img = _canvas()
    before = img.copy()
    draw_shatter_text(img, "BREAK", (960, 540), 1.3, 1.0, duration=1.2)
    assert _diff_bbox(before, img) is not None


def test_shatter_after_end_leaves_image_unchanged() -> None:
    img = _canvas()
    before = img.copy()
    draw_shatter_text(img, "BREAK", (960, 540), 2.2, 1.0, duration=1.2)
    draw_shatter_text(img, "BREAK", (960, 540), 2.5, 1.0, duration=1.2)
    assert _diff_bbox(before, img) is None


def test_shatter_before_start_is_noop() -> None:
    img = _canvas()
    before = img.copy()
    draw_shatter_text(img, "BREAK", (960, 540), 0.5, 1.0, duration=1.2)
    assert _diff_bbox(before, img) is None


def test_shatter_is_deterministic() -> None:
    a = _canvas()
    b = _canvas()
    draw_shatter_text(a, "PUSH", (960, 540), 1.4, 1.0)
    draw_shatter_text(b, "PUSH", (960, 540), 1.4, 1.0)
    assert _diff_bbox(a, b) is None


# --- draw_kinetic_line ------------------------------------------------------

KINETIC_WORDS = "lift the bar every single day".split()


def test_kinetic_line_changes_pixels_within_band_between_two_t() -> None:
    y, font_size = 200, 84
    rows = -(-len(KINETIC_WORDS) // 4)  # five words wrap onto two rows of at most four
    band_top, band_bottom = y - 60, y + rows * int(font_size * 1.25) + 60
    early = _canvas()
    late = _canvas()
    draw_kinetic_line(early, KINETIC_WORDS, 1.3, 1.0, 4.0, y=y)
    draw_kinetic_line(late, KINETIC_WORDS, 2.5, 1.0, 4.0, y=y)
    bbox = _diff_bbox(early, late)
    assert bbox is not None
    assert bbox[1] >= band_top and bbox[3] <= band_bottom
    assert _diff_bbox(_canvas(), late) is not None


def test_kinetic_line_is_empty_before_start() -> None:
    img = _canvas()
    before = img.copy()
    draw_kinetic_line(img, KINETIC_WORDS, 0.9, 1.0, 4.0)
    draw_kinetic_line(img, KINETIC_WORDS, 1.0, 1.0, 4.0)
    assert _diff_bbox(before, img) is None


def test_kinetic_line_is_empty_after_end() -> None:
    img = _canvas()
    before = img.copy()
    draw_kinetic_line(img, KINETIC_WORDS, 4.5, 1.0, 4.0)
    assert _diff_bbox(before, img) is None


def test_chunk_lines_caps_words_per_line() -> None:
    words = "one two three four five six seven eight nine ten".split()
    rows = _chunk_lines(words, 4)
    assert len(rows) == 3
    assert all(len(row) <= 4 for row in rows)
    assert [w for row in rows for w in row] == words


def test_kinetic_line_wraps_into_more_rows_when_longer() -> None:
    short = _canvas()
    long = _canvas()
    draw_kinetic_line(short, "one two three four", 5.0, 1.0, 9.0, max_per_line=4)
    draw_kinetic_line(
        long, "one two three four five six seven eight nine", 5.0, 1.0, 9.0, max_per_line=4
    )
    short_box = _diff_bbox(_canvas(), short)
    long_box = _diff_bbox(_canvas(), long)
    assert short_box is not None and long_box is not None
    assert (long_box[3] - long_box[1]) > (short_box[3] - short_box[1])


def test_kinetic_line_uppercase_flag_changes_rendering() -> None:
    lower = _canvas()
    upper = _canvas()
    draw_kinetic_line(lower, "rise up", 5.0, 1.0, 9.0)
    draw_kinetic_line(upper, "rise up", 5.0, 1.0, 9.0, uppercase=True)
    assert lower.tobytes() != upper.tobytes()


def test_kinetic_line_highlight_uses_highlight_colour() -> None:
    plain = _canvas()
    marked = _canvas()
    draw_kinetic_line(plain, KINETIC_WORDS, 5.0, 1.0, 9.0)
    draw_kinetic_line(marked, KINETIC_WORDS, 5.0, 1.0, 9.0, highlight_indices=(1,))
    bbox = _diff_bbox(_canvas(), marked)
    assert bbox is not None
    gold = sum(1 for px in _pixels(marked, bbox) if px[0] > 200 and px[1] > 150 and px[2] < 120)
    assert gold > 0
    gold_plain = sum(
        1 for px in _pixels(plain, bbox) if px[0] > 200 and px[1] > 150 and px[2] < 120
    )
    assert gold_plain == 0
