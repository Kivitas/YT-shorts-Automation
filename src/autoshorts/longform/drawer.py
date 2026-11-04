"""Pillow animated studio renderer for long-form videos (1920x1080, 30 fps).

Features articulated 2D characters with real-time viseme lip-sync, expressive brow rigs,
two-character parody banter stages, smooth Ken-Burns camera motion, and 5 specialized
genre visual engines (History, Science, Comparison, Finance, Motivation).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterator, Sequence
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from autoshorts.core.theme import (
    ACCENT_CORAL,
    ACCENT_GREEN,
    ACCENT_PURPLE,
    ACCENT_TEAL,
    ACCENT_YELLOW,
    BG_DEEP,
    BG_SURFACE,
    TEXT_MUTED,
    TEXT_PRIMARY,
    ease_in_out_cubic,
    lerp_rgb,
)
from autoshorts.longform import motion
from autoshorts.longform.beats import BeatPlan, draw_beat, draw_beat_text, plan_beat
from autoshorts.longform.engines import font_candidates
from autoshorts.longform.fx import camera_shake_offset, impact_flash_alpha
from autoshorts.longform.gsap_bridge import sample_timeline, sample_value
from autoshorts.longform.shots import Shot, plan_shots
from autoshorts.longform.types import MAX_VIDEO_SECONDS, VISEME_SHAPES, SectionAudio

logger = logging.getLogger(__name__)

WIDTH = 1920
HEIGHT = 1080
FPS = 30
TRANSITION_SECONDS = 0.7
STAGE_TOP = 96
STAGE_BOTTOM = 880

# Palette: aliases onto the shared flat theme tokens (core/theme.py).
OUTLINE = BG_DEEP  # dark ink for pupils, brows and text; no longer used as a stroke colour
WHITE = (255, 255, 255)
YELLOW = ACCENT_YELLOW
PINK = ACCENT_CORAL
GREEN = ACCENT_GREEN
CYAN = ACCENT_TEAL
CAPTION_BG = BG_SURFACE
WOOD = (176, 110, 60)
PANTS = (40, 52, 112)
SHOE = (34, 34, 46)
PEACH = (255, 206, 166)
BROWN_SKIN = (205, 140, 100)
SHIRT_RED = ACCENT_CORAL
SHIRT_BLUE = (40, 130, 250)
SHIRT_TEAL = ACCENT_TEAL
SHIRT_PURPLE = ACCENT_PURPLE
HAIR_BROWN = (92, 52, 28)
HAIR_BLACK = (30, 28, 34)
MOUTH_DARK = (120, 24, 44)
TONGUE_PINK = (235, 96, 120)

MAIN_SHIRTS = (SHIRT_RED, SHIRT_BLUE, SHIRT_PURPLE)
MOODS = ("happy", "shocked", "deadpan", "smug", "hyped", "thinking", "angry", "worried")
TRANSITIONS = ("cut", "dip", "flash", "cut", "dip", "cut")
FLASH_FRAMES = 6
FLASH_PEAK = 0.45
SHOT_ZOOM: dict[str, float] = {
    "wide": 1.0,
    "close": 1.08,
    "insert": 1.15,
    "ecu": 1.25,
    "match": 1.1,
    "montage": 1.0,
    "impact": 1.0,
}
IMPACT_FLASH_FRAMES = 3  # first frames of an impact shot blend towards white
SHAKE_SECTION = 1  # the first section that has a transition into it
SHAKE_SECONDS = 0.2
SHAKE_AMPLITUDE = 14.0
PUSH_IN_END = 1.06  # slow push-in per section: 1.0 at the section start, this at the end
OPENER_START = 1.12  # first section opens wide and pulls back to medium
OPENER_SECONDS = 2.0
FLOOR_Y = 760

# ---------------------------------------------------------------------------
# Poses: joint positions relative to the hip, in "units" (y grows downward).
# ---------------------------------------------------------------------------
Pose = dict[str, tuple[float, float]]

JOINTS = (
    "head", "neck", "l_elbow", "l_hand", "r_elbow", "r_hand",
    "l_knee", "l_foot", "r_knee", "r_foot",
)  # fmt: skip

POSES: dict[str, Pose] = {
    "stand": {
        "head": (0, -150), "neck": (0, -122),
        "l_elbow": (-28, -85), "l_hand": (-34, -45),
        "r_elbow": (28, -85), "r_hand": (34, -45),
        "l_knee": (-12, 45), "l_foot": (-16, 100),
        "r_knee": (12, 45), "r_foot": (16, 100),
    },
    "point": {
        "head": (0, -150), "neck": (0, -122),
        "l_elbow": (-28, -85), "l_hand": (-34, -45),
        "r_elbow": (50, -112), "r_hand": (105, -128),
        "l_knee": (-14, 45), "l_foot": (-20, 100),
        "r_knee": (14, 45), "r_foot": (22, 100),
    },
    "sit": {
        "head": (0, -118), "neck": (0, -92),
        "l_elbow": (-20, -55), "l_hand": (18, -38),
        "r_elbow": (22, -55), "r_hand": (50, -38),
        "l_knee": (45, 0), "l_foot": (45, 70),
        "r_knee": (70, 0), "r_foot": (70, 70),
    },
    "lift": {
        "head": (0, -150), "neck": (0, -122),
        "l_elbow": (-45, -125), "l_hand": (-38, -190),
        "r_elbow": (45, -125), "r_hand": (38, -190),
        "l_knee": (-18, 45), "l_foot": (-26, 100),
        "r_knee": (18, 45), "r_foot": (26, 100),
    },
    "walk": {
        "head": (4, -150), "neck": (2, -122),
        "l_elbow": (-20, -88), "l_hand": (-45, -55),
        "r_elbow": (22, -88), "r_hand": (40, -52),
        "l_knee": (-32, 40), "l_foot": (-58, 95),
        "r_knee": (28, 42), "r_foot": (50, 98),
    },
    "shrug": {
        "head": (0, -148), "neck": (0, -120),
        "l_elbow": (-48, -80), "l_hand": (-68, -112),
        "r_elbow": (48, -80), "r_hand": (68, -112),
        "l_knee": (-12, 45), "l_foot": (-16, 100),
        "r_knee": (12, 45), "r_foot": (16, 100),
    },
    "think": {
        "head": (0, -150), "neck": (0, -122),
        "l_elbow": (-28, -85), "l_hand": (-34, -45),
        "r_elbow": (30, -90), "r_hand": (8, -138),
        "l_knee": (-12, 45), "l_foot": (-16, 100),
        "r_knee": (12, 45), "r_foot": (16, 100),
    },
    "present": {
        "head": (2, -150), "neck": (0, -122),
        "l_elbow": (-32, -82), "l_hand": (-42, -52),
        "r_elbow": (54, -108), "r_hand": (112, -122),
        "l_knee": (-14, 45), "l_foot": (-18, 100),
        "r_knee": (16, 45), "r_foot": (22, 100),
    },
    "debate": {
        "head": (4, -150), "neck": (2, -122),
        "l_elbow": (-40, -98), "l_hand": (-64, -120),
        "r_elbow": (46, -102), "r_hand": (82, -118),
        "l_knee": (-15, 45), "l_foot": (-20, 100),
        "r_knee": (15, 45), "r_foot": (20, 100),
    },
}  # fmt: skip

POSE_CYCLE = ("stand", "point", "present", "think", "lift", "shrug", "walk", "point")
FRIEND_CYCLE = ("sit", "point", "sit", "think")
BANTER_CYCLE_A = ("debate", "point", "present", "shrug", "stand")
BANTER_CYCLE_B = ("stand", "shrug", "think", "debate", "point")


def ease_in_out(t: float) -> float:
    """Smoothstep-style ease-in-out on [0, 1]."""
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def ease_out_back(t: float) -> float:
    """Overshoot-and-settle curve for pop-ins and entrances."""
    t = min(1.0, max(0.0, t))
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def interpolate_pose(a: Pose | str, b: Pose | str, t: float) -> Pose:
    """Tween pose ``a`` toward ``b`` with ease-in-out; t=0 gives a, t=1 gives b."""
    pa = POSES[a] if isinstance(a, str) else a
    pb = POSES[b] if isinstance(b, str) else b
    k = ease_in_out(t)
    out: Pose = {}
    for j in JOINTS:
        ax, ay = pa[j]
        bx, by = pb[j]
        out[j] = (ax + (bx - ax) * k, ay + (by - ay) * k)
    return out


def pose_at(sequence: Sequence[str], time_s: float, hold: float = 2.2, tween: float = 0.7) -> Pose:
    """Pose of a character that holds each pose for ``hold`` s then tweens to the next."""
    period = hold + tween
    idx = int(time_s // period)
    local = time_s - idx * period
    cur = sequence[idx % len(sequence)]
    nxt = sequence[(idx + 1) % len(sequence)]
    if local <= hold:
        return interpolate_pose(cur, cur, 0.0)
    return interpolate_pose(cur, nxt, (local - hold) / tween)


# ---------------------------------------------------------------------------
# Viseme Lip-Sync Engine
# ---------------------------------------------------------------------------
def _char_to_viseme(ch: str) -> str:
    c = ch.lower()
    if c in "mbp":
        return "MBP"
    if c in "ouwq":
        return "O"
    if c in "aiy":
        return "AI"
    if c in "esztdkgcrlnfvjxh":
        return "E"
    return ""


def viseme_at(words: Sequence[dict[str, Any]], t: float) -> str:
    """Return the active mouth viseme ('REST', 'AI', 'O', 'E', 'MBP') at timestamp ``t``."""
    if not words:
        return "REST"
    for w in words:
        start = float(w.get("start", 0.0))
        end = float(w.get("end", start + 0.18))
        if end <= start:
            end = start + 0.18
        if start <= t <= end:
            raw_word = "".join(ch for ch in str(w.get("word", "")) if ch.isalpha())
            dur = max(0.05, end - start)
            rel = min(0.999, max(0.0, (t - start) / dur))
            # Rapid syllable oscillation so mouth visibly opens/closes across long words
            beat = int((t - start) * 14.0)
            if beat % 5 == 4:
                return "MBP"
            if raw_word:
                idx = min(len(raw_word) - 1, int(rel * len(raw_word)))
                mapped = _char_to_viseme(raw_word[idx])
                if mapped:
                    # Alternate if consecutive frames would stay static too long
                    if beat % 3 == 2 and mapped == "E":
                        return "AI"
                    return mapped
            cycle = ("AI", "O", "E", "MBP")
            return cycle[beat % len(cycle)]
    return "REST"


# ---------------------------------------------------------------------------
# Characters
# ---------------------------------------------------------------------------
def _limb(
    draw: ImageDraw.ImageDraw, pts: list[tuple[float, float]], width: float, fill: Any
) -> None:
    """Flat limb: a thick polyline with round joints and ends, no outline."""
    w = max(1, round(width))
    draw.line(pts, fill=fill, width=w, joint="curve")
    rad = w / 2
    for x, y in pts:
        draw.ellipse([x - rad, y - rad, x + rad, y + rad], fill=fill)


def _blob(
    draw: ImageDraw.ImageDraw, cx: float, cy: float, rx: float, ry: float, fill: Any, out: int = 0
) -> None:
    """Flat filled ellipse. ``out`` is kept for call compatibility and ignored."""
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill)


def _brow(mood: str, side: int, r: float) -> tuple[float, float]:
    """Vertical offsets of the (outer, inner) end of one eyebrow."""
    if mood == "angry":
        return -0.12 * r, 0.16 * r
    if mood == "worried":
        return 0.12 * r, -0.14 * r
    if mood in ("shock", "shocked", "hyped", "excited"):
        return -0.24 * r, -0.22 * r
    if mood == "deadpan":
        return 0.04 * r, 0.04 * r
    if mood == "smug":
        return (-0.22 * r, -0.08 * r) if side > 0 else (0.05 * r, 0.08 * r)
    if mood in ("think", "thinking") and side > 0:
        return -0.22 * r, -0.12 * r
    return 0.0, 0.0


def _draw_mouth(
    draw: ImageDraw.ImageDraw,
    mx: float,
    my: float,
    r: float,
    line_w: int,
    mood: str,
    viseme: str,
) -> None:
    mw = 0.36 * r
    if viseme == "AI":
        # Wide open vowel mouth with white teeth bar and pink tongue
        rx, ry = 0.34 * r, 0.25 * r
        draw.ellipse(
            [mx - rx, my - ry * 0.7, mx + rx, my + ry * 1.1],
            fill=MOUTH_DARK,
        )
        draw.ellipse(
            [mx - rx * 0.65, my + ry * 0.15, mx + rx * 0.65, my + ry * 0.95],
            fill=TONGUE_PINK,
        )
        draw.rectangle(
            [mx - rx * 0.7, my - ry * 0.55, mx + rx * 0.7, my - ry * 0.15],
            fill=WHITE,
        )
        return
    if viseme == "O":
        # Rounded O vowel mouth
        rx, ry = 0.20 * r, 0.24 * r
        draw.ellipse(
            [mx - rx, my - ry * 0.8, mx + rx, my + ry],
            fill=MOUTH_DARK,
        )
        draw.ellipse(
            [mx - rx * 0.55, my + ry * 0.1, mx + rx * 0.55, my + ry * 0.8],
            fill=TONGUE_PINK,
        )
        return
    if viseme == "E":
        # Wide stretched E / consonant mouth with teeth line
        rx, ry = 0.38 * r, 0.15 * r
        draw.rounded_rectangle(
            [mx - rx, my - ry * 0.6, mx + rx, my + ry],
            radius=max(3, int(0.08 * r)),
            fill=MOUTH_DARK,
        )
        draw.line(
            [(mx - rx * 0.78, my + ry * 0.1), (mx + rx * 0.78, my + ry * 0.1)],
            fill=WHITE,
            width=max(2, line_w - 1),
        )
        return
    if viseme == "MBP":
        # Closed bilabial consonant press
        draw.line(
            [(mx - 0.32 * r, my + 0.04 * r), (mx + 0.32 * r, my + 0.04 * r)],
            fill=OUTLINE,
            width=line_w + 1,
        )
        return

    # REST state: render mood expression
    if mood in ("shock", "shocked"):
        draw.ellipse(
            [mx - 0.16 * r, my - 0.2 * r, mx + 0.16 * r, my + 0.2 * r],
            fill=MOUTH_DARK,
        )
    elif mood in ("excited", "hyped"):
        draw.pieslice(
            [mx - mw, my - 0.2 * r, mx + mw, my + 0.4 * r],
            0,
            180,
            fill=MOUTH_DARK,
        )
    elif mood in ("angry", "worried"):
        draw.arc([mx - mw, my, mx + mw, my + 0.5 * r], 180, 360, fill=OUTLINE, width=line_w)
    elif mood in ("think", "thinking", "deadpan"):
        draw.line(
            [(mx - 0.65 * mw, my + 0.05 * r), (mx + 0.65 * mw, my + 0.05 * r)],
            fill=OUTLINE,
            width=line_w,
        )
    elif mood == "smug":
        draw.arc(
            [mx - 0.8 * mw, my - 0.18 * r, mx + 1.1 * mw, my + 0.32 * r],
            10,
            155,
            fill=OUTLINE,
            width=line_w,
        )
    else:
        draw.arc([mx - mw, my - 0.2 * r, mx + mw, my + 0.4 * r], 0, 180, fill=OUTLINE, width=line_w)


def draw_character(
    draw: ImageDraw.ImageDraw,
    pose: Pose,
    origin: tuple[float, float],
    scale: float = 1.9,
    shirt: tuple[int, int, int] = SHIRT_RED,
    skin: tuple[int, int, int] = PEACH,
    hair: tuple[int, int, int] = HAIR_BROWN,
    mood: str = "happy",
    flip: int = 1,
    blink: bool = False,
    viseme: str = "REST",
    pointer_stick: bool = False,
    is_speaking: bool = False,
) -> None:
    """Draw a flat-vector round-headed character with real-time viseme lip-sync.

    Flat fills only: round-capped limbs, a rounded-rectangle torso, a filled head with a
    hair cap, soft blush and simple eyes and brows. ``origin`` is the hip in pixels.
    ``flip`` mirrors the pose left/right. ``mood`` sets the eyebrows and resting mouth;
    ``viseme`` drives active speech shapes.
    """
    ox, oy = origin
    arm_w = round(11 * scale)
    leg_w = round(13 * scale)
    line_w = max(2, round(0.07 * 30 * scale))

    def p(j: str) -> tuple[float, float]:
        x, y = pose[j]
        return ox + x * scale * flip, oy + y * scale

    neck = p("neck")
    hx, hy = p("head")
    r = 30 * scale

    # Optional active-speaker stage floor disc
    if is_speaking:
        foot_y = oy + 104 * scale
        draw.ellipse(
            [ox - 48 * scale, foot_y - 12 * scale, ox + 48 * scale, foot_y + 14 * scale],
            fill=YELLOW,
        )

    legs = [[(ox, oy), p(f"{s}_knee"), p(f"{s}_foot")] for s in ("l", "r")]
    arms = [[neck, p(f"{s}_elbow"), p(f"{s}_hand")] for s in ("l", "r")]

    for chain in legs:
        _limb(draw, chain, leg_w, PANTS)

    tw = 17 * scale
    draw.rounded_rectangle(
        [ox - tw, neck[1] - 4 * scale, ox + tw, oy + 8 * scale],
        radius=round(10 * scale),
        fill=shirt,
    )
    for chain in arms:
        _limb(draw, chain, arm_w, shirt)

    # Optional pointer stick extending from the pointing hand toward charts/diagrams
    if pointer_stick:
        rhx, rhy = p("r_hand")
        tip_x = rhx + 95 * scale * flip
        tip_y = rhy - 42 * scale
        draw.line([(rhx, rhy), (tip_x, tip_y)], fill=YELLOW, width=max(3, round(3 * scale)))
        draw.ellipse(
            [tip_x - 6 * scale, tip_y - 6 * scale, tip_x + 6 * scale, tip_y + 6 * scale],
            fill=PINK,
        )

    for chain in arms:
        _blob(draw, chain[-1][0], chain[-1][1], 9 * scale, 9 * scale, skin)
    for s in ("l", "r"):
        fx, fy = p(f"{s}_foot")
        _blob(draw, fx, fy, 12 * scale, 7 * scale, SHOE)

    # Head: flat skin disc, hair as a cap, soft blush on both cheeks.
    draw.ellipse([hx - r, hy - r, hx + r, hy + r], fill=skin)
    draw.chord([hx - r, hy - r, hx + r, hy + r], 200, 340, fill=hair)
    blush = lerp_rgb(skin, PINK, 0.3)
    for side in (-1, 1):
        bx, by, br = hx + side * 0.62 * r, hy + 0.28 * r, 0.13 * r
        draw.ellipse([bx - br, by - br * 0.7, bx + br, by + br * 0.7], fill=blush)

    eye_w, eye_h = 0.36 * r, 0.46 * r
    eye_y = hy - 0.02 * r
    look = 0.1 * r * flip
    for side in (-1, 1):
        ex = hx + side * 0.42 * r
        if blink:
            draw.line(
                [(ex - eye_w / 2, eye_y), (ex + eye_w / 2, eye_y)], fill=OUTLINE, width=line_w
            )
        else:
            draw.ellipse(
                [ex - eye_w / 2, eye_y - eye_h / 2, ex + eye_w / 2, eye_y + eye_h / 2],
                fill=WHITE,
            )
            if mood == "deadpan":
                # Half-lidded deadpan expression
                draw.chord(
                    [ex - eye_w / 2, eye_y - eye_h / 2, ex + eye_w / 2, eye_y + eye_h / 2],
                    180,
                    360,
                    fill=skin,
                )
            pr = 0.15 * r
            px, py = ex + look, eye_y + 0.04 * r
            draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=OUTLINE)
        outer, inner = _brow(mood, side, r)
        by = hy - 0.5 * r
        draw.line(
            [(hx + side * 0.8 * r, by + outer), (hx + side * 0.22 * r, by + inner)],
            fill=OUTLINE,
            width=max(3, line_w),
        )

    mx, my = hx, hy + 0.42 * r
    norm_viseme = viseme if viseme in VISEME_SHAPES else "REST"
    _draw_mouth(draw, mx, my, r, line_w, mood, norm_viseme)


# ---------------------------------------------------------------------------
# Stickers and sparkles
# ---------------------------------------------------------------------------
def _star(
    cx: float, cy: float, outer: float, inner: float, points: int
) -> list[tuple[float, float]]:
    pts = []
    for i in range(points * 2):
        rad = outer if i % 2 == 0 else inner
        a = -math.pi / 2 + i * math.pi / points
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts


def _filled_poly(
    d: ImageDraw.ImageDraw, pts: list[tuple[float, float]], fill: tuple[int, int, int]
) -> None:
    d.polygon(pts, fill=fill)


def draw_sticker(
    d: ImageDraw.ImageDraw, kind: str, cx: float, cy: float, size: float, label: str = ""
) -> None:
    """Draw one bright sticker centred on (cx, cy) with radius ``size``."""
    if kind == "star":
        _filled_poly(d, _star(cx, cy, size, size * 0.45, 5), YELLOW)
    elif kind == "coin":
        d.ellipse(
            [cx - size, cy - size, cx + size, cy + size],
            fill=(255, 196, 40),
        )
        d.text((cx, cy), "$", font=load_font(int(size * 1.2)), fill=OUTLINE, anchor="mm")
    elif kind == "check":
        d.ellipse([cx - size, cy - size, cx + size, cy + size], fill=GREEN)
        d.line(
            [(cx - 0.45 * size, cy + 0.02 * size), (cx - 0.12 * size, cy + 0.36 * size), (cx + 0.5 * size, cy - 0.35 * size)],
            fill=WHITE, width=max(4, int(size * 0.22)), joint="curve",
        )  # fmt: skip
    elif kind == "arrow":
        s = size
        pts = [
            (cx, cy - s), (cx + 0.7 * s, cy), (cx + 0.3 * s, cy),
            (cx + 0.3 * s, cy + 0.9 * s), (cx - 0.3 * s, cy + 0.9 * s),
            (cx - 0.3 * s, cy), (cx - 0.7 * s, cy),
        ]  # fmt: skip
        _filled_poly(d, pts, PINK)
    else:  # "boom": a burst with a short word inside
        _filled_poly(d, _star(cx, cy, size, size * 0.72, 12), (255, 80, 80))
        if label:
            font = _fit_font(d, label, int(size * 0.6), size * 1.6)
            d.text(
                (cx, cy),
                label,
                font=font,
                fill=WHITE,
                anchor="mm",
            )


# ---------------------------------------------------------------------------
# Scenes: flat sets painted once per scene and cached + dynamic genre engines.
# ---------------------------------------------------------------------------
def _box(
    d: ImageDraw.ImageDraw,
    box: Sequence[float],
    fill: tuple[int, int, int],
    radius: int = 18,
    width: int = 7,
) -> None:
    d.rounded_rectangle(list(box), radius=radius, fill=fill)


def _gradient(
    img: Image.Image,
    box: tuple[int, int, int, int],
    top: tuple[int, int, int],
    bottom: tuple[int, int, int],
) -> None:
    """Paint a flat vertical gradient (one solid line per row) inside ``box``."""
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(img)
    span = max(1, y1 - y0 - 1)
    for y in range(y0, y1):
        d.line([(x0, y), (x1, y)], fill=lerp_rgb(top, bottom, (y - y0) / span))


def _paint_bedroom(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH, 760], fill=(255, 214, 102))
    d.rectangle([0, 760, WIDTH, HEIGHT], fill=(232, 150, 84))
    pts = [(x, 150 + 50 * math.sin(math.pi * x / WIDTH)) for x in range(0, WIDTH + 1, 40)]
    d.line(pts, fill=(120, 90, 40), width=3)
    for x, y in pts[::2]:
        d.ellipse(
            [x - 9, y - 9, x + 9, y + 9], fill=(255, 250, 210), outline=(120, 90, 40), width=3
        )
    _box(d, (120, 200, 300, 350), (255, 110, 170))
    _box(d, (330, 230, 480, 330), (80, 200, 220))
    _box(d, (1450, 200, 1800, 520), (130, 215, 255), radius=10, width=10)
    d.line([(1625, 200), (1625, 520)], fill=OUTLINE, width=10)
    d.line([(1450, 360), (1800, 360)], fill=OUTLINE, width=10)
    for x0 in (40, 560):  # bunk bed posts
        _box(d, (x0, 330, x0 + 55, 880), WOOD, radius=6)
    _box(d, (40, 455, 615, 500), WOOD, radius=6)
    _box(d, (70, 415, 585, 455), (120, 200, 255))
    _box(d, (95, 540, 560, 640), (210, 240, 255))
    _box(d, (100, 540, 230, 590), WHITE, radius=20)
    _box(d, (95, 600, 555, 720), (60, 120, 230), radius=30)
    _box(d, (1200, 640, 1600, 668), WOOD, radius=6)
    d.rectangle([1220, 668, 1244, 880], fill=WOOD)
    d.rectangle([1556, 668, 1580, 880], fill=WOOD)
    _box(d, (1290, 470, 1500, 620), (40, 40, 70))
    _box(d, (1305, 485, 1485, 605), (80, 210, 255), radius=6)
    _box(d, (760, 820, 1160, 900), (120, 220, 160), radius=40)


def _paint_gym(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH, 760], fill=(110, 200, 255))
    d.rectangle([0, 760, WIDTH, HEIGHT], fill=(70, 70, 100))
    d.rectangle([0, 96, WIDTH, 200], fill=YELLOW)
    for x in range(-200, WIDTH, 120):
        d.polygon([(x, 96), (x + 60, 96), (x + 120, 200), (x + 60, 200)], fill=OUTLINE)
    _box(d, (120, 260, 420, 480), (255, 240, 120))
    d.text((150, 300), "GO", font=load_font(120, True), fill=OUTLINE)
    _box(d, (760, 800, 1320, 870), (255, 90, 90), radius=30)
    for i in range(4):
        x = 1460 + i * 90
        d.rectangle([x, 640, x + 70, 656], fill=(90, 90, 100))
        _box(d, (x - 14, 620, x + 8, 676), (240, 80, 80), radius=6, width=5)
        _box(d, (x + 62, 620, x + 84, 676), (240, 80, 80), radius=6, width=5)


def _paint_kitchen(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH, HEIGHT], fill=(255, 242, 206))
    for row in range(5):
        for col in range(32):
            fill = (120, 220, 200) if (row + col) % 2 == 0 else WHITE
            d.rectangle([col * 60, 380 + row * 36, col * 60 + 60, 416 + row * 36], fill=fill)
    d.rectangle([0, 610, WIDTH, HEIGHT], fill=(255, 140, 60))
    d.rectangle([0, 560, WIDTH, 610], fill=(250, 250, 245))
    for x in range(0, WIDTH, 320):
        d.line([(x, 630), (x, 880)], fill=OUTLINE, width=6)
    _box(d, (1560, 200, 1800, 560), (240, 250, 255))
    d.line([(1560, 400), (1800, 400)], fill=OUTLINE, width=6)
    d.ellipse([290, 190, 370, 270], fill=WHITE)
    d.line([(330, 230), (330, 205)], fill=OUTLINE, width=6)
    d.line([(330, 230), (358, 245)], fill=OUTLINE, width=6)
    d.ellipse([650, 528, 770, 562], fill=WHITE)
    d.ellipse([682, 500, 728, 540], fill=(255, 80, 80))


def _paint_street(img: Image.Image) -> None:
    _gradient(img, (0, 0, WIDTH, 760), (255, 140, 90), (255, 226, 150))
    d = ImageDraw.Draw(img)
    d.ellipse([1380, 260, 1580, 460], fill=(255, 240, 130))
    buildings = [
        (60, 200, 430, (70, 60, 120)), (280, 160, 300, (100, 70, 150)),
        (460, 240, 500, (50, 50, 95)), (720, 180, 360, (100, 70, 150)),
        (920, 220, 460, (70, 60, 120)), (1160, 170, 320, (50, 50, 95)),
        (1350, 240, 420, (100, 70, 150)), (1620, 260, 500, (70, 60, 120)),
    ]  # fmt: skip
    for x0, w, h, col in buildings:
        top = 760 - h
        d.rectangle([x0, top, x0 + w, 760], fill=col)
        for wy in range(top + 30, 740, 70):
            for wx in range(x0 + 24, x0 + w - 30, 60):
                d.rectangle([wx, wy, wx + 26, wy + 36], fill=(255, 226, 120))
    d.rectangle([0, 760, WIDTH, HEIGHT], fill=(80, 80, 96))
    for x in range(0, WIDTH, 200):
        d.rectangle([x + 40, 820, x + 120, 830], fill=WHITE)


def _paint_office(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH, 760], fill=(186, 232, 255))
    d.rectangle([0, 760, WIDTH, HEIGHT], fill=(240, 205, 150))
    _box(d, (1000, 170, 1560, 500), WHITE, radius=16)
    pts = [(1040, 450), (1140, 400), (1230, 420), (1330, 330), (1430, 300), (1520, 220)]
    d.line(pts, fill=GREEN, width=14, joint="curve")
    for x, y in pts:
        d.ellipse([x - 12, y - 12, x + 12, y + 12], fill=WHITE)
    _box(d, (90, 200, 430, 520), (255, 240, 190))
    d.line([(260, 200), (260, 520)], fill=OUTLINE, width=8)
    d.rectangle([1080, 640, 1700, 668], fill=WHITE)
    d.rectangle([1100, 668, 1120, 880], fill=(230, 230, 240))
    d.rectangle([1660, 668, 1680, 880], fill=(230, 230, 240))
    _box(d, (1240, 470, 1420, 630), (40, 40, 70))
    _box(d, (1255, 485, 1405, 615), (120, 220, 255), radius=6)
    d.ellipse([160, 560, 260, 720], fill=GREEN)
    d.ellipse([240, 520, 340, 690], fill=(40, 180, 100))
    _filled_poly(d, [(200, 740), (300, 740), (285, 860), (215, 860)], (255, 130, 60))


def _paint_beach(img: Image.Image) -> None:
    _gradient(img, (0, 0, WIDTH, 640), (255, 170, 90), (255, 218, 140))
    d = ImageDraw.Draw(img)
    d.ellipse([1380, 210, 1620, 450], fill=(255, 246, 170))
    d.rectangle([0, 640, WIDTH, 780], fill=(60, 190, 255))
    for x in range(40, WIDTH, 220):
        d.arc([x, 680, x + 90, 720], 200, 340, fill=WHITE, width=6)
    d.rectangle([0, 780, WIDTH, HEIGHT], fill=(255, 224, 150))
    d.line([(300, 880), (340, 560)], fill=(120, 80, 50), width=34)
    for ang in (-2.9, -2.3, -1.6, -0.9, -0.3):
        tip = (340 + 170 * math.cos(ang), 540 + 170 * math.sin(ang))
        side = (340 + 90 * math.cos(ang + 0.35), 540 + 90 * math.sin(ang + 0.35))
        _filled_poly(d, [(340, 540), side, tip], (40, 190, 120))


def _paint_money(img: Image.Image) -> None:
    _gradient(img, (0, 0, WIDTH, HEIGHT), (40, 180, 130), (20, 130, 100))
    d = ImageDraw.Draw(img)
    d.ellipse([1300, 230, 1700, 630], fill=(150, 160, 175))
    d.ellipse([1360, 290, 1640, 570], fill=(120, 130, 145))
    for k in range(8):
        a = k * math.pi / 4
        d.line(
            [(1500, 430), (1500 + 120 * math.cos(a), 430 + 120 * math.sin(a))],
            fill=OUTLINE,
            width=10,
        )
    for i in range(5):
        y = 850 - i * 34
        d.rounded_rectangle([1180, y - 30, 1480, y], radius=8, fill=(120, 220, 120))
        d.rectangle([1300, y - 30, 1360, y], fill=WHITE)
    for x, y in [(300, 840), (360, 842), (420, 838), (330, 800), (390, 800)]:
        d.ellipse([x - 34, y - 18, x + 34, y + 18], fill=(255, 200, 40))
    d.ellipse([720, 600, 880, 840], fill=(245, 245, 225))
    d.text((800, 710), "$", font=load_font(140, True), fill=(60, 160, 80), anchor="mm")


def _paint_parody(img: Image.Image) -> None:
    _gradient(img, (0, 0, WIDTH, 760), (42, 28, 74), (24, 18, 44))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 760, WIDTH, HEIGHT], fill=(58, 38, 68))
    # Paint-explainer style chalkboard / comedy studio backdrop
    _box(d, (660, 185, 1260, 520), (248, 244, 228), radius=18, width=8)
    d.line([(720, 420), (880, 320), (1020, 390), (1190, 250)], fill=PINK, width=10, joint="curve")
    d.text(
        (960, 230), "EXPECTATION vs REALITY", font=load_font(28, True), fill=OUTLINE, anchor="mm"
    )


def _paint_dark_studio(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH, HEIGHT], fill=(12, 16, 26))


SCENE_PAINTERS = {
    "bedroom": _paint_bedroom,
    "gym": _paint_gym,
    "kitchen": _paint_kitchen,
    "street": _paint_street,
    "office": _paint_office,
    "beach": _paint_beach,
    "money": _paint_money,
    "parody": _paint_parody,
    "history": _paint_dark_studio,
    "science": _paint_dark_studio,
    "comparison": _paint_dark_studio,
    "finance": _paint_dark_studio,
    "motivation": _paint_dark_studio,
}
DEFAULT_SCENES = ("bedroom", "street", "gym", "office", "beach", "kitchen", "money")
TOPIC_SCENES = {
    "discipline": ("motivation", "gym", "motivation", "office", "street"),
    "self_improvement": ("motivation", "beach", "motivation", "bedroom", "office"),
    "motivation": ("motivation", "gym", "motivation", "office", "motivation"),
    "money": ("finance", "money", "finance", "office", "finance"),
    "finance": ("finance", "office", "finance", "money", "finance"),
    "science": ("science", "office", "science", "science", "science"),
    "testosterone": ("science", "gym", "kitchen", "science", "bedroom"),
    "history": ("history", "history", "history", "history", "history"),
    "comparison": ("comparison", "comparison", "comparison", "comparison", "comparison"),
    "parody": ("parody", "street", "parody", "office", "parody"),
}


def scenes_for_topic(topic: str) -> tuple[str, ...]:
    """Scene order for a topic; routes custom topics through genre inference when needed."""
    if topic in TOPIC_SCENES:
        return TOPIC_SCENES[topic]
    from autoshorts.longform.topics import infer_genre

    genre, _ = infer_genre(topic)
    return TOPIC_SCENES.get(genre, DEFAULT_SCENES)


@lru_cache(maxsize=16)
def _scene_background(name: str) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    SCENE_PAINTERS[name](img)
    return img


# ---------------------------------------------------------------------------
# Fonts and captions
# ---------------------------------------------------------------------------
_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "bold": font_candidates(True),
    "regular": font_candidates(False),
}
_warned_font = False


def load_font(size: int, bold: bool = True) -> Any:
    """Load a system TrueType font (Windows, macOS, Linux), or fall back to PIL's default font."""
    return _load_font_cached(size, bold)


