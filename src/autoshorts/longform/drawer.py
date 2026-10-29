"""Pillow animated studio renderer for long-form videos (1920x1080, 30 fps).

Features articulated 2D characters with real-time viseme lip-sync, expressive brow rigs,
two-character parody banter stages, smooth Ken-Burns camera motion, and 5 specialized
genre visual engines (History, Science, Comparison, Finance, Motivation).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator, Sequence
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from autoshorts.longform.engines import (
    draw_comparison_canvas,
    draw_finance_canvas,
    draw_history_canvas,
    draw_motivation_canvas,
    draw_science_canvas,
)
from autoshorts.longform.types import MAX_VIDEO_SECONDS, VISEME_SHAPES, SectionAudio

logger = logging.getLogger(__name__)

WIDTH = 1920
HEIGHT = 1080
FPS = 30
TRANSITION_SECONDS = 0.7
STAGE_TOP = 96
STAGE_BOTTOM = 880

OUTLINE = (18, 18, 24)
WHITE = (255, 255, 255)
YELLOW = (255, 214, 40)
PINK = (255, 72, 120)
GREEN = (60, 200, 90)
CYAN = (0, 228, 255)
CAPTION_BG = (22, 18, 48)
WOOD = (176, 110, 60)
PANTS = (40, 52, 112)
SHOE = (34, 34, 46)
PEACH = (255, 206, 166)
BROWN_SKIN = (205, 140, 100)
SHIRT_RED = (235, 62, 72)
SHIRT_BLUE = (40, 130, 250)
SHIRT_TEAL = (24, 184, 168)
SHIRT_PURPLE = (150, 90, 235)
HAIR_BROWN = (92, 52, 28)
HAIR_BLACK = (30, 28, 34)
MOUTH_DARK = (120, 24, 44)
TONGUE_PINK = (235, 96, 120)

MAIN_SHIRTS = (SHIRT_RED, SHIRT_BLUE, SHIRT_PURPLE)
MOODS = ("happy", "shocked", "deadpan", "smug", "hyped", "thinking", "angry", "worried")
TRANSITIONS = ("slide", "zoom", "iris", "wipe")
STICKER_KINDS = ("star", "boom", "coin", "check", "arrow")
STICKER_LABELS = ("WOW", "NO WAY", "REAL", "YES", "STOP", "KEY")
STICKER_CYCLE = 5.0  # seconds between pops for one sticker slot
STICKER_SIZE = 62

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
def _blob(
    draw: ImageDraw.ImageDraw, cx: float, cy: float, rx: float, ry: float, fill: Any, out: int
) -> None:
    draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill, outline=OUTLINE, width=out)


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
            outline=OUTLINE,
            width=line_w,
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
            outline=OUTLINE,
            width=line_w,
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
            outline=OUTLINE,
            width=line_w,
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
            outline=OUTLINE,
            width=line_w,
        )
    elif mood in ("excited", "hyped"):
        draw.pieslice(
            [mx - mw, my - 0.2 * r, mx + mw, my + 0.4 * r],
            0,
            180,
            fill=MOUTH_DARK,
            outline=OUTLINE,
            width=line_w,
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
    """Draw an articulated round-headed 2D character with real-time viseme lip-sync.

    ``origin`` is the hip in pixels. ``flip`` mirrors the pose left/right.
    ``mood`` sets the eyebrows and resting mouth; ``viseme`` drives active speech shapes.
    """
    ox, oy = origin
    out = max(2, round(4 * scale))  # outline thickness
    arm_w = round(11 * scale)
    leg_w = round(13 * scale)
    line_w = max(2, round(0.07 * 30 * scale))

    def p(j: str) -> tuple[float, float]:
        x, y = pose[j]
        return ox + x * scale * flip, oy + y * scale

    neck = p("neck")
    hx, hy = p("head")
    r = 30 * scale

    # Optional active-speaker stage floor ring
    if is_speaking:
        foot_y = oy + 104 * scale
        draw.ellipse(
            [ox - 48 * scale, foot_y - 12 * scale, ox + 48 * scale, foot_y + 14 * scale],
            fill=(255, 214, 40),
            outline=OUTLINE,
            width=max(2, out // 2),
        )

    legs = [[(ox, oy), p(f"{s}_knee"), p(f"{s}_foot")] for s in ("l", "r")]
    arms = [[neck, p(f"{s}_elbow"), p(f"{s}_hand")] for s in ("l", "r")]

    # Black outline first, then coloured limbs on top.
    for chain in legs:
        draw.line(chain, fill=OUTLINE, width=leg_w + 2 * out, joint="curve")
    for chain in arms:
        draw.line(chain, fill=OUTLINE, width=arm_w + 2 * out, joint="curve")
    for chain in legs:
        draw.line(chain, fill=PANTS, width=leg_w, joint="curve")
    for chain in arms:
        draw.line(chain, fill=shirt, width=arm_w, joint="curve")

    tw = 17 * scale
    draw.rounded_rectangle(
        [ox - tw, neck[1] - 4 * scale, ox + tw, oy + 8 * scale],
        radius=round(10 * scale), fill=shirt, outline=OUTLINE, width=out,
    )  # fmt: skip

    # Optional pointer stick extending from the pointing hand toward charts/diagrams
    if pointer_stick:
        rhx, rhy = p("r_hand")
        tip_x = rhx + 95 * scale * flip
        tip_y = rhy - 42 * scale
        draw.line([(rhx, rhy), (tip_x, tip_y)], fill=OUTLINE, width=max(4, round(5 * scale)))
        draw.line([(rhx, rhy), (tip_x, tip_y)], fill=YELLOW, width=max(2, round(2.5 * scale)))
        draw.ellipse(
            [tip_x - 6 * scale, tip_y - 6 * scale, tip_x + 6 * scale, tip_y + 6 * scale],
            fill=PINK,
            outline=OUTLINE,
            width=2,
        )

    for chain in arms:
        _blob(draw, chain[-1][0], chain[-1][1], 9 * scale, 9 * scale, skin, out)
    for s in ("l", "r"):
        fx, fy = p(f"{s}_foot")
        _blob(draw, fx, fy, 12 * scale, 7 * scale, SHOE, out)

    # Big round head with a small hair curl on top.
    draw.ellipse([hx - r, hy - r, hx + r, hy + r], fill=skin, outline=OUTLINE, width=out)
    draw.arc(
        [hx - 0.25 * r, hy - 1.28 * r, hx + 0.4 * r, hy - 0.86 * r],
        200,
        330,
        fill=hair,
        width=line_w + 2,
    )

    eye_w, eye_h = 0.36 * r, 0.46 * r
    eye_y = hy - 0.02 * r
    look = 0.1 * r * flip
    for side in (-1, 1):
        ex = hx + side * 0.42 * r
        if blink:
            draw.line([(ex - eye_w / 2, eye_y), (ex + eye_w / 2, eye_y)], fill=OUTLINE, width=line_w)
        else:
            draw.ellipse(
                [ex - eye_w / 2, eye_y - eye_h / 2, ex + eye_w / 2, eye_y + eye_h / 2],
                fill=WHITE, outline=OUTLINE, width=max(2, line_w // 2),
            )  # fmt: skip
            if mood == "deadpan":
                # Half-lidded deadpan expression
                draw.chord(
                    [ex - eye_w / 2, eye_y - eye_h / 2, ex + eye_w / 2, eye_y + eye_h / 2],
                    180,
                    360,
                    fill=skin,
                    outline=OUTLINE,
                    width=max(2, line_w // 2),
                )
            pr = 0.15 * r
            px, py = ex + look, eye_y + 0.04 * r
            draw.ellipse([px - pr, py - pr, px + pr, py + pr], fill=OUTLINE)
        outer, inner = _brow(mood, side, r)
        by = hy - 0.5 * r
        draw.line(
            [(hx + side * 0.8 * r, by + outer), (hx + side * 0.22 * r, by + inner)],
            fill=OUTLINE, width=max(3, line_w),
        )  # fmt: skip

    mx, my = hx, hy + 0.42 * r
    norm_viseme = viseme if viseme in VISEME_SHAPES else "REST"
    _draw_mouth(draw, mx, my, r, line_w, mood, norm_viseme)


# ---------------------------------------------------------------------------
# Stickers and sparkles
# ---------------------------------------------------------------------------
def _star(cx: float, cy: float, outer: float, inner: float, points: int) -> list[tuple[float, float]]:
    pts = []
    for i in range(points * 2):
        rad = outer if i % 2 == 0 else inner
        a = -math.pi / 2 + i * math.pi / points
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    return pts


def _filled_poly(d: ImageDraw.ImageDraw, pts: list[tuple[float, float]], fill: tuple[int, int, int]) -> None:
    d.polygon(pts, fill=fill)
    d.line(pts + [pts[0]], fill=OUTLINE, width=6, joint="curve")


def draw_sticker(
    d: ImageDraw.ImageDraw, kind: str, cx: float, cy: float, size: float, label: str = ""
) -> None:
    """Draw one bright sticker centred on (cx, cy) with radius ``size``."""
    if kind == "star":
        _filled_poly(d, _star(cx, cy, size, size * 0.45, 5), YELLOW)
    elif kind == "coin":
        d.ellipse([cx - size, cy - size, cx + size, cy + size], fill=(255, 196, 40), outline=OUTLINE, width=6)
        d.text((cx, cy), "$", font=load_font(int(size * 1.2)), fill=OUTLINE, anchor="mm")
    elif kind == "check":
        d.ellipse([cx - size, cy - size, cx + size, cy + size], fill=GREEN, outline=OUTLINE, width=6)
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
            d.text((cx, cy), label, font=font, fill=WHITE, anchor="mm", stroke_width=3, stroke_fill=OUTLINE)


def _draw_sparkles(d: ImageDraw.ImageDraw, t: float) -> None:
    for i in range(14):
        x = (i * 211 + t * (30 + (i % 4) * 18)) % WIDTH
        y = STAGE_TOP + 40 + (i * 97) % (STAGE_BOTTOM - STAGE_TOP - 80)
        if math.sin(t * 5 + i) < -0.2:
            continue
        r = 5 + (i % 3) * 2
        d.ellipse([x - r, y - r, x + r, y + r], fill=WHITE if i % 2 else YELLOW)


def _pop(age: float, life: float = 2.8) -> float:
    """Scale factor for a sticker that grows, holds, then shrinks away."""
    if age < 0:
        return 0.0
    grow = ease_out_back(min(1.0, age / 0.35))
    shrink = 1.0 if age < life else max(0.0, 1 - (age - life) / 0.25)
    return grow * shrink


# ---------------------------------------------------------------------------
# Scenes: flat sets painted once per scene and cached + dynamic genre engines.
# ---------------------------------------------------------------------------
def _box(
    d: ImageDraw.ImageDraw, box: Sequence[float], fill: tuple[int, int, int], radius: int = 18, width: int = 7
) -> None:
    d.rounded_rectangle(list(box), radius=radius, fill=fill, outline=OUTLINE, width=width)


def _gradient(img: Image.Image, box: tuple[int, int, int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(img)
    span = max(1, y1 - y0 - 1)
    for y in range(y0, y1):
        k = (y - y0) / span
        d.line([(x0, y), (x1, y)], fill=tuple(round(top[i] + (bottom[i] - top[i]) * k) for i in range(3)))


def _paint_bedroom(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH, 760], fill=(255, 214, 102))
    d.rectangle([0, 760, WIDTH, HEIGHT], fill=(232, 150, 84))
    pts = [(x, 150 + 50 * math.sin(math.pi * x / WIDTH)) for x in range(0, WIDTH + 1, 40)]
    d.line(pts, fill=(120, 90, 40), width=3)
    for x, y in pts[::2]:
        d.ellipse([x - 9, y - 9, x + 9, y + 9], fill=(255, 250, 210), outline=(120, 90, 40), width=3)
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
    d.rectangle([1220, 668, 1244, 880], fill=WOOD, outline=OUTLINE, width=6)
    d.rectangle([1556, 668, 1580, 880], fill=WOOD, outline=OUTLINE, width=6)
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
        d.rectangle([x, 640, x + 70, 656], fill=(90, 90, 100), outline=OUTLINE, width=5)
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
    d.rectangle([0, 560, WIDTH, 610], fill=(250, 250, 245), outline=OUTLINE, width=6)
    for x in range(0, WIDTH, 320):
        d.line([(x, 630), (x, 880)], fill=OUTLINE, width=6)
    _box(d, (1560, 200, 1800, 560), (240, 250, 255))
    d.line([(1560, 400), (1800, 400)], fill=OUTLINE, width=6)
    d.ellipse([290, 190, 370, 270], fill=WHITE, outline=OUTLINE, width=8)
    d.line([(330, 230), (330, 205)], fill=OUTLINE, width=6)
    d.line([(330, 230), (358, 245)], fill=OUTLINE, width=6)
    d.ellipse([650, 528, 770, 562], fill=WHITE, outline=OUTLINE, width=6)
    d.ellipse([682, 500, 728, 540], fill=(255, 80, 80), outline=OUTLINE, width=5)


def _paint_street(img: Image.Image) -> None:
    _gradient(img, (0, 0, WIDTH, 760), (255, 140, 90), (255, 226, 150))
    d = ImageDraw.Draw(img)
    d.ellipse([1380, 260, 1580, 460], fill=(255, 240, 130), outline=OUTLINE, width=8)
    buildings = [
        (60, 200, 430, (70, 60, 120)), (280, 160, 300, (100, 70, 150)),
        (460, 240, 500, (50, 50, 95)), (720, 180, 360, (100, 70, 150)),
        (920, 220, 460, (70, 60, 120)), (1160, 170, 320, (50, 50, 95)),
        (1350, 240, 420, (100, 70, 150)), (1620, 260, 500, (70, 60, 120)),
    ]  # fmt: skip
    for x0, w, h, col in buildings:
        top = 760 - h
        d.rectangle([x0, top, x0 + w, 760], fill=col, outline=OUTLINE, width=6)
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
        d.ellipse([x - 12, y - 12, x + 12, y + 12], fill=WHITE, outline=OUTLINE, width=5)
    _box(d, (90, 200, 430, 520), (255, 240, 190))
    d.line([(260, 200), (260, 520)], fill=OUTLINE, width=8)
    d.rectangle([1080, 640, 1700, 668], fill=WHITE, outline=OUTLINE, width=6)
    d.rectangle([1100, 668, 1120, 880], fill=(230, 230, 240), outline=OUTLINE, width=5)
    d.rectangle([1660, 668, 1680, 880], fill=(230, 230, 240), outline=OUTLINE, width=5)
    _box(d, (1240, 470, 1420, 630), (40, 40, 70))
    _box(d, (1255, 485, 1405, 615), (120, 220, 255), radius=6)
    d.ellipse([160, 560, 260, 720], fill=GREEN, outline=OUTLINE, width=6)
    d.ellipse([240, 520, 340, 690], fill=(40, 180, 100), outline=OUTLINE, width=6)
    _filled_poly(d, [(200, 740), (300, 740), (285, 860), (215, 860)], (255, 130, 60))


def _paint_beach(img: Image.Image) -> None:
    _gradient(img, (0, 0, WIDTH, 640), (255, 170, 90), (255, 218, 140))
    d = ImageDraw.Draw(img)
    d.ellipse([1380, 210, 1620, 450], fill=(255, 246, 170), outline=OUTLINE, width=8)
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
    d.ellipse([1300, 230, 1700, 630], fill=(150, 160, 175), outline=OUTLINE, width=10)
    d.ellipse([1360, 290, 1640, 570], fill=(120, 130, 145), outline=OUTLINE, width=8)
    for k in range(8):
        a = k * math.pi / 4
        d.line([(1500, 430), (1500 + 120 * math.cos(a), 430 + 120 * math.sin(a))], fill=OUTLINE, width=10)
    for i in range(5):
        y = 850 - i * 34
        d.rounded_rectangle([1180, y - 30, 1480, y], radius=8, fill=(120, 220, 120), outline=OUTLINE, width=6)
        d.rectangle([1300, y - 30, 1360, y], fill=WHITE, outline=OUTLINE, width=4)
    for x, y in [(300, 840), (360, 842), (420, 838), (330, 800), (390, 800)]:
        d.ellipse([x - 34, y - 18, x + 34, y + 18], fill=(255, 200, 40), outline=OUTLINE, width=6)
    d.ellipse([720, 600, 880, 840], fill=(245, 245, 225), outline=OUTLINE, width=8)
    d.text((800, 710), "$", font=load_font(140, True), fill=(60, 160, 80), anchor="mm")


def _paint_parody(img: Image.Image) -> None:
    _gradient(img, (0, 0, WIDTH, 760), (42, 28, 74), (24, 18, 44))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 760, WIDTH, HEIGHT], fill=(58, 38, 68))
    # Paint-explainer style chalkboard / comedy studio backdrop
    _box(d, (660, 185, 1260, 520), (248, 244, 228), radius=18, width=8)
    d.line([(720, 420), (880, 320), (1020, 390), (1190, 250)], fill=PINK, width=10, joint="curve")
    d.text((960, 230), "EXPECTATION vs REALITY", font=load_font(28, True), fill=OUTLINE, anchor="mm")


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
_FONT_PATHS = {
    "bold": r"C:\Windows\Fonts\arialbd.ttf",
    "regular": r"C:\Windows\Fonts\arial.ttf",
}
_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "bold": (
        r"C:\Windows\Fonts\arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "Arial Bold",
        "DejaVuSans-Bold",
    ),
    "regular": (
        r"C:\Windows\Fonts\arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "Arial",
        "DejaVuSans",
    ),
}
_warned_font = False


def load_font(size: int, bold: bool = True) -> Any:
    """Load a system TrueType font (Windows, macOS, Linux), or fall back to PIL's default font."""
    return _load_font_cached(size, bold)


