"""Parts-based character composer for longform meme segments.

``spec_from_context`` maps a context dict (country, setting, emotion, keywords taken
from the line, and optional explicit body attributes) to a :class:`CharacterSpec` by
fixed tables and a seeded hash. ``draw_character_from_spec`` draws the named parts
with Pillow in a hand-drawn ink style at 2x and downscales to 600x1000 RGBA.
``character_for_context`` caches the PNG under ``assets/characters/<spec hash>.png``.

Body attributes (gender presentation, skin tone, height, build) are never tied to
country. They come from the explicit context value when one is given, otherwise from
a seeded hash of the setting and keywords (the emotion and country are left out, so
the same person keeps the same body across expressions and countries).

All artwork is original and drawn from primitives in this module; no existing meme or
cartoon character is reproduced. Rendering is deterministic: the same spec always gives
identical pixels.

Keyword table (first matching keyword in the line wins, case-insensitive):
    props:   bill, invoice, receipt -> bill; phone, hold, call, wait, waiting -> phone;
             tea -> tea_cup; coffee -> coffee_cup; pasta, lasagna, spaghetti -> pasta_pot;
             pastry, croissant, pie, donut -> pastry; rugby, ball -> ball;
             bandage, plaster -> bandage; clipboard, form, forms -> clipboard;
             flag, pennant -> pennant; bag, luggage -> bag; sign, notice -> sign;
             stethoscope, doctor -> stethoscope
    hair:    cap, hat -> cap; toque -> toque; bun -> bun; curls, curly -> curls;
             helmet, hardhat, construction -> helmet; long -> long; short -> short
    torso:   rugby, jersey, team -> jersey; coat -> coat; gown, wedding -> gown;
             jacket -> jacket; lab, scrubs -> lab_coat; shirt -> shirt
    head:    ears, ear -> big_ears

Setting table (first matching rule wins; sets the default torso and prop):
    hospital, clinic, icu, er, emergency, ward, surgery, doctor -> lab_coat, stethoscope
    waiting, queue, lobby, reception -> jacket, phone
    office, bank, government, council, tax, desk -> jacket, clipboard
    kitchen, restaurant, bistro -> shirt, pasta_pot
    cafe, bakery, coffee -> shirt, coffee_cup
    stadium, field, gym, pitch -> jersey, ball
    wedding, gala, party -> gown, none
    airport, street, station, road, park -> coat, bag

Country accents (only where the keyword and setting tables say nothing):
    Canada -> round head, toque hair (unless a keyword sets hair)
    New Zealand (or NZ) -> tall head, jersey torso
    other countries -> boxy head, shirt torso, seeded hair
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import re
import unicodedata
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

CANVAS_W = 600
CANVAS_H = 1000
CHARACTERS_DIR = Path(__file__).resolve().parents[3] / "assets" / "characters"

HEADS = ("round", "boxy", "tall", "big_ears")
HAIRS = ("none", "short", "long", "cap", "toque", "bun", "curls", "helmet")
TORSOS = ("shirt", "jacket", "coat", "jersey", "gown", "lab_coat")
PROPS = (
    "none", "clipboard", "coffee_cup", "tea_cup", "stethoscope", "phone", "bill",
    "pasta_pot", "pastry", "pennant", "bandage", "bag", "sign", "ball",
)  # fmt: skip
EXPRESSIONS = ("neutral", "smug", "annoyed", "tired", "sad", "happy", "shocked")
GENDERS = ("female", "male", "androgynous")
HEIGHTS = ("short", "average", "tall")
BUILDS = ("slim", "average", "broad")
LEGWEAR = ("trousers", "skirt")
SKIN_TONES: tuple[tuple[int, int, int, int], ...] = (
    (250, 226, 204, 255),
    (238, 200, 166, 255),
    (218, 168, 126, 255),
    (186, 128, 88, 255),
    (142, 90, 58, 255),
    (96, 58, 36, 255),
)

_SS = 2  # supersampling factor; the canvas is downscaled with LANCZOS
_INK = (16, 16, 16, 255)
_WHITE = (255, 255, 255, 255)
_GREY = (190, 190, 190, 255)
_DARK = (105, 105, 105, 255)
_SHADE_X = 330  # hatching only right of this x: light comes from the left
_PIVOT_Y = 338  # height and build transforms keep the neck line fixed
_LEFT_HAND = (150.0, 628.0)
_RIGHT_HAND = (456.0, 606.0)

_HEIGHT_SCALE = {"short": 0.9, "average": 1.0, "tall": 1.05}
_BUILD_SCALE = {"slim": 0.94, "average": 1.0, "broad": 1.07}
_BODY_OPTIONS: dict[str, tuple] = {
    "gender": GENDERS,
    "skin_tone": tuple(range(len(SKIN_TONES))),
    "height": HEIGHTS,
    "build": BUILDS,
}
_HAIR_BY_GENDER = {
    "female": ("long", "bun", "none"),
    "male": ("short", "cap", "none"),
    "androgynous": ("short", "bun", "curls", "none"),
}


@dataclass(frozen=True)
class CharacterSpec:
    """One character: a choice from each parts library plus body attributes."""

    head: str = "boxy"
    hair: str = "none"
    torso: str = "shirt"
    expression: str = "neutral"
    prop: str = "none"
    gender: str = "androgynous"
    skin_tone: int = 2
    height: str = "average"
    build: str = "average"
    legwear: str = "trousers"


_ALLOWED: dict[str, tuple] = {
    "head": HEADS,
    "hair": HAIRS,
    "torso": TORSOS,
    "expression": EXPRESSIONS,
    "prop": PROPS,
    "gender": GENDERS,
    "skin_tone": _BODY_OPTIONS["skin_tone"],
    "height": HEIGHTS,
    "build": BUILDS,
    "legwear": LEGWEAR,
}

_EMOTION_ALIASES = {
    "angry": "annoyed", "irritated": "annoyed", "frustrated": "annoyed",
    "bored": "tired", "exhausted": "tired", "worried": "sad", "crying": "sad",
    "upset": "sad", "cocky": "smug", "proud": "smug", "surprised": "shocked",
    "excited": "happy", "glad": "happy",
}  # fmt: skip
_COUNTRY_ALIASES = {"nz": "new zealand"}
_COUNTRY_ACCENTS: dict[str, dict[str, str]] = {
    "canada": {"head": "round", "hair": "toque"},
    "new zealand": {"head": "tall", "torso": "jersey"},
}
_KEYWORD_PROPS = {
    "bill": "bill", "invoice": "bill", "receipt": "bill",
    "phone": "phone", "hold": "phone", "call": "phone", "wait": "phone", "waiting": "phone",
    "tea": "tea_cup", "coffee": "coffee_cup",
    "pasta": "pasta_pot", "lasagna": "pasta_pot", "spaghetti": "pasta_pot",
    "pastry": "pastry", "croissant": "pastry", "pie": "pastry", "donut": "pastry",
    "rugby": "ball", "ball": "ball",
    "bandage": "bandage", "plaster": "bandage",
    "clipboard": "clipboard", "form": "clipboard", "forms": "clipboard",
    "flag": "pennant", "pennant": "pennant",
    "bag": "bag", "luggage": "bag",
    "sign": "sign", "notice": "sign",
    "stethoscope": "stethoscope", "doctor": "stethoscope",
}  # fmt: skip
_KEYWORD_HAIR = {
    "cap": "cap", "hat": "cap", "toque": "toque", "bun": "bun",
    "curls": "curls", "curly": "curls", "helmet": "helmet", "hardhat": "helmet",
    "construction": "helmet", "long": "long", "short": "short",
}  # fmt: skip
_KEYWORD_TORSOS = {
    "rugby": "jersey", "jersey": "jersey", "team": "jersey", "coat": "coat",
    "gown": "gown", "wedding": "gown", "jacket": "jacket", "lab": "lab_coat",
    "scrubs": "lab_coat", "shirt": "shirt",
}  # fmt: skip
_KEYWORD_HEADS = {"ears": "big_ears", "ear": "big_ears"}
_SETTING_RULES: tuple[tuple[frozenset[str], str, str | None], ...] = (
    (frozenset({"hospital", "clinic", "icu", "er", "emergency", "ward", "surgery", "doctor"}),
     "lab_coat", "stethoscope"),
    (frozenset({"waiting", "queue", "lobby", "reception"}), "jacket", "phone"),
    (frozenset({"office", "bank", "government", "council", "tax", "desk"}), "jacket", "clipboard"),
    (frozenset({"kitchen", "restaurant", "bistro"}), "shirt", "pasta_pot"),
    (frozenset({"cafe", "bakery", "coffee"}), "shirt", "coffee_cup"),
    (frozenset({"stadium", "field", "gym", "pitch"}), "jersey", "ball"),
    (frozenset({"wedding", "gala", "party"}), "gown", None),
    (frozenset({"airport", "street", "station", "road", "park"}), "coat", "bag"),
)  # fmt: skip

_FACE_EYES = {
    "neutral": "dot", "smug": "lid", "annoyed": "dot", "tired": "heavy",
    "sad": "dot", "happy": "crescent", "shocked": "wide",
}  # fmt: skip
_BROWS = {
    "neutral": (0, 0, 0, 0), "smug": (-12, -10, 2, 2), "annoyed": (-8, 10, 10, -8),
    "tired": (4, 2, 2, 4), "sad": (10, -10, -10, 10), "happy": (-6, -6, -6, -6),
    "shocked": (-14, -12, -12, -14),
}  # fmt: skip
_MOUTH = {
    "neutral": "soft", "smug": "smirk", "annoyed": "wave", "tired": "flat",
    "sad": "frown", "happy": "grin", "shocked": "o",
}  # fmt: skip
_MOUTHS: dict[str, list[tuple[float, float]]] = {
    "soft": [(282, 302), (300, 306), (318, 302)],
    "smirk": [(280, 306), (300, 309), (316, 305), (330, 293)],
    "wave": [(276, 303), (288, 298), (300, 304), (312, 298), (324, 303)],
    "frown": [(274, 314), (288, 302), (300, 298), (312, 302), (326, 314)],
    "flat": [(278, 306), (300, 306), (322, 305)],
}


# --- spec mapping ---------------------------------------------------------------------------


def _text(value) -> str:
    if isinstance(value, str):
        return value
    return "" if value is None else str(value)


def _words(text: str) -> list[str]:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z]+", ascii_text.lower())


def _keyword_words(value) -> list[str]:
    if isinstance(value, str):
        items: list = [value]
    elif isinstance(value, list | tuple):
        items = [v for v in value if isinstance(v, str)]
    else:
        return []
    words: list[str] = []
    for item in items:
        words.extend(_words(item))
    return words


def _first_match(words: list[str], table: dict[str, str]) -> str | None:
    for word in words:
        if word in table:
            return table[word]
    return None


def _country_key(text: str) -> str:
    key = " ".join(_words(text))
    return _COUNTRY_ALIASES.get(key, key)


def _emotion_key(text: str) -> str:
    key = " ".join(_words(text))
    key = _EMOTION_ALIASES.get(key, key)
    return key if key in EXPRESSIONS else "neutral"


def _setting_defaults(words: set[str]) -> tuple[str | None, str | None]:
    for rule_words, torso, prop in _SETTING_RULES:
        if words & rule_words:
            return torso, prop
    return None, None


def _seeded_index(seed_text: str, key: str, count: int) -> int:
    return zlib.crc32(f"{key}|{seed_text}".encode()) % count


def _explicit(value, options: tuple) -> object | None:
    """Return an explicit context value if it is one of ``options``, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in options:
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key.isdigit() and int(key) in options:
            return int(key)
        if key in options:
            return key
    return None