@lru_cache(maxsize=64)
def _load_font_cached(size: int, bold: bool) -> Any:
    global _warned_font
    key = "bold" if bold else "regular"
    for path in _FONT_CANDIDATES[key]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    if not _warned_font:
        logger.warning("System TrueType fonts missing; falling back to PIL default font")
        _warned_font = True
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def _fit_font(
    draw: ImageDraw.ImageDraw, text: str, size: int, max_w: float, bold: bool = True
) -> Any:
    while size > 18:
        font = load_font(size, bold)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 4
    return load_font(18, bold)


HEADING_Y = 1048  # centred in the bottom letterbox bar, clear of the narration line
HEADING_SIZE = 40
OFF_WHITE = (236, 232, 224)
LETTERBOX_TOP = 64
LETTERBOX_BOTTOM = 1016


def _commit_layer(img: Image.Image, layer: Image.Image) -> None:
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    img.paste(base.convert("RGB"))


DAY_MILESTONES: tuple[int, ...] = (1, 3, 12, 30, 64, 90, 150, 270, 365)
ACCENT_GOLD: tuple[int, int, int] = ACCENT_YELLOW
_DAY_PATTERN = re.compile(r"\bday\s+(\d{1,4})\b", re.IGNORECASE)


def _resolve_day_label(index: int, heading: str = "", narration: str = "") -> str:
    """Resolve day progression counter (e.g. DAY 1, DAY 3, DAY 12) from text or milestone table."""
    match = _DAY_PATTERN.search(heading or "")
    if not match:
        match = _DAY_PATTERN.search(narration or "")
    if match:
        return f"DAY {match.group(1)}"
    clamped = min(max(index, 0), len(DAY_MILESTONES) - 1)
    return f"DAY {DAY_MILESTONES[clamped]}"


