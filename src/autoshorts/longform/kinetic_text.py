"""In-scene kinetic typography for explainer scenes.

Every ``draw_*`` helper paints onto an image in place by compositing an RGBA layer.
Each helper is a no-op outside its own time window, so callers can invoke them on
every frame without checking the window first. All motion is a pure function of
``t``, so the same frame always renders the same pixels.
"""

from __future__ import annotations

import math
import random
import re
import zlib
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

RGB = tuple[int, int, int]

_BOLD_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)

NARRATION_FONT_SIZE = 64
NARRATION_MAX_WIDTH = 1600
INNER_VOICE_FONT_SIZE = 56
NUMBER_POP_FONT_SIZE = 220
SHATTER_FONT_SIZE = 96
POP_DURATION = 0.2
SHATTER_GRAVITY = 1400.0
_PAD = 48  # transparent margin around every rendered glyph layer (room for glow and rotation)

_SCRATCH = ImageDraw.Draw(Image.new("L", (1, 1)))


@lru_cache(maxsize=64)
def _font(size: int) -> Any:
    """Load a bold system TrueType font, falling back to PIL's built-in default font."""
    for path in _BOLD_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow without sized default fonts
        return ImageFont.load_default()


def _text_width(text: str, font: Any) -> float:
    return float(_SCRATCH.textlength(text, font=font))


def _normalise(word: str) -> str:
    return re.sub(r"\W", "", word).lower()


def _envelope(t: float, start: float, end: float, fade: float) -> float:
    """Opacity multiplier in [0, 1]: linear fade in from start and out before end."""
    if fade <= 0:
        return 1.0
    return max(0.0, min(1.0, (t - start) / fade, (end - t) / fade))


def _ink_layer(
    text: str,
    font: Any,
    fill: tuple[int, ...],
    stroke_width: int = 0,
    stroke_fill: tuple[int, ...] | None = None,
) -> Image.Image:
    """Render text on a transparent RGBA layer; the ink box starts at (_PAD, _PAD)."""
    bbox = _SCRATCH.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    width = max(1, bbox[2] - bbox[0])
    height = max(1, bbox[3] - bbox[1])
    layer = Image.new("RGBA", (width + 2 * _PAD, height + 2 * _PAD), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(
        (_PAD - bbox[0], _PAD - bbox[1]),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )
    return layer


def _blit(img: Image.Image, src: Image.Image, x: int, y: int) -> None:
    """Alpha-composite an RGBA layer onto img with its top-left at (x, y), clipped to bounds."""
    width, height = img.size
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + src.width), min(height, y + src.height)
    if x0 >= x1 or y0 >= y1:
        return
    sub = src.crop((x0 - x, y0 - y, x1 - x, y1 - y))
    base = img.crop((x0, y0, x1, y1)).convert("RGBA")
    base.alpha_composite(sub)
    img.paste(base.convert(img.mode), (x0, y0))


