"""Deep navy atmosphere for the longform backdrop.

Paints a navy sky with a top-centre glow, three parallax silhouette layers, a faint
teal horizon band and slow drifting dust motes, then a radial vignette. Everything is
deterministic for a given ``(t, camera_x)`` so frames re-render identically. The
canvas is RGB and is painted in place.
"""

from __future__ import annotations

import math
import random
from typing import NamedTuple

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

HORIZON_Y = 760
HORIZON_ALPHA = 0.10
HORIZON_SIGMA = 45.0
VIGNETTE_STRENGTH = 0.35
DUST_COUNT = 20
_TAU = 2.0 * math.pi

# (depth, base_y, amplitude, colour blend from NAVY_DEEP to NAVY_LIGHT, harmonics)
# Harmonics are (k, weight, phase): k must be an integer so the profile tiles seamlessly.
_LAYERS: tuple[tuple[float, float, float, float, tuple[tuple[int, float, float], ...]], ...] = (
    (0.15, 770.0, 80.0, 0.55, ((1, 0.6, 0.0), (2, 0.25, 0.9), (3, 0.1, 2.1))),
    (0.35, 860.0, 90.0, 0.40, ((1, 0.5, 1.4), (3, 0.3, 0.3), (5, 0.1, 2.6))),
    (0.60, 1000.0, 70.0, 0.25, ((2, 0.5, 0.7), (4, 0.25, 1.9), (7, 0.1, 0.2))),
)

# Dust palette: same family as the navy scene, kept low-alpha.
_DUST_COLOURS: tuple[RGB, ...] = (OFFWHITE, TEAL, YELLOW_SOFT)


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
    w, h = img.size
    deep = Image.new("RGB", (w, h), NAVY_DEEP)
    navy = Image.new("RGB", (w, h), NAVY)
    light = Image.new("RGB", (w, h), NAVY_LIGHT)
    fade = Image.linear_gradient("L").resize((w, h), Image.Resampling.BILINEAR)
    sky = Image.composite(deep, navy, fade)
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
    blend: float,
    harmonics: tuple[tuple[int, float, float], ...],
    camera_x: float,
) -> None:
    """Fill one silhouette layer, shifted by its parallax offset and wrapped seamlessly."""
    w, h = img.size
    period = float(w)
    offset = parallax_offset(camera_x, depth) % period
    colour = lerp_rgb(NAVY_DEEP, NAVY_LIGHT, blend)
    points: list[tuple[float, float]] = []
    step = 8
    for sx in range(0, w + step, step):
        height = _hill(sx + offset, period, harmonics)
        points.append((float(sx), base_y - amp * height))
    points.append((float(w), float(h)))
    points.append((0.0, float(h)))
    ImageDraw.Draw(img).polygon(points, fill=colour)


def _blend_overlay(img: Image.Image, overlay: Image.Image) -> None:
    """Alpha-composite an RGBA overlay onto an RGB image in place."""
    base = img.convert("RGBA")
    base.alpha_composite(overlay)
    img.paste(base.convert("RGB"))


def _paint_horizon(img: Image.Image) -> None:
    """Faint teal glow band centred on HORIZON_Y, peaking at HORIZON_ALPHA."""
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    reach = int(HORIZON_SIGMA * 3)
    for y in range(max(0, HORIZON_Y - reach), min(h, HORIZON_Y + reach + 1)):
        dy = (y - HORIZON_Y) / HORIZON_SIGMA
        alpha = HORIZON_ALPHA * math.exp(-dy * dy)
        if alpha < 0.002:
            continue
        draw.line([(0, y), (w, y)], fill=(*TEAL, round(alpha * 255)))
    _blend_overlay(img, overlay)


def _paint_dust(img: Image.Image, t: float, camera_x: float) -> None:
    """Slow upward drift with gentle sway; deeper motes shift more under the camera pan."""
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for mote in _DUST:
        x = mote.lane_x + mote.sway * math.sin(_TAU * t / mote.period + mote.phase)
        x = (x + parallax_offset(camera_x, mote.depth)) % w
        y = (mote.base_y - mote.rise * t) % h
        r = mote.radius
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(*mote.colour, round(255 * mote.alpha)))
    overlay = overlay.filter(ImageFilter.GaussianBlur(1.0))
    _blend_overlay(img, overlay)


def draw_background(img: Image.Image, t: float, camera_x: float = 0.0) -> None:
    """Paint the navy atmosphere onto ``img`` in place.

    ``t`` is the scene time in seconds (drives dust drift). ``camera_x`` is the horizontal
    camera pan in pixels; each silhouette layer shifts by ``parallax_offset(camera_x, depth)``.
    """
    if img.mode != "RGB":
        raise ValueError(f"draw_background needs an RGB image, got {img.mode}")
    _paint_sky(img)
    for index, (depth, base_y, amp, blend, harmonics) in enumerate(_LAYERS):
        _paint_silhouette(img, depth, base_y, amp, blend, harmonics, camera_x)
        if index == 0:
            _paint_horizon(img)
    _paint_dust(img, t, camera_x)
    draw_vignette(img, VIGNETTE_STRENGTH)