def _draw_chrome(
    draw: ImageDraw.ImageDraw,
    index: int | str = 0,
    heading: str | int = "",
    narration: str = "",
    **kwargs: Any,
) -> None:
    """Cinematic frame: black letterbox bars, and top-left HUD pill with DAY progression."""
    # Handle callers that pass (draw, title, index)
    if isinstance(index, str) and isinstance(heading, int):
        heading, index = index, heading
    elif isinstance(index, str) and not heading:
        heading = index
        index = 0
    if "title" in kwargs and not heading:
        heading = kwargs["title"]

    idx = int(index)
    heading_str = str(heading).strip() if heading else ""
    narration_str = str(narration).strip() if narration else ""

    draw.rectangle([0, 0, WIDTH, LETTERBOX_TOP], fill=(0, 0, 0))
    draw.rectangle([0, LETTERBOX_BOTTOM, WIDTH, HEIGHT], fill=(0, 0, 0))

    day_label = _resolve_day_label(idx, heading_str, narration_str)

    if heading_str:
        hud_text = f"{day_label}  ·  PART {idx + 1}  ·  {heading_str[:28].upper()}"
    else:
        hud_text = f"{day_label}  ·  PART {idx + 1}"

    pill_x0 = 72
    pill_y0 = 16
    pill_h = 32
    font = load_font(20, True)
    text_w = draw.textlength(hud_text, font=font)
    pad_x = 12
    pill_w = int(text_w + pad_x * 2)

    # Rounded pill container
    draw.rounded_rectangle(
        [pill_x0, pill_y0, pill_x0 + pill_w, pill_y0 + pill_h],
        radius=4,
        fill=BG_SURFACE,
    )

    # Crisp gold/yellow left accent tick (3px wide vertical bar in ACCENT_GOLD)
    draw.rectangle([pill_x0, pill_y0, pill_x0 + 3, pill_y0 + pill_h], fill=ACCENT_GOLD)

    # Text
    draw.text((pill_x0 + pad_x, pill_y0 + 5), hud_text, font=font, fill=TEXT_PRIMARY)


