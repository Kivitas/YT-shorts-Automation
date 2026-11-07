"""Tests for the flat-illustrated longform prop library."""

from __future__ import annotations

import pytest
from PIL import Image, ImageChops

from autoshorts.longform.props import PROP_NAMES, draw_prop

CANVAS = (1920, 1080)
BACKGROUND = (8, 8, 12)


def _canvas(mode: str = "RGB") -> Image.Image:
    if mode == "RGBA":
        return Image.new("RGBA", CANVAS, (*BACKGROUND, 255))
    return Image.new("RGB", CANVAS, BACKGROUND)


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
@pytest.mark.parametrize("scale", [1.0, 0.5])
@pytest.mark.parametrize("name", PROP_NAMES)
def test_every_prop_draws(name: str, scale: float, mode: str) -> None:
    img = _canvas(mode)
    before = img.convert("RGB")
    draw_prop(img, name, (200, 150), scale=scale)

    assert img.size == CANVAS
    assert img.mode == mode
    diff = ImageChops.difference(img.convert("RGB"), before)
    assert diff.getbbox() is not None, f"{name} drew no pixels"


def test_prop_names_are_unique_and_complete() -> None:
    assert isinstance(PROP_NAMES, tuple)
    assert len(PROP_NAMES) == len(set(PROP_NAMES)) == 33
    assert "alarm_clock_7seg" in PROP_NAMES
    assert "shadow_demon" in PROP_NAMES
    for name in (
        "plant",
        "lamp",
        "bookshelf",
        "poster",
        "cup",
        "rug",
        "computer",
        "sofa",
        "tv",
        "coffee_table",
        "trash_bin",
        "clock_wall",
        "window_night",
        "sunrise",
    ):
        assert name in PROP_NAMES


def test_unknown_name_raises_value_error() -> None:
    with pytest.raises(ValueError):
        draw_prop(_canvas(), "toaster", (0, 0))


def test_non_positive_scale_raises_value_error() -> None:
    with pytest.raises(ValueError):
        draw_prop(_canvas(), "bed", (0, 0), scale=0)


def test_image_size_unchanged_when_partly_off_canvas() -> None:
    for origin in ((-60, -60), (1900, 1040), (1800, -30)):
        img = _canvas()
        draw_prop(img, "fridge", origin)
        assert img.size == CANVAS


def test_calendar_marks_change_pixels() -> None:
    blank = _canvas()
    marked = _canvas()
    draw_prop(blank, "calendar", (100, 100), marks=0)
    draw_prop(marked, "calendar", (100, 100), marks=5)
    assert blank.tobytes() != marked.tobytes()


def test_speech_bubble_accepts_text() -> None:
    short = _canvas()
    long = _canvas()
    draw_prop(short, "speech_bubble", (100, 100), text="Hi")
    draw_prop(long, "speech_bubble", (100, 100), text="A much longer sentence here")
    assert short.tobytes() != long.tobytes()


def test_bar_chart_highlight_changes_pixels() -> None:
    plain = _canvas()
    highlighted = _canvas()
    draw_prop(plain, "bar_chart", (100, 100))
    draw_prop(highlighted, "bar_chart", (100, 100), highlight=1)
    assert plain.tobytes() != highlighted.tobytes()


def test_bar_chart_bad_highlight_raises() -> None:
    with pytest.raises(ValueError):
        draw_prop(_canvas(), "bar_chart", (0, 0), highlight=7)


def test_animation_time_changes_glowing_props() -> None:
    a = _canvas()
    b = _canvas()
    draw_prop(a, "shadow_demon", (100, 100), t=0.0)
    draw_prop(b, "shadow_demon", (100, 100), t=0.25)
    assert a.tobytes() != b.tobytes()


def test_entrance_in_future_draws_nothing() -> None:
    img = _canvas()
    before = img.tobytes()
    draw_prop(img, "bed", (200, 150), entrance_start=5.0, t=1.0)
    assert img.tobytes() == before


def test_entrance_settled_matches_plain_draw() -> None:
    plain = _canvas()
    entered = _canvas()
    draw_prop(plain, "bed", (200, 150), t=0.5)
    draw_prop(entered, "bed", (200, 150), entrance_start=0.0, t=0.5)
    assert plain.tobytes() == entered.tobytes()


def test_entrance_mid_pop_is_partial() -> None:
    full = _canvas()
    mid = _canvas()
    draw_prop(full, "bed", (200, 150), t=0.5)
    draw_prop(mid, "bed", (200, 150), entrance_start=0.0, t=0.1)
    assert mid.tobytes() != full.tobytes()
    assert ImageChops.difference(mid.convert("RGB"), _canvas()).getbbox() is not None


def test_drop_differs_from_pop_mid_way() -> None:
    pop = _canvas()
    drop = _canvas()
    draw_prop(pop, "bed", (200, 150), entrance_start=0.0, t=0.1)
    draw_prop(drop, "bed", (200, 150), entrance_start=0.0, t=0.1, entrance="drop")
    assert pop.tobytes() != drop.tobytes()


def test_drop_settles_to_plain_draw() -> None:
    plain = _canvas()
    dropped = _canvas()
    draw_prop(plain, "bed", (200, 150))
    draw_prop(dropped, "bed", (200, 150), entrance_start=0.0, t=0.5, entrance="drop")
    assert plain.tobytes() == dropped.tobytes()