@lru_cache(maxsize=64)
def _load_font_cached(size: int, bold: bool) -> Any:
    global _warned_font
    key = "bold" if bold else "regular"
    candidates = (_FONT_PATHS[key], *_FONT_CANDIDATES[key])
    for path in dict.fromkeys(candidates):
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


def _fit_font(draw: ImageDraw.ImageDraw, text: str, size: int, max_w: float, bold: bool = True) -> Any:
    while size > 18:
        font = load_font(size, bold)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 4
    return load_font(18, bold)


def _caption_chunk(words: list[dict[str, Any]], active: int, per_line: int = 7) -> tuple[int, list[dict[str, Any]]]:
    start = (max(active, 0) // per_line) * per_line
    return start, words[start : start + per_line]


def _active_word(words: list[dict[str, Any]], t: float) -> int:
    active = -1
    for i, w in enumerate(words):
        if float(w.get("start", 0.0)) <= t:
            active = i
        else:
            break
    return active


def _draw_caption(d: ImageDraw.ImageDraw, words: list[dict[str, Any]], t: float) -> None:
    d.rectangle([0, STAGE_BOTTOM, WIDTH, 1030], fill=CAPTION_BG)
    d.line([(0, STAGE_BOTTOM), (WIDTH, STAGE_BOTTOM)], fill=PINK, width=6)
    if not words:
        return
    active = _active_word(words, t)
    base, chunk = _caption_chunk(words, active)
    font = load_font(56, True)
    texts = [str(w.get("word", "")) for w in chunk]
    space = d.textlength(" ", font=font)
    total = sum(d.textlength(s, font=font) for s in texts) + space * max(len(texts) - 1, 0)
    x = (WIDTH - total) / 2
    for i, s in enumerate(texts):
        is_active = base + i == active
        d.text(
            (x, 912), s, font=font, fill=YELLOW if is_active else WHITE,
            stroke_width=4, stroke_fill=OUTLINE,
        )  # fmt: skip
        x += d.textlength(s, font=font) + space


def _draw_chrome(
    d: ImageDraw.ImageDraw, title: str, index: int, count: int, t: float, words: list[dict[str, Any]], frac: float
) -> None:
    d.rectangle([0, 0, WIDTH, 96], fill=PINK)
    tfont = _fit_font(d, title, 48, WIDTH - 360)
    d.text((48, 22), title, font=tfont, fill=WHITE, stroke_width=3, stroke_fill=OUTLINE)
    cfont = load_font(34, True)
    counter = f"{index + 1} / {count}"
    d.text(
        (WIDTH - 48 - d.textlength(counter, font=cfont), 28), counter, font=cfont,
        fill=YELLOW, stroke_width=3, stroke_fill=OUTLINE,
    )  # fmt: skip
    _draw_caption(d, words, t)
    d.rectangle([0, 1030, WIDTH, 1080], fill=CAPTION_BG)
    d.rectangle([0, 1036, int(WIDTH * frac), 1074], fill=YELLOW)


def _draw_heading(img: Image.Image, heading: str, t: float) -> None:
    """Heading sticker: a tilted yellow card that bounces in from the left."""
    probe = ImageDraw.Draw(img)
    font = _fit_font(probe, heading, 52, 900)
    w = int(probe.textlength(heading, font=font)) + 72
    h = 104
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle([0, 0, w - 1, h - 1], radius=20, fill=YELLOW, outline=OUTLINE, width=7)
    cd.text((36, 18), heading, font=font, fill=OUTLINE)
    card = card.rotate(-2, resample=Image.BICUBIC, expand=True)
    k = ease_out_back(min(1.0, t / 0.55))
    x = int(-w + (44 + w) * k)
    img.paste(card, (x, 114), card)


# ---------------------------------------------------------------------------
# Scene composition & Camera motion
# ---------------------------------------------------------------------------
def _blinking(t: float, offset: int) -> bool:
    return (t + offset * 0.37) % 3.4 < 0.12


def _layout(index: int) -> tuple[int, int, bool]:
    """Return (main character x, facing flip, whether a friend sits on the bed)."""
    slot = index % 3
    if slot == 0:
        return 1400, -1, True
    if slot == 1:
        return 520, 1, False
    return 960, 1, False


def _sticker_plan(index: int, main_x: int) -> list[tuple[int, float, float, float]]:
    """(slot, x, y, first pop time) for the stickers in one section."""
    if main_x == 960:
        slots = [(250, 420), (1670, 420)]
    else:
        slots = [(1700, 380), (1700, 640)]
    return [(n, x, y, 0.9 + n * 2.0) for n, (x, y) in enumerate(slots)]


def _resolve_engine_kind(sa: SectionAudio, scene: str) -> str:
    vm = getattr(sa.section, "visual_mode", "auto") or "auto"
    if vm.startswith("history") or scene == "history":
        return "history"
    if vm.startswith("science") or scene == "science":
        return "science"
    if vm.startswith("comparison") or scene == "comparison":
        return "comparison"
    if vm.startswith("finance") or scene == "finance":
        return "finance"
    if vm.startswith("motivation") or scene == "motivation":
        return "motivation"
    if vm.startswith("parody") or scene == "parody" or getattr(sa.section, "speaker", "narrator") in ("skeptic", "cohost"):
        return "parody"
    return "classic"


def _apply_camera_motion(img: Image.Image, t: float, dur: float, engine_kind: str, speaker: str) -> Image.Image:
    """Apply subtle Ken-Burns zoom/pan or parody deadpan punch-in on the stage area."""
    zoom = 1.0 + 0.032 * (0.5 - 0.5 * math.cos(t * 0.55))
    focus_x, focus_y = WIDTH * 0.5, HEIGHT * 0.48
    if engine_kind == "parody" and dur > 1.0 and (t > dur * 0.78 or speaker in ("skeptic", "cohost")):
        # Deadpan punch-in toward the active speaker
        zoom = 1.06
        focus_x = 1360.0 if speaker in ("skeptic", "cohost") else 560.0
    if zoom <= 1.002:
        return img
    crop_w = int(WIDTH / zoom)
    crop_h = int(HEIGHT / zoom)
    x0 = max(0, min(WIDTH - crop_w, int(focus_x - crop_w / 2)))
    y0 = max(0, min(HEIGHT - crop_h, int(focus_y - crop_h / 2)))
    cropped = img.crop((x0, y0, x0 + crop_w, y0 + crop_h))
    return cropped.resize((WIDTH, HEIGHT), Image.BILINEAR)


def _draw_scene(
    sa: SectionAudio,
    title: str,
    t: float,
    index: int,
    count: int,
    elapsed_before: float,
    total: float,
    scene: str,
) -> Image.Image:
    img = _scene_background(scene).copy()
    d = ImageDraw.Draw(img)
    engine_kind = _resolve_engine_kind(sa, scene)
    active_viseme = viseme_at(sa.words, t)
    speaker = (getattr(sa.section, "speaker", "narrator") or "narrator").lower()
    sub_domain = getattr(sa.section, "sub_domain", "") or ""
    callout = getattr(sa.section, "hook_callout", "") or ""
    comp_data = getattr(sa.section, "comparison_data", None)
    bob = math.sin(t * 3.5) * 6.0

    if engine_kind in ("history", "science", "comparison", "finance", "motivation"):
        if engine_kind == "history":
            draw_history_canvas(img, d, t, index, sa.section.heading, callout, sub_domain, load_font)
        elif engine_kind == "science":
            draw_science_canvas(img, d, t, index, sa.section.heading, callout, sub_domain, load_font)
        elif engine_kind == "comparison":
            draw_comparison_canvas(img, d, t, index, sa.section.heading, comp_data, sub_domain, load_font)
        elif engine_kind == "finance":
            draw_finance_canvas(img, d, t, index, sa.section.heading, callout, load_font)
        else:
            draw_motivation_canvas(img, d, t, index, sa.section.heading, callout, sub_domain, load_font)

        # Articulated presenter character on the left stage pointing at the live canvas
        enter = ease_out_back(min(1.0, t / 0.55))
        mx = 265 - (1.0 - enter) * 360
        my = 655 + bob
        cur_pose = pose_at(("present", "point", "think", "present", "stand"), t + index * 0.7)
        draw_character(
            d,
            cur_pose,
            (mx, my),
            scale=1.85,
            shirt=MAIN_SHIRTS[index % len(MAIN_SHIRTS)],
            mood=MOODS[index % len(MOODS)],
            flip=1,
            blink=_blinking(t, index),
            viseme=active_viseme,
            pointer_stick=True,
            is_speaking=active_viseme != "REST",
        )
    elif engine_kind == "parody":
        _draw_sparkles(d, t)
        enter = ease_out_back(min(1.0, t / 0.55))
        # Two-Character Banter Stage: Character A (Narrator) on left, Character B (Skeptic) on right
        narr_speaking = speaker not in ("skeptic", "cohost", "friend")
        lx = 440 - (1.0 - enter) * 450
        ly = 648 + bob
        rx = 1480 + (1.0 - enter) * 450
        ry = 648 + math.sin(t * 3.5 + 1.4) * 5.0

        draw_character(
            d,
            pose_at(BANTER_CYCLE_A, t + index * 0.6),
            (lx, ly),
            scale=1.9,
            shirt=SHIRT_BLUE,
            skin=PEACH,
            hair=HAIR_BROWN,
            mood="smug" if narr_speaking else "shocked",
            flip=1,
            blink=_blinking(t, index),
            viseme=active_viseme if narr_speaking else "REST",
            is_speaking=narr_speaking and active_viseme != "REST",
        )
        draw_character(
            d,
            pose_at(BANTER_CYCLE_B, t + index * 0.8),
            (rx, ry),
            scale=1.9,
            shirt=SHIRT_TEAL,
            skin=BROWN_SKIN,
            hair=HAIR_BLACK,
            mood="deadpan" if not narr_speaking else "thinking",
            flip=-1,
            blink=_blinking(t, index + 3),
            viseme=active_viseme if not narr_speaking else "REST",
            is_speaking=(not narr_speaking) and active_viseme != "REST",
        )
    else:
        _draw_sparkles(d, t)
        main_x, flip, friend = _layout(index)
        direction = (main_x > 960) - (main_x < 960)
        enter = ease_out_back(min(1.0, t / 0.6))
        mx = main_x + (1 - enter) * 700 * direction
        my = 640 + bob
        if direction == 0:
            my -= (1 - enter) * 260

        if friend:
            fenter = ease_out_back(min(1.0, (t - 0.25) / 0.6))
            fx = 520 - (1 - fenter) * 600
            fy = 600 + math.sin(t * 3.2 + 1.0) * 4
            friend_speaking = speaker in ("skeptic", "cohost", "friend")
            draw_character(
                d,
                pose_at(FRIEND_CYCLE, t + index * 0.5),
                (fx, fy),
                shirt=SHIRT_TEAL,
                skin=BROWN_SKIN,
                hair=HAIR_BLACK,
                mood="shocked" if index % 2 else "deadpan",
                flip=1,
                blink=_blinking(t, index + 3),
                viseme=active_viseme if friend_speaking else "REST",
                is_speaking=friend_speaking and active_viseme != "REST",
            )
        main_speaking = speaker not in ("skeptic", "cohost", "friend")
        draw_character(
            d,
            pose_at(POSE_CYCLE, t + index * 0.9),
            (mx, my),
            shirt=MAIN_SHIRTS[index % len(MAIN_SHIRTS)],
            mood=MOODS[index % len(MOODS)],
            flip=flip,
            blink=_blinking(t, index),
            viseme=active_viseme if main_speaking else "REST",
            is_speaking=main_speaking and active_viseme != "REST",
        )

        for slot, sx, sy, start in _sticker_plan(index, main_x):
            if t < start:
                continue
            age = (t - start) % STICKER_CYCLE
            cycle_no = int((t - start) // STICKER_CYCLE)
            j = index * 2 + slot + cycle_no
            size = STICKER_SIZE * _pop(age)
            if size >= 2:
                kind = STICKER_KINDS[j % len(STICKER_KINDS)]
                draw_sticker(d, kind, sx, sy, size, STICKER_LABELS[j % len(STICKER_LABELS)])

    img = _apply_camera_motion(img, t, sa.duration, engine_kind, speaker)
    _draw_heading(img, sa.section.heading, t)
    d = ImageDraw.Draw(img)
    frac = min(1.0, (elapsed_before + t) / total) if total > 0 else 0.0
    _draw_chrome(d, title, index, count, t, sa.words, frac)
    return img


def _transition(kind: str, prev: Image.Image, cur: Image.Image, k: float) -> Image.Image:
    """Blend the previous section's last frame into the next one. ``k`` runs 0 to 1."""
    if kind == "slide":
        x = int(k * WIDTH)
        out = Image.new("RGB", (WIDTH, HEIGHT))
        out.paste(prev, (-x, 0))
        out.paste(cur, (WIDTH - x, 0))
        return out
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
    # wipe: the new scene is revealed behind a bright bar.
    x = int(k * WIDTH)
    out = prev.copy()
    if x > 0:
        out.paste(cur.crop((0, 0, x, HEIGHT)), (0, 0))
    ImageDraw.Draw(out).rectangle([x - 14, 0, x + 14, HEIGHT], fill=YELLOW, outline=OUTLINE, width=6)
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
    elapsed = 0.0
    trans_frames = max(1, int(TRANSITION_SECONDS * FPS))
    prev_last: Image.Image | None = None
    for idx, sa in enumerate(sections):
        dur = sa.duration if max_seconds is None else min(sa.duration, max_seconds)
        n = max(1, round(dur * FPS))
        scene = scenes[idx % len(scenes)]
        kind = TRANSITIONS[idx % len(TRANSITIONS)]
        img: Image.Image | None = None
        for f in range(n):
            img = _draw_scene(sa, title, f / FPS, idx, len(sections), elapsed, total, scene)
            if prev_last is not None and f < trans_frames:
                img = _transition(kind, prev_last, img, ease_in_out((f + 1) / trans_frames))
            yield img.tobytes()
        if img is not None:
            prev_last = img
        elapsed += sa.duration