def _paint_heading(layer: Image.Image, heading: str, t: float) -> None:
    """Section heading: centred off-white line in the bottom bar that fades in over 0.4 s."""
    alpha = min(1.0, max(0.0, t / 0.4))
    if not heading or alpha <= 0.0:
        return
    ld = ImageDraw.Draw(layer)
    font = _fit_font(ld, heading, HEADING_SIZE, WIDTH - 360)
    ld.text(
        (WIDTH / 2, HEADING_Y),
        heading,
        font=font,
        fill=(*OFF_WHITE, round(255 * alpha)),
        anchor="mm",
    )


# ---------------------------------------------------------------------------
# Scene composition & Camera motion
# ---------------------------------------------------------------------------
def _zoom_centre(img: Image.Image, zoom: float) -> Image.Image:
    """Punch in on the frame centre by ``zoom``; 1.0 returns ``img`` unchanged."""
    if zoom <= 1.0:
        return img
    crop_w = int(WIDTH / zoom)
    crop_h = int(HEIGHT / zoom)
    x0 = (WIDTH - crop_w) // 2
    y0 = (HEIGHT - crop_h) // 2
    cropped = img.crop((x0, y0, x0 + crop_w, y0 + crop_h))
    return cropped.resize((WIDTH, HEIGHT), Image.BICUBIC)


