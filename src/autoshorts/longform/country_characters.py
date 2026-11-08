"""Original hand-drawn country stereotype characters for longform meme segments.

Each figure is drawn with Pillow only: jittered ink outlines of varying weight,
flat white fill, light grey hatching on the shadow side and a few country props.
The designs are original; nothing here reproduces an existing meme or cartoon.

Rendered PNGs (RGBA, 600x1000, transparent background) are cached under
``assets/characters/`` on first use and are not committed.
"""

from __future__ import annotations

import math
import os
import random
import re
import unicodedata
import zlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

CANVAS_W = 600
CANVAS_H = 1000
CHARACTERS_DIR = Path(__file__).resolve().parents[3] / "assets" / "characters"
EXPRESSIONS: tuple[str, ...] = ("neutral", "smug", "annoyed", "tired", "sad")

_SS = 2  # supersampling factor; the canvas is downscaled with LANCZOS
_INK = (16, 16, 16, 255)
_WHITE = (255, 255, 255, 255)
_GREY = (190, 190, 190, 255)
_DARK = (105, 105, 105, 255)
_SHADE_X = 330  # hatching only right of this x: light comes from the left


@dataclass(frozen=True)
class _Face:
    eyes: str
    brows: tuple[float, float, float, float]  # left outer, left inner, right inner, right outer
    mouth: str
    tear: bool = False


_FACES: dict[str, _Face] = {
    "neutral": _Face("dot", (0, 0, 0, 0), "soft"),
    "smug": _Face("lid", (-12, -10, 2, 2), "smirk"),
    "annoyed": _Face("dot", (-8, 10, 10, -8), "wave"),
    "tired": _Face("heavy", (4, 2, 2, 4), "o"),
    "sad": _Face("dot", (10, -10, -10, 10), "frown", tear=True),
}

_MOUTHS: dict[str, list[tuple[float, float]]] = {
    "soft": [(282, 302), (300, 306), (318, 302)],
    "smirk": [(280, 306), (300, 309), (316, 305), (330, 293)],
    "wave": [(276, 303), (288, 298), (300, 304), (312, 298), (324, 303)],
    "frown": [(274, 314), (288, 302), (300, 298), (312, 302), (326, 314)],
}


@dataclass(frozen=True)
class _Spec:
    outfit: str
    head: str = "boxy"  # round, boxy or tall: the head silhouette
    eyes: str | None = None  # fixed eye style overriding the expression's eyes
    warp: tuple[float, float] = (1.0, 1.0)  # body proportions: (width, height) about the centre
    ear_size: float = 12.0


_SPECS: dict[str, _Spec] = {
    "canada": _Spec("canada", head="round", warp=(1.08, 0.96), ear_size=18.0),
    "new_zealand": _Spec("new_zealand", head="tall", eyes="crescent", warp=(0.9, 1.04)),
}
_GENERIC = _Spec("generic")
_TORSO_PATTERN = {"canada": "plaid", "new_zealand": "hoops", "generic": "shade"}


def country_slug(country: str) -> str:
    """Return a lowercase ASCII slug such as ``new_zealand``; ValueError if nothing is left."""
    if not isinstance(country, str):
        raise TypeError("country must be a string")
    ascii_text = unicodedata.normalize("NFKD", country).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")[:80].rstrip("_")
    if not slug:
        raise ValueError(f"country name has no usable characters: {country!r}")
    return slug


def _check_expression(expression: str) -> str:
    if expression not in EXPRESSIONS:
        raise ValueError(f"unknown expression {expression!r}; use one of {EXPRESSIONS}")
    return expression


def _cache_path(slug: str, expression: str) -> Path:
    name = f"{slug}.png" if expression == "neutral" else f"{slug}.{expression}.png"
    return CHARACTERS_DIR / name


def _seed_for(slug: str) -> int:
    return zlib.crc32(slug.encode("ascii"))


# --- geometry -------------------------------------------------------------------------------


