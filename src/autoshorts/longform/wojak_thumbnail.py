"""High-CTR 16:9 (1920x1080) YouTube Thumbnail Generator in SapX Style.

Key features:
1. Saturated, dramatic background with depth blur and high-contrast radial vignette.
2. Prominently featured Shocked Wojak / Meme character (occupying ~50% of the screen)
   with bold white/yellow rim lighting for instant separation on mobile screens.
3. Giant comical prop: Angled hospital bill / invoice sticker with absurd line items
   and a stamped red 'BANKRUPT!' / 'DENIED!' badge.
4. Top punchy banner: e.g. 'EVERY COUNTRY'S HOSPITAL BILL' in bold uppercase pill.
5. High-CTR shock badges: '$2,500,000?!' in electric yellow with thick 8px black stroke
   and deep drop shadow.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from autoshorts.longform.wojak_backgrounds import get_wojak_background
from autoshorts.longform.wojak_compositor import (
    COLOR_BLACK,
    _ink_metrics,
    create_soft_drop_shadow,
    get_font,
)

logger = logging.getLogger(__name__)

THUMBNAIL_WIDTH = 1920
THUMBNAIL_HEIGHT = 1080

COLOR_ACCENT_YELLOW = (255, 220, 0)
COLOR_ACCENT_RED = (235, 35, 35)
COLOR_ACCENT_CYAN = (0, 225, 255)


def _draw_stroke_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: Any,
    fill: tuple[int, int, int] = COLOR_ACCENT_YELLOW,
    stroke_fill: tuple[int, int, int] = COLOR_BLACK,
    stroke_width: int = 8,
) -> None:
    """Draw text with a heavy stroke/outline for high-CTR readability."""
    draw.text(
        xy,
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )


def _create_radial_vignette(size: tuple[int, int]) -> Image.Image:
    """Create a dark cinematic radial vignette layer."""
    w, h = size
    # Create radial gradient via coordinate distance
    y, x = np.ogrid[:h, :w]
    cx, cy = w / 2.0, h / 2.0
    # Normalized distance from center (0 to 1)
    dist = np.sqrt(((x - cx) / (w * 0.55)) ** 2 + ((y - cy) / (h * 0.55)) ** 2)
    alpha = np.clip((dist - 0.5) * 180.0, 0, 160).astype(np.uint8)

    vignette_arr = np.zeros((h, w, 4), dtype=np.uint8)
    vignette_arr[..., 0:3] = 0  # Black
    vignette_arr[..., 3] = alpha
    return Image.fromarray(vignette_arr, "RGBA")


def _draw_giant_hospital_bill(
    width: int = 560,
    height: int = 700,
    total_amount: str = "$1,850,000",
    angle: float = -8.0,
) -> Image.Image:
    """Render a comical, gigantic hospital bill prop with red rubber stamp."""
    # Extra canvas space for rotation
    canvas_w = int(width * 1.5)
    canvas_h = int(height * 1.5)
    bill = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(bill)

    # Center coords
    bx0 = (canvas_w - width) // 2
    by0 = (canvas_h - height) // 2
    bx1 = bx0 + width
    by1 = by0 + height

    # Paper base with subtle drop shadow
    bdraw.rectangle([(bx0 + 8, by0 + 12), (bx1 + 8, by1 + 12)], fill=(0, 0, 0, 90))
    bdraw.rectangle(
        [(bx0, by0), (bx1, by1)], fill=(252, 252, 250), outline=(210, 215, 220), width=3
    )

    # Red Header Banner
    header_h = 75
    bdraw.rectangle([(bx0, by0), (bx1, by0 + header_h)], fill=(210, 30, 30))
    hfont = get_font(34, bold=True)
    htext = "HOSPITAL INVOICE"
    hw = hfont.getlength(htext)
    bdraw.text((bx0 + (width - hw) / 2, by0 + 20), htext, font=hfont, fill=(255, 255, 255))

    # Line Items
    tfont = get_font(26, bold=True)
    lines = [
        ("Ambulance (2 miles)", "$18,500"),
        ("1x Tylenol Pill", "$2,400"),
        ("ER Waiting Chair", "$8,200"),
        ("Doctor Eye Contact", "$14,000"),
        ("Breathing Hospital Air", "$32,000"),
        ("Band-Aid Application", "$4,900"),
    ]

    curr_y = by0 + header_h + 35
    for item, price in lines:
        bdraw.text((bx0 + 35, curr_y), item, font=tfont, fill=(35, 40, 50))
        pw = tfont.getlength(price)
        bdraw.text((bx1 - 35 - pw, curr_y), price, font=tfont, fill=(35, 40, 50))
        curr_y += 42
        bdraw.line([(bx0 + 30, curr_y - 6), (bx1 - 30, curr_y - 6)], fill=(230, 232, 235), width=1)

    # Big Total Box
    total_box_y0 = by1 - 150
    bdraw.rectangle(
        [(bx0 + 25, total_box_y0), (bx1 - 25, by1 - 35)],
        fill=(240, 243, 248),
        outline=(190, 200, 210),
        width=2,
    )
    tot_label_font = get_font(24, bold=True)
    bdraw.text(
        (bx0 + 45, total_box_y0 + 15), "TOTAL DUE:", font=tot_label_font, fill=(100, 110, 125)
    )

    tot_val_font = get_font(56, bold=True)
    tot_val_text = total_amount.strip()
    tot_val_w = tot_val_font.getlength(tot_val_text)
    bdraw.text(
        (bx1 - 45 - tot_val_w, total_box_y0 + 38),
        tot_val_text,
        font=tot_val_font,
        fill=COLOR_ACCENT_RED,
    )

    # Rubber Stamp "DENIED / BANKRUPT"
    stamp_w, stamp_h = 320, 90
    stamp = Image.new("RGBA", (stamp_w, stamp_h), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(stamp)
    sdraw.rounded_rectangle(
        [(0, 0), (stamp_w - 1, stamp_h - 1)], radius=12, outline=(220, 20, 20, 220), width=6
    )
    sfont = get_font(42, bold=True)
    stext = "BANKRUPT!"
    sw = sfont.getlength(stext)
    sdraw.text(((stamp_w - sw) / 2, 18), stext, font=sfont, fill=(220, 20, 20, 230))
    stamp = stamp.rotate(-18, resample=Image.Resampling.BILINEAR, expand=True)

    # Paste stamp across bill
    bill.paste(stamp, (bx0 + 70, by0 + 240), stamp)

    # Rotate entire bill slightly
    rotated = bill.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    return rotated


def _create_character_halo(
    char_rgba: Image.Image, halo_color: tuple[int, int, int] = (255, 255, 255), width: int = 12
) -> Image.Image:
    """Create a high-contrast white/yellow outline halo around character cutout."""
    alpha = char_rgba.split()[3]
    # Dilate alpha via MaxFilter
    dilated = alpha.filter(ImageFilter.MaxFilter(width * 2 + 1))
    halo = Image.new("RGBA", char_rgba.size, (*halo_color, 255))
    halo.putalpha(dilated)
    # Paste original char on top
    result = halo.copy()
    result.paste(char_rgba, (0, 0), char_rgba)
    return result


def generate_wojak_thumbnail(
    topic_title: str = "EVERY COUNTRY'S HOSPITAL BILL",
    left_badge: str = "USA: $2,500,000?!",
    right_badge: str = "VS FREE?!",
    character_img: Image.Image | None = None,
    background_img: Image.Image | None = None,
    output_path: Path | str | None = None,
    setting: str = "hospital",
) -> Image.Image:
    """Generate a high-CTR 1920x1080 YouTube thumbnail matching SapX viral styling.

    Args:
        topic_title: Main bold banner text (e.g. 'EVERY COUNTRY'S HOSPITAL BILL').
        left_badge: Primary shock text badge (e.g. '$2,500,000?!').
        right_badge: Secondary contrast badge (e.g. 'VS FREE?!').
        character_img: Shocked Wojak or meme character RGBA image.
        background_img: Optional custom background; procedural hospital generated if None.
        output_path: Path to save thumbnail JPG/PNG.
        setting: Environment preset ('hospital', 'school', 'transport').

    Returns:
        1920x1080 RGB PIL Image.
    """
    w, h = THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT

    # 1. Background
    if background_img is None:
        bg = get_wojak_background("USA", setting=setting, width=w, height=h)
    else:
        bg = background_img.convert("RGB")
        if bg.size != (w, h):
            scale = max(w / bg.width, h / bg.height)
            nw, nh = max(w, round(bg.width * scale)), max(h, round(bg.height * scale))
            bg = bg.resize((nw, nh), Image.Resampling.LANCZOS)
            bg = bg.crop(((nw - w) // 2, (nh - h) // 2, (nw - w) // 2 + w, (nh - h) // 2 + h))

    # Apply slight blur to background so foreground stands out sharply
    bg = bg.filter(ImageFilter.GaussianBlur(3.5))

    # Apply cinematic vignette
    vignette = _create_radial_vignette((w, h))
    bg.paste(vignette, (0, 0), vignette)

    canvas = bg

    # 2. Giant Comical Prop (Hospital Bill) on the Left
    bill_img = _draw_giant_hospital_bill(
        width=470, height=580, total_amount="$1,850,000", angle=-6.0
    )
    canvas.paste(bill_img, (60, 195), bill_img)

    # 3. Shocked Wojak / Meme Character on the Right
    if character_img is not None:
        char = character_img.convert("RGBA")
        target_h = 920  # Prominent, taking up almost full height
        target_w = max(1, round(char.width * (target_h / char.height)))
        char = char.resize((target_w, target_h), Image.Resampling.LANCZOS)
        char_with_halo = _create_character_halo(char, halo_color=(255, 255, 255), width=10)
        char_x = w - target_w - 60
        char_y = h - target_h
        canvas.paste(char_with_halo, (char_x, char_y), char_with_halo)

    draw = ImageDraw.Draw(canvas)

    # 4. Top Header Banner Bar (Clean & Authoritative)
    banner_w = w - 160
    banner_h = 100
    banner_x0 = 80
    banner_y0 = 40
    banner_x1 = banner_x0 + banner_w
    banner_y1 = banner_y0 + banner_h

    # Banner drop shadow
    b_shadow, px, py = create_soft_drop_shadow(
        banner_w, banner_h, radius=20, blur=24, offset_y=12, shadow_color=(0, 0, 0, 160)
    )
    canvas.paste(b_shadow, (banner_x0 - px, banner_y0 - py), b_shadow)

    # Banner rounded container (Pure white with bold red accent bar)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        [(banner_x0, banner_y0), (banner_x1, banner_y1)], radius=20, fill=(255, 255, 255)
    )
    draw.rounded_rectangle(
        [(banner_x0, banner_y0), (banner_x0 + 24, banner_y1)], radius=4, fill=(220, 30, 30)
    )

    # Title text inside banner
    t_font = get_font(52, bold=True)
    t_text = topic_title.strip().upper()
    tw = t_font.getlength(t_text)
    ink_top, ink_h = _ink_metrics(t_font, t_text)
    tx = (w - tw) / 2
    ty = banner_y0 + (banner_h - ink_h) / 2 - ink_top
    draw.text((tx, ty), t_text, font=t_font, fill=COLOR_BLACK)

    # 5. Massive High-CTR Shock Badges with 8px Stroke
    # Left Badge (e.g. USA: $2,500,000?!) positioned cleanly below prop
    badge_font = get_font(58, bold=True)
    _draw_stroke_text(
        draw,
        (80, h - 110),
        left_badge.upper(),
        font=badge_font,
        fill=COLOR_ACCENT_YELLOW,
        stroke_fill=COLOR_BLACK,
        stroke_width=8,
    )

    # Right / Center Badge (e.g. VS FREE?!)
    vs_font = get_font(62, bold=True)
    _draw_stroke_text(
        draw,
        (w // 2 - 40, h - 150),
        right_badge.upper(),
        font=vs_font,
        fill=COLOR_ACCENT_CYAN,
        stroke_fill=COLOR_BLACK,
        stroke_width=8,
    )

    # 6. Save if output_path requested
    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(str(out), quality=95)
        logger.info("Generated high-CTR Wojak thumbnail: %s", out)

    return canvas