def _resolve_body(data: dict, seed_text: str) -> dict:
    body: dict[str, object] = {}
    for name, options in _BODY_OPTIONS.items():
        explicit = _explicit(data.get(name), options)
        body[name] = explicit if explicit is not None else options[
            _seeded_index(seed_text, name, len(options))
        ]  # fmt: skip
    if body["gender"] == "female":
        body["legwear"] = LEGWEAR[_seeded_index(seed_text, "legwear", len(LEGWEAR))]
    else:
        body["legwear"] = "trousers"
    return body


def spec_from_context(ctx) -> CharacterSpec:
    """Map a context dict to a CharacterSpec. Never raises on unknown or malformed text."""
    data = ctx if isinstance(ctx, dict) else {}
    country = _country_key(_text(data.get("country")))
    accent = _COUNTRY_ACCENTS.get(country, {})
    setting_words = _words(_text(data.get("setting")))
    keywords = _keyword_words(data.get("keywords"))
    seed_text = " ".join(setting_words) + "|" + " ".join(keywords)
    body = _resolve_body(data, seed_text)
    setting_torso, setting_prop = _setting_defaults(set(setting_words))
    hair_pool = _HAIR_BY_GENDER[str(body["gender"])]
    seeded_hair = hair_pool[_seeded_index(seed_text, "hair", len(hair_pool))]
    torso = (
        _first_match(keywords, _KEYWORD_TORSOS)
        or setting_torso
        or accent.get("torso")
        or "shirt"
    )  # fmt: skip
    prop = _first_match(keywords, _KEYWORD_PROPS) or setting_prop or "none"
    return CharacterSpec(
        head=_first_match(keywords, _KEYWORD_HEADS) or accent.get("head", "boxy"),
        hair=_first_match(keywords, _KEYWORD_HAIR) or accent.get("hair") or seeded_hair,
        torso=torso,
        expression=_emotion_key(_text(data.get("emotion"))),
        prop=prop,
        gender=str(body["gender"]),
        skin_tone=int(body["skin_tone"]),  # type: ignore[call-overload]
        height=str(body["height"]),
        build=str(body["build"]),
        legwear=str(body["legwear"]),
    )


