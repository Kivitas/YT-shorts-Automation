"""Pure Pillow coloured-light helpers for explainer scenes in the long-form renderer.

The in-place helpers accept RGB or RGBA images and keep the size, mode and alpha
channel of the image they change. ``add_bloom`` returns a new RGB image. Colour and
intensity values are clamped, and any geometry that falls outside the canvas is
clipped rather than raising. Blurs run at reduced size where that is cheap.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter

_BLACK = (0, 0, 0)
_WHITE_HOT = (255, 248, 232)
_CONE_LAYERS = 12
_DOWNSCALE = 4


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _rgb_triplet(color: Sequence[float]) -> tuple[int, int, int]:
    r, g, b = (int(max(0, min(255, round(c)))) for c in tuple(color)[:3])
    return (r, g, b)


def _scale_lut(k: float, gain: float = 1.0) -> list[int]:
    """Return a 256-entry LUT that multiplies values by ``k * gain`` with clamping."""
    return [min(255, int(v * k * gain)) for v in range(256)]


def _split(img: Image.Image) -> tuple[Image.Image, Image.Image | None]:
    """Return ``(rgb, alpha)``. For RGB, ``rgb`` is ``img`` itself and alpha is None."""
    if img.mode == "RGB":
        return img, None
    if img.mode == "RGBA":
        return img.convert("RGB"), img.getchannel("A")
    raise ValueError(f"unsupported image mode {img.mode!r}; expected RGB or RGBA")


def _finish(img: Image.Image, rgb: Image.Image, alpha: Image.Image | None) -> None:
    """Write the working RGB buffer back into ``img``, restoring its original alpha."""
    if alpha is None:
        return
    img.paste(Image.merge("RGBA", (*rgb.split(), alpha)))


def _clip_box(
    box: tuple[float, float, float, float], size: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """Clip a float box to the canvas. Returns None when nothing is left."""
    w, h = size
    x0 = max(0, math.floor(box[0]))
    y0 = max(0, math.floor(box[1]))
    x1 = min(w, math.ceil(box[2]))
    y1 = min(h, math.ceil(box[3]))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _blur(mask: Image.Image, radius: float, scale: int = 1) -> Image.Image:
    """Gaussian-blur ``mask``, optionally at ``1/scale`` size then upscaled."""
    if radius <= 0:
        return mask
    if scale <= 1:
        return mask.filter(ImageFilter.GaussianBlur(radius))
    w, h = mask.size
    small = mask.resize((max(1, w // scale), max(1, h // scale)), Image.Resampling.BILINEAR)
    small = small.filter(ImageFilter.GaussianBlur(max(0.5, radius / scale)))
    return small.resize((w, h), Image.Resampling.BICUBIC)


def _screen_into(rgb: Image.Image, box: tuple[int, int, int, int], tint: Image.Image) -> None:
    """Screen-blend an RGB ``tint`` (box-sized) onto ``rgb`` inside ``box``."""
    x0, y0, x1, y1 = box
    region = rgb.crop(box)
    rgb.paste(ImageChops.screen(region, tint), (x0, y0))


def _draw_disc(layer: Image.Image, cx: float, cy: float, r: float, value: int) -> None:
    ImageDraw.Draw(layer).ellipse((cx - r, cy - r, cx + r, cy + r), fill=value)


def add_bloom(
    img: Image.Image,
    threshold: int = 180,
    radius: float = 24,
    strength: float = 0.6,
) -> Image.Image:
    """Return a new RGB image where areas brighter than ``threshold`` bleed outward.

    The bright parts are isolated with a soft knee, blurred, scaled by ``strength``
    and screen-blended back over the original.
    """
    base = img.convert("RGB")
    thr = int(max(0, min(254, threshold)))
    span = 255 - thr
    knee = [min(255, (v - thr) * 255 // span) if v > thr else 0 for v in range(256)]
    bright = base.point(knee * 3)
    scale = _DOWNSCALE if radius >= 8 else 1
    blurred = _blur(bright, float(radius), scale)
    glow = blurred.point(_scale_lut(_clamp01(strength)) * 3)
    return ImageChops.screen(base, glow)


def light_cone(
    img: Image.Image,
    apex: tuple[float, float],
    target: tuple[float, float],
    spread_deg: float,
    color: tuple[int, int, int],
    intensity: float = 0.35,
) -> None:
    """Composite a soft triangular light cone from ``apex`` toward ``target`` in place.

    The cone is widest at ``target`` (full angle ``spread_deg``), fades toward the far
    end, is blurred, and carries a few faint dust specks.
    """
    rgb, alpha = _split(img)
    width, height = rgb.size
    ax, ay = float(apex[0]), float(apex[1])
    tx, ty = float(target[0]), float(target[1])
    length = math.hypot(tx - ax, ty - ay)
    k = _clamp01(intensity)
    if length < 1.0 or k <= 0.0:
        _finish(img, rgb, alpha)
        return

    ux, uy = (tx - ax) / length, (ty - ay) / length
    nx, ny = -uy, ux
    half = math.radians(max(1.0, min(170.0, float(spread_deg)))) / 2.0
    half_w = length * math.tan(half)
    blur = max(6.0, length * 0.04)
    margin = int(3 * blur) + 2
    xs = [ax, tx + nx * half_w, tx - nx * half_w]
    ys = [ay, ty + ny * half_w, ty - ny * half_w]
    box = _clip_box(
        (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin),
        (width, height),
    )
    if box is None:
        _finish(img, rgb, alpha)
        return
    bx0, by0, bx1, by1 = box
    bsize = (bx1 - bx0, by1 - by0)

    # Stack nested triangles, each adding a share of 255: the sum falls off linearly
    # from the apex to the far end.
    share = [v // _CONE_LAYERS for v in range(256)]
    mask = Image.new("L", bsize, 0)
    for i in range(1, _CONE_LAYERS + 1):
        f = i / _CONE_LAYERS
        cx = ax + ux * length * f - bx0
        cy = ay + uy * length * f - by0
        hw = half_w * f
        poly = [
            (ax - bx0, ay - by0),
            (cx + nx * hw, cy + ny * hw),
            (cx - nx * hw, cy - ny * hw),
        ]
        layer = Image.new("L", bsize, 0)
        ImageDraw.Draw(layer).polygon(poly, fill=255)
        mask = ImageChops.add(mask, layer.point(share))
    mask = _blur(mask, blur, scale=_DOWNSCALE if blur >= 16 else 1)

    # Faint dust specks drifting inside the beam.
    rng = random.Random(17)
    dust = Image.new("L", bsize, 0)
    dd = ImageDraw.Draw(dust)
    for _ in range(14):
        f = rng.uniform(0.12, 0.95)
        lateral = rng.uniform(-0.9, 0.9) * half_w * f
        px = ax + ux * length * f + nx * lateral - bx0
        py = ay + uy * length * f + ny * lateral - by0
        r = rng.choice((1, 1, 2))
        dd.ellipse((px - r, py - r, px + r, py + r), fill=rng.randint(90, 140))
    mask = ImageChops.add(mask, dust)

    alpha_mask = mask.point(_scale_lut(k))
    colour = Image.new("RGB", bsize, _rgb_triplet(color))
    tint = Image.composite(colour, Image.new("RGB", bsize, _BLACK), alpha_mask)
    _screen_into(rgb, box, tint)
    _finish(img, rgb, alpha)


def rim_light(
    img: Image.Image,
    box: tuple[float, float, float, float],
    color: tuple[int, int, int],
    width: int = 6,
    intensity: float = 0.5,
) -> None:
    """Add a soft coloured edge glow centred on the edges of ``box`` in place.

    ``box`` is ``(x0, y0, x1, y1)``. Coordinates may be given in any order and may
    extend past the canvas.
    """
    rgb, alpha = _split(img)
    x0, x1 = sorted((float(box[0]), float(box[2])))
    y0, y1 = sorted((float(box[1]), float(box[3])))
    w = max(1, int(width))
    k = _clamp01(intensity)
    margin = 3 * w + 2
    clip = _clip_box((x0 - margin, y0 - margin, x1 + margin, y1 + margin), rgb.size)
    if clip is None or k <= 0.0:
        _finish(img, rgb, alpha)
        return
    bx0, by0, bx1, by1 = clip
    bsize = (bx1 - bx0, by1 - by0)

    half = w / 2.0
    layer = Image.new("L", bsize, 0)
    ring = (
        round(x0 - bx0 - half),
        round(y0 - by0 - half),
        round(x1 - bx0 + half),
        round(y1 - by0 + half),
    )
    ImageDraw.Draw(layer).rectangle(ring, outline=255, width=w)
    layer = _blur(layer, w * 1.2)

    alpha_mask = layer.point(_scale_lut(k, gain=1.6))
    colour = Image.new("RGB", bsize, _rgb_triplet(color))
    tint = Image.composite(colour, Image.new("RGB", bsize, _BLACK), alpha_mask)
    _screen_into(rgb, clip, tint)
    _finish(img, rgb, alpha)


def colour_wash(
    img: Image.Image,
    color: tuple[int, int, int],
    intensity: float = 0.12,
) -> None:
    """Tint the whole frame toward ``color`` in place using a screen blend.

    An intensity of 0 leaves the image unchanged.
    """
    rgb, alpha = _split(img)
    k = _clamp01(intensity)
    if k > 0.0:
        scaled = tuple(int(round(c * k)) for c in _rgb_triplet(color))
        rgb.paste(ImageChops.screen(rgb, Image.new("RGB", rgb.size, scaled)))
    _finish(img, rgb, alpha)


def source_glow(
    img: Image.Image,
    center: tuple[float, float],
    radius: float,
    color: tuple[int, int, int],
    intensity: float,
) -> None:
    """Paint a strong radial bloom for a lamp or phone in place.

    A coloured halo falls off across ``radius`` and a small white-hot core sits at the
    centre. ``center`` may lie off the canvas.
    """
    rgb, alpha = _split(img)
    cx, cy = float(center[0]), float(center[1])
    r = float(radius)
    k = _clamp01(intensity)
    if r < 1.0 or k <= 0.0:
        _finish(img, rgb, alpha)
        return
    reach = 2.0 * r
    clip = _clip_box((cx - reach, cy - reach, cx + reach, cy + reach), rgb.size)
    if clip is None:
        _finish(img, rgb, alpha)
        return
    bx0, by0, _bx1, _by1 = clip
    bsize = (clip[2] - bx0, clip[3] - by0)
    lcx, lcy = cx - bx0, cy - by0

    halo = Image.new("L", bsize, 0)
    _draw_disc(halo, lcx, lcy, r, 255)
    halo = _blur(halo, r * 0.35, scale=_DOWNSCALE if r >= 32 else 1)

    core_r = max(2.0, r * 0.22)
    core = Image.new("L", bsize, 0)
    _draw_disc(core, lcx, lcy, core_r, 255)
    core = _blur(core, core_r * 0.6)

    black = Image.new("RGB", bsize, _BLACK)
    halo_rgb = Image.composite(
        Image.new("RGB", bsize, _rgb_triplet(color)), black, halo.point(_scale_lut(k))
    )
    core_rgb = Image.composite(
        Image.new("RGB", bsize, _WHITE_HOT), black, core.point(_scale_lut(k))
    )
    _screen_into(rgb, clip, ImageChops.add(halo_rgb, core_rgb))
    _finish(img, rgb, alpha)
