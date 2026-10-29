"""1280x720 thumbnail: sunburst background, big outlined title and a cartoon character."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from autoshorts.longform.drawer import (
    OUTLINE,
    POSES,
    SHIRT_BLUE,
    WHITE,
    YELLOW,
    draw_character,
    draw_sticker,
    load_font,
)

THUMB_W, THUMB_H = 1280, 720
RAY_A = (255, 196, 60)
RAY_B = (255, 140, 50)


def _thumb_text(title: str) -> list[str]:
    words = title.upper().split()[:5]
    while words and len(" ".join(words)) > 40:
        words.pop()
    if not words:
        words = ["WATCH", "THIS"]
    # Wrap to at most 3 lines.
    lines: list[str] = []
    per = 2 if len(words) > 3 else 1 if len(words) <= 2 else 2
    for i in range(0, len(words), per):
        lines.append(" ".join(words[i : i + per]))
    return lines


def make_thumbnail(title: str, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (THUMB_W, THUMB_H), RAY_B)
    d = ImageDraw.Draw(img)

    # Sunburst rays radiating from behind the character.
    cx, cy, radius = 900, 340, 1600
    for i in range(24):
        a0 = 2 * math.pi * i / 24
        a1 = 2 * math.pi * (i + 1) / 24
        fill = RAY_A if i % 2 == 0 else RAY_B
        d.polygon(
            [(cx, cy), (cx + radius * math.cos(a0), cy + radius * math.sin(a0)),
             (cx + radius * math.cos(a1), cy + radius * math.sin(a1))],
            fill=fill,
        )  # fmt: skip

    lines = _thumb_text(title)
    max_w = 640
    size = 150
    font = load_font(size)
    while size > 40 and max(d.textlength(ln, font=font) for ln in lines) > max_w:
        size -= 6
        font = load_font(size)
    line_h = int(size * 1.12)
    y = (THUMB_H - line_h * len(lines)) // 2
    for i, ln in enumerate(lines):
        fill = YELLOW if i == len(lines) - 1 else WHITE
        d.text((60, y), ln, font=font, fill=fill, stroke_width=12, stroke_fill=OUTLINE)
        y += line_h

    draw_character(d, POSES["lift"], (1020, 500), scale=2.0, shirt=SHIRT_BLUE, mood="excited", flip=1)
    draw_sticker(d, "star", 1220, 170, 90)
    draw_sticker(d, "boom", 1210, 560, 80, "WOW")
    img.save(out_path, format="PNG")
    return out_path