def normalise_spec(spec) -> CharacterSpec:
    """Return ``spec`` with every unknown field replaced by its default."""
    defaults = CharacterSpec()
    if isinstance(spec, CharacterSpec):
        values: Mapping = {name: getattr(spec, name) for name in _ALLOWED}
    elif isinstance(spec, Mapping):
        values = spec
    else:
        values = {}
    clean = {}
    for name, allowed in _ALLOWED.items():
        value = values.get(name, getattr(defaults, name))
        clean[name] = value if value in allowed else getattr(defaults, name)
    return CharacterSpec(**clean)


def spec_hash(spec) -> str:
    """Stable 16-character hex digest of the normalised spec (cache file name)."""
    return hashlib.sha256(repr(normalise_spec(spec)).encode("utf-8")).hexdigest()[:16]


def _seed(spec: CharacterSpec) -> int:
    return zlib.crc32(repr(spec).encode("utf-8"))


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


def _bezier(p0, p1, p2, n=12):
    """Quadratic curve from p0 to p2 bending toward p1 (used for limbs with a bend)."""
    out = []
    for i in range(n):
        t = i / (n - 1)
        a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
        out.append((a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1]))
    return out


def _taper(w0: float, w1: float, n: int) -> list[float]:
    return [w0 + (w1 - w0) * i / (n - 1) for i in range(n)]


