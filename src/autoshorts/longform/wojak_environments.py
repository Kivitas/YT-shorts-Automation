"""Procedural outdoor and indoor environments for Wojak scenes.

The hospital room is the default. This module adds distinct environments so a scene's setting
(and its country) changes the background instead of always showing the same room:

- ``rural``: village fields, dirt road, mud houses, sun, hills (India defaults here).
- ``road``: city street with skyline, sidewalk, asphalt and lane markings.
- ``home``: warm interior with wall, wood floor, window, sofa and lamp.

Everything is drawn with PIL/numpy; no assets or network are needed.
"""

from __future__ import annotations

import random
from typing import Final

import numpy as np
from PIL import Image, ImageDraw

ENVIRONMENTS: Final[tuple[str, ...]] = ("rural", "road", "home")

_RURAL_WORDS: Final[tuple[str, ...]] = (
    "rural",
    "village",
    "farm",
    "field",
    "countryside",
    "lake",
    "dock",
    "outdoor",
)
_ROAD_WORDS: Final[tuple[str, ...]] = (
    "road",
    "highway",
    "traffic",
    "intersection",
    "roundabout",
    "rearview",
    "shoulder",
    "street",
    "city",
    "lane",
)
_HOME_WORDS: Final[tuple[str, ...]] = (
    "bedroom",
    "sofa",
    "lounge",
    "home",
    "room",
    "cafe",
    "coffee",
    "gym",
    "locker",
    "airport",
)


def environment_for(setting: str | None, country: str | None) -> str | None:
    """Pick an environment for a setting, or None to keep the caller's default (hospital)."""
    text = (setting or "").strip().lower()
    slug = (country or "").strip().lower().replace(" ", "_")
    if any(w in text for w in _RURAL_WORDS):
        return "rural"
    if any(w in text for w in _ROAD_WORDS):
        return "road"
    if any(w in text for w in _HOME_WORDS):
        return "home"
    if slug == "india" and (not text or "hospital" in text or "clinic" in text):
        return "rural"
    return None


def _gradient(draw: ImageDraw.ImageDraw, w: int, y0: int, y1: int, top, bottom) -> None:
    span = max(1, y1 - y0)
    for y in range(y0, y1):
        f = (y - y0) / span
        color = tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3))
        draw.line([(0, y), (w, y)], fill=color)


def _grain(img: Image.Image, seed: int, intensity: float = 4.0) -> Image.Image:
    rng = np.random.default_rng(seed)
    arr = np.asarray(img, dtype=np.float32)
    noise = rng.normal(0.0, intensity, arr.shape[:2])[..., None]
    return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))