def _shift(img: Image.Image, dx: int, dy: int) -> Image.Image:
    """Translate ``img`` by (dx, dy), filling the exposed edge with black."""
    if dx == 0 and dy == 0:
        return img
    out = Image.new("RGB", img.size, (0, 0, 0))
    out.paste(img, (dx, dy))
    return out


def _section_shots(sa: SectionAudio) -> list[Shot]:
    """Sub-shots for one section; falls back to a single wide shot if timing data is unusable."""
    duration = float(sa.duration)
    if duration <= 0:
        return []
    try:
        return plan_shots(duration, sa.words)
    except (KeyError, TypeError, ValueError):
        return [Shot(0.0, duration, "wide")]


def _speaker(sa: SectionAudio) -> str:
    return (getattr(sa.section, "speaker", "narrator") or "narrator").lower()


def _locate_frame(shots: Sequence[Shot], frame: int) -> tuple[Shot, int]:
    """Return the shot covering section frame ``frame`` and the frame where that shot starts."""
    for shot in shots:
        start_frame = round(shot.start * FPS)
        if start_frame <= frame < round(shot.end * FPS):
            return shot, start_frame
    last = shots[-1]
    return last, round(last.start * FPS)


_gsap_fallback_warned = False


def _push_in_spec(frame_count: int) -> dict[str, Any]:
    """GSAP timeline for the push-in: 1.0 to ``PUSH_IN_END`` with sine.inOut over the section."""
    return {
        "duration": 1.0,
        "frames": frame_count,
        "state": {"zoom": 1.0},
        "tweens": [
            {"at": 0.0, "duration": 1.0, "vars": {"zoom": PUSH_IN_END}, "ease": "sine.inOut"}
        ],
    }