def _chain(points, widths):
    """Outline of a tapered limb that follows a polyline."""
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


# --- ink ------------------------------------------------------------------------------------


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
        return [(300 + (x - 300) * sx, _PIVOT_Y + (y - _PIVOT_Y) * sy) for x, y in pts]

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
        px, py = self._xf([(x, y)])[0]
        self.draw.text((px * _SS, py * _SS), text, fill=_INK, font=_font(size * _SS), anchor="mm")

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
        for (x0, y0), (x1, y1) in zip(pts, nxt, strict=False):
            w = max(1, round(width * _SS * rng.uniform(0.6, 1.3)))
            self.draw.line([(x0 * _SS, y0 * _SS), (x1 * _SS, y1 * _SS)], fill=_INK, width=w)
        r = max(1, round(width * _SS / 2))
        for x, y in pts:  # round joins hide gaps between segments of different widths
            self.draw.ellipse([x * _SS - r, y * _SS - r, x * _SS + r, y * _SS + r], fill=_INK)


# --- body parts -----------------------------------------------------------------------------

_BOX = [(204, 404), (262, 392), (338, 392), (396, 404), (412, 520), (396, 652),
        (204, 652), (188, 520)]  # fmt: skip
_LAPELS = [(262, 392), (300, 470), (338, 392)]


def _draw_legs(ink: _Ink, legwear: str) -> None:
    for side, hip, ctrl, ankle in (("L", 252, 232, 246), ("R", 348, 368, 354)):
        pts = _bezier((hip, 640), (ctrl, 800), (ankle, 936), 12)
        ink.shape(_chain(pts, _taper(62, 40, 12)), f"leg{side}", pattern="shade")
    ink.shape(_ellipse(238, 952, 36, 16, steps=14), "shoeL")
    ink.shape(_ellipse(362, 952, 36, 16, steps=14), "shoeR")
    if legwear == "skirt":
        skirt = [(206, 600), (394, 600), (430, 724), (170, 724)]
        ink.shape(skirt, "skirt", fill=_GREY, pattern="shade", width=3.2)


def _torso_shirt(ink: _Ink) -> None:
    ink.shape(_BOX, "torso", pattern="shade", width=3.4)
    ink.line([(266, 392), (300, 426), (334, 392)], "collar", width=4.0)


def _torso_jacket(ink: _Ink) -> None:
    ink.shape(_BOX, "torso", pattern="shade", width=3.4)
    ink.line([(262, 392), (286, 470), (300, 492)], "lapelL", width=3.0)
    ink.line([(338, 392), (314, 470), (300, 492)], "lapelR", width=3.0)
    ink.shape([(330, 520), (372, 520), (372, 556), (330, 556)], "pocket", width=2.2)


def _torso_coat(ink: _Ink) -> None:
    coat = [(204, 404), (262, 392), (338, 392), (396, 404), (420, 560), (430, 716),
            (300, 730), (170, 716), (180, 560)]  # fmt: skip
    ink.shape(coat, "coat", fill=_GREY, pattern="shade", width=3.4)
    ink.line([(300, 430), (300, 716)], "coat-front", width=2.6)
    ink.dot(300, 500, 5, "coat-btn1")
    ink.dot(300, 590, 5, "coat-btn2")


def _torso_jersey(ink: _Ink) -> None:
    ink.shape(_BOX, "torso", pattern="hoops", width=3.4)
    ink.line([(266, 392), (300, 426), (334, 392)], "collar", width=4.0)


