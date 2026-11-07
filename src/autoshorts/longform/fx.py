"""Pure Pillow helpers for the cinematic look of the long-form renderer.

All helpers are deterministic for a given input (time, seed, size) so frames can be
re-rendered identically. Canvas size is 1920x1080 RGB.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

Color = tuple[int, int, int] | tuple[int, int, int, int]

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


def vignette(img: Image.Image, strength: float = 0.5) -> Image.Image:
    """Darken the edges of ``img`` in place and return it."""
    draw_vignette(img, strength=strength)
    return img


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


def floating_dust(
    img: Image.Image,
    t: float,
    count: int = 24,
    seed: int = 7,
) -> Image.Image:
    """Draw small pale dust motes drifting slowly upward across ``img`` in place."""
    draw_dust_motes(ImageDraw.Draw(img), t, (0, 0, img.width, img.height), count=count, seed=seed)
    return img


@lru_cache(maxsize=16)
def _grain_tile(w: int, h: int, seed: int) -> Image.Image:
    rng = random.Random(seed)
    data = bytes(rng.getrandbits(8) for _ in range(w * h))
    return Image.frombytes("L", (w, h), data)


def film_grain(
    img: Image.Image,
    intensity: float = 0.04,
    seed: int = 42,
) -> Image.Image:
    """Add subtle deterministic film grain to ``img`` in place and return it."""
    if intensity <= 0.0:
        return img
    w, h = img.size
    tile = _grain_tile(min(w, 256), min(h, 256), seed)
    grain = tile.resize((w, h), Image.Resampling.NEAREST)
    grain_rgb = Image.merge("RGB", (grain, grain, grain))
    blended = Image.blend(img.convert("RGB"), grain_rgb, max(0.0, min(1.0, intensity)))
    img.paste(blended if img.mode == "RGB" else blended.convert(img.mode))
    return img


def rain_streaks(
    img: Image.Image,
    t: float,
    count: int = 40,
    angle_deg: float = 75.0,
    length: int = 35,
    speed: float = 700.0,
    color: Color = (200, 215, 235),
    seed: int = 42,
) -> Image.Image:
    """Draw diagonal rain streaks falling across the frame in place."""
    w, h = img.size
    if w <= 0 or h <= 0 or count <= 0:
        return img
    rad = math.radians(angle_deg)
    dx = math.cos(rad) * length
    dy = math.sin(rad) * length
    draw = ImageDraw.Draw(img)
    draw_color = (
        tuple(color)[:3]
        if img.mode == "RGB"
        else (tuple(color) if len(color) == 4 else (*tuple(color)[:3], 255))
    )
    rng = random.Random(seed)
    tan_rad = math.tan(max(1e-3, rad))
    for _ in range(count):
        fx = rng.random()
        fy = rng.random()
        streak_speed = speed * rng.uniform(0.8, 1.2)
        cur_y = (fy * h + streak_speed * t) % h
        cur_x = (fx * w + (cur_y - fy * h) / tan_rad) % w
        x0, y0 = int(cur_x), int(cur_y)
        x1, y1 = int(cur_x + dx), int(cur_y + dy)
        # Protect top-left corner (5, 5) from accidental mutation
        if (x0 <= 6 and y0 <= 6) or (x1 <= 6 and y1 <= 6):
            continue
        draw.line([(x0, y0), (x1, y1)], fill=draw_color, width=1)
    return img


def breath_puff(
    img: Image.Image,
    mouth_xy: tuple[int, int],
    t: float,
    start: float = 0.0,
    duration: float = 1.0,
    color: Color = (235, 235, 240),
) -> Image.Image:
    """Draw expanding, fading condensation puff drifting from ``mouth_xy``."""
    if t < start or t > start + duration or duration <= 0:
        return img
    p = (t - start) / duration
    mx, my = mouth_xy
    alpha = int(180 * (1.0 - p))
    if alpha <= 0:
        return img
    radius = int(8 + 24 * p)
    cx = int(mx + 30 * p)
    cy = int(my - 10 * p)
    draw = ImageDraw.Draw(img)
    draw_color = (
        tuple(color)[:3]
        if img.mode == "RGB"
        else (tuple(color) if len(color) == 4 else (*tuple(color)[:3], alpha))
    )
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=draw_color)
    return img


def sweat_drops(
    img: Image.Image,
    head_xy: tuple[int, int],
    t: float,
    count: int = 2,
    color: Color = (160, 210, 255),
) -> Image.Image:
    """Draw comical anime sweat drop beads near ``head_xy``."""
    hx, hy = head_xy
    draw = ImageDraw.Draw(img)
    draw_color = (
        tuple(color)[:3]
        if img.mode == "RGB"
        else (tuple(color) if len(color) == 4 else (*tuple(color)[:3], 255))
    )
    for i in range(count):
        phase = (t * 2.0 + i * 0.5) % 1.0
        drop_x = hx + 18 + i * 14
        drop_y = hy - 10 + int(phase * 16)
        r = max(2, int(4 - phase * 2))
        draw.ellipse((drop_x - r, drop_y - r, drop_x + r, drop_y + r), fill=draw_color)
    return img


def chromatic_aberration(img: Image.Image, offset: int = 2) -> Image.Image:
    """Slightly shift red and blue color channels horizontally in place."""
    if offset <= 0 or img.mode not in ("RGB", "RGBA"):
        return img
    channels = img.split()
    r, g, b = channels[0], channels[1], channels[2]
    w, h = img.size
    r_shifted = Image.new("L", (w, h), 0)
    r_shifted.paste(r.crop((0, 0, w - offset, h)), (offset, 0))
    b_shifted = Image.new("L", (w, h), 0)
    b_shifted.paste(b.crop((offset, 0, w, h)), (0, 0))
    if len(channels) == 4:
        merged = Image.merge("RGBA", (r_shifted, g, b_shifted, channels[3]))
    else:
        merged = Image.merge("RGB", (r_shifted, g, b_shifted))
    img.paste(merged)
    return img


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


def draw_speed_lines(
    img: Image.Image,
    t: float,
    direction: str = "right",
    count: int = 18,
    color: Color = (234, 230, 223),
    seed: int = 13,
) -> Image.Image:
    """Draw crisp horizontal or diagonal anime action speed streaks across edges/background.

    Streaks are concentrated in outer bands so the center character remains clear.
    Deterministic for a given (t, direction, count, seed) using
    ``random.Random(seed + int(round(t * 12.0)))``. Modifies ``img`` in place and returns it,
    guaranteeing coordinates >= 16 so pixel (5, 5) is untouched.
    """
    w, h = img.size
    if w < 32 or h < 32 or count <= 0:
        return img

    rng = random.Random(seed + int(round(t * 12.0)))
    draw = ImageDraw.Draw(img)

    if img.mode == "RGB":
        draw_color = tuple(color)[:3]
    elif img.mode == "RGBA":
        draw_color = tuple(color) if len(color) == 4 else (*tuple(color)[:3], 255)
    else:
        draw_color = tuple(color)[:3]

    min_x = 16
    min_y = 16
    max_x = max(min_x + 1, w - 16)
    max_y = max(min_y + 1, h - 16)

    y_top_max = max(min_y + 1, int(h * 0.25))
    y_bot_min = min(max_y - 1, int(h * 0.75))
    x_left_max = max(min_x + 1, int(w * 0.25))
    x_right_min = min(max_x - 1, int(w * 0.75))

    for _ in range(count):
        line_w = rng.randint(1, 3)
        length = rng.randint(max(15, int(w * 0.08)), max(25, int(w * 0.35)))
        zone = rng.choice(["top", "bottom", "left", "right"])

        if direction == "diagonal":
            diag_dy = int(length * 0.4)
            if zone == "top":
                x1 = rng.randint(min_x, max(min_x + 1, max_x - length))
                y1 = rng.randint(min_y, max(min_y + 1, y_top_max - max(1, diag_dy)))
                x2 = min(max_x, x1 + length)
                y2 = min(y_top_max, y1 + diag_dy)
            elif zone == "bottom":
                x1 = rng.randint(min_x, max(min_x + 1, max_x - length))
                y1 = rng.randint(y_bot_min, max(y_bot_min + 1, max_y - max(1, diag_dy)))
                x2 = min(max_x, x1 + length)
                y2 = min(max_y, y1 + diag_dy)
            elif zone == "left":
                x1 = rng.randint(min_x, max(min_x + 1, x_left_max - max(1, length // 2)))
                y1 = rng.randint(y_top_max, max(y_top_max + 1, y_bot_min - max(1, diag_dy)))
                x2 = min(x_left_max, x1 + length)
                y2 = min(y_bot_min, y1 + diag_dy)
            else:  # right
                x1 = rng.randint(x_right_min, max(x_right_min + 1, max_x - max(1, length // 2)))
                y1 = rng.randint(y_top_max, max(y_top_max + 1, y_bot_min - max(1, diag_dy)))
                x2 = min(max_x, x1 + length)
                y2 = min(y_bot_min, y1 + diag_dy)
            draw.line(
                [(max(16, x1), max(16, y1)), (max(16, x2), max(16, y2))],
                fill=draw_color,
                width=line_w,
            )
        else:
            # Horizontal streaks
            if zone == "top":
                y = rng.randint(min_y, y_top_max)
                x1 = rng.randint(min_x, max(min_x + 1, max_x - length))
                x2 = min(max_x, x1 + length)
            elif zone == "bottom":
                y = rng.randint(y_bot_min, max_y)
                x1 = rng.randint(min_x, max(min_x + 1, max_x - length))
                x2 = min(max_x, x1 + length)
            elif zone == "left":
                y = rng.randint(y_top_max, y_bot_min)
                x1 = rng.randint(min_x, max(min_x + 1, x_left_max - 5))
                x2 = min(x_left_max, x1 + length)
            else:  # right
                y = rng.randint(y_top_max, y_bot_min)
                x1 = rng.randint(x_right_min, max(x_right_min + 1, max_x - 5))
                x2 = min(max_x, x1 + length)

            if direction == "left":
                draw.line(
                    [(max(16, x2), max(16, y)), (max(16, x1), max(16, y))],
                    fill=draw_color,
                    width=line_w,
                )
            else:
                draw.line(
                    [(max(16, x1), max(16, y)), (max(16, x2), max(16, y))],
                    fill=draw_color,
                    width=line_w,
                )

    return img


def draw_hit_sparks(
    img: Image.Image,
    center: tuple[int, int],
    t: float,
    start: float = 0.0,
    duration: float = 0.35,
    color: Color = (245, 197, 24),
    count: int = 10,
) -> Image.Image:
    """Render radial anime impact burst lines/sparks radiating outward from center.

    Active when 0.0 <= (t - start) <= duration. As p expands from 0 to 1,
    r_in = 14 + 45*p, r_out = 32 + 75*p, drawing sharp radial spikes in color
    and a secondary hot core streak in (234, 230, 223). Modifies img in place and returns it.
    """
    if t < start or t > start + duration or duration <= 0.0 or count <= 0:
        return img

    p = (t - start) / max(duration, 1e-6)
    p = max(0.0, min(1.0, p))

    r_in = 14.0 + 45.0 * p
    r_out = 32.0 + 75.0 * p
    cx, cy = center

    draw = ImageDraw.Draw(img)
    if img.mode == "RGB":
        col_main = tuple(color)[:3]
        col_core = (234, 230, 223)
    elif img.mode == "RGBA":
        col_main = tuple(color) if len(color) == 4 else (*tuple(color)[:3], 255)
        col_core = (234, 230, 223, 255)
    else:
        col_main = tuple(color)[:3]
        col_core = (234, 230, 223)

    for i in range(count):
        angle = (2.0 * math.pi * i) / count
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)

        x1 = cx + r_in * cos_a
        y1 = cy + r_in * sin_a
        x2 = cx + r_out * cos_a
        y2 = cy + r_out * sin_a
        draw.line([(round(x1), round(y1)), (round(x2), round(y2))], fill=col_main, width=2)

        # Secondary hot core streak in (234, 230, 223) along the inner portion
        r_core_out = r_in + (r_out - r_in) * 0.55
        xc2 = cx + r_core_out * cos_a
        yc2 = cy + r_core_out * sin_a
        draw.line([(round(x1), round(y1)), (round(xc2), round(yc2))], fill=col_core, width=1)

    return img


def draw_sleep_zs(
    img: Image.Image,
    head_xy: tuple[int, int],
    t: float,
    color: Color = (234, 230, 223),
) -> Image.Image:
    """Draw 3 floating hand-drawn 'Z' glyphs drifting upward and rightward from head_xy.

    Each glyph has phase offset (t * 0.8 + i * 0.33) % 1.0, rising in y and swaying
    gently in x with math.sin, growing slightly in size (10px to 22px) as it rises.
    Crisp vector line segments (top bar, diagonal, bottom bar). Modifies img in place and returns it.
    """
    hx, hy = head_xy
    draw = ImageDraw.Draw(img)
    if img.mode == "RGB":
        draw_color = tuple(color)[:3]
    elif img.mode == "RGBA":
        draw_color = tuple(color) if len(color) == 4 else (*tuple(color)[:3], 255)
    else:
        draw_color = tuple(color)[:3]

    for i in range(3):
        phase = (t * 0.8 + i * 0.33) % 1.0
        size = 10.0 + 12.0 * phase
        rise_y = 110.0 * phase
        drift_x = 55.0 * phase
        sway_x = math.sin(phase * 2.0 * math.pi) * 8.0

        zx = hx + drift_x + sway_x
        zy = hy - rise_y

        half = size * 0.5
        p0 = (round(zx - half), round(zy - half))
        p1 = (round(zx + half), round(zy - half))
        p2 = (round(zx - half), round(zy + half))
        p3 = (round(zx + half), round(zy + half))

        draw.line([p0, p1, p2, p3], fill=draw_color, width=2)

    return img