@lru_cache(maxsize=256)
def _push_in_table(frame_count: int) -> tuple[float, ...] | None:
    """Per-frame push-in values sampled from GSAP, or ``None`` when node is unavailable."""
    global _gsap_fallback_warned
    try:
        return tuple(sample_timeline(_push_in_spec(frame_count))["zoom"])
    except RuntimeError as exc:
        if not _gsap_fallback_warned:
            _gsap_fallback_warned = True
            logger.warning("GSAP push-in unavailable (%s); using Python easing fallback", exc)
        return None


def _push_in_zoom(frame: int, frame_count: int) -> float:
    """Section push-in: eases from 1.0 at the first frame to ``PUSH_IN_END`` at the last."""
    frame_count = max(1, frame_count)
    table = _push_in_table(frame_count)
    if table is not None:
        return sample_value(table, frame)
    p = frame / max(1, frame_count - 1)
    return 1.0 + (PUSH_IN_END - 1.0) * motion.ease_in_out_sine(p)


def _opener_zoom(frame: int) -> float:
    """Wide-shot opener for section 0: pulls back from ``OPENER_START`` to 1.0 over two seconds."""
    p = frame / (OPENER_SECONDS * FPS)
    return OPENER_START + (1.0 - OPENER_START) * motion.ease_in_out_sine(p)