def _torso_gown(ink: _Ink) -> None:
    gown = [(204, 404), (262, 392), (338, 392), (396, 404), (410, 520), (452, 740),
            (148, 740), (190, 520)]  # fmt: skip
    ink.shape(gown, "gown", pattern="shade", width=3.4)
    ink.line([(200, 540), (400, 540)], "sash", width=4.0)


def _torso_lab_coat(ink: _Ink) -> None:
    lab = [(204, 404), (262, 392), (338, 392), (396, 404), (410, 520), (400, 700),
           (200, 700), (190, 520)]  # fmt: skip
    ink.shape(lab, "labcoat", pattern="shade", width=3.4)
    ink.line(_LAPELS, "lab-lapel", width=3.0)
    ink.shape([(328, 520), (370, 520), (370, 556), (328, 556)], "lab-pocket", width=2.2)
    ink.line([(346, 500), (346, 530)], "lab-pen", width=3.0)


_TORSO_DRAW = {
    "shirt": _torso_shirt,
    "jacket": _torso_jacket,
    "coat": _torso_coat,
    "jersey": _torso_jersey,
    "gown": _torso_gown,
    "lab_coat": _torso_lab_coat,
}


def _draw_arms(ink: _Ink, skin) -> None:
    ink.shape(_chain(_bezier((206, 420), (160, 520), (150, 624)), _taper(46, 32, 12)), "armL")
    ink.shape(_chain(_bezier((394, 420), (440, 520), (456, 606)), _taper(46, 32, 12)), "armR")
    ink.shape(_ellipse(150, 628, 20, 20, steps=14), "handL", fill=skin, width=2.6)
    ink.shape(_ellipse(456, 606, 21, 21, steps=14), "handR", fill=skin, width=2.6)


def _draw_body(ink: _Ink, spec: CharacterSpec) -> None:
    skin = SKIN_TONES[spec.skin_tone]
    ink.shape([(266, 338), (334, 338), (338, 410), (262, 410)], "neck", fill=skin,
              pattern="shade")  # fmt: skip
    _draw_legs(ink, spec.legwear)
    _TORSO_DRAW[spec.torso](ink)
    _draw_arms(ink, skin)


# --- props ----------------------------------------------------------------------------------


def _prop_clipboard(ink: _Ink, hx: float, hy: float) -> None:
    ink.shape([(hx - 44, hy - 96), (hx + 6, hy - 100), (hx + 14, hy - 4), (hx - 36, hy)],
              "clipboard", width=3.0)  # fmt: skip
    ink.shape([(hx - 18, hy - 106), (hx + 4, hy - 108), (hx + 6, hy - 92), (hx - 16, hy - 90)],
              "clip", fill=_DARK, width=2.0)  # fmt: skip
    for n, dy in enumerate((-70, -50, -30)):
        ink.line([(hx - 36, hy + dy), (hx + 6, hy + dy - 4)], f"clipline{n}", width=2.0)


def _prop_coffee_cup(ink: _Ink, hx: float, hy: float) -> None:
    ink.shape([(hx - 18, hy - 70), (hx + 18, hy - 70), (hx + 14, hy - 4), (hx - 14, hy - 4)],
              "cup")  # fmt: skip
    ink.shape([(hx - 16, hy - 44), (hx + 16, hy - 44), (hx + 15, hy - 24), (hx - 15, hy - 24)],
              "sleeve", fill=_GREY)  # fmt: skip
    ink.shape([(hx - 20, hy - 76), (hx + 20, hy - 76), (hx + 20, hy - 68), (hx - 20, hy - 68)],
              "lid", fill=_DARK, width=2.0)  # fmt: skip


def _prop_tea_cup(ink: _Ink, hx: float, hy: float) -> None:
    ink.shape(_ellipse(hx, hy, 28, 8, 14), "saucer", fill=_GREY, width=2.4)
    ink.shape([(hx - 18, hy - 46), (hx + 18, hy - 46), (hx + 14, hy - 14), (hx - 14, hy - 14)],
              "teacup")  # fmt: skip
    ink.line(_arc(hx + 18, hy - 30, 9, 9, -math.pi / 2, math.pi / 2, 8), "teahandle", 3.0)
    ink.line([(hx - 6, hy - 62), (hx - 10, hy - 70), (hx - 2, hy - 78)], "steam1", width=2.2)
    ink.line([(hx + 6, hy - 62), (hx + 2, hy - 70), (hx + 10, hy - 78)], "steam2", width=2.2)


