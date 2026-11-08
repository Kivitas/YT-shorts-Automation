"""Countryball (Polandball) & "Types Of..." Meme Engine for AutoShorts.

Features:
1. Procedural Countryballs (Polandball):
   - Flag-textured spheres with authentic Polandball lore (Poland inverted, USA with sunglasses,
     UK with top hat & monocle, Germany, France, Japan, etc.).
   - Spherical 3D lighting, curved shading, soft terminator, and contact ground shadow.
   - Squash & stretch bounce physics with momentum, impact compression, and velocity stretch.
   - Expressive non-verbal eye morphology state machine: neutral, angry, shocked, smug,
     deadpan, happy, worried, crying, squint.
2. "Types Of..." Meme Vignettes:
   - Top archetype pill badges (e.g. 'TYPE 01: THE OVERPREPARER') with glass styling & pop-in.
   - RPG radar stat cards with animated polygonal spider charts and glowing vertices.
   - Freeze-frame comedic scenario timing with dramatic impact freeze and action lines.
   - Sound effect synchronization with autoshorts.longform.sfx (whoosh, punch, freeze crack).
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from autoshorts.core.theme import (
    ACCENT_CORAL,
    ACCENT_TEAL,
    BG_DEEP,
    BG_SURFACE,
    TEXT_PRIMARY,
    ease_out_cubic,
)
from autoshorts.longform.drawer import (
    FLOOR_Y,
    WIDTH,
    load_font,
)
from autoshorts.longform.motion import (
    blink,
    ease_in_out_sine,
    ease_out_back,
    ease_out_elastic,
)
from autoshorts.longform.sfx import (
    synth_alarm,
    synth_glass_crack,
    synth_punch,
    synth_whoosh,
)

# ---------------------------------------------------------------------------
# Colors & Styles
# ---------------------------------------------------------------------------
INK = (20, 20, 26)  # Polandball comic thick hand-drawn outline
WHITE = (255, 255, 255)
SHADOW_TINT = (0, 0, 0, 75)
GLASS_BG = (18, 24, 52, 220)  # Translucent dark glass for badges and cards
NEON_CYAN = (60, 220, 230)
NEON_CORAL = (255, 105, 115)
NEON_YELLOW = (255, 215, 60)


class CountryballFlag(StrEnum):
    POLAND = "poland"  # Lore: red top, white bottom
    POLAND_REAL = "poland_real"  # Real: white top, red bottom
    USA = "usa"
    UK = "uk"
    GERMANY = "germany"
    FRANCE = "france"
    JAPAN = "japan"
    ITALY = "italy"
    CANADA = "canada"
    UKRAINE = "ukraine"
    CUSTOM = "custom"


class EyeMood(StrEnum):
    NEUTRAL = "neutral"
    ANGRY = "angry"
    SHOCKED = "shocked"
    SMUG = "smug"
    DEADPAN = "deadpan"
    HAPPY = "happy"
    WORRIED = "worried"
    CRYING = "crying"
    SQUINT = "squint"


# ---------------------------------------------------------------------------
# Squash & Stretch Bounce Physics
# ---------------------------------------------------------------------------
LANDING_SQUASH_DY = 0.20  # vertical compression at landing (-20%)
LANDING_SQUASH_DX = 0.25  # horizontal expansion at landing (+25%)
JUMP_STRETCH_MAX = 0.25  # max horizontal stretch while airborne
JUMP_STRETCH_PER_VELOCITY = 0.00028  # stretch per px/s of vertical speed


@dataclass
class BounceState:
    """Instantaneous physics state of a bouncing Countryball."""

    x: float
    y: float
    radius_x: float  # Stretched / squashed horizontal radius
    radius_y: float  # Stretched / squashed vertical radius
    velocity_y: float
    altitude: float  # Height above floor in pixels
    shadow_scale: float
    shadow_alpha: float
    is_grounded: bool


class CountryballPhysics:
    """Realistic & cartoon squash/stretch physics simulation for Countryballs.

    Computes parabolic gravity arc, mid-air vertical stretch based on velocity,
    and impact squash on ground contact while conserving volume/area.
    """

    def __init__(
        self,
        base_radius: float = 80.0,
        floor_y: float = FLOOR_Y,
        gravity: float = 2400.0,
    ) -> None:
        self.base_r = base_radius
        self.floor_y = floor_y
        self.gravity = gravity

    def simulate_periodic(
        self,
        t: float,
        start_x: float = 960.0,
        bounce_period: float = 0.75,
        bounce_height: float = 180.0,
        drift_vx: float = 0.0,
    ) -> BounceState:
        """Simulate a rhythmic repetitive cartoon bounce."""
        if bounce_period <= 0:
            bounce_period = 0.75

        cycle_t = t % bounce_period
        p = cycle_t / bounce_period  # 0.0 -> 1.0

        # Parabolic arc: 0 at start, 1 at peak (p=0.5), 0 at end (p=1.0)
        # altitude = bounce_height * (1 - (2p - 1)^2)
        norm_h = max(0.0, 1.0 - (2.0 * p - 1.0) ** 2)
        altitude = bounce_height * norm_h

        # Velocity is derivative of altitude with respect to time:
        # d/dt [h * (1 - (2t/T - 1)^2)] = -4h/T * (2t/T - 1)
        velocity_y = -(4.0 * bounce_height / bounce_period) * (2.0 * p - 1.0)

        # Ground impact compression phase: within 7% of cycle boundary
        is_ground = False
        impact_window = 0.07 * bounce_period
        squash_k = 0.0  # 0 = resting, 1 = full landing squash

        if cycle_t < impact_window:
            # Rebounding from ground
            k = 1.0 - (cycle_t / impact_window)
            squash_k = ease_out_cubic(k)
            is_ground = True
        elif cycle_t > bounce_period - impact_window:
            # Touching down into ground
            k = (cycle_t - (bounce_period - impact_window)) / impact_window
            squash_k = ease_in_out_sine(k)
            is_ground = True

        if is_ground:
            # Landing squash: dY = -20% (vertical), dX = +25% (horizontal).
            # At full squash 0.8 * 1.25 = 1.0, so the sphere keeps its area.
            ry = self.base_r * (1.0 - LANDING_SQUASH_DY * squash_k)
            rx = self.base_r * (1.0 + LANDING_SQUASH_DX * squash_k)
        else:
            # Mid-air: horizontal stretch along the jump, proportional to speed.
            # Area is preserved (rx * ry = base_r^2).
            stretch = min(JUMP_STRETCH_MAX, abs(velocity_y) * JUMP_STRETCH_PER_VELOCITY)
            rx = self.base_r * (1.0 + stretch)
            ry = (self.base_r**2) / max(1.0, rx)

        x = start_x + drift_vx * t
        # Ball sits on floor: center y = floor_y - ry
        y = (self.floor_y - ry) - altitude

        # Contact shadow scale & alpha
        alt_ratio = min(1.0, altitude / max(1.0, bounce_height))
        shadow_scale = 1.0 - 0.45 * alt_ratio
        shadow_alpha = 1.0 - 0.65 * alt_ratio

        return BounceState(
            x=x,
            y=y,
            radius_x=rx,
            radius_y=ry,
            velocity_y=velocity_y,
            altitude=altitude,
            shadow_scale=shadow_scale,
            shadow_alpha=shadow_alpha,
            is_grounded=is_ground,
        )


# ---------------------------------------------------------------------------
# Procedural Flag & Sphere Shading
# ---------------------------------------------------------------------------
def _clip_layer_to_circle(
    layer: Image.Image,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
) -> None:
    """Zero the alpha of ``layer`` outside the ellipse (cx, cy, rx, ry), in place.

    Flag stripes, chords and lines are drawn against the full bounding box, so they
    spill past the sphere; this mask keeps every pixel inside the ball radius.
    """
    mask = Image.new("L", layer.size, 0)
    ImageDraw.Draw(mask).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))


def _draw_flag_texture(
    draw: ImageDraw.ImageDraw,
    flag: CountryballFlag | str,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    custom_colors: tuple[tuple[int, int, int], ...] | None = None,
) -> None:
    """Draw procedural flag patterns clipped to an ellipse."""
    x0, y0, x1, y1 = cx - rx, cy - ry, cx + rx, cy + ry
    h = 2.0 * ry

    if flag == CountryballFlag.POLAND:
        # Canon Polandball: Red top, White bottom (inverted)
        draw.chord([x0, y0, x1, y1], 180, 360, fill=(220, 36, 48))
        draw.chord([x0, y0, x1, y1], 0, 180, fill=(255, 255, 255))
        draw.line([(x0, cy), (x1, cy)], fill=(220, 36, 48), width=1)
    elif flag == CountryballFlag.POLAND_REAL:
        # Real Poland flag: White top, Red bottom
        draw.chord([x0, y0, x1, y1], 180, 360, fill=(255, 255, 255))
        draw.chord([x0, y0, x1, y1], 0, 180, fill=(220, 36, 48))
        draw.line([(x0, cy), (x1, cy)], fill=(220, 36, 48), width=1)
    elif flag == CountryballFlag.GERMANY:
        # Horizontal tricolor: Black, Red, Gold
        draw.ellipse([x0, y0, x1, y1], fill=(255, 204, 0))  # Gold bottom
        draw.chord([x0, y0, x1, y1], 180, 360, fill=(24, 24, 28))  # Black top
        # Red middle band
        draw.rectangle([x0, cy - 0.18 * ry, x1, cy + 0.18 * ry], fill=(220, 36, 48))
    elif flag == CountryballFlag.FRANCE:
        # Vertical tricolor: Blue, White, Red
        draw.ellipse([x0, y0, x1, y1], fill=(255, 255, 255))
        draw.chord([x0, y0, x1, y1], 90, 270, fill=(0, 85, 164))  # Blue left
        draw.chord([x0, y0, x1, y1], 270, 90, fill=(239, 65, 53))  # Red right
        draw.rectangle([cx - 0.28 * rx, y0, cx + 0.28 * rx, y1], fill=(255, 255, 255))
    elif flag == CountryballFlag.ITALY:
        # Vertical tricolor: Green, White, Red
        draw.ellipse([x0, y0, x1, y1], fill=(255, 255, 255))
        draw.chord([x0, y0, x1, y1], 90, 270, fill=(0, 146, 70))  # Green left
        draw.chord([x0, y0, x1, y1], 270, 90, fill=(206, 43, 55))  # Red right
        draw.rectangle([cx - 0.28 * rx, y0, cx + 0.28 * rx, y1], fill=(255, 255, 255))
    elif flag == CountryballFlag.UKRAINE:
        # Azure blue top, Golden yellow bottom
        draw.chord([x0, y0, x1, y1], 180, 360, fill=(0, 91, 187))
        draw.chord([x0, y0, x1, y1], 0, 180, fill=(255, 213, 0))
    elif flag == CountryballFlag.JAPAN:
        # White field, central crimson red sun circle
        draw.ellipse([x0, y0, x1, y1], fill=(255, 255, 255))
        sun_r = 0.42 * min(rx, ry)
        draw.ellipse([cx - sun_r, cy - sun_r, cx + sun_r, cy + sun_r], fill=(188, 0, 45))
    elif flag == CountryballFlag.USA:
        # USA: Red & white horizontal stripes + blue canton top-left
        draw.ellipse([x0, y0, x1, y1], fill=(255, 255, 255))
        stripe_count = 7
        stripe_h = h / stripe_count
        for i in range(0, stripe_count, 2):
            sy0 = y0 + i * stripe_h
            draw.rectangle([x0, sy0, x1, sy0 + stripe_h], fill=(187, 19, 62))
        # Blue canton top-left
        draw.pieslice([x0, y0, x1, y1], 180, 270, fill=(0, 33, 71))
        # Mini star dots in canton
        star_x, star_y = cx - 0.45 * rx, cy - 0.38 * ry
        for dx_s, dy_s in [(-12, -10), (0, -10), (12, -10), (-6, 5), (6, 5)]:
            draw.ellipse(
                [
                    star_x + dx_s - 2,
                    star_y + dy_s - 2,
                    star_x + dx_s + 2,
                    star_y + dy_s + 2,
                ],
                fill=WHITE,
            )
    elif flag == CountryballFlag.UK:
        # UK Union Jack: Blue field with white/red cross and saltires
        draw.ellipse([x0, y0, x1, y1], fill=(1, 33, 105))  # Deep navy blue
        # White diagonals
        draw.line([(x0, y0), (x1, y1)], fill=WHITE, width=max(4, int(0.18 * ry)))
        draw.line([(x0, y1), (x1, y0)], fill=WHITE, width=max(4, int(0.18 * ry)))
        # Red diagonals
        draw.line([(x0, y0), (x1, y1)], fill=(200, 16, 46), width=max(2, int(0.08 * ry)))
        draw.line([(x0, y1), (x1, y0)], fill=(200, 16, 46), width=max(2, int(0.08 * ry)))
        # White St George Cross
        cross_w = max(6, int(0.24 * ry))
        draw.rectangle([cx - cross_w / 2, y0, cx + cross_w / 2, y1], fill=WHITE)
        draw.rectangle([x0, cy - cross_w / 2, x1, cy + cross_w / 2], fill=WHITE)
        # Red St George Cross
        red_w = max(3, int(0.14 * ry))
        draw.rectangle([cx - red_w / 2, y0, cx + red_w / 2, y1], fill=(200, 16, 46))
        draw.rectangle([x0, cy - red_w / 2, x1, cy + red_w / 2], fill=(200, 16, 46))
    else:
        # Custom or generic fallback
        colors = custom_colors or ((60, 130, 240), (255, 255, 255))
        draw.ellipse([x0, y0, x1, y1], fill=colors[0])
        if len(colors) > 1:
            draw.chord([x0, y0, x1, y1], 0, 180, fill=colors[1])


def _apply_spherical_shading(
    img: Image.Image,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
) -> None:
    """Apply 3D spherical Lambertian shading with soft terminator & rim highlight."""
    shade_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shade_layer)

    # 1. Soft spherical shadow on bottom-right (simulating light from upper-left)
    steps = 14
    for i in range(steps):
        factor = (i + 1) / steps
        alpha = int(120 * (factor**1.6))
        # Inward offset circle on shaded side
        ox = cx + 0.28 * rx * factor
        oy = cy + 0.32 * ry * factor
        s_rx = rx * (1.0 - 0.22 * factor)
        s_ry = ry * (1.0 - 0.22 * factor)
        d.chord(
            [ox - s_rx, oy - s_ry, ox + s_rx, oy + s_ry],
            0,
            180,
            fill=(10, 12, 28, alpha),
        )

    # 2. Subtle specular fresnel rim on top-left edge
    highlight_alpha = 65
    hx = cx - 0.18 * rx
    hy = cy - 0.24 * ry
    hr_x = rx * 0.72
    hr_y = ry * 0.65
    d.chord(
        [hx - hr_x, hy - hr_y, hx + hr_x, hy + hr_y],
        180,
        360,
        fill=(255, 255, 255, highlight_alpha),
    )

    # Shading is bounded by the sphere too; the offset chords would otherwise spill outside.
    _clip_layer_to_circle(shade_layer, cx, cy, rx, ry)

    if img.mode == "RGBA":
        img.alpha_composite(shade_layer)
    else:
        base = img.convert("RGBA")
        base.alpha_composite(shade_layer)
        img.paste(base.convert(img.mode))


# ---------------------------------------------------------------------------
# Expressive Non-Verbal Eye Morphology State Machine
# ---------------------------------------------------------------------------
def _draw_sweat_droplet(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    size: float,
    line_width: int,
) -> None:
    """Comic sweat droplet (pointed top, round bottom) centred near (x, y)."""
    pts = [(x, y - 1.6 * size)]
    for i in range(1, 24):  # round bottom, from right side around to left side
        a = math.pi * i / 24
        pts.append((x + size * math.cos(a), y + size * math.sin(a)))
    pts.append((x, y - 1.6 * size))
    draw.polygon(pts, fill=(100, 180, 245), outline=INK, width=max(2, line_width // 2))


def draw_countryball_eyes(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    mood: EyeMood | str = EyeMood.NEUTRAL,
    gaze_x: float = 0.0,  # -1.0 left to +1.0 right
    gaze_y: float = 0.0,
    blink_factor: float = 1.0,  # 1.0 open, 0.0 shut
    line_width: int = 4,
) -> None:
    """Render expressive non-verbal white Polandball eyes with thick black outline.

    Polandballs strictly emote through eye shapes (no mouths or eyebrows).
    """
    eye_span = 0.36 * rx
    base_eye_w = 0.22 * rx
    base_eye_h = 0.28 * ry * blink_factor

    center_y = cy - 0.12 * ry + gaze_y * 0.15 * ry
    left_x = cx - eye_span / 2 + gaze_x * 0.18 * rx
    right_x = cx + eye_span / 2 + gaze_x * 0.18 * rx

    # If completely blinking shut:
    if blink_factor < 0.12:
        draw.line(
            [(left_x - base_eye_w, center_y), (left_x + base_eye_w, center_y)],
            fill=INK,
            width=line_width,
        )
        draw.line(
            [(right_x - base_eye_w, center_y), (right_x + base_eye_w, center_y)],
            fill=INK,
            width=line_width,
        )
        return

    mood_str = mood.value if isinstance(mood, EyeMood) else str(mood).lower()

    if mood_str == "angry":
        # Angled inward slanted trapezoidal/triangular angry eyes
        for sign, eye_x in ((-1, left_x), (1, right_x)):
            pts = [
                (eye_x - base_eye_w, center_y - 0.2 * base_eye_h + sign * 0.4 * base_eye_h),
                (eye_x + base_eye_w, center_y - 0.2 * base_eye_h - sign * 0.4 * base_eye_h),
                (eye_x + base_eye_w * 0.8, center_y + base_eye_h),
                (eye_x - base_eye_w * 0.8, center_y + base_eye_h),
            ]
            draw.polygon(pts, fill=WHITE, outline=INK, width=line_width)

    elif mood_str == "shocked":
        # Enormous wide open circular eyes
        shock_r = max(base_eye_w, base_eye_h) * 1.45
        for eye_x in (left_x - 4, right_x + 4):
            draw.ellipse(
                [eye_x - shock_r, center_y - shock_r, eye_x + shock_r, center_y + shock_r],
                fill=WHITE,
                outline=INK,
                width=line_width,
            )
        # Detached sweat droplet, floating clear of the sphere outline (upper right)
        _draw_sweat_droplet(draw, cx + 1.22 * rx, cy - 0.62 * ry, 0.13 * min(rx, ry), line_width)

    elif mood_str == "smug":
        # Half-closed sly crescent squints: the upper lid bows up into a crescent (^-shaped)
        for eye_x in (left_x, right_x):
            draw.chord(
                [
                    eye_x - base_eye_w,
                    center_y - 0.5 * base_eye_h,
                    eye_x + base_eye_w,
                    center_y + base_eye_h,
                ],
                180,
                360,
                fill=WHITE,
                outline=INK,
                width=line_width,
            )
            # Flat lower lid across the chord's base (the chord centre row)
            base_y = center_y + 0.25 * base_eye_h
            draw.line(
                [(eye_x - base_eye_w, base_y), (eye_x + base_eye_w, base_y)],
                fill=INK,
                width=line_width,
            )

    elif mood_str == "deadpan":
        # Horizontal slit ovals: flat, half-lidded, no pupil
        slit_h = base_eye_h * 0.32
        for eye_x in (left_x, right_x):
            draw.ellipse(
                [eye_x - base_eye_w, center_y - slit_h, eye_x + base_eye_w, center_y + slit_h],
                fill=WHITE,
                outline=INK,
                width=line_width,
            )

    elif mood_str == "happy":
        # Cheerful inverted 'U' crescent curves (^ ^)
        arc_h = base_eye_h * 0.9
        for eye_x in (left_x, right_x):
            draw.arc(
                [eye_x - base_eye_w, center_y - arc_h, eye_x + base_eye_w, center_y + arc_h],
                180,
                360,
                fill=INK,
                width=line_width + 1,
            )

    elif mood_str == "worried":
        # Inward raised sad oval eyes
        for sign, eye_x in ((-1, left_x), (1, right_x)):
            pts = [
                (eye_x - base_eye_w, center_y - sign * 0.3 * base_eye_h),
                (eye_x + base_eye_w, center_y + sign * 0.3 * base_eye_h),
                (eye_x + base_eye_w * 0.7, center_y + base_eye_h),
                (eye_x - base_eye_w * 0.7, center_y + base_eye_h),
            ]
            draw.polygon(pts, fill=WHITE, outline=INK, width=line_width)

    elif mood_str == "crying":
        # Worried eyes with animated comic blue teardrops
        for eye_x in (left_x, right_x):
            draw.ellipse(
                [
                    eye_x - base_eye_w,
                    center_y - base_eye_h,
                    eye_x + base_eye_w,
                    center_y + base_eye_h,
                ],
                fill=WHITE,
                outline=INK,
                width=line_width,
            )
            # Teardrop stream
            tear_y = center_y + base_eye_h + 8
            draw.line(
                [(eye_x, tear_y), (eye_x, tear_y + 22)],
                fill=(100, 180, 245),
                width=max(3, int(line_width * 0.8)),
            )

    elif mood_str == "squint":
        # (> <) tight squint lines
        d = base_eye_w * 0.8
        draw.line(
            [(left_x - d, center_y - d), (left_x + d, center_y), (left_x - d, center_y + d)],
            fill=INK,
            width=line_width,
            joint="curve",
        )
        draw.line(
            [(right_x + d, center_y - d), (right_x - d, center_y), (right_x + d, center_y + d)],
            fill=INK,
            width=line_width,
            joint="curve",
        )

    else:
        # Default NEUTRAL: Two upright white ovals
        for eye_x in (left_x, right_x):
            draw.ellipse(
                [
                    eye_x - base_eye_w,
                    center_y - base_eye_h,
                    eye_x + base_eye_w,
                    center_y + base_eye_h,
                ],
                fill=WHITE,
                outline=INK,
                width=line_width,
            )


# ---------------------------------------------------------------------------
# Accessories (Sunglasses for USA, Top Hat for UK)
# ---------------------------------------------------------------------------
def _draw_sunglasses(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    line_w: int = 4,
) -> None:
    """Classic cool black sunglasses for USA countryball."""
    y = cy - 0.12 * ry
    sh = 0.28 * ry
    # Left & right glass polygons
    pts_l = [
        (cx - 0.45 * rx, y - sh * 0.4),
        (cx - 0.08 * rx, y - sh * 0.4),
        (cx - 0.12 * rx, y + sh * 0.8),
        (cx - 0.42 * rx, y + sh * 0.8),
    ]
    pts_r = [
        (cx + 0.08 * rx, y - sh * 0.4),
        (cx + 0.45 * rx, y - sh * 0.4),
        (cx + 0.42 * rx, y + sh * 0.8),
        (cx + 0.12 * rx, y + sh * 0.8),
    ]
    draw.polygon(pts_l, fill=(24, 24, 28), outline=INK, width=line_w)
    draw.polygon(pts_r, fill=(24, 24, 28), outline=INK, width=line_w)
    # Bridge
    draw.line([(cx - 0.10 * rx, y - sh * 0.2), (cx + 0.10 * rx, y - sh * 0.2)], fill=INK, width=line_w)  # fmt: skip
    # White reflection streak
    draw.line([(cx - 0.38 * rx, y - sh * 0.1), (cx - 0.28 * rx, y + sh * 0.5)], fill=(120, 120, 130), width=2)  # fmt: skip
    draw.line([(cx + 0.18 * rx, y - sh * 0.1), (cx + 0.28 * rx, y + sh * 0.5)], fill=(120, 120, 130), width=2)  # fmt: skip


def _draw_top_hat(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    line_w: int = 4,
) -> None:
    """Distinguished gentleman's top hat & monocle for UK countryball."""
    # Top hat brim
    brim_y = cy - 0.88 * ry
    draw.line([(cx - 0.65 * rx, brim_y), (cx + 0.65 * rx, brim_y)], fill=INK, width=line_w + 3)
    # Hat cylinder
    hw = 0.45 * rx
    hh = 0.75 * ry
    draw.rectangle([cx - hw, brim_y - hh, cx + hw, brim_y], fill=(28, 28, 34), outline=INK, width=line_w)  # fmt: skip
    # Red hat ribbon band
    draw.rectangle([cx - hw, brim_y - 0.22 * hh, cx + hw, brim_y], fill=(200, 24, 38))
    # Monocle on right eye
    mx = cx + 0.22 * rx
    my = cy - 0.10 * ry
    mr = 0.24 * min(rx, ry)
    draw.ellipse([mx - mr, my - mr, mx + mr, my + mr], outline=(230, 190, 40), width=line_w)
    # Monocle string
    draw.line([(mx + mr, my), (mx + mr + 12, my + 45)], fill=(230, 190, 40), width=2)