def _camera_zoom(section_index: int, frame: int, frame_count: int) -> float:
    """Combined section camera zoom: the push-in, plus the opener on the first section."""
    zoom = _push_in_zoom(frame, frame_count)
    if section_index == 0:
        zoom *= _opener_zoom(frame)
    return zoom


def _shot_camera(
    img: Image.Image,
    shot: Shot,
    t_in_shot: float = 0.0,
    camera_zoom: float | tuple[float, float] | None = 1.0,
    focus_xy: tuple[float, float] | None = None,
    progress: float | None = None,
) -> Image.Image:
    """Shot-kind camera: centre punch-in (on top of the section camera), plus a decaying shake.

    When ``focus_xy`` is provided and ``shot.kind in ('ecu', 'insert', 'close')`` and
    ``zoom > 1.0005``, blends the crop centre 35% toward ``focus_xy`` (clamped safely inside
    ``[0, W] x [0, H]``).
    """
    if progress is not None:
        t_in_shot = progress
    if isinstance(camera_zoom, tuple):
        focus_xy = camera_zoom
        camera_zoom = 1.0
    elif camera_zoom is None:
        camera_zoom = 1.0

    zoom = SHOT_ZOOM.get(shot.kind, 1.0) * float(camera_zoom)
    w, h = img.size

    if zoom > 1.0005 and focus_xy is not None and shot.kind in ("ecu", "insert", "close"):
        crop_w = int(w / zoom)
        crop_h = int(h / zoom)
        cx_default = w / 2.0
        cy_default = h / 2.0
        fx = max(0.0, min(float(w), float(focus_xy[0])))
        fy = max(0.0, min(float(h), float(focus_xy[1])))
        cx = cx_default + 0.35 * (fx - cx_default)
        cy = cy_default + 0.35 * (fy - cy_default)
        x0 = int(round(cx - crop_w / 2.0))
        y0 = int(round(cy - crop_h / 2.0))
        x0 = max(0, min(w - crop_w, x0))
        y0 = max(0, min(h - crop_h, y0))
        cropped = img.crop((x0, y0, x0 + crop_w, y0 + crop_h))
        img = cropped.resize((w, h), Image.BICUBIC)
    else:
        img = _zoom_centre(img, zoom)

    if shot.kind == "impact":
        dx, dy = camera_shake_offset(
            t_in_shot, 0.0, duration=SHAKE_SECONDS, amplitude=SHAKE_AMPLITUDE
        )
        img = _shift(img, dx, dy)
    return img


def _impact_flash(img: Image.Image, shot: Shot, t_in_shot: float) -> Image.Image:
    """Blend the first frames of an impact shot towards white."""
    if shot.kind != "impact":
        return img
    alpha = impact_flash_alpha(t_in_shot, 0.0, frames=IMPACT_FLASH_FRAMES, fps=FPS)
    if alpha <= 0.0:
        return img
    white = Image.new("RGB", img.size, (255, 255, 255))
    return Image.blend(img, white, FLASH_PEAK * alpha)