def _arc(cx, cy, rx, ry, a0, a1, steps=18):
    return [
        (cx + rx * math.cos(a), cy + ry * math.sin(a))
        for a in (a0 + (a1 - a0) * i / steps for i in range(steps + 1))
    ]


def _ellipse(cx, cy, rx, ry, steps=24):
    return _arc(cx, cy, rx, ry, 0.0, math.tau, steps)[:-1]


def _superellipse(cx, cy, rx, ry, power=2.6, steps=44):
    pts = []
    for i in range(steps):
        t = math.tau * i / steps
        c, s = math.cos(t), math.sin(t)
        x = cx + rx * math.copysign(abs(c) ** (2 / power), c)
        y = cy + ry * math.copysign(abs(s) ** (2 / power), s)
        pts.append((x, y))
    return pts


def _chain(points, widths):
    """Outline of a tapered limb that follows a polyline (arm or leg)."""
    left, right = [], []
    last = len(points) - 1
    for i, (x, y) in enumerate(points):
        if i == 0:
            dx, dy = points[1][0] - x, points[1][1] - y
        elif i == last:
            dx, dy = x - points[i - 1][0], y - points[i - 1][1]
        else:
            dx = points[i + 1][0] - points[i - 1][0]
            dy = points[i + 1][1] - points[i - 1][1]
        length = math.hypot(dx, dy) or 1.0
        nx = -dy / length * widths[i] / 2
        ny = dx / length * widths[i] / 2
        left.append((x + nx, y + ny))
        right.append((x - nx, y - ny))
    return left + right[::-1]