def wrap_lines(text: str, max_width: float, font: Any) -> list[str]:
    """Greedy word wrap so each line fits ``max_width`` px.

    A single word wider than ``max_width`` gets a line of its own rather than being split.
    """
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if current and _text_width(candidate, font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines


def draw_narration(
    img: Image.Image,
    text: str,
    t: float,
    start: float,
    end: float,
    y: int = 240,
    fade: float = 0.5,
    color: RGB = (234, 230, 223),
    highlight: str | None = None,
    highlight_color: RGB = (255, 200, 60),
) -> None:
    """Centred narration line that reveals word by word across [start, end].

    Word ``i`` appears at ``start + i * (end - start) / n``. The whole block fades in and
    out over ``fade`` seconds. ``highlight`` names one word (punctuation and case are
    ignored) to draw in ``highlight_color``.
    """
    if not text.strip() or t < start or t > end:
        return
    words = text.split()
    span = max(end - start, 1e-6)
    alpha_mul = _envelope(t, start, end, fade)
    if alpha_mul <= 0:
        return

    font = _font(NARRATION_FONT_SIZE)
    lines = wrap_lines(text, NARRATION_MAX_WIDTH, font)
    space_w = _text_width(" ", font)
    line_h = int(NARRATION_FONT_SIZE * 1.25)

    wanted = _normalise(highlight) if highlight else None
    hi_index = next((i for i, w in enumerate(words) if wanted and _normalise(w) == wanted), -1)

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = int(255 * alpha_mul)
    index = 0
    for row, line in enumerate(lines):
        line_words = line.split()
        widths = [_text_width(w, font) for w in line_words]
        total = sum(widths) + space_w * (len(line_words) - 1)
        x = (img.width - total) / 2
        row_y = y + row * line_h
        for word, width in zip(line_words, widths, strict=True):
            if t >= start + index * span / len(words):
                rgb = highlight_color if index == hi_index else color
                draw.text(
                    (x, row_y),
                    word,
                    font=font,
                    fill=(*rgb, alpha),
                    stroke_width=2,
                    stroke_fill=(0, 0, 0, int(alpha * 0.8)),
                )
            x += width + space_w
            index += 1
    _blit(img, layer, 0, 0)


def draw_inner_voice(
    img: Image.Image,
    text: str,
    anchor: tuple[int, int],
    t: float,
    start: float,
    end: float,
    color: RGB = (230, 57, 70),
) -> None:
    """Red inner-voice line at ``anchor`` (top-left of the text) with a soft glow and jitter."""
    if not text.strip() or t < start or t > end:
        return
    env = _envelope(t, start, end, 0.3)
    if env <= 0:
        return
    # Small deterministic tremor, about 4.5 Hz, that reads as a nervous inner thought.
    jitter_x = math.sin(t * 28) * 1.5
    jitter_y = math.cos(t * 28) * 1.0

    font = _font(INNER_VOICE_FONT_SIZE)
    glow_alpha = int(190 * env)
    glow = _ink_layer(
        text, font, (*color, glow_alpha), stroke_width=6, stroke_fill=(*color, glow_alpha)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=10))
    body = _ink_layer(text, font, (*color, int(255 * env)))

    ox = round(anchor[0] - _PAD + jitter_x)
    oy = round(anchor[1] - _PAD + jitter_y)
    _blit(img, glow, ox, oy)
    _blit(img, body, ox, oy)


def draw_number_pop(
    img: Image.Image,
    value: int | float | str,
    anchor: tuple[int, int],
    t: float,
    start: float,
    color: RGB,
) -> None:
    """Big number centred on ``anchor`` that scales from 1.3x to 1.0x over 0.2 s, then holds.

    The number stays visible from ``start`` onwards (rep counters keep their value on
    screen). Scaling uses the font size, so every frame is crisp.
    """
    if t < start:
        return
    k = min(1.0, (t - start) / POP_DURATION)
    eased = 1.0 - (1.0 - k) ** 2
    scale = 1.3 - 0.3 * eased
    font = _font(max(8, round(NUMBER_POP_FONT_SIZE * scale)))
    layer = _ink_layer(
        str(value),
        font,
        (*color, 255),
        stroke_width=4,
        stroke_fill=(0, 0, 0, 255),
    )
    ink_w = layer.width - 2 * _PAD
    ink_h = layer.height - 2 * _PAD
    _blit(img, layer, round(anchor[0] - ink_w / 2 - _PAD), round(anchor[1] - ink_h / 2 - _PAD))


def draw_shatter_text(
    img: Image.Image,
    text: str,
    anchor: tuple[int, int],
    t: float,
    start: float,
    duration: float = 1.2,
    color: RGB = (230, 57, 70),
) -> None:
    """Letters burst outward from ``anchor`` after ``start``, falling under gravity and spinning.

    Each letter gets a velocity and spin rate from a PRNG seeded by the text and the
    letter index, so the effect is deterministic. Letters fade out linearly in alpha
    squared and are gone at ``start + duration``; the image is untouched from then on.
    """
    tau = t - start
    if not text.strip() or tau < 0 or tau >= duration:
        return
    fade = 1.0 - (tau / duration) ** 2
    alpha = int(255 * fade)
    if alpha <= 0:
        return

    font = _font(SHATTER_FONT_SIZE)
    total = _text_width(text, font)
    origin_x = anchor[0] - total / 2
    seed_base = zlib.crc32(text.encode("utf-8"))

    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        rng = random.Random(seed_base * 1_000_003 + i)
        vx = rng.uniform(-260.0, 260.0)
        vy = rng.uniform(-420.0, -120.0)  # negative y is up, so letters start by flying up
        omega = rng.uniform(-420.0, 420.0)  # degrees per second

        advance = _text_width(ch, font)
        cx = origin_x + _text_width(text[:i], font) + advance / 2 + vx * tau
        cy = anchor[1] + vy * tau + 0.5 * SHATTER_GRAVITY * tau * tau

        glyph = _ink_layer(ch, font, (*color, alpha))
        spun = glyph.rotate(omega * tau, resample=Image.Resampling.BICUBIC, expand=True)
        _blit(img, spun, round(cx - spun.width / 2), round(cy - spun.height / 2))
