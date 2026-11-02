"""Pure Pillow helpers for the cinematic look of the long-form renderer.

All helpers are deterministic for a given input (time, seed, size) so frames can be
re-rendered identically. Canvas size is 1920x1080 RGB.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

# Horizontal advance used to lay out characters for shatter_text (pixels).
_SHATTER_ADVANCE: float = 28.0
_FLASH_FPS_DEFAULT: int = 30


def draw_radial_glow(
    img: Image.Image,
    center: tuple[int, int],
    radius: int,
    color: tuple[int, int, int],
    intensity: float = 0.6,
) -> None:
    """Composite a soft radial glow onto ``img`` in place.

    The glow may be centred off-canvas; Pillow clips the drawing safely.
    """
    size = img.size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    cx, cy = center
    r = max(0, int(radius))
    ImageDraw.Draw(layer).ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*color, 255))
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.5))
    alpha = layer.getchannel("A").point(lambda a: int(a * max(0.0, min(1.0, intensity))))
    layer.putalpha(alpha)
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    img.paste(base.convert("RGB"))


@lru_cache(maxsize=16)
def _vignette_mask(w: int, h: int, strength: float) -> Image.Image:
    """Build (and cache) the L-mode edge-darkening mask for a canvas size."""
    gradient = Image.radial_gradient("L").resize((w, h), Image.Resampling.BILINEAR)
    s = max(0.0, min(1.0, strength))
    return gradient.point(lambda v: int(min(255, v * s)))


def draw_vignette(img: Image.Image, strength: float = 0.5) -> None:
    """Darken the edges of ``img`` in place with a radial gradient mask."""
    w, h = img.size
    mask = _vignette_mask(w, h, float(strength))
    black = Image.new("RGB", img.size, (0, 0, 0))
    img.paste(black, mask=mask)


def draw_dust_motes(
    d: ImageDraw.ImageDraw,
    t: float,
    box: tuple[int, int, int, int],
    count: int = 24,
    seed: int = 7,
) -> None:
    """Draw small pale dust motes drifting slowly upward inside ``box``."""
    x0, y0, x1, y1 = box
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    rng = random.Random(seed)
    for _ in range(count):
        fx = rng.random()
        fy = rng.random()
        speed = rng.uniform(0.01, 0.04)  # fraction of box height per second
        phase = rng.uniform(0.0, math.tau)
        size = rng.choice((1, 2, 3))
        frac_y = (fy - speed * t) % 1.0
        frac_x = (fx + 0.02 * math.sin(t * 0.4 + phase)) % 1.0
        px = x0 + frac_x * width
        py = y0 + frac_y * height
        shade = 200 + int(40 * (0.5 + 0.5 * math.sin(t * 0.7 + phase)))
        d.ellipse((px - size, py - size, px + size, py + size), fill=(shade, shade - 6, shade - 20))


def shatter_text(
    text: str,
    x: float,
    y: float,
    t: float,
    start: float,
    duration: float = 1.2,
    gravity: float = 900.0,
    seed: int = 3,
) -> list[tuple[str, float, float, float, float]]:
    """Return ``(char, x, y, angle_deg, alpha)`` for each character at time ``t``.

    Before ``start`` the characters sit at their original positions. From ``start``
    each character flies outward with a seeded velocity and falls under gravity while
    spinning and fading. Returns an empty list once ``start + duration`` has passed.
    """
    if t >= start + duration:
        return []
    rng = random.Random(seed)
    # Pre-draw per-character motion so results depend only on the seed and index.
    motion = [
        (
            rng.uniform(-260.0, 260.0),  # vx
            rng.uniform(-420.0, -80.0),  # vy (negative is up)
            rng.uniform(-360.0, 360.0),  # angular velocity deg/s
        )
        for _ in text
    ]
    out: list[tuple[str, float, float, float, float]] = []
    if t < start:
        for i, ch in enumerate(text):
            out.append((ch, x + i * _SHATTER_ADVANCE, y, 0.0, 1.0))
        return out
    dt = t - start
    alpha = max(0.0, 1.0 - dt / duration) if duration > 0 else 0.0
    for i, ch in enumerate(text):
        vx, vy, spin = motion[i]
        px = x + i * _SHATTER_ADVANCE + vx * dt
        py = y + vy * dt + 0.5 * gravity * dt * dt
        out.append((ch, px, py, spin * dt, alpha))
    return out


def impact_flash_alpha(
    t: float,
    start: float,
    frames: int = 3,
    fps: int = _FLASH_FPS_DEFAULT,
) -> float:
    """Return 1.0 for the first ``frames`` frames after ``start``, else 0.0."""
    if fps <= 0:
        return 0.0
    window = frames / fps
    return 1.0 if start <= t < start + window else 0.0


def camera_shake_offset(
    t: float,
    start: float,
    duration: float = 0.2,
    amplitude: float = 12.0,
) -> tuple[int, int]:
    """Return a decaying pseudo-random ``(dx, dy)`` pixel offset for a camera shake."""
    if t < start or t >= start + duration or duration <= 0:
        return (0, 0)
    p = (t - start) / duration
    envelope = (1.0 - p) ** 2
    amp = amplitude * envelope
    dx = amp * (math.sin(t * 47.0) + 0.5 * math.sin(t * 91.0 + 1.0))
    dy = amp * (math.sin(t * 59.0 + 2.0) + 0.5 * math.sin(t * 113.0))
    return (round(dx / 1.5), round(dy / 1.5))
