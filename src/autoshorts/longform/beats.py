"""Per-section visual beats for long-form explainer videos.

``plan_beat`` turns one narration section into a :class:`BeatPlan` (figure pose,
gesture, expression, props, light sources, inner voice and number pops). The rules are
plain keyword matches on the lower-cased narration, so the same inputs always give
the same plan. ``draw_beat`` renders that plan for one shot time ``t`` onto a
1920x1080 RGB frame. The backdrop (sky, silhouettes, dust and vignette) comes from
:func:`autoshorts.longform.background.draw_background`.

The ``heading`` and ``speaker`` arguments are accepted so callers can pass the full
section context, but the current rules only read the narration.
"""

from __future__ import annotations

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
from autoshorts.longform.background import draw_background
from autoshorts.longform.kinetic_text import draw_inner_voice, draw_kinetic_line, draw_number_pop
from autoshorts.longform.lighting import colour_wash, source_glow
from autoshorts.longform.props import draw_prop
from autoshorts.longform.rig import animate_pose, draw_rig
from autoshorts.longform.sphere import draw_sphere, orbit_point

RGB = tuple[int, int, int]

FRAME_W = 1920
FRAME_H = 1080
FLOOR_Y = 760
RIG_SCALE = 1.5
RIG_ORIGIN = (1000, 598)  # hip height so the feet land on FLOOR_Y (108 * 1.5 = 162 px leg)
MOOD_WASH_INTENSITY = 0.08
NARRATION_Y = 180
NARRATION_FONT_SIZE = 66
NARRATION_MAX_PER_LINE = 5
INNER_VOICE_ANCHOR = (140, 420)
NUMBER_ANCHOR = (1500, 520)

REST_POSE = "stand"
POSE_TRANSITION_S = 0.4  # the figure eases from REST_POSE into the planned pose after a shot start
GESTURE_HAND_LIFT_S = 0.3  # the raised hand ramps from 0 to 1 over this long after a section start
HEAD_TURN_MAGNITUDE = 0.5

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
CALENDAR_WORDS = frozenset({"every", "day", "daily"})
DAY_WORDS = frozenset({"day", "daily"})

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
    "calendar": CALENDAR_WORDS,
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


def _choose_gesture(words: set[str], lowered: str) -> str | None:
    """Pick a gesture from the narration, or None to keep the pose cycle."""
    if words & GESTURE_POINT_WORDS:
        return "gesture_point_up"
    if words & GESTURE_OPEN_WORDS:
        return "gesture_open_hands"
    if words & GESTURE_SHRUG_WORDS or any(phrase in lowered for phrase in GESTURE_SHRUG_PHRASES):
        return "gesture_shrug"
    return None


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
        props.append(PropSpec("bed", 60, 560, 1.0, {}))
        props.append(PropSpec("alarm_clock_7seg", 330, 470, 1.0, {}))
    if words & PHONE_WORDS:
        props.append(PropSpec("phone", 1420, 560, 1.0, {}))
        light_sources.append(LightSource((1450, 600), 260.0, PALETTE.blue, 0.8))
    if words & COUCH_WORDS:
        props.append(PropSpec("couch", 1380, 600, 1.0, {}))
        pose = pose or "slump_couch"
    if words & DESK_WORDS:
        props.append(PropSpec("desk_monitor", 1380, 420, 1.0, {}))
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
    if words & CALENDAR_WORDS:
        marks = sum(1 for token in tokens if token in DAY_WORDS)
        props.append(PropSpec("calendar", 1380, 300, 1.0, {"marks": marks}))

    if not props:
        props.append(PropSpec("window", 80, 340, 1.0, {}))

    if pose is None and words & LOOK_WORDS:
        pose = "look_right" if section_index % 2 == 0 else "look_left"

    if pose is None:
        pose = POSE_CYCLE[section_index % len(POSE_CYCLE)]

    expression = "determined" if is_last else ("exhausted" if section_index % 2 == 0 else "grit")

    return BeatPlan(
        section_index=section_index,
        pose=pose,
        expression=expression,
        mirror=section_index % 2 == 1,
        headband=is_last,
        props=props,
        inner_voice=inner_voice,
        number_values=_NUMBER_RE.findall(narration),
        mood=mood,
        light_sources=light_sources,
        gesture=_choose_gesture(words, lowered),
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


def _hand_lift(t: float, start: float) -> float:
    """Raised-hand amount in 0..1, ramping up over the first ``GESTURE_HAND_LIFT_S`` seconds."""
    return min(1.0, max(0.0, (t - start) / GESTURE_HAND_LIFT_S))


def _is_space_topic(section_text: str) -> bool:
    return bool(SPACE_WORDS.intersection(_WORD_RE.findall(section_text.lower())))


def _split_sentences(text: str) -> list[str]:
    """Split narration into sentences, keeping at least one (possibly empty) entry."""
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text.strip()) if part.strip()]
    return parts or [text.strip()]


def _plain_word(word: str) -> str:
    return re.sub(r"[^a-z]", "", word.lower())


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
        y=NARRATION_Y,
        color=PALETTE.text,
        highlight_indices=highlight,
        highlight_color=PALETTE.highlight,
        max_per_line=NARRATION_MAX_PER_LINE,
        font_size=NARRATION_FONT_SIZE,
        uppercase=False,
    )


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
    draw_background(img, t, camera_x=PARALLAX_CAMERA_SPEED * t)
    ImageDraw.Draw(img).line([(0, FLOOR_Y), (FRAME_W, FLOOR_Y)], fill=pal.floor, width=2)

    for prop in plan.props:
        draw_prop(
            img,
            prop.name,
            (prop.x, prop.y),
            scale=prop.scale,
            t=t,
            entrance_start=_entrance_start(prop.name, words, start),
            entrance=PROP_ENTRANCE_KIND.get(prop.name, "pop"),
            wiggle=PROP_WIGGLE.get(prop.name, 0.0),
            **prop.params,
        )

    if _is_space_topic(section_text):
        _draw_space(img, t, pal.blue, pal.highlight)

    draw_rig(
        img,
        _rig_pose(plan, start, t),
        RIG_ORIGIN,
        RIG_SCALE,
        expression=plan.expression,
        headband=plan.headband,
        t=t,
        mirror=plan.mirror,
        head_turn=_head_turn(section_text),
        hand_lift=_hand_lift(t, start),
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
    "PALETTE",
    "PropSpec",
    "beat_palette",
    "draw_beat",
    "plan_beat",
]