# ---------------------------------------------------------------------------
# Complete Procedural Countryball Entity
# ---------------------------------------------------------------------------
class ProceduralCountryball:
    """Master Procedural Countryball renderer with physics, squash/stretch, and emotions."""

    def __init__(
        self,
        flag: CountryballFlag | str = CountryballFlag.POLAND,
        base_radius: float = 80.0,
        accessories: bool = True,
    ) -> None:
        self.flag = flag
        self.base_radius = base_radius
        self.accessories = accessories
        self.physics = CountryballPhysics(base_radius=base_radius)

    def draw_ball(
        self,
        img: Image.Image,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        mood: EyeMood | str = EyeMood.NEUTRAL,
        gaze: tuple[float, float] = (0.0, 0.0),
        blink_p: float = 1.0,
    ) -> None:
        """Render complete countryball onto img at center (cx, cy) with radii (rx, ry)."""
        # 1. Base flag texture, clipped strictly to the ball ellipse
        ball_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        _draw_flag_texture(ImageDraw.Draw(ball_layer), self.flag, cx, cy, rx, ry)
        _clip_layer_to_circle(ball_layer, cx, cy, rx, ry)

        # 2. Comic ink border
        outline_w = max(4, int(0.05 * min(rx, ry)))
        d_ball = ImageDraw.Draw(ball_layer)
        d_ball.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=INK, width=outline_w)

        # Composite base ball
        if img.mode == "RGBA":
            img.alpha_composite(ball_layer)
        else:
            base = img.convert("RGBA")
            base.alpha_composite(ball_layer)
            img.paste(base.convert(img.mode))

        # 3. 3D spherical shading
        _apply_spherical_shading(img, cx, cy, rx, ry)

        # 4. Accessories (Sunglasses for USA, Top Hat for UK)
        acc_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d_acc = ImageDraw.Draw(acc_layer)

        flag_str = self.flag.value if isinstance(self.flag, CountryballFlag) else str(self.flag)
        if self.accessories and flag_str == "usa":
            _draw_sunglasses(d_acc, cx, cy, rx, ry, line_w=outline_w)
        else:
            # Draw expressive non-verbal eyes
            draw_countryball_eyes(
                d_acc,
                cx,
                cy,
                rx,
                ry,
                mood=mood,
                gaze_x=gaze[0],
                gaze_y=gaze[1],
                blink_factor=blink_p,
                line_width=outline_w,
            )

        if self.accessories and flag_str == "uk":
            _draw_top_hat(d_acc, cx, cy, rx, ry, line_w=outline_w)

        if img.mode == "RGBA":
            img.alpha_composite(acc_layer)
        else:
            base = img.convert("RGBA")
            base.alpha_composite(acc_layer)
            img.paste(base.convert(img.mode))

    def render_bounce_frame(
        self,
        img: Image.Image,
        t: float,
        start_x: float = 960.0,
        mood: EyeMood | str = EyeMood.NEUTRAL,
        bounce_period: float = 0.75,
        bounce_height: float = 180.0,
        drift_vx: float = 0.0,
    ) -> BounceState:
        """Simulate squash/stretch bounce physics and render ball with contact shadow."""
        state = self.physics.simulate_periodic(
            t=t,
            start_x=start_x,
            bounce_period=bounce_period,
            bounce_height=bounce_height,
            drift_vx=drift_vx,
        )

        # 1. Soft contact shadow on ground
        if state.shadow_alpha > 0.02:
            shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            d_s = ImageDraw.Draw(shadow_layer)
            s_rx = self.base_radius * 1.15 * state.shadow_scale
            s_ry = self.base_radius * 0.28 * state.shadow_scale
            s_alpha = int(75 * state.shadow_alpha)
            d_s.ellipse(
                [state.x - s_rx, FLOOR_Y - s_ry, state.x + s_rx, FLOOR_Y + s_ry],
                fill=(0, 0, 0, s_alpha),
            )
            if img.mode == "RGBA":
                img.alpha_composite(shadow_layer)
            else:
                base = img.convert("RGBA")
                base.alpha_composite(shadow_layer)
                img.paste(base.convert(img.mode))

        # 2. Draw ball
        blink_val = blink(t)
        self.draw_ball(
            img=img,
            cx=state.x,
            cy=state.y,
            rx=state.radius_x,
            ry=state.radius_y,
            mood=mood,
            blink_p=blink_val,
        )

        return state