def test_unknown_entrance_raises_value_error() -> None:
    with pytest.raises(ValueError):
        draw_prop(_canvas(), "bed", (0, 0), entrance_start=0.0, entrance="fly")


def test_negative_wiggle_raises_value_error() -> None:
    with pytest.raises(ValueError):
        draw_prop(_canvas(), "bed", (0, 0), wiggle=-1.0)


def test_wiggle_changes_pixels_between_times() -> None:
    a = _canvas()
    b = _canvas()
    draw_prop(a, "bed", (200, 150), wiggle=5.0, t=0.0)
    draw_prop(b, "bed", (200, 150), wiggle=5.0, t=0.25)
    assert a.tobytes() != b.tobytes()


def test_alarm_clock_time_str_and_ringing() -> None:
    default_clock = _canvas()
    clock_459 = _canvas()
    clock_847 = _canvas()
    clock_ringing = _canvas()

    draw_prop(default_clock, "alarm_clock_7seg", (200, 150))
    draw_prop(clock_459, "alarm_clock_7seg", (200, 150), time_str="4:59")
    draw_prop(clock_847, "alarm_clock_7seg", (200, 150), time_str="8:47")
    draw_prop(clock_ringing, "alarm_clock_7seg", (200, 150), ringing=True)

    # Different time strings render different digits
    assert clock_459.tobytes() != default_clock.tobytes()
    assert clock_847.tobytes() != default_clock.tobytes()
    assert clock_459.tobytes() != clock_847.tobytes()

    # Ringing draws acoustic vibration rays/arcs
    assert clock_ringing.tobytes() != default_clock.tobytes()
    diff = ImageChops.difference(clock_ringing, default_clock)
    assert diff.getbbox() is not None

    # Test explicit digits parameter matches equivalent time_str
    explicit_digits = _canvas()
    draw_prop(explicit_digits, "alarm_clock_7seg", (200, 150), digits=("4", "5", "9"))
    assert explicit_digits.tobytes() == clock_459.tobytes()


def test_mirror_cracked_and_shadow_reflection() -> None:
    default_mirror = _canvas()
    cracked_mirror = _canvas()
    shadow_mirror = _canvas()
    both_mirror = _canvas()

    draw_prop(default_mirror, "mirror", (200, 150))
    draw_prop(cracked_mirror, "mirror", (200, 150), cracked=True)
    draw_prop(shadow_mirror, "mirror", (200, 150), shadow_reflection=True)
    draw_prop(both_mirror, "mirror", (200, 150), cracked=True, shadow_reflection=True)

    assert cracked_mirror.tobytes() != default_mirror.tobytes()
    assert shadow_mirror.tobytes() != default_mirror.tobytes()
    assert both_mirror.tobytes() != cracked_mirror.tobytes()
    assert both_mirror.tobytes() != shadow_mirror.tobytes()

    diff_cracked = ImageChops.difference(cracked_mirror, default_mirror)
    diff_shadow = ImageChops.difference(shadow_mirror, default_mirror)
    assert diff_cracked.getbbox() is not None
    assert diff_shadow.getbbox() is not None


def test_heavy_bag_hit_recoil() -> None:
    still_bag = _canvas()
    hit_bag = _canvas()

    draw_prop(still_bag, "heavy_bag", (200, 150), hit_recoil=False)
    draw_prop(hit_bag, "heavy_bag", (200, 150), hit_recoil=True)

    assert hit_bag.tobytes() != still_bag.tobytes()
    diff = ImageChops.difference(hit_bag, still_bag)
    bbox = diff.getbbox()
    assert bbox is not None
    # Punch impact lines expand leftward of the still bag
    assert bbox[0] < 200 + 20


def test_shadow_demon_cower() -> None:
    towering_demon = _canvas()
    cowering_demon = _canvas()

    draw_prop(towering_demon, "shadow_demon", (200, 150), cower=False)
    draw_prop(cowering_demon, "shadow_demon", (200, 150), cower=True)

    assert cowering_demon.tobytes() != towering_demon.tobytes()
    diff = ImageChops.difference(cowering_demon, towering_demon)
    assert diff.getbbox() is not None

    # In cower mode, the upper portion (e.g. y < 150 + 100) has no demon pixels (shrunken down)
    cower_crop = cowering_demon.crop((200 + 50, 150, 200 + 200, 150 + 100))
    bg_crop = _canvas().crop((200 + 50, 150, 200 + 200, 150 + 100))
    diff_upper = ImageChops.difference(cower_crop, bg_crop)
    # The towering demon definitely has pixels here, cowering does not
    tower_crop = towering_demon.crop((200 + 50, 150, 200 + 200, 150 + 100))
    diff_tower = ImageChops.difference(tower_crop, bg_crop)
    assert diff_tower.getbbox() is not None
    assert diff_upper.getbbox() is None


def test_draw_7seg_digit_all_numbers() -> None:
    for digit in "0123456789":
        img = _canvas("RGBA")
        draw_prop(img, "alarm_clock_7seg", (100, 100), time_str=f"{digit}:00")
        diff = ImageChops.difference(img.convert("RGB"), _canvas())
        assert diff.getbbox() is not None
