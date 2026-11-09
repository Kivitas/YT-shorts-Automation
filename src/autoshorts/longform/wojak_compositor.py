"""Pure-PIL 16:9 (1920x1080) Layout Renderer matching the SapX style.

Exact specifications:
- Frame Size: 1920x1080 (16:9 Landscape).
- Scene 0: Parody Disclaimer Card (3.5s):
  - Dark sleek background with subtle vignette and border.
  - Top gold/white warning badge: `⚠️ PARODY DISCLAIMER`.
  - Centered clean white disclaimer text auto-wrapped with crisp typography.
- Scenes 1 to N: Country Meme Scenes (4.5s each):
  - Top White Header Bar: `y: 0` to `y: 110` (height 110px), solid white background,
    1px subtle bottom border.
  - Centered Country Name: bold black uppercase sans-serif font (e.g., USA, UK, SWEDEN).
  - Photographic Background: 1920x970 below the header bar (y: 110 to 1080).
  - Character (Wojak / Meme figure): Scaled to ~600-750px height, anchored to the bottom
    on the left or right side.
  - Floating White Punchline Card:
    - Positioned on the OPPOSITE side of the character.
    - Floating rounded rectangle (radius 16px, solid white fill).
    - Realistic soft Gaussian-blurred drop shadow.
    - Inner text: Bold black text (36-44px), auto-wrapped to 1-3 lines, centered.
"""

from __future__ import annotations

import logging
import math
import os
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from autoshorts.longform.flags import get_country_flag

logger = logging.getLogger(__name__)

FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080

HEADER_HEIGHT = 110
HEADER_RULE_PX = 1

# Colors
COLOR_WHITE = (255, 255, 255)
COLOR_BLACK = (16, 16, 16)
COLOR_HEADER_BORDER = (220, 224, 230)
COLOR_DISCLAIMER_BG = (18, 20, 24)
COLOR_DISCLAIMER_CARD = (26, 30, 38)
COLOR_DISCLAIMER_BORDER = (60, 68, 80)
COLOR_GOLD = (245, 180, 28)
COLOR_GOLD_BG = (42, 34, 16)
COLOR_SHADOW = (0, 0, 0, 75)

# Typography candidates (Windows, Linux, macOS)
FONT_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/Montserrat-ExtraBold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/ARLRDBD.TTF",
    "C:/Windows/Fonts/trebucbd.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)

DISCLAIMER_BODY_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/trebuc.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _resolve_font(bold: bool = True) -> str | None:
    """Find a high-quality TrueType font candidate on the system."""
    candidates = FONT_CANDIDATES if bold else (*DISCLAIMER_BODY_CANDIDATES, *FONT_CANDIDATES)
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


@lru_cache(maxsize=128)
def get_font(size: int, bold: bool = True) -> Any:
    """Load cached ImageFont instance."""
    path = _resolve_font(bold=bold)
    if path is not None:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):
        return ImageFont.load_default()


def _ink_metrics(font: Any, sample_text: str = "H") -> tuple[int, int]:
    """Return (ink top offset, ink height) for accurate vertical centering."""
    bbox = font.getbbox(sample_text)
    if bbox:
        return int(bbox[1]), int(bbox[3] - bbox[1])
    return 0, 24


