"""Per-section visual beats for long-form explainer videos.

``plan_beat`` turns one narration section into a :class:`BeatPlan` (figure pose,
gesture, expression, props, backdrop theme, light sources, inner voice and number
pops). The rules are plain keyword matches on the lower-cased narration, so the same
inputs always give the same plan. ``draw_beat`` renders that plan for one shot time
``t`` onto a 1920x1080 RGB frame. The backdrop comes from
:func:`autoshorts.longform.background.draw_themed_background` with the plan's theme.

The ``heading`` and ``speaker`` arguments are accepted so callers can pass the full
section context, but the current rules only read the narration.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from PIL import Image, ImageDraw

import autoshorts.longform.fx as _fx_mod
from autoshorts.core.theme import (
    ACCENT_CORAL,
    ACCENT_TEAL,
    BG_SURFACE,
    CORAL,
    NAVY_DEEP,
    NAVY_LIGHT,
    OFFWHITE,
    TEAL,
    TEXT_MUTED,
    YELLOW_SOFT,
)
from autoshorts.longform.background import draw_themed_background
from autoshorts.longform.kinetic_text import (
    _PAD,
    _blit,
    _font,
    _ink_layer,
    _text_width,
    draw_inner_voice,
    draw_number_pop,
    draw_shatter_text,
)
from autoshorts.longform.lighting import colour_wash, source_glow
from autoshorts.longform.props import draw_prop
from autoshorts.longform.rig import POSES, animate_pose, draw_rig
from autoshorts.longform.sphere import draw_sphere, orbit_point
from autoshorts.longform.sprite_rig import (
    ANKLE,
    CANVAS_PX_PER_RIG_UNIT,
    HIP,
    draw_sprite_rig,
    wrist_offset,
)
from autoshorts.longform.sprites import paste_sprite, sprite_size

RGB = tuple[int, int, int]
Box = tuple[float, float, float, float]  # x0, y0, x1, y1 in output pixels

_LOG = logging.getLogger(__name__)

FRAME_W = 1920
FRAME_H = 1080
FLOOR_Y = 760
# Standing and seated figures are drawn FIGURE_SCALE_UP times the base size.
FIGURE_SCALE_UP = 1.25
BASE_RIG_SCALE = 1.5  # rig.draw_rig scale: 108 units from hip to ankle
BASE_SPRITE_SCALE = 0.65  # satori canvas px -> output px at the base size
RIG_SCALE = BASE_RIG_SCALE * FIGURE_SCALE_UP
FIGURE_SPRITE_SCALE = BASE_SPRITE_SCALE * FIGURE_SCALE_UP
# Hip origin that puts the ankles on FLOOR_Y: the hip-to-ankle drop in canvas px, times the scale.
RIG_ORIGIN = (1000, round(FLOOR_Y - (ANKLE["l"][1] - HIP[1]) * FIGURE_SPRITE_SCALE))
# Top of the satori head art (head.png alpha box) in canvas px, for the head-top placement.
HEAD_TOP_CANVAS_Y = 75.0
HAND_REACH_CANVAS_PX = 50.0  # approximate length of the hand past the wrist, in canvas px
# A standing figure's highest point should stay below this line (see _lift_origin).
FIGURE_TOP_MIN_Y = 300
MOOD_WASH_INTENSITY = 0.08
# Narration sits in the top band, from NARRATION_TOP down to just above a standing head.
NARRATION_TOP = 120
NARRATION_BOTTOM = round(RIG_ORIGIN[1] + (HEAD_TOP_CANVAS_Y - HIP[1]) * FIGURE_SPRITE_SCALE) - 8
NARRATION_BAND = (NARRATION_TOP, NARRATION_BOTTOM)
NARRATION_FONT_SIZE = 66
NARRATION_MIN_FONT_SIZE = 40
NARRATION_MAX_PER_LINE = 7
NARRATION_LINE_SPACING = 1.25  # draw_kinetic_line uses int(font_size * 1.25) per row

# Subtitle Engine & Kinetic Burst Constants
NARRATION_BURST_MAX_WORDS = 3
SHORTS_SAFE_ZONE_Y_MIN = 1200
SHORTS_SAFE_ZONE_Y_MAX = 1350
SHORTS_SAFE_ZONE_Y_DEFAULT = 1250  # vertical 1080x1920 caption anchor
HORIZONTAL_SAFE_ZONE_Y = 880  # horizontal 1920x1080 caption anchor
SHORTS_CLEAR_BOTTOM_PX = 450
SHORTS_CLEAR_TOP_PX = 280

# Spring pop keyframes (seconds since the word's Whisper start -> scale factor).
# Linear segments, matching the ASS \t tags: 100->125 (0-70ms), 125->96 (70-130ms),
# 96->100 (130-180ms).
KARAOKE_SPRING_KEYFRAMES: tuple[tuple[float, float], ...] = (
    (0.000, 1.00),
    (0.070, 1.25),
    (0.130, 0.96),
    (0.180, 1.00),
)
KARAOKE_POP_SCALE = 1.25
KARAOKE_SETTLE_SCALE = 1.00
KARAOKE_POP_DURATION = 0.18

KARAOKE_ACTIVE_COLOR = (255, 230, 0)  # Solar Yellow #FFE600
KARAOKE_EMPHASIS_COLOR = (0, 240, 255)  # Supernova Cyan #00F0FF
KARAOKE_INACTIVE_COLOR = OFFWHITE  # Crisp Offwhite (234, 230, 223)
KARAOKE_STROKE_COLOR = (0, 0, 0)
KARAOKE_STROKE_WIDTH = 4
KARAOKE_BOX_FILL = (11, 14, 27, round(0.85 * 255))  # rgba(11,14,27,0.85) pill; alpha 217
KARAOKE_BOX_BORDER = (40, 50, 70, 220)
INNER_VOICE_ANCHOR = (140, 420)
NUMBER_ANCHOR = (1500, 520)

# Yellow base under the standing figure, as in the reference image. Sizes are output pixels.
# Poses whose feet are not on the floor line (seated, kneeling, crouching, lying) get no base.
NO_BASE_POSES = frozenset(
    {
        "sleep_bed",
        "lie_bed_prone",
        "sit_chair",
        "sit_desk_focus",
        "sit_floor_cross",
        "typing",
        "reading",
        "kneel",
        "squat",
        "lean_forward_table",
    }
)
FIGURE_BASE_YELLOW = (245, 197, 24)
FIGURE_BASE_EDGE = (120, 90, 10)
FIGURE_BASE_RX_PX = 120.0
FIGURE_BASE_RY_PX = 26.0
FIGURE_BASE_DROP_PX = 10.0

REST_POSE = "stand"
POSE_TRANSITION_S = 0.4  # the figure eases from REST_POSE into the planned pose after a shot start
HEAD_TURN_MAGNITUDE = 0.5

# Bed and lying figure. The bed is drawn at BED_SCALE from BED_XY; its pillow is at the left
# end. LYING_HIP is the hip so the head rests on the pillow and the body lies on the mattress.
BED_XY = (60, 480)
BED_SCALE = 1.4
LYING_HIP = (374, 570)
LYING_POSES = frozenset({"sleep_bed", "lie_bed_prone"})
# Poses that keep the figure's own side: a mirrored figure would leave the pillow or the desk.
NO_MIRROR_POSES = LYING_POSES | {"sit_desk_focus", "typing", "reading"}
# Poses whose body stands, so a narration gesture may replace the hand pose.
GESTURE_POSES = frozenset({"stand", "lean_wall", "look_left", "look_right"})
# Desk monitor, placed relative to the seated figure's near hand (figure origin + hand offset).
DESK_POSE = "typing"
DESK_MONITOR_SCALE = 0.6
DESK_MONITOR_OFFSET = (10, -100)  # top-left corner, measured from the hand

SPACE_WORDS = frozenset({"planet", "earth", "space", "moon", "orbit", "universe"})
SPHERE_CENTRE = (1500, 380)
SPHERE_RADIUS = 150.0
SPHERE_SPIN_DEG_S = 10.0
SPHERE_LAND = (120, 190, 110)
ORBIT_RADIUS = 210.0
ORBIT_TILT = 0.35
SATELLITE_SPEED_DEG_S = 45.0
SATELLITE_RADIUS = 9

PARALLAX_CAMERA_SPEED = 40.0  # px per second of camera pan, passed to the backdrop
NIGHT_THEME = "navy_night"  # the default backdrop, which keeps the navy gradient sprite
# Backdrops used when no keyword picks one; consecutive sections never share a theme.
THEME_CYCLE: tuple[str, ...] = (
    "forest_dawn",
    "office_warm",
    "lab_teal",
    "city_neon",
    "candy",
    "sunset",
    NIGHT_THEME,
)

BACKDROP_SPRITE = "navy_gradient"
# Props with a PNG sprite; the sprite is used once the prop has entered, else draw_prop runs.
PROP_SPRITES: dict[str, str] = {
    "window": "window",
    "phone": "phone",
    "alarm_clock_7seg": "alarm_clock",
    "mountain": "mountain",
    "heavy_bag": "heavy_bag",
}
# Fixed top-left positions (scale 1.0) for the props added by keyword. Each one sits on
# FLOOR_Y, on the coffee table top, or on a wall, and stays inside the frame.
PROP_XY: dict[str, tuple[int, int]] = {
    "lamp": (1600, 436),
    "bookshelf": (80, 459),
    "plant": (1780, 529),
    "tv": (1400, 549),
    "sofa": (1380, 559),
    "trash_bin": (300, 559),
    "clock_wall": (1560, 140),
    "sunrise": (1480, 340),
    "window_night": (1500, 160),
    "coffee_table": (1420, 559),
    "cup": (1500, 470),  # bottom edge rests on the coffee table top at y 639
    "computer": (1440, 509),
    "mirror": (1320, 460),
    "fridge_open": (1340, 520),
}
MAX_PROPS = 4  # keeps each frame readable; earlier rules win when the cap cuts props
# Narration keyword (whole word or phrase) -> satori sprite name. A missing sprite file is
# skipped at draw time, so these names can be added before their PNGs exist.
SPRITE_KEYWORDS: dict[str, str] = {
    # Science and nature
    "atom": "atom",
    "atoms": "atom",
    "electron": "atom",
    "electrons": "atom",
    "nucleus": "atom",
    "water": "molecule_water",
    "h2o": "molecule_water",
    "carbon": "molecule_co2",
    "co2": "molecule_co2",
    "dna": "dna_helix",
    "gene": "dna_helix",
    "genes": "dna_helix",
    "sun": "sun",
    "sunrise": "sun",
    "solar": "sun",
    "moon": "moon",
    "crescent": "moon",
    "lunar": "moon_full",
    "night sky": "moon_full",
    "earth": "earth",
    "planet": "planet_ringed",
    "planets": "planet_ringed",
    "star": "star",
    "stars": "star",
    "leaf": "leaf",
    "green": "leaf",
    "eco": "leaf",
    "nature": "leaf",
    "drop": "water_drop",
    "droplet": "water_drop",
    "rain": "water_drop",
    "cold": "snowflake",
    "snow": "snowflake",
    "winter": "snowflake",
    "fire": "fire",
    "burn": "fire",
    "hot": "fire",
    "heat": "fire",
    "lightning": "lightning",
    "fast": "lightning",
    "shock": "lightning",
    "electric": "lightning",
    # Ideas, tools and science
    "idea": "lightbulb",
    "light": "lightbulb",
    "bulb": "lightbulb",
    "gear": "gear",
    "system": "gear",
    "process": "gear",
    "search": "magnifier",
    "research": "magnifier",
    "investigate": "magnifier",
    "look closer": "magnifier",
    "flask": "flask",
    "chemistry": "flask",
    "experiment": "flask",
    "lab": "flask",
    "microscope": "microscope",
    "cell": "microscope",
    "cells": "microscope",
    "biology": "microscope",
    "brain": "brain",
    "mind": "brain",
    "think": "brain",
    "thinking": "brain",
    "battery": "battery",
    "energy": "battery",
    "charge": "battery",
    "power": "battery",
    # Feelings and answers
    "heart": "heart",
    "love": "heart",
    "care": "heart",
    "yes": "check_mark",
    "correct": "check_mark",
    "right": "check_mark",
    "true": "check_mark",
    "wrong": "cross_mark",
    "false": "cross_mark",
    "mistake": "cross_mark",
    "no": "cross_mark",
    "warning": "warning_triangle",
    "danger": "warning_triangle",
    "risk": "warning_triangle",
    # Money, growth and places
    "money": "coin",
    "coin": "coin",
    "cash": "coin",
    "pay": "coin",
    "dollar": "coin",
    "growth": "chart_up",
    "grow": "chart_up",
    "increase": "chart_up",
    "rise": "chart_up",
    "profit": "chart_up",
    "world": "globe",
    "global": "globe",
    "globe": "globe",
    "country": "globe",
    "location": "map_pin",
    "place": "map_pin",
    "map": "map_pin",
    "say": "speech_bubble",
    "tell": "speech_bubble",
    "talk": "speech_bubble",
    "message": "speech_bubble",
    "announce": "megaphone",
    "shout": "megaphone",
    # Wins and safety
    "win": "trophy",
    "trophy": "trophy",
    "champion": "trophy",
    "medal": "medal",
    "award": "medal",
    "goal": "medal",
    "safe": "shield",
    "protect": "shield",
    "secure": "shield",
    "lock": "lock",
    "password": "lock",
    "private": "lock",
    "key": "key",
    "unlock": "key",
    "solution": "key",
    # Time
    "time": "timer",
    "timer": "timer",
    "minutes": "timer",
    "seconds": "timer",
}
SPRITE_CAP = 3  # sprite props per section, taken in narration order
SPRITE_HEIGHT_PX = 220
# Placement classes. Each sprite gets a preferred centre from its class, then draw time moves it
# to the first candidate that clears the figure, the drawn props and the other sprites.
SKY_SPRITES = frozenset(
    {
        "sun",
        "moon",
        "moon_full",
        "star",
        "star_cluster",
        "planet_ringed",
        "earth",
        "cloud",
        "rocket",
        "snowflake",
        "lightning",
        "rainbow",
    }
)
HAND_SPRITES = frozenset(
    {
        "atom",
        "molecule_water",
        "molecule_co2",
        "dna_helix",
        "flask",
        "test_tube",
        "microscope",
        "lightbulb",
        "magnifier",
        "book_stack",
        "laptop",
        "coffee_cup",
        "phone",
        "gear",
        "battery",
    }
)
ICON_SPRITES = frozenset(
    {
        "check_mark",
        "cross_mark",
        "warning_triangle",
        "coin",
        "trophy",
        "medal",
        "heart",
        "shield",
        "lock",
        "key",
        "chart_up",
        "speech_bubble",
        "megaphone",
        "timer",
        "brain",
        "fire",
        "water_drop",
        "leaf",
    }
)  # any sprite name not listed in SKY or HAND is ICON
SKY_BAND_Y = (300, 460)  # the centre y of a sky sprite stays in this band
SKY_CENTRE_Y = 380
SKY_SLOT_X = (240, 1680)  # left and right third, alternated by sky sprite order
HAND_Y = 520  # hand height of the standing and seated figure
HAND_OFFSET_X = 220
ICON_Y = 330  # next to the head
ICON_OFFSET_X = 260
FIGURE_HALF_WIDTH_PX = 120  # the figure box, from the body centre, covers the arms at rest
SPRITE_MARGIN_X = (40, 1880)
SPRITE_CANDIDATES = 6
SKY_STEP_X = 120
SKY_STEP_Y = 80
SPRITE_SHIFT_X = (0, 120, -120, 240, -240, 360)
# One alternation, longest keys first, so "night sky" wins over any shorter key it contains.
_SPRITE_KEYWORD_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(key) for key in sorted(SPRITE_KEYWORDS, key=len, reverse=True))
    + r")\b"
)
# Seated figure's near hand, relative to RIG_ORIGIN, from the DESK_POSE pose.
_HAND_OFFSET = wrist_offset(POSES[DESK_POSE], FIGURE_SPRITE_SCALE, "l")

GESTURE_POINT_WORDS = frozenset({"look", "see", "watch", "first", "start"})
GESTURE_OPEN_WORDS = frozenset({"you", "your"})
GESTURE_SHRUG_WORDS = frozenset({"maybe", "why"})
GESTURE_SHRUG_PHRASES = ("not sure",)
LOOK_WORDS = frozenset({"look"})
HEAD_TURN_WORDS = frozenset({"window", "bed"})

# Narration words drawn in the highlight colour. Positive and negative words have palette
# tokens too, but draw_kinetic_line takes one highlight colour per line.
HIGHLIGHT_WORDS = frozenset({"you", "your", "every", "more", "now", "today"})
POSITIVE_WORDS = frozenset({"better", "start", "new"})
NEGATIVE_WORDS = frozenset({"not", "never", "lazy", "fail"})


@dataclass(frozen=True)
class BeatPalette:
    """Colours used by the beat renderer. Every value is a theme token."""

    void: RGB
    floor: RGB
    red: RGB
    blue: RGB
    text: RGB
    text_muted: RGB
    highlight: RGB
    inner_voice: RGB
    rim: RGB
    positive: RGB
    negative: RGB


def beat_palette() -> BeatPalette:
    """Return the beat palette. Only this function names colours, all from the theme."""
    return BeatPalette(
        void=NAVY_DEEP,
        floor=NAVY_LIGHT,
        red=CORAL,
        blue=TEAL,
        text=OFFWHITE,
        text_muted=TEXT_MUTED,
        highlight=YELLOW_SOFT,
        inner_voice=ACCENT_CORAL,
        rim=ACCENT_TEAL,
        positive=ACCENT_TEAL,
        negative=ACCENT_CORAL,
    )


PALETTE = beat_palette()

POSE_CYCLE: tuple[str, ...] = ("stand", "point", "lean_wall", "sit_chair", "reach_up")

BED_WORDS = frozenset({"bed", "sleep", "morning", "alarm", "wake"})
PHONE_WORDS = frozenset({"phone", "scroll"})
COUCH_WORDS = frozenset({"couch", "tired"})
DESK_WORDS = frozenset({"desk", "work", "focus"})
BAG_WORDS = frozenset({"bag", "punch", "fight", "hit"})
MOUNTAIN_WORDS = frozenset({"mountain", "climb", "summit", "top"})
MIRROR_WORDS = frozenset({"mirror", "reflection", "stare at yourself"})
FRIDGE_WORDS = frozenset({"fridge", "refrigerator", "midnight snack"})
SHADOW_WORDS = frozenset({"shadow", "excuse", "demon", "tomorrow", "later"})
SLEEP_WORDS = frozenset({"bed", "sleep", "night", "tired"})
PRONE_WORDS = frozenset({"stomach", "prone", "face"})
CHAIR_WORDS = frozenset({"chair", "sit"})
WIN_WORDS = frozenset({"better", "new", "win", "won", "success", "celebrate"})
START_WORDS = frozenset({"start", "begin"})
# The calendar needs a clear scheduling word or the phrase "every day"; "day" alone is too generic.
CALENDAR_WORDS = frozenset({"calendar", "schedule", "date", "planner", "deadline"})
CALENDAR_PHRASE_RE = re.compile(r"\bevery day\b")
DAY_WORDS = frozenset({"day", "daily"})  # counted for the calendar marks once it is shown
# Extra props and poses, each from the narration keywords named in the section rules.
LAMP_WORDS = frozenset({"bed", "sleep"})
STUDY_WORDS = frozenset({"study", "book", "read", "reading"})
PLANT_WORDS = frozenset({"plant", "nature", "grow", "garden"})
TV_WORDS = frozenset({"tv", "watch", "show"})
SOFA_WORDS = frozenset({"sofa", "couch", "relax"})
TRASH_WORDS = frozenset({"trash", "delete", "throw"})
CLOCK_WORDS = frozenset({"clock", "late"})  # "time" is too generic to pick a clock
MORNING_SUN_WORDS = frozenset({"sun", "morning", "dawn", "rise"})
NIGHT_WORDS = frozenset({"night", "midnight", "dark"})
COMPUTER_WORDS = frozenset({"email", "typing", "office", "meeting"})
TABLE_WORDS = frozenset({"desk", "work"})
TYPING_WORDS = frozenset({"typing", "desk", "work", "focus", "office", "email", "meeting"})
READING_WORDS = frozenset({"reading", "read", "study", "book", "research"})
ANGER_WORDS = frozenset({"anger", "angry", "unfair", "mad", "furious"})
FAIL_WORDS = frozenset({"fail", "failed", "failing", "never"})
SURPRISE_WORDS = frozenset({"surprise", "surprised", "wait", "what", "suddenly"})

# Backdrop keyword sets, checked in this order; the first set that matches wins.
NIGHT_THEME_WORDS = frozenset({"night", "sleep", "bed", "dark", "shadow", "midnight"})
MORNING_THEME_WORDS = frozenset({"morning", "sun", "wake", "dawn", "rise"})
OFFICE_THEME_WORDS = frozenset({"work", "desk", "office", "email", "meeting", "typing"})
LAB_THEME_WORDS = frozenset({"study", "lab", "science", "data", "research"})
CITY_THEME_WORDS = frozenset({"city", "phone", "scroll", "neon", "online"})
CANDY_THEME_WORDS = frozenset({"fun", "happy", "win", "celebrate"})

# Props enter when the word that names them is first spoken in the section; the others
# enter at the shot start. Bags and mountains drop in, the rest pop. Alarm and phone wiggle.
PROP_ENTRANCE_WORDS: dict[str, frozenset[str]] = {
    "bed": frozenset({"bed", "sleep"}),
    "alarm_clock_7seg": frozenset({"alarm", "wake"}),
    "phone": PHONE_WORDS,
    "couch": COUCH_WORDS,
    "desk_monitor": DESK_WORDS,
    "heavy_bag": BAG_WORDS,
    "mountain": MOUNTAIN_WORDS,
    "shadow_demon": SHADOW_WORDS,
    "calendar": CALENDAR_WORDS | DAY_WORDS | {"every"},
    "mirror": MIRROR_WORDS,
    "fridge_open": FRIDGE_WORDS,
}
PROP_ENTRANCE_KIND: dict[str, str] = {"heavy_bag": "drop", "mountain": "drop"}
PROP_WIGGLE: dict[str, float] = {"alarm_clock_7seg": 1.2, "phone": 1.2}

# Inner-voice line shown for each excuse trigger word (first one in the narration wins).
EXCUSE_LINES: dict[str, str] = {
    "tomorrow": "I'll start tomorrow",
    "later": "I'll do it later",
    "excuse": "Just one more excuse",
    "shadow": "Not today, maybe",
    "demon": "Let me rest first",
}

_WORD_RE = re.compile(r"[a-z]+")
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class PropSpec(NamedTuple):
    """One prop: name, top-left position in pixels, scale and prop-specific options."""

    name: str
    x: int
    y: int
    scale: float
    params: dict[str, Any]

    @property
    def pos(self) -> tuple[int, int]:
        return (self.x, self.y)


class PropPlacement(PropSpec):
    """PropPlacement representation, interchangeable with PropSpec.

    Supports both (name, (x, y), scale, params) and (name, x, y, scale, params).
    """

    def __new__(
        cls,
        name: str,
        pos_or_x: int | float | tuple[int | float, int | float],
        y_or_scale: int | float = 1.0,
        scale_or_params: float | dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> PropPlacement:
        if isinstance(pos_or_x, tuple):
            x = int(round(pos_or_x[0]))
            y = int(round(pos_or_x[1]))
            scale = float(y_or_scale)
            if isinstance(scale_or_params, dict):
                p = dict(scale_or_params)
            else:
                p = dict(params) if params is not None else {}
        else:
            x = int(round(pos_or_x))
            y = int(round(y_or_scale))
            scale = float(1.0 if scale_or_params is None else scale_or_params)
            p = dict(params) if params is not None else {}
        return super().__new__(cls, name, x, y, scale, p)


def _contains_any(
    blob: str | set[str] | Sequence[str], keywords: frozenset[str] | set[str]
) -> bool:
    """Check if any keyword or phrase in ``keywords`` is present in ``blob``."""
    if isinstance(blob, str):
        lowered = blob.lower()
        for kw in keywords:
            if " " in kw:
                if kw in lowered:
                    return True
            else:
                if re.search(r"\b" + re.escape(kw) + r"\b", lowered):
                    return True
        return False
    token_set = set(blob)
    for kw in keywords:
        if " " in kw:
            words = kw.split()
            if isinstance(blob, (list, tuple)):
                for i in range(len(blob) - len(words) + 1):
                    if list(blob[i : i + len(words)]) == words:
                        return True
        elif kw in token_set:
            return True
    return False


class SpriteSpec(NamedTuple):
    """One sprite prop: sprite name and the pixel centre it is pasted at."""

    name: str
    x: int
    y: int


@dataclass(frozen=True)
class LightSource:
    """A lamp or screen glow painted with ``source_glow``."""

    center: tuple[int, int]
    radius: float
    color: RGB
    intensity: float


@dataclass
class BeatPlan:
    """Everything needed to draw one section's visual, independent of time."""

    section_index: int
    pose: str
    expression: str
    mirror: bool
    headband: bool
    props: list[PropSpec] = field(default_factory=list)
    inner_voice: str | None = None
    number_values: list[str] = field(default_factory=list)
    mood: RGB | None = None
    light_sources: list[LightSource] = field(default_factory=list)
    gesture: str | None = None  # when set, this rig pose replaces ``pose`` on screen
    theme: str = NIGHT_THEME  # backdrop theme name for draw_themed_background
    sprites: list[SpriteSpec] = field(default_factory=list)  # keyword sprites, at most SPRITE_CAP
    shatter_text: str | None = None


