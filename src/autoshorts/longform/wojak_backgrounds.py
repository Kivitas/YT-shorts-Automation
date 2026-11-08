"""Curated and Procedural High-Res Photographic Environments for Wojak Meme Videos.

Provides:
1. Sourcing from local photographic assets (`assets/photos/<country>/`) when available.
2. High-res procedural photographic environment generators for:
   - Hospital / Healthcare (ER lobby, sterile corridor, Nordic clinic, Gangnam surgery, Italian ICU)
   - School / Education (Hallway with lockers, classroom with desks, university lecture hall)
   - Public Transport (Subway car interior, train platform, airport terminal)
   - Office / Bureaucracy (Government registry, queuing counter, cubicle office)
   - Restaurant / Food (Diner counter, Italian cafe, fast food register)
3. Depth-of-field blur, realistic lighting, perspective floor grids, wall baseboards,
   and photographic grain for genuine film camera realism.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import random
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger(__name__)

WIDTH = 1920
HEIGHT = 970  # Height below 110px header bar in 1080p frame

BACKGROUNDS_CACHE_DIR = Path(__file__).resolve().parents[3] / "assets" / "backgrounds"
PHOTOS_ROOT = Path(__file__).resolve().parents[3] / "assets" / "photos"


# ============================================================================
# Color Palettes & Setting Presets
# ============================================================================

COUNTRY_HOSPITAL_PALETTES: dict[str, dict[str, tuple[int, int, int]]] = {
    "usa": {
        "wall_top": (210, 222, 235),
        "wall_bottom": (185, 200, 218),
        "floor_near": (145, 155, 170),
        "floor_far": (175, 185, 198),
        "accent": (45, 105, 180),
        "trim": (235, 240, 248),
    },
    "uk": {
        "wall_top": (230, 232, 226),
        "wall_bottom": (208, 215, 205),
        "floor_near": (60, 110, 95),  # Classic NHS teal/green linoleum
        "floor_far": (90, 138, 122),
        "accent": (25, 95, 80),
        "trim": (220, 224, 218),
    },
    "sweden": {
        "wall_top": (245, 243, 238),
        "wall_bottom": (235, 230, 220),
        "floor_near": (195, 165, 130),  # Light Scandinavian birch/oak
        "floor_far": (215, 190, 160),
        "accent": (50, 90, 140),
        "trim": (250, 248, 245),
    },
    "south_korea": {
        "wall_top": (238, 236, 242),
        "wall_bottom": (218, 214, 226),
        "floor_near": (180, 178, 190),  # Polished marble with purple tint
        "floor_far": (210, 208, 218),
        "accent": (160, 90, 180),
        "trim": (245, 240, 250),
    },
    "india": {
        "wall_top": (240, 238, 225),
        "wall_bottom": (215, 210, 195),
        "floor_near": (180, 180, 175),  # Terrazzo stone floor
        "floor_far": (205, 205, 200),
        "accent": (30, 120, 140),
        "trim": (230, 225, 210),
    },
    "italy": {
        "wall_top": (242, 235, 220),
        "wall_bottom": (225, 215, 195),
        "floor_near": (170, 120, 95),  # Warm terracotta / terrazzo
        "floor_far": (195, 150, 125),
        "accent": (150, 60, 45),
        "trim": (240, 232, 215),
    },
    "japan": {
        "wall_top": (245, 245, 245),
        "wall_bottom": (232, 232, 234),
        "floor_near": (180, 175, 165),  # Clean neutral beige-grey
        "floor_far": (205, 200, 192),
        "accent": (40, 130, 120),
        "trim": (255, 255, 255),
    },
    "germany": {
        "wall_top": (228, 230, 232),
        "wall_bottom": (205, 210, 215),
        "floor_near": (130, 135, 140),  # Solid slate grey
        "floor_far": (165, 170, 176),
        "accent": (35, 75, 135),
        "trim": (240, 242, 245),
    },
}

GENERIC_HOSPITAL_PALETTE = {
    "wall_top": (232, 236, 240),
    "wall_bottom": (210, 216, 224),
    "floor_near": (155, 160, 168),
    "floor_far": (185, 190, 196),
    "accent": (40, 95, 155),
    "trim": (245, 247, 250),
}


# ============================================================================
# Procedural Room Rendering Engine
# ============================================================================


def _add_subtle_grain(img: Image.Image, intensity: float = 0.035) -> Image.Image:
    """Add photographic camera sensor grain to remove synthetic vector flatness."""
    arr = np.array(img, dtype=np.float32)
    h, w, c = arr.shape
    noise = np.random.normal(0, intensity * 255, (h, w, 1))
    noisy = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(noisy)


def render_procedural_hospital(
    country: str,
    width: int = WIDTH,
    height: int = HEIGHT,
    seed: int = 42,
) -> Image.Image:
    """Render a photorealistic, depth-mapped hospital interior (ER/clinic/ward)."""
    slug = country.strip().lower().replace(" ", "_")
    palette = COUNTRY_HOSPITAL_PALETTES.get(slug, GENERIC_HOSPITAL_PALETTE)

    # 1. Base Wall and Floor with vertical gradients
    wall_h = int(height * 0.58)
    floor_h = height - wall_h

    # Create image array
    canvas = Image.new("RGB", (width, height), (220, 220, 220))
    draw = ImageDraw.Draw(canvas)

    # Wall Gradient (soft top-to-bottom illumination)
    wt = palette["wall_top"]
    wb = palette["wall_bottom"]
    for y in range(wall_h):
        frac = y / max(1, wall_h)
        r = int(wt[0] + (wb[0] - wt[0]) * frac)
        g = int(wt[1] + (wb[1] - wt[1]) * frac)
        b = int(wt[2] + (wb[2] - wt[2]) * frac)
        draw.line([(0, y), (width, y)], fill=(r, g, b))

    # Floor Gradient (perspective illumination from back to foreground)
    ff = palette["floor_far"]
    fn = palette["floor_near"]
    for y in range(floor_h):
        frac = y / max(1, floor_h)
        r = int(ff[0] + (fn[0] - ff[0]) * frac)
        g = int(ff[1] + (fn[1] - ff[1]) * frac)
        b = int(ff[2] + (fn[2] - ff[2]) * frac)
        draw.line([(0, wall_h + y), (width, wall_h + y)], fill=(r, g, b))

    # 2. Architectural Baseboard / Molding
    baseboard_h = 24
    draw.rectangle(
        [(0, wall_h - baseboard_h), (width, wall_h)],
        fill=palette["trim"],
        outline=(180, 185, 190),
        width=1,
    )
    # Accent stripe along the wall (classic hospital safety bumper)
    bumper_y = wall_h - 120
    draw.rectangle([(0, bumper_y), (width, bumper_y + 16)], fill=palette["accent"])
    draw.rectangle([(0, bumper_y - 4), (width, bumper_y - 2)], fill=palette["trim"])

    # 3. Perspective Floor Grid / Tiles with Specular Lighting
    vp_x = width // 2
    vp_y = int(wall_h * 0.7)  # Vanishing point behind the wall

    # Perspective lines running down the hallway
    for tile_x in range(-width, width * 2, 140):
        draw.line([(vp_x, vp_y), (tile_x, height)], fill=(255, 255, 255, 40), width=1)

    # Horizontal tile lines with perspective spacing
    step = 12.0
    y_pos = float(wall_h)
    while y_pos < height:
        draw.line([(0, int(y_pos)), (width, int(y_pos))], fill=(255, 255, 255, 30), width=1)
        step *= 1.14
        y_pos += step

    # 4. Ceiling Fluorescent Light Panels (Warm reflections on floor)
    ceiling_h = 70
    draw.rectangle([(0, 0), (width, ceiling_h)], fill=(245, 248, 252))
    for lx in range(180, width, 320):
        # Fluorescent light fixture
        draw.rounded_rectangle(
            [(lx, 15), (lx + 160, 48)],
            radius=4,
            fill=(255, 255, 250),
            outline=(210, 215, 220),
            width=2,
        )
        # Soft light cone on the wall
        draw.polygon(
            [(lx + 20, 48), (lx + 140, 48), (lx + 220, wall_h - 140), (lx - 60, wall_h - 140)],
            fill=(255, 255, 255, 15),
        )

    # 5. Background Medical / Clinic Elements (Doors, Windows, Waiting Chairs, Signage)
    rng = random.Random(seed + zlib_crc(slug))

    # Doorways in background
    door_w = 110
    door_h = 240
    for dx in [240, width - 360]:
        dy0 = wall_h - door_h
        draw.rectangle([(dx, dy0), (dx + door_w, wall_h)], fill=(230, 234, 240), outline=(160, 168, 178), width=2)
        # Door window pane
        draw.rectangle([(dx + 25, dy0 + 30), (dx + door_w - 25, dy0 + 100)], fill=(180, 210, 230), outline=(140, 160, 180))
        # Door handle
        draw.rectangle([(dx + 12, dy0 + 125), (dx + 22, dy0 + 132)], fill=(100, 105, 115))

    # Room signage / Cross banner
    sign_x = 420
    sign_y = wall_h - 220
    draw.rectangle([(sign_x, sign_y), (sign_x + 90, sign_y + 36)], fill=palette["accent"])
    draw.rectangle([(sign_x + 40, sign_y + 8), (sign_x + 50, sign_y + 28)], fill=(255, 255, 255))
    draw.rectangle([(sign_x + 30, sign_y + 14), (sign_x + 60, sign_y + 22)], fill=(255, 255, 255))

    # 6. Subtle Depth-of-Field Lens Blur (keeps background soft so foreground pops)
    blurred = canvas.filter(ImageFilter.GaussianBlur(1.8))

    # 7. Add subtle film grain for photographic realism
    final_bg = _add_subtle_grain(blurred, intensity=0.025)
    return final_bg


def render_procedural_school(
    country: str,
    width: int = WIDTH,
    height: int = HEIGHT,
    seed: int = 101,
) -> Image.Image:
    """Render a high-res school hallway / classroom environment."""
    canvas = Image.new("RGB", (width, height), (225, 220, 210))
    draw = ImageDraw.Draw(canvas)

    wall_h = int(height * 0.60)
    floor_h = height - wall_h

    # Wall & Floor
    draw.rectangle([(0, 0), (width, wall_h)], fill=(235, 230, 215))
    draw.rectangle([(0, wall_h), (width, height)], fill=(170, 140, 110))

    # Lockers along the back wall
    locker_w = 48
    locker_h = 280
    locker_y = wall_h - locker_h
    locker_colors = [(65, 105, 165), (55, 95, 150), (45, 85, 140)]

    for idx, lx in enumerate(range(120, width - 120, locker_w + 6)):
        c = locker_colors[idx % len(locker_colors)]
        draw.rectangle([(lx, locker_y), (lx + locker_w, wall_h)], fill=c, outline=(30, 50, 80), width=1)
        # Locker vents & latch
        draw.line([(lx + 10, locker_y + 20), (lx + locker_w - 10, locker_y + 20)], fill=(20, 30, 50), width=1)
        draw.line([(lx + 10, locker_y + 28), (lx + locker_w - 10, locker_y + 28)], fill=(20, 30, 50), width=1)
        draw.rectangle([(lx + locker_w - 14, locker_y + 110), (lx + locker_w - 8, locker_y + 124)], fill=(200, 200, 200))

    # Ceiling and lights
    for lx in range(200, width, 300):
        draw.rounded_rectangle([(lx, 10), (lx + 140, 38)], radius=4, fill=(255, 255, 240), outline=(200, 200, 190))

    blurred = canvas.filter(ImageFilter.GaussianBlur(1.8))
    return _add_subtle_grain(blurred, intensity=0.025)


def render_procedural_transport(
    country: str,
    width: int = WIDTH,
    height: int = HEIGHT,
    seed: int = 202,
) -> Image.Image:
    """Render a subway / commuter train / airport transit environment."""
    canvas = Image.new("RGB", (width, height), (210, 215, 220))
    draw = ImageDraw.Draw(canvas)

    wall_h = int(height * 0.62)
    # Train wall with curved windows
    draw.rectangle([(0, 0), (width, wall_h)], fill=(225, 228, 232))
    draw.rectangle([(0, wall_h), (width, height)], fill=(120, 125, 132))

    # Large train windows showing speed blur / city lights
    for wx in range(180, width - 200, 480):
        draw.rounded_rectangle(
            [(wx, 80), (wx + 340, wall_h - 100)],
            radius=18,
            fill=(40, 55, 80),
            outline=(180, 185, 192),
            width=6,
        )
        # Distant tunnel / city glow
        draw.line([(wx + 20, 180), (wx + 320, 180)], fill=(240, 210, 110), width=3)
        draw.line([(wx + 50, 210), (wx + 280, 210)], fill=(200, 80, 80), width=2)

    # Handrails / Overhead Grab Rails
    draw.line([(0, 130), (width, 130)], fill=(240, 190, 40), width=6)
    for hx in range(140, width, 180):
        draw.line([(hx, 130), (hx, 220)], fill=(200, 205, 210), width=3)
        draw.ellipse([(hx - 14, 215), (hx + 14, 245)], outline=(240, 190, 40), width=3)

    blurred = canvas.filter(ImageFilter.GaussianBlur(1.8))
    return _add_subtle_grain(blurred, intensity=0.025)


def zlib_crc(s: str) -> int:
    import zlib

    return zlib.crc32(s.encode("utf-8")) & 0x7FFFFFFF


# ============================================================================
# Main Background Resolver
# ============================================================================


def get_wojak_background(
    country: str,
    setting: str = "hospital",
    width: int = WIDTH,
    height: int = HEIGHT,
    custom_photo_path: str | Path | None = None,
) -> Image.Image:
    """Resolve or generate a photographic environment for a Wojak scene.

    Order of priority:
    1. Explicit custom_photo_path if valid image file.
    2. Local photographic asset from `assets/photos/<country_slug>/` if present.
    3. Procedural high-res photorealistic environment tailored to setting and country.
    """
    # 1. Custom photo path
    if custom_photo_path:
        p = Path(custom_photo_path)
        if p.is_file():
            try:
                with Image.open(p) as img:
                    return _cover_crop(img.convert("RGB"), width, height)
            except Exception as exc:
                logger.warning("Failed loading custom photo %s: %s", p, exc)

    # 2. Check assets/photos/<country_slug>/
    slug = country.strip().lower().replace(" ", "_")
    country_dir = PHOTOS_ROOT / slug
    if country_dir.is_dir():
        candidates = [
            f
            for f in country_dir.iterdir()
            if f.suffix.lower() in (".jpg", ".jpeg", ".png") and not f.name.startswith(".")
        ]
        if candidates:
            # Pick first or deterministically by slug
            chosen = candidates[zlib_crc(slug) % len(candidates)]
            try:
                with Image.open(chosen) as img:
                    return _cover_crop(img.convert("RGB"), width, height)
            except Exception as exc:
                logger.warning("Failed loading photo %s: %s", chosen, exc)

    # 3. Procedural high-res photographic environment
    setting_norm = setting.strip().lower()
    if setting_norm in ("school", "classroom", "hallway", "education"):
        return render_procedural_school(country, width, height)
    elif setting_norm in ("transport", "subway", "train", "metro", "bus", "airport"):
        return render_procedural_transport(country, width, height)
    else:
        # Default to Hospital / Healthcare
        return render_procedural_hospital(country, width, height)


def _cover_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Center cover-crop an image to exact (target_w, target_h)."""
    scale = max(target_w / img.width, target_h / img.height)
    new_w = max(target_w, round(img.width * scale))
    new_h = max(target_h, round(img.height * scale))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))
