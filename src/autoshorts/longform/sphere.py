"""Flat-illustrated, shaded planet helpers for explainer scenes in the long-form renderer.

``draw_sphere`` paints a lit globe in place onto an RGB or RGBA image: Lambert shading
with a soft terminator, a seeded land mask of blobs that rotates with ``t``, optional
cloud bands, a faint fresnel edge and an outer rim glow. The disc edge is anti-aliased
by rasterising its coverage at twice the size and downscaling. The land mask is a
frame-independent texture cached per seed and height by ``sphere_frame_cached``.

Pure Pillow and ``math`` only. ``light_dir`` is the direction *towards* the light, in
screen space with y pointing down, so ``(-0.5, -0.6, 0.6)`` lights the upper left.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from functools import lru_cache

from PIL import Image, ImageDraw

_TEX_H = 128
_BLOB_COUNT = 9
_AMBIENT = 0.2
_WRAP = 0.25
_RIM_ALPHA = 0.6
_FRESNEL = 0.45
_TAU = math.tau


def glow_width(radius: float) -> int:
    """Width in pixels of the rim glow drawn outside a sphere of ``radius``."""
    return max(3, int(round(float(radius) * 0.22)))


def orbit_point(
    centre: Sequence[float],
    orbit_radius: float,
    angle_deg: float,
    tilt: float = 0.3,
) -> tuple[float, float]:
    """Return the screen point of a satellite on a tilted circular orbit.

    ``angle_deg`` 0 is directly right of the centre. ``tilt`` (radians) squashes the
    orbit vertically by ``cos(tilt)``, so the orbit reads as a flattened ellipse.
    """
    a = math.radians(float(angle_deg))
    x = float(centre[0]) + float(orbit_radius) * math.cos(a)
    y = float(centre[1]) + float(orbit_radius) * math.cos(float(tilt)) * math.sin(a)
    return (x, y)


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def _unit(lon: float, lat: float) -> tuple[float, float, float]:
    cl = math.cos(lat)
    return (cl * math.sin(lon), math.sin(lat), cl * math.cos(lon))


def _rgb(color: Sequence[float]) -> tuple[int, int, int]:
    r, g, b = (int(max(0, min(255, round(float(c))))) for c in tuple(color)[:3])
    return (r, g, b)


def _lighten(color: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return tuple(int(v + (255 - v) * k) for v in color)  # type: ignore[return-value]


@lru_cache(maxsize=32)
def sphere_frame_cached(seed: int, height: int = _TEX_H) -> bytes:
    """Return the land mask as an equirectangular texture of ``2 * height`` by ``height``.

    Values are 0..255 (255 is land). The texture is deterministic for a given seed and
    height and does not depend on time, so it is computed once and then reused for
    every frame. Row 0 is the north pole and column 0 is longitude 0.
    """
    tex_w = 2 * height
    rng = random.Random(seed)
    blobs = []
    for _ in range(_BLOB_COUNT):
        lon = rng.uniform(0.0, _TAU)
        lat = math.asin(rng.uniform(-0.85, 0.85))
        rad = rng.uniform(0.28, 0.55)
        blobs.append((_unit(lon, lat), math.cos(rad), rng.uniform(0.7, 1.0)))

    out = bytearray(tex_w * height)
    for vi in range(height):
        lat = (0.5 - (vi + 0.5) / height) * math.pi
        cl = math.cos(lat)
        sl = math.sin(lat)
        for ui in range(tex_w):
            lon = (ui + 0.5) / tex_w * _TAU
            px, py, pz = cl * math.sin(lon), sl, cl * math.cos(lon)
            field = 0.0
            for (cx_, cy_, cz_), cos_r, amp in blobs:
                dot = px * cx_ + py * cy_ + pz * cz_
                if dot > cos_r:
                    field += amp * math.sqrt((dot - cos_r) / (1.0 - cos_r))
            out[vi * tex_w + ui] = int(255 * _smoothstep(0.35, 0.6, field))
    return bytes(out)


def _disc_coverage(radius: float, edge: int, size: int) -> bytes:
    """Anti-aliased disc coverage: drawn at 2x and box-downscaled to ``size``."""
    big = Image.new("L", (2 * size, 2 * size), 0)
    c = 2.0 * edge
    r2 = 2.0 * radius
    ImageDraw.Draw(big).ellipse((c - r2, c - r2, c + r2, c + r2), fill=255)
    return big.resize((size, size), Image.Resampling.BOX).tobytes()


def _shade(
    nx: float,
    ny: float,
    nz: float,
    base_c: tuple[int, int, int],
    land_c: tuple[int, int, int],
    tex: bytes,
    tex_w: int,
    tex_h: int,
    spin: float,
    bands: bool,
    light: tuple[float, float, float],
    rim_c: tuple[int, int, int],
) -> tuple[int, int, int]:
    lat = math.asin(min(1.0, max(-1.0, -ny)))
    theta = math.atan2(nx, nz) + spin
    ui = min(tex_w - 1, int((theta / _TAU) % 1.0 * tex_w))
    vi = min(tex_h - 1, max(0, int((0.5 - lat / math.pi) * tex_h)))
    lm = tex[vi * tex_w + ui] / 255.0

    rr = base_c[0] + (land_c[0] - base_c[0]) * lm
    rg = base_c[1] + (land_c[1] - base_c[1]) * lm
    rb = base_c[2] + (land_c[2] - base_c[2]) * lm
    if bands:
        cloud = 0.35 * _smoothstep(0.55, 0.9, math.sin(lat * 6.0 + 0.9 * math.sin(theta * 3.0)))
        rr += (255.0 - rr) * cloud
        rg += (255.0 - rg) * cloud
        rb += (255.0 - rb) * cloud

    lam = nx * light[0] + ny * light[1] + nz * light[2]
    diff = max(0.0, (lam + _WRAP) / (1.0 + _WRAP))
    shade = _AMBIENT + (1.0 - _AMBIENT) * diff
    fres = (1.0 - nz) ** 3 * _FRESNEL
    out = []
    for chan, rim_v in zip((rr, rg, rb), rim_c, strict=True):
        v = chan * shade * (1.0 - fres) + rim_v * fres
        out.append(int(max(0.0, min(255.0, v))))
    return (out[0], out[1], out[2])


def draw_sphere(
    img: Image.Image,
    centre: Sequence[float],
    radius: float,
    t: float = 0.0,
    base: Sequence[float] = (70, 130, 200),
    land: Sequence[float] = (120, 190, 110),
    spin_deg_per_s: float = 12.0,
    light_dir: Sequence[float] = (-0.5, -0.6, 0.6),
    rim: Sequence[float] | None = None,
    bands: bool = True,
    seed: int = 7,
) -> None:
    """Paint a shaded, rotating planet onto ``img`` in place.

    ``t`` is time in seconds and drives the rotation at ``spin_deg_per_s``. ``rim`` is the
    glow colour; when None it is derived from ``base``. Pixels further than
    ``radius + glow_width(radius)`` from the centre are left untouched. RGB images are
    composited onto with their alpha ignored; RGBA images keep their alpha channel.
    """
    if img.mode not in ("RGB", "RGBA"):
        raise ValueError(f"draw_sphere needs an RGB or RGBA image, got mode {img.mode!r}")
    radius = float(radius)
    if radius <= 0:
        return

    glow = glow_width(radius)
    outer = radius + glow
    edge = int(math.ceil(outer + 2))
    size = 2 * edge
    cx = int(round(float(centre[0])))
    cy = int(round(float(centre[1])))

    lx, ly, lz = (float(v) for v in light_dir)
    norm = math.sqrt(lx * lx + ly * ly + lz * lz) or 1.0
    light = (lx / norm, ly / norm, lz / norm)

    base_c = _rgb(base)
    land_c = _rgb(land)
    rim_c = _rgb(rim) if rim is not None else _lighten(base_c, 0.45)
    spin = math.radians(float(spin_deg_per_s)) * float(t)
    tex = sphere_frame_cached(seed, _TEX_H)
    tex_w, tex_h = 2 * _TEX_H, _TEX_H
    cover = _disc_coverage(radius, edge, size)

    colour = bytearray(size * size * 3)
    alpha = bytearray(size * size)
    limit = (outer + 1.0) ** 2
    for j in range(size):
        dy = j + 0.5 - edge
        if dy * dy > limit:
            continue
        for i in range(size):
            dx = i + 0.5 - edge
            d2 = dx * dx + dy * dy
            if d2 > limit:
                continue
            d = math.sqrt(d2)
            if d > outer:
                continue
            k = j * size + i
            cov = cover[k]
            glow_a = 0.0 if d <= radius else _RIM_ALPHA * (1.0 - (d - radius) / glow) ** 2
            a = max(cov, int(round(glow_a * 255)))
            if a == 0:
                continue
            if cov == 0:
                r, g, b = rim_c
            else:
                if d <= radius:
                    nx, ny = dx / radius, dy / radius
                    nz = math.sqrt(max(0.0, 1.0 - nx * nx - ny * ny))
                else:
                    nx, ny, nz = dx / d, dy / d, 0.0
                r, g, b = _shade(
                    nx, ny, nz, base_c, land_c, tex, tex_w, tex_h, spin, bands, light, rim_c
                )
            o = k * 3
            colour[o] = r
            colour[o + 1] = g
            colour[o + 2] = b
            alpha[k] = a

    src = Image.frombytes("RGB", (size, size), bytes(colour)).convert("RGBA")
    src.putalpha(Image.frombytes("L", (size, size), bytes(alpha)))
    box = (cx - edge, cy - edge, cx + edge, cy + edge)
    region = img.crop(box).convert("RGBA")
    merged = Image.alpha_composite(region, src)
    img.paste(merged.convert(img.mode), box)