def _prop_stethoscope(ink: _Ink, hx: float, hy: float) -> None:
    ink.line([(262, 394), (266, 480), (292, 536)], "steth-l", width=4.0)
    ink.line([(338, 394), (334, 480), (308, 536)], "steth-r", width=4.0)
    ink.shape(_ellipse(300, 548, 13, 13, 14), "steth-disc", fill=_GREY, width=2.6)


def _prop_phone(ink: _Ink, hx: float, hy: float) -> None:
    ink.shape([(hx - 22, hy - 70), (hx + 22, hy - 70), (hx + 22, hy + 6), (hx - 22, hy + 6)],
              "phone", fill=_DARK, width=2.8)  # fmt: skip
    ink.shape([(hx - 16, hy - 62), (hx + 16, hy - 62), (hx + 16, hy - 2), (hx - 16, hy - 2)],
              "phone-screen", fill=_GREY, width=1.6)  # fmt: skip


def _prop_bill(ink: _Ink, hx: float, hy: float) -> None:
    ink.shape([(hx - 40, hy - 80), (hx + 40, hy - 80), (hx + 40, hy - 6), (hx - 40, hy - 6)],
              "bill", width=3.0)  # fmt: skip
    for n, dy in enumerate((-62, -46, -30)):
        ink.line([(hx - 26, hy + dy), (hx + 26, hy + dy)], f"billline{n}", width=2.0)
    ink.line([(hx + 4, hy - 14), (hx + 26, hy - 14)], "bill-total", width=3.0)
    ink.shape(_ellipse(hx, hy - 80, 46, 9, 14), "bill-roll-top", fill=_GREY, width=2.4)
    ink.shape(_ellipse(hx, hy - 6, 46, 9, 14), "bill-roll-bottom", fill=_GREY, width=2.4)


def _prop_pasta_pot(ink: _Ink, hx: float, hy: float) -> None:
    ink.shape([(hx - 40, hy - 44), (hx + 40, hy - 44), (hx + 34, hy + 14), (hx - 34, hy + 14)],
              "pot", fill=_GREY, pattern="shade", width=3.0)  # fmt: skip
    ink.line([(hx - 46, hy - 44), (hx + 46, hy - 44)], "pot-rim", width=4.0)
    for side in (-1, 1):
        ink.line([(hx + side * 40, hy - 30), (hx + side * 56, hy - 30)], f"pot-h{side}", 4.0)
    zig = [(hx - 30, hy - 58), (hx - 18, hy - 70), (hx - 6, hy - 58), (hx + 6, hy - 70),
           (hx + 18, hy - 58), (hx + 30, hy - 70)]  # fmt: skip
    ink.line(zig, "pasta", width=3.0)


def _prop_pastry(ink: _Ink, hx: float, hy: float) -> None:
    ink.shape(_ellipse(hx, hy - 24, 28, 16, 18), "pastry", fill=_GREY, pattern="ribs")
    ink.shape(_ellipse(hx - 26, hy - 12, 14, 9, 12), "pastry-l", width=2.6)
    ink.shape(_ellipse(hx + 26, hy - 12, 14, 9, 12), "pastry-r", width=2.6)


def _prop_pennant(ink: _Ink, hx: float, hy: float) -> None:
    ink.line([(hx, hy), (hx, hy - 150)], "pole", width=6.0)
    ink.shape([(hx, hy - 150), (hx + 80, hy - 126), (hx, hy - 102)], "pennant", width=3.0)


def _prop_bandage(ink: _Ink, hx: float, hy: float) -> None:
    ink.shape([(hx - 44, hy - 22), (hx + 40, hy - 40), (hx + 46, hy - 18), (hx - 38, hy + 4)],
              "bandage", width=2.5)  # fmt: skip
    ink.line([(hx - 30, hy - 14), (hx + 30, hy - 30)], "bandage-x", width=2.0)
    ink.line([(hx - 12, hy), (hx + 8, hy - 34)], "bandage-y", width=2.0)


def _prop_bag(ink: _Ink, hx: float, hy: float) -> None:
    ink.shape([(hx - 34, hy - 70), (hx + 34, hy - 70), (hx + 38, hy + 6), (hx - 38, hy + 6)],
              "bag", width=3.0)  # fmt: skip
    ink.line(_arc(hx, hy - 70, 20, 22, math.pi, 2 * math.pi, 12), "bag-handle", width=3.6)


def _prop_sign(ink: _Ink, hx: float, hy: float) -> None:
    ink.line([(hx, hy), (hx, hy - 100)], "sign-pole", width=6.0)
    ink.shape([(hx - 62, hy - 196), (hx + 62, hy - 196), (hx + 62, hy - 102), (hx - 62, hy - 102)],
              "sign", width=3.2)  # fmt: skip
    ink.label(hx, hy - 150, "!", size=64)