# ---------------------------------------------------------------------------
# "Types Of..." Archetype Pill Badges
# ---------------------------------------------------------------------------
def draw_archetype_pill_badge(
    img: Image.Image,
    type_number: int,
    archetype_title: str,
    t_entrance: float,
    current_t: float,
    cx: float = WIDTH / 2,
    target_y: float = 85.0,
    accent_color: tuple[int, int, int] = ACCENT_CORAL,
) -> None:
    """Draw top archetype pill badge (e.g. 'TYPE 01: THE OVERPREPARER').

    Enters with overshoot pop-in (ease_out_back), dark glass panel, and neon border.
    """
    if current_t < t_entrance:
        return

    dt = current_t - t_entrance
    pop_p = ease_out_back(min(1.0, dt / 0.35))
    if pop_p <= 0.001:
        return

    # Floating hover bob
    hover_y = math.sin(current_t * 2.5) * 3.0
    effective_y = target_y * pop_p + hover_y

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    font_badge = load_font(26, bold=True)
    font_title = load_font(32, bold=True)

    prefix = f"TYPE {type_number:02d}:"
    w_prefix = d.textlength(prefix, font=font_badge)
    w_title = d.textlength(archetype_title, font=font_title)

    total_w = w_prefix + w_title + 80.0
    half_w = (total_w * pop_p) / 2.0
    half_h = 36.0 * pop_p

    box = [cx - half_w, effective_y - half_h, cx + half_w, effective_y + half_h]

    # Glass container background
    d.rounded_rectangle(box, radius=int(half_h), fill=GLASS_BG, outline=accent_color, width=3)  # fmt: skip

    # Text rendering
    if pop_p > 0.8:
        text_start_x = cx - (w_prefix + w_title + 20.0) / 2.0
        d.text((text_start_x, effective_y), prefix, font=font_badge, fill=accent_color, anchor="lm")  # fmt: skip
        d.text((text_start_x + w_prefix + 20.0, effective_y), archetype_title, font=font_title, fill=TEXT_PRIMARY, anchor="lm")  # fmt: skip

    if img.mode == "RGBA":
        img.alpha_composite(overlay)
    else:
        base = img.convert("RGBA")
        base.alpha_composite(overlay)
        img.paste(base.convert(img.mode))