def render_rural(country: str, width: int, height: int, seed: int = 7) -> Image.Image:
    rng = random.Random(seed)
    horizon = int(height * 0.50)
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    _gradient(draw, width, 0, horizon, (120, 182, 236), (252, 238, 196))
    # sun with glow
    sx, sy = int(width * 0.80), int(height * 0.20)
    for r, col in ((130, (255, 246, 210)), (90, (255, 240, 170)), (60, (255, 226, 110))):
        draw.ellipse((sx - r, sy - r, sx + r, sy + r), fill=col)
    # distant hills
    pts = [(0, horizon)]
    for x in range(0, width + 1, 80):
        pts.append((x, horizon - rng.randint(20, 80)))
    pts.append((width, horizon))
    draw.polygon(pts, fill=(118, 150, 96))
    # fields in perspective stripes
    _gradient(draw, width, horizon, height, (110, 162, 78), (84, 140, 60))
    for i in range(14):
        x0 = int(width * (i / 14.0))
        shade = (96, 150, 68) if i % 2 else (124, 176, 88)
        draw.polygon(
            [
                (x0, horizon),
                (x0 + width // 14, horizon),
                (x0 + width // 6, height),
                (x0 - width // 10, height),
            ],
            fill=shade,
        )
    # dirt road converging to the horizon
    draw.polygon(
        [
            (int(width * 0.36), height),
            (int(width * 0.64), height),
            (int(width * 0.53), horizon),
            (int(width * 0.47), horizon),
        ],
        fill=(196, 160, 112),
    )
    # mud houses with terracotta roofs, one with a clinic sign
    for hx, hy, hw in (
        (int(width * 0.12), int(height * 0.58), 240),
        (int(width * 0.70), int(height * 0.60), 220),
        (int(width * 0.26), int(height * 0.66), 170),
    ):
        draw.rectangle((hx, hy, hx + hw, hy + hw // 2), fill=(226, 204, 166))
        draw.polygon(
            [(hx - 12, hy), (hx + hw + 12, hy), (hx + hw // 2, hy - hw // 3)], fill=(184, 92, 62)
        )
        draw.rectangle(
            (hx + hw // 3, hy + hw // 5, hx + hw // 3 + 36, hy + hw // 2), fill=(90, 70, 52)
        )
    cx = int(width * 0.12) + 60
    draw.rectangle((cx, int(height * 0.50), cx + 140, int(height * 0.54)), fill=(250, 250, 250))
    draw.rectangle((cx + 62, int(height * 0.505), cx + 78, int(height * 0.53)), fill=(200, 40, 40))
    draw.rectangle((cx + 55, int(height * 0.51), cx + 85, int(height * 0.52)), fill=(200, 40, 40))
    # trees
    for tx, ty in (
        (int(width * 0.04), int(height * 0.62)),
        (int(width * 0.92), int(height * 0.66)),
        (int(width * 0.60), int(height * 0.56)),
    ):
        draw.rectangle((tx - 8, ty, tx + 8, ty + 110), fill=(96, 64, 40))
        draw.ellipse((tx - 70, ty - 110, tx + 70, ty + 20), fill=(60, 120, 56))
    return _grain(img, seed)


def render_road(country: str, width: int, height: int, seed: int = 11) -> Image.Image:
    rng = random.Random(seed)
    horizon = int(height * 0.52)
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    _gradient(draw, width, 0, horizon, (150, 196, 234), (232, 240, 248))
    # skyline
    x = 0
    while x < width:
        bw = rng.randint(90, 180)
        bh = rng.randint(160, 340)
        col = rng.choice([(96, 110, 128), (120, 128, 140), (140, 150, 160), (80, 92, 110)])
        draw.rectangle((x, horizon - bh, x + bw, horizon), fill=col)
        for wy in range(horizon - bh + 16, horizon - 10, 34):
            for wx in range(x + 12, x + bw - 14, 26):
                draw.rectangle(
                    (wx, wy, wx + 12, wy + 16),
                    fill=(200, 220, 235) if rng.random() < 0.6 else (60, 70, 84),
                )
        x += bw + rng.randint(4, 14)
    # sidewalk and asphalt
    draw.rectangle((0, horizon, width, int(height * 0.62)), fill=(176, 174, 170))
    draw.rectangle((0, int(height * 0.62), width, height), fill=(72, 74, 80))
    # lane markings in perspective
    for i in range(-3, 4):
        x_top = width // 2 + i * 22
        x_bot = width // 2 + i * 180
        draw.line(
            [(x_top, int(height * 0.62)), (x_bot, height)],
            fill=(236, 236, 236),
            width=8 if i == 0 else 5,
        )
    # street lamps
    for lx in (int(width * 0.08), int(width * 0.92)):
        draw.rectangle((lx, int(height * 0.30), lx + 10, int(height * 0.62)), fill=(60, 60, 66))
        draw.ellipse(
            (lx - 18, int(height * 0.27), lx + 28, int(height * 0.31)), fill=(255, 240, 180)
        )
    return _grain(img, seed)


def render_home(country: str, width: int, height: int, seed: int = 13) -> Image.Image:
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    wall_h = int(height * 0.62)
    _gradient(draw, width, 0, wall_h, (236, 218, 194), (222, 198, 170))
    # wall window with daylight
    wx0, wy0 = int(width * 0.62), int(height * 0.14)
    draw.rectangle((wx0, wy0, wx0 + 380, wy0 + 300), fill=(176, 214, 240))
    draw.rectangle((wx0 + 186, wy0, wx0 + 194, wy0 + 300), fill=(240, 232, 220))
    draw.rectangle((wx0 - 12, wy0 - 12, wx0 + 392, wy0), fill=(246, 240, 230))
    # wood floor planks in perspective
    _gradient(draw, width, wall_h, height, (150, 102, 62), (116, 76, 44))
    for i in range(-8, 9):
        draw.line(
            [(width // 2 + i * 60, wall_h), (width // 2 + i * 260, height)],
            fill=(100, 66, 40),
            width=3,
        )
    # rug
    draw.polygon(
        [
            (width * 0.22, height * 0.86),
            (width * 0.78, height * 0.86),
            (width * 0.86, height * 0.98),
            (width * 0.14, height * 0.98),
        ],
        fill=(150, 60, 70),
    )
    # sofa silhouette and lamp
    sx0, sy0 = int(width * 0.14), int(height * 0.56)
    draw.rounded_rectangle((sx0, sy0, sx0 + 420, sy0 + 170), radius=26, fill=(92, 74, 96))
    draw.rounded_rectangle(
        (sx0 - 20, sy0 - 40, sx0 + 440, sy0 + 40), radius=22, fill=(108, 88, 112)
    )
    lx = int(width * 0.06)
    draw.rectangle((lx, int(height * 0.30), lx + 8, int(height * 0.60)), fill=(70, 60, 56))
    draw.polygon(
        [
            (lx - 50, int(height * 0.30)),
            (lx + 58, int(height * 0.30)),
            (lx + 30, int(height * 0.22)),
            (lx - 22, int(height * 0.22)),
        ],
        fill=(250, 232, 180),
    )
    return _grain(img, seed)


def render_environment(name: str, country: str, width: int, height: int) -> Image.Image:
    """Render one named environment at the requested size."""
    if name == "rural":
        return render_rural(country, width, height)
    if name == "road":
        return render_road(country, width, height)
    if name == "home":
        return render_home(country, width, height)
    raise ValueError(f"unknown environment: {name}")