def _prop_ball(ink: _Ink, hx: float, hy: float) -> None:
    ink.shape(_ellipse(hx, hy - 18, 30, 18, 18), "ball", fill=_DARK, width=3.0)
    ink.line([(hx - 12, hy - 18), (hx + 12, hy - 18)], "ball-lace", width=3.0)


_PROP_DRAW = {
    "clipboard": _prop_clipboard,
    "coffee_cup": _prop_coffee_cup,
    "tea_cup": _prop_tea_cup,
    "stethoscope": _prop_stethoscope,
    "phone": _prop_phone,
    "bill": _prop_bill,
    "pasta_pot": _prop_pasta_pot,
    "pastry": _prop_pastry,
    "pennant": _prop_pennant,
    "bandage": _prop_bandage,
    "bag": _prop_bag,
    "sign": _prop_sign,
    "ball": _prop_ball,
}
_LEFT_HAND_PROPS = frozenset({"clipboard", "bag"})


def _draw_props(ink: _Ink, prop: str) -> None:
    if prop == "none":
        return
    hx, hy = _LEFT_HAND if prop in _LEFT_HAND_PROPS else _RIGHT_HAND
    _PROP_DRAW[prop](ink, hx, hy)


# --- head, hair and face --------------------------------------------------------------------


def _head_outline(kind: str) -> list[tuple[float, float]]:
    if kind == "round":
        return _superellipse(300, 250, 102, 106, power=2.0, steps=48)
    if kind == "big_ears":
        return _superellipse(300, 250, 96, 104, power=2.0, steps=48)
    if kind == "boxy":
        return _superellipse(300, 252, 88, 104, power=6.0, steps=56)
    pts = [(300, 112), (362, 164), (380, 246), (356, 334), (300, 362), (244, 334),
           (220, 246), (238, 164)]  # fmt: skip
    return pts  # tall


_EAR_X = {"round": 200.0, "boxy": 212.0, "tall": 220.0, "big_ears": 196.0}
_EAR_SIZE = {"round": 12.0, "boxy": 12.0, "tall": 12.0, "big_ears": 30.0}


def _draw_head(ink: _Ink, head: str, skin) -> None:
    ear_x, s = _EAR_X[head], _EAR_SIZE[head]
    ink.line(_arc(ear_x, 252, s, 1.6 * s, math.pi / 2, 1.5 * math.pi, 10), "earL", width=3.2)
    ink.line(_arc(600 - ear_x, 252, s, 1.6 * s, -math.pi / 2, math.pi / 2, 10), "earR", width=3.2)
    ink.shape(_head_outline(head), "head", fill=skin, pattern="shade", width=3.6)


def _noop(ink: _Ink) -> None:
    return None


def _hair_long_back(ink: _Ink) -> None:
    pts = _arc(300, 236, 116, 124, math.pi, 2 * math.pi, 22)
    pts += [(416, 360), (404, 424), (196, 424), (184, 360)]
    ink.shape(pts, "hair-long", fill=_DARK, pattern="shade", width=3.0)


def _hair_short(ink: _Ink) -> None:
    ink.shape(_arc(300, 228, 106, 110, math.pi * 0.96, math.pi * 2.04, 22), "hair-short",
              fill=_DARK, pattern="shade", width=3.0)  # fmt: skip


def _hair_cap(ink: _Ink) -> None:
    ink.shape(_arc(300, 204, 104, 96, math.pi, 2 * math.pi, 22) + [(404, 204)], "cap",
              fill=_DARK, pattern="shade", width=3.0)  # fmt: skip
    ink.shape([(296, 198), (430, 210), (428, 226), (296, 216)], "cap-brim", fill=_DARK,
              width=3.0)  # fmt: skip


def _hair_toque(ink: _Ink) -> None:
    ink.shape(_arc(300, 200, 104, 128, math.pi, 2 * math.pi, 22), "toque", pattern="ribs")
    ink.shape([(190, 172), (410, 172), (414, 212), (186, 212)], "toque-band", pattern="ribs",
              width=3.0)  # fmt: skip
    ink.shape(_ellipse(300, 76, 18, 18, steps=14), "pom", pattern="shade", width=2.6)


def _hair_bun(ink: _Ink) -> None:
    ink.shape(_arc(300, 206, 100, 96, math.pi, 2 * math.pi, 20), "bun-hair", fill=_DARK,
              pattern="shade", width=3.0)  # fmt: skip
    ink.shape(_ellipse(300, 112, 30, 28, 16), "bun", fill=_DARK, pattern="shade", width=2.6)