def _choose_gesture(words: set[str], lowered: str) -> str | None:
    """Pick a gesture from the narration, or None to keep the pose cycle."""
    if words & GESTURE_POINT_WORDS:
        return "gesture_point_up"
    if words & GESTURE_OPEN_WORDS:
        return "gesture_open_hands"
    if words & GESTURE_SHRUG_WORDS or any(phrase in lowered for phrase in GESTURE_SHRUG_PHRASES):
        return "gesture_shrug"
    return None


def _choose_theme(words: set[str], section_index: int) -> str:
    """Pick the backdrop from keywords; unmatched sections cycle through THEME_CYCLE."""
    if words & NIGHT_THEME_WORDS:
        return NIGHT_THEME
    if words & MORNING_THEME_WORDS:
        return "forest_dawn" if section_index % 2 == 0 else "sunset"
    if words & OFFICE_THEME_WORDS:
        return "office_warm"
    if words & LAB_THEME_WORDS:
        return "lab_teal"
    if words & CITY_THEME_WORDS:
        return "city_neon"
    if words & CANDY_THEME_WORDS:
        return "candy"
    return THEME_CYCLE[section_index % len(THEME_CYCLE)]


def _sprite_class(name: str) -> str:
    """Placement class of a sprite name: 'sky', 'hand' or 'icon' (the default)."""
    if name in SKY_SPRITES:
        return "sky"
    if name in HAND_SPRITES:
        return "hand"
    return "icon"


