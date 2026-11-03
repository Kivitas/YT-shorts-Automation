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

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from PIL import Image, ImageDraw

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
from autoshorts.longform.kinetic_text import draw_inner_voice, draw_kinetic_line, draw_number_pop
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
INNER_VOICE_ANCHOR = (140, 420)
NUMBER_ANCHOR = (1500, 520)

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
}
MAX_PROPS = 4  # keeps each frame readable; earlier rules win when the cap cuts props
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


def _has_prop(props: Sequence[PropSpec], name: str) -> bool:
    return any(prop.name == name for prop in props)


def _add_prop(props: list[PropSpec], name: str) -> None:
    """Add the prop at its fixed position unless the section already has it."""
    if not _has_prop(props, name):
        x, y = PROP_XY[name]
        props.append(PropSpec(name, x, y, 1.0, {}))


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
    tokens = _WORD_RE.findall(lowered)
    words = set(tokens)

    props: list[PropSpec] = []
    light_sources: list[LightSource] = []
    pose: str | None = None
    mood: RGB | None = None
    inner_voice: str | None = None

    if words & BED_WORDS:
        props.append(PropSpec("bed", *BED_XY, BED_SCALE, {}))
        props.append(PropSpec("alarm_clock_7seg", 600, 600, 0.5, {}))
    if words & PHONE_WORDS:
        props.append(PropSpec("phone", 1420, 560, 1.0, {}))
        light_sources.append(LightSource((1450, 600), 260.0, PALETTE.blue, 0.8))
    if words & COUCH_WORDS:
        props.append(PropSpec("couch", 1380, 600, 1.0, {}))
        pose = pose or "slump_couch"
    if words & DESK_WORDS:
        desk_x = RIG_ORIGIN[0] + _HAND_OFFSET[0] + DESK_MONITOR_OFFSET[0]
        desk_y = RIG_ORIGIN[1] + _HAND_OFFSET[1] + DESK_MONITOR_OFFSET[1]
        props.append(PropSpec("desk_monitor", round(desk_x), round(desk_y), DESK_MONITOR_SCALE, {}))
    if words & BAG_WORDS:
        props.append(PropSpec("heavy_bag", 1460, 380, 1.0, {}))
        pose = pose or "box_punch"
    if words & MOUNTAIN_WORDS:
        props.append(PropSpec("mountain", 60, 380, 1.0, {}))
        pose = pose or ("climb_slope" if "climb" in words else "summit_stand")
    if words & SHADOW_WORDS:
        props.append(PropSpec("shadow_demon", 1480, 380, 1.0, {}))
        mood = PALETTE.red
        for token in tokens:
            if token in EXCUSE_LINES:
                inner_voice = EXCUSE_LINES[token]
                break
    if words & CALENDAR_WORDS or CALENDAR_PHRASE_RE.search(lowered):
        marks = sum(1 for token in tokens if token in DAY_WORDS)
        props.append(PropSpec("calendar", 1380, 300, 1.0, {"marks": marks}))

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
        props.append(PropSpec("window", 80, 340, 1.0, {}))

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
        props.insert(0, PropSpec("bed", *BED_XY, BED_SCALE, {}))

    del props[MAX_PROPS:]

    if mood_expression is not None:
        expression = mood_expression
    elif is_last:
        expression = "determined"
    else:
        expression = "exhausted" if section_index % 2 == 0 else "grit"

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
    img: Image.Image, section_text: str, t: float, start: float, end: float
) -> None:
    """Show the sentence that is current at ``t``, each sentence taking an equal slice of the section."""
    sentences = _split_sentences(section_text)
    slot = (end - start) / len(sentences) if end > start else 0.0
    last = len(sentences) - 1
    index = last if slot <= 0 else min(last, max(0, int((t - start) // slot)))
    sentence_start = start + index * slot
    items = sentences[index].split()
    highlight = [i for i, word in enumerate(items) if _plain_word(word) in HIGHLIGHT_WORDS]
    draw_kinetic_line(
        img,
        items,
        t,
        sentence_start,
        end,
        y=NARRATION_TOP,
        color=PALETTE.text,
        highlight_indices=highlight,
        highlight_color=PALETTE.highlight,
        max_per_line=NARRATION_MAX_PER_LINE,
        font_size=_narration_font_size(sentences),
        uppercase=False,
    )


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


def draw_beat(
    img: Image.Image,
    plan: BeatPlan,
    t: float,
    start: float,
    end: float,
    section_text: str,
    words: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """Draw the full scene for one shot time ``t`` onto ``img`` in place.

    ``start`` and ``end`` are the section's absolute times in seconds. ``words`` is an
    optional sequence of ``{"word", "start", "end"}`` mappings in absolute seconds; it
    sets when named props enter. The narration line is drawn from ``section_text``.
    """
    if img.mode != "RGB" or img.size != (FRAME_W, FRAME_H):
        raise ValueError(
            f"draw_beat needs an RGB {FRAME_W}x{FRAME_H} image, got {img.mode} {img.size}"
        )

    pal = PALETTE
    # The navy default keeps its backdrop sprite, parallax and dust included. Other themes
    # are painted procedurally.
    if plan.theme != NIGHT_THEME or not paste_sprite(
        img, BACKDROP_SPRITE, (FRAME_W / 2, FRAME_H / 2)
    ):
        draw_themed_background(img, plan.theme, t, camera_x=PARALLAX_CAMERA_SPEED * t)
    ImageDraw.Draw(img).line([(0, FLOOR_Y), (FRAME_W, FLOOR_Y)], fill=pal.floor, width=2)

    for prop in plan.props:
        _draw_prop(img, prop, t, start, words)

    if _is_space_topic(section_text):
        _draw_space(img, t, pal.blue, pal.highlight)

    joints = _figure_joints(plan, start, section_text, t)
    if plan.pose in LYING_POSES:
        origin, sprite_scale, rig_scale = LYING_HIP, BASE_SPRITE_SCALE, BASE_RIG_SCALE
    else:
        origin = _lift_origin(joints, RIG_ORIGIN, FIGURE_SPRITE_SCALE)
        sprite_scale, rig_scale = FIGURE_SPRITE_SCALE, RIG_SCALE
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

    for src in plan.light_sources:
        source_glow(img, src.center, src.radius, src.color, src.intensity)

    if plan.mood is not None:
        colour_wash(img, plan.mood, MOOD_WASH_INTENSITY)

    if section_text.strip():
        _draw_narration(img, section_text, t, start, end)
    if plan.inner_voice:
        draw_inner_voice(
            img, plan.inner_voice, INNER_VOICE_ANCHOR, t, start, end, color=pal.inner_voice
        )
    _draw_number_pops(img, plan.number_values, t, start, end)


__all__ = [
    "BeatPalette",
    "BeatPlan",
    "LightSource",
    "MAX_PROPS",
    "PALETTE",
    "PropSpec",
    "beat_palette",
    "draw_beat",
    "plan_beat",
]
