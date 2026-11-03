"""Easing and animation helpers for the long-form explainer rig.

Every function here is a pure function of time or progress: the same input always
gives the same output, and nothing reads a clock or keeps state. Progress values
(``p``) are clamped to 0..1 unless noted otherwise.
"""

from __future__ import annotations

import math

_TAU = 2.0 * math.pi

# Blink timing: one blink per period, centred on this fraction of the period, lasting
# BLINK_DURATION seconds in total.
BLINK_CENTER_FRACTION = 0.62
BLINK_DURATION = 0.16


def _clamp01(p: float) -> float:
    return 0.0 if p < 0.0 else 1.0 if p > 1.0 else p


def ease_out_back(p: float, overshoot: float = 1.70158) -> float:
    """Ease out with a small overshoot past 1 before settling at 1."""
    p = _clamp01(p)
    c3 = overshoot + 1.0
    q = p - 1.0
    return 1.0 + c3 * q * q * q + overshoot * q * q


def ease_out_elastic(p: float) -> float:
    """Ease out with decaying elastic wobble around the target."""
    p = _clamp01(p)
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    c4 = _TAU / 3.0
    return 2.0 ** (-10.0 * p) * math.sin((p * 10.0 - 0.75) * c4) + 1.0


def ease_out_bounce(p: float) -> float:
    """Ease out with a bouncing settle, like a ball dropping onto a floor."""
    p = _clamp01(p)
    n1 = 7.5625
    d1 = 2.75
    if p < 1.0 / d1:
        return n1 * p * p
    if p < 2.0 / d1:
        p -= 1.5 / d1
        return n1 * p * p + 0.75
    if p < 2.5 / d1:
        p -= 2.25 / d1
        return n1 * p * p + 0.9375
    p -= 2.625 / d1
    return n1 * p * p + 0.984375


def ease_in_out_sine(p: float) -> float:
    """Smooth start and stop using a half cosine."""
    p = _clamp01(p)
    return -(math.cos(math.pi * p) - 1.0) / 2.0


def anticipation(p: float) -> float:
    """Small dip below zero first (about 12 percent), then a smooth go up to 1."""
    p = _clamp01(p)
    dip_end = 0.3
    dip = 0.12
    if p <= dip_end:
        return -dip * ease_in_out_sine(p / dip_end)
    return -dip + (1.0 + dip) * ease_in_out_sine((p - dip_end) / (1.0 - dip_end))


def pop_in(t: float, start: float, dur: float = 0.35) -> float:
    """Scale from 0 to 1 with an overshoot. Zero before ``start``, one from ``start + dur``."""
    if t < start:
        return 0.0
    if dur <= 0.0:
        return 1.0
    return ease_out_back((t - start) / dur)


def drop_in(t: float, start: float, dur: float = 0.5, height: float = 120.0) -> float:
    """Vertical offset that falls from ``height`` and bounces to rest at 0."""
    if t < start:
        return height
    if dur <= 0.0:
        return 0.0
    return height * (1.0 - ease_out_bounce((t - start) / dur))


def draw_on(t: float, start: float, dur: float) -> float:
    """Fraction of a line or path that is drawn at time ``t`` (0 before, 1 after)."""
    if t < start:
        return 0.0
    if dur <= 0.0:
        return 1.0
    return _clamp01((t - start) / dur)


def squash_stretch(
    base_w: float, base_h: float, velocity: float, k: float = 0.08
) -> tuple[float, float]:
    """Stretch height and squeeze width in proportion to speed.

    The stretch factor is ``f = 1 + clamp(k * |velocity|, 0, 0.5)``. Height is scaled by
    ``f`` and width by ``1 / f``, so the area ``w * h`` stays equal to the base area.
    """
    f = 1.0 + min(0.5, max(0.0, k * abs(velocity)))
    return base_w / f, base_h * f


def blink(t: float, period: float = 3.4, closed: float = 0.12) -> float:
    """Eye openness factor: 1.0 when open, ``closed`` at the blink instant.

    Each period holds one short blink centred on ``BLINK_CENTER_FRACTION`` of the
    period. The eye closes and reopens with a sine ease across ``BLINK_DURATION``.
    """
    if period <= 0.0:
        return 1.0
    phase = t % period
    centre = period * BLINK_CENTER_FRACTION
    dist = abs(phase - centre)
    dist = min(dist, period - dist)  # wrap around the period boundary
    half = BLINK_DURATION / 2.0
    if dist >= half:
        return 1.0
    return closed + (1.0 - closed) * ease_in_out_sine(dist / half)


def breathe(t: float, period: float = 2.6, amp: float = 0.012) -> float:
    """Scale factor around 1.0 that rises and falls by at most ``amp``."""
    if period <= 0.0:
        return 1.0
    return 1.0 + amp * math.sin(_TAU * t / period)


def parallax_offset(camera_x: float, depth: float) -> float:
    """Horizontal shift for a layer at ``depth`` (0 = far, 1 = near) under camera pan."""
    return camera_x * depth