def _choose_sprites(lowered: str, figure_x: float) -> list[SpriteSpec]:
    """Sprite props for the first distinct keyword matches, in narration order, capped at SPRITE_CAP.

    Each sprite gets its class's preferred centre; ``draw_beat`` moves it clear of the figure and
    props when it draws (see ``_place_sprites``).
    """
    names: list[str] = []
    for match in _SPRITE_KEYWORD_RE.finditer(lowered):
        name = SPRITE_KEYWORDS[match.group(0)]
        if name not in names:
            names.append(name)
        if len(names) == SPRITE_CAP:
            break
    specs: list[SpriteSpec] = []
    sky_count = hand_count = 0
    for name in names:
        kind = _sprite_class(name)
        if kind == "sky":
            x, y = SKY_SLOT_X[sky_count % len(SKY_SLOT_X)], SKY_CENTRE_Y
            sky_count += 1
        elif kind == "hand":
            side = 1 if hand_count % 2 == 0 else -1
            x, y = round(figure_x + side * HAND_OFFSET_X), HAND_Y
            hand_count += 1
        else:
            x, y = (
                round(figure_x + (ICON_OFFSET_X if len(specs) % 2 == 0 else -ICON_OFFSET_X)),
                ICON_Y,
            )
        specs.append(SpriteSpec(name, x, y))
    return specs