# ---------------------------------------------------------------------------
# RPG Radar Stat Card
# ---------------------------------------------------------------------------
class RPGRadarCard:
    """Polygonal spider / radar stat chart with neon glow and animated deployment."""

    def __init__(
        self,
        stats: dict[str, float],
        title: str = "STATISTICS",
        width: float = 460.0,
        height: float = 440.0,
        accent_color: tuple[int, int, int] = ACCENT_TEAL,
        display: dict[str, str] | None = None,
    ) -> None:
        self.stats = stats
        self.title = title
        self.width = width
        self.height = height
        self.accent = accent_color
        # Optional label overrides, e.g. {"Caffeine": "999%", "Anxiety": "Max"}; the polygon
        # still uses the numeric stat, clamped to 0-100.
        self.display = display or {}

    def draw(
        self,
        img: Image.Image,
        center_x: float,
        center_y: float,
        reveal_progress: float,  # 0.0 to 1.0 (animated)
    ) -> None:
        """Render the RPG radar stat card onto img."""
        if reveal_progress <= 0.001:
            return

        prog = ease_out_elastic(min(1.0, reveal_progress))
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)

        half_w = (self.width * min(1.0, reveal_progress * 1.5)) / 2.0
        half_h = self.height / 2.0

        # Card glass frame
        card_box = [center_x - half_w, center_y - half_h, center_x + half_w, center_y + half_h]
        d.rounded_rectangle(card_box, radius=20, fill=GLASS_BG, outline=BG_SURFACE, width=2)

        # Title
        font_header = load_font(22, bold=True)
        d.text(
            (center_x, center_y - half_h + 32),
            self.title,
            font=font_header,
            fill=self.accent,
            anchor="mm",
        )

        # Chart center and radius
        chart_cx = center_x
        chart_cy = center_y + 20
        max_r = 130.0

        keys = list(self.stats.keys())
        n = len(keys)
        if n < 3:
            return

        # Concentric polygonal web lines (25%, 50%, 75%, 100%)
        angles = [-math.pi / 2 + 2.0 * math.pi * i / n for i in range(n)]
        web_levels = (0.25, 0.50, 0.75, 1.00)

        for lvl in web_levels:
            lvl_r = max_r * lvl
            web_pts = [
                (chart_cx + lvl_r * math.cos(a), chart_cy + lvl_r * math.sin(a)) for a in angles
            ]
            d.polygon(web_pts, outline=(50, 65, 110, 160), width=1)

        # Spoke axis lines
        for a in angles:
            x_end = chart_cx + max_r * math.cos(a)
            y_end = chart_cy + max_r * math.sin(a)
            d.line([(chart_cx, chart_cy), (x_end, y_end)], fill=(50, 65, 110, 160), width=1)

        # Stat polygon vertices scaled by reveal progress
        stat_pts: list[tuple[float, float]] = []
        for i, k in enumerate(keys):
            val = min(100.0, max(0.0, float(self.stats[k])))
            val_r = (val / 100.0) * max_r * prog
            px = chart_cx + val_r * math.cos(angles[i])
            py = chart_cy + val_r * math.sin(angles[i])
            stat_pts.append((px, py))

        # Draw translucent filled stat polygon
        fill_color = (*self.accent, 95)
        d.polygon(stat_pts, fill=fill_color, outline=self.accent, width=3)

        # Vertex dots & axis labels
        font_lbl = load_font(18, bold=True)
        for i, k in enumerate(keys):
            vx, vy = stat_pts[i]
            # Vertex circle
            d.ellipse([vx - 4, vy - 4, vx + 4, vy + 4], fill=WHITE, outline=self.accent, width=2)

            # Axis tip label
            tip_x = chart_cx + (max_r + 26) * math.cos(angles[i])
            tip_y = chart_cy + (max_r + 26) * math.sin(angles[i])
            val_text = self.display.get(k, str(int(round(self.stats[k]))))
            lbl_text = f"{k}: {val_text}"
            d.text((tip_x, tip_y), lbl_text, font=font_lbl, fill=TEXT_PRIMARY, anchor="mm")

        if img.mode == "RGBA":
            img.alpha_composite(overlay)
        else:
            base = img.convert("RGBA")
            base.alpha_composite(overlay)
            img.paste(base.convert(img.mode))


# ---------------------------------------------------------------------------
# "Types Of..." Meme Vignette Controller
# ---------------------------------------------------------------------------
TYPES_OF_MIN_ARCHETYPES = 3
TYPES_OF_MAX_ARCHETYPES = 5
TYPES_OF_MIN_SEGMENT_S = 6.0
TYPES_OF_MAX_SEGMENT_S = 8.0
TYPES_OF_SEGMENT_S = 7.0  # default segment length, inside the 6-8 s window
ESCALATION_FREEZE_S = 2.4  # offset of the impact freeze inside a segment


def clamp_archetype_count(count: int) -> int:
    """Clamp the number of archetypes in one 'Types Of...' Short to 3-5."""
    return max(TYPES_OF_MIN_ARCHETYPES, min(TYPES_OF_MAX_ARCHETYPES, int(count)))


def clamp_segment_seconds(seconds: float) -> float:
    """Clamp one archetype segment's length to 6-8 seconds."""
    return max(TYPES_OF_MIN_SEGMENT_S, min(TYPES_OF_MAX_SEGMENT_S, float(seconds)))


@dataclass(frozen=True)
class VignetteSlot:
    """One archetype's window on the Short timeline, in seconds."""

    index: int  # 0-based archetype index
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def local_time(self, t: float) -> float:
        return t - self.start


