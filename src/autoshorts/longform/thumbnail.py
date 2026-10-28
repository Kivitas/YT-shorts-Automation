"""1280x720 thumbnail: big uppercase text plus a stick figure."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from autoshorts.longform.drawer import BG, YELLOW, draw_figure, load_font

THUMB_W, THUMB_H = 1280, 720


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
    img = Image.new("RGB", (THUMB_W, THUMB_H), BG)
    d = ImageDraw.Draw(img)
    for x in range(0, THUMB_W, 64):
        d.line([(x, 0), (x, THUMB_H)], fill=(26, 38, 72), width=1)
    for y in range(0, THUMB_H, 64):
        d.line([(0, y), (THUMB_W, y)], fill=(26, 38, 72), width=1)

    lines = _thumb_text(title)
    max_w = 740
    size = 170
    font = load_font(size)
    while size > 40 and max(d.textlength(ln, font=font) for ln in lines) > max_w:
        size -= 6
        font = load_font(size)
    line_h = int(size * 1.15)
    y = (THUMB_H - line_h * len(lines)) // 2
    for ln in lines:
        d.text((64 + 4, y + 4), ln, font=font, fill=(0, 0, 0))
        d.text((64, y), ln, font=font, fill=YELLOW)
        y += line_h

    from autoshorts.longform.drawer import POSES

    draw_figure(d, POSES["lift"], (1020, 470), scale=2.4, width=12)
    d.line([(800, 590), (1240, 590)], fill=(80, 160, 255), width=6)
    img.save(out_path, format="PNG")
    return out_path