def _draw_scene(
    sa: SectionAudio,
    title: str,
    index: int,
    plan: BeatPlan,
    shot: Shot,
    t: float,
    t_in_shot: float,
    section_seconds: float,
    camera_zoom: float = 1.0,
) -> Image.Image:
    """One frame: the beat scene at section time ``t``, then shot camera, heading and chrome."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    # The beat runs on section time: the pose, props, sprites and narration start once per
    # section. A shot cut only changes the camera, so nothing replays its entrance at each cut.
    draw_beat(img, plan, t, 0.0, section_seconds, sa.section.narration, sa.words, text=False)
    img = _shot_camera(img, shot, t_in_shot, camera_zoom)
    # Text goes on after the camera, so the zoom cannot push the narration into the letterbox.
    draw_beat_text(img, plan, t, 0.0, section_seconds, sa.section.narration)
    _draw_chrome(ImageDraw.Draw(img), index, sa.section.heading or title, sa.section.narration)
    # The heading sits in the bottom bar, so it is painted after the letterbox.
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    _paint_heading(layer, sa.section.heading, t)
    _commit_layer(img, layer)
    return _impact_flash(img, shot, t_in_shot)


def render_scene_frame(
    sa: SectionAudio,
    title: str = "",
    index: int = 0,
    t: float = 0.0,
    camera_zoom: float = 1.0,
    focus_xy: tuple[float, float] | None = None,
) -> Image.Image:
    """Render a single frame of a section audio beat at section time ``t``."""
    plan = plan_beat(
        index, sa.section.heading, sa.section.narration, _speaker(sa), False
    )
    shots = _section_shots(sa) or [Shot(0.0, max(float(sa.duration), 0.0), "wide")]
    f = round(t * FPS)
    shot, shot_start = _locate_frame(shots, f)
    t_in_shot = max(0.0, f - shot_start) / FPS

    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw_beat(img, plan, t, 0.0, sa.duration, sa.section.narration, sa.words, text=False)
    img = _shot_camera(img, shot, t_in_shot, camera_zoom, focus_xy=focus_xy)
    draw_beat_text(img, plan, t, 0.0, sa.duration, sa.section.narration)
    _draw_chrome(ImageDraw.Draw(img), index, sa.section.heading or title, sa.section.narration)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    _paint_heading(layer, sa.section.heading, t)
    _commit_layer(img, layer)
    return _impact_flash(img, shot, t_in_shot)


render_beat_frame = render_scene_frame


def _transition(kind: str, prev: Image.Image, cur: Image.Image, k: float) -> Image.Image:
    """Blend the previous section's last frame into the next one. ``k`` runs 0 to 1 (eased).

    Live kinds are ``cut`` (hard cut), ``dip`` (fade through black) and ``flash`` (a white
    impact). The legacy slide, zoom, iris, wipe, crossfade and push kinds are kept but unused.
    """
    if kind == "cut":
        return cur
    if kind == "dip":
        black = Image.new("RGB", cur.size, (0, 0, 0))
        if k < 0.5:
            return Image.blend(prev, black, min(1.0, 2.0 * k))
        return Image.blend(black, cur, min(1.0, 2.0 * k - 1.0))
    if kind == "flash":
        flash = impact_flash_alpha(k * TRANSITION_SECONDS, 0.0, frames=FLASH_FRAMES, fps=FPS)
        if flash <= 0.0:
            return cur
        white = Image.new("RGB", cur.size, (255, 255, 255))
        return Image.blend(prev, white, FLASH_PEAK * flash)
    if kind == "slide":
        x = int(k * WIDTH)
        out = Image.new("RGB", (WIDTH, HEIGHT))
        out.paste(prev, (-x, 0))
        out.paste(cur, (WIDTH - x, 0))
        return out
    if kind == "push":
        # The new scene pushes the old one up and off the top of the frame.
        y = int(k * HEIGHT)
        out = Image.new("RGB", (WIDTH, HEIGHT))
        out.paste(prev, (0, -y))
        out.paste(cur, (0, HEIGHT - y))
        return out
    if kind == "crossfade":
        return Image.blend(prev, cur, k)
    if kind == "zoom":
        s = 0.4 + 0.6 * k
        w, h = int(WIDTH * s), int(HEIGHT * s)
        out = prev.copy()
        out.paste(cur.resize((w, h), Image.BILINEAR), ((WIDTH - w) // 2, (HEIGHT - h) // 2))
        return out
    if kind == "iris":
        mask = Image.new("L", (WIDTH, HEIGHT), 0)
        radius = k * math.hypot(WIDTH / 2, HEIGHT / 2)
        cx, cy = WIDTH / 2, HEIGHT / 2
        ImageDraw.Draw(mask).ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=255)
        return Image.composite(cur, prev, mask)
    # wipe: the new scene is revealed behind a flat yellow bar.
    x = int(k * WIDTH)
    out = prev.copy()
    if x > 0:
        out.paste(cur.crop((0, 0, x, HEIGHT)), (0, 0))
    ImageDraw.Draw(out).rectangle([x - 7, 0, x + 7, HEIGHT], fill=YELLOW)
    return out


def render_scene_frames(
    sections: Sequence[SectionAudio],
    title: str,
    max_seconds_per_section: float | None = None,
    scenes: Sequence[str] | None = None,
) -> Iterator[bytes]:
    """Yield raw RGB24 frames (1920x1080) for each scene in order.

    Raises ValueError before drawing if the total duration exceeds MAX_VIDEO_SECONDS.
    ``max_seconds_per_section`` is only for quick previews/tests. ``scenes`` picks the sets
    in order (defaults to DEFAULT_SCENES); section i uses ``scenes[i % len(scenes)]``.
    """
    total = float(sum(sa.duration for sa in sections))
    if total > MAX_VIDEO_SECONDS:
        raise ValueError(f"Video duration {total:.1f}s exceeds the {MAX_VIDEO_SECONDS:.0f}s cap")
    chosen = tuple(scenes) if scenes else DEFAULT_SCENES
    unknown = [name for name in chosen if name not in SCENE_PAINTERS]
    if unknown:
        raise ValueError(f"Unknown scene(s): {unknown}")
    return _frames(list(sections), title, total, max_seconds_per_section, chosen)


def _frames(
    sections: list[SectionAudio],
    title: str,
    total: float,
    max_seconds: float | None,
    scenes: tuple[str, ...],
) -> Iterator[bytes]:
    # ``total`` and ``scenes`` stay in the signature; beats paints its own backdrop per section.
    trans_frames = max(1, int(TRANSITION_SECONDS * FPS))
    last_index = len(sections) - 1
    prev_last: Image.Image | None = None
    for idx, sa in enumerate(sections):
        dur = sa.duration if max_seconds is None else min(sa.duration, max_seconds)
        n = max(1, round(dur * FPS))
        kind = TRANSITIONS[idx % len(TRANSITIONS)]
        plan = plan_beat(
            idx, sa.section.heading, sa.section.narration, _speaker(sa), idx == last_index
        )
        shots = _section_shots(sa) or [Shot(0.0, max(dur, 0.0), "wide")]
        img: Image.Image | None = None
        for f in range(n):
            t = f / FPS
            shot, shot_start = _locate_frame(shots, f)
            camera = _camera_zoom(idx, f, n)
            img = _draw_scene(
                sa, title, idx, plan, shot, t, max(0, f - shot_start) / FPS, sa.duration, camera
            )
            if prev_last is not None and f < trans_frames:
                img = _transition(kind, prev_last, img, ease_in_out_cubic((f + 1) / trans_frames))
                if idx == SHAKE_SECTION:
                    dx, dy = camera_shake_offset(
                        t, 0.0, duration=SHAKE_SECONDS, amplitude=SHAKE_AMPLITUDE
                    )
                    img = _shift(img, dx, dy)
            yield img.tobytes()
        if img is not None:
            prev_last = img