def plan_types_of_timeline(
    archetype_count: int,
    segment_seconds: float = TYPES_OF_SEGMENT_S,
    start_s: float = 0.0,
) -> list[VignetteSlot]:
    """Lay archetype segments back to back. Count is clamped to 3-5; length to 6-8 s."""
    seg = clamp_segment_seconds(segment_seconds)
    count = clamp_archetype_count(archetype_count)
    return [
        VignetteSlot(index=i, start=start_s + i * seg, end=start_s + (i + 1) * seg)
        for i in range(count)
    ]


def slot_at(slots: Sequence[VignetteSlot], t: float) -> VignetteSlot | None:
    """Return the slot active at time ``t`` (start inclusive, end exclusive), if any."""
    for slot in slots:
        if slot.start <= t < slot.end:
            return slot
    return None


@dataclass(frozen=True)
class Archetype:
    """Content for one 'Types Of...' archetype."""

    title: str
    tagline: str
    stats: dict[str, float]
    display: dict[str, str] = field(default_factory=dict)
    flag: CountryballFlag | str = CountryballFlag.POLAND


DEFAULT_ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        title="THE OVERTHINKER",
        tagline="Rewrote the opening line 40 times and still didn't send it.",
        stats={"Stamina": 12.0, "Caffeine": 100.0, "Anxiety": 100.0, "Speed": 15.0},
        display={"Caffeine": "999%", "Anxiety": "Max"},
        flag=CountryballFlag.GERMANY,
    ),
    Archetype(
        title="THE OVERPREPARER",
        tagline="Packed 4 backup laptops for a 10-minute presentation.",
        stats={"Preparation": 99.0, "Anxiety": 95.0, "Speed": 20.0, "Caffeine": 85.0},
        flag=CountryballFlag.POLAND,
    ),
    Archetype(
        title="THE NIGHT-BEFORE CRAMMER",
        tagline="Learned the whole syllabus between 2 AM and the exam.",
        stats={"Stamina": 40.0, "Caffeine": 100.0, "Focus": 10.0, "Panic": 90.0},
        display={"Caffeine": "999%", "Panic": "Max"},
        flag=CountryballFlag.UK,
    ),
    Archetype(
        title="THE INBOX ZERO SAINT",
        tagline="Replied to a 3-year-old email before the coffee was cold.",
        stats={"Order": 100.0, "Speed": 90.0, "Sleep": 5.0, "Chaos": 0.0},
        flag=CountryballFlag.JAPAN,
    ),
    Archetype(
        title="THE PERPETUAL 'JUST ONE MORE'",
        tagline="Said 'one more episode' at 4 AM. Seventh time this week.",
        stats={"Willpower": 0.0, "Snacks": 100.0, "Regret": 100.0, "Stamina": 60.0},
        display={"Regret": "Max"},
        flag=CountryballFlag.USA,
    ),
)


