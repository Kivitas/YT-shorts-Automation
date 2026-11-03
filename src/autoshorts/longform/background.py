"""Atmosphere backdrops for the longform scenes.

The default ``draw_background`` paints a navy sky with a top-centre glow, three parallax
silhouette layers, a faint teal horizon band and slow drifting dust motes, then a radial
vignette. ``draw_themed_background`` runs the same pipeline with a named palette from
``THEMES`` so scenes can vary in colour; ``navy_night`` reproduces the default look.

Everything is deterministic for a given ``(t, camera_x)`` so frames re-render identically.
The canvas is RGB and is painted in place.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any, NamedTuple

from PIL import Image, ImageDraw, ImageFilter

from autoshorts.core.theme import (
    NAVY,
    NAVY_DEEP,
    NAVY_LIGHT,
    OFFWHITE,
    RGB,
    TEAL,
    YELLOW_SOFT,
    lerp_rgb,
)
from autoshorts.longform.fx import draw_vignette
from autoshorts.longform.motion import parallax_offset

_log = logging.getLogger(__name__)

HORIZON_Y = 760
HORIZON_ALPHA = 0.10
HORIZON_SIGMA = 45.0
VIGNETTE_STRENGTH = 0.35
DUST_COUNT = 20
DEFAULT_THEME = "navy_night"
_TAU = 2.0 * math.pi

# (depth, base_y, amplitude, colour blend from hill_deep to hill_light, harmonics)
# Harmonics are (k, weight, phase): k must be an integer so the profile tiles seamlessly.
_LAYERS: tuple[tuple[float, float, float, float, tuple[tuple[int, float, float], ...]], ...] = (
    (0.15, 770.0, 80.0, 0.55, ((1, 0.6, 0.0), (2, 0.25, 0.9), (3, 0.1, 2.1))),
    (0.35, 860.0, 90.0, 0.40, ((1, 0.5, 1.4), (3, 0.3, 0.3), (5, 0.1, 2.6))),
    (0.60, 1000.0, 70.0, 0.25, ((2, 0.5, 0.7), (4, 0.25, 1.9), (7, 0.1, 0.2))),
)

# Dust palette: same family as the navy scene, kept low-alpha.
_DUST_COLOURS: tuple[RGB, ...] = (OFFWHITE, TEAL, YELLOW_SOFT)

_SKYLINE_SEED = 7
_SKYLINE_DEPTH = 0.35
_SKYLINE_BASE_Y = 900.0

# Per-theme palettes. ``decor`` names an optional effect: "none", "sun", "lamp", "grid",
# "mist", "clouds", "neon", "beach", "stars", "snow", "cafe" or "mint_grid". Most effects are
# painted behind the hills; those in ``_FOREGROUND_DECOR`` are painted over them. ``skyline``
# swaps the hill silhouettes for a building skyline.
THEMES: dict[str, dict[str, Any]] = {
    "navy_night": {
        "sky_top": NAVY,
        "sky_bottom": NAVY_DEEP,
        "sky_glow": NAVY_LIGHT,
        "hill_deep": NAVY_DEEP,
        "hill_light": NAVY_LIGHT,
        "horizon": TEAL,
        "dust": _DUST_COLOURS,
        "decor": "none",
        "accent": YELLOW_SOFT,
        "accent2": TEAL,
        "skyline": False,
    },
    "sunset": {
        "sky_top": (255, 122, 64),
        "sky_bottom": (190, 50, 120),
        "sky_glow": (255, 190, 110),
        "hill_deep": (70, 24, 80),
        "hill_light": (120, 44, 110),
        "horizon": (255, 160, 90),
        "dust": ((255, 220, 160), (255, 190, 120), (255, 240, 200)),
        "decor": "sun",
        "accent": (255, 214, 90),
        "accent2": (255, 150, 80),
        "skyline": False,
    },
    "lab_teal": {
        "sky_top": (10, 120, 130),
        "sky_bottom": (8, 40, 100),
        "sky_glow": (40, 200, 210),
        "hill_deep": (6, 30, 80),
        "hill_light": (20, 80, 120),
        "horizon": (0, 230, 220),
        "dust": ((220, 255, 255), (90, 230, 230), (255, 255, 255)),
        "decor": "grid",
        "accent": (255, 255, 255),
        "accent2": (0, 230, 220),
        "skyline": False,
    },
    "office_warm": {
        "sky_top": (250, 240, 220),
        "sky_bottom": (232, 206, 168),
        "sky_glow": (255, 246, 230),
        "hill_deep": (128, 140, 110),
        "hill_light": (160, 172, 140),
        "horizon": (214, 170, 110),
        "dust": ((255, 220, 150), (245, 200, 120), (255, 250, 230)),
        "decor": "lamp",
        "accent": (255, 186, 80),
        "accent2": (255, 220, 150),
        "skyline": False,
    },
    "forest_dawn": {
        "sky_top": (200, 240, 190),
        "sky_bottom": (250, 215, 120),
        "sky_glow": (240, 255, 230),
        "hill_deep": (20, 60, 35),
        "hill_light": (40, 96, 56),
        "horizon": (255, 230, 140),
        "dust": ((255, 250, 210), (230, 255, 200), (255, 220, 130)),
        "decor": "mist",
        "accent": (255, 252, 235),
        "accent2": (230, 255, 220),
        "skyline": False,
    },
    "city_neon": {
        "sky_top": (40, 10, 80),
        "sky_bottom": (5, 8, 30),
        "sky_glow": (90, 30, 130),
        "hill_deep": (10, 6, 28),
        "hill_light": (20, 14, 50),
        "horizon": (0, 230, 255),
        "dust": ((255, 80, 200), (0, 230, 255), (200, 160, 255)),
        "decor": "neon",
        "accent": (255, 60, 170),
        "accent2": (0, 230, 255),
        "skyline": True,
    },
    "candy": {
        "sky_top": (255, 190, 215),
        "sky_bottom": (190, 245, 220),
        "sky_glow": (255, 240, 250),
        "hill_deep": (240, 150, 190),
        "hill_light": (255, 190, 215),
        "horizon": (255, 255, 255),
        "dust": ((255, 255, 255), (255, 220, 240), (200, 255, 230)),
        "decor": "clouds",
        "accent": (255, 255, 255),
        "accent2": (255, 240, 250),
        "skyline": False,
    },
    "beach_day": {
        "sky_top": (168, 214, 240),
        "sky_bottom": (255, 246, 232),
        "sky_glow": (255, 255, 246),
        "hill_deep": (40, 150, 160),
        "hill_light": (70, 180, 182),
        "horizon": (150, 230, 225),
        "dust": ((255, 255, 255), (255, 240, 200), (200, 240, 255)),
        "decor": "beach",
        "accent": (240, 255, 255),
        "accent2": (255, 236, 185),
        "sun": (255, 226, 130),
        "sea": (32, 160, 170),
        "sand": (236, 214, 178),
        "skyline": False,
    },
    "space_violet": {
        "sky_top": (74, 22, 124),
        "sky_bottom": (28, 22, 96),
        "sky_glow": (140, 70, 190),
        "hill_deep": (30, 12, 70),
        "hill_light": (62, 32, 122),
        "horizon": (200, 120, 255),
        "dust": ((255, 255, 255), (255, 150, 220), (200, 180, 255)),
        "decor": "stars",
        "accent": (255, 255, 255),
        "accent2": (255, 110, 190),
        "skyline": False,
    },
    "snow_blue": {
        "sky_top": (186, 218, 248),
        "sky_bottom": (246, 251, 255),
        "sky_glow": (255, 255, 255),
        "hill_deep": (206, 226, 246),
        "hill_light": (255, 255, 255),
        "horizon": (220, 238, 255),
        "dust": ((255, 255, 255), (214, 234, 255), (245, 250, 255)),
        "decor": "snow",
        "accent": (255, 255, 255),
        "accent2": (168, 200, 236),
        "skyline": False,
    },
    "cafe_amber": {
        "sky_top": (88, 50, 26),
        "sky_bottom": (196, 124, 42),
        "sky_glow": (255, 196, 110),
        "hill_deep": (68, 34, 16),
        "hill_light": (118, 64, 28),
        "horizon": (255, 170, 80),
        "dust": ((255, 220, 150), (255, 180, 90), (250, 236, 200)),
        "decor": "cafe",
        "accent": (255, 200, 110),
        "accent2": (255, 150, 60),
        "floor": (34, 18, 8),
        "skyline": False,
    },
    "mint_grid": {
        "sky_top": (168, 240, 208),
        "sky_bottom": (222, 250, 204),
        "sky_glow": (240, 255, 246),
        "hill_deep": (64, 168, 128),
        "hill_light": (118, 204, 164),
        "horizon": (255, 255, 255),
        "dust": ((255, 255, 255), (180, 255, 220), (220, 255, 200)),
        "decor": "mint_grid",
        "accent": (255, 255, 255),
        "accent2": (120, 220, 180),
        "skyline": False,
    },
}


class _Mote(NamedTuple):
    lane_x: float
    base_y: float
    rise: float
    sway: float
    period: float
    phase: float
    radius: float
    depth: float
    colour: RGB
    alpha: float


def _build_dust(count: int) -> tuple[_Mote, ...]:
    """Lay motes out in evenly spaced lanes with a fixed seed so the drift is stable."""
    rng = random.Random(20260)
    motes: list[_Mote] = []
    depths = (0.15, 0.35, 0.6)
    for i in range(count):
        motes.append(
            _Mote(
                lane_x=(i + 0.5) * 1920.0 / count + rng.uniform(-24.0, 24.0),
                base_y=rng.uniform(0.0, 1080.0),
                rise=rng.uniform(6.0, 14.0),
                sway=rng.uniform(10.0, 22.0),
                period=rng.uniform(9.0, 16.0),
                phase=rng.uniform(0.0, _TAU),
                radius=rng.uniform(1.4, 2.8),
                depth=depths[i % len(depths)],
                colour=_DUST_COLOURS[i % len(_DUST_COLOURS)],
                alpha=rng.uniform(0.18, 0.32),
            )
        )
    return tuple(motes)


_DUST: tuple[_Mote, ...] = _build_dust(DUST_COUNT)


def _build_skyline(width: int) -> tuple[tuple[float, float, float], ...]:
    """Fixed building row as (x, width, height) covering one parallax period."""
    rng = random.Random(_SKYLINE_SEED)
    buildings: list[tuple[float, float, float]] = []
    x = 0.0
    while x < width:
        bw = rng.uniform(70.0, 170.0)
        buildings.append((x, bw, rng.uniform(140.0, 430.0)))
        x += bw + rng.uniform(4.0, 26.0)
    return tuple(buildings)


_SKYLINE: tuple[tuple[float, float, float], ...] = _build_skyline(1920)


def _top_glow_mask(w: int, h: int) -> Image.Image:
    """L-mode mask, bright at top-centre and fading to zero toward the edges."""
    gw, gh = int(w * 0.9), int(h * 1.1)
    radial = Image.radial_gradient("L").resize((gw, gh), Image.Resampling.BILINEAR)
    falloff = radial.point(lambda v: int((255 - v) * 0.6))
    mask = Image.new("L", (w, h), 0)
    mask.paste(falloff, (w // 2 - gw // 2, -gh // 2))
    return mask


def _paint_sky(img: Image.Image) -> None:
    """Vertical navy gradient with a NAVY_LIGHT glow at top-centre."""
    _paint_sky_palette(img, NAVY, NAVY_DEEP, NAVY_LIGHT)


def _paint_sky_palette(img: Image.Image, top: RGB, bottom: RGB, glow: RGB) -> None:
    """Vertical gradient from ``top`` to ``bottom`` with a ``glow`` pool at top-centre."""
    w, h = img.size
    top_img = Image.new("RGB", (w, h), top)
    bottom_img = Image.new("RGB", (w, h), bottom)
    light = Image.new("RGB", (w, h), glow)
    fade = Image.linear_gradient("L").resize((w, h), Image.Resampling.BILINEAR)
    sky = Image.composite(bottom_img, top_img, fade)
    sky = Image.composite(light, sky, _top_glow_mask(w, h))
    img.paste(sky)


def _hill(u: float, period: float, harmonics: tuple[tuple[int, float, float], ...]) -> float:
    """Rounded-dome profile in [0, 1], periodic in ``period``."""
    total = 0.0
    weight_sum = 0.0
    for k, weight, phase in harmonics:
        total += weight * abs(math.sin(math.pi * k * u / period + phase))
        weight_sum += weight
    return total / weight_sum


def _paint_silhouette(
    img: Image.Image,
    depth: float,
    base_y: float,
    amp: float,
    colour: RGB,
    harmonics: tuple[tuple[int, float, float], ...],
    camera_x: float,
) -> None:
    """Fill one silhouette layer, shifted by its parallax offset and wrapped seamlessly."""
    w, h = img.size
    period = float(w)
    offset = parallax_offset(camera_x, depth) % period
    points: list[tuple[float, float]] = []
    step = 8
    for sx in range(0, w + step, step):
        height = _hill(sx + offset, period, harmonics)
        points.append((float(sx), base_y - amp * height))
    points.append((float(w), float(h)))
    points.append((0.0, float(h)))
    ImageDraw.Draw(img).polygon(points, fill=colour)


def _paint_skyline(img: Image.Image, colour: RGB, camera_x: float) -> None:
    """Wrapped row of building silhouettes shifted by the parallax offset."""
    w, h = img.size
    offset = parallax_offset(camera_x, _SKYLINE_DEPTH) % w
    draw = ImageDraw.Draw(img)
    for x, bw, bh in _SKYLINE:
        for shift in (-w, 0.0, w):
            left = x - offset + shift
            if left > w or left + bw < 0:
                continue
            draw.rectangle((left, _SKYLINE_BASE_Y - bh, left + bw, h), fill=colour)


def _blend_overlay(img: Image.Image, overlay: Image.Image) -> None:
    """Alpha-composite an RGBA overlay onto an RGB image in place."""
    base = img.convert("RGBA")
    base.alpha_composite(overlay)
    img.paste(base.convert("RGB"))


def _paint_mask(img: Image.Image, colour: RGB, mask: Image.Image) -> None:
    """Blend a solid ``colour`` onto ``img`` through an L-mode ``mask`` (0 to 255)."""
    img.paste(Image.new("RGB", img.size, colour), mask=mask)


def _paint_horizon(img: Image.Image, colour: RGB = TEAL) -> None:
    """Faint glow band centred on HORIZON_Y, peaking at HORIZON_ALPHA."""
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    reach = int(HORIZON_SIGMA * 3)
    for y in range(max(0, HORIZON_Y - reach), min(h, HORIZON_Y + reach + 1)):
        dy = (y - HORIZON_Y) / HORIZON_SIGMA
        alpha = HORIZON_ALPHA * math.exp(-dy * dy)
        if alpha < 0.002:
            continue
        draw.line([(0, y), (w, y)], fill=(*colour, round(alpha * 255)))
    _blend_overlay(img, overlay)


def _paint_dust(
    img: Image.Image,
    t: float,
    camera_x: float,
    colours: tuple[RGB, ...] = _DUST_COLOURS,
) -> None:
    """Slow upward drift with gentle sway; deeper motes shift more under the camera pan."""
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for index, mote in enumerate(_DUST):
        x = mote.lane_x + mote.sway * math.sin(_TAU * t / mote.period + mote.phase)
        x = (x + parallax_offset(camera_x, mote.depth)) % w
        y = (mote.base_y - mote.rise * t) % h
        r = mote.radius
        colour = colours[index % len(colours)]
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(*colour, round(255 * mote.alpha)))
    overlay = overlay.filter(ImageFilter.GaussianBlur(1.0))
    _blend_overlay(img, overlay)


def _blurred_mask(size: tuple[int, int], draw_fn: Any, blur: float = 0.0) -> Image.Image:
    """Build an L mask by drawing with ``draw_fn(draw)``, then optionally blurring it."""
    mask = Image.new("L", size, 0)
    draw_fn(ImageDraw.Draw(mask))
    if blur > 0.0:
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
    return mask


def _paint_sun(img: Image.Image, theme: dict[str, Any]) -> None:
    """Large warm sun halo low on the right with a brighter core."""
    w, h = img.size
    cx, cy = w * 0.73, h * 0.6

    def halo(d: ImageDraw.ImageDraw) -> None:
        d.ellipse((cx - 420, cy - 420, cx + 420, cy + 420), fill=int(255 * 0.55))

    def core(d: ImageDraw.ImageDraw) -> None:
        d.ellipse((cx - 110, cy - 110, cx + 110, cy + 110), fill=int(255 * 0.9))

    _paint_mask(img, theme["accent2"], _blurred_mask((w, h), halo, blur=140.0))
    _paint_mask(img, theme["accent"], _blurred_mask((w, h), core, blur=40.0))


def _paint_lamp(img: Image.Image, theme: dict[str, Any]) -> None:
    """Soft amber lamp pool in the upper right."""
    w, h = img.size
    cx, cy = w * 0.78, h * 0.18

    def pool(d: ImageDraw.ImageDraw) -> None:
        d.ellipse((cx - 520, cy - 380, cx + 520, cy + 380), fill=int(255 * 0.4))

    _paint_mask(img, theme["accent"], _blurred_mask((w, h), pool, blur=170.0))


def _paint_grid(img: Image.Image, theme: dict[str, Any]) -> None:
    """Faint white lab grid with a cyan glow pool over the horizon."""
    w, h = img.size
    spacing = 120

    def lines(d: ImageDraw.ImageDraw) -> None:
        for x in range(0, w, spacing):
            d.line([(x, 0), (x, h)], fill=int(255 * 0.07), width=2)
        for y in range(0, h, spacing):
            d.line([(0, y), (w, y)], fill=int(255 * 0.07), width=2)

    _paint_mask(img, theme["accent"], _blurred_mask((w, h), lines))

    def glow(d: ImageDraw.ImageDraw) -> None:
        d.ellipse(
            (w * 0.5 - 700, HORIZON_Y - 260, w * 0.5 + 700, HORIZON_Y + 260), fill=int(255 * 0.3)
        )

    _paint_mask(img, theme["accent2"], _blurred_mask((w, h), glow, blur=120.0))


def _paint_mist(img: Image.Image, theme: dict[str, Any]) -> None:
    """Pale horizontal mist bands drifting across the valley."""
    w, h = img.size

    def bands(d: ImageDraw.ImageDraw) -> None:
        d.rectangle((0, 600, w, 660), fill=int(255 * 0.35))
        d.rectangle((0, 720, w, 780), fill=int(255 * 0.45))

    _paint_mask(img, theme["accent"], _blurred_mask((w, h), bands, blur=45.0))


def _paint_clouds(img: Image.Image, theme: dict[str, Any]) -> None:
    """Soft white cloud puffs scattered across the sky."""
    w, h = img.size
    puffs = (
        (0.12, 0.22, 1.0),
        (0.30, 0.14, 0.8),
        (0.55, 0.26, 1.1),
        (0.78, 0.18, 0.9),
        (0.92, 0.35, 0.7),
        (0.42, 0.45, 0.6),
    )

    def clouds(d: ImageDraw.ImageDraw) -> None:
        for fx, fy, scale in puffs:
            cx, cy = w * fx, h * fy
            for dx, dy, rx, ry in ((0, 0, 130, 50), (-90, 20, 90, 40), (90, 15, 100, 42)):
                rx2, ry2 = rx * scale, ry * scale
                d.ellipse(
                    (
                        cx + dx * scale - rx2,
                        cy + dy * scale - ry2,
                        cx + dx * scale + rx2,
                        cy + dy * scale + ry2,
                    ),
                    fill=int(255 * 0.6),
                )

    _paint_mask(img, theme["accent"], _blurred_mask((w, h), clouds, blur=22.0))


def _paint_neon(img: Image.Image, theme: dict[str, Any]) -> None:
    """Pink and cyan neon glow bands behind the skyline."""
    w, h = img.size

    def pink_band(d: ImageDraw.ImageDraw) -> None:
        d.rectangle((0, 640, w, 700), fill=int(255 * 0.5))

    def cyan_band(d: ImageDraw.ImageDraw) -> None:
        d.rectangle((0, 780, w, 800), fill=int(255 * 0.45))

    _paint_mask(img, theme["accent"], _blurred_mask((w, h), pink_band, blur=60.0))
    _paint_mask(img, theme["accent2"], _blurred_mask((w, h), cyan_band, blur=28.0))


_STAR_SEED = 11
_STAR_COUNT = 40
_STAR_WHITE: RGB = (255, 255, 255)
_STAR_YELLOW: RGB = (255, 236, 150)
_FLAKE_SEED = 23
_FLAKE_COUNT = 60


def _build_points(seed: int, count: int, y_max: float) -> tuple[tuple[float, float, float], ...]:
    """Seeded scatter of (x, y, radius) dots across the frame, from y 0 down to ``y_max``."""
    rng = random.Random(seed)
    points: list[tuple[float, float, float]] = []
    for _ in range(count):
        points.append((rng.uniform(0.0, 1920.0), rng.uniform(0.0, y_max), rng.uniform(1.2, 3.0)))
    return tuple(points)


_STARS: tuple[tuple[float, float, float], ...] = _build_points(_STAR_SEED, _STAR_COUNT, 640.0)
_FLAKES: tuple[tuple[float, float, float], ...] = _build_points(_FLAKE_SEED, _FLAKE_COUNT, 1080.0)


def _paint_dots(
    img: Image.Image,
    dots: tuple[tuple[float, float, float], ...],
    colours: tuple[RGB, ...],
    alpha: int,
) -> None:
    """Draw soft dots cycling through ``colours`` at the given alpha (0 to 255)."""
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for index, (x, y, r) in enumerate(dots):
        colour = colours[index % len(colours)]
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(*colour, alpha))
    overlay = overlay.filter(ImageFilter.GaussianBlur(0.6))
    _blend_overlay(img, overlay)


def _paint_beach(img: Image.Image, theme: dict[str, Any]) -> None:
    """Foreground: warm sun glow top-right, a teal sea band with foam lines, then sand."""
    w, h = img.size
    sea_top, sand_top = int(h * 0.64), int(h * 0.80)
    cx, cy = w * 0.84, h * 0.14

    def halo(d: ImageDraw.ImageDraw) -> None:
        d.ellipse((cx - 300, cy - 300, cx + 300, cy + 300), fill=int(255 * 0.5))

    def core(d: ImageDraw.ImageDraw) -> None:
        d.ellipse((cx - 90, cy - 90, cx + 90, cy + 90), fill=int(255 * 0.9))

    _paint_mask(img, theme["accent2"], _blurred_mask((w, h), halo, blur=110.0))
    _paint_mask(img, theme["sun"], _blurred_mask((w, h), core, blur=30.0))

    def sea(d: ImageDraw.ImageDraw) -> None:
        d.rectangle((0, sea_top, w, sand_top), fill=255)

    _paint_mask(img, theme["sea"], _blurred_mask((w, h), sea, blur=3.0))

    def foam(d: ImageDraw.ImageDraw) -> None:
        for row in range(sea_top + 26, sand_top - 10, 28):
            points = [
                (float(x), row + 6.0 * math.sin(x / 38.0 + row * 0.3)) for x in range(0, w + 12, 12)
            ]
            d.line(points, fill=int(255 * 0.6), width=3)

    _paint_mask(img, theme["accent"], _blurred_mask((w, h), foam, blur=1.2))

    def sand(d: ImageDraw.ImageDraw) -> None:
        d.rectangle((0, sand_top, w, h), fill=255)

    _paint_mask(img, theme["sand"], _blurred_mask((w, h), sand, blur=3.0))


def _paint_stars(img: Image.Image, theme: dict[str, Any]) -> None:
    """Soft pink nebula glow with seeded white and yellow star dots."""
    w, h = img.size
    cx, cy = w * 0.28, h * 0.30

    def nebula(d: ImageDraw.ImageDraw) -> None:
        d.ellipse((cx - 460, cy - 280, cx + 460, cy + 280), fill=int(255 * 0.45))

    _paint_mask(img, theme["accent2"], _blurred_mask((w, h), nebula, blur=150.0))
    _paint_dots(img, _STARS, (_STAR_WHITE, _STAR_WHITE, _STAR_YELLOW), 230)


def _paint_snow(img: Image.Image, theme: dict[str, Any]) -> None:
    """Pale blue shadows beneath the white hills and seeded falling flakes."""
    w, h = img.size

    def shadow(d: ImageDraw.ImageDraw) -> None:
        d.ellipse((-200, 740, w * 0.55, 1120), fill=int(255 * 0.6))
        d.ellipse((w * 0.45, 800, w + 200, 1140), fill=int(255 * 0.6))

    _paint_mask(img, theme["accent2"], _blurred_mask((w, h), shadow, blur=60.0))
    _paint_dots(img, _FLAKES, (theme["accent"],), 210)


def _paint_cafe(img: Image.Image, theme: dict[str, Any]) -> None:
    """Foreground: soft amber lamp pool top-left and a darker floor band along the bottom."""
    w, h = img.size
    cx, cy = w * 0.18, h * 0.16

    def pool(d: ImageDraw.ImageDraw) -> None:
        d.ellipse((cx - 500, cy - 360, cx + 500, cy + 360), fill=int(255 * 0.45))

    _paint_mask(img, theme["accent"], _blurred_mask((w, h), pool, blur=170.0))

    def floor(d: ImageDraw.ImageDraw) -> None:
        d.rectangle((0, int(h * 0.84), w, h), fill=int(255 * 0.6))

    _paint_mask(img, theme["floor"], _blurred_mask((w, h), floor, blur=8.0))


def _paint_mint_grid(img: Image.Image, theme: dict[str, Any]) -> None:
    """Faint white grid of lines across the mint sky."""
    w, h = img.size
    spacing = 96

    def lines(d: ImageDraw.ImageDraw) -> None:
        for x in range(0, w, spacing):
            d.line([(x, 0), (x, h)], fill=int(255 * 0.35), width=2)
        for y in range(0, h, spacing):
            d.line([(0, y), (w, y)], fill=int(255 * 0.35), width=2)

    _paint_mask(img, theme["accent"], _blurred_mask((w, h), lines))


_DECOR_PAINTERS: dict[str, Any] = {
    "sun": _paint_sun,
    "lamp": _paint_lamp,
    "grid": _paint_grid,
    "mist": _paint_mist,
    "clouds": _paint_clouds,
    "neon": _paint_neon,
    "beach": _paint_beach,
    "stars": _paint_stars,
    "snow": _paint_snow,
    "cafe": _paint_cafe,
    "mint_grid": _paint_mint_grid,
}

# Decor painted over the hill silhouettes rather than behind them.
_FOREGROUND_DECOR: frozenset[str] = frozenset({"beach", "cafe"})


def _resolve_theme(theme_name: str) -> dict[str, Any]:
    """Return the palette for ``theme_name``, falling back to the default with a warning."""
    theme = THEMES.get(theme_name)
    if theme is None:
        _log.warning("unknown background theme %r; falling back to %r", theme_name, DEFAULT_THEME)
        theme = THEMES[DEFAULT_THEME]
    return theme


def theme_names() -> tuple[str, ...]:
    """Names of all available background themes, in definition order."""
    return tuple(THEMES)


def draw_background(img: Image.Image, t: float, camera_x: float = 0.0) -> None:
    """Paint the navy atmosphere onto ``img`` in place.

    ``t`` is the scene time in seconds (drives dust drift). ``camera_x`` is the horizontal
    camera pan in pixels; each silhouette layer shifts by ``parallax_offset(camera_x, depth)``.
    """
    if img.mode != "RGB":
        raise ValueError(f"draw_background needs an RGB image, got {img.mode}")
    _paint_sky(img)
    for index, (depth, base_y, amp, blend, harmonics) in enumerate(_LAYERS):
        colour = lerp_rgb(NAVY_DEEP, NAVY_LIGHT, blend)
        _paint_silhouette(img, depth, base_y, amp, colour, harmonics, camera_x)
        if index == 0:
            _paint_horizon(img, TEAL)
    _paint_dust(img, t, camera_x, _DUST_COLOURS)
    draw_vignette(img, VIGNETTE_STRENGTH)


def draw_themed_background(
    img: Image.Image, theme_name: str, t: float, camera_x: float = 0.0
) -> None:
    """Paint the atmosphere for ``theme_name`` onto ``img`` in place.

    Unknown names log one warning and paint the ``navy_night`` theme. ``t`` and ``camera_x``
    behave as in ``draw_background``.
    """
    if img.mode != "RGB":
        raise ValueError(f"draw_themed_background needs an RGB image, got {img.mode}")
    theme = _resolve_theme(theme_name)
    _paint_sky_palette(img, theme["sky_top"], theme["sky_bottom"], theme["sky_glow"])
    decor = _DECOR_PAINTERS.get(theme["decor"])
    foreground = theme["decor"] in _FOREGROUND_DECOR
    if decor is not None and not foreground:
        decor(img, theme)
    for index, (depth, base_y, amp, blend, harmonics) in enumerate(_LAYERS):
        if theme["skyline"]:
            if index == 0:
                _paint_skyline(img, theme["hill_light"], camera_x)
        else:
            colour = lerp_rgb(theme["hill_deep"], theme["hill_light"], blend)
            _paint_silhouette(img, depth, base_y, amp, colour, harmonics, camera_x)
        if index == 0:
            _paint_horizon(img, theme["horizon"])
    if decor is not None and foreground:
        decor(img, theme)
    _paint_dust(img, t, camera_x, theme["dust"])
    draw_vignette(img, VIGNETTE_STRENGTH)
