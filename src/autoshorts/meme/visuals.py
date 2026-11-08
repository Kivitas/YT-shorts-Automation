"""Manga and Anime visual and animation rendering engine for meme videos.

Generates procedural anime characters with expressive faces and poses,
cinematic backgrounds, comic speed lines, screentone halftone effects,
speech bubbles, heading banners, and high-impact subtitle overlays.
Supports both 9:16 vertical (1080x1920) and 16:9 landscape (1920x1080).
"""

from __future__ import annotations

import functools
import math
import random
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from autoshorts.meme.types import MemeScript, MemeSegment

# Standard resolutions
RESOLUTION_9_16 = (1080, 1920)
RESOLUTION_16_9 = (1920, 1080)

# Colors
COLOR_WHITE = (255, 255, 255)
COLOR_BLACK = (15, 15, 20)
COLOR_YELLOW = (255, 230, 0)
COLOR_CYAN = (0, 240, 255)
COLOR_CORAL = (255, 75, 75)
COLOR_GOLD = (255, 191, 0)
COLOR_PURPLE = (168, 85, 247)
COLOR_BLUE_SHADOW = (30, 41, 59)


def get_resolution(aspect_ratio: str) -> tuple[int, int]:
    """Return (width, height) for the requested aspect ratio."""
    if aspect_ratio == "16:9":
        return RESOLUTION_16_9
    return RESOLUTION_9_16


