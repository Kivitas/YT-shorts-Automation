"""Pillow stick-figure renderer for long-form explainer videos (1920x1080, 30 fps)."""

from __future__ import annotations

import logging
import math
from collections.abc import Iterator, Sequence
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from autoshorts.longform.types import MAX_VIDEO_SECONDS, SectionAudio

logger = logging.getLogger(__name__)

WIDTH = 1920
HEIGHT = 1080
FPS = 30
TRANSITION_SECONDS = 0.6

BG = (14, 22, 48)
GRID = (26, 38, 72)
BAR = (8, 13, 30)
WHITE = (245, 247, 252)
YELLOW = (255, 214, 10)
FIGURE = (235, 240, 255)
ACCENT = (80, 160, 255)

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
}  # fmt: skip

POSE_CYCLE = ("stand", "point", "think", "lift", "shrug", "walk", "point", "stand")


def ease_in_out(t: float) -> float:
    """Smoothstep-style ease-in-out on [0, 1]."""
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


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
    """Pose of a figure that holds each pose for ``hold`` s then tweens to the next."""
    period = hold + tween
    idx = int(time_s // period)
    local = time_s - idx * period
    cur = sequence[idx % len(sequence)]
    nxt = sequence[(idx + 1) % len(sequence)]
    if local <= hold:
        return interpolate_pose(cur, cur, 0.0)
    return interpolate_pose(cur, nxt, (local - hold) / tween)


def draw_figure(
    draw: ImageDraw.ImageDraw,
    pose: Pose,
    origin: tuple[float, float],
    scale: float = 2.0,
    color: tuple[int, int, int] = FIGURE,
    width: int = 9,
) -> None:
    """Draw a stick figure; ``origin`` is the hip position in pixels."""
    ox, oy = origin

    def p(j: str) -> tuple[float, float]:
        x, y = pose[j]
        return ox + x * scale, oy + y * scale

    hip = (ox, oy)
    neck = p("neck")
    # Torso, legs, arms.
    draw.line([hip, neck], fill=color, width=width)
    for side in ("l", "r"):
        draw.line(
            [hip, p(f"{side}_knee"), p(f"{side}_foot")], fill=color, width=width, joint="curve"
        )
        draw.line(
            [neck, p(f"{side}_elbow"), p(f"{side}_hand")], fill=color, width=width, joint="curve"
        )
    hx, hy = p("head")
    r = 26 * scale
    draw.ellipse([hx - r, hy - r, hx + r, hy + r], outline=color, fill=BG, width=width)
    # Tiny face: two eyes.
    for dx in (-0.35, 0.35):
        ex, ey = hx + dx * r, hy - 0.1 * r
        draw.ellipse([ex - 3, ey - 3, ex + 3, ey + 3], fill=color)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
_FONT_PATHS = {
    "bold": r"C:\Windows\Fonts\arialbd.ttf",
    "regular": r"C:\Windows\Fonts\arial.ttf",
}
_warned_font = False


def load_font(size: int, bold: bool = True) -> Any:
    """Load a Windows font, or fall back to PIL's default font (logged once)."""
    return _load_font_cached(size, bold)


@lru_cache(maxsize=64)
def _load_font_cached(size: int, bold: bool) -> Any:
    global _warned_font
    path = _FONT_PATHS["bold" if bold else "regular"]
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        if not _warned_font:
            logger.warning("Windows font %s missing; falling back to PIL default font", path)
            _warned_font = True
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # very old Pillow
            return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Frame composition
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _background() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    for x in range(0, WIDTH, 80):
        d.line([(x, 0), (x, HEIGHT)], fill=GRID, width=1)
    for y in range(0, HEIGHT, 80):
        d.line([(0, y), (WIDTH, y)], fill=GRID, width=1)
    return img


def _fit_font(
    draw: ImageDraw.ImageDraw, text: str, size: int, max_w: int, bold: bool = True
) -> Any:
    while size > 18:
        font = load_font(size, bold)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 4
    return load_font(18, bold)


def _caption_chunk(
    words: list[dict[str, Any]], active: int, per_line: int = 7
) -> tuple[int, list[dict[str, Any]]]:
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


def _draw_caption(draw: ImageDraw.ImageDraw, words: list[dict[str, Any]], t: float) -> None:
    draw.rectangle([0, 880, WIDTH, 1010], fill=BAR)
    if not words:
        return
    active = _active_word(words, t)
    base, chunk = _caption_chunk(words, active)
    font = load_font(54, True)
    texts = [str(w.get("word", "")) for w in chunk]
    space = draw.textlength(" ", font=font)
    total = sum(draw.textlength(s, font=font) for s in texts) + space * max(len(texts) - 1, 0)
    x = (WIDTH - total) / 2
    for i, s in enumerate(texts):
        is_active = base + i == active
        draw.text((x, 915), s, font=font, fill=YELLOW if is_active else WHITE)
        x += draw.textlength(s, font=font) + space


def _draw_scene(
    sa: SectionAudio,
    title: str,
    t: float,
    index: int,
    count: int,
    elapsed_before: float,
    total: float,
    cycle: Sequence[str],
) -> Image.Image:
    img = _background().copy()
    d = ImageDraw.Draw(img)

    # Title bar.
    d.rectangle([0, 0, WIDTH, 96], fill=BAR)
    d.line([(0, 96), (WIDTH, 96)], fill=ACCENT, width=3)
    tfont = _fit_font(d, title, 48, WIDTH - 360)
    d.text((48, 22), title, font=tfont, fill=WHITE)
    cfont = load_font(34, True)
    counter = f"{index + 1} / {count}"
    d.text((WIDTH - 48 - d.textlength(counter, font=cfont), 30), counter, font=cfont, fill=YELLOW)

    # Heading card: slides in from the left while fading.
    k = ease_in_out(t / 0.7)
    hfont = _fit_font(d, sa.section.heading, 64, 1000)
    card_w = int(d.textlength(sa.section.heading, font=hfont)) + 90
    final_x = 80
    x = int(-card_w + (final_x + card_w) * k)
    layer = Image.new("RGB", (card_w, 120), (24, 44, 92))
    ld = ImageDraw.Draw(layer)
    ld.rectangle([0, 0, 12, 120], fill=YELLOW)
    ld.text((45, 24), sa.section.heading, font=hfont, fill=WHITE)
    region = img.crop((max(x, 0), 150, max(x, 0) + card_w, 270))
    if x >= 0:
        img.paste(Image.blend(region, layer, k), (x, 150))
    else:
        img.paste(
            Image.blend(img.crop((0, 150, card_w, 270)), layer, k).crop((-x, 0, card_w, 120)),
            (0, 150),
        )

    # Figure.
    pose = pose_at(cycle, t + index * 0.9)
    bob = math.sin(t * 2.2) * 5
    draw_figure(d, pose, (1380, 700 + bob), scale=2.2)
    d.line([(1150, 920 - 100 + 10), (1610, 920 - 100 + 10)], fill=GRID, width=4)

    _draw_caption(d, sa.words, t)

    # Progress bar.
    frac = min(1.0, (elapsed_before + t) / total) if total > 0 else 0.0
    d.rectangle([0, 1030, WIDTH, 1080], fill=BAR)
    d.rectangle([0, 1038, int(WIDTH * frac), 1072], fill=ACCENT)
    return img


def render_scene_frames(
    sections: Sequence[SectionAudio],
    title: str,
    max_seconds_per_section: float | None = None,
) -> Iterator[bytes]:
    """Yield raw RGB24 frames (1920x1080) for each scene in order.

    Raises ValueError before drawing if the total duration exceeds MAX_VIDEO_SECONDS.
    ``max_seconds_per_section`` is only for quick previews/tests.
    """
    total = float(sum(sa.duration for sa in sections))
    if total > MAX_VIDEO_SECONDS:
        raise ValueError(f"Video duration {total:.1f}s exceeds the {MAX_VIDEO_SECONDS:.0f}s cap")
    return _frames(list(sections), title, total, max_seconds_per_section)


def _frames(
    sections: list[SectionAudio], title: str, total: float, max_seconds: float | None
) -> Iterator[bytes]:
    elapsed = 0.0
    prev_last: Image.Image | None = None
    for idx, sa in enumerate(sections):
        dur = sa.duration if max_seconds is None else min(sa.duration, max_seconds)
        n = max(1, round(dur * FPS))
        trans_frames = int(TRANSITION_SECONDS * FPS)
        img: Image.Image | None = None
        for f in range(n):
            t = f / FPS
            img = _draw_scene(sa, title, t, idx, len(sections), elapsed, total, POSE_CYCLE)
            if prev_last is not None and f < trans_frames:
                k = ease_in_out((f + 1) / trans_frames)
                shifted = Image.new("RGB", (WIDTH, HEIGHT), BG)
                shifted.paste(img, (int((1 - k) * 120), 0))
                img = Image.blend(prev_last, shifted, k)
            yield img.tobytes()
        if img is not None:
            prev_last = img
        elapsed += sa.duration