def _has_prop(props: Sequence[PropSpec], name: str) -> bool:
    return any(prop.name == name for prop in props)


def _add_prop(props: list[PropSpec], name: str) -> None:
    """Add the prop at its fixed position unless the section already has it."""
    if not _has_prop(props, name):
        x, y = PROP_XY[name]
        props.append(PropPlacement(name, (x, y), 1.0, {}))


def _choose_mood(words: set[str]) -> tuple[str | None, str | None]:
    """Body pose and expression from the narration, first matching rule wins."""
    if words & SLEEP_WORDS:
        return ("lie_bed_prone" if words & PRONE_WORDS else "sleep_bed"), "tired"
    if words & PHONE_WORDS:
        return "phone_hold", "tired"
    if words & TYPING_WORDS:
        return "typing", "focus"
    if words & READING_WORDS:
        return "reading", "focus"
    if words & ANGER_WORDS:
        return "hug_self", "angry"
    if words & FAIL_WORDS:
        return "facepalm", "worried"
    if words & NEGATIVE_WORDS:
        return "slump", "sad"
    if words & SURPRISE_WORDS:
        return "point_side", "surprised"
    if words & WIN_WORDS:
        return "thumbs_up", "excited"
    if words & START_WORDS:
        return "fist_up", "excited"
    if words & CHAIR_WORDS:
        return "sit_chair", None
    return None, None


def plan_beat(
    section_index: int,
    heading: str,
    narration: str,
    speaker: str,
    is_last: bool,
) -> BeatPlan:
    """Build the visual plan for one section. Deterministic for the same inputs."""
    del heading, speaker  # accepted for API context; the rules read the narration only
    lowered = narration.lower()
    blob = lowered
    tokens = _WORD_RE.findall(lowered)
    words = set(tokens)

    props: list[PropSpec] = []
    light_sources: list[LightSource] = []
    pose: str | None = None
    mood: RGB | None = None
    inner_voice: str | None = None

    has_negative = bool(words & NEGATIVE_WORDS)
    has_shadow = bool(words & SHADOW_WORDS)

    if words & BED_WORDS:
        props.append(PropPlacement("bed", BED_XY, BED_SCALE, {}))
        props.append(
            PropPlacement(
                "alarm_clock_7seg",
                (600, 600),
                0.5,
                {"ringing": True, "time_str": "5:00"},
            )
        )
    if words & PHONE_WORDS:
        props.append(PropPlacement("phone", (1420, 560), 1.0, {}))
        light_sources.append(LightSource((1450, 600), 260.0, PALETTE.blue, 0.8))
    if words & COUCH_WORDS:
        props.append(PropPlacement("couch", (1380, 600), 1.0, {}))
        pose = pose or "slump_couch"
    if words & DESK_WORDS:
        desk_x = RIG_ORIGIN[0] + _HAND_OFFSET[0] + DESK_MONITOR_OFFSET[0]
        desk_y = RIG_ORIGIN[1] + _HAND_OFFSET[1] + DESK_MONITOR_OFFSET[1]
        props.append(
            PropPlacement(
                "desk_monitor",
                (round(desk_x), round(desk_y)),
                DESK_MONITOR_SCALE,
                {},
            )
        )
    if words & BAG_WORDS:
        props.append(PropPlacement("heavy_bag", (1460, 380), 1.0, {"hit_recoil": True}))
        pose = pose or "box_punch"
    if words & MOUNTAIN_WORDS:
        props.append(PropPlacement("mountain", (60, 380), 1.0, {}))
        pose = pose or ("climb_slope" if "climb" in words else "summit_stand")
    if words & SHADOW_WORDS:
        props.append(PropPlacement("shadow_demon", (1480, 380), 1.0, {}))
        mood = PALETTE.red
        for token in tokens:
            if token in EXCUSE_LINES:
                inner_voice = EXCUSE_LINES[token]
                break
    if words & CALENDAR_WORDS or CALENDAR_PHRASE_RE.search(lowered):
        marks = sum(1 for token in tokens if token in DAY_WORDS)
        props.append(PropPlacement("calendar", (1380, 300), 1.0, {"marks": marks}))
    if _contains_any(blob, MIRROR_WORDS):
        props.append(
            PropPlacement(
                "mirror",
                (1320, 460),
                1.0,
                {
                    "cracked": has_negative or has_shadow,
                    "shadow_reflection": has_shadow or has_negative,
                },
            )
        )
    if _contains_any(blob, FRIDGE_WORDS):
        props.append(PropPlacement("fridge_open", (1340, 520), 1.0))

    # Scene props added by keyword. Each name is added at most once.
    if words & LAMP_WORDS:
        _add_prop(props, "lamp")
    if words & STUDY_WORDS:
        _add_prop(props, "bookshelf")
        _add_prop(props, "lamp")
    if words & PLANT_WORDS:
        _add_prop(props, "plant")
    if words & TV_WORDS:
        _add_prop(props, "tv")
    if words & SOFA_WORDS and not _has_prop(props, "couch"):
        _add_prop(props, "sofa")
    if words & TRASH_WORDS:
        _add_prop(props, "trash_bin")
    if words & CLOCK_WORDS and not _has_prop(props, "alarm_clock_7seg"):
        _add_prop(props, "clock_wall")
    if words & MORNING_SUN_WORDS:
        _add_prop(props, "sunrise")
    elif words & NIGHT_WORDS:
        _add_prop(props, "window_night")
    if words & COMPUTER_WORDS:
        _add_prop(props, "computer")
    elif words & TABLE_WORDS:
        _add_prop(props, "coffee_table")
        _add_prop(props, "cup")

    if not props:
        props.append(PropPlacement("window", (80, 340), 1.0, {}))

    # Body position and expression from the narration, first match wins. The props above
    # (couch, bag, mountain) keep their own pose.
    mood_pose, mood_expression = _choose_mood(words)

    if pose is None:
        pose = mood_pose

    if pose is None and words & LOOK_WORDS:
        pose = "look_right" if section_index % 2 == 0 else "look_left"

    if pose is None:
        pose = POSE_CYCLE[section_index % len(POSE_CYCLE)]

    # A lying figure always has a bed under it, even when the narration never names one.
    if pose in LYING_POSES and not _has_prop(props, "bed"):
        props.insert(0, PropPlacement("bed", BED_XY, BED_SCALE, {}))

    del props[MAX_PROPS:]
    # Figure centre x, used to place the hand and icon sprites beside the body.
    figure_x = LYING_HIP[0] if pose in LYING_POSES else RIG_ORIGIN[0]

    if mood_expression is not None:
        expression = mood_expression
    elif is_last:
        expression = "determined"
    else:
        expression = "exhausted" if section_index % 2 == 0 else "grit"

    shatter_text: str | None = None
    if pose in ("box_punch", "climb_slope", "fist_up", "thumbs_up") and (
        inner_voice is not None
        or has_negative
        or _contains_any(blob, BAG_WORDS)
        or _contains_any(blob, MOUNTAIN_WORDS)
    ):
        shatter_text = inner_voice or '"you can\'t."'

    return BeatPlan(
        section_index=section_index,
        pose=pose,
        expression=expression,
        mirror=section_index % 2 == 1 and pose not in NO_MIRROR_POSES,
        headband=is_last,
        props=props,
        inner_voice=inner_voice,
        number_values=_NUMBER_RE.findall(narration),
        mood=mood,
        light_sources=light_sources,
        gesture=_choose_gesture(words, lowered) if pose in GESTURE_POSES else None,
        theme=_choose_theme(words, section_index),
        sprites=_choose_sprites(lowered, figure_x),
        shatter_text=shatter_text,
    )