def get_font(family: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a system TrueType font with fallbacks to avoid missing font crashes."""
    candidates: list[str] = []
    family_lower = family.lower()

    if "impact" in family_lower:
        candidates = ["impact.ttf", "arialbd.ttf", "calibrib.ttf", "arial.ttf"]
    elif "bold" in family_lower or "black" in family_lower:
        candidates = ["arialbd.ttf", "calibrib.ttf", "impact.ttf", "arial.ttf"]
    elif "emoji" in family_lower:
        candidates = ["seguiemj.ttf", "arial.ttf"]
    else:
        candidates = ["arialbd.ttf", "arial.ttf", "calibri.ttf", "DejaVuSans.ttf"]

    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue

    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def draw_text_with_outline(
    draw: ImageDraw.ImageDraw,
    pos: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int] | tuple[int, int, int, int],
    outline_color: tuple[int, int, int] | tuple[int, int, int, int] = (0, 0, 0),
    outline_width: int = 4,
    anchor: str = "mm",
) -> None:
    """Draw text with a bold outline and drop shadow using native Pillow stroke."""
    x, y = pos
    shadow_offset = max(2, outline_width)
    shadow_fill = (0, 0, 0, 160) if len(outline_color) == 4 else (0, 0, 0)
    # Shadow pass
    draw.text(
        (x + shadow_offset, y + shadow_offset),
        text,
        font=font,
        fill=shadow_fill,
        anchor=anchor,
        stroke_width=outline_width,
        stroke_fill=shadow_fill,
    )
    # Foreground with stroke
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        anchor=anchor,
        stroke_width=outline_width,
        stroke_fill=outline_color,
    )


# ============================================================================
# Procedural Manga & Anime Backgrounds
# ============================================================================


@functools.lru_cache(maxsize=32)
def _get_gradient_layer(
    width: int,
    height: int,
    top_color: tuple[int, int, int],
    bottom_color: tuple[int, int, int],
) -> Image.Image:
    r1, g1, b1 = top_color
    r2, g2, b2 = bottom_color
    t = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    r = (r1 + (r2 - r1) * t).astype(np.uint8)
    g = (g1 + (g2 - g1) * t).astype(np.uint8)
    b = (b1 + (b2 - b1) * t).astype(np.uint8)
    rgba = np.dstack(
        [
            np.broadcast_to(r, (height, width)),
            np.broadcast_to(g, (height, width)),
            np.broadcast_to(b, (height, width)),
            np.full((height, width), 255, dtype=np.uint8),
        ]
    )
    return Image.fromarray(rgba, mode="RGBA")


def _draw_gradient(
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
    top_color: tuple[int, int, int],
    bottom_color: tuple[int, int, int],
) -> None:
    """Render a vertical smooth color gradient quickly."""
    grad_img = _get_gradient_layer(width, height, top_color, bottom_color)
    if hasattr(draw, "_image") and draw._image is not None:
        draw._image.paste(grad_img, (0, 0))
    else:
        for y in range(0, height, 4):
            ratio = y / max(1, height)
            r = int(top_color[0] + (bottom_color[0] - top_color[0]) * ratio)
            g = int(top_color[1] + (bottom_color[1] - top_color[1]) * ratio)
            b = int(top_color[2] + (bottom_color[2] - top_color[2]) * ratio)
            draw.rectangle([0, y, width, y + 4], fill=(r, g, b))


def draw_background(
    width: int,
    height: int,
    theme: str,
    visual_style: str = "anime_color",
    time_sec: float = 0.0,
) -> Image.Image:
    """Synthesize a procedural anime/manga background tailored to the theme."""
    img = Image.new("RGBA", (width, height), (30, 30, 35, 255))
    draw = ImageDraw.Draw(img)
    theme = (theme or "classroom").lower().strip()

    if theme == "train_station":
        # Tokyo / Shinkansen high-speed rail platform
        _draw_gradient(draw, width, height, (70, 85, 110), (140, 155, 175))
        # Overhead steel support trusses
        for x in range(0, width + 100, 160):
            draw.line([(x, 0), (x - 60, int(height * 0.4))], fill=(40, 45, 55), width=8)
            draw.line([(x, int(height * 0.4)), (x + 80, 0)], fill=(30, 35, 45), width=6)
        draw.line([(0, int(height * 0.4)), (width, int(height * 0.4))], fill=(25, 30, 40), width=12)

        # Distant tracks & train car
        track_y = int(height * 0.55)
        draw.rectangle([0, track_y, width, height], fill=(160, 165, 175))  # platform floor
        draw.rectangle(
            [0, int(height * 0.35), int(width * 0.42), track_y], fill=(220, 225, 235)
        )  # train body
        draw.rectangle(
            [0, int(height * 0.40), int(width * 0.42), int(height * 0.43)], fill=(30, 60, 180)
        )  # blue stripe
        # Train windows with warm light
        for wx in range(30, int(width * 0.38), 90):
            draw.rectangle(
                [wx, int(height * 0.36), wx + 65, int(height * 0.395)], fill=(255, 245, 180)
            )

        # Yellow safety tactile warning line
        draw.rectangle([0, track_y + 10, width, track_y + 35], fill=(250, 204, 21))
        for tx in range(0, width, 30):
            draw.rectangle([tx, track_y + 14, tx + 16, track_y + 31], fill=(210, 160, 10))

        # Overhead LED departure board
        board_w, board_h = int(width * 0.6), int(height * 0.12)
        bx = (width - board_w) // 2
        by = int(height * 0.10)
        draw.rectangle(
            [bx, by, bx + board_w, by + board_h], fill=(15, 15, 20), outline=(80, 80, 90), width=4
        )
        draw.line([bx + 40, by, bx + 40, 0], fill=(50, 50, 60), width=6)
        draw.line([bx + board_w - 40, by, bx + board_w - 40, 0], fill=(50, 50, 60), width=6)
        # Glowing LED lines
        f_led = get_font("bold", max(18, int(board_h * 0.28)))
        draw.text(
            (bx + 24, by + int(board_h * 0.18)),
            "SHINKANSEN NOZOMI 23",
            font=f_led,
            fill=(255, 180, 0),
        )
        draw.text(
            (bx + 24, by + int(board_h * 0.55)),
            "14:32 TOKYO   TRACK 14  ON TIME",
            font=f_led,
            fill=(74, 222, 128),
        )

    elif theme == "classroom":
        # Japanese anime high school classroom with sunny window & chalkboard
        _draw_gradient(draw, width, height, (245, 235, 220), (220, 205, 185))
        # Huge tall windows on the left
        win_w = int(width * 0.38)
        draw.rectangle([0, 0, win_w, int(height * 0.7)], fill=(125, 211, 252))  # bright blue sky
        # Fluffy anime clouds
        cloud_color = (255, 255, 255, 240)
        draw.ellipse([20, int(height * 0.15), 180, int(height * 0.28)], fill=cloud_color)
        draw.ellipse([80, int(height * 0.12), 260, int(height * 0.29)], fill=cloud_color)
        draw.ellipse([180, int(height * 0.18), 340, int(height * 0.30)], fill=cloud_color)
        # Window frame panes
        for y in range(0, int(height * 0.75), int(height * 0.22)):
            draw.line([(0, y), (win_w, y)], fill=(255, 255, 255), width=8)
        draw.line([(win_w // 2, 0), (win_w // 2, int(height * 0.7))], fill=(255, 255, 255), width=8)
        draw.line([(win_w, 0), (win_w, int(height * 0.7))], fill=(160, 130, 95), width=12)

        # Huge green chalkboard on the right
        cb_x1 = int(width * 0.42)
        cb_y1 = int(height * 0.10)
        cb_x2 = int(width * 0.96)
        cb_y2 = int(height * 0.52)
        draw.rectangle(
            [cb_x1, cb_y1, cb_x2, cb_y2], fill=(20, 75, 45), outline=(130, 80, 30), width=10
        )
        # Chalk formulas and sketches
        f_chalk = get_font("bold", max(16, int(height * 0.02)))
        draw.text(
            (cb_x1 + 30, cb_y1 + 30),
            "MATH:  f(x) = sin(x)^2 + cos(x)^2 = 1",
            font=f_chalk,
            fill=(230, 240, 230),
        )
        draw.text(
            (cb_x1 + 30, cb_y1 + 70),
            "HOMEWORK: CH. 4 Q1 - Q15 (DUE TODAY!)",
            font=f_chalk,
            fill=(250, 250, 210),
        )
        draw.polygon(
            [(cb_x1 + 40, cb_y1 + 130), (cb_x1 + 140, cb_y1 + 130), (cb_x1 + 40, cb_y1 + 190)],
            outline=(255, 255, 255),
            width=3,
        )

        # Wooden parquet floor with perspective planks
        floor_y = int(height * 0.65)
        draw.rectangle([0, floor_y, width, height], fill=(180, 115, 60))
        for px in range(-100, width + 200, 120):
            draw.line([(px, floor_y), (px * 1.5 - 200, height)], fill=(140, 85, 35), width=4)

        # Wooden school desks
        for dx in [int(width * 0.15), int(width * 0.55), int(width * 0.82)]:
            dy = int(height * 0.72)
            draw.rectangle(
                [dx, dy, dx + int(width * 0.22), dy + 25],
                fill=(210, 140, 70),
                outline=(70, 40, 20),
                width=3,
            )
            draw.line([(dx + 15, dy + 25), (dx + 15, dy + 140)], fill=(50, 50, 60), width=5)
            draw.line(
                [(dx + int(width * 0.22) - 15, dy + 25), (dx + int(width * 0.22) - 15, dy + 140)],
                fill=(50, 50, 60),
                width=5,
            )

    elif theme == "supermarket":
        # Supermarket aisle with colorful grocery shelves & bright fluorescents
        _draw_gradient(draw, width, height, (230, 235, 245), (195, 205, 220))
        # Ceiling fluorescent strip lights
        for lx in [int(width * 0.25), int(width * 0.5), int(width * 0.75)]:
            draw.rectangle(
                [lx - 25, 0, lx + 25, int(height * 0.15)],
                fill=(255, 255, 255),
                outline=(200, 210, 225),
                width=2,
            )
            draw.rectangle(
                [lx - 15, int(height * 0.02), lx + 15, int(height * 0.13)], fill=(255, 255, 240)
            )

        # Hanging aisle sign
        sign_w, sign_h = int(width * 0.45), int(height * 0.08)
        sx = (width - sign_w) // 2
        sy = int(height * 0.16)
        draw.rectangle(
            [sx, sy, sx + sign_w, sy + sign_h], fill=(220, 38, 38), outline=(255, 255, 255), width=4
        )
        draw.line([sx + 30, sy, sx + 30, 0], fill=(120, 120, 130), width=3)
        draw.line([sx + sign_w - 30, sy, sx + sign_w - 30, 0], fill=(120, 120, 130), width=3)
        f_sign = get_font("impact", max(18, int(sign_h * 0.45)))
        draw.text(
            (width // 2, sy + sign_h // 2),
            "AISLE 3 - SNACKS & RAMEN",
            font=f_sign,
            fill=(255, 255, 255),
            anchor="mm",
        )

        # Floor with shiny tile perspective
        floor_y = int(height * 0.58)
        draw.rectangle([0, floor_y, width, height], fill=(220, 225, 230))
        for line_x in range(0, width, 100):
            draw.line(
                [(line_x, floor_y), (int(line_x * 1.6 - width * 0.3), height)],
                fill=(190, 200, 210),
                width=3,
            )

        # Grocery shelves on left and right
        colors_items = [
            (239, 68, 68),
            (59, 130, 246),
            (34, 197, 94),
            (234, 179, 8),
            (168, 85, 247),
            (249, 115, 22),
        ]
        for _side, x1, x2 in [("left", 0, int(width * 0.28)), ("right", int(width * 0.72), width)]:
            for shelf_y in range(int(height * 0.28), floor_y, 70):
                draw.rectangle(
                    [x1, shelf_y, x2, shelf_y + 12], fill=(160, 170, 185), outline=(100, 110, 125)
                )
                # Items on shelf
                item_x = x1 + 10
                while item_x < x2 - 20:
                    iw = random.Random(shelf_y + item_x).randint(20, 35)
                    col = colors_items[(item_x + shelf_y) % len(colors_items)]
                    draw.rectangle(
                        [item_x, shelf_y - 45, item_x + iw, shelf_y],
                        fill=col,
                        outline=(40, 40, 50),
                        width=2,
                    )
                    item_x += iw + 8

    elif theme == "snowy_winter":
        # Cold snowy landscape with gradient twilight sky, snowdrifts, and falling flakes
        _draw_gradient(draw, width, height, (30, 27, 75), (147, 197, 253))
        # Distant frosty mountains / hills
        draw.polygon(
            [
                (0, int(height * 0.55)),
                (int(width * 0.3), int(height * 0.35)),
                (int(width * 0.7), int(height * 0.48)),
                (width, int(height * 0.38)),
                (width, height),
                (0, height),
            ],
            fill=(190, 215, 240),
        )
        draw.polygon(
            [
                (0, int(height * 0.65)),
                (int(width * 0.5), int(height * 0.50)),
                (width, int(height * 0.62)),
                (width, height),
                (0, height),
            ],
            fill=(225, 235, 245),
        )

        # Bare winter pine trees with snow caps
        for tx, ty, sz in [
            (int(width * 0.12), int(height * 0.58), 90),
            (int(width * 0.85), int(height * 0.54), 110),
        ]:
            draw.line([(tx, ty), (tx, ty - sz)], fill=(80, 55, 40), width=8)
            draw.polygon(
                [
                    (tx - sz // 2, ty - int(sz * 0.3)),
                    (tx, ty - sz),
                    (tx + sz // 2, ty - int(sz * 0.3)),
                ],
                fill=(245, 250, 255),
            )
            draw.polygon(
                [
                    (tx - sz // 2, ty - int(sz * 0.3)),
                    (tx, ty - sz),
                    (tx + sz // 2, ty - int(sz * 0.3)),
                ],
                outline=(180, 205, 230),
                width=2,
            )

        # Big snowy ground drift
        draw.ellipse(
            [-100, int(height * 0.68), width + 100, int(height * 1.1)], fill=(248, 250, 252)
        )

        # Animated falling snowflakes
        flake_rng = random.Random(42)
        for i in range(75):
            fx = flake_rng.randint(0, width)
            base_fy = flake_rng.randint(0, height)
            fy = (base_fy + int(time_sec * (80 + (i % 6) * 30))) % height
            fr = 2 + (i % 5)
            draw.ellipse([fx - fr, fy - fr, fx + fr, fy + fr], fill=(255, 255, 255, 220))

    elif theme == "gaming_room":
        # Dark aesthetic cyberpunk gaming room with neon RGB strips & dual monitors
        _draw_gradient(draw, width, height, (10, 10, 15), (25, 20, 35))
        # Acoustic hexagon wall panels
        hex_color = (35, 30, 48)
        for row in range(4):
            for col in range(6):
                hx = int(col * 140 + (row % 2) * 70)
                hy = int(height * 0.12 + row * 80)
                draw.regular_polygon((hx, hy, 35), 6, fill=hex_color, outline=(55, 45, 75))

        # Wall RGB light strips
        draw.line(
            [(0, int(height * 0.08)), (width, int(height * 0.08))], fill=(236, 72, 153), width=8
        )  # Neon Pink
        draw.line(
            [(0, int(height * 0.48)), (width, int(height * 0.48))], fill=(6, 182, 212), width=8
        )  # Neon Cyan

        # Heavy gaming desk
        desk_y = int(height * 0.62)
        draw.rectangle(
            [0, desk_y, width, height], fill=(18, 18, 24), outline=(6, 182, 212), width=4
        )

        # Dual glowing monitors
        # Left monitor (Game screen)
        draw.rectangle(
            [int(width * 0.12), int(height * 0.28), int(width * 0.48), desk_y - 20],
            fill=(20, 25, 35),
            outline=(80, 80, 100),
            width=6,
        )
        draw.rectangle(
            [int(width * 0.14), int(height * 0.30), int(width * 0.46), desk_y - 35],
            fill=(30, 41, 59),
        )  # Game display
        draw.polygon(
            [
                (int(width * 0.20), desk_y - 50),
                (int(width * 0.30), int(height * 0.38)),
                (int(width * 0.40), desk_y - 50),
            ],
            fill=(239, 68, 68),
        )
        # Right monitor (Discord / Chat / Radar)
        draw.rectangle(
            [int(width * 0.52), int(height * 0.28), int(width * 0.88), desk_y - 20],
            fill=(20, 25, 35),
            outline=(80, 80, 100),
            width=6,
        )
        draw.rectangle(
            [int(width * 0.54), int(height * 0.30), int(width * 0.86), desk_y - 35],
            fill=(15, 23, 42),
        )
        for bar_y in range(int(height * 0.32), desk_y - 45, 25):
            draw.line(
                [(int(width * 0.56), bar_y), (int(width * 0.80), bar_y)],
                fill=(56, 189, 248),
                width=4,
            )

        # Glowing RGB Keyboard & Mouse on desk
        draw.rectangle(
            [int(width * 0.32), desk_y + 30, int(width * 0.68), desk_y + 90],
            fill=(30, 30, 38),
            outline=(236, 72, 153),
            width=3,
        )
        draw.ellipse(
            [int(width * 0.74), desk_y + 35, int(width * 0.82), desk_y + 85],
            fill=(30, 30, 38),
            outline=(6, 182, 212),
            width=3,
        )

    elif theme == "street_city":
        # Modern urban city street with skyscrapers, zebra crossing, and neon glow
        _draw_gradient(draw, width, height, (15, 23, 42), (51, 65, 85))
        # Skyscraper silhouettes with window grids
        bldgs = [
            (0, int(width * 0.22), int(height * 0.22)),
            (int(width * 0.20), int(width * 0.45), int(height * 0.12)),
            (int(width * 0.42), int(width * 0.68), int(height * 0.18)),
            (int(width * 0.65), int(width * 0.85), int(height * 0.15)),
            (int(width * 0.82), width, int(height * 0.25)),
        ]
        for bx1, bx2, by in bldgs:
            draw.rectangle(
                [bx1, by, bx2, int(height * 0.62)], fill=(20, 28, 45), outline=(35, 45, 70), width=2
            )
            # Illuminated windows
            w_rng = random.Random(bx1 + by)
            for wx in range(bx1 + 12, bx2 - 12, 24):
                for wy in range(by + 16, int(height * 0.58), 35):
                    if w_rng.random() > 0.4:
                        win_col = (254, 240, 138) if w_rng.random() > 0.3 else (147, 197, 253)
                        draw.rectangle([wx, wy, wx + 14, wy + 20], fill=win_col)

        # Distant neon billboards
        draw.rectangle(
            [int(width * 0.24), int(height * 0.24), int(width * 0.40), int(height * 0.32)],
            fill=(236, 72, 153),
        )
        f_neo = get_font("bold", 22)
        draw.text(
            (int(width * 0.32), int(height * 0.28)),
            "RAMEN 24H",
            font=f_neo,
            fill=(255, 255, 255),
            anchor="mm",
        )

        # Asphalt street and sidewalk
        road_y = int(height * 0.60)
        draw.rectangle([0, road_y, width, height], fill=(35, 38, 44))
        # Sidewalk curb
        draw.rectangle([0, road_y, width, road_y + 20], fill=(100, 110, 120))

        # Bold white zebra crosswalk lines
        for zx in range(int(width * 0.08), width, 120):
            draw.polygon(
                [(zx, road_y + 40), (zx + 60, road_y + 40), (zx + 30, height), (zx - 30, height)],
                fill=(240, 245, 250),
            )

        # Street lamp post
        lamp_x = int(width * 0.88)
        draw.line([(lamp_x, road_y + 10), (lamp_x, int(height * 0.32))], fill=(70, 75, 85), width=8)
        draw.arc(
            [lamp_x - 50, int(height * 0.28), lamp_x, int(height * 0.36)],
            180,
            270,
            fill=(70, 75, 85),
            width=8,
        )
        # Lamp light cone glow
        draw.polygon(
            [
                (lamp_x - 55, int(height * 0.34)),
                (lamp_x - 140, road_y + 30),
                (lamp_x + 30, road_y + 30),
            ],
            fill=(254, 240, 138, 50),
        )

    elif theme == "kitchen":
        # Warm family kitchen with countertop, stove, fridge, and steam
        _draw_gradient(draw, width, height, (254, 243, 199), (253, 230, 138))
        # Tiled backsplash
        for tx in range(0, width, 50):
            draw.line([(tx, 0), (tx, int(height * 0.58))], fill=(240, 220, 180), width=2)
        for ty in range(0, int(height * 0.58), 40):
            draw.line([(0, ty), (width, ty)], fill=(240, 220, 180), width=2)

        # Countertop
        counter_y = int(height * 0.54)
        draw.rectangle(
            [0, counter_y, width, height], fill=(217, 119, 6), outline=(146, 64, 14), width=6
        )

        # Stainless steel refrigerator on the left
        rf_w = int(width * 0.32)
        draw.rectangle(
            [0, int(height * 0.15), rf_w, counter_y],
            fill=(226, 232, 240),
            outline=(148, 163, 184),
            width=4,
        )
        draw.line(
            [(0, int(height * 0.34)), (rf_w, int(height * 0.34))], fill=(100, 116, 139), width=3
        )
        # Refrigerator magnets & sticky notes
        draw.rectangle([40, int(height * 0.22), 85, int(height * 0.27)], fill=(250, 204, 21))
        draw.rectangle([95, int(height * 0.25), 140, int(height * 0.30)], fill=(244, 114, 182))

        # Cooking stove with hot soup pot & steam curls
        pot_x = int(width * 0.65)
        pot_y = counter_y - 65
        draw.rectangle(
            [pot_x, pot_y, pot_x + 110, counter_y],
            fill=(71, 85, 105),
            outline=(30, 41, 59),
            width=3,
        )
        draw.ellipse(
            [pot_x, pot_y - 12, pot_x + 110, pot_y + 12],
            fill=(148, 163, 184),
            outline=(30, 41, 59),
            width=3,
        )
        # Rising steam
        for s_idx in range(3):
            sx = pot_x + 25 + s_idx * 30
            sy_base = pot_y - 20
            sway = int(math.sin(time_sec * 3.0 + s_idx) * 12)
            draw.arc(
                [sx - 15 + sway, sy_base - 50, sx + 15 + sway, sy_base],
                180,
                360,
                fill=(255, 255, 255, 180),
                width=4,
            )

    elif theme == "gym":
        # Fitness gym with mirrors, dumbbell racks, and barbells
        _draw_gradient(draw, width, height, (24, 24, 27), (39, 39, 42))
        # Giant gym mirror wall reflecting lights
        mirror_y = int(height * 0.15)
        mirror_h = int(height * 0.45)
        draw.rectangle(
            [int(width * 0.08), mirror_y, int(width * 0.92), mirror_y + mirror_h],
            fill=(45, 50, 60),
            outline=(113, 113, 122),
            width=6,
        )
        # Neon gym motivation sign
        f_gym = get_font("impact", max(24, int(mirror_h * 0.16)))
        draw.text(
            (width // 2, mirror_y + int(mirror_h * 0.20)),
            "LIGHT WEIGHT BABY!",
            font=f_gym,
            fill=(234, 179, 8),
            anchor="mm",
        )

        # Rubber floor mats
        floor_y = mirror_y + mirror_h
        draw.rectangle([0, floor_y, width, height], fill=(18, 18, 18))
        for gx in range(0, width, 80):
            draw.line([(gx, floor_y), (gx, height)], fill=(28, 28, 30), width=2)

        # Dumbbell rack
        rack_y = floor_y + 20
        draw.rectangle(
            [int(width * 0.15), rack_y, int(width * 0.85), rack_y + 16], fill=(82, 82, 91)
        )
        for dbx in range(int(width * 0.18), int(width * 0.82), 65):
            # Hex dumbbell
            draw.rectangle(
                [dbx, rack_y - 25, dbx + 12, rack_y],
                fill=(24, 24, 27),
                outline=(63, 63, 70),
                width=2,
            )
            draw.line([dbx + 12, rack_y - 12, dbx + 35, rack_y - 12], fill=(161, 161, 170), width=5)
            draw.rectangle(
                [dbx + 35, rack_y - 25, dbx + 47, rack_y],
                fill=(24, 24, 27),
                outline=(63, 63, 70),
                width=2,
            )

    elif theme == "zombie_wasteland":
        # Apocalyptic ruined skyline with smoky blood-red skies and crumbling ruins
        _draw_gradient(draw, width, height, (69, 10, 10), (194, 65, 12))
        # Drifting dark smoke plumes
        for sm_idx in range(5):
            sm_x = int(width * (0.15 + sm_idx * 0.20))
            sm_y = int(height * 0.25)
            draw.ellipse([sm_x - 70, sm_y - 50, sm_x + 70, sm_y + 50], fill=(30, 20, 20, 140))

        # Ruined collapsed buildings
        draw.polygon(
            [
                (0, int(height * 0.40)),
                (int(width * 0.25), int(height * 0.28)),
                (int(width * 0.28), int(height * 0.35)),
                (int(width * 0.45), int(height * 0.32)),
                (int(width * 0.45), int(height * 0.60)),
                (0, int(height * 0.60)),
            ],
            fill=(24, 24, 27),
        )
        draw.polygon(
            [
                (int(width * 0.55), int(height * 0.60)),
                (int(width * 0.55), int(height * 0.25)),
                (int(width * 0.70), int(height * 0.38)),
                (int(width * 0.88), int(height * 0.30)),
                (width, int(height * 0.45)),
                (width, int(height * 0.60)),
            ],
            fill=(30, 30, 36),
        )

        # Cracked ruined asphalt
        floor_y = int(height * 0.60)
        draw.rectangle([0, floor_y, width, height], fill=(20, 20, 22))
        # Fissures / cracks
        draw.line(
            [(int(width * 0.2), floor_y), (int(width * 0.35), height)], fill=(120, 30, 15), width=4
        )
        draw.line(
            [(int(width * 0.6), floor_y + 20), (int(width * 0.75), height)],
            fill=(120, 30, 15),
            width=3,
        )

    elif theme == "airport":
        # Floor-to-ceiling glass airport terminal overlooking runway with jet airliner
        _draw_gradient(draw, width, height, (56, 189, 248), (224, 242, 254))
        # Runway tarmac
        tarmac_y = int(height * 0.48)
        draw.rectangle([0, tarmac_y, width, height], fill=(100, 116, 139))
        draw.line([0, tarmac_y + 40, width, tarmac_y + 40], fill=(250, 204, 21), width=6)

        # Commercial airliner parked on tarmac
        plane_cx = int(width * 0.55)
        plane_cy = tarmac_y - 20
        # Fuselage
        draw.ellipse(
            [plane_cx - 220, plane_cy - 45, plane_cx + 180, plane_cy + 25],
            fill=(248, 250, 252),
            outline=(30, 41, 59),
            width=3,
        )
        # Tail fin
        draw.polygon(
            [
                (plane_cx + 120, plane_cy - 40),
                (plane_cx + 180, plane_cy - 110),
                (plane_cx + 195, plane_cy - 40),
            ],
            fill=(37, 99, 235),
            outline=(30, 41, 59),
            width=3,
        )
        # Wing
        draw.polygon(
            [
                (plane_cx - 40, plane_cy),
                (plane_cx - 140, plane_cy + 30),
                (plane_cx - 20, plane_cy + 20),
            ],
            fill=(203, 213, 225),
            outline=(30, 41, 59),
            width=3,
        )

        # Huge terminal glass mullions in foreground
        for mx in range(0, width + 100, int(width * 0.32)):
            draw.line([(mx, 0), (mx, height)], fill=(51, 65, 85), width=14)
        draw.line([(0, tarmac_y + 80), (width, tarmac_y + 80)], fill=(30, 41, 59), width=18)
        # Interior terminal floor
        draw.rectangle([0, tarmac_y + 80, width, height], fill=(241, 245, 249))

    else:
        # Cozy modern room fallback
        _draw_gradient(draw, width, height, (254, 243, 199), (251, 191, 36))
        # Window
        draw.rectangle(
            [int(width * 0.55), int(height * 0.15), int(width * 0.90), int(height * 0.50)],
            fill=(125, 211, 252),
            outline=(120, 53, 15),
            width=8,
        )
        draw.line(
            [(int(width * 0.725), int(height * 0.15)), (int(width * 0.725), int(height * 0.50))],
            fill=(120, 53, 15),
            width=6,
        )
        floor_y = int(height * 0.62)
        draw.rectangle([0, floor_y, width, height], fill=(146, 64, 14))

    # Convert to manga black-and-white if requested
    if visual_style == "manga_bw":
        img = convert_to_manga_screentone(img)

    return img


# ============================================================================
# Manga Halftone & Ink Lineart Effects
# ============================================================================


def convert_to_manga_screentone(image: Image.Image, dot_spacing: int = 6) -> Image.Image:
    """Convert an RGBA image to high-contrast manga screentone halftone styling."""
    # Convert RGB to grayscale array
    rgb = image.convert("RGB")
    gray_arr = np.array(ImageOps.grayscale(rgb), dtype=np.float32) / 255.0
    h, w = gray_arr.shape

    # Generate 45-degree screentone dot mask using vectorized numpy
    y_coords, x_coords = np.ogrid[:h, :w]
    # Rotate coords by 45 degrees
    u = (x_coords + y_coords) % dot_spacing
    v = (x_coords - y_coords) % dot_spacing
    dist = np.sqrt((u - dot_spacing / 2) ** 2 + (v - dot_spacing / 2) ** 2)
    max_dist = dot_spacing / 1.414
    dot_pattern = 1.0 - (dist / max_dist)

    # Threshold: dark areas get heavy dots, midtones get halftone, highlights stay clean white
    manga_arr = np.where(
        gray_arr < 0.22,
        18,
        np.where(gray_arr > 0.88, 255, np.where(dot_pattern > (1.0 - gray_arr * 0.9), 35, 245)),
    ).astype(np.uint8)

    # Create monochrome image with crisp edges
    res_l = Image.fromarray(manga_arr, mode="L")
    res_rgba = res_l.convert("RGBA")
    return res_rgba


_SPEED_LINES_CACHE: dict[tuple, Image.Image] = {}


def draw_manga_speed_lines(
    width: int,
    height: int,
    center: tuple[int, int] | None = None,
    num_lines: int = 56,
    seed: int = 0,
    color: tuple[int, int, int, int] = (20, 20, 25, 200),
) -> Image.Image:
    """Generate dynamic radial manga speedlines bursting toward center."""
    cx, cy = center or (width // 2, int(height * 0.45))
    cache_key = (width, height, cx, cy, num_lines, seed % 8, color)
    if cache_key in _SPEED_LINES_CACHE:
        return _SPEED_LINES_CACHE[cache_key].copy()

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rng = random.Random(seed % 8)

    max_r = math.hypot(width, height)
    inner_safe_r = min(width, height) * 0.22

    for i in range(num_lines):
        angle = (i / num_lines) * 2 * math.pi + rng.uniform(-0.04, 0.04)
        wedge_width = rng.uniform(0.02, 0.06)
        r_inner = inner_safe_r + rng.uniform(0, 100)

        # Points of the triangular speed wedge
        x0 = cx + math.cos(angle) * r_inner
        y0 = cy + math.sin(angle) * r_inner

        x1 = cx + math.cos(angle - wedge_width) * max_r
        y1 = cy + math.sin(angle - wedge_width) * max_r

        x2 = cx + math.cos(angle + wedge_width) * max_r
        y2 = cy + math.sin(angle + wedge_width) * max_r

        draw.polygon([(x0, y0), (x1, y1), (x2, y2)], fill=color)

    _SPEED_LINES_CACHE[cache_key] = img
    return img.copy()


def draw_horizontal_speed_lines(
    width: int,
    height: int,
    seed: int = 0,
    color: tuple[int, int, int, int] = (255, 255, 255, 180),
) -> Image.Image:
    """Generate horizontal action comic motion lines across screen."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rng = random.Random(seed)

    for _ in range(35):
        y = rng.randint(0, height)
        line_len = rng.randint(int(width * 0.3), int(width * 0.85))
        x_start = rng.randint(-100, int(width * 0.4))
        w = rng.randint(2, 6)
        draw.line([(x_start, y), (x_start + line_len, y)], fill=color, width=w)

    return img


# ============================================================================
# Procedural Anime Characters with Expressive Faces & Poses
# ============================================================================


def draw_anime_character(
    width: int,
    height: int,
    pose: str,
    expression: str,
    visual_style: str = "anime_color",
    time_sec: float = 0.0,
    blink: bool = False,
    breathing_offset: float = 0.0,
) -> Image.Image:
    """Procedurally draw an anime character with expressive facial features and poses."""
    char_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(char_img)

    pose = (pose or "dramatic_heroic").lower().strip()
    expression = (expression or "smug").lower().strip()

    is_bw = visual_style == "manga_bw"

    # Base anime palette
    skin_base = (255, 228, 206) if not is_bw else (255, 255, 255)
    skin_shade = (245, 195, 170) if not is_bw else (225, 225, 225)
    blush_col = (251, 146, 170, 200) if not is_bw else (200, 200, 200, 180)
    hair_col = (30, 41, 59) if not is_bw else (20, 20, 24)
    hair_shine = (96, 165, 250) if not is_bw else (240, 240, 240)
    eye_iris_col = (37, 99, 235) if not is_bw else (30, 30, 30)
    outfit_primary = (30, 58, 138) if not is_bw else (40, 40, 48)
    outfit_secondary = (248, 250, 252) if not is_bw else (240, 240, 240)
    outline_col = (15, 15, 20)

    # Calculate center of character anchor
    cx = width // 2
    # Base chin / face center
    face_y = int(height * 0.46 + breathing_offset)
    head_r = int(min(width, height) * 0.16)

    # Adjust positioning based on pose
    if pose == "hiding_corner":
        cx = int(width * 0.22)
        face_y = int(height * 0.52 + breathing_offset)
    elif pose == "sleeping_desk":
        face_y = int(height * 0.54 + breathing_offset)
    elif pose == "gaming_intense":
        face_y = int(height * 0.44 + breathing_offset)
    elif pose == "screaming_run":
        cx = int(width * 0.58)

    # 1. Body & Torso according to Pose
    torso_top = face_y + int(head_r * 0.75)
    torso_w = int(head_r * 1.6)

    if pose == "dramatic_heroic":
        # Billowing hero cape
        if not is_bw:
            draw.polygon(
                [
                    (cx - torso_w, torso_top + 40),
                    (cx - torso_w * 2, height),
                    (cx + torso_w * 2, height),
                    (cx + torso_w, torso_top + 40),
                ],
                fill=(220, 38, 38),
                outline=outline_col,
                width=4,
            )
        # Chest puffed out
        draw.polygon(
            [
                (cx - torso_w, torso_top),
                (cx + torso_w, torso_top),
                (cx + int(torso_w * 1.2), height),
                (cx - int(torso_w * 1.2), height),
            ],
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )
        # Collar & Golden tie/emblem
        draw.polygon(
            [(cx - 35, torso_top), (cx + 35, torso_top), (cx, torso_top + 50)],
            fill=outfit_secondary,
            outline=outline_col,
            width=3,
        )
        draw.polygon(
            [(cx - 15, torso_top + 45), (cx + 15, torso_top + 45), (cx, torso_top + 130)],
            fill=COLOR_GOLD,
            outline=outline_col,
            width=3,
        )
        # Arm 1: pointing dramatically skyward!
        arm_p1 = (cx - torso_w + 20, torso_top + 30)
        arm_p2 = (cx - int(torso_w * 1.6), torso_top - 60)
        arm_p3 = (cx - int(torso_w * 1.8), torso_top - 180)
        draw.line([arm_p1, arm_p2, arm_p3], fill=outfit_primary, width=32)
        draw.line([arm_p1, arm_p2, arm_p3], fill=outline_col, width=6)
        # Pointing hand finger
        draw.ellipse(
            [arm_p3[0] - 18, arm_p3[1] - 18, arm_p3[0] + 18, arm_p3[1] + 18],
            fill=skin_base,
            outline=outline_col,
            width=4,
        )
        draw.line([(arm_p3[0], arm_p3[1]), (arm_p3[0], arm_p3[1] - 40)], fill=skin_base, width=12)
        draw.line([(arm_p3[0], arm_p3[1]), (arm_p3[0], arm_p3[1] - 40)], fill=outline_col, width=3)
        # Sparkle star at finger tip
        f_star_x, f_star_y = arm_p3[0], arm_p3[1] - 45
        draw.polygon(
            [
                (f_star_x, f_star_y - 25),
                (f_star_x + 8, f_star_y - 8),
                (f_star_x + 25, f_star_y),
                (f_star_x + 8, f_star_y + 8),
                (f_star_x, f_star_y + 25),
                (f_star_x - 8, f_star_y + 8),
                (f_star_x - 25, f_star_y),
                (f_star_x - 8, f_star_y - 8),
            ],
            fill=COLOR_YELLOW,
        )

    elif pose == "smug_crossed_arms":
        # Torso
        draw.polygon(
            [
                (cx - torso_w, torso_top),
                (cx + torso_w, torso_top),
                (cx + torso_w, height),
                (cx - torso_w, height),
            ],
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )
        # White shirt & tie
        draw.polygon(
            [(cx - 30, torso_top), (cx + 30, torso_top), (cx, torso_top + 60)],
            fill=outfit_secondary,
            outline=outline_col,
            width=3,
        )
        draw.polygon(
            [(cx - 12, torso_top + 55), (cx + 12, torso_top + 55), (cx, torso_top + 140)],
            fill=COLOR_CORAL,
            outline=outline_col,
            width=3,
        )
        # Crossed arms block
        crossed_y = torso_top + 90
        draw.rounded_rectangle(
            [cx - int(torso_w * 0.95), crossed_y, cx + int(torso_w * 0.95), crossed_y + 60],
            radius=18,
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )
        # Hand tucked over elbow
        draw.ellipse(
            [cx - int(torso_w * 0.8), crossed_y + 10, cx - int(torso_w * 0.5), crossed_y + 50],
            fill=skin_base,
            outline=outline_col,
            width=3,
        )
        draw.ellipse(
            [cx + int(torso_w * 0.5), crossed_y + 10, cx + int(torso_w * 0.8), crossed_y + 50],
            fill=skin_base,
            outline=outline_col,
            width=3,
        )

    elif pose == "sleeping_desk":
        # Slumped body on wooden school desk
        draw.ellipse(
            [cx - torso_w, torso_top, cx + torso_w, torso_top + 180],
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )
        # Desk in front
        desk_y = torso_top + 110
        draw.rectangle(
            [0, desk_y, width, height], fill=(180, 115, 60), outline=(70, 40, 20), width=6
        )
        # Limp arms spread on desk
        draw.rounded_rectangle(
            [cx - int(torso_w * 1.2), desk_y - 20, cx - 30, desk_y + 35],
            radius=15,
            fill=outfit_primary,
            outline=outline_col,
            width=4,
        )
        draw.ellipse(
            [cx - int(torso_w * 1.3), desk_y - 15, cx - int(torso_w * 1.05), desk_y + 30],
            fill=skin_base,
            outline=outline_col,
            width=3,
        )
        draw.rounded_rectangle(
            [cx + 30, desk_y - 20, cx + int(torso_w * 1.2), desk_y + 35],
            radius=15,
            fill=outfit_primary,
            outline=outline_col,
            width=4,
        )
        draw.ellipse(
            [cx + int(torso_w * 1.05), desk_y - 15, cx + int(torso_w * 1.3), desk_y + 30],
            fill=skin_base,
            outline=outline_col,
            width=3,
        )

    elif pose == "surrounded_books":
        # Crouched under towering book stacks
        draw.polygon(
            [
                (cx - torso_w, torso_top + 30),
                (cx + torso_w, torso_top + 30),
                (cx + torso_w, height),
                (cx - torso_w, height),
            ],
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )
        # Towering stacks of books left and right
        book_colors = [(220, 38, 38), (37, 99, 235), (22, 163, 74), (202, 138, 4), (147, 51, 234)]
        for _side, bx_base in [
            ("left", cx - int(torso_w * 1.6)),
            ("right", cx + int(torso_w * 0.9)),
        ]:
            by = height
            for b_i in range(8):
                bw = 140 + (b_i % 3) * 20
                bh = 35
                by -= bh + 4
                tilt = (b_i % 2) * 12 - 6
                col = book_colors[b_i % len(book_colors)]
                draw.rectangle(
                    [bx_base + tilt, by, bx_base + tilt + bw, by + bh],
                    fill=col,
                    outline=outline_col,
                    width=3,
                )
                # Pages edge
                draw.rectangle(
                    [bx_base + tilt + 10, by + 6, bx_base + tilt + bw - 10, by + bh - 6],
                    fill=(255, 255, 255),
                )
        # Floating test paper with 'F' or '0%'
        draw.polygon(
            [
                (cx - 70, torso_top - 60),
                (cx - 10, torso_top - 75),
                (cx + 10, torso_top - 20),
                (cx - 50, torso_top - 5),
            ],
            fill=(255, 255, 255),
            outline=outline_col,
            width=2,
        )
        f_paper = get_font("bold", 24)
        draw.text((cx - 30, torso_top - 40), "F", font=f_paper, fill=(220, 38, 38))

    elif pose == "gaming_intense":
        # Leaning forward with gamepad controller
        draw.polygon(
            [
                (cx - torso_w, torso_top - 20),
                (cx + torso_w, torso_top - 20),
                (cx + int(torso_w * 1.1), height),
                (cx - int(torso_w * 1.1), height),
            ],
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )
        # Gaming headset over ears
        draw.arc(
            [cx - head_r - 20, face_y - head_r - 25, cx + head_r + 20, face_y + 20],
            180,
            360,
            fill=(40, 40, 50),
            width=18,
        )
        draw.ellipse(
            [cx - head_r - 30, face_y - 30, cx - head_r, face_y + 35],
            fill=(6, 182, 212),
            outline=outline_col,
            width=4,
        )
        draw.ellipse(
            [cx + head_r, face_y - 30, cx + head_r + 30, face_y + 35],
            fill=(6, 182, 212),
            outline=outline_col,
            width=4,
        )
        # Mic boom
        draw.line(
            [(cx - head_r - 10, face_y + 20), (cx - 30, face_y + int(head_r * 0.4))],
            fill=(20, 20, 25),
            width=4,
        )
        # Gamepad controller held in hands
        ctrl_y = torso_top + 100
        draw.rounded_rectangle(
            [cx - 85, ctrl_y, cx + 85, ctrl_y + 60],
            radius=18,
            fill=(30, 41, 59),
            outline=outline_col,
            width=4,
        )
        # D-pad and buttons
        draw.rectangle([cx - 55, ctrl_y + 20, cx - 35, ctrl_y + 40], fill=(200, 200, 210))
        for bx_offset, b_col in [
            (-5, COLOR_YELLOW),
            (15, COLOR_CYAN),
            (35, COLOR_CORAL),
            (15, (34, 197, 94)),
        ]:
            draw.ellipse(
                [cx + 30 + bx_offset, ctrl_y + 18, cx + 46 + bx_offset, ctrl_y + 34], fill=b_col
            )

    elif pose == "hiding_corner":
        # Peeking behind wall edge
        wall_x = int(width * 0.35)
        # Shivering body behind
        draw.polygon(
            [
                (cx - torso_w, torso_top),
                (wall_x + 10, torso_top),
                (wall_x + 10, height),
                (cx - torso_w, height),
            ],
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )
        # The solid wall corner
        draw.rectangle([wall_x, 0, width, height], fill=(60, 65, 75), outline=outline_col, width=6)
        # Trembling hands gripping wall edge
        draw.ellipse(
            [wall_x - 18, face_y + 20, wall_x + 18, face_y + 60],
            fill=skin_base,
            outline=outline_col,
            width=3,
        )
        draw.ellipse(
            [wall_x - 18, face_y + 70, wall_x + 18, face_y + 110],
            fill=skin_base,
            outline=outline_col,
            width=3,
        )
        # Shiver vibration lines
        for sv_y in [face_y - 20, face_y + 40, face_y + 90]:
            draw.arc(
                [wall_x - 45, sv_y, wall_x - 25, sv_y + 20], 90, 270, fill=COLOR_WHITE, width=3
            )

    elif pose == "shocked_facepalm":
        # Standing torso
        draw.polygon(
            [
                (cx - torso_w, torso_top),
                (cx + torso_w, torso_top),
                (cx + torso_w, height),
                (cx - torso_w, height),
            ],
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )
        # Hand slapped firmly over face/forehead
        hand_x = cx + int(head_r * 0.1)
        hand_y = face_y - int(head_r * 0.2)
        # Arm reaching up
        draw.line(
            [
                (cx + int(torso_w * 0.8), torso_top + 60),
                (hand_x + 40, hand_y + 50),
                (hand_x, hand_y),
            ],
            fill=outfit_primary,
            width=30,
        )
        draw.line(
            [
                (cx + int(torso_w * 0.8), torso_top + 60),
                (hand_x + 40, hand_y + 50),
                (hand_x, hand_y),
            ],
            fill=outline_col,
            width=5,
        )
        # Splayed fingers over forehead
        draw.ellipse(
            [hand_x - 30, hand_y - 25, hand_x + 30, hand_y + 35],
            fill=skin_base,
            outline=outline_col,
            width=4,
        )
        for fx_offset in [-18, -6, 8, 20]:
            draw.line(
                [(hand_x + fx_offset, hand_y), (hand_x + fx_offset, hand_y - 35)],
                fill=skin_base,
                width=8,
            )
            draw.line(
                [(hand_x + fx_offset, hand_y), (hand_x + fx_offset, hand_y - 35)],
                fill=outline_col,
                width=3,
            )

    elif pose == "screaming_run":
        # Torso tilted sharply forward in anime run
        draw.polygon(
            [
                (cx - torso_w + 30, torso_top - 30),
                (cx + torso_w, torso_top + 20),
                (cx + torso_w - 60, height),
                (cx - torso_w - 30, height),
            ],
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )
        # Arms thrown back in Naruto run
        draw.line([(cx - 30, torso_top), (cx - 180, torso_top - 60)], fill=outfit_primary, width=28)
        draw.line([(cx - 30, torso_top), (cx - 180, torso_top - 60)], fill=outline_col, width=5)
        draw.ellipse(
            [cx - 200, torso_top - 75, cx - 170, torso_top - 45],
            fill=skin_base,
            outline=outline_col,
            width=3,
        )

    elif pose == "peace_sign":
        # Cheerful cute standing pose
        draw.polygon(
            [
                (cx - torso_w, torso_top),
                (cx + torso_w, torso_top),
                (cx + torso_w, height),
                (cx - torso_w, height),
            ],
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )
        # V-sign peace hand held up next to eye
        v_hand_x = cx + head_r + 25
        v_hand_y = face_y - 15
        # Arm reaching up
        draw.line(
            [(cx + torso_w - 20, torso_top + 50), (v_hand_x, v_hand_y + 40), (v_hand_x, v_hand_y)],
            fill=outfit_primary,
            width=26,
        )
        draw.line(
            [(cx + torso_w - 20, torso_top + 50), (v_hand_x, v_hand_y + 40), (v_hand_x, v_hand_y)],
            fill=outline_col,
            width=4,
        )
        # Palm and two fingers (peace V)
        draw.ellipse(
            [v_hand_x - 18, v_hand_y - 10, v_hand_x + 18, v_hand_y + 25],
            fill=skin_base,
            outline=outline_col,
            width=3,
        )
        # Index finger
        draw.line(
            [(v_hand_x - 8, v_hand_y), (v_hand_x - 22, v_hand_y - 45)], fill=skin_base, width=10
        )
        draw.line(
            [(v_hand_x - 8, v_hand_y), (v_hand_x - 22, v_hand_y - 45)], fill=outline_col, width=3
        )
        # Middle finger
        draw.line(
            [(v_hand_x + 8, v_hand_y), (v_hand_x + 22, v_hand_y - 45)], fill=skin_base, width=10
        )
        draw.line(
            [(v_hand_x + 8, v_hand_y), (v_hand_x + 22, v_hand_y - 45)], fill=outline_col, width=3
        )

    else:
        # Default standing torso
        draw.polygon(
            [
                (cx - torso_w, torso_top),
                (cx + torso_w, torso_top),
                (cx + torso_w, height),
                (cx - torso_w, height),
            ],
            fill=outfit_primary,
            outline=outline_col,
            width=5,
        )

    # 2. Anime Head Base & Chin
    chin_y = face_y + head_r
    left_jaw = (cx - head_r, face_y)
    right_jaw = (cx + head_r, face_y)
    chin_pt = (cx, chin_y)

    # Neck
    draw.polygon(
        [
            (cx - 30, face_y + 20),
            (cx + 30, face_y + 20),
            (cx + 35, torso_top + 10),
            (cx - 35, torso_top + 10),
        ],
        fill=skin_shade,
        outline=outline_col,
        width=3,
    )

    # Head shape (smooth anime jawline)
    draw.polygon(
        [
            left_jaw,
            (cx - int(head_r * 0.7), face_y + int(head_r * 0.7)),
            chin_pt,
            (cx + int(head_r * 0.7), face_y + int(head_r * 0.7)),
            right_jaw,
            (cx + head_r, face_y - head_r),
            (cx - head_r, face_y - head_r),
        ],
        fill=skin_base,
        outline=outline_col,
        width=4,
    )

    # Cute cheek blush
    draw.ellipse([cx - head_r + 20, face_y + 15, cx - head_r + 65, face_y + 40], fill=blush_col)
    draw.ellipse([cx + head_r - 65, face_y + 15, cx + head_r - 20, face_y + 40], fill=blush_col)

    # 3. Expressive Anime Faces & Eyes
    eye_y = face_y - int(head_r * 0.10)
    eye_spacing = int(head_r * 0.48)
    left_eye_cx = cx - eye_spacing
    right_eye_cx = cx + eye_spacing

    if blink:
        # Closed curved anime blinking eyelids
        for ecx in [left_eye_cx, right_eye_cx]:
            draw.arc(
                [ecx - 28, eye_y - 12, ecx + 28, eye_y + 16], 190, 350, fill=outline_col, width=5
            )
            # Eyelashes
            draw.line([(ecx + 25, eye_y), (ecx + 35, eye_y - 10)], fill=outline_col, width=3)

    elif expression == "shocked":
        # Wide circular terror eyes with tiny dot pupils
        for ecx in [left_eye_cx, right_eye_cx]:
            draw.ellipse(
                [ecx - 26, eye_y - 28, ecx + 26, eye_y + 24],
                fill=(255, 255, 255),
                outline=outline_col,
                width=4,
            )
            # Tiny dot pupil
            draw.ellipse([ecx - 5, eye_y - 5, ecx + 5, eye_y + 5], fill=outline_col)
        # High arched shocked eyebrows
        draw.arc(
            [left_eye_cx - 25, eye_y - 50, left_eye_cx + 25, eye_y - 25],
            200,
            340,
            fill=outline_col,
            width=5,
        )
        draw.arc(
            [right_eye_cx - 25, eye_y - 50, right_eye_cx + 25, eye_y - 25],
            200,
            340,
            fill=outline_col,
            width=5,
        )
        # Dropped jaw mouth (huge oval)
        draw.ellipse(
            [cx - 28, face_y + 28, cx + 28, face_y + 80],
            fill=(185, 28, 28),
            outline=outline_col,
            width=4,
        )
        draw.ellipse([cx - 16, face_y + 55, cx + 16, face_y + 78], fill=(251, 113, 133))  # tongue
        # Giant sweat drops at temple
        draw.ellipse(
            [cx - head_r - 20, face_y - 45, cx - head_r + 10, face_y - 10],
            fill=(56, 189, 248),
            outline=outline_col,
            width=3,
        )
        draw.polygon(
            [
                (cx - head_r - 5, face_y - 58),
                (cx - head_r - 18, face_y - 35),
                (cx - head_r + 8, face_y - 35),
            ],
            fill=(56, 189, 248),
        )
        # Blue shock lines down forehead
        for sh_x in range(cx - 50, cx + 60, 20):
            draw.line(
                [(sh_x, face_y - head_r + 20), (sh_x, face_y - 30)], fill=(96, 165, 250), width=3
            )

    elif expression == "smug":
        # Half-lidded confident smirk eyes
        for ecx in [left_eye_cx, right_eye_cx]:
            # Half-closed iris
            draw.ellipse(
                [ecx - 24, eye_y - 15, ecx + 24, eye_y + 22],
                fill=eye_iris_col,
                outline=outline_col,
                width=3,
            )
            draw.ellipse([ecx - 8, eye_y - 5, ecx + 8, eye_y + 12], fill=COLOR_BLACK)
            # Sparkle shine
            draw.ellipse([ecx - 14, eye_y - 8, ecx - 4, eye_y + 2], fill=COLOR_WHITE)
            # Heavy upper anime lash cutting off top
            draw.arc(
                [ecx - 28, eye_y - 18, ecx + 28, eye_y + 12], 190, 350, fill=outline_col, width=6
            )
        # One eyebrow raised high
        draw.line(
            [(left_eye_cx - 25, eye_y - 25), (left_eye_cx + 25, eye_y - 28)],
            fill=outline_col,
            width=4,
        )
        draw.arc(
            [right_eye_cx - 25, eye_y - 48, right_eye_cx + 25, eye_y - 20],
            190,
            350,
            fill=outline_col,
            width=5,
        )
        # Iconic cat mouth ':3' or tilted smirk
        draw.arc([cx - 26, face_y + 32, cx - 2, face_y + 55], 0, 180, fill=outline_col, width=4)
        draw.arc([cx + 2, face_y + 32, cx + 26, face_y + 55], 0, 180, fill=outline_col, width=4)
        # Smug star glint
        glint_x, glint_y = cx + head_r - 20, face_y + 10
        draw.polygon(
            [
                (glint_x, glint_y - 16),
                (glint_x + 5, glint_y - 5),
                (glint_x + 16, glint_y),
                (glint_x + 5, glint_y + 5),
                (glint_x, glint_y + 16),
                (glint_x - 5, glint_y + 5),
                (glint_x - 16, glint_y),
                (glint_x - 5, glint_y - 5),
            ],
            fill=COLOR_YELLOW,
        )

    elif expression == "deadpan":
        # Straight unblinking horizontal dot eyes (Saitama style)
        for ecx in [left_eye_cx, right_eye_cx]:
            draw.ellipse(
                [ecx - 20, eye_y - 10, ecx + 20, eye_y + 10],
                fill=(255, 255, 255),
                outline=outline_col,
                width=3,
            )
            draw.ellipse([ecx - 5, eye_y - 5, ecx + 5, eye_y + 5], fill=outline_col)
        # Flat lowered straight eyebrows
        draw.line(
            [(left_eye_cx - 25, eye_y - 22), (left_eye_cx + 25, eye_y - 22)],
            fill=outline_col,
            width=4,
        )
        draw.line(
            [(right_eye_cx - 25, eye_y - 22), (right_eye_cx + 25, eye_y - 22)],
            fill=outline_col,
            width=4,
        )
        # Flat straight line mouth
        draw.line([(cx - 22, face_y + 45), (cx + 22, face_y + 45)], fill=outline_col, width=4)

    elif expression == "panicked":
        # Trembling wavy spiral eyes
        for ecx in [left_eye_cx, right_eye_cx]:
            draw.ellipse(
                [ecx - 22, eye_y - 22, ecx + 22, eye_y + 22],
                fill=(255, 255, 255),
                outline=outline_col,
                width=3,
            )
            # Spiral pupils
            draw.arc(
                [ecx - 15, eye_y - 15, ecx + 15, eye_y + 15], 0, 270, fill=outline_col, width=3
            )
            draw.arc([ecx - 8, eye_y - 8, ecx + 8, eye_y + 8], 90, 360, fill=outline_col, width=3)
        # Angled distress eyebrows
        draw.line(
            [(left_eye_cx - 25, eye_y - 25), (left_eye_cx + 25, eye_y - 38)],
            fill=outline_col,
            width=4,
        )
        draw.line(
            [(right_eye_cx - 25, eye_y - 38), (right_eye_cx + 25, eye_y - 25)],
            fill=outline_col,
            width=4,
        )
        # Wavy squiggly zigzag mouth
        mouth_pts = [
            (cx - 25, face_y + 45),
            (cx - 15, face_y + 52),
            (cx - 5, face_y + 40),
            (cx + 5, face_y + 52),
            (cx + 15, face_y + 40),
            (cx + 25, face_y + 48),
        ]
        draw.line(mouth_pts, fill=outline_col, width=4)
        # Speed sweat drops spraying
        for s_off in [-40, 40]:
            draw.ellipse(
                [cx + s_off - 10, face_y - 50, cx + s_off + 10, face_y - 30],
                fill=(56, 189, 248),
                outline=outline_col,
                width=2,
            )

    elif expression == "screaming":
        # Eyes squeezed shut into sharp < > brackets
        # Left eye '<'
        draw.line(
            [(left_eye_cx - 24, eye_y - 18), (left_eye_cx + 15, eye_y)], fill=outline_col, width=6
        )
        draw.line(
            [(left_eye_cx - 24, eye_y + 18), (left_eye_cx + 15, eye_y)], fill=outline_col, width=6
        )
        # Right eye '>'
        draw.line(
            [(right_eye_cx + 24, eye_y - 18), (right_eye_cx - 15, eye_y)], fill=outline_col, width=6
        )
        draw.line(
            [(right_eye_cx + 24, eye_y + 18), (right_eye_cx - 15, eye_y)], fill=outline_col, width=6
        )
        # Gigantic wide open mouth with shark teeth or big hollow
        m_top = face_y + 20
        m_bot = face_y + 85
        draw.polygon(
            [(cx - 45, m_top), (cx + 45, m_top), (cx + 35, m_bot), (cx - 35, m_bot)],
            fill=(127, 29, 29),
            outline=outline_col,
            width=5,
        )
        # Sharp zigzag shark teeth on top and bottom
        teeth_top = [
            (cx - 40, m_top),
            (cx - 25, m_top + 16),
            (cx - 10, m_top),
            (cx + 5, m_top + 16),
            (cx + 20, m_top),
            (cx + 35, m_top + 16),
            (cx + 40, m_top),
        ]
        draw.polygon(teeth_top, fill=(255, 255, 255), outline=outline_col, width=2)
        # Big red tongue
        draw.ellipse([cx - 22, m_bot - 30, cx + 22, m_bot], fill=(244, 63, 94))
        # Giant anime tears streaming sideways
        draw.polygon(
            [
                (left_eye_cx - 20, eye_y),
                (left_eye_cx - 120, eye_y - 35),
                (left_eye_cx - 140, eye_y + 40),
            ],
            fill=(56, 189, 248, 200),
            outline=outline_col,
            width=3,
        )
        draw.polygon(
            [
                (right_eye_cx + 20, eye_y),
                (right_eye_cx + 120, eye_y - 35),
                (right_eye_cx + 140, eye_y + 40),
            ],
            fill=(56, 189, 248, 200),
            outline=outline_col,
            width=3,
        )

    elif expression == "sleeping":
        # Peaceful closed curved eyelids
        for ecx in [left_eye_cx, right_eye_cx]:
            draw.arc(
                [ecx - 22, eye_y - 10, ecx + 22, eye_y + 15], 190, 350, fill=outline_col, width=5
            )
        # Gentle open mouth
        draw.ellipse(
            [cx - 10, face_y + 40, cx + 10, face_y + 55],
            fill=(244, 114, 182),
            outline=outline_col,
            width=3,
        )
        # Expanding giant translucent snot bubble from nostril
        sb_r = 38 + int(math.sin(time_sec * 4.0) * 8)
        sb_cx, sb_cy = cx + 25, face_y + 30
        draw.ellipse(
            [sb_cx - sb_r, sb_cy - sb_r, sb_cx + sb_r, sb_cy + sb_r],
            fill=(186, 230, 253, 160),
            outline=(56, 189, 248),
            width=3,
        )
        draw.ellipse(
            [
                sb_cx - int(sb_r * 0.6),
                sb_cy - int(sb_r * 0.6),
                sb_cx - int(sb_r * 0.2),
                sb_cy - int(sb_r * 0.2),
            ],
            fill=(255, 255, 255, 220),
        )
        # Floating 'Z z z'
        f_zzz = get_font("bold", 32)
        draw.text((sb_cx + sb_r + 20, sb_cy - 40), "Z", font=f_zzz, fill=COLOR_CYAN)
        draw.text((sb_cx + sb_r + 45, sb_cy - 75), "z", font=get_font("bold", 24), fill=COLOR_CYAN)
        draw.text((sb_cx + sb_r + 65, sb_cy - 105), "z", font=get_font("bold", 18), fill=COLOR_CYAN)

    elif expression == "crying_laughing":
        # Joyful squinting closed eyes ^ ^
        draw.line(
            [
                (left_eye_cx - 25, eye_y + 10),
                (left_eye_cx, eye_y - 12),
                (left_eye_cx + 25, eye_y + 10),
            ],
            fill=outline_col,
            width=6,
        )
        draw.line(
            [
                (right_eye_cx - 25, eye_y + 10),
                (right_eye_cx, eye_y - 12),
                (right_eye_cx + 25, eye_y + 10),
            ],
            fill=outline_col,
            width=6,
        )
        # Giant open laughing grin
        draw.polygon(
            [(cx - 38, face_y + 30), (cx + 38, face_y + 30), (cx, face_y + 75)],
            fill=(185, 28, 28),
            outline=outline_col,
            width=4,
        )
        draw.rectangle(
            [cx - 30, face_y + 30, cx + 30, face_y + 42], fill=(255, 255, 255)
        )  # teeth row
        # Streaming twin geyser tears pouring down
        draw.polygon(
            [
                (left_eye_cx - 15, eye_y),
                (left_eye_cx + 15, eye_y),
                (left_eye_cx + 25, chin_y + 80),
                (left_eye_cx - 25, chin_y + 80),
            ],
            fill=(56, 189, 248, 190),
            outline=outline_col,
            width=3,
        )
        draw.polygon(
            [
                (right_eye_cx - 15, eye_y),
                (right_eye_cx + 15, eye_y),
                (right_eye_cx + 25, chin_y + 80),
                (right_eye_cx - 25, chin_y + 80),
            ],
            fill=(56, 189, 248, 190),
            outline=outline_col,
            width=3,
        )

    elif expression == "sparkling_eyes":
        # Giant dazzling anime eyes with star pupils & blushing cheeks
        for ecx in [left_eye_cx, right_eye_cx]:
            # Big round eye
            draw.ellipse(
                [ecx - 28, eye_y - 25, ecx + 28, eye_y + 25],
                fill=(255, 255, 255),
                outline=outline_col,
                width=4,
            )
            # Vibrant gradient iris
            draw.ellipse([ecx - 22, eye_y - 20, ecx + 22, eye_y + 20], fill=eye_iris_col)
            # Four-pointed golden star pupil!
            st_r = 14
            draw.polygon(
                [
                    (ecx, eye_y - st_r),
                    (ecx + 4, eye_y - 4),
                    (ecx + st_r, eye_y),
                    (ecx + 4, eye_y + 4),
                    (ecx, eye_y + st_r),
                    (ecx - 4, eye_y + 4),
                    (ecx - st_r, eye_y),
                    (ecx - 4, eye_y - 4),
                ],
                fill=COLOR_YELLOW,
            )
            # Sparkle circles
            draw.ellipse([ecx - 14, eye_y - 16, ecx - 4, eye_y - 6], fill=(255, 255, 255))
            draw.ellipse([ecx + 6, eye_y + 6, ecx + 14, eye_y + 14], fill=(255, 255, 255))
            # Long anime lash
            draw.arc([ecx - 30, eye_y - 28, ecx + 30, eye_y], 190, 350, fill=outline_col, width=6)
        # Happy triangular beaming smile
        draw.polygon(
            [(cx - 25, face_y + 35), (cx + 25, face_y + 35), (cx, face_y + 65)],
            fill=(225, 29, 72),
            outline=outline_col,
            width=4,
        )
        draw.rectangle([cx - 20, face_y + 35, cx + 20, face_y + 44], fill=(255, 255, 255))
        # Extra rosy blush hatching
        for bx_h in [-40, 40]:
            draw.line(
                [(cx + bx_h - 15, face_y + 20), (cx + bx_h - 5, face_y + 35)],
                fill=(244, 63, 94),
                width=3,
            )
            draw.line(
                [(cx + bx_h - 5, face_y + 20), (cx + bx_h + 5, face_y + 35)],
                fill=(244, 63, 94),
                width=3,
            )

    else:
        # Standard cheerful anime face
        for ecx in [left_eye_cx, right_eye_cx]:
            draw.ellipse(
                [ecx - 22, eye_y - 20, ecx + 22, eye_y + 20],
                fill=eye_iris_col,
                outline=outline_col,
                width=3,
            )
            draw.ellipse([ecx - 10, eye_y - 12, ecx - 2, eye_y - 4], fill=(255, 255, 255))
            draw.arc(
                [ecx - 24, eye_y - 22, ecx + 24, eye_y + 6], 190, 350, fill=outline_col, width=5
            )
        draw.arc([cx - 20, face_y + 30, cx + 20, face_y + 55], 0, 180, fill=outline_col, width=4)

    # 4. Stylized Anime Hair (Bangs & Locks)
    hair_top = face_y - int(head_r * 1.05)
    # Hair back dome
    draw.ellipse(
        [cx - head_r - 25, hair_top - 20, cx + head_r + 25, face_y + int(head_r * 0.4)],
        fill=hair_col,
        outline=outline_col,
        width=5,
    )
    # Side locks
    draw.polygon(
        [
            (cx - head_r - 20, face_y - 20),
            (cx - head_r + 10, face_y + int(head_r * 0.8)),
            (cx - head_r - 35, face_y + int(head_r * 0.9)),
        ],
        fill=hair_col,
        outline=outline_col,
        width=4,
    )
    draw.polygon(
        [
            (cx + head_r + 20, face_y - 20),
            (cx + head_r - 10, face_y + int(head_r * 0.8)),
            (cx + head_r + 35, face_y + int(head_r * 0.9)),
        ],
        fill=hair_col,
        outline=outline_col,
        width=4,
    )
    # Spiky layered forehead bangs
    bang_spikes = [
        (cx - 75, hair_top + 10, cx - 40, face_y - 15),
        (cx - 45, hair_top, cx - 15, face_y - 5),
        (cx - 20, hair_top, cx + 15, face_y - 8),
        (cx + 10, hair_top, cx + 45, face_y - 15),
        (cx + 40, hair_top + 10, cx + 75, face_y - 20),
    ]
    for b_x1, b_y1, b_x2, b_y2 in bang_spikes:
        draw.polygon(
            [(b_x1, b_y1), ((b_x1 + b_x2) // 2 + 10, b_y2), (b_x2, b_y1 + 15)],
            fill=hair_col,
            outline=outline_col,
            width=3,
        )

    # Shiny hair highlight band (anime sheen)
    sheen_y = hair_top + 30
    draw.arc(
        [cx - head_r + 10, sheen_y - 15, cx + head_r - 10, sheen_y + 15],
        200,
        340,
        fill=hair_shine,
        width=8,
    )

    # If manga style, apply screentone
    if is_bw:
        char_img = convert_to_manga_screentone(char_img)

    return char_img


# ============================================================================
# Speech Bubbles, Heading Banners & Subtitles
# ============================================================================


def draw_speech_bubble(
    base_img: Image.Image,
    text: str,
    anchor: tuple[int, int],
    mouth_pos: tuple[int, int],
    visual_style: str = "anime_color",
    is_screaming: bool = False,
) -> None:
    """Draw a vibrant comic speech bubble with pointer tail toward character's mouth."""
    if not text.strip():
        return

    draw = ImageDraw.Draw(base_img)
    bx, by = anchor
    mx, my = mouth_pos

    # Determine font size and calculate bubble dimensions
    font_size = 32 if base_img.width > 1200 else 26
    font = get_font("bold", font_size)

    # Wrap text into lines of ~20-25 chars
    words = text.split()
    lines: list[str] = []
    curr = ""
    for w in words:
        if len(curr) + len(w) + 1 <= 22:
            curr = f"{curr} {w}".strip()
        else:
            if curr:
                lines.append(curr)
            curr = w
    if curr:
        lines.append(curr)

    line_h = int(font_size * 1.3)
    bubble_h = len(lines) * line_h + 40
    max_line_len = max(len(ln) for ln in lines) if lines else 10
    bubble_w = max(240, int(max_line_len * font_size * 0.65) + 60)

    # Keep bubble within canvas margins
    x1 = max(30, min(base_img.width - bubble_w - 30, bx - bubble_w // 2))
    y1 = max(140, min(base_img.height - bubble_h - 250, by - bubble_h // 2))
    x2 = x1 + bubble_w
    y2 = y1 + bubble_h

    # Pointer tail points toward character mouth
    tail_base_x = (x1 + x2) // 2
    tail_base_y = y2

    # Draw pointer tail
    draw.polygon(
        [(tail_base_x - 20, tail_base_y - 2), (tail_base_x + 20, tail_base_y - 2), (mx, my - 20)],
        fill=(255, 255, 255),
        outline=(15, 15, 20),
        width=4,
    )

    # Draw bubble body
    if is_screaming:
        # Spiky burst dialogue balloon for screams
        spikes = 16
        pts: list[tuple[float, float]] = []
        cx_b, cy_b = (x1 + x2) / 2, (y1 + y2) / 2
        rx, ry = bubble_w / 2, bubble_h / 2
        for s in range(spikes * 2):
            ang = (s / (spikes * 2)) * 2 * math.pi
            r_scale = 1.15 if s % 2 == 1 else 0.85
            pts.append((cx_b + math.cos(ang) * rx * r_scale, cy_b + math.sin(ang) * ry * r_scale))
        draw.polygon(pts, fill=(255, 255, 255), outline=(15, 15, 20), width=5)
    else:
        # Rounded comic balloon
        draw.rounded_rectangle(
            [x1, y1, x2, y2], radius=24, fill=(255, 255, 255), outline=(15, 15, 20), width=5
        )

    # Draw inside text
    ty = y1 + 20
    for ln in lines:
        draw_text_with_outline(
            draw,
            ((x1 + x2) // 2, ty + line_h // 2),
            ln,
            font=font,
            fill=(15, 15, 20),
            outline_color=(255, 255, 255),
            outline_width=2,
            anchor="mm",
        )
        ty += line_h


def draw_section_heading(
    base_img: Image.Image,
    heading: str,
    mode: str = "country_meme",
    visual_style: str = "anime_color",
) -> None:
    """Draw prominent top banner for Country name (with flag accents) or Type Name pill."""
    if not heading.strip():
        return

    draw = ImageDraw.Draw(base_img)
    width, height = base_img.size

    banner_w = int(width * 0.88)
    banner_h = int(height * 0.075)
    bx1 = (width - banner_w) // 2
    by1 = int(height * 0.05)
    bx2 = bx1 + banner_w
    by2 = by1 + banner_h

    # Background badge
    badge_bg = (15, 23, 42, 240) if visual_style != "manga_bw" else (20, 20, 24, 245)
    badge_border = COLOR_YELLOW if mode == "country_meme" else COLOR_CYAN
    if visual_style == "manga_bw":
        badge_border = (255, 255, 255)

    # 3D drop shadow
    draw.rounded_rectangle([bx1 + 6, by1 + 6, bx2 + 6, by2 + 6], radius=22, fill=(0, 0, 0, 160))
    # Main pill
    draw.rounded_rectangle(
        [bx1, by1, bx2, by2], radius=22, fill=badge_bg, outline=badge_border, width=5
    )

    # Country mode accent corner stripes
    if mode == "country_meme" and visual_style != "manga_bw":
        draw.polygon(
            [(bx1 + 10, by1 + 6), (bx1 + 35, by1 + 6), (bx1 + 6, by1 + 35), (bx1 + 6, by1 + 10)],
            fill=COLOR_CORAL,
        )
        draw.polygon(
            [(bx2 - 35, by2 - 6), (bx2 - 10, by2 - 6), (bx2 - 6, by2 - 10), (bx2 - 6, by2 - 35)],
            fill=COLOR_CYAN,
        )

    # Font sizing
    font_size = max(28, int(banner_h * 0.52))
    font = get_font("impact", font_size)

    # Render Heading Text
    text_color = COLOR_WHITE
    draw_text_with_outline(
        draw,
        (width // 2, (by1 + by2) // 2),
        heading.upper(),
        font=font,
        fill=text_color,
        outline_color=(0, 0, 0),
        outline_width=4,
        anchor="mm",
    )


def draw_subtitles(
    base_img: Image.Image,
    subtitle_text: str,
    visual_style: str = "anime_color",
) -> None:
    """Draw bold, high-contrast subtitles within mobile safe zones with keyword highlights."""
    if not subtitle_text.strip():
        return

    draw = ImageDraw.Draw(base_img)
    width, height = base_img.size

    # Position at lower third: ~78-82% of height
    sub_y = int(height * 0.80)
    font_size = max(28, int(width * 0.048))
    font = get_font("impact", font_size)

    # Word wrap subtitles cleanly
    words = subtitle_text.split()
    lines: list[list[str]] = []
    curr_line: list[str] = []
    curr_len = 0
    max_chars = 24 if width < height else 45

    for w in words:
        if curr_len + len(w) + 1 <= max_chars:
            curr_line.append(w)
            curr_len += len(w) + 1
        else:
            if curr_line:
                lines.append(curr_line)
            curr_line = [w]
            curr_len = len(w)
    if curr_line:
        lines.append(curr_line)

    line_spacing = int(font_size * 1.35)
    total_sub_h = len(lines) * line_spacing
    start_y = sub_y - total_sub_h // 2

    # Draw dark backing container for max contrast readability
    bg_w = int(width * 0.92)
    bg_x1 = (width - bg_w) // 2
    bg_y1 = start_y - 20
    bg_x2 = bg_x1 + bg_w
    bg_y2 = start_y + total_sub_h + 10
    draw.rounded_rectangle([bg_x1, bg_y1, bg_x2, bg_y2], radius=18, fill=(0, 0, 0, 195))

    # Keyword emphasis highlight colors
    accent_words = {
        "never",
        "always",
        "every",
        "literally",
        "nobody",
        "when",
        "be",
        "like",
        "god",
        "bro",
        "pro",
        "sleep",
        "gaming",
        "exam",
        "dead",
        "shock",
        "food",
        "run",
    }

    for line_idx, line_words in enumerate(lines):
        line_str = " ".join(line_words)
        curr_y = start_y + line_idx * line_spacing + line_spacing // 2

        # Compute word positions for color highlighting
        line_bbox = draw.textbbox((0, 0), line_str, font=font)
        total_w = line_bbox[2] - line_bbox[0]
        curr_x = (width - total_w) // 2

        for w in line_words:
            w_bbox = draw.textbbox((0, 0), w + " ", font=font)
            w_w = w_bbox[2] - w_bbox[0]

            clean_w = "".join(ch for ch in w.lower() if ch.isalnum())
            if clean_w in accent_words or (len(clean_w) > 6 and line_idx == len(lines) - 1):
                word_fill = COLOR_YELLOW if visual_style != "manga_bw" else (255, 255, 255)
            else:
                word_fill = COLOR_WHITE

            draw_text_with_outline(
                draw,
                (curr_x + w_w // 2, curr_y),
                w,
                font=font,
                fill=word_fill,
                outline_color=(0, 0, 0),
                outline_width=4,
                anchor="mm",
            )
            curr_x += w_w


# ============================================================================
# Dynamic Animation & Camera Effects Engine
# ============================================================================


def render_meme_frame(
    segment: MemeSegment,
    frame_idx: int,
    total_frames: int,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    visual_style: str = "anime_color",
    mode: str = "country_meme",
    heading_text: str | None = None,
    subtitle_override: str | None = None,
) -> Image.Image:
    """Render a single animated meme video frame with camera motion and visual FX."""
    total_frames = max(1, total_frames)
    t = frame_idx / max(1, total_frames - 1)  # Normalized progress 0.0 -> 1.0
    time_sec = frame_idx / max(1, fps)

    # 1. Periodic blinking logic (blink at 1.4s-1.6s and 3.4s-3.6s)
    blink_cycle = time_sec % 3.0
    is_blinking = 1.35 <= blink_cycle <= 1.55

    # 2. Subtle breathing bob
    breathing_offset = math.sin(time_sec * 2 * math.pi * 0.8) * 6.0

    # 3. Base Background layer
    bg_img = draw_background(width, height, segment.background_theme, visual_style, time_sec)

    # 4. Character layer
    char_img = draw_anime_character(
        width,
        height,
        segment.pose,
        segment.expression,
        visual_style,
        time_sec=time_sec,
        blink=is_blinking,
        breathing_offset=breathing_offset,
    )

    # 5. Composite Base Scene
    scene = Image.alpha_composite(bg_img, char_img)

    # 6. Camera Motion Effects
    anim = (segment.animation_type or "zoom_in").lower().strip()
    scale = 1.0
    dx, dy = 0.0, 0.0

    if anim == "zoom_in":
        # Smooth push-in zoom on character
        scale = 1.0 + 0.22 * t
    elif anim == "zoom_out":
        # Pull-out reveal
        scale = 1.25 - 0.25 * t
    elif anim == "shake" or segment.expression in ("shocked", "screaming"):
        # Camera shake / vibration for shock or loud punchlines
        shake_mag = max(0.0, 1.0 - t * 1.5) * 22.0
        dx = math.sin(time_sec * 45.0) * shake_mag
        dy = math.cos(time_sec * 38.0) * shake_mag
    elif anim == "pan_left":
        dx = width * 0.08 * (0.5 - t)
    elif anim == "pan_right":
        dx = width * 0.08 * (t - 0.5)
    elif anim == "parallax":
        # Foreground character moves slightly relative to background
        scale = 1.0 + 0.08 * math.sin(time_sec * 2.0)
        dx = math.sin(time_sec * 2.0) * 15.0

    # Apply scale / translation if not identity
    if abs(scale - 1.0) > 0.001 or abs(dx) > 0.5 or abs(dy) > 0.5:
        target_w = int(width * scale)
        target_h = int(height * scale)
        scaled_scene = scene.resize((target_w, target_h), Image.Resampling.BILINEAR)

        # Center crop back to (width, height)
        crop_cx = target_w / 2 - dx
        crop_cy = target_h / 2 - dy
        crop_x1 = max(0, min(target_w - width, int(crop_cx - width / 2)))
        crop_y1 = max(0, min(target_h - height, int(crop_cy - height / 2)))
        scene = scaled_scene.crop((crop_x1, crop_y1, crop_x1 + width, crop_y1 + height))
        if scene.size != (width, height):
            scene = scene.resize((width, height), Image.Resampling.BILINEAR)

    # 7. Manga Speed Lines Overlay
    if anim == "manga_speed_lines" or segment.expression in ("screaming", "shocked"):
        speed_color = (20, 20, 25, 170) if visual_style == "manga_bw" else (255, 255, 255, 140)
        speed_overlay = draw_manga_speed_lines(
            width,
            height,
            center=(width // 2, int(height * 0.46)),
            seed=frame_idx % 8,
            color=speed_color,
        )
        scene = Image.alpha_composite(scene, speed_overlay)

    # 8. Impact Frames (B/W high-contrast inversion for 2-3 frames on impact/punchlines)
    if anim == "impact_pop" and frame_idx in (0, 1, 2):
        # Invert colors for anime impact pop
        rgb_part = scene.convert("RGB")
        inverted = ImageOps.invert(rgb_part)
        scene = inverted.convert("RGBA")

    # 9. Screen Flash (Fading white transition on frame start)
    if frame_idx < 4:
        flash_alpha = int(220 * (1.0 - frame_idx / 4.0))
        flash_layer = Image.new("RGBA", (width, height), (255, 255, 255, flash_alpha))
        scene = Image.alpha_composite(scene, flash_layer)

    # 10. Comic Speech Bubble (if speech_bubble_text present)
    bubble_text = segment.speech_bubble_text.strip()
    if bubble_text:
        char_mouth = (width // 2, int(height * 0.48 + breathing_offset))
        bubble_anchor = (width // 2, int(height * 0.26))
        is_scream = segment.expression in ("screaming", "panicked", "shocked")
        draw_speech_bubble(
            scene, bubble_text, bubble_anchor, char_mouth, visual_style, is_screaming=is_scream
        )

    # 11. Prominent Section Heading Banner
    heading = heading_text or segment.heading or segment.name
    if heading:
        draw_section_heading(scene, heading, mode=mode, visual_style=visual_style)

    # 12. Bottom Subtitles
    sub_text = subtitle_override or segment.subtitle_text or segment.narration
    if sub_text:
        draw_subtitles(scene, sub_text, visual_style=visual_style)

    return scene.convert("RGB")


def generate_segment_frames(
    segment: MemeSegment,
    duration_sec: float,
    fps: int = 30,
    aspect_ratio: str = "9:16",
    visual_style: str = "anime_color",
    mode: str = "country_meme",
) -> Iterator[Image.Image]:
    """Generator yielding all rendered frames for a single meme segment."""
    width, height = get_resolution(aspect_ratio)
    total_frames = max(1, int(round(duration_sec * fps)))

    for frame_idx in range(total_frames):
        yield render_meme_frame(
            segment=segment,
            frame_idx=frame_idx,
            total_frames=total_frames,
            fps=fps,
            width=width,
            height=height,
            visual_style=visual_style,
            mode=mode,
        )


def render_all_meme_frames(
    script: MemeScript,
    audio_segments: Sequence[Any] | None,
    out_dir: Path,
    aspect_ratio: str = "9:16",
    visual_style: str = "anime_color",
    fps: int = 30,
) -> list[Path]:
    """Render all frames for the complete meme script and save sequentially to out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    width, height = get_resolution(aspect_ratio)
    rendered_paths: list[Path] = []
    global_frame_idx = 0

    # Determine segments & durations
    # Construct sequence: Hook/Intro (if applicable), Segments, Ending
    sequence_items: list[tuple[MemeSegment, float, str, str]] = []

    # Map audio segments if provided
    audio_durations: list[float] = []
    if audio_segments:
        for sa in audio_segments:
            if hasattr(sa, "duration"):
                audio_durations.append(float(sa.duration))
            elif isinstance(sa, dict) and "duration" in sa:
                audio_durations.append(float(sa["duration"]))
            elif isinstance(sa, (int, float)):
                audio_durations.append(float(sa))

    # Fallback duration per segment if audio durations aren't provided
    default_seg_dur = max(3.5, script.estimated_duration / max(1, len(script.segments) + 2))

    # 1. Hook / Intro segment
    if script.hook or script.intro:
        hook_dur = audio_durations[0] if audio_durations else 3.5
        intro_seg = MemeSegment(
            id="intro",
            name="INTRO",
            heading=script.topic or script.title,
            narration=f"{script.hook} {script.intro}".strip(),
            scenario="Opening video hook",
            character_description="Excited host",
            pose="dramatic_heroic",
            expression="sparkling_eyes",
            background_theme=script.segments[0].background_theme
            if script.segments
            else "classroom",
            animation_type="zoom_in",
            music_mood="playful",
            sfx_cue="whoosh",
            subtitle_text=f"{script.hook} {script.intro}".strip(),
            speech_bubble_text=script.hook or "ARE YOU READY?!",
        )
        sequence_items.append(
            (
                intro_seg,
                hook_dur,
                script.topic or script.title,
                f"{script.hook} {script.intro}".strip(),
            )
        )

    # 2. Main Segments
    for i, seg in enumerate(script.segments):
        audio_idx = (i + 1) if (script.hook or script.intro) else i
        dur = audio_durations[audio_idx] if audio_idx < len(audio_durations) else default_seg_dur
        sequence_items.append(
            (seg, dur, seg.heading or seg.name, seg.subtitle_text or seg.narration)
        )

    # 3. Ending Segment
    if script.ending:
        ending_dur = audio_durations[-1] if len(audio_durations) > len(sequence_items) else 4.0
        ending_seg = MemeSegment(
            id="ending",
            name="WHICH ONE ARE YOU?",
            heading="WHICH ONE ARE YOU?",
            narration=script.ending,
            scenario="Video closing & engagement CTA",
            character_description="Laughing character",
            pose="peace_sign",
            expression="crying_laughing",
            background_theme=script.segments[-1].background_theme
            if script.segments
            else "classroom",
            animation_type="zoom_out",
            music_mood="playful",
            sfx_cue="cartoon_impact",
            subtitle_text=script.ending,
            speech_bubble_text="DROP A COMMENT!",
        )
        sequence_items.append((ending_seg, ending_dur, "DROP A COMMENT!", script.ending))

    # Render all frames sequentially
    for seg, dur, heading, sub_text in sequence_items:
        seg_frames = max(1, int(round(dur * fps)))
        for f_i in range(seg_frames):
            frame_img = render_meme_frame(
                segment=seg,
                frame_idx=f_i,
                total_frames=seg_frames,
                fps=fps,
                width=width,
                height=height,
                visual_style=visual_style,
                mode=script.mode,
                heading_text=heading,
                subtitle_override=sub_text,
            )
            frame_path = out_dir / f"frame_{global_frame_idx:06d}.png"
            frame_img.save(frame_path, format="PNG")
            rendered_paths.append(frame_path)
            global_frame_idx += 1

    return rendered_paths


def stream_all_meme_frames(
    script: MemeScript,
    audio_segments: Sequence[Any] | None,
    aspect_ratio: str = "9:16",
    visual_style: str = "anime_color",
    fps: int = 30,
) -> Iterator[bytes]:
    """Yield raw rgb24 bytes for each frame sequentially directly to an FFmpeg pipe."""
    width, height = get_resolution(aspect_ratio)
    sequence_items: list[tuple[MemeSegment, float, str, str]] = []

    audio_durations: list[float] = []
    if audio_segments:
        for sa in audio_segments:
            if hasattr(sa, "duration"):
                audio_durations.append(float(sa.duration))
            elif isinstance(sa, dict) and "duration" in sa:
                audio_durations.append(float(sa["duration"]))
            elif isinstance(sa, (int, float)):
                audio_durations.append(float(sa))

    default_seg_dur = max(3.5, script.estimated_duration / max(1, len(script.segments) + 2))

    # 1. Hook / Intro segment
    if script.hook or script.intro:
        hook_dur = audio_durations[0] if audio_durations else 3.5
        intro_seg = MemeSegment(
            id="intro",
            name="INTRO",
            heading=script.topic or script.title,
            narration=f"{script.hook} {script.intro}".strip(),
            scenario="Opening video hook",
            character_description="Excited host",
            pose="dramatic_heroic",
            expression="sparkling_eyes",
            background_theme=script.segments[0].background_theme
            if script.segments
            else "classroom",
            animation_type="zoom_in",
            music_mood="playful",
            sfx_cue="whoosh",
            subtitle_text=f"{script.hook} {script.intro}".strip(),
            speech_bubble_text=script.hook or "ARE YOU READY?!",
        )
        sequence_items.append(
            (
                intro_seg,
                hook_dur,
                script.topic or script.title,
                f"{script.hook} {script.intro}".strip(),
            )
        )

    # 2. Main Segments
    for i, seg in enumerate(script.segments):
        audio_idx = (i + 1) if (script.hook or script.intro) else i
        dur = audio_durations[audio_idx] if audio_idx < len(audio_durations) else default_seg_dur
        sequence_items.append(
            (seg, dur, seg.heading or seg.name, seg.subtitle_text or seg.narration)
        )

    # 3. Ending Segment
    if script.ending:
        ending_dur = audio_durations[-1] if len(audio_durations) > len(sequence_items) else 4.0
        ending_seg = MemeSegment(
            id="ending",
            name="WHICH ONE ARE YOU?",
            heading="WHICH ONE ARE YOU?",
            narration=script.ending,
            scenario="Video closing & engagement CTA",
            character_description="Laughing character",
            pose="peace_sign",
            expression="crying_laughing",
            background_theme=script.segments[-1].background_theme
            if script.segments
            else "classroom",
            animation_type="zoom_out",
            music_mood="playful",
            sfx_cue="cartoon_impact",
            subtitle_text=script.ending,
            speech_bubble_text="DROP A COMMENT!",
        )
        sequence_items.append((ending_seg, ending_dur, "DROP A COMMENT!", script.ending))

    for seg, dur, heading, sub_text in sequence_items:
        seg_frames = max(1, int(round(dur * fps)))
        for f_i in range(seg_frames):
            frame_img = render_meme_frame(
                segment=seg,
                frame_idx=f_i,
                total_frames=seg_frames,
                fps=fps,
                width=width,
                height=height,
                visual_style=visual_style,
                mode=script.mode,
                heading_text=heading,
                subtitle_override=sub_text,
            )
            yield frame_img.tobytes()
