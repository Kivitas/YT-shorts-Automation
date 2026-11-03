"""Satori-built character rig: draws the figure from its separate body-part PNGs.

``remotion/scripts/build-assets.mjs`` renders each body part (``head``, ``eyes``, ``eyebrows``,
``mouth``, ``torso``, ``upper_arm``, ``forearm``, ``hand``, ``upper_leg``, ``lower_leg``,
``shoe``) as a 400x800 PNG on the same canvas, so every part shares the figure's own
coordinate space. Each limb PNG holds both sides of the body, so :func:`draw_sprite_rig`
splits the left and right halves at the midline (x = 200) and rotates each half on its
own joint chain.

Pose format is the one used by :mod:`autoshorts.longform.rig`: a ``dict`` of absolute
angles in degrees, read with the same keys:

* ``torso`` and ``head``: 0 points straight up, +90 points toward +x (screen right).
* ``l_upper``, ``l_lower``, ``l_thigh``, ``l_shin`` (and ``r_`` twins): absolute segment
  angles, 0 straight down, +90 toward +x.
* ``drop``: hip shift in rig body units (positive is down).
* ``head_turn``: -1..1, shifts the head sideways and tilts it, as in ``rig.draw_rig``.

Missing keys default to 0. The pivots below are the joint points of the part shapes in
``build-assets.mjs`` (canvas pixels, 400x800). The part shapes are fixed, so each part's
rest direction is measured from its pivot to its far end (``_from_down``). A part is
rotated by ``joint - rest``, so the rest pose reproduces the satori art.
"""

from __future__ import annotations

import math

from PIL import Image

import autoshorts.longform.sprites as sprites
from autoshorts.longform.sprites import load_sprite

CANVAS_SIZE = (400, 800)
MIDLINE_X = 200.0

# Every part must exist; a missing one makes draw_sprite_rig return False.
PART_NAMES = (
    "upper_leg", "lower_leg", "shoe", "torso", "upper_arm", "forearm", "hand",
    "head", "eyes", "eyebrows", "mouth",
)  # fmt: skip
# Face parts that have optional ``<name>_<expression>`` variants.
EXPRESSION_PARTS = ("eyes", "eyebrows", "mouth")

# Joint pivots in canvas pixels, from the part shapes in build-assets.mjs.
HIP = (200.0, 470.0)  # torso bottom centre and draw origin; leg tops sit at y=470
TORSO_TOP = (200.0, 222.0)  # neck base: the head pivots here
SHOULDER = {"l": (138.0, 250.0), "r": (262.0, 250.0)}
ELBOW = {"l": (112.0, 350.0), "r": (288.0, 350.0)}
WRIST = {"l": (118.0, 448.0), "r": (282.0, 448.0)}
LEG_TOP = {"l": (180.0, 470.0), "r": (220.0, 470.0)}
KNEE = {"l": (176.0, 600.0), "r": (224.0, 600.0)}
ANKLE = {"l": (172.0, 720.0), "r": (228.0, 720.0)}

# Canvas pixels per rig body unit (head radius 72 px in the art vs 30 units in rig.py).
CANVAS_PX_PER_RIG_UNIT = 2.4
HEAD_TURN_RIG_PX = 10.0  # rig.HEAD_TURN_PX
HEAD_TURN_TILT_DEG = 6.0  # rig.HEAD_TURN_TILT_DEG


