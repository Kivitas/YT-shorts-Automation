"""Flat-illustrated explainer figure rig for longform scenes (Kurzgesagt-like).

The figure has a rounded cream body with a grey shaded back side, a shirt with a
collar, three-finger mitt hands, shoes, a soft ground shadow, and a face with
pupils, eyelids, highlights, brows and several mouth shapes. An optional red
headband with fluttering ribbons is available, and the whole figure can be
mirrored for variety.

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
from collections.abc import Callable

from PIL import Image, ImageDraw

from autoshorts.longform.motion import blink, breathe, ease_in_out_sine

CREAM = (234, 230, 223)
CREAM_SHADE = (184, 180, 172)
EYE_WHITE = (251, 250, 247)
MOUTH = (92, 30, 40)
BLUSH = (238, 118, 128, 110)
SHIRT = (44, 122, 132)
SHIRT_SHADE = (31, 92, 101)
SHOE = (52, 40, 56)
SHOE_SHADE = (34, 26, 38)
SHADOW = (0, 0, 0, 70)
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

_Point = tuple[float, float]
_Map = Callable[[float, float], _Point]

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
    "sit_chair": {
        "torso": 6,
        "head": 2,
        "l_upper": 50,
        "l_lower": 105,
        "r_upper": 40,
        "r_lower": 100,
        "l_thigh": 92,
        "l_shin": 2,
        "r_thigh": 88,
        "r_shin": 0,
    },
    "lean_wall": {
        "torso": 16,
        "head": 8,
        "l_upper": 95,
        "l_lower": 150,
        "r_upper": -20,
        "r_lower": -40,
        "l_thigh": 18,
        "l_shin": -6,
        "r_thigh": 6,
        "r_shin": 8,
    },
    "hug_knees": {
        "torso": 25,
        "head": 40,
        "l_upper": 70,
        "l_lower": 100,
        "r_upper": 60,
        "r_lower": 110,
        "l_thigh": 110,
        "l_shin": -30,
        "r_thigh": 106,
        "r_shin": -20,
        "drop": 12,
    },
    "fist_up": {
        "torso": 0,
        "head": 0,
        "r_upper": 165,
        "r_lower": 178,
        "l_upper": -14,
        "l_lower": -18,
        "l_thigh": -8,
        "l_shin": -6,
        "r_thigh": 8,
        "r_shin": 6,
    },
    "reach_up": {
        "torso": 0,
        "head": -4,
        "l_upper": -160,
        "l_lower": -176,
        "r_upper": 160,
        "r_lower": 176,
    },
    "fall_back": {
        "torso": -58,
        "head": -30,
        "l_upper": -150,
        "l_lower": -175,
        "r_upper": 130,
        "r_lower": 165,
        "l_thigh": 25,
        "l_shin": -5,
        "r_thigh": 45,
        "r_shin": 15,
        "drop": 0.5,
    },
    "gesture_point_up": {
        "torso": 2,
        "head": 4,
        "r_upper": 150,
        "r_lower": 176,
    },
    "gesture_open_hands": {
        "torso": 0,
        "head": 2,
        "l_upper": -65,
        "l_lower": -95,
        "r_upper": 65,
        "r_lower": 95,
    },
    "gesture_shrug": {
        "torso": 0,
        "head": 6,
        "l_upper": -40,
        "l_lower": -100,
        "r_upper": 40,
        "r_lower": 100,
    },
}

# Look poses keep the stand body. ``head_turn`` is a pose-level key: it shifts the head
# sideways, the same as the ``head_turn`` argument of :func:`draw_rig`.
POSES["look_left"] = {**POSES["stand"], "head_turn": -1.0}
POSES["look_right"] = {**POSES["stand"], "head_turn": 1.0}

# Face parameters. lid: 0 open to 1 shut. brow: degrees, positive = inner end down
# (angry), negative = inner end up (worried). curve: mouth, -1 frown to +1 smile.
# open: mouth oval height, 0 closed. squint: 1 turns the eyes into happy arcs.
# look: pupil shift, -1 left to +1 right. blush, bags, teeth, vein, glow, smirk: 0/1.
_EXPR_DEFAULTS: dict[str, float] = {
    "lid": 0.0, "brow": 0.0, "curve": 0.0, "bags": 0, "teeth": 0, "vein": 0,
    "glow": 0, "smirk": 0, "open": 0, "blush": 0, "squint": 0, "look": 0.0,
}  # fmt: skip


def _expr(**overrides: float) -> dict[str, float]:
    values = dict(_EXPR_DEFAULTS)
    values.update(overrides)
    return values


EXPRESSIONS: dict[str, dict[str, float]] = {
    "neutral": _expr(curve=0.0),
    "exhausted": _expr(lid=0.55, brow=-10.0, curve=-0.6, bags=1, look=-0.3),
    "grit": _expr(brow=28.0, teeth=1, vein=1),
    "shadow_smirk": _expr(lid=0.35, brow=14.0, glow=1, smirk=1, look=0.4),
    "happy": _expr(squint=1, curve=1.0, open=1.0, blush=1, brow=-8.0),
    "worried": _expr(brow=-22.0, curve=-0.5, open=0.5, look=-0.4),
    "determined": _expr(lid=0.12, brow=22.0, curve=0.0, look=0.25),
    "tired_smile": _expr(lid=0.5, brow=-4.0, curve=0.5, bags=1, blush=1),
}

_HALO_ALPHA = (HEADBAND[0], HEADBAND[1], HEADBAND[2], 80)

# How far a full head turn (head_turn = 1) moves the head sideways, in px at scale 1,
# and how far it tilts the head, in degrees.
HEAD_TURN_PX = 10.0
HEAD_TURN_TILT_DEG = 6.0
HAND_LIFT_DEG = 40.0


def _resolve_pose(name: str | dict[str, float]) -> dict[str, float]:
    """Merge a named pose (or a joint dict) over the stand defaults so every joint has a value."""
    joints = {k: 0.0 for k in _STAND_KEYS}
    joints["head_turn"] = 0.0
    joints.update({k: float(v) for k, v in POSES["stand"].items()})
    source = POSES[name] if isinstance(name, str) else name
    joints.update({k: float(v) for k, v in source.items()})
    return joints


def animate_pose(pose_name_a: str, pose_name_b: str, p: float, t: float = 0.0) -> dict[str, float]:
    """Joint angles interpolated from pose A to pose B.

    ``p`` is the progress from 0 (pose A) to 1 (pose B), eased with ease_in_out_sine.
    Unknown names fall back to ``stand``. ``t`` adds a slow idle sway to the head, so
    a held pose still looks alive. At ``t=0.0`` the sway is zero. The result can be
    passed straight to :func:`draw_rig` as its ``pose`` argument.
    """
    a = _resolve_pose(pose_name_a if pose_name_a in POSES else "stand")
    b = _resolve_pose(pose_name_b if pose_name_b in POSES else "stand")
    e = ease_in_out_sine(p)
    joints = {k: a[k] + (b[k] - a[k]) * e for k in (*_STAND_KEYS, "head_turn")}
    joints["head"] += 1.5 * math.sin(2.0 * math.pi * t / 3.1)
    return joints


def _up(base: _Point, deg: float, length: float, s: float) -> _Point:
    """Point ``length`` units from ``base`` along an angle measured from straight up."""
    a = math.radians(deg)
    return (base[0] + length * s * math.sin(a), base[1] - length * s * math.cos(a))


def _down(base: _Point, deg: float, length: float, s: float) -> _Point:
    """Point ``length`` units from ``base`` along an angle measured from straight down."""
    a = math.radians(deg)
    return (base[0] + length * s * math.sin(a), base[1] + length * s * math.cos(a))


def _rot(v: _Point, deg: float) -> _Point:
    a = math.radians(deg)
    return (v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a))


def _capsule(
    draw: ImageDraw.ImageDraw,
    a: _Point,
    b: _Point,
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
    pts: list[_Point],
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


def _disc(draw: ImageDraw.ImageDraw, c: _Point, r: float, fill: tuple[int, ...]) -> None:
    draw.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=fill)


def _outlined_poly(
    draw: ImageDraw.ImageDraw,
    pts: list[_Point],
    fill: tuple[int, ...],
    ow: float,
) -> None:
    """Fill a polygon with a thick INK outline made by stamping it around its edge."""
    for k in range(12):
        a = 2 * math.pi * k / 12
        dx, dy = ow * math.cos(a), ow * math.sin(a)
        draw.polygon([(x + dx, y + dy) for x, y in pts], fill=INK)
    draw.polygon(pts, fill=fill)


def _hand(
    draw: ImageDraw.ImageDraw,
    elbow: _Point,
    wrist: _Point,
    s: float,
    fill: tuple[int, ...],
    ow: float,
) -> None:
    """Three-finger mitt continuing the forearm direction."""
    dx, dy = wrist[0] - elbow[0], wrist[1] - elbow[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return
    d = (dx / length, dy / length)
    hr = 7.0 * s
    palm = (wrist[0] + d[0] * hr * 0.35, wrist[1] + d[1] * hr * 0.35)
    base = (palm[0] + d[0] * hr * 0.4, palm[1] + d[1] * hr * 0.4)
    fingers = []
    for ang in (-30.0, 0.0, 30.0):
        fd = _rot(d, ang)
        fingers.append((base, (base[0] + fd[0] * hr * 0.85, base[1] + fd[1] * hr * 0.85)))
    fw = hr * 0.62
    for a, b in fingers:
        _capsule(draw, a, b, fw + 2 * ow, INK)
    _disc(draw, palm, hr + ow, INK)
    for a, b in fingers:
        _capsule(draw, a, b, fw, fill)
    _disc(draw, palm, hr, fill)


def _shoe(
    draw: ImageDraw.ImageDraw,
    ankle: _Point,
    s: float,
    fill: tuple[int, ...],
    ow: float,
) -> None:
    """Rounded shoe pointing toward screen right."""
    a = (ankle[0] - 3 * s, ankle[1] + 4 * s)
    b = (ankle[0] + 9 * s, ankle[1] + 4 * s)
    _capsule(draw, a, b, 12 * s + 2 * ow, INK)
    _capsule(draw, a, b, 12 * s, fill)


def _ell_pts(hp: _Map, cu: float, cv: float, rx: float, ry: float, n: int = 24) -> list[_Point]:
    """Head-local ellipse sampled into canvas points."""
    return [
        hp(cu + rx * math.cos(2 * math.pi * k / n), cv + ry * math.sin(2 * math.pi * k / n))
        for k in range(n)
    ]


def _lid_cover(
    hp: _Map, cu: float, cv: float, rx: float, ry: float, lid: float
) -> tuple[list[_Point], list[_Point]]:
    """Top boundary and lid-edge boundary of the eyelid region, clipped to the eye."""
    edge_v = cv - ry + 2 * ry * lid
    tops: list[_Point] = []
    bots: list[_Point] = []
    for i in range(13):
        u = -rx + 2 * rx * i / 12
        half = math.sqrt(max(0.0, 1 - (u / rx) ** 2))
        top = cv - ry * half
        bot = cv + ry * half
        edge = max(top, min(edge_v, bot))
        tops.append(hp(cu + u, top))
        bots.append(hp(cu + u, edge))
    return tops, bots


def _overlay_discs(
    layer: Image.Image, items: list[tuple[_Point, float, float, tuple[int, ...]]]
) -> None:
    """Alpha-blend soft ellipses onto ``layer`` without erasing what is underneath."""
    if not items:
        return
    overlay = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for c, rx, ry, color in items:
        od.ellipse([c[0] - rx, c[1] - ry, c[0] + rx, c[1] + ry], fill=color)
    layer.alpha_composite(overlay)


def _render(
    layer: Image.Image,
    joints: dict[str, float],
    origin: _Point,
    scale: float,
    expr: dict[str, float],
    headband: bool,
    t: float,
    torso_scale: float = 1.0,
) -> None:
    draw = ImageDraw.Draw(layer)
    s = scale
    ow = max(1.0, 2.0 * s)
    ox, oy = origin
    hip = (ox, oy + joints["drop"] * s)
    top = _up(hip, joints["torso"], TORSO_LEN * torso_scale, s)
    head_turn = float(joints.get("head_turn", 0.0))
    head_c = _up(top, joints["head"], NECK_LEN + HEAD_R, s)
    head_c = (head_c[0] + head_turn * HEAD_TURN_PX * s, head_c[1])
    R = HEAD_R * s

    knee_r = _down(hip, joints["r_thigh"], THIGH_LEN, s)
    knee_l = _down(hip, joints["l_thigh"], THIGH_LEN, s)
    leg_r = [hip, knee_r, _down(knee_r, joints["r_shin"], SHIN_LEN, s)]
    leg_l = [hip, knee_l, _down(knee_l, joints["l_shin"], SHIN_LEN, s)]
    elb_r = _down(top, joints["r_upper"], UPPER_LEN, s)
    elb_l = _down(top, joints["l_upper"], UPPER_LEN, s)
    arm_r = [top, elb_r, _down(elb_r, joints["r_lower"], LOWER_LEN, s)]
    arm_l = [top, elb_l, _down(elb_l, joints["l_lower"], LOWER_LEN, s)]

    # Soft cast shadow under the feet, drawn first so the figure sits on it.
    shadow_y = max(leg_r[2][1], leg_l[2][1]) + 7 * s
    draw.ellipse(
        [hip[0] - 62 * s, shadow_y - 10 * s, hip[0] + 62 * s, shadow_y + 10 * s], fill=SHADOW
    )

    # Back side (screen right) is shaded: far leg, far arm and far hand use CREAM_SHADE.
    _limb(draw, leg_r, LEG_W * s, CREAM_SHADE, ow)
    _shoe(draw, leg_r[2], s, SHOE_SHADE, ow)
    _limb(draw, leg_l, LEG_W * s, CREAM, ow)
    _shoe(draw, leg_l[2], s, SHOE, ow)
    _limb(draw, arm_r, ARM_W * s, CREAM_SHADE, ow)
    _hand(draw, arm_r[1], arm_r[2], s, CREAM_SHADE, ow)

    # Shirt torso with a shaded strip along its back edge.
    tw = TORSO_W * s * torso_scale
    _capsule(draw, hip, top, tw + 2 * ow, INK)
    _capsule(draw, hip, top, tw, SHIRT)
    perp = (math.cos(math.radians(joints["torso"])), math.sin(math.radians(joints["torso"])))
    axis = (-perp[1], perp[0])  # hip-to-top direction, perpendicular to perp
    off = 0.28 * tw
    strip_a = (hip[0] + perp[0] * off, hip[1] + perp[1] * off)
    strip_b = (top[0] + perp[0] * off, top[1] + perp[1] * off)
    _capsule(draw, strip_a, strip_b, 0.36 * tw, SHIRT_SHADE)

    # Neck, then a flat collar sitting over the neck base.
    _capsule(draw, top, head_c, NECK_W * s + 2 * ow, INK)
    _capsule(draw, top, head_c, NECK_W * s, CREAM)
    half_w = 0.42 * tw
    c_left = (
        top[0] - perp[0] * half_w + axis[0] * 0.1 * tw,
        top[1] - perp[1] * half_w + axis[1] * 0.1 * tw,
    )
    c_right = (
        top[0] + perp[0] * half_w + axis[0] * 0.1 * tw,
        top[1] + perp[1] * half_w + axis[1] * 0.1 * tw,
    )
    c_mid = (top[0] - axis[0] * 0.3 * tw, top[1] - axis[1] * 0.3 * tw)
    _outlined_poly(draw, [c_left, c_right, c_mid], CREAM, ow * 0.8)

    h_rad = math.radians(joints["head"] + head_turn * HEAD_TURN_TILT_DEG)
    hc_cos, hc_sin = math.cos(h_rad), math.sin(h_rad)

    def hp(u: float, v: float) -> _Point:
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

    _draw_face(draw, layer, hp, R, expr)

    # Near arm and hand sit over the torso and head.
    _limb(draw, arm_l, ARM_W * s, CREAM, ow)
    _hand(draw, arm_l[1], arm_l[2], s, CREAM, ow)


def _draw_face(
    draw: ImageDraw.ImageDraw,
    layer: Image.Image,
    hp: _Map,
    R: float,
    expr: dict[str, float],
) -> None:
    lw = max(2, round(0.06 * R))
    brow_w = max(2, round(0.07 * R))
    line_w = max(2, round(0.05 * R))
    lid = float(expr["lid"])
    look = float(expr["look"]) * 0.05
    glow = expr["glow"] > 0.5
    squint = expr["squint"] > 0.5
    eye_centers = [hp(ue, -0.08) for ue in (-0.36, 0.36)]

    # Soft layers first: cheek blush and the glow halo, blended over the head.
    soft: list[tuple[_Point, float, float, tuple[int, ...]]] = []
    if expr["blush"] > 0.5:
        for u in (-0.56, 0.56):
            soft.append((hp(u, 0.2), 0.17 * R, 0.09 * R, BLUSH))
    if glow:
        for c in eye_centers:
            soft.append((c, 0.34 * R, 0.34 * R, _HALO_ALPHA))
    _overlay_discs(layer, soft)

    for ue in (-0.36, 0.36):
        if squint:
            arc = [hp(ue - 0.17, -0.02), hp(ue, -0.2), hp(ue + 0.17, -0.02)]
            draw.line(arc, fill=INK, width=lw, joint="curve")
        else:
            ring = _ell_pts(hp, ue, -0.08, 0.2, 0.24)
            draw.polygon(ring, fill=EYE_WHITE)
            pupil = hp(ue + look, -0.06)
            if glow:
                _disc(draw, pupil, 0.13 * R, HEADBAND)
            else:
                _disc(draw, pupil, 0.11 * R, INK)
                _disc(draw, hp(ue + look - 0.05, -0.13), 0.035 * R, EYE_WHITE)
            draw.line(ring + [ring[0]], fill=INK, width=lw, joint="curve")
            if lid > 0.02:
                tops, bots = _lid_cover(hp, ue, -0.08, 0.2, 0.24, lid)
                draw.polygon(tops + bots[::-1], fill=CREAM)
                draw.line(tops, fill=INK, width=lw, joint="curve")
                draw.line(bots, fill=INK, width=lw, joint="curve")

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
        draw.line([inner, outer], fill=INK, width=brow_w)

    open_amt = float(expr["open"])
    curve = float(expr["curve"])
    if expr["teeth"] > 0.5:
        box = [hp(-0.3, 0.37), hp(0.3, 0.37), hp(0.3, 0.53), hp(-0.3, 0.53)]
        draw.polygon(box, fill=CREAM)
        draw.line(box + [box[0]], fill=INK, width=line_w)
        for u in (-0.1, 0.1):
            draw.line([hp(u, 0.37), hp(u, 0.53)], fill=INK, width=line_w)
        draw.line([hp(-0.3, 0.45), hp(0.3, 0.45)], fill=INK, width=line_w)
    elif open_amt > 0.05:
        mouth_pts = _ell_pts(hp, 0.0, 0.44 + 0.04 * curve, 0.17, 0.13 * open_amt, 20)
        draw.polygon(mouth_pts, fill=MOUTH)
        draw.line(mouth_pts + [mouth_pts[0]], fill=INK, width=line_w, joint="curve")
    elif expr["smirk"] > 0.5:
        draw.line(
            [hp(-0.02, 0.44), hp(0.12, 0.42), hp(0.24, 0.34)],
            fill=INK,
            width=lw,
            joint="curve",
        )
    else:
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
    pose: str | dict[str, float],
    origin: tuple[float, float],
    scale: float,
    expression: str = "neutral",
    headband: bool = False,
    t: float = 0.0,
    mirror: bool = False,
    head_turn: float = 0.0,
    hand_lift: float = 0.0,
) -> None:
    """Draw the figure onto ``img`` in place.

    ``origin`` is the hip in pixels and ``scale`` multiplies body units. ``pose`` is a
    pose name or a joint dict such as the one from :func:`animate_pose`. Unknown pose
    or expression names fall back to ``stand`` and ``neutral``. ``t`` drives the
    headband ribbon flutter. When ``t`` is non-zero the eyes blink (see
    ``motion.blink``) and the torso breathes (see ``motion.breathe``). At ``t=0.0``
    neither applies. ``mirror=True`` flips the figure horizontally about the hip so
    it faces the other way.

    ``head_turn`` in -1..1 shifts the head (and eyes) sideways by ``head_turn * 10``
    px at scale 1 and tilts it by ``head_turn * 6`` degrees. It adds to any
    ``head_turn`` key in the pose. ``hand_lift`` in 0..1 raises the right arm by
    ``hand_lift * 40`` degrees. Both default to 0.0, which leaves the output unchanged.
    """
    if isinstance(pose, str):
        pose = pose if pose in POSES else "stand"
    expr_name = expression if expression in EXPRESSIONS else "neutral"
    expr = dict(EXPRESSIONS[expr_name])
    torso_scale = 1.0
    if t != 0.0:
        expr["lid"] = max(float(expr["lid"]), 1.0 - blink(t))
        torso_scale = breathe(t)
    joints = _resolve_pose(pose)
    turn = max(-1.0, min(1.0, float(joints["head_turn"]) + float(head_turn)))
    joints["head_turn"] = turn
    lift = max(0.0, min(1.0, float(hand_lift)))
    if lift != 0.0:
        joints["r_upper"] += lift * HAND_LIFT_DEG
        joints["r_lower"] += lift * HAND_LIFT_DEG
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    _render(
        layer,
        joints,
        origin,
        scale,
        expr,
        headband,
        t,
        torso_scale,
    )
    if mirror:
        layer = layer.transform(
            layer.size,
            Image.Transform.AFFINE,
            (-1, 0, 2 * origin[0], 0, 1, 0),
            resample=Image.Resampling.NEAREST,
        )
    _composite(img, layer)