def _draw_number_pops(
    img: Image.Image,
    values: Sequence[str],
    t: float,
    start: float,
    end: float,
) -> None:
    """Show the numbers one after another, each in an equal slice of the section."""
    count = len(values)
    if count == 0 or t < start:
        return
    slot = (end - start) / count
    index = count - 1 if slot <= 0 else min(count - 1, int((t - start) // slot))
    slot_start = start + index * slot if slot > 0 else start
    draw_number_pop(img, values[index], NUMBER_ANCHOR, t, slot_start, PALETTE.highlight)


def _entrance_start(
    name: str, words: Sequence[Mapping[str, Any]] | None, shot_start: float
) -> float:
    """Start time of the earliest word that names prop ``name``, else ``shot_start``."""
    triggers = PROP_ENTRANCE_WORDS.get(name, frozenset())
    times = [
        float(entry["start"])
        for entry in words or ()
        if triggers.intersection(_WORD_RE.findall(str(entry["word"]).lower()))
    ]
    return min(times) if times else shot_start


def _rig_pose(plan: BeatPlan, shot_start: float, t: float) -> dict[str, float]:
    """Joint angles easing from the rest pose into the gesture (or planned pose) after a shot start."""
    target = plan.gesture or plan.pose
    p = (t - shot_start) / POSE_TRANSITION_S
    return animate_pose(REST_POSE, target, p, t)


def _head_turn(section_text: str) -> float:
    """Turn toward the window or bed when they are named, otherwise away from them."""
    words = set(_WORD_RE.findall(section_text.lower()))
    return HEAD_TURN_MAGNITUDE if words & HEAD_TURN_WORDS else -HEAD_TURN_MAGNITUDE


def _is_space_topic(section_text: str) -> bool:
    return bool(SPACE_WORDS.intersection(_WORD_RE.findall(section_text.lower())))


def _split_sentences(text: str) -> list[str]:
    """Split narration into sentences, keeping at least one (possibly empty) entry."""
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text.strip()) if part.strip()]
    return parts or [text.strip()]


def _plain_word(word: str) -> str:
    return re.sub(r"[^a-z]", "", word.lower())


