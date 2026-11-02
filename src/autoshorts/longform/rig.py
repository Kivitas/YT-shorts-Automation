"""Cinematic flat-illustrated figure rig for longform scenes.

The figure has thick dark outlines, a cream body with a grey shaded back side, an
optional red headband with fluttering ribbons, and a face that can read as
exhausted, gritted, neutral or a shadowy smirk.

Pose format
-----------
A pose is a ``dict[str, float]`` of joint angles in degrees:

* ``torso`` and ``head``: 0 points straight up, +90 points toward +x (screen right).
* ``l_upper``, ``l_lower``, ``l_thigh``, ``l_shin`` (and the ``r_`` twins): absolute
  segment angles, 0 straight down, +90 toward +x, 180 straight up.
* ``drop``: hip shift in body units, positive is down.

Keys missing from a pose fall back to the ``stand`` pose. This module is
self-contained and does not import ``drawer.py``.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

CREAM = (234, 230, 223)
CREAM_SHADE = (184, 180, 172)
INK = (16, 16, 22)
HEADBAND = (230, 57, 70)

# Segment lengths and widths in body units; the renderer multiplies them by scale.
TORSO_LEN = 90.0
NECK_LEN = 26.0
HEAD_R = 30.0
UPPER_LEN = 50.0
LOWER_LEN = 48.0
THIGH_LEN = 56.0
SHIN_LEN = 52.0
TORSO_W = 34.0
NECK_W = 14.0
ARM_W = 13.0
LEG_W = 16.0

_STAND_KEYS = (
    "torso", "head",
    "l_upper", "l_lower", "r_upper", "r_lower",
    "l_thigh", "l_shin", "r_thigh", "r_shin",
    "drop",
)  # fmt: skip

POSES: dict[str, dict[str, float]] = {
    "stand": {
        "torso": 0,
        "head": 0,
        "l_upper": -12,
        "l_lower": -14,
        "r_upper": 12,
        "r_lower": 14,
        "l_thigh": -8,
        "l_shin": -6,
        "r_thigh": 8,
        "r_shin": 6,
        "drop": 0,
    },
    "point": {
        "torso": 4,
        "head": 8,
        "l_upper": -12,
        "l_lower": -14,
        "r_upper": 105,
        "r_lower": 115,
    },
    "sleep_bed": {
        "torso": 90,
        "head": 90,
        "l_upper": 100,
        "l_lower": 110,
        "r_upper": 80,
        "r_lower": 95,
        "l_thigh": -88,
        "l_shin": -92,
        "r_thigh": -88,
        "r_shin": -86,
    },
    "slump_couch": {
        "torso": 18,
        "head": 38,
        "l_upper": 62,
        "l_lower": 96,
        "r_upper": 50,
        "r_lower": 80,
        "l_thigh": 88,
        "l_shin": 6,
        "r_thigh": 86,
        "r_shin": 4,
    },
    "slump_desk": {
        "torso": 62,
        "head": 80,
        "l_upper": 78,
        "l_lower": 96,
        "r_upper": 70,
        "r_lower": 92,
        "l_thigh": 88,
        "l_shin": 4,
        "r_thigh": 86,
        "r_shin": 2,
    },
    "crouch_despair": {
        "torso": 28,
        "head": 62,
        "l_upper": 40,
        "l_lower": 62,
        "r_upper": 34,
        "r_lower": 56,
        "l_thigh": 58,
        "l_shin": -12,
        "r_thigh": 50,
        "r_shin": -8,
        "drop": 22,
    },
    "pushup_up": {
        "torso": 92,
        "head": 92,
        "l_upper": 2,
        "l_lower": 0,
        "r_upper": 2,
        "r_lower": 0,
        "l_thigh": -28,
        "l_shin": -22,
        "r_thigh": -30,
        "r_shin": -24,
    },
    "pushup_down": {
        "torso": 92,
        "head": 92,
        "l_upper": -45,
        "l_lower": -8,
        "r_upper": -45,
        "r_lower": -8,
        "l_thigh": -28,
        "l_shin": -22,
        "r_thigh": -30,
        "r_shin": -24,
        "drop": 16,
    },
    "box_guard": {
        "torso": 6,
        "head": -4,
        "l_upper": -100,
        "l_lower": 150,
        "r_upper": 100,
        "r_lower": -150,
    },
    "box_punch": {
        "torso": 14,
        "head": -2,
        "l_upper": -100,
        "l_lower": 150,
        "r_upper": 90,
        "r_lower": 92,
    },
    "climb_slope": {
        "torso": 28,
        "head": 20,
        "l_upper": 150,
        "l_lower": 170,
        "r_upper": 35,
        "r_lower": 55,
        "l_thigh": 55,
        "l_shin": 25,
        "r_thigh": -20,
        "r_shin": -35,
    },
    "summit_stand": {
        "torso": 0,
        "head": 0,
        "l_upper": -150,
        "l_lower": -172,
        "r_upper": 150,
        "r_lower": 172,
        "l_thigh": -12,
        "l_shin": -10,
        "r_thigh": 12,
        "r_shin": 10,
    },
}

# Face parameters. lid: 0 open to 1 shut. brow: degrees, positive = inner end down
# (angry), negative = inner end up (worried). curve: mouth, -1 frown to +1 smile.
EXPRESSIONS: dict[str, dict[str, float]] = {
    "neutral": {"lid": 0.0, "brow": 0.0, "curve": 0.2, "bags": 0, "teeth": 0,
                "vein": 0, "glow": 0, "smirk": 0},
    "exhausted": {"lid": 0.55, "brow": -10.0, "curve": -0.6, "bags": 1, "teeth": 0,
                  "vein": 0, "glow": 0, "smirk": 0},
    "grit": {"lid": 0.0, "brow": 28.0, "curve": 0.0, "bags": 0, "teeth": 1,
             "vein": 1, "glow": 0, "smirk": 0},
    "shadow_smirk": {"lid": 0.35, "brow": 14.0, "curve": 0.0, "bags": 0, "teeth": 0,
                     "vein": 0, "glow": 1, "smirk": 1},
}  # fmt: skip

_HALO_ALPHA = (HEADBAND[0], HEADBAND[1], HEADBAND[2], 80)


def _resolve_pose(name: str) -> dict[str, float]:
    """Merge a named pose over the stand defaults so every joint has a value."""
    joints = {k: 0.0 for k in _STAND_KEYS}
    joints.update({k: float(v) for k, v in POSES["stand"].items()})
    joints.update({k: float(v) for k, v in POSES[name].items()})
    return joints


def _up(base: tuple[float, float], deg: float, length: float, s: float) -> tuple[float, float]:
    """Point ``length`` units from ``base`` along an angle measured from straight up."""
    a = math.radians(deg)
    return (base[0] + length * s * math.sin(a), base[1] - length * s * math.cos(a))


def _down(base: tuple[float, float], deg: float, length: float, s: float) -> tuple[float, float]:
    """Point ``length`` units from ``base`` along an angle measured from straight down."""
    a = math.radians(deg)
    return (base[0] + length * s * math.sin(a), base[1] + length * s * math.cos(a))


def _capsule(
    draw: ImageDraw.ImageDraw,
    a: tuple[float, float],
    b: tuple[float, float],
    width: float,
    fill: tuple[int, ...],
) -> None:
    """Round-capped thick line segment."""
    draw.line([a, b], fill=fill, width=max(1, round(width)))
    r = width / 2
    for x, y in (a, b):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def _limb(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    width: float,
    fill: tuple[int, ...],
    ow: float,
) -> None:
    """Outline pass for every segment first, then fill, so joints stay clean."""
    segs = list(zip(pts, pts[1:], strict=False))
    for a, b in segs:
        _capsule(draw, a, b, width + 2 * ow, INK)
    for a, b in segs:
        _capsule(draw, a, b, width, fill)


def _disc(
    draw: ImageDraw.ImageDraw, c: tuple[float, float], r: float, fill: tuple[int, ...]
) -> None:
    draw.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=fill)


def _outlined_poly(
    draw: ImageDraw.ImageDraw,
    pts: list[tuple[float, float]],
    fill: tuple[int, ...],
    ow: float,
) -> None:
    """Fill a polygon with a thick INK outline made by stamping it around its edge."""
    for k in range(12):
        a = 2 * math.pi * k / 12
        dx, dy = ow * math.cos(a), ow * math.sin(a)
        draw.polygon([(x + dx, y + dy) for x, y in pts], fill=INK)
    draw.polygon(pts, fill=fill)


def _render(
    draw: ImageDraw.ImageDraw,
    joints: dict[str, float],
    origin: tuple[float, float],
    scale: float,
    expr: dict[str, float],
    headband: bool,
    t: float,
) -> None:
    s = scale
    ow = max(2.0, 3.5 * s)
    ox, oy = origin
    hip = (ox, oy + joints["drop"] * s)
    top = _up(hip, joints["torso"], TORSO_LEN, s)
    head_c = _up(top, joints["head"], NECK_LEN + HEAD_R, s)
    R = HEAD_R * s

    knee_r = _down(hip, joints["r_thigh"], THIGH_LEN, s)
    knee_l = _down(hip, joints["l_thigh"], THIGH_LEN, s)
    leg_r = [hip, knee_r, _down(knee_r, joints["r_shin"], SHIN_LEN, s)]
    leg_l = [hip, knee_l, _down(knee_l, joints["l_shin"], SHIN_LEN, s)]
    elb_r = _down(top, joints["r_upper"], UPPER_LEN, s)
    elb_l = _down(top, joints["l_upper"], UPPER_LEN, s)
    arm_r = [top, elb_r, _down(elb_r, joints["r_lower"], LOWER_LEN, s)]
    arm_l = [top, elb_l, _down(elb_l, joints["l_lower"], LOWER_LEN, s)]

    # Back side (screen right) is shaded: far leg and far arm are CREAM_SHADE.
    _limb(draw, leg_r, LEG_W * s, CREAM_SHADE, ow)
    _limb(draw, leg_l, LEG_W * s, CREAM, ow)
    _limb(draw, arm_r, ARM_W * s, CREAM_SHADE, ow)

    # Torso with a shaded strip along its back edge.
    tw = TORSO_W * s
    _capsule(draw, hip, top, tw + 2 * ow, INK)
    _capsule(draw, hip, top, tw, CREAM)
    dx, dy = math.cos(math.radians(joints["torso"])), math.sin(math.radians(joints["torso"]))
    off = 0.28 * tw
    strip_a = (hip[0] + dx * off, hip[1] + dy * off)
    strip_b = (top[0] + dx * off, top[1] + dy * off)
    _capsule(draw, strip_a, strip_b, 0.36 * tw, CREAM_SHADE)

    # Neck, headband tails (behind the head), then the head.
    _capsule(draw, top, head_c, NECK_W * s + 2 * ow, INK)
    _capsule(draw, top, head_c, NECK_W * s, CREAM)

    h_rad = math.radians(joints["head"])
    hc_cos, hc_sin = math.cos(h_rad), math.sin(h_rad)

    def hp(u: float, v: float) -> tuple[float, float]:
        """Head-local (u right, v down, in head radii) to canvas coordinates."""
        return (
            head_c[0] + R * (u * hc_cos - v * hc_sin),
            head_c[1] + R * (u * hc_sin + v * hc_cos),
        )

    if headband:
        wave1 = math.sin(t * 14.0) * 0.2 * R
        wave2 = math.sin(t * 14.0 + 1.9) * 0.2 * R
        for shift, length, wave in ((0.0, 1.1 * R, wave1), (0.14 * R, 0.9 * R, wave2)):
            bt = hp(-0.8, -0.66)
            bt = (bt[0], bt[1] + shift)
            bb = hp(-0.86, -0.5)
            bb = (bb[0], bb[1] + shift)
            tail = [
                bt,
                (bt[0] - 0.5 * length, bt[1] + 0.15 * R + wave * 0.5),
                (bt[0] - length, bt[1] + 0.3 * R + wave),
                (bb[0] - 0.9 * length, bb[1] + 0.5 * R + wave * 1.2),
                (bb[0] - 0.4 * length, bb[1] + 0.2 * R + wave * 0.5),
                bb,
            ]
            _outlined_poly(draw, tail, HEADBAND, ow * 0.8)

    draw.ellipse(
        [head_c[0] - R - ow, head_c[1] - R - ow, head_c[0] + R + ow, head_c[1] + R + ow], fill=INK
    )
    draw.ellipse([head_c[0] - R, head_c[1] - R, head_c[0] + R, head_c[1] + R], fill=CREAM)

    if headband:
        band = [hp(-0.69, -0.72), hp(0.69, -0.72), hp(0.86, -0.5), hp(-0.86, -0.5)]
        _outlined_poly(draw, band, HEADBAND, ow * 0.7)

    _draw_face(draw, hp, R, expr)

    # Near arm sits over the torso and head.
    _limb(draw, arm_l, ARM_W * s, CREAM, ow)


def _draw_face(
    draw: ImageDraw.ImageDraw,
    hp,
    R: float,
    expr: dict[str, float],
) -> None:
    lw = max(2, round(0.07 * R))
    line_w = max(2, round(0.05 * R))
    lid = float(expr["lid"])
    glow = expr["glow"] > 0.5

    for ue in (-0.36, 0.36):
        eye = hp(ue, -0.08)
        if glow:
            _disc(draw, eye, 0.34 * R, _HALO_ALPHA)
            _disc(draw, eye, 0.15 * R, HEADBAND)
        else:
            _disc(draw, eye, 0.12 * R, INK)

        if lid > 0:
            edge = -0.32 + lid * 0.46
            lid_poly = [
                hp(ue - 0.2, -0.34),
                hp(ue + 0.2, -0.34),
                hp(ue + 0.2, edge),
                hp(ue - 0.2, edge),
            ]
            draw.polygon(lid_poly, fill=CREAM)
            draw.line([hp(ue - 0.2, edge), hp(ue + 0.2, edge)], fill=INK, width=lw)

        if expr["bags"] > 0.5:
            c = hp(ue, 0.22)
            draw.ellipse(
                [c[0] - 0.17 * R, c[1] - 0.06 * R, c[0] + 0.17 * R, c[1] + 0.06 * R],
                fill=CREAM_SHADE,
            )

        side = -1.0 if ue < 0 else 1.0
        inner_u = ue - side * 0.2
        outer_u = ue + side * 0.2
        sin_b = math.sin(math.radians(expr["brow"]))
        inner = hp(inner_u, -0.28 + 0.3 * sin_b)
        outer = hp(outer_u, -0.28 - 0.3 * sin_b)
        draw.line([inner, outer], fill=INK, width=lw)

    if expr["teeth"] > 0.5:
        box = [hp(-0.3, 0.37), hp(0.3, 0.37), hp(0.3, 0.53), hp(-0.3, 0.53)]
        draw.polygon(box, fill=CREAM)
        draw.line(box + [box[0]], fill=INK, width=line_w)
        for u in (-0.1, 0.1):
            draw.line([hp(u, 0.37), hp(u, 0.53)], fill=INK, width=line_w)
        draw.line([hp(-0.3, 0.45), hp(0.3, 0.45)], fill=INK, width=line_w)
    elif expr["smirk"] > 0.5:
        draw.line(
            [hp(-0.02, 0.44), hp(0.12, 0.42), hp(0.24, 0.34)],
            fill=HEADBAND,
            width=lw,
            joint="curve",
        )
    else:
        curve = float(expr["curve"])
        mouth = []
        for i in range(9):
            u = -0.2 + 0.4 * i / 8
            v = 0.44 + curve * 0.1 * (1 - (u / 0.2) ** 2)
            mouth.append(hp(u, v))
        draw.line(mouth, fill=INK, width=lw, joint="curve")

    if expr["vein"] > 0.5:
        draw.line(
            [hp(0.2, -0.52), hp(0.28, -0.44), hp(0.36, -0.5)], fill=INK, width=line_w, joint="curve"
        )
        draw.line([hp(0.28, -0.44), hp(0.3, -0.36)], fill=INK, width=line_w)


def _composite(img: Image.Image, layer: Image.Image) -> None:
    """Alpha-composite the figure layer onto ``img`` in place, keeping its mode."""
    if img.mode == "RGBA":
        img.alpha_composite(layer)
        return
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    img.paste(base.convert(img.mode))


def draw_rig(
    img: Image.Image,
    pose: str,
    origin: tuple[float, float],
    scale: float,
    expression: str = "neutral",
    headband: bool = False,
    t: float = 0.0,
) -> None:
    """Draw the figure onto ``img`` in place.

    ``origin`` is the hip in pixels and ``scale`` multiplies body units. Unknown pose
    or expression names fall back to ``stand`` and ``neutral``. ``t`` drives the
    headband ribbon flutter.
    """
    pose_name = pose if pose in POSES else "stand"
    expr_name = expression if expression in EXPRESSIONS else "neutral"
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    _render(
        draw,
        _resolve_pose(pose_name),
        origin,
        scale,
        EXPRESSIONS[expr_name],
        headband,
        t,
    )
    _composite(img, layer)