def _densify(pts, closed, step=14.0):
    ring = list(pts) + ([pts[0]] if closed else [])
    out = []
    for (x0, y0), (x1, y1) in zip(ring, ring[1:], strict=False):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        out.extend((x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(n))
    if not closed:
        out.append(ring[-1])
    return out


def _wobble(pts, rng, amp):
    """Hand jitter: a slow sway plus small per-point noise, all drawn from ``rng``."""
    phase = rng.uniform(0, math.tau)
    out = []
    for i, (x, y) in enumerate(pts):
        sway = amp * math.sin(i * 0.8 + phase)
        dx = sway * 0.5 + rng.gauss(0, amp * 0.35)
        dy = -sway * 0.3 + rng.gauss(0, amp * 0.35)
        out.append((x + dx, y + dy))
    return out


# --- drawing --------------------------------------------------------------------------------


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow older than 10.1 has no sized default font
        return ImageFont.load_default()


def _pattern_lines(pattern: str, size: tuple[int, int], rng: random.Random):
    """Return (colour, L-mode line mask) for a fill pattern covering the whole canvas."""
    w, h = size
    lines = Image.new("L", size, 0)
    d = ImageDraw.Draw(lines)
    if pattern == "shade":
        for x0 in range(-h, w, 13 * _SS):
            jit = rng.uniform(-2.0, 2.0) * _SS
            d.line([(x0 + jit, 0), (x0 + jit + h, h)], fill=255, width=2 * _SS)
        d.rectangle((0, 0, _SHADE_X * _SS, h), fill=0)
        return _GREY, lines
    if pattern == "plaid":
        for x in range(0, w, 36 * _SS):
            d.line([(x, 0), (x, h)], fill=255, width=3 * _SS)
        for y in range(10 * _SS, h, 48 * _SS):
            d.line([(0, y), (w, y)], fill=255, width=3 * _SS)
        return _GREY, lines
    if pattern == "hoops":
        for y in range(0, h, 90 * _SS):
            d.rectangle((0, y, w, y + 26 * _SS), fill=255)
        return _INK, lines
    if pattern == "ribs":
        for x in range(0, w, 11 * _SS):
            d.line([(x, 0), (x, h)], fill=255, width=2 * _SS)
        return _DARK, lines
    raise ValueError(f"unknown pattern {pattern!r}")


class _Ink:
    """Accumulates hand-drawn strokes on a supersampled transparent canvas."""

    def __init__(self, seed: int, warp: tuple[float, float] = (1.0, 1.0)) -> None:
        self.seed = seed
        self.warp = warp
        self.size = (CANVAS_W * _SS, CANVAS_H * _SS)
        self.canvas = Image.new("RGBA", self.size, (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.canvas)

    def _xf(self, pts):
        sx, sy = self.warp
        return [(300 + (x - 300) * sx, 500 + (y - 500) * sy) for x, y in pts]

    def _rng(self, key: str) -> random.Random:
        # One stream per named part, so geometry is identical across expressions.
        return random.Random(f"{self.seed}:{key}")

    def shape(self, pts, key, *, fill=_WHITE, pattern=None, width=3.0) -> None:
        rng = self._rng(key)
        ring = _wobble(_densify(self._xf(pts), closed=True), rng, 1.0)
        poly = [(x * _SS, y * _SS) for x, y in ring]
        self.draw.polygon(poly, fill=fill)
        if pattern:
            self._pattern(poly, pattern, rng)
        self._stroke(ring, rng, width, closed=True)

    def line(self, pts, key, width=3.0) -> None:
        rng = self._rng(key)
        ring = _wobble(_densify(self._xf(pts), closed=False), rng, 0.8)
        self._stroke(ring, rng, width, closed=False)

    def dot(self, x, y, r, key) -> None:
        self.shape(_ellipse(x, y, r, r, steps=12), key, fill=_INK, width=1.0)

    def label(self, x, y, text, size) -> None:
        font = _font(size * _SS)
        self.draw.text((x * _SS, y * _SS), text, fill=_INK, font=font, anchor="mm")

    def finish(self) -> Image.Image:
        return self.canvas.resize((CANVAS_W, CANVAS_H), Image.Resampling.LANCZOS)

    def _pattern(self, poly, pattern, rng) -> None:
        mask = Image.new("L", self.size, 0)
        ImageDraw.Draw(mask).polygon(poly, fill=255)
        color, lines = _pattern_lines(pattern, self.size, rng)
        layer = Image.new("RGBA", self.size, color)
        layer.putalpha(ImageChops.multiply(mask, lines))
        self.canvas.alpha_composite(layer)

    def _stroke(self, ring, rng, width, closed) -> None:
        self._pass(ring, rng, width, closed)
        if rng.random() < 0.3:  # sketchy second, lighter pass
            self._pass(_wobble(ring, rng, 1.2), rng, width * 0.45, closed)

    def _pass(self, pts, rng, width, closed) -> None:
        nxt = pts[1:] + pts[:1] if closed else pts[1:]
        segs = zip(pts, nxt, strict=False)
        for (x0, y0), (x1, y1) in segs:
            w = max(1, round(width * _SS * rng.uniform(0.6, 1.3)))
            self.draw.line([(x0 * _SS, y0 * _SS), (x1 * _SS, y1 * _SS)], fill=_INK, width=w)
        r = max(1, round(width * _SS / 2))
        for x, y in pts:  # round joins hide gaps between segments of different widths
            self.draw.ellipse([x * _SS - r, y * _SS - r, x * _SS + r, y * _SS + r], fill=_INK)


def _draw_body(ink: _Ink, outfit: str) -> None:
    ink.shape([(266, 338), (334, 338), (338, 410), (262, 410)], "neck", pattern="shade")
    for side, hip_x, knee_x, ankle_x in (("L", 252, 248, 246), ("R", 348, 352, 354)):
        leg = [(hip_x, 640), (knee_x, 800), (ankle_x, 936)]
        ink.shape(_chain(leg, [62, 54, 40]), f"leg{side}", pattern="shade")
    ink.shape(_ellipse(238, 952, 36, 16, steps=14), "shoeL")
    ink.shape(_ellipse(362, 952, 36, 16, steps=14), "shoeR")
    torso = [(204, 404), (262, 392), (338, 392), (396, 404), (412, 520), (396, 652),
             (204, 652), (188, 520)]  # fmt: skip
    ink.shape(torso, "torso", pattern=_TORSO_PATTERN[outfit], width=3.4)
    ink.line([(266, 392), (300, 426), (334, 392)], "collar", width=4.0)
    if outfit == "new_zealand":
        shorts = [(200, 632), (400, 632), (414, 736), (306, 748), (300, 716), (294, 748),
                  (186, 736)]  # fmt: skip
        ink.shape(shorts, "shorts", fill=_DARK, width=3.2)
    ink.shape(_chain([(206, 420), (170, 528), (150, 624)], [46, 38, 32]), "armL")
    ink.shape(_chain([(394, 420), (430, 528), (456, 606)], [46, 38, 32]), "armR")
    if outfit == "new_zealand":
        ink.shape([(220, 784), (277, 790), (279, 822), (222, 816)], "bandage", width=2.5)
        ink.line([(228, 792), (272, 814)], "bandage-x", width=2.0)


def _canada_props(ink: _Ink) -> None:
    ink.shape([(100, 520), (166, 506), (182, 606), (116, 620)], "clipboard", width=3.0)
    ink.shape([(126, 496), (152, 499), (154, 516), (128, 514)], "clip", fill=_DARK, width=2.0)
    for n, (y0, y1) in enumerate(((548, 540), (572, 566), (596, 590))):
        ink.line([(118, y0), (166, y1)], f"clipline{n}", width=2.0)
    ink.shape([(440, 540), (480, 540), (474, 604), (446, 604)], "cup")
    ink.shape([(442, 566), (478, 566), (476, 588), (444, 588)], "sleeve", fill=_GREY)
    ink.shape([(435, 528), (485, 528), (485, 541), (435, 541)], "lid", fill=_DARK, width=2.0)


def _pennant(ink: _Ink, letter: str) -> None:
    ink.line([(458, 612), (468, 330)], "pole", width=6.0)
    ink.shape([(468, 330), (566, 352), (468, 376)], "pennant", width=3.0)
    ink.label(502, 353, letter, size=34)


def _draw_props(ink: _Ink, outfit: str, letter: str) -> None:
    if outfit == "canada":
        _canada_props(ink)
    elif outfit == "generic":
        _pennant(ink, letter)
    ink.shape(_ellipse(150, 628, 20, 20, steps=14), "handL", width=2.6)
    ink.shape(_ellipse(456, 606, 21, 21, steps=14), "handR", width=2.6)


def _nz_tuft() -> list[tuple[float, float]]:
    """Spiky hair tuft falling onto the forehead."""
    return [(236, 176), (250, 126), (272, 150), (292, 88), (308, 150), (326, 114),
            (350, 150), (366, 178), (300, 198)]  # fmt: skip


def _curly_hair() -> list[tuple[float, float]]:
    """Scalloped, curly hair crown with a bumpy outer edge."""
    outer, inner = [], []
    for i in range(48):
        a = math.pi * 0.97 + math.pi * 1.06 * i / 47
        bump = 1 + 0.05 * math.cos(9 * a)
        outer.append((300 + 96 * bump * math.cos(a), 252 + 112 * bump * math.sin(a)))
    for i in range(47, -1, -1):
        a = math.pi * 0.97 + math.pi * 1.06 * i / 47
        inner.append((300 + 84 * math.cos(a), 258 + 100 * math.sin(a)))
    return outer + inner


def _draw_headgear(ink: _Ink, outfit: str) -> None:
    if outfit == "new_zealand":
        ink.shape(_nz_tuft(), "hair", fill=_DARK, pattern="shade", width=3.0)
    elif outfit == "generic":
        ink.shape(_curly_hair(), "hair", fill=_DARK, pattern="shade", width=3.0)
    elif outfit == "canada":
        ink.shape(_arc(300, 186, 104, 114, math.pi, 2 * math.pi, 22), "toque", pattern="ribs")
        band = [(190, 150), (410, 150), (414, 186), (186, 186)]
        ink.shape(band, "toque-band", pattern="ribs", width=3.0)
        ink.shape(_ellipse(300, 70, 18, 18, steps=14), "pom", pattern="shade", width=2.6)


def _head_outline(kind: str) -> tuple[list[tuple[float, float]], float]:
    """Return (head outline, x of the ear attachment line) for a silhouette kind."""
    if kind == "round":
        return _superellipse(300, 250, 102, 106, power=2.0, steps=48), 200.0
    if kind == "boxy":
        return _superellipse(300, 252, 88, 104, power=6.0, steps=56), 212.0
    if kind == "tall":
        pts = [(300, 112), (362, 164), (380, 246), (356, 334), (300, 362), (244, 334),
               (220, 246), (238, 164)]  # fmt: skip
        return pts, 220.0
    raise ValueError(f"unknown head kind {kind!r}")


def _draw_head(ink: _Ink, spec: _Spec) -> None:
    outline, ear_x = _head_outline(spec.head)
    s = spec.ear_size
    ink.line(_arc(ear_x, 252, s, 1.6 * s, math.pi / 2, 1.5 * math.pi, 10), "earL", width=3.2)
    ink.line(_arc(600 - ear_x, 252, s, 1.6 * s, -math.pi / 2, math.pi / 2, 10), "earR", width=3.2)
    ink.shape(outline, "head", pattern="shade", width=3.6)


def _eye(ink: _Ink, style: str, x: float, y: float, key: str) -> None:
    if style == "dot":
        ink.dot(x, y, 8.0, key)
    elif style == "lid":
        ink.line([(x - 15, y - 5), (x + 15, y - 5)], key + "-lid", width=5.0)
        ink.dot(x, y + 2, 4.5, key)
    elif style == "heavy":
        ink.line([(x - 16, y - 7), (x + 16, y - 7)], key + "-lid", width=6.0)
        ink.line(_arc(x, y + 4, 13, 8, 0.1 * math.pi, 0.9 * math.pi, 8), key + "-bag", 2.0)
        ink.dot(x, y + 2, 4.0, key)
    elif style == "crescent":  # upward bowl: a friendly squint
        ink.line(_arc(x, y + 8, 15, 13, 1.14 * math.pi, 1.86 * math.pi, 12), key, width=4.0)
    else:
        raise ValueError(f"unknown eye style {style!r}")


def _draw_face(ink: _Ink, expression: str, eyes_override: str | None) -> None:
    face = _FACES[expression]
    lo, li, ri, ro = face.brows
    ink.line([(238, 200 + lo), (280, 200 + li)], "browL", width=5.0)
    ink.line([(320, 200 + ri), (362, 200 + ro)], "browR", width=5.0)
    style = eyes_override or face.eyes
    _eye(ink, style, 262.0, 236.0, "eyeL")
    _eye(ink, style, 338.0, 236.0, "eyeR")
    ink.line([(300, 254), (293, 276), (302, 281)], "nose", width=3.0)
    if face.mouth == "o":
        ink.shape(_ellipse(300, 306, 9, 11, steps=14), "mouth", fill=_INK, width=2.0)
    else:
        ink.line(_MOUTHS[face.mouth], "mouth", width=5.0)
    if face.tear:
        ink.shape([(262, 252), (267, 262), (262, 270), (257, 262)], "tear", width=2.0)


def draw_character(country: str, expression: str = "neutral") -> Image.Image:
    """Render the character for ``country`` as an RGBA image (600x1000). Nothing is saved."""
    slug = country_slug(country)
    expr = _check_expression(expression)
    spec = _SPECS.get(slug, _GENERIC)
    ink = _Ink(_seed_for(slug), spec.warp)
    _draw_body(ink, spec.outfit)
    _draw_props(ink, spec.outfit, slug[:1].upper())
    _draw_head(ink, spec)
    _draw_headgear(ink, spec.outfit)
    _draw_face(ink, expr, spec.eyes)
    return ink.finish()


def character_for_country(country: str, expression: str = "neutral") -> Path:
    """Return the cached PNG for ``country``/``expression``, rendering it on first use."""
    slug = country_slug(country)
    expr = _check_expression(expression)
    path = _cache_path(slug, expr)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    draw_character(country, expr).save(tmp, format="PNG")
    os.replace(tmp, path)
    return path