def _hair_curls(ink: _Ink) -> None:
    outer, inner = [], []
    for i in range(48):
        a = math.pi * 0.97 + math.pi * 1.06 * i / 47
        bump = 1 + 0.05 * math.cos(9 * a)
        outer.append((300 + 96 * bump * math.cos(a), 252 + 112 * bump * math.sin(a)))
    for i in range(47, -1, -1):
        a = math.pi * 0.97 + math.pi * 1.06 * i / 47
        inner.append((300 + 84 * math.cos(a), 258 + 100 * math.sin(a)))
    ink.shape(outer + inner, "curls", fill=_DARK, pattern="shade", width=3.0)


def _hair_helmet(ink: _Ink) -> None:
    ink.shape(_arc(300, 214, 112, 102, math.pi, 2 * math.pi, 22), "helmet", pattern="ribs")
    ink.shape([(172, 212), (428, 212), (436, 226), (164, 226)], "helmet-brim", width=3.0)


_HAIR_FRONT = {
    "none": _noop, "long": _noop, "short": _hair_short, "cap": _hair_cap,
    "toque": _hair_toque, "bun": _hair_bun, "curls": _hair_curls, "helmet": _hair_helmet,
}  # fmt: skip
_HAIR_BACK = {"long": _hair_long_back}


def _eye(ink: _Ink, style: str, x: float, y: float, key: str) -> None:
    if style == "dot":
        ink.dot(x, y, 10.0, key)
    elif style == "lid":
        ink.line([(x - 15, y - 5), (x + 15, y - 5)], key + "-lid", width=5.0)
        ink.dot(x, y + 2, 6.0, key)
    elif style == "heavy":
        ink.line([(x - 16, y - 7), (x + 16, y - 7)], key + "-lid", width=6.0)
        ink.line(_arc(x, y + 4, 13, 8, 0.1 * math.pi, 0.9 * math.pi, 8), key + "-bag", 2.0)
        ink.dot(x, y + 2, 5.0, key)
    elif style == "crescent":  # upward bowl: a happy squint
        ink.line(_arc(x, y + 8, 15, 13, 1.14 * math.pi, 1.86 * math.pi, 12), key, width=4.0)
    elif style == "wide":
        ink.shape(_ellipse(x, y, 16, 16, steps=16), key + "-eye", width=2.6)
        ink.dot(x, y, 6.0, key)
    else:
        raise ValueError(f"unknown eye style {style!r}")


def _draw_mouth(ink: _Ink, mouth: str) -> None:
    if mouth == "o":
        ink.shape(_ellipse(300, 306, 9, 11, steps=14), "mouth", fill=_INK, width=2.0)
    elif mouth == "grin":
        ink.line(_arc(300, 292, 28, 16, 0.08 * math.pi, 0.92 * math.pi, 10), "mouth", 5.0)
    else:
        ink.line(_MOUTHS[mouth], "mouth", width=5.0)


def _draw_face(ink: _Ink, expression: str) -> None:
    lo, li, ri, ro = _BROWS[expression]
    ink.line([(238, 200 + lo), (280, 200 + li)], "browL", width=6.0)
    ink.line([(320, 200 + ri), (362, 200 + ro)], "browR", width=6.0)
    _eye(ink, _FACE_EYES[expression], 262.0, 236.0, "eyeL")
    _eye(ink, _FACE_EYES[expression], 338.0, 236.0, "eyeR")
    ink.line([(300, 254), (293, 276), (302, 281)], "nose", width=3.0)
    _draw_mouth(ink, _MOUTH[expression])
    if expression == "sad":
        ink.shape([(262, 252), (267, 262), (262, 270), (257, 262)], "tear", width=2.0)


# --- public rendering -----------------------------------------------------------------------


def draw_character_from_spec(spec) -> Image.Image:
    """Render ``spec`` as a 600x1000 RGBA image with a transparent background. Nothing saved."""
    clean = normalise_spec(spec)
    skin = SKIN_TONES[clean.skin_tone]
    warp = (_BUILD_SCALE[clean.build], _HEIGHT_SCALE[clean.height])
    ink = _Ink(_seed(clean), warp)
    _draw_body(ink, clean)
    _draw_props(ink, clean.prop)
    _HAIR_BACK.get(clean.hair, _noop)(ink)
    _draw_head(ink, clean.head, skin)
    _HAIR_FRONT[clean.hair](ink)
    _draw_face(ink, clean.expression)
    return ink.finish()


def character_for_context(ctx) -> Path:
    """Return the cached PNG for ``ctx``, drawing and saving it on first use."""
    spec = spec_from_context(ctx)
    path = CHARACTERS_DIR / f"{spec_hash(spec)}.png"
    if path.exists():
        return path
    CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        draw_character_from_spec(spec).save(tmp, format="PNG")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path