@dataclass
class TypesOfMemeOverlay:
    """Plays archetype vignettes over a timeline of a Short's frames.

    ``apply`` draws onto a frame at global time ``t`` if a segment is active there and
    leaves the frame untouched otherwise.
    """

    archetypes: Sequence[Archetype] = DEFAULT_ARCHETYPES
    archetype_count: int = 4
    segment_seconds: float = TYPES_OF_SEGMENT_S
    start_s: float = 0.0

    def __post_init__(self) -> None:
        self.archetype_count = clamp_archetype_count(self.archetype_count)
        self.segment_seconds = clamp_segment_seconds(self.segment_seconds)
        if not self.archetypes:
            raise ValueError("TypesOfMemeOverlay needs at least one archetype")
        self.slots = plan_types_of_timeline(
            self.archetype_count, self.segment_seconds, self.start_s
        )
        self._vignettes: dict[int, TypesOfMemeVignette] = {}

    def _vignette_for(self, slot: VignetteSlot) -> TypesOfMemeVignette:
        if slot.index not in self._vignettes:
            arc = self.archetypes[slot.index % len(self.archetypes)]
            cfg = VignetteConfig(
                type_number=slot.index + 1,
                archetype_title=arc.title,
                tagline=arc.tagline,
                stats=dict(arc.stats),
                flag=arc.flag,
                freeze_time=ESCALATION_FREEZE_S,
                total_duration=slot.duration,
                display=dict(arc.display),
            )
            self._vignettes[slot.index] = TypesOfMemeVignette(cfg)
        return self._vignettes[slot.index]

    def apply(self, img: Image.Image, t: float) -> bool:
        """Draw the active vignette onto ``img`` at global time ``t``. Returns True if drawn."""
        slot = slot_at(self.slots, t)
        if slot is None:
            return False
        self._vignette_for(slot).draw_frame(img, slot.local_time(t))
        return True


