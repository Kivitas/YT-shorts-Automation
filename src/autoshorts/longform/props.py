"""Flat-illustrated prop library for long-form explainer scenes (Pillow).

Each prop is drawn on a transparent RGBA layer the size of the target image and
alpha-composited onto it, so RGB and RGBA canvases (1920x1080 in practice) both work.

Conventions:
- ``origin`` is the top-left corner of the prop's nominal box (1x units).
- ``scale`` multiplies every dimension and the 2 px ink outline (min 1 px).
- ``t`` is an animation time in seconds; it only drives glow pulses.
- Prop-specific options are keyword arguments: ``calendar(marks=int)``,
  ``speech_bubble(text=str)``, ``bar_chart(highlight=int | None)``.

Only ``sun_glow``, ``shadow_demon``, ``alarm_clock_7seg`` and the mountain sky use
gradients or blurred glows; everything else is flat fills with ink outlines.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

Color = tuple[int, int, int, int]
RGB = tuple[int, int, int]


def _hex(value: str, alpha: int = 255) -> Color:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


VOID = _hex("08080C")
CREAM = _hex("EAE6DF")
SLATE = _hex("2B2E3B")
MUTED_SLATE = _hex("5B6075")
CORAL = _hex("E63946")
TEAL = _hex("4DA6FF")
AMBER = _hex("D4A359")
RED_GLOW = _hex("E62E2E")
WHITE = (255, 255, 255, 255)
INK = VOID

_INK_UNITS = 2.0


class _Canvas:
    """Drawing helper that maps 1x prop coordinates onto the target layer."""

    def __init__(self, layer: Image.Image, origin: tuple[float, float], scale: float) -> None:
        self.layer = layer
        self.draw = ImageDraw.Draw(layer)
        self.ox, self.oy = origin
        self.s = scale
        self.ink = max(1, round(_INK_UNITS * scale))

    def n(self, value: float) -> int:
        return max(1, round(value * self.s))

    def pt(self, x: float, y: float) -> tuple[int, int]:
        return (round(self.ox + x * self.s), round(self.oy + y * self.s))

    def pts(self, points: list[tuple[float, float]]) -> list[tuple[int, int]]:
        return [self.pt(x, y) for x, y in points]

    def box(self, x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
        a = self.pt(x0, y0)
        b = self.pt(x1, y1)
        return (a[0], a[1], b[0], b[1])

    def rect(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        fill: Color,
        radius: float = 0.0,
        outline: bool = True,
    ) -> None:
        kwargs: dict[str, Any] = {"fill": fill}
        if outline:
            kwargs.update(outline=INK, width=self.ink)
        box = self.box(x0, y0, x1, y1)
        if radius > 0:
            self.draw.rounded_rectangle(box, radius=self.n(radius), **kwargs)
        else:
            self.draw.rectangle(box, **kwargs)

    def ellipse(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        fill: Color,
        outline: bool = True,
    ) -> None:
        kwargs: dict[str, Any] = {"fill": fill}
        if outline:
            kwargs.update(outline=INK, width=self.ink)
        self.draw.ellipse(self.box(x0, y0, x1, y1), **kwargs)

    def poly(
        self,
        points: list[tuple[float, float]],
        fill: Color,
        outline: bool = True,
    ) -> None:
        pts = self.pts(points)
        self.draw.polygon(pts, fill=fill)
        if outline:
            self.draw.line(pts + [pts[0]], fill=INK, width=self.ink, joint="curve")

    def line(self, points: list[tuple[float, float]], color: Color, width: float = 2.0) -> None:
        self.draw.line(self.pts(points), fill=color, width=self.n(width), joint="curve")

    def arc(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        start: float,
        end: float,
        color: Color,
        width: float,
    ) -> None:
        self.draw.arc(self.box(x0, y0, x1, y1), start, end, fill=color, width=self.n(width))

    def glow(
        self,
        draw_fn: Callable[[ImageDraw.ImageDraw], None],
        radius: float,
        rgb: RGB,
        strength: float = 1.0,
    ) -> None:
        """Blur a mask drawn by ``draw_fn`` (white = lit) and composite it in ``rgb``."""
        mask = Image.new("L", self.layer.size, 0)
        draw_fn(ImageDraw.Draw(mask))
        mask = mask.filter(ImageFilter.GaussianBlur(self.n(radius)))
        mask = mask.point(lambda v: min(255, round(v * strength)))
        color = Image.new("RGBA", self.layer.size, (*rgb, 255))
        color.putalpha(mask)
        self.layer.alpha_composite(color)


_FONT_CACHE: dict[int, ImageFont.ImageFont | ImageFont.FreeTypeFont] = {}


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    size = max(8, int(size))
    if size not in _FONT_CACHE:
        font: ImageFont.ImageFont | ImageFont.FreeTypeFont | None = None
        for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
            try:
                font = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default(size=size)
        _FONT_CACHE[size] = font
    return _FONT_CACHE[size]


def _vgradient(width: int, height: int, top: Color, bottom: Color) -> Image.Image:
    grad = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(grad)
    for y in range(height):
        f = y / max(1, height - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * f) for i in range(3))
        draw.line([(0, y), (width, y)], fill=(*color, 255))
    return grad


def _pulse(t: float) -> float:
    return 0.8 + 0.2 * math.sin(2 * math.pi * t)


_ENTRANCE_S = 0.35
_ENTRANCES = ("pop", "drop")
_DROP_HEIGHT = 120.0
_WIGGLE_PERIOD_S = 2.2
_MIN_ENTRANCE_SCALE = 0.01


def _ease_out_back(x: float) -> float:
    """Ease-out with a small overshoot (peaks near 1.10) for x in [0, 1]."""
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * (x - 1.0) ** 3 + c1 * (x - 1.0) ** 2


def _drop_offset(p: float) -> float:
    """Vertical offset in px for a drop entrance at progress ``p`` in [0, 1].

    Falls from ``-_DROP_HEIGHT`` to 0, bounces once (20 px) and settles at 0.
    """
    if p < 0.6:
        return -_DROP_HEIGHT * (1.0 - (p / 0.6) ** 2)
    if p < 0.8:
        return -20.0 * math.sin(math.pi * (p - 0.6) / 0.2)
    return 0.0


def _scale_about(layer: Image.Image, origin: tuple[float, float], k: float) -> Image.Image:
    """Scale the drawn content of ``layer`` by ``k`` about ``origin``."""
    bbox = layer.getbbox()
    if bbox is None:
        return layer
    x0, y0, x1, y1 = bbox
    width = max(1, round((x1 - x0) * k))
    height = max(1, round((y1 - y0) * k))
    piece = layer.crop(bbox).resize((width, height), Image.Resampling.BICUBIC)
    px = round(origin[0] + (x0 - origin[0]) * k)
    py = round(origin[1] + (y0 - origin[1]) * k)
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.paste(piece, (px, py))
    return out


def _shift(layer: Image.Image, dy: float) -> Image.Image:
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.paste(layer, (0, round(dy)))
    return out


# ---------------------------------------------------------------------------
# Props. Each drawer receives the canvas, animation time and extra params.
# ---------------------------------------------------------------------------


def _draw_bed(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.rect(0, 30, 24, 170, MUTED_SLATE, radius=6)
    cv.rect(0, 150, 360, 176, SLATE, radius=4)
    cv.rect(10, 176, 24, 200, VOID, outline=False)
    cv.rect(336, 176, 350, 200, VOID, outline=False)
    cv.rect(24, 92, 360, 150, CREAM, radius=10)
    cv.rect(40, 74, 112, 104, WHITE, radius=12)
    cv.rect(120, 80, 300, 140, CORAL, radius=8)


def _draw_desk_monitor(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.rect(30, 10, 270, 180, SLATE, radius=12)
    cv.rect(44, 24, 256, 166, TEAL, radius=4)
    for i, width in enumerate((140, 96, 120, 70)):
        y = 44 + i * 26
        cv.rect(60, y, 60 + width, y + 8, CREAM, outline=False)
    cv.rect(135, 180, 165, 220, MUTED_SLATE)
    cv.rect(95, 220, 205, 236, SLATE, radius=6)
    cv.rect(0, 236, 300, 252, AMBER, radius=2)
    cv.rect(16, 252, 30, 260, MUTED_SLATE, outline=False)
    cv.rect(270, 252, 284, 260, MUTED_SLATE, outline=False)


def _draw_phone(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.rect(0, 0, 120, 220, SLATE, radius=16)
    cv.rect(10, 22, 110, 196, TEAL, radius=8)
    cv.rect(22, 40, 46, 64, CORAL, radius=5)
    cv.rect(56, 40, 80, 64, AMBER, radius=5)
    cv.rect(22, 74, 46, 98, CREAM, radius=5)
    cv.rect(56, 74, 80, 98, CORAL, radius=5)
    cv.rect(48, 8, 72, 14, VOID, radius=3, outline=False)
    cv.rect(42, 204, 78, 208, CREAM, radius=2, outline=False)


def _draw_couch(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.rect(30, 190, 44, 200, VOID, outline=False)
    cv.rect(316, 190, 330, 200, VOID, outline=False)
    cv.rect(20, 40, 340, 130, CORAL, radius=18)
    cv.rect(40, 120, 320, 170, CORAL, radius=10)
    cv.rect(0, 80, 50, 180, CORAL, radius=14)
    cv.rect(310, 80, 360, 180, CORAL, radius=14)
    cv.line([(180, 124), (180, 166)], INK, 2)


def _draw_fridge(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.rect(0, 0, 160, 340, CREAM, radius=14)
    cv.line([(0, 120), (160, 120)], INK, 2)
    cv.rect(132, 30, 140, 96, SLATE, radius=3, outline=False)
    cv.rect(132, 140, 140, 230, SLATE, radius=3, outline=False)
    cv.rect(30, 160, 58, 186, CORAL, radius=4)


def _draw_mirror(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.rect(0, 0, 180, 260, AMBER, radius=20)
    cv.rect(14, 14, 166, 246, SLATE, radius=12)
    cv.poly([(44, 30), (66, 30), (40, 140), (18, 140)], CREAM, outline=False)
    cv.poly([(78, 30), (86, 30), (60, 140), (52, 140)], CREAM, outline=False)


def _draw_heavy_bag(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.line([(80, 0), (80, 30)], MUTED_SLATE, 3)
    cv.rect(40, 30, 120, 48, SLATE, radius=6)
    cv.ellipse(30, 312, 130, 330, VOID, outline=False)
    cv.rect(20, 48, 140, 300, CORAL, radius=40)
    cv.rect(20, 130, 140, 142, AMBER, outline=False)
    cv.rect(20, 200, 140, 212, AMBER, outline=False)


_SEGMENTS = {
    "0": "abcdef",
    "1": "bc",
    "2": "abdeg",
    "3": "abcdg",
    "4": "bcfg",
    "5": "acdfg",
    "6": "acdefg",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcdfg",
}


def _draw_digit(cv: _Canvas, char: str, x: float, y: float, w: float, h: float) -> None:
    th = 9.0
    mid = y + h / 2
    rects = {
        "a": (x + th, y, x + w - th, y + th),
        "b": (x + w - th, y + th, x + w, mid - th / 2),
        "c": (x + w - th, mid + th / 2, x + w, y + h - th),
        "d": (x + th, y + h - th, x + w - th, y + h),
        "e": (x, mid + th / 2, x + th, y + h - th),
        "f": (x, y + th, x + th, mid - th / 2),
        "g": (x + th, mid - th / 2, x + w - th, mid + th / 2),
    }
    lit = _SEGMENTS[char]
    for seg, box in rects.items():
        cv.rect(*box, CORAL if seg in lit else SLATE, outline=False)


def _draw_alarm_clock(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.rect(0, 0, 300, 180, SLATE, radius=24)
    cv.rect(20, 30, 280, 150, VOID, radius=10)
    for x, char in ((44, "5"), (140, "0"), (206, "0")):
        _draw_digit(cv, char, x, 48, 52, 84)
    colon_x = 118.0
    dots = ((colon_x - 5, 67, colon_x + 5, 77), (colon_x - 5, 103, colon_x + 5, 113))
    strength = _pulse(t)

    def _dots(d: ImageDraw.ImageDraw) -> None:
        for x0, y0, x1, y1 in dots:
            d.ellipse(cv.box(x0 - 4, y0 - 4, x1 + 4, y1 + 4), fill=255)

    cv.glow(_dots, radius=7, rgb=RED_GLOW[:3], strength=strength)
    for x0, y0, x1, y1 in dots:
        cv.ellipse(x0, y0, x1, y1, RED_GLOW, outline=False)


def _draw_calendar(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    marks = max(0, min(35, int(p.get("marks", 0))))
    cv.rect(56, -6, 68, 22, SLATE, radius=4)
    cv.rect(172, -6, 184, 22, SLATE, radius=4)
    cv.rect(0, 0, 240, 260, CREAM, radius=14)
    cv.rect(0, 0, 240, 56, CORAL, radius=14)
    cv.rect(0, 40, 240, 56, CORAL, outline=False)
    cell_w = 208 / 7
    cell_h = (260 - 72 - 12) / 5
    for i in range(35):
        row, col = divmod(i, 7)
        cx = 16 + col * cell_w + cell_w / 2
        cy = 72 + row * cell_h + cell_h / 2
        if i < marks:
            arm = min(cell_w, cell_h) * 0.3
            cv.line([(cx - arm, cy - arm), (cx + arm, cy + arm)], CORAL, 3)
            cv.line([(cx + arm, cy - arm), (cx - arm, cy + arm)], CORAL, 3)
        else:
            cv.ellipse(cx - 2.5, cy - 2.5, cx + 2.5, cy + 2.5, MUTED_SLATE, outline=False)


def _draw_window(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.rect(0, 0, 240, 292, CREAM, radius=4)
    cv.rect(14, 14, 226, 278, TEAL)
    cv.rect(110, 14, 130, 278, CREAM)
    cv.rect(14, 140, 226, 158, CREAM)
    cv.ellipse(36, 44, 92, 78, WHITE, outline=False)
    cv.ellipse(66, 32, 110, 66, WHITE, outline=False)
    cv.rect(-12, 278, 252, 300, SLATE, radius=4)


def _draw_door(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.rect(0, 0, 200, 360, MUTED_SLATE, radius=4)
    cv.rect(16, 16, 184, 360, AMBER)
    cv.rect(34, 40, 166, 170, AMBER)
    cv.rect(34, 190, 166, 340, AMBER)
    cv.ellipse(146, 250, 162, 266, CREAM)


def _draw_chair(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.line([(40, 160), (30, 258)], SLATE, 8)
    cv.line([(140, 160), (150, 258)], SLATE, 8)
    cv.line([(40, 200), (140, 200)], MUTED_SLATE, 4)
    cv.rect(26, 0, 154, 136, SLATE, radius=16)
    cv.rect(44, 16, 136, 116, MUTED_SLATE, radius=8)
    cv.rect(10, 130, 170, 160, CORAL, radius=10)


def _draw_mountain(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    w, h = cv.n(480), cv.n(300)
    sky = _vgradient(w, h, SLATE, AMBER)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=cv.n(18), fill=255)
    cv.layer.paste(sky, (round(cv.ox), round(cv.oy)), mask)
    cv.ellipse(256, 150, 346, 240, CREAM, outline=False)
    back = [[(0, 300), (150, 90), (300, 300)], [(180, 300), (340, 120), (480, 300)]]
    mid = [[(-40, 300), (90, 170), (220, 300)], [(130, 300), (300, 150), (440, 300)]]
    front = [[(60, 300), (200, 230), (340, 300)]]
    for layer, fill in ((back, MUTED_SLATE), (mid, SLATE), (front, VOID)):
        for tri in layer:
            cv.poly(tri, fill)


def _draw_sun_glow(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cx = cy = 150.0
    cv.glow(
        lambda d: d.ellipse(cv.box(cx - 120, cy - 120, cx + 120, cy + 120), fill=255),
        radius=40,
        rgb=AMBER[:3],
        strength=_pulse(t),
    )
    for i in range(8):
        angle = i * math.pi / 4
        p0 = (cx + 74 * math.cos(angle), cy + 74 * math.sin(angle))
        p1 = (cx + 100 * math.cos(angle), cy + 100 * math.sin(angle))
        cv.line([p0, p1], AMBER, 6)
    cv.ellipse(cx - 64, cy - 64, cx + 64, cy + 64, AMBER, outline=False)
    cv.ellipse(cx - 36, cy - 36, cx + 36, cy + 36, CREAM, outline=False)


def _draw_shadow_demon(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    strength = _pulse(t)
    cv.glow(
        lambda d: d.ellipse(cv.box(20, 20, 240, 320), fill=255),
        radius=22,
        rgb=RED_GLOW[:3],
        strength=strength,
    )
    cv.poly([(88, 70), (70, 8), (108, 52)], VOID, outline=False)
    cv.poly([(172, 70), (190, 8), (152, 52)], VOID, outline=False)
    cv.ellipse(76, 30, 184, 140, VOID, outline=False)
    cv.poly([(64, 130), (196, 130), (236, 318), (24, 318)], VOID, outline=False)
    eyes = ((98, 74, 118, 96), (142, 74, 162, 96))

    def _eyes(d: ImageDraw.ImageDraw) -> None:
        for x0, y0, x1, y1 in eyes:
            d.ellipse(cv.box(x0 - 4, y0 - 4, x1 + 4, y1 + 4), fill=255)

    cv.glow(_eyes, radius=5, rgb=RED_GLOW[:3], strength=strength)
    for x0, y0, x1, y1 in eyes:
        cv.ellipse(x0, y0, x1, y1, RED_GLOW, outline=False)


def _draw_speech_bubble(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    text = str(p.get("text", ""))
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    left, _, right, _ = measure.textbbox((0, 0), text, font=_font(28))
    width = max(160.0, (right - left) + 56.0)
    height = 96.0
    cv.rect(0, 0, width, height, CREAM, radius=22)
    tail_x = width * 0.22
    cv.poly(
        [(tail_x, height - 2), (tail_x + 34, height - 2), (tail_x - 10, height + 26)],
        CREAM,
        outline=False,
    )
    cv.line([(tail_x, height), (tail_x - 10, height + 26), (tail_x + 34, height)], INK, 2)
    if text:
        cv.draw.text(
            cv.pt(width / 2, height / 2),
            text,
            font=_font(cv.n(28)),
            fill=VOID,
            anchor="mm",
        )


def _draw_bar_chart(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    highlight = p.get("highlight")
    if highlight is not None and highlight not in (0, 1, 2):
        raise ValueError(f"bar_chart highlight must be 0, 1, 2 or None, got {highlight!r}")
    baseline = 236
    cv.line([(0, baseline), (360, baseline)], INK, 2)
    for index, (x, height) in enumerate(((28, 150), (140, 96), (252, 196))):
        fill = CORAL if index == highlight else TEAL
        cv.rect(x, baseline - height, x + 80, baseline, fill, radius=4)


def _draw_shield(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    outer = [(100, 0), (200, 30), (190, 130), (100, 240), (10, 130), (0, 30)]
    inner = [(100 + (x - 100) * 0.78, 120 + (y - 120) * 0.78) for x, y in outer]
    cv.poly(outer, TEAL)
    cv.poly(inner, CREAM)
    cv.line([(58, 122), (90, 156), (146, 92)], CORAL, 16)


def _star(cx: float, cy: float, r_out: float, r_in: float) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for i in range(10):
        radius = r_out if i % 2 == 0 else r_in
        angle = -math.pi / 2 + i * math.pi / 5
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def _draw_trophy(cv: _Canvas, t: float, p: dict[str, Any]) -> None:
    cv.arc(4, 36, 58, 104, 90, 270, INK, 12)
    cv.arc(142, 36, 196, 104, -90, 90, INK, 12)
    cv.arc(4, 36, 58, 104, 90, 270, AMBER, 6)
    cv.arc(142, 36, 196, 104, -90, 90, AMBER, 6)
    cv.poly([(30, 14), (170, 14), (156, 110), (128, 146), (72, 146), (44, 110)], AMBER)
    cv.rect(24, 6, 176, 22, CORAL, radius=4)
    cv.rect(88, 146, 112, 190, AMBER)
    cv.rect(60, 190, 140, 206, SLATE, radius=4)
    cv.rect(44, 206, 156, 232, MUTED_SLATE, radius=6)
    cv.poly(_star(100, 80, 22, 9), CREAM, outline=False)
    cv.line([(54, 36), (64, 90)], CREAM, 6)


_DRAWERS: dict[str, Callable[[_Canvas, float, dict[str, Any]], None]] = {
    "bed": _draw_bed,
    "desk_monitor": _draw_desk_monitor,
    "phone": _draw_phone,
    "couch": _draw_couch,
    "fridge": _draw_fridge,
    "mirror": _draw_mirror,
    "heavy_bag": _draw_heavy_bag,
    "alarm_clock_7seg": _draw_alarm_clock,
    "calendar": _draw_calendar,
    "window": _draw_window,
    "door": _draw_door,
    "chair": _draw_chair,
    "mountain": _draw_mountain,
    "sun_glow": _draw_sun_glow,
    "shadow_demon": _draw_shadow_demon,
    "speech_bubble": _draw_speech_bubble,
    "bar_chart": _draw_bar_chart,
    "shield": _draw_shield,
    "trophy": _draw_trophy,
}

PROP_NAMES: tuple[str, ...] = (
    "bed",
    "desk_monitor",
    "phone",
    "couch",
    "fridge",
    "mirror",
    "heavy_bag",
    "alarm_clock_7seg",
    "calendar",
    "window",
    "door",
    "chair",
    "mountain",
    "sun_glow",
    "shadow_demon",
    "speech_bubble",
    "bar_chart",
    "shield",
    "trophy",
)


def draw_prop(
    img: Image.Image,
    name: str,
    origin: tuple[float, float],
    scale: float = 1.0,
    t: float = 0.0,
    *,
    entrance_start: float | None = None,
    entrance: str = "pop",
    wiggle: float = 0.0,
    **params: Any,
) -> None:
    """Composite the named prop onto ``img`` in place.

    ``entrance_start`` (same timeline as ``t``) makes the prop appear at that time:
    nothing is drawn before it; over 0.35 s it either pops in (``entrance="pop"``,
    scale 0 to 1 with ease-out-back overshoot) or drops 120 px with one bounce
    (``entrance="drop"``). ``wiggle`` (degrees) adds a sin rotation wobble with a
    2.2 s period about ``origin``, starting once the entrance has finished.

    Raises ValueError for an unknown ``name``, a non-positive ``scale``, an unknown
    ``entrance`` or a negative ``wiggle``.
    """
    drawer = _DRAWERS.get(name)
    if drawer is None:
        raise ValueError(f"unknown prop {name!r}; expected one of {PROP_NAMES}")
    if not scale > 0:
        raise ValueError(f"scale must be positive, got {scale!r}")
    if entrance not in _ENTRANCES:
        raise ValueError(f"entrance must be one of {_ENTRANCES}, got {entrance!r}")
    if wiggle < 0:
        raise ValueError(f"wiggle must be non-negative, got {wiggle!r}")

    anim_t = float(t)
    ox, oy = float(origin[0]), float(origin[1])
    k = 1.0
    dy = 0.0
    elapsed: float | None = None
    if entrance_start is not None:
        elapsed = anim_t - float(entrance_start)
        if elapsed < 0:
            return
        progress = min(1.0, elapsed / _ENTRANCE_S)
        if entrance == "pop":
            k = max(_MIN_ENTRANCE_SCALE, _ease_out_back(progress))
        else:
            dy = _drop_offset(progress)

    angle = 0.0
    settled = elapsed is None or elapsed >= _ENTRANCE_S
    if wiggle > 0 and settled:
        angle = wiggle * math.sin(2 * math.pi * anim_t / _WIGGLE_PERIOD_S)

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    cv = _Canvas(layer, (ox, oy), float(scale))
    drawer(cv, anim_t, params)

    if k != 1.0:
        layer = _scale_about(layer, (ox, oy), k)
    if dy != 0.0:
        layer = _shift(layer, dy)
    if angle != 0.0:
        layer = layer.rotate(angle, resample=Image.Resampling.BICUBIC, center=(ox, oy))

    if img.mode == "RGBA":
        img.alpha_composite(layer)
    else:
        base = img.convert("RGBA")
        base.alpha_composite(layer)
        img.paste(base.convert(img.mode))


__all__ = ["PROP_NAMES", "draw_prop"]