def _from_down(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Angle in degrees of the segment a->b, measured from straight down toward +x."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))


# Rest angles of the limb segments in the satori art, same convention as rig.py.
REST_UPPER = {s: _from_down(SHOULDER[s], ELBOW[s]) for s in ("l", "r")}
REST_LOWER = {s: _from_down(ELBOW[s], WRIST[s]) for s in ("l", "r")}
REST_THIGH = {s: _from_down(LEG_TOP[s], KNEE[s]) for s in ("l", "r")}
REST_SHIN = {s: _from_down(KNEE[s], ANKLE[s]) for s in ("l", "r")}

_Point = tuple[float, float]


def _to_world(point: _Point, pivot: _Point, world: _Point, phi: float) -> _Point:
    """Forward map of a native point: rotate by ``phi`` degrees about ``pivot``, then move it to ``world``."""
    a = math.radians(phi)
    vx, vy = point[0] - pivot[0], point[1] - pivot[1]
    c, s = math.cos(a), math.sin(a)
    return (world[0] + c * vx - s * vy, world[1] + s * vx + c * vy)


def _place(part: Image.Image, pivot: _Point, world: _Point, phi: float) -> Image.Image:
    """Return ``part`` rotated by ``phi`` about ``pivot`` and moved so the pivot lands on ``world``.

    Done as a single inverse AFFINE transform, so each part is resampled once.
    """
    if phi == 0.0 and pivot == world:
        return part
    a = math.radians(phi)
    c, s = math.cos(a), math.sin(a)
    coeffs = (
        c,
        s,
        pivot[0] - c * world[0] - s * world[1],
        -s,
        c,
        pivot[1] + s * world[0] - c * world[1],
    )
    return part.transform(
        part.size, Image.Transform.AFFINE, coeffs, resample=Image.Resampling.BICUBIC
    )


def _half(part: Image.Image, side: str) -> Image.Image:
    """Copy of ``part`` with only its left (``l``) or right (``r``) half kept, split at the midline."""
    out = part.copy()
    width, height = out.size
    if side == "l":
        out.paste((0, 0, 0, 0), (round(MIDLINE_X), 0, width, height))
    else:
        out.paste((0, 0, 0, 0), (0, 0, round(MIDLINE_X), height))
    return out


def _face_part(name: str, expression: str, base: Image.Image) -> Image.Image:
    """The ``<name>_<expression>`` variant when the PNG exists, otherwise the neutral part."""
    if expression == "neutral":
        return base
    path = sprites.SPRITE_DIR / f"{name}_{expression}.png"
    if not path.is_file():
        return base
    variant = load_sprite(f"{name}_{expression}")
    if variant is None or variant.size != CANVAS_SIZE:
        return base
    return variant


def _load_parts(expression: str) -> dict[str, Image.Image] | None:
    """Load every required part at canvas size, or ``None`` when any is missing."""
    parts: dict[str, Image.Image] = {}
    for name in PART_NAMES:
        sprite = load_sprite(name)
        if sprite is None or sprite.size != CANVAS_SIZE:
            return None
        parts[name] = sprite
    for name in EXPRESSION_PARTS:
        parts[name] = _face_part(name, expression, parts[name])
    return parts


def _composite(img: Image.Image, layer: Image.Image) -> None:
    """Alpha-composite ``layer`` onto ``img`` in place, keeping the mode of ``img``."""
    if img.mode == "RGBA":
        img.alpha_composite(layer)
        return
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    img.paste(base.convert(img.mode))


def draw_sprite_rig(
    img: Image.Image,
    joints: dict[str, float],
    origin: tuple[float, float],
    scale: float,
    expression: str = "neutral",
    t: float = 0.0,
    mirror: bool = False,
) -> bool:
    """Draw the satori figure onto ``img`` in place, posed by ``joints``.

    ``origin`` is the hip in output pixels (the same meaning as in ``rig.draw_rig``) and
    ``scale`` is the output pixels per canvas pixel. Parts are painted in this order: far
    leg (screen right), near leg, torso, far arm, near arm, head, eyes, eyebrows, mouth.
    Each side's upper limb, lower limb and shoe rotate about their own joint and inherit
    the rotation of their parent, as in ``rig.draw_rig``.

    ``expression`` picks the ``eyes``, ``eyebrows`` and ``mouth`` variant
    ``<name>_<expression>.png`` when that file exists, and the neutral parts otherwise.
    ``t`` is accepted for parity with ``draw_rig`` but is not used: the satori parts do not
    blink or breathe. ``mirror=True`` flips the figure about ``origin``.

    Returns ``False`` without drawing when any required part PNG is missing or is not the
    400x800 canvas size, so the caller can fall back to ``rig.draw_rig``.
    """
    del t  # accepted for signature parity with rig.draw_rig; the satori parts do not animate
    parts = _load_parts(expression)
    if parts is None:
        return False

    def joint(key: str) -> float:
        return float(joints.get(key, 0.0))

    turn = max(-1.0, min(1.0, joint("head_turn")))
    drop_px = joint("drop") * CANVAS_PX_PER_RIG_UNIT * scale

    canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))

    def paint(part: Image.Image, pivot: _Point, world: _Point, phi: float) -> None:
        canvas.alpha_composite(_place(part, pivot, world, phi))

    # Legs: the joint dict holds absolute angles, so a part's rotation is -(joint - rest),
    # because the limb rest direction is measured from straight down.
    for side in ("r", "l"):
        thigh_phi = -(joint(f"{side}_thigh") - REST_THIGH[side])
        shin_phi = -(joint(f"{side}_shin") - REST_SHIN[side])
        knee_w = _to_world(KNEE[side], LEG_TOP[side], LEG_TOP[side], thigh_phi)
        ankle_w = _to_world(ANKLE[side], KNEE[side], knee_w, shin_phi)
        paint(_half(parts["upper_leg"], side), LEG_TOP[side], LEG_TOP[side], thigh_phi)
        paint(_half(parts["lower_leg"], side), KNEE[side], knee_w, shin_phi)
        paint(_half(parts["shoe"], side), ANKLE[side], ankle_w, shin_phi)

    # Torso rotates about the hip; the rig's "up" convention means the angle is not negated.
    torso_phi = joint("torso")
    paint(parts["torso"], HIP, HIP, torso_phi)

    # Arms follow the torso at the shoulders.
    for side in ("r", "l"):
        shoulder_w = _to_world(SHOULDER[side], HIP, HIP, torso_phi)
        upper_phi = -(joint(f"{side}_upper") - REST_UPPER[side])
        lower_phi = -(joint(f"{side}_lower") - REST_LOWER[side])
        elbow_w = _to_world(ELBOW[side], SHOULDER[side], shoulder_w, upper_phi)
        wrist_w = _to_world(WRIST[side], ELBOW[side], elbow_w, lower_phi)
        paint(_half(parts["upper_arm"], side), SHOULDER[side], shoulder_w, upper_phi)
        paint(_half(parts["forearm"], side), ELBOW[side], elbow_w, lower_phi)
        paint(_half(parts["hand"], side), WRIST[side], wrist_w, lower_phi)

    # Head pivots at the neck, with the head_turn shift and tilt applied.
    neck_w = _to_world(TORSO_TOP, HIP, HIP, torso_phi)
    head_w = (neck_w[0] + turn * HEAD_TURN_RIG_PX * CANVAS_PX_PER_RIG_UNIT, neck_w[1])
    head_phi = joint("head") + turn * HEAD_TURN_TILT_DEG
    paint(parts["head"], TORSO_TOP, head_w, head_phi)
    for name in EXPRESSION_PARTS:
        paint(parts[name], TORSO_TOP, head_w, head_phi)

    # Scale the whole figure once, anchoring the canvas hip on the output origin.
    ox, oy = origin
    width = max(1, round(CANVAS_SIZE[0] * scale))
    height = max(1, round(CANVAS_SIZE[1] * scale))
    scaled = canvas.resize((width, height), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    left = round(ox - HIP[0] * scale)
    top = round(oy + drop_px - HIP[1] * scale)
    layer.paste(scaled, (left, top))
    if mirror:
        layer = layer.transform(
            layer.size,
            Image.Transform.AFFINE,
            (-1, 0, 2 * ox, 0, 1, 0),
            resample=Image.Resampling.NEAREST,
        )
    _composite(img, layer)
    return True


__all__ = ["CANVAS_SIZE", "PART_NAMES", "draw_sprite_rig"]
