"""Shared visual design tokens for longform, Shorts captions, thumbnails and Remotion.

Every renderer imports colours, fonts and easing from here so the look stays
consistent. Remotion mirrors these values in remotion/src/theme.ts.
"""

from __future__ import annotations

from collections.abc import Callable

RGB = tuple[int, int, int]

# Backgrounds: deep navy with a lighter surface for cards and panels.
BG_DEEP: RGB = (14, 18, 38)
BG_SURFACE: RGB = (28, 34, 62)

# Flat accent palette (Kurzgesagt-like warm and cool pairs, no neon glow).
ACCENT_YELLOW: RGB = (255, 200, 60)
ACCENT_TEAL: RGB = (60, 200, 200)
ACCENT_CORAL: RGB = (255, 110, 100)
ACCENT_PURPLE: RGB = (150, 110, 230)
ACCENT_GREEN: RGB = (120, 210, 140)

TEXT_PRIMARY: RGB = (245, 247, 255)
TEXT_MUTED: RGB = (160, 170, 200)

# Navy atmosphere: deep navy replaces pure black for longform backdrops.
NAVY_DEEP: RGB = (18, 24, 52)
NAVY: RGB = (26, 36, 72)
NAVY_LIGHT: RGB = (40, 56, 104)
TEAL: RGB = (42, 184, 176)
YELLOW_SOFT: RGB = (246, 200, 72)
CORAL: RGB = (232, 122, 118)
PURPLE: RGB = (120, 100, 190)
OFFWHITE: RGB = (242, 240, 235)

# Font stacks: first available wins. Display for titles and captions, body for text.
FONT_DISPLAY: tuple[str, ...] = ("Montserrat", "Arial")
FONT_BODY: tuple[str, ...] = ("Arial",)

# Motion timing (seconds) shared by scene transitions and caption pops.
MOTION_FAST = 0.18
MOTION_BASE = 0.35
MOTION_SLOW = 0.6


def ease_in_out_cubic(t: float) -> float:
    """Smooth start and stop, used for scene transitions and camera moves."""
    t = min(1.0, max(0.0, t))
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def ease_out_cubic(t: float) -> float:
    """Fast start, gentle landing, used for entrances."""
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3


EASINGS: dict[str, Callable[[float], float]] = {
    "in_out": ease_in_out_cubic,
    "out": ease_out_cubic,
}


def lerp_rgb(a: RGB, b: RGB, t: float) -> RGB:
    """Blend two colours; used for flat-gradient fills."""
    t = min(1.0, max(0.0, t))
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )
