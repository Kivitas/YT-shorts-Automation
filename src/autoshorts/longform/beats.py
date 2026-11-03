"""Per-section visual beats for long-form explainer videos.

``plan_beat`` turns one narration section into a :class:`BeatPlan` (figure pose,
expression, props, light sources, inner voice and number pops). The rules are
plain keyword matches on the lower-cased narration, so the same inputs always give
the same plan. ``draw_beat`` renders that plan for one shot time ``t`` onto a
1920x1080 RGB frame.

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
    ACCENT_YELLOW,
    BG_SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
)
from autoshorts.longform.fx import draw_dust_motes, draw_vignette
from autoshorts.longform.kinetic_text import draw_inner_voice, draw_narration, draw_number_pop
from autoshorts.longform.lighting import colour_wash, rim_light, source_glow
from autoshorts.longform.motion import parallax_offset
from autoshorts.longform.props import draw_prop
from autoshorts.longform.rig import animate_pose, draw_rig
from autoshorts.longform.sphere import draw_sphere, orbit_point

RGB = tuple[int, int, int]

FRAME_W = 1920
FRAME_H = 1080
FLOOR_Y = 760
RIG_SCALE = 1.5
RIG_ORIGIN = (1000, 598)  # hip height so the feet land on FLOOR_Y (108 * 1.5 = 162 px leg)
RIG_BOX = (913, 330, 1087, 765)  # rough bounds of the figure, used for the rim light
DUST_BOX = (0, 64, FRAME_W, FLOOR_Y)
DUST_COUNT = 12
VIGNETTE_STRENGTH = 0.6
MOOD_WASH_INTENSITY = 0.08
NARRATION_Y = 240
INNER_VOICE_ANCHOR = (140, 420)
NUMBER_ANCHOR = (1500, 520)

REST_POSE = "stand"
POSE_TRANSITION_S = 0.4  # the figure eases from REST_POSE into the planned pose after a shot start

SPACE_WORDS = frozenset({"planet", "earth", "space", "moon", "orbit", "universe"})
SPHERE_CENTRE = (1500, 380)
SPHERE_RADIUS = 150.0
SPHERE_SPIN_DEG_S = 10.0
SPHERE_LAND = (120, 190, 110)
ORBIT_RADIUS = 210.0
ORBIT_TILT = 0.35
SATELLITE_SPEED_DEG_S = 45.0
SATELLITE_RADIUS = 9

PARALLAX_CAMERA_SPEED = 40.0  # px per second of camera pan


class ParallaxShape(NamedTuple):
    """A faint background shape. ``size`` is the radius for dots, the height for rounded bars."""

    kind: str  # "dot" or "bar"
    x: float
    y: float
    size: float
    width: float
    depth: float


PARALLAX_SHAPES: tuple[ParallaxShape, ...] = (
    ParallaxShape("dot", 220.0, 150.0, 6.0, 0.0, 0.2),
    ParallaxShape("bar", 640.0, 120.0, 14.0, 180.0, 0.4),
    ParallaxShape("dot", 1180.0, 220.0, 9.0, 0.0, 0.4),
    ParallaxShape("bar", 1600.0, 90.0, 16.0, 220.0, 0.7),
    ParallaxShape("dot", 420.0, 330.0, 5.0, 0.0, 0.7),
)


@dataclass(frozen=True)
class BeatPalette:
    """Colours used by the beat renderer. Theme tokens are reused where they exist."""

    void: RGB
    floor: RGB
    red: RGB
    blue: RGB
    text: RGB
    text_muted: RGB
    highlight: RGB
    inner_voice: RGB
    rim: RGB


def beat_palette() -> BeatPalette:
    """Return the beat palette. Only this function holds literal colour values."""
    return BeatPalette(
        void=(8, 8, 12),  # #08080C
        floor=(43, 46, 59),  # #2B2E3B
        red=(230, 46, 46),  # #E62E2E
        blue=(77, 166, 255),  # #4DA6FF
        text=TEXT_PRIMARY,
        text_muted=TEXT_MUTED,
        highlight=ACCENT_YELLOW,
        inner_voice=ACCENT_CORAL,
        rim=ACCENT_TEAL,
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


def plan_beat(
    section_index: int,
    heading: str,
    narration: str,
    speaker: str,
    is_last: bool,
) -> BeatPlan:
    """Build the visual plan for one section. Deterministic for the same inputs."""
    del heading, speaker  # accepted for API context; the rules read the narration only
    tokens = _WORD_RE.findall(narration.lower())
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
    )


def _active_word(words: Sequence[Mapping[str, Any]] | None, t: float) -> str | None:
    """Return the word whose ``[start, end)`` span covers ``t`` (absolute seconds)."""
    for entry in words or ():
        if float(entry["start"]) <= t < float(entry["end"]):
            return str(entry["word"])
    return None


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
    """Joint angles easing from the rest pose into the planned pose after a shot start."""
    p = (t - shot_start) / POSE_TRANSITION_S
    return animate_pose(REST_POSE, plan.pose, p, t)


def _is_space_topic(section_text: str) -> bool:
    return bool(SPACE_WORDS.intersection(_WORD_RE.findall(section_text.lower())))


def _draw_parallax(img: Image.Image, t: float) -> None:
    """Faint background shapes that slide at a depth-dependent speed under a camera pan."""
    draw = ImageDraw.Draw(img)
    camera_x = PARALLAX_CAMERA_SPEED * t
    for shape in PARALLAX_SHAPES:
        x = (shape.x - parallax_offset(camera_x, shape.depth)) % FRAME_W
        for dx in (0.0, -float(FRAME_W)):  # draw a second copy so the wrap is seamless
            cx = x + dx
            if shape.kind == "dot":
                r = shape.size
                draw.ellipse((cx - r, shape.y - r, cx + r, shape.y + r), fill=BG_SURFACE)
            else:
                h = shape.size
                draw.rounded_rectangle(
                    (cx, shape.y, cx + shape.width, shape.y + h), radius=h / 2, fill=BG_SURFACE
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
    optional sequence of ``{"word", "start", "end"}`` mappings in absolute seconds;
    the word active at ``t`` is highlighted in the narration line.
    """
    if img.mode != "RGB" or img.size != (FRAME_W, FRAME_H):
        raise ValueError(
            f"draw_beat needs an RGB {FRAME_W}x{FRAME_H} image, got {img.mode} {img.size}"
        )

    pal = PALETTE
    ImageDraw.Draw(img).rectangle((0, 0, FRAME_W - 1, FRAME_H - 1), fill=pal.void)
    ImageDraw.Draw(img).line([(0, FLOOR_Y), (FRAME_W, FLOOR_Y)], fill=pal.floor, width=2)
    _draw_parallax(img, t)

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
    )
    rim_light(img, RIG_BOX, pal.rim, width=6, intensity=0.35)

    for src in plan.light_sources:
        source_glow(img, src.center, src.radius, src.color, src.intensity)

    if plan.mood is not None:
        colour_wash(img, plan.mood, MOOD_WASH_INTENSITY)

    draw_dust_motes(ImageDraw.Draw(img), t, DUST_BOX, count=DUST_COUNT, seed=plan.section_index + 7)
    draw_vignette(img, VIGNETTE_STRENGTH)

    if section_text.strip():
        draw_narration(
            img,
            section_text,
            t,
            start,
            end,
            y=NARRATION_Y,
            color=pal.text,
            highlight=_active_word(words, t),
            highlight_color=pal.highlight,
        )
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