def resolve_meme_overlay(
    enabled: bool | None,
    archetype_count: int = 4,
    start_s: float = 0.0,
) -> TypesOfMemeOverlay | None:
    """Build the overlay when enabled. ``None`` reads AUTOSHORTS_MEME_ENGINE (default off)."""
    if enabled is None:
        enabled = os.environ.get("AUTOSHORTS_MEME_ENGINE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if not enabled:
        return None
    return TypesOfMemeOverlay(archetype_count=archetype_count, start_s=start_s)


@dataclass
class VignetteConfig:
    """Specification for a comedic 'Types Of...' meme segment."""

    type_number: int = 1
    archetype_title: str = "THE OVERPREPARER"
    tagline: str = "Packed 4 backup laptops for a 10-minute presentation."
    stats: dict[str, float] = field(
        default_factory=lambda: {
            "Preparation": 99.0,
            "Anxiety": 95.0,
            "Speed": 20.0,
            "Caffeine": 85.0,
            "Chaos": 40.0,
        }
    )
    flag: CountryballFlag | str = CountryballFlag.POLAND
    freeze_time: float = ESCALATION_FREEZE_S  # Escalation start: impact freeze + record stop
    total_duration: float = TYPES_OF_SEGMENT_S
    accent_color: tuple[int, int, int] = ACCENT_CORAL
    display: dict[str, str] = field(default_factory=dict)  # radar label overrides

    def __post_init__(self) -> None:
        self.total_duration = clamp_segment_seconds(self.total_duration)


class TypesOfMemeVignette:
    """Executes a full comedic freeze-frame vignette with sound sync.

    Timeline:
    1. 0.0s -> freeze_time: Action scenario (Countryball frantic bounce / pacing).
    2. freeze_time: Sudden instant freeze-frame, impact flash, dramatic vignette.
    3. freeze_time + 0.12s: Archetype pill badge slams in with overshoot.
    4. freeze_time + 0.35s: RPG radar stat card expands.
    """

    def __init__(self, config: VignetteConfig) -> None:
        self.cfg = config
        self.ball = ProceduralCountryball(flag=config.flag, base_radius=85.0)
        self.radar = RPGRadarCard(
            stats=config.stats,
            title="ARCHETYPE PROFILE",
            accent_color=config.accent_color,
            display=config.display,
        )

    def draw_frame(self, img: Image.Image, t: float) -> None:
        """Render the complete vignette frame at time t."""
        is_frozen = t >= self.cfg.freeze_time
        effective_ball_t = min(t, self.cfg.freeze_time)

        # 1. Action lines / dramatic flash at the freeze-frame moment
        if is_frozen and (t - self.cfg.freeze_time) < 0.25:
            flash_p = 1.0 - ((t - self.cfg.freeze_time) / 0.25)
            flash_overlay = Image.new("RGBA", img.size, (255, 255, 255, int(110 * flash_p)))
            if img.mode == "RGBA":
                img.alpha_composite(flash_overlay)
            else:
                base = img.convert("RGBA")
                base.alpha_composite(flash_overlay)
                img.paste(base.convert(img.mode))

        # 2. Render Countryball
        # Character moves towards left side to make room for RPG Card on right
        ball_mood = EyeMood.SHOCKED if is_frozen else EyeMood.HAPPY
        ball_x = 540.0
        self.ball.render_bounce_frame(
            img=img,
            t=effective_ball_t,
            start_x=ball_x,
            mood=ball_mood,
            bounce_period=0.68,
            bounce_height=170.0,
        )

        # 3. Top Archetype Pill Badge
        badge_entrance = self.cfg.freeze_time + 0.12
        if t >= badge_entrance:
            draw_archetype_pill_badge(
                img=img,
                type_number=self.cfg.type_number,
                archetype_title=self.cfg.archetype_title,
                t_entrance=badge_entrance,
                current_t=t,
                cx=WIDTH / 2,
                target_y=95.0,
                accent_color=self.cfg.accent_color,
            )

        # 4. RPG Radar Stat Card
        radar_entrance = self.cfg.freeze_time + 0.35
        if t >= radar_entrance:
            reveal_p = min(1.0, (t - radar_entrance) / 0.65)
            card_cx = 1420.0
            card_cy = 520.0
            self.radar.draw(
                img=img,
                center_x=card_cx,
                center_y=card_cy,
                reveal_progress=reveal_p,
            )

        # 5. Tagline subtitle card under countryball
        if is_frozen and t > self.cfg.freeze_time + 0.6:
            font_sub = load_font(24, bold=False)
            d = ImageDraw.Draw(img)
            tag_box = [200, 840, 880, 910]
            d.rounded_rectangle(tag_box, radius=12, fill=BG_SURFACE, outline=BG_DEEP, width=2)
            d.text((540, 875), f'"{self.cfg.tagline}"', font=font_sub, fill=TEXT_PRIMARY, anchor="mm")  # fmt: skip

    def generate_sfx_events(
        self,
        work_dir: Path,
    ) -> list[tuple[Path, float]]:
        """Synthesize synchronized sound effects and return timeline cues."""
        work_dir = Path(work_dir)
        sfx_dir = work_dir / "meme_sfx"
        sfx_dir.mkdir(parents=True, exist_ok=True)

        events: list[tuple[Path, float]] = []

        # 1. Entrance whoosh
        p_whoosh = sfx_dir / f"whoosh_entrance_{self.cfg.type_number}.wav"
        synth_whoosh(p_whoosh, duration=0.28)
        events.append((p_whoosh, 0.05))

        # 2. Freeze-frame crack / record scratch
        p_crack = sfx_dir / f"freeze_crack_{self.cfg.type_number}.wav"
        synth_glass_crack(p_crack, duration=0.35)
        events.append((p_crack, self.cfg.freeze_time))

        # 3. Badge slam impact punch
        p_punch = sfx_dir / f"badge_slam_{self.cfg.type_number}.wav"
        synth_punch(p_punch, duration=0.32)
        events.append((p_punch, self.cfg.freeze_time + 0.12))

        # 4. RPG Radar deployment tick / alarm ping
        p_ping = sfx_dir / f"radar_reveal_{self.cfg.type_number}.wav"
        synth_alarm(p_ping, duration=0.22, freq=920.0)
        events.append((p_ping, self.cfg.freeze_time + 0.45))

        return events
