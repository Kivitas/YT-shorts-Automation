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
* ``back_of_head``: 0..1, at 0.5 or more the face is hidden (the back of the head shows).

Missing keys default to 0. The pivots below are the joint points of the part shapes in
``build-assets.mjs`` (canvas pixels, 400x800). The part shapes are fixed, so each part's
rest direction is measured from its pivot to its far end (``_from_down``). A part is
rotated by ``joint - rest``, so the rest pose reproduces the satori art.

The satori art has no face variants. The eyes, eyebrows and mouth are therefore drawn with
the procedural face of :mod:`autoshorts.longform.rig` on top of the head, so every
expression shows. A complete set of ``<name>_<expression>.png`` variants overrides it.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PIL import Image, ImageDraw

import autoshorts.longform.sprites as sprites
from autoshorts.longform import rig
from autoshorts.longform.sprites import load_sprite

CANVAS_SIZE = (400, 800)
MIDLINE_X = 200.0
# Transparent margin around the figure canvas, so limbs rotated past the 400x800 art box stay whole.
FIGURE_MARGIN = 400

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
# Centre of the head circle in the satori head art, and the face radius drawn inside it.
HEAD_CENTRE_REST = (200.0, 150.0)
HEAD_FACE_R = 72.0


def _from_down(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Angle in degrees of the segment a->b, measured from straight down toward +x."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))


# Rest angles of the limb segments in the satori art, same convention as rig.py.
REST_UPPER = {s: _from_down(SHOULDER[s], ELBOW[s]) for s in ("l", "r")}
REST_LOWER = {s: _from_down(ELBOW[s], WRIST[s]) for s in ("l", "r")}
REST_THIGH = {s: _from_down(LEG_TOP[s], KNEE[s]) for s in ("l", "r")}
REST_SHIN = {s: _from_down(KNEE[s], ANKLE[s]) for s in ("l", "r")}

_Point = tuple[float, float]
_JointGetter = Callable[[str], float]


def _to_world(point: _Point, pivot: _Point, world: _Point, phi: float) -> _Point:
    """Forward map of a native point: rotate by ``phi`` degrees about ``pivot``, then move it to ``world``."""
    a = math.radians(phi)
    vx, vy = point[0] - pivot[0], point[1] - pivot[1]
    c, s = math.cos(a), math.sin(a)
    return (world[0] + c * vx - s * vy, world[1] + s * vx + c * vy)


def _rotation_radius(part: Image.Image, pivot: _Point) -> int:
    """Smallest radius about ``pivot`` that holds every opaque pixel of ``part`` at any rotation."""
    box = part.getbbox()
    if box is None:
        return 0
    x0, y0, x1, y1 = box
    far_x = max(abs(x0 - pivot[0]), abs(x1 - pivot[0]))
    far_y = max(abs(y0 - pivot[1]), abs(y1 - pivot[1]))
    return math.ceil(math.hypot(far_x, far_y))


def _place(
    part: Image.Image, pivot: _Point, world: _Point, phi: float
) -> tuple[Image.Image, tuple[int, int]]:
    """Return ``part`` rotated by ``phi`` about ``pivot``, with the pivot moved onto ``world``.

    The rotation runs on a square layer padded around the pivot, so no rotated pixel is
    clipped. Returns the layer and its top-left corner in figure coordinates. Each part is
    still resampled once, by a single inverse AFFINE transform.
    """
    if phi == 0.0 and pivot == world:
        return part, (0, 0)
    radius = _rotation_radius(part, pivot)
    # Integer top-left of the layer; the fractional part of ``world`` stays in the coefficients.
    ox = math.floor(world[0]) - radius
    oy = math.floor(world[1]) - radius
    size = 2 * radius + 1
    a = math.radians(phi)
    c, s = math.cos(a), math.sin(a)
    # Layer pixel (X, Y) is figure pixel (X + ox, Y + oy), so the inverse map is shifted by (ox, oy).
    coeffs = (
        c,
        s,
        pivot[0] - c * world[0] - s * world[1] + c * ox + s * oy,
        -s,
        c,
        pivot[1] + s * world[0] - c * world[1] - s * ox + c * oy,
    )
    layer = part.transform(
        (size, size), Image.Transform.AFFINE, coeffs, resample=Image.Resampling.BICUBIC
    )
    return layer, (ox, oy)


def _half(part: Image.Image, side: str) -> Image.Image:
    """Copy of ``part`` with only its left (``l``) or right (``r``) half kept, split at the midline."""
    out = part.copy()
    width, height = out.size
    if side == "l":
        out.paste((0, 0, 0, 0), (round(MIDLINE_X), 0, width, height))
    else:
        out.paste((0, 0, 0, 0), (0, 0, round(MIDLINE_X), height))
    return out


def _face_variants(expression: str) -> dict[str, Image.Image] | None:
    """The eyes, eyebrows and mouth PNGs named ``<name>_<expression>``, or ``None``.

    Only a complete set counts: all three must exist at canvas size. Otherwise the face is
    drawn procedurally, so the expression still shows.
    """
    if expression == "neutral":
        return None
    variants: dict[str, Image.Image] = {}
    for name in EXPRESSION_PARTS:
        variant_name = f"{name}_{expression}"
        if not (sprites.SPRITE_DIR / f"{variant_name}.png").is_file():
            return None
        variant = load_sprite(variant_name)
        if variant is None or variant.size != CANVAS_SIZE:
            return None
        variants[name] = variant
    return variants


def _load_parts() -> dict[str, Image.Image] | None:
    """Load every required part at canvas size, or ``None`` when any is missing."""
    parts: dict[str, Image.Image] = {}
    for name in PART_NAMES:
        sprite = load_sprite(name)
        if sprite is None or sprite.size != CANVAS_SIZE:
            return None
        parts[name] = sprite
    return parts


def _composite_at(dest: Image.Image, layer: Image.Image, left: int, top: int) -> None:
    """Alpha-composite ``layer`` onto ``dest`` with its top-left at (``left``, ``top``), clipping to ``dest``."""
    x0, y0 = max(0, -left), max(0, -top)
    x1, y1 = min(layer.width, dest.width - left), min(layer.height, dest.height - top)
    if x1 <= x0 or y1 <= y0:
        return
    dest.alpha_composite(layer.crop((x0, y0, x1, y1)), (left + x0, top + y0))


def _composite(img: Image.Image, layer: Image.Image) -> None:
    """Alpha-composite ``layer`` onto ``img`` in place, keeping the mode of ``img``."""
    if img.mode == "RGBA":
        img.alpha_composite(layer)
        return
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    img.paste(base.convert(img.mode))


def _joint(joints: dict[str, float], key: str) -> float:
    return float(joints.get(key, 0.0))


def _arm_world(
    joint: _JointGetter, side: str, torso_phi: float
) -> tuple[_Point, _Point, _Point, float, float]:
    """Shoulder, elbow and wrist in canvas coordinates, plus the upper and lower arm rotations.

    The shoulder follows the torso; the arm angles are absolute, as in ``rig.draw_rig``.
    """
    shoulder_w = _to_world(SHOULDER[side], HIP, HIP, torso_phi)
    upper_phi = -(joint(f"{side}_upper") - REST_UPPER[side])
    lower_phi = -(joint(f"{side}_lower") - REST_LOWER[side])
    elbow_w = _to_world(ELBOW[side], SHOULDER[side], shoulder_w, upper_phi)
    wrist_w = _to_world(WRIST[side], ELBOW[side], elbow_w, lower_phi)
    return shoulder_w, elbow_w, wrist_w, upper_phi, lower_phi


def wrist_offset(joints: dict[str, float], scale: float, side: str = "l") -> tuple[float, float]:
    """Offset in output pixels of a wrist from the figure origin, for ``joints`` at figure ``scale``.

    Uses the same arm and hip-drop maths as :func:`draw_sprite_rig`, so a prop can be placed
    on a hand. ``side`` is ``"l"`` or ``"r"``.
    """
    torso_phi = _joint(joints, "torso")
    _, _, wrist_w, _, _ = _arm_world(lambda key: _joint(joints, key), side, torso_phi)
    drop_px = _joint(joints, "drop") * CANVAS_PX_PER_RIG_UNIT * scale
    return ((wrist_w[0] - HIP[0]) * scale, drop_px + (wrist_w[1] - HIP[1]) * scale)


def _draw_procedural_face(canvas: Image.Image, centre: _Point, phi: float, expression: str) -> None:
    """Draw the eyes, brows and mouth of ``expression`` on the head centred at ``centre``.

    ``centre`` is in figure coordinates and ``phi`` is the head rotation, the same as the
    head part. The face uses :func:`rig._draw_face`, so it matches the procedural figure.
    """
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    a = math.radians(phi)
    c, s = math.cos(a), math.sin(a)
    cx, cy = centre[0] + FIGURE_MARGIN, centre[1] + FIGURE_MARGIN
    radius = HEAD_FACE_R

    def hp(u: float, v: float) -> _Point:
        """Head-local (u right, v down, in head radii) to canvas coordinates."""
        return (cx + radius * (c * u - s * v), cy + radius * (s * u + c * v))

    name = expression if expression in rig.EXPRESSIONS else "neutral"
    rig._draw_face(draw, layer, hp, radius, dict(rig.EXPRESSIONS[name]))
    canvas.alpha_composite(layer)


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
    leg (screen right), near leg, torso, far arm, near arm, head, face. Each side's upper
    limb, lower limb and shoe rotate about their own joint and inherit the rotation of their
    parent, as in ``rig.draw_rig``.

    The face is drawn procedurally on the head for every expression, so it always shows.
    A complete set of ``eyes``, ``eyebrows`` and ``mouth`` variants named
    ``<name>_<expression>.png`` is drawn in its place. A ``back_of_head`` joint of 0.5 or
    more hides the face altogether. ``t`` is accepted for parity with ``draw_rig`` but is
    not used: the satori parts do not blink or breathe. ``mirror=True`` flips the figure
    about ``origin``.

    Returns ``False`` without drawing when any required part PNG is missing or is not the
    400x800 canvas size, so the caller can fall back to ``rig.draw_rig``.
    """
    del t  # accepted for signature parity with rig.draw_rig; the satori parts do not animate
    parts = _load_parts()
    if parts is None:
        return False
    variants = _face_variants(expression)

    def joint(key: str) -> float:
        return _joint(joints, key)

    turn = max(-1.0, min(1.0, joint("head_turn")))
    drop_px = joint("drop") * CANVAS_PX_PER_RIG_UNIT * scale
    back_of_head = joint("back_of_head") >= 0.5

    # The figure is drawn on a canvas padded by FIGURE_MARGIN on each side; figure coordinates
    # are shifted by that margin.
    canvas_w = CANVAS_SIZE[0] + 2 * FIGURE_MARGIN
    canvas_h = CANVAS_SIZE[1] + 2 * FIGURE_MARGIN
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    def paint(part: Image.Image, pivot: _Point, world: _Point, phi: float) -> None:
        layer, (left, top) = _place(part, pivot, world, phi)
        _composite_at(canvas, layer, left + FIGURE_MARGIN, top + FIGURE_MARGIN)

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
        shoulder_w, elbow_w, wrist_w, upper_phi, lower_phi = _arm_world(joint, side, torso_phi)
        paint(_half(parts["upper_arm"], side), SHOULDER[side], shoulder_w, upper_phi)
        paint(_half(parts["forearm"], side), ELBOW[side], elbow_w, lower_phi)
        paint(_half(parts["hand"], side), WRIST[side], wrist_w, lower_phi)

    # Head pivots at the neck, with the head_turn shift and tilt applied.
    neck_w = _to_world(TORSO_TOP, HIP, HIP, torso_phi)
    head_w = (neck_w[0] + turn * HEAD_TURN_RIG_PX * CANVAS_PX_PER_RIG_UNIT, neck_w[1])
    head_phi = joint("head") + turn * HEAD_TURN_TILT_DEG
    paint(parts["head"], TORSO_TOP, head_w, head_phi)
    if variants is not None:
        for name in EXPRESSION_PARTS:
            paint(variants[name], TORSO_TOP, head_w, head_phi)
    elif not back_of_head:
        head_centre = _to_world(HEAD_CENTRE_REST, TORSO_TOP, head_w, head_phi)
        _draw_procedural_face(canvas, head_centre, head_phi, expression)

    # Scale the whole figure once, anchoring the canvas hip on the output origin.
    ox, oy = origin
    width = max(1, round(canvas_w * scale))
    height = max(1, round(canvas_h * scale))
    scaled = canvas.resize((width, height), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    left = round(ox - (HIP[0] + FIGURE_MARGIN) * scale)
    top = round(oy + drop_px - (HIP[1] + FIGURE_MARGIN) * scale)
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


__all__ = ["CANVAS_SIZE", "PART_NAMES", "draw_sprite_rig", "wrist_offset"]