def wrap_text(font: Any, text: str, max_width: float) -> list[str]:
    """Greedily wrap text to fit within max_width in pixels."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if not current or font.getlength(candidate) <= max_width:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def fit_title(
    text: str, max_w: int = 1500, max_h: int = 80, initial_size: int = 56
) -> tuple[Any, int]:
    """Fit country header title to available dimensions."""
    size = initial_size
    clean_text = text.strip().upper()
    while size >= 24:
        font = get_font(size, bold=True)
        length = font.getlength(clean_text)
        _, ink_h = _ink_metrics(font, clean_text)
        if length <= max_w and ink_h <= max_h:
            return font, size
        size -= 2
    return get_font(24, bold=True), 24


def fit_punchline(
    text: str,
    max_w: int = 760,
    max_h: int = 240,
    max_lines: int = 3,
    initial_size: int = 42,
    min_size: int = 28,
) -> tuple[Any, list[str], int]:
    """Find the largest font size (up to max_lines) that comfortably fits the punchline."""
    clean_text = " ".join(text.split())
    size = initial_size
    while size >= min_size:
        font = get_font(size, bold=True)
        lines = wrap_text(font, clean_text, max_w)
        line_height = math.ceil(size * 1.32)
        total_h = len(lines) * line_height
        if len(lines) <= max_lines and total_h <= max_h:
            return font, lines, line_height
        size -= 2

    font = get_font(min_size, bold=True)
    lines = wrap_text(font, clean_text, max_w)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1 :])]
        # Ellipsize last line if needed
        last = lines[-1]
        while last and font.getlength(last + "...") > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    line_height = math.ceil(min_size * 1.32)
    return font, lines, line_height


def create_soft_drop_shadow(
    width: int,
    height: int,
    radius: int = 16,
    blur: int = 20,
    offset_y: int = 12,
    shadow_color: tuple[int, int, int, int] = COLOR_SHADOW,
) -> tuple[Image.Image, int, int]:
    """Create a soft Gaussian-blurred drop shadow for a floating card.

    Returns:
        (shadow_image, pad_x, pad_y) where pad_x/pad_y are the extra margins
        needed to center the shadow under the card.
    """
    pad = blur * 2
    sw = width + pad * 2
    sh = height + pad * 2
    shadow = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)

    x0 = pad
    y0 = pad + offset_y
    x1 = pad + width
    y1 = pad + height + offset_y

    sdraw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=shadow_color)
    blurred = shadow.filter(ImageFilter.GaussianBlur(blur))
    return blurred, pad, pad


# ============================================================================
# Scene 0: Parody Disclaimer Card
# ============================================================================


def render_disclaimer_frame(
    size: tuple[int, int] = (FRAME_WIDTH, FRAME_HEIGHT),
    disclaimer_text: str | None = None,
    warning_badge: str = "PARODY & SATIRE DISCLAIMER",
    sub_notice: str = "CREATED FOR COMEDIC & ENTERTAINMENT PURPOSES ONLY • ALL STEREOTYPES ARE EXAGGERATED MEMES",
) -> Image.Image:
    """Render Scene 0 Parody Disclaimer Card in sleek dark aesthetic."""
    w, h = size
    canvas = Image.new("RGB", (w, h), COLOR_DISCLAIMER_BG)
    draw = ImageDraw.Draw(canvas)

    # Sleek dark radial-like background with subtle vignette lines
    for i in range(0, h, 8):
        shade = max(14, int(22 - (i / h) * 10))
        draw.line([(0, i), (w, i)], fill=(shade, shade + 2, shade + 6), width=1)

    # Central Disclaimer Card Box
    card_w = min(1500, w - 240)
    card_h = min(740, h - 220)
    card_x0 = (w - card_w) // 2
    card_y0 = (h - card_h) // 2
    card_y1 = card_y0 + card_h

    # Card shadow
    shadow_img, pad_x, pad_y = create_soft_drop_shadow(
        card_w, card_h, radius=24, blur=28, offset_y=16, shadow_color=(0, 0, 0, 140)
    )
    canvas.paste(shadow_img, (card_x0 - pad_x, card_y0 - pad_y), shadow_img)

    # Card background and border
    card_canvas = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(card_canvas)
    cdraw.rounded_rectangle(
        (0, 0, card_w - 1, card_h - 1),
        radius=24,
        fill=(*COLOR_DISCLAIMER_CARD, 245),
        outline=(*COLOR_DISCLAIMER_BORDER, 255),
        width=2,
    )
    canvas.paste(card_canvas, (card_x0, card_y0), card_canvas)
    draw = ImageDraw.Draw(canvas)

    # Top Warning Badge / Pill
    badge_font = get_font(32, bold=True)
    badge_text = warning_badge.strip()
    badge_text_w = badge_font.getlength(badge_text)
    badge_pad_x = 36
    badge_w = int(badge_text_w + badge_pad_x * 2)
    badge_h = 56
    badge_x0 = (w - badge_w) // 2
    badge_y0 = card_y0 + 55
    badge_x1 = badge_x0 + badge_w
    badge_y1 = badge_y0 + badge_h

    draw.rounded_rectangle(
        (badge_x0, badge_y0, badge_x1, badge_y1),
        radius=14,
        fill=COLOR_GOLD_BG,
        outline=COLOR_GOLD,
        width=2,
    )
    ink_top, ink_h = _ink_metrics(badge_font, badge_text)
    bx = (w - badge_text_w) / 2
    by = badge_y0 + (badge_h - ink_h) / 2 - ink_top
    draw.text((bx, by), badge_text, font=badge_font, fill=COLOR_GOLD)

    # Main Disclaimer Text
    if not disclaimer_text:
        disclaimer_text = (
            "This video is created strictly for satirical, comedic, and entertainment purposes.\n\n"
            "Depictions, situations, and national tropes are exaggerated internet memes\n"
            "and do not represent real individuals, healthcare systems, or malicious intent.\n\n"
            "Please take everything with a grain of salt and enjoy the humor!"
        )

    body_font = get_font(34, bold=False)
    paragraphs = disclaimer_text.strip().split("\n\n")
    max_text_w = card_w - 160
    wrapped_lines: list[str] = []
    for p in paragraphs:
        if not p.strip():
            continue
        p_lines = wrap_text(body_font, " ".join(p.split()), max_text_w)
        wrapped_lines.extend(p_lines)
        wrapped_lines.append("")  # paragraph separator
    if wrapped_lines and wrapped_lines[-1] == "":
        wrapped_lines.pop()

    line_h = 52
    total_text_h = len(wrapped_lines) * line_h
    text_start_y = badge_y1 + 50 + max(0, (card_y1 - badge_y1 - 120 - total_text_h) // 2)

    ink_top, ink_h = _ink_metrics(body_font)
    for idx, line in enumerate(wrapped_lines):
        if not line:
            continue
        lw = body_font.getlength(line)
        lx = (w - lw) / 2
        ly = text_start_y + idx * line_h + (line_h - ink_h) / 2 - ink_top
        draw.text((lx, ly), line, font=body_font, fill=COLOR_WHITE)

    # Bottom Sub-notice pill / text
    sub_font = get_font(20, bold=True)
    sub_text = sub_notice.strip()
    sub_w = sub_font.getlength(sub_text)
    sub_x = (w - sub_w) / 2
    sub_y = card_y1 - 50
    draw.text((sub_x, sub_y), sub_text, font=sub_font, fill=(140, 148, 160))

    return canvas


# ============================================================================
# Scenes 1 to N: Country Meme Scenes
# ============================================================================


def render_wojak_scene_frame(
    country: str,
    caption: str,
    background_img: Image.Image,
    character_img: Image.Image | None = None,
    character_side: str = "left",
    size: tuple[int, int] = (FRAME_WIDTH, FRAME_HEIGHT),
    character_height: int = 700,
    progress: float = 0.0,
) -> Image.Image:
    """Render a 1920x1080 Country Meme scene matching the SapX style.

    Args:
        country: Country Name (e.g. 'USA', 'SWEDEN', 'INDIA').
        caption: Punchline text for the floating card.
        background_img: Image covering 1920x970 below the header bar.
        character_img: RGBA transparent cutout image of Wojak/meme character.
        character_side: 'left' or 'right' (floating card appears on opposite side).
        size: Target frame size (1920, 1080).
        character_height: Target height for character in pixels (~600-750px).
        progress: Cinematic Ken-Burns progress (0.0 to 1.0) for subtle camera breathing.
    """
    w, h = size
    canvas = Image.new("RGB", (w, h), COLOR_WHITE)

    # 1. Background (y: HEADER_HEIGHT to FRAME_HEIGHT = 970px height)
    bg_h = h - HEADER_HEIGHT
    bg = background_img.convert("RGB")

    # Ken-Burns subtle zoom drift (1.00 -> 1.03)
    zoom = 1.0 + 0.03 * max(0.0, min(1.0, progress))
    scale = max(w / bg.width, bg_h / bg.height) * zoom
    new_w = max(w, round(bg.width * scale))
    new_h = max(bg_h, round(bg.height * scale))
    bg = bg.resize((new_w, new_h), Image.Resampling.BILINEAR)
    left = (new_w - w) // 2
    top = (new_h - bg_h) // 2
    bg = bg.crop((left, top, left + w, top + bg_h))
    canvas.paste(bg, (0, HEADER_HEIGHT))

    # 2. Position and draw Character (scaled to ~600-750px height, anchored to bottom)
    char_x = 0
    char_y = 0
    if character_img is not None:
        char = character_img.convert("RGBA")
        target_h = max(200, min(character_height, bg_h))
        target_w = max(1, round(char.width * (target_h / char.height)))
        char = char.resize((target_w, target_h), Image.Resampling.LANCZOS)

        char_y = h - target_h
        if character_side == "left":
            char_x = 100
        else:
            char_x = w - target_w - 100
        canvas.paste(char, (char_x, char_y), char)

    # 3. Position and draw Floating White Punchline Card (on opposite side!)
    clean_caption = " ".join(caption.split())
    if clean_caption:
        card_max_w = 820
        card_max_h = 280
        pad_x = 44
        pad_y = 34

        font, lines, line_h = fit_punchline(
            clean_caption,
            max_w=card_max_w - 2 * pad_x,
            max_h=card_max_h - 2 * pad_y,
            max_lines=3,
            initial_size=42,
            min_size=30,
        )

        # Measure text box bounds
        max_line_w = max(font.getlength(line) for line in lines) if lines else 0
        text_total_h = len(lines) * line_h

        card_w = max(520, min(card_max_w, int(max_line_w + pad_x * 2)))
        card_h = text_total_h + pad_y * 2

        # Horizontal placement on opposite side of character
        if character_side == "left":
            # Card on right half: center around x = 1420
            card_center_x = 1400
            card_x0 = max(w // 2 + 60, min(w - card_w - 70, card_center_x - card_w // 2))
        else:
            # Card on left half: center around x = 500
            card_center_x = 520
            card_x0 = max(70, min(w // 2 - card_w - 60, card_center_x - card_w // 2))

        # Vertical placement: centered in the background area
        card_y0 = HEADER_HEIGHT + (bg_h - card_h) // 2

        # Render realistic soft Gaussian drop shadow
        shadow_img, s_pad_x, s_pad_y = create_soft_drop_shadow(
            card_w,
            card_h,
            radius=16,
            blur=22,
            offset_y=14,
            shadow_color=COLOR_SHADOW,
        )
        canvas.paste(shadow_img, (card_x0 - s_pad_x, card_y0 - s_pad_y), shadow_img)

        # Draw floating white rounded card
        card_canvas = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        cdraw = ImageDraw.Draw(card_canvas)
        cdraw.rounded_rectangle(
            (0, 0, card_w - 1, card_h - 1),
            radius=16,
            fill=(*COLOR_WHITE, 255),
            outline=(230, 234, 240, 255),
            width=1,
        )
        canvas.paste(card_canvas, (card_x0, card_y0), card_canvas)

        # Render punchline text lines centered inside card
        draw = ImageDraw.Draw(canvas)
        ink_top, ink_h = _ink_metrics(font)
        center_x = card_x0 + card_w / 2
        for idx, line in enumerate(lines):
            lw = font.getlength(line)
            lx = center_x - lw / 2
            row_y = card_y0 + pad_y + idx * line_h
            ly = row_y + (line_h - ink_h) / 2 - ink_top
            draw.text((lx, ly), line, font=font, fill=COLOR_BLACK)

    # 4. Top White Header Bar (y: 0 to 110, solid white, 1px bottom border)
    # Always drawn over background and characters to guarantee crisp clean separation!
    header = Image.new("RGB", (w, HEADER_HEIGHT), COLOR_WHITE)
    hdraw = ImageDraw.Draw(header)

    # Bottom 1px subtle divider rule
    hdraw.rectangle(
        (0, HEADER_HEIGHT - HEADER_RULE_PX, w - 1, HEADER_HEIGHT - 1), fill=COLOR_HEADER_BORDER
    )

    # Centered Country Name & Crisp National Flag: bold uppercase black sans-serif
    title_text = country.strip().upper()
    title_font, _ = fit_title(title_text, max_w=w - 320, max_h=HEADER_HEIGHT - 30, initial_size=58)
    title_w = title_font.getlength(title_text)
    ink_top, ink_h = _ink_metrics(title_font, title_text)

    flag_img = get_country_flag(country, target_height=52)
    spacing = 20
    if flag_img:
        total_w = flag_img.width + spacing + title_w
        start_x = (w - total_w) / 2
        flag_x = round(start_x)
        flag_y = round((HEADER_HEIGHT - flag_img.height) / 2)
        header.paste(flag_img, (flag_x, flag_y), flag_img)
        title_x = start_x + flag_img.width + spacing
    else:
        title_x = (w - title_w) / 2

    title_y = (HEADER_HEIGHT - ink_h) / 2 - ink_top
    hdraw.text((round(title_x), round(title_y)), title_text, font=title_font, fill=COLOR_BLACK)
    canvas.paste(header, (0, 0))

    return canvas