def _narration_font_size(sentences: Sequence[str]) -> int:
    """Largest font whose rows for the longest sentence fit inside NARRATION_BAND."""
    rows = max(
        (math.ceil(len(sentence.split()) / NARRATION_MAX_PER_LINE) for sentence in sentences),
        default=1,
    )
    height = NARRATION_BAND[1] - NARRATION_BAND[0]
    fit = int(height // (max(1, rows) * NARRATION_LINE_SPACING))
    return max(NARRATION_MIN_FONT_SIZE, min(NARRATION_FONT_SIZE, fit))


def _draw_narration(
    img: Image.Image,
    section_text: str,
    t: float,
    start: float,
    end: float,
    words: Sequence[Mapping[str, Any]] | None = None,
    *,
    safe_zone_y: int | None = None,
    active_box: bool = True,
) -> None:
    """Render the narration at time ``t`` as strict single-line 1-3 word bursts.

    Bursts come from ``words`` (Whisper word timestamps, absolute seconds) when given, otherwise
    from ``section_text`` with estimated timings across ``[start, end]``. Only one burst, one line,
    is on screen at a time. ``safe_zone_y`` overrides the default anchor (Shorts Y=1250,
    horizontal Y=880).
    """
    if not section_text.strip() or t < start or t > end:
        return

    bursts = chunk_narration_bursts(words, section_text, start, end)
    if not bursts:
        return

    active_burst = None
    for b in bursts:
        if b.start <= t <= b.end:
            active_burst = b
            break
    if active_burst is None:
        active_burst = bursts[0] if t < bursts[0].start else bursts[-1]

    draw_karaoke_burst(img, active_burst, t, y=safe_zone_y, active_box=active_box)


def _spring_scale(tau: float) -> float:
    """Scale factor for a word ``tau`` seconds after its Whisper start.

    Piecewise linear, identical to the ASS tags: 1.00 at 0 ms, 1.25 at 70 ms (overshoot),
    0.96 at 130 ms (rebound), 1.00 at 180 ms (settle). Before 0 and after 180 ms it is 1.00.
    """
    if tau <= 0.0:
        return KARAOKE_SPRING_KEYFRAMES[0][1]
    keys = KARAOKE_SPRING_KEYFRAMES
    for (t0, s0), (t1, s1) in zip(keys, keys[1:], strict=False):
        if tau <= t1:
            return s0 + (s1 - s0) * (tau - t0) / (t1 - t0)
    return KARAOKE_SPRING_KEYFRAMES[-1][1]


@dataclass(frozen=True)
class NarrationBurst:
    """A single-line burst of 1-3 spoken words."""

    words: list[dict[str, Any]]
    text: str
    start: float
    end: float


def chunk_narration_bursts(
    words: Sequence[Mapping[str, Any]] | None,
    section_text: str,
    start: float,
    end: float,
    max_words: int = NARRATION_BURST_MAX_WORDS,
    max_gap_sec: float = 0.5,
) -> list[NarrationBurst]:
    """Group narration words into strict single-line bursts of 1-3 words max.

    If `words` (Whisper word timestamps) are provided, uses their exact millisecond timings.
    Otherwise, splits `section_text` into words and estimates proportional timings across [start, end].
    """
    raw_words: list[dict[str, Any]] = []
    if words:
        for w in words:
            word_str = str(w.get("word", "")).strip()
            if not word_str:
                continue
            w_start = float(w.get("start", start))
            w_end = float(w.get("end", max(w_start + 0.1, end)))
            raw_words.append({"word": word_str, "start": w_start, "end": w_end})
    else:
        text_words = [w.strip() for w in section_text.split() if w.strip()]
        if not text_words:
            return []
        total_chars = sum(len(w) for w in text_words)
        dur = max(0.2, end - start)
        cursor = start
        for w in text_words:
            fraction = len(w) / max(1, total_chars)
            w_dur = max(0.12, dur * fraction)
            w_end = min(end, cursor + w_dur)
            raw_words.append({"word": w, "start": cursor, "end": w_end})
            cursor = w_end

    if not raw_words:
        return []

    bursts: list[NarrationBurst] = []
    current_words: list[dict[str, Any]] = []

    for i, w in enumerate(raw_words):
        current_words.append(w)
        w_text = w["word"]

        ends_terminal = w_text.endswith((".", "?", "!"))
        ends_clause = w_text.endswith((",", ";", ":", "-", "—"))

        has_gap = False
        if i + 1 < len(raw_words):
            next_start = raw_words[i + 1]["start"]
            if (next_start - w["end"]) >= max_gap_sec:
                has_gap = True

        count = len(current_words)
        split = False
        if count >= max_words:
            split = True
        elif ends_terminal or ends_clause or has_gap:
            split = True

        if split or (i == len(raw_words) - 1):
            burst_start = current_words[0]["start"]
            burst_end = current_words[-1]["end"]
            if i + 1 < len(raw_words):
                burst_end = min(raw_words[i + 1]["start"], max(burst_end, burst_start + 0.2))
            else:
                burst_end = max(end, burst_end)

            bursts.append(
                NarrationBurst(
                    words=list(current_words),
                    text=" ".join(cw["word"] for cw in current_words),
                    start=burst_start,
                    end=burst_end,
                )
            )
            current_words = []

    return bursts


def _default_caption_y(img: Image.Image) -> int:
    """Default caption anchor (top edge of the burst line) for this frame's orientation."""
    if img.height == 1920 or img.height > img.width:
        return SHORTS_SAFE_ZONE_Y_DEFAULT
    return HORIZONTAL_SAFE_ZONE_Y


def _active_word_index(burst: NarrationBurst, t: float) -> int:
    """Index of the word whose highlight is live at ``t``.

    A word becomes active at its own Whisper ``start`` and stays active until the next word
    starts. Before the first word the first word is used, so its spring has not begun yet.
    """
    active_idx = 0
    for idx, w in enumerate(burst.words):
        if w["start"] <= t <= w["end"]:
            return idx
        if t > w["end"]:
            active_idx = idx
    return active_idx


def draw_karaoke_burst(
    img: Image.Image,
    burst: NarrationBurst,
    t: float,
    *,
    y: int | None = None,
    font_size: int = 68,
    active_box: bool = True,
    active_color: tuple[int, int, int] = KARAOKE_ACTIVE_COLOR,
    emphasis_color: tuple[int, int, int] = KARAOKE_EMPHASIS_COLOR,
    inactive_color: tuple[int, int, int] = KARAOKE_INACTIVE_COLOR,
    stroke_color: tuple[int, int, int] = KARAOKE_STROKE_COLOR,
    stroke_width: int = KARAOKE_STROKE_WIDTH,
) -> None:
    """Render a single-line burst of 1-3 words with karaoke spring highlight and high-contrast active box."""
    if not burst.words or t < burst.start or t > burst.end:
        return

    # Anchor: Shorts Y=1250 on a vertical 1080x1920 frame, horizontal Y=880 otherwise.
    if y is None:
        target_y = _default_caption_y(img)
    else:
        target_y = y

    active_idx = _active_word_index(burst, t)
    active_word = burst.words[active_idx]
    tau = max(0.0, t - active_word["start"])
    active_scale = _spring_scale(tau)

    base_font = _font(font_size)
    scaled_font = _font(max(8, round(font_size * active_scale)))
    space_w = _text_width(" ", base_font)

    # Compute word widths
    word_widths: list[float] = []
    for idx, w in enumerate(burst.words):
        f = scaled_font if idx == active_idx else base_font
        word_widths.append(_text_width(w["word"], f))

    total_w = sum(word_widths) + space_w * (len(burst.words) - 1)
    line_h = int(font_size * 1.3)
    start_x = (img.width - total_w) / 2.0

    # Draw high-contrast active backing box / pill
    if active_box:
        pad_x = 22
        pad_y = 12
        box_x0 = round(start_x - pad_x)
        box_y0 = round(target_y - pad_y)
        box_x1 = round(start_x + total_w + pad_x)
        box_y1 = round(target_y + line_h + pad_y)

        box_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        box_draw = ImageDraw.Draw(box_layer)
        box_draw.rounded_rectangle(
            [box_x0, box_y0, box_x1, box_y1],
            radius=16,
            fill=KARAOKE_BOX_FILL,
            outline=KARAOKE_BOX_BORDER,
            width=2,
        )
        _blit(img, box_layer, 0, 0)

    # Render individual words
    cur_x = start_x
    for idx, w in enumerate(burst.words):
        w_text = w["word"]
        is_active = idx == active_idx
        w_font = scaled_font if is_active else base_font
        w_width = word_widths[idx]

        if is_active:
            plain = _plain_word(w_text).upper()
            is_emp = plain in HIGHLIGHT_WORDS or w_text.endswith("!")
            fill_rgb = emphasis_color if is_emp else active_color
            strk_w = stroke_width + 1
        else:
            fill_rgb = inactive_color
            strk_w = max(2, stroke_width - 1)

        ink = _ink_layer(
            w_text,
            w_font,
            (*fill_rgb, 255),
            stroke_width=strk_w,
            stroke_fill=(*stroke_color, 255),
        )

        ink_w = ink.width - 2 * _PAD
        ink_h = ink.height - 2 * _PAD
        glyph_y = target_y + (line_h - ink_h) / 2.0
        glyph_x = cur_x + (w_width - ink_w) / 2.0

        _blit(img, ink, round(glyph_x - _PAD), round(glyph_y - _PAD))
        cur_x += w_width + space_w


def _draw_prop(
    img: Image.Image,
    prop: PropSpec,
    t: float,
    start: float,
    words: Sequence[Mapping[str, Any]] | None,
) -> None:
    """Paste the prop's sprite once it has entered, otherwise draw it procedurally."""
    entrance_start = _entrance_start(prop.name, words, start)
    sprite_name = PROP_SPRITES.get(prop.name)
    if sprite_name is not None and entrance_start <= t:
        size = sprite_size(sprite_name, prop.scale)
        if size is not None:
            centre = (prop.x + size[0] / 2, prop.y + size[1] / 2)
            if paste_sprite(img, sprite_name, centre, scale=prop.scale):
                return
    try:
        draw_prop(
            img,
            prop.name,
            (prop.x, prop.y),
            scale=prop.scale,
            t=t,
            entrance_start=entrance_start,
            entrance=PROP_ENTRANCE_KIND.get(prop.name, "pop"),
            wiggle=PROP_WIGGLE.get(prop.name, 0.0),
            **prop.params,
        )
    except ValueError:
        # Fallback if prop drawer is not yet registered (e.g. fridge_open falling back to fridge)
        fallback_name = "fridge" if prop.name == "fridge_open" else None
        if fallback_name is not None:
            draw_prop(
                img,
                fallback_name,
                (prop.x, prop.y),
                scale=prop.scale,
                t=t,
                entrance_start=entrance_start,
                entrance=PROP_ENTRANCE_KIND.get(prop.name, "pop"),
                wiggle=PROP_WIGGLE.get(prop.name, 0.0),
                **prop.params,
            )


def _draw_figure_base(
    img: Image.Image,
    plan: BeatPlan,
    joints: dict[str, float],
    origin: tuple[float, float],
    scale: float,
) -> None:
    """Yellow disc the standing figure stands on, as in the reference. Seated and lying figures get none."""
    if plan.pose in NO_BASE_POSES:
        return
    cx = origin[0]
    cy = _feet_y(joints, origin[1], scale) + FIGURE_BASE_DROP_PX
    rx, ry = FIGURE_BASE_RX_PX, FIGURE_BASE_RY_PX
    ImageDraw.Draw(img).ellipse(
        (cx - rx, cy - ry, cx + rx, cy + ry),
        fill=FIGURE_BASE_YELLOW,
        outline=FIGURE_BASE_EDGE,
        width=4,
    )


def _draw_sprite(img: Image.Image, spec: SpriteSpec) -> None:
    """Paste a keyword sprite scaled to SPRITE_HEIGHT_PX tall; a missing sprite draws nothing."""
    native = sprite_size(spec.name)
    if native is None:
        return
    paste_sprite(img, spec.name, (spec.x, spec.y), scale=SPRITE_HEIGHT_PX / native[1])


def _sprite_draw_size(name: str) -> tuple[float, float] | None:
    """Output size of a keyword sprite drawn SPRITE_HEIGHT_PX tall, or None when it is missing."""
    native = sprite_size(name)
    if native is None:
        return None
    return native[0] * SPRITE_HEIGHT_PX / native[1], float(SPRITE_HEIGHT_PX)


def _box_at(cx: float, cy: float, size: tuple[float, float]) -> Box:
    width, height = size
    return (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)


def _overlaps(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _in_margins(box: Box) -> bool:
    return (
        box[0] >= SPRITE_MARGIN_X[0]
        and box[2] <= SPRITE_MARGIN_X[1]
        and box[1] >= 0
        and box[3] <= FRAME_H
    )


def _sprite_candidates(spec: SpriteSpec) -> list[tuple[float, float]]:
    """Up to SPRITE_CANDIDATES centres to try, the preferred one first.

    Other classes shift x in SPRITE_SHIFT_X steps. Sky sprites also move up and down inside
    SKY_BAND_Y.
    """
    if _sprite_class(spec.name) == "sky":
        steps = ((0, 0), (1, 0), (-1, 0), (0, -1), (0, 1), (1, -1))
        low, high = SKY_BAND_Y
        candidates = [
            (spec.x + dx * SKY_STEP_X, min(max(spec.y + dy * SKY_STEP_Y, low), high))
            for dx, dy in steps
        ]
    else:
        candidates = [(spec.x + dx, spec.y) for dx in SPRITE_SHIFT_X]
    return candidates[:SPRITE_CANDIDATES]


def _prop_box(prop: PropSpec) -> Box | None:
    """Frame box of a prop drawn from its sprite, or None when the box is not known."""
    sprite_name = PROP_SPRITES.get(prop.name)
    if sprite_name is None:
        return None
    size = sprite_size(sprite_name, prop.scale)
    if size is None:
        return None
    return (prop.x, prop.y, prop.x + size[0], prop.y + size[1])


def _drawn_prop_boxes(
    plan: BeatPlan, shot_start: float, words: Sequence[Mapping[str, Any]] | None, t: float
) -> list[Box]:
    """Boxes of the props that have entered by ``t`` and have a known size."""
    boxes: list[Box] = []
    for prop in plan.props:
        if _entrance_start(prop.name, words, shot_start) > t:
            continue
        box = _prop_box(prop)
        if box is not None:
            boxes.append(box)
    return boxes


def _figure_box(joints: dict[str, float], origin: tuple[float, float], scale: float) -> Box:
    """Box around the figure: its arms-wide width, from the highest point down to the feet."""
    origin_x, origin_y = origin
    return (
        origin_x - FIGURE_HALF_WIDTH_PX,
        _highest_figure_y(joints, origin_y, scale),
        origin_x + FIGURE_HALF_WIDTH_PX,
        _feet_y(joints, origin_y, scale),
    )


def _place_sprites(plan: BeatPlan, figure_box: Box, prop_boxes: Sequence[Box]) -> list[SpriteSpec]:
    """Final centres for the plan's sprites, in narration order.

    A sprite takes the first candidate that stays inside the frame margins and does not overlap
    the figure, a drawn prop or an already placed sprite. A sprite with no free candidate is
    skipped, and a missing sprite file is skipped as it is at draw time.
    """
    placed: list[Box] = []
    result: list[SpriteSpec] = []
    for spec in plan.sprites:
        size = _sprite_draw_size(spec.name)
        if size is None:
            continue
        chosen: tuple[float, float, Box] | None = None
        for cx, cy in _sprite_candidates(spec):
            box = _box_at(cx, cy, size)
            if not _in_margins(box):
                continue
            blockers = (figure_box, *prop_boxes, *placed)
            if any(_overlaps(box, other) for other in blockers):
                continue
            chosen = (cx, cy, box)
            break
        if chosen is None:
            _LOG.info("No free position for sprite %r; skipped for this section", spec.name)
            continue
        cx, cy, box = chosen
        placed.append(box)
        result.append(SpriteSpec(spec.name, round(cx), round(cy)))
    return result


def _figure_placement(
    plan: BeatPlan, shot_start: float, section_text: str, t: float
) -> tuple[dict[str, float], tuple[float, float], float, float]:
    """Joints, origin, sprite scale and rig scale for the figure at ``t``."""
    joints = _figure_joints(plan, shot_start, section_text, t)
    if plan.pose in LYING_POSES:
        return joints, LYING_HIP, BASE_SPRITE_SCALE, BASE_RIG_SCALE
    origin = _lift_origin(joints, RIG_ORIGIN, FIGURE_SPRITE_SCALE)
    return joints, origin, FIGURE_SPRITE_SCALE, RIG_SCALE


def _sprite_layout(
    plan: BeatPlan,
    shot_start: float,
    section_text: str,
    t: float,
    words: Sequence[Mapping[str, Any]] | None = None,
) -> list[SpriteSpec]:
    """The sprites ``draw_beat`` pastes at ``t``, at their collision-free centres."""
    joints, origin, sprite_scale, _ = _figure_placement(plan, shot_start, section_text, t)
    figure_box = _figure_box(joints, origin, sprite_scale)
    prop_boxes = _drawn_prop_boxes(plan, shot_start, words, t)
    return _place_sprites(plan, figure_box, prop_boxes)


def _figure_joints(
    plan: BeatPlan, shot_start: float, section_text: str, t: float
) -> dict[str, float]:
    """Joint angles for the satori figure: the planned pose plus the head turn.

    The arms come only from the pose, so no shot raises a hand by default.
    """
    joints = dict(_rig_pose(plan, shot_start, t))
    joints["head_turn"] = _head_turn(section_text)
    return joints


def _figure_drop_px(joints: dict[str, float], scale: float) -> float:
    return joints.get("drop", 0.0) * CANVAS_PX_PER_RIG_UNIT * scale


def _highest_figure_y(joints: dict[str, float], origin_y: float, scale: float) -> float:
    """Output y of the figure's highest point: the head top or a raised wrist."""
    head = origin_y + _figure_drop_px(joints, scale) + (HEAD_TOP_CANVAS_Y - HIP[1]) * scale
    # The fist sits past the wrist along the forearm, so the hand's top is HAND_REACH_CANVAS_PX above it.
    hands = [
        origin_y + wrist_offset(joints, scale, side)[1] - HAND_REACH_CANVAS_PX * scale
        for side in ("l", "r")
    ]
    return min(head, *hands)


def _feet_y(joints: dict[str, float], origin_y: float, scale: float) -> float:
    return origin_y + _figure_drop_px(joints, scale) + (ANKLE["l"][1] - HIP[1]) * scale


def _lift_origin(
    joints: dict[str, float], origin: tuple[float, float], scale: float
) -> tuple[float, float]:
    """Lower a standing figure so its highest point sits at FIGURE_TOP_MIN_Y.

    The shift is clamped so the feet never go below FLOOR_Y, so a figure already standing on
    the floor is not moved.
    """
    origin_x, origin_y = origin
    top_gap = FIGURE_TOP_MIN_Y - _highest_figure_y(joints, origin_y, scale)
    floor_gap = FLOOR_Y - _feet_y(joints, origin_y, scale)
    shift = min(max(0.0, top_gap), max(0.0, floor_gap))
    return (origin_x, origin_y + shift)


def _draw_space(img: Image.Image, t: float, blue: RGB, highlight: RGB) -> None:
    """A spinning planet with two satellites on a tilted orbit."""
    draw_sphere(
        img,
        SPHERE_CENTRE,
        SPHERE_RADIUS,
        t,
        base=blue,
        land=SPHERE_LAND,
        spin_deg_per_s=SPHERE_SPIN_DEG_S,
    )
    draw = ImageDraw.Draw(img)
    ring = [
        orbit_point(SPHERE_CENTRE, ORBIT_RADIUS, angle, ORBIT_TILT) for angle in range(0, 361, 6)
    ]
    draw.line(ring, fill=BG_SURFACE, width=2, joint="curve")
    for offset, colour in ((0.0, highlight), (180.0, blue)):
        angle = SATELLITE_SPEED_DEG_S * t + offset
        x, y = orbit_point(SPHERE_CENTRE, ORBIT_RADIUS, angle, ORBIT_TILT)
        r = SATELLITE_RADIUS
        draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)


def _fallback_draw_speed_lines(
    img: Image.Image,
    t: float,
    direction: str = "right",
    count: int = 16,
    color: tuple[int, int, int] = (234, 230, 223),
) -> None:
    draw = ImageDraw.Draw(img)
    for i in range(count):
        phase = (t * 5.0 + i * 0.37) % 1.0
        if direction == "diagonal":
            x0 = int(200 + (i / max(1, count)) * 1400 + phase * 60)
            y0 = int(200 + phase * 100 + (i % 5) * 120)
            length = 80 + (i % 3) * 30
            x1 = x0 + length
            y1 = y0 - length
            draw.line([(x0, y0), (x1, y1)], fill=color, width=2)
        else:
            y = int(150 + (i / max(1, count)) * 750 + ((i * 37) % 40))
            x0 = int(300 + phase * 400 + (i % 4) * 200)
            x1 = x0 + 100 + (i % 3) * 50
            draw.line([(x0, y), (x1, y)], fill=color, width=2)


def _fallback_draw_hit_sparks(
    img: Image.Image,
    center: tuple[int, int],
    t: float,
    start: float = 0.0,
    duration: float = 0.4,
    color: tuple[int, int, int] = (245, 197, 24),
) -> None:
    if not (start <= t <= start + duration):
        return
    tau = t - start
    k = tau / duration if duration > 0 else 0.0
    r_inner = 15.0 + k * 40.0
    r_outer = 35.0 + k * 70.0
    draw = ImageDraw.Draw(img)
    cx, cy = center
    num_sparks = 8
    for i in range(num_sparks):
        angle = (i * 2 * math.pi / num_sparks) + 0.3 * math.sin(t * 10)
        p0 = (cx + r_inner * math.cos(angle), cy + r_inner * math.sin(angle))
        p1 = (cx + r_outer * math.cos(angle), cy + r_outer * math.sin(angle))
        draw.line([p0, p1], fill=color, width=3)


def _fallback_draw_sleep_zs(
    img: Image.Image,
    anchor: tuple[int, int],
    t: float,
    color: tuple[int, int, int] = (234, 230, 223),
) -> None:
    draw = ImageDraw.Draw(img)
    cx, cy = anchor
    for i in range(3):
        phase = (t * 0.8 + i * 0.4) % 1.2
        if phase > 1.0:
            continue
        scale = 0.7 + i * 0.3
        x = cx + math.sin(phase * 3.0 + i) * 20.0 + i * 25.0
        y = cy - phase * 90.0 - i * 30.0
        size = 12 * scale
        draw.line([(x, y), (x + size, y)], fill=color, width=2)
        draw.line([(x + size, y), (x, y + size)], fill=color, width=2)
        draw.line([(x, y + size), (x + size, y + size)], fill=color, width=2)


def draw_beat_text(
    img: Image.Image,
    plan: BeatPlan,
    t: float,
    start: float,
    end: float,
    section_text: str,
    words: Sequence[Mapping[str, Any]] | None = None,
    *,
    safe_zone_y: int | None = None,
    active_box: bool = True,
) -> None:
    """Draw the text layer for time ``t``: narration line, inner voice, shatter text and number pops.

    Kept apart from :func:`draw_beat` so the caller can paint it after the camera. Text
    drawn before a zoom would drift toward the frame edge and into the letterbox bars.
    """
    if section_text.strip():
        _draw_narration(
            img,
            section_text,
            t,
            start,
            end,
            words=words,
            safe_zone_y=safe_zone_y,
            active_box=active_box,
        )

    if plan.shatter_text:
        impact_t = start + max(0.35, (end - start) * 0.62)
        if t < impact_t:
            if not plan.inner_voice:
                draw_inner_voice(
                    img,
                    plan.shatter_text,
                    (960, 200),
                    t,
                    start,
                    end,
                    color=PALETTE.inner_voice,
                )
            else:
                draw_inner_voice(
                    img,
                    plan.inner_voice,
                    INNER_VOICE_ANCHOR,
                    t,
                    start,
                    end,
                    color=PALETTE.inner_voice,
                )
        else:
            draw_shatter_text(
                img,
                plan.shatter_text,
                (960, 200),
                t,
                impact_t,
                duration=0.65,
            )
    elif plan.inner_voice:
        draw_inner_voice(
            img, plan.inner_voice, INNER_VOICE_ANCHOR, t, start, end, color=PALETTE.inner_voice
        )

    _draw_number_pops(img, plan.number_values, t, start, end)


def draw_beat(
    img: Image.Image,
    plan: BeatPlan,
    t: float,
    start: float,
    end: float,
    section_text: str,
    words: Sequence[Mapping[str, Any]] | None = None,
    *,
    text: bool = True,
    draw_background: bool = True,
) -> None:
    """Draw the full scene for one shot time ``t`` onto ``img`` in place.

    ``start`` and ``end`` are the section's absolute times in seconds. ``words`` is an
    optional sequence of ``{"word", "start", "end"}`` mappings in absolute seconds; it
    sets when named props enter. The narration line is drawn from ``section_text``.
    ``text=False`` leaves the text layer to :func:`draw_beat_text`.
    ``draw_background=False`` preserves any pre-painted dynamic procedural background.
    """
    if img.mode != "RGB" or img.size != (FRAME_W, FRAME_H):
        raise ValueError(
            f"draw_beat needs an RGB {FRAME_W}x{FRAME_H} image, got {img.mode} {img.size}"
        )

    pal = PALETTE
    # The navy default keeps its backdrop sprite, parallax and dust included. Other themes
    # are painted procedurally.
    if draw_background:
        if plan.theme != NIGHT_THEME or not paste_sprite(
            img, BACKDROP_SPRITE, (FRAME_W / 2, FRAME_H / 2)
        ):
            draw_themed_background(img, plan.theme, t, camera_x=PARALLAX_CAMERA_SPEED * t)
    ImageDraw.Draw(img).line([(0, FLOOR_Y), (FRAME_W, FLOOR_Y)], fill=pal.floor, width=2)

    for prop in plan.props:
        _draw_prop(img, prop, t, start, words)
    # Sprites go after the props so they can avoid them, and before the figure is drawn.
    for sprite in _sprite_layout(plan, start, section_text, t, words):
        _draw_sprite(img, sprite)

    if _is_space_topic(section_text):
        _draw_space(img, t, pal.blue, pal.highlight)

    joints, origin, sprite_scale, rig_scale = _figure_placement(plan, start, section_text, t)
    _draw_figure_base(img, plan, joints, origin, sprite_scale)
    if not draw_sprite_rig(
        img,
        joints,
        origin,
        sprite_scale,
        expression=plan.expression,
        t=t,
        mirror=plan.mirror,
    ):
        draw_rig(
            img,
            _rig_pose(plan, start, t),
            origin,
            rig_scale,
            expression=plan.expression,
            headband=plan.headband,
            t=t,
            mirror=plan.mirror,
            head_turn=_head_turn(section_text),
        )

    # Action FX & Sleep Z's
    if plan.pose in ("box_punch", "climb_slope"):
        direction = "diagonal" if plan.pose == "climb_slope" else "right"
        speed_fn = getattr(_fx_mod, "draw_speed_lines", _fallback_draw_speed_lines)
        speed_fn(img, t, direction=direction, count=16, color=(234, 230, 223))

    if plan.pose == "box_punch" and any(prop.name == "heavy_bag" for prop in plan.props):
        spark_fn = getattr(_fx_mod, "draw_hit_sparks", _fallback_draw_hit_sparks)
        spark_fn(
            img,
            (1320, 480),
            t,
            start=start + 0.15,
            duration=max(0.4, end - start),
            color=(245, 197, 24),
        )

    if plan.pose in LYING_POSES:
        zs_fn = getattr(_fx_mod, "draw_sleep_zs", _fallback_draw_sleep_zs)
        zs_fn(img, (RIG_ORIGIN[0] - 80, RIG_ORIGIN[1] - 110), t, color=(234, 230, 223))

    for src in plan.light_sources:
        source_glow(img, src.center, src.radius, src.color, src.intensity)

    if plan.mood is not None:
        colour_wash(img, plan.mood, MOOD_WASH_INTENSITY)

    if text:
        draw_beat_text(img, plan, t, start, end, section_text, words=words)


__all__ = [
    "BeatPalette",
    "BeatPlan",
    "FRIDGE_WORDS",
    "LightSource",
    "MAX_PROPS",
    "MIRROR_WORDS",
    "NarrationBurst",
    "PALETTE",
    "PropPlacement",
    "PropSpec",
    "SHORTS_SAFE_ZONE_Y_DEFAULT",
    "SHORTS_SAFE_ZONE_Y_MAX",
    "SHORTS_SAFE_ZONE_Y_MIN",
    "beat_palette",
    "chunk_narration_bursts",
    "draw_beat",
    "draw_beat_text",
    "draw_karaoke_burst",
    "plan_beat",
]
