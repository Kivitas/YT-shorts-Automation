"""1280x720 high-CTR YouTube thumbnail generator: atmospheric dark gradient, neon badge, bold typography, and character focal point."""

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
BG_TOP = (12, 16, 30)
BG_BOTTOM = (26, 18, 48)
RAY_A = (36, 48, 86)
RAY_B = (20, 26, 52)
NEON_CYAN = (0, 228, 255)
NEON_EMERALD = (16, 215, 138)
NEON_CRIMSON = (255, 72, 104)

_GENRE_BADGES: dict[str, tuple[str, tuple[int, int, int]]] = {
    "history": ("HISTORICAL DEEP DIVE", (255, 196, 60)),
    "science": ("SCIENCE EXPLAINED", NEON_CYAN),
    "comparison": ("ULTIMATE VS BREAKDOWN", NEON_CRIMSON),
    "finance": ("WEALTH & COMPOUNDING", NEON_EMERALD),
    "parody": ("REALITY VS EXPECTATION", (255, 96, 180)),
    "motivation": ("MINDSET & SYSTEMS", NEON_CYAN),
    "self_improvement": ("HIGH-RETENTION GUIDE", YELLOW),
}


def _thumb_text(title: str) -> list[str]:
    clean = title.replace(":", " ").upper()
    words = clean.split()[:6]
    while words and len(" ".join(words)) > 42:
        words.pop()
    if not words:
        words = ["WATCH", "THIS"]
    lines: list[str] = []
    per = 2 if len(words) >= 3 else 1
    for i in range(0, len(words), per):
        lines.append(" ".join(words[i : i + per]))
    return lines[:3]


def _infer_badge(title: str, genre: str) -> tuple[str, tuple[int, int, int]]:
    if genre in _GENRE_BADGES:
        return _GENRE_BADGES[genre]
    from autoshorts.longform.topics import infer_genre

    g, _ = infer_genre(title)
    return _GENRE_BADGES.get(g, ("FULL VISUAL BREAKDOWN", NEON_CYAN))


def make_thumbnail(title: str, out_path: Path, genre: str = "auto") -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (THUMB_W, THUMB_H), BG_TOP)
    d = ImageDraw.Draw(img)

    # Atmospheric dark gradient backdrop
    for y in range(THUMB_H):
        k = y / max(1, THUMB_H - 1)
        col = tuple(round(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * k) for i in range(3))
        d.line([(0, y), (THUMB_W, y)], fill=col)

    # Radial spotlight rays behind the focal character on the right
    cx, cy, radius = 980, 360, 1500
    for i in range(24):
        if i % 2 != 0:
            continue
        a0 = 2 * math.pi * i / 24
        a1 = 2 * math.pi * (i + 1) / 24
        d.polygon(
            [
                (cx, cy),
                (cx + radius * math.cos(a0), cy + radius * math.sin(a0)),
                (cx + radius * math.cos(a1), cy + radius * math.sin(a1)),
            ],
            fill=RAY_A,
        )

    # Subtle glowing chart/diagram motif on the right background card
    d.rounded_rectangle(
        [750, 90, 1230, 640],
        radius=28,
        fill=(18, 26, 46),
        outline=NEON_CYAN,
        width=4,
    )
    chart_pts = [(790, 560), (880, 510), (960, 440), (1055, 310), (1185, 150)]
    d.line(chart_pts, fill=NEON_EMERALD, width=10, joint="curve")
    for px, py in chart_pts:
        d.ellipse([px - 10, py - 10, px + 10, py + 10], fill=YELLOW, outline=OUTLINE, width=3)

    # Neon accent badge at top-left
    badge_label, badge_col = _infer_badge(title, genre)
    bfont = load_font(26, True)
    bw = int(d.textlength(badge_label, font=bfont)) + 48
    d.rounded_rectangle([56, 56, 56 + bw, 112], radius=16, fill=badge_col, outline=OUTLINE, width=4)
    d.text((56 + bw // 2, 84), badge_label, font=bfont, fill=OUTLINE, anchor="mm")

    # High-contrast multi-line impact typography
    lines = _thumb_text(title)
    max_w = 650
    size = 128
    font = load_font(size, True)
    while size > 42 and max(d.textlength(ln, font=font) for ln in lines) > max_w:
        size -= 6
        font = load_font(size, True)
    line_h = int(size * 1.15)
    y = max(145, (THUMB_H - line_h * len(lines)) // 2 + 24)
    for i, ln in enumerate(lines):
        is_last = i == len(lines) - 1
        if is_last:
            tw = int(d.textlength(ln, font=font)) + 36
            d.rounded_rectangle(
                [48, y - 6, 48 + tw, y + line_h - 10],
                radius=16,
                fill=YELLOW,
                outline=OUTLINE,
                width=5,
            )
            d.text((66, y), ln, font=font, fill=OUTLINE)
        else:
            d.text((64, y + 6), ln, font=font, fill=(8, 8, 12))
            d.text((58, y), ln, font=font, fill=WHITE, stroke_width=10, stroke_fill=OUTLINE)
        y += line_h

    # Expressive character focal point with active viseme mouth & stickers
    draw_character(
        d,
        POSES["lift"],
        (1000, 505),
        scale=2.05,
        shirt=SHIRT_BLUE,
        mood="hyped",
        flip=-1,
        viseme="AI",
        is_speaking=True,
    )
    draw_sticker(d, "star", 1190, 150, 74)
    draw_sticker(d, "boom", 815, 565, 72, "WOW")

    # High-contrast outer frame border
    d.rectangle([0, 0, THUMB_W - 1, THUMB_H - 1], outline=badge_col, width=6)
    img.save(out_path, format="PNG")
    return out_path
