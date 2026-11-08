"""High-CTR Thumbnail generator for Country Memes and Types Of... videos.

Generates eye-catching, high-contrast thumbnails featuring:
- Expressive anime reaction characters with sticker cutout outlines
- Bold text cascades with heavy drop shadows and glowing yellow/cyan accents
- Country flag / Type category badge pills
- Comic speedlines, starburst rays, and safe-zone border padding
- Support for both 9:16 (Shorts/TikTok) and 16:9 (Landscape)
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from autoshorts.meme.types import MemeScript, MemeSegment
from autoshorts.meme.visuals import (
    COLOR_CORAL,
    COLOR_CYAN,
    COLOR_PURPLE,
    COLOR_WHITE,
    COLOR_YELLOW,
    draw_anime_character,
    draw_manga_speed_lines,
    draw_text_with_outline,
    get_font,
    get_resolution,
)


def _draw_starburst_background(
    width: int,
    height: int,
    num_rays: int = 32,
    color1: tuple[int, int, int] = (15, 23, 42),
    color2: tuple[int, int, int] = (30, 41, 59),
) -> Image.Image:
    """Generate high-energy radial starburst background."""
    img = Image.new("RGBA", (width, height), color1)
    draw = ImageDraw.Draw(img)
    cx, cy = width // 2, int(height * 0.45)
    max_r = math.hypot(width, height)

    for i in range(num_rays):
        if i % 2 == 0:
            ang1 = (i / num_rays) * 2 * math.pi
            ang2 = ((i + 1) / num_rays) * 2 * math.pi
            p1 = (cx + math.cos(ang1) * max_r, cy + math.sin(ang1) * max_r)
            p2 = (cx + math.cos(ang2) * max_r, cy + math.sin(ang2) * max_r)
            draw.polygon([(cx, cy), p1, p2], fill=color2)

    return img


def _add_sticker_outline(
    char_img: Image.Image,
    outline_color: tuple[int, int, int, int] = (255, 255, 255, 255),
    outline_width: int = 14,
) -> Image.Image:
    """Add a thick, vibrant white sticker cutout border and dark drop shadow around character."""
    # Extract alpha mask
    alpha = char_img.split()[3]

    # Dilate alpha mask to create the sticker border
    dilated = alpha.filter(ImageFilter.MaxFilter(outline_width * 2 + 1))

    # Create solid outline image
    outline_layer = Image.new("RGBA", char_img.size, outline_color)
    outline_layer.putalpha(dilated)

    # Soft dark drop shadow under sticker
    shadow_mask = dilated.filter(ImageFilter.GaussianBlur(12))
    shadow_layer = Image.new("RGBA", char_img.size, (0, 0, 0, 180))
    shadow_layer.putalpha(shadow_mask)

    # Composite shadow -> outline -> character
    res = Image.new("RGBA", char_img.size, (0, 0, 0, 0))
    res = Image.alpha_composite(res, shadow_layer)
    res = Image.alpha_composite(res, outline_layer)
    res = Image.alpha_composite(res, char_img)
    return res


def _parse_title_cascade(title: str, topic: str, mode: str) -> tuple[str, str, str]:
    """Break title or topic into a punchy 2-3 line thumbnail text cascade."""
    # Never include "anime edition" anywhere in thumbnail text
    raw = title or topic or ""
    clean = re.sub(r"\(?\s*anime\s+edition\s*\)?", "", raw, flags=re.IGNORECASE)
    clean = re.sub(r"[^\w\s\':-]", "", clean).strip()

    prefix = "EVERY COUNTRY'S" if mode == "country_meme" else "TYPES OF"
    main_subject = ""
    punchline = "BE LIKE!"

    upper = clean.upper().replace("ANIME EDITION", "").strip()
    if "BE LIKE" in upper:
        parts = upper.split("BE LIKE")
        main_subject = parts[0].strip()
        punchline = "BE LIKE!"
    else:
        main_subject = upper

    # Remove standard prefix if present in main_subject
    for p in ("EVERY COUNTRY'S", "EVERY COUNTRY", "TYPES OF"):
        if main_subject.startswith(p):
            prefix = "EVERY COUNTRY'S" if "COUNTRY" in p else "TYPES OF"
            main_subject = main_subject.replace(p, "").strip()

    if not main_subject:
        main_subject = "PUBLIC TRANSPORT" if mode == "country_meme" else "PEOPLE"

    return prefix, main_subject, punchline


def make_meme_thumbnail(
    script: MemeScript,
    out_path: Path,
    aspect_ratio: str = "9:16",
    visual_style: str = "anime_color",
) -> Path:
    """Generate a high-CTR, viral thumbnail for the meme video.

    Args:
        script: The MemeScript containing topic, title, and segments.
        out_path: Destination path for saving the thumbnail image.
        aspect_ratio: '9:16' (1080x1920) or '16:9' (1920x1080).
        visual_style: 'anime_color' or 'manga_bw'.

    Returns:
        Path to the generated thumbnail file.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    width, height = get_resolution(aspect_ratio)
    is_vertical = height > width
    is_bw = visual_style == "manga_bw"

    # 1. Base Background: High energy starburst or themed environment
    if is_bw:
        base_bg = _draw_starburst_background(width, height, 28, (25, 25, 28), (50, 50, 55))
    else:
        # Vibrant saturated background
        base_bg = _draw_starburst_background(width, height, 36, (15, 23, 42), (30, 58, 138))

    # Add speedlines layer
    speedlines = draw_manga_speed_lines(
        width,
        height,
        center=(width // 2, int(height * 0.45)),
        num_lines=64,
        color=(255, 255, 255, 60) if not is_bw else (255, 255, 255, 90),
    )
    base_bg = Image.alpha_composite(base_bg, speedlines)

    # 2. Select the most exaggerated reaction character
    # Look for shocked or screaming segment, or fallback to first segment
    best_seg: MemeSegment | None = None
    for seg in script.segments:
        if seg.expression in ("shocked", "screaming", "crying_laughing"):
            best_seg = seg
            break
    if not best_seg and script.segments:
        best_seg = script.segments[0]

    pose = best_seg.pose if best_seg else "dramatic_heroic"
    expression = best_seg.expression if best_seg else "shocked"

    # Scale character up for thumbnail prominence
    char_canvas_w = width
    char_canvas_h = height

    # Draw reaction character
    raw_char = draw_anime_character(
        char_canvas_w,
        char_canvas_h,
        pose=pose,
        expression=expression,
        visual_style=visual_style,
        time_sec=1.0,
        blink=False,
        breathing_offset=0.0,
    )

    # 3. Apply thick sticker cutout border around reaction character
    sticker_char = _add_sticker_outline(
        raw_char,
        outline_color=(255, 255, 255, 255) if not is_bw else (240, 240, 240, 255),
        outline_width=16 if is_vertical else 12,
    )

    # Composite character onto background
    thumb = Image.alpha_composite(base_bg, sticker_char)
    draw = ImageDraw.Draw(thumb)

    # 4. Prominent Category / Country Pill Badge at top
    badge_w = int(width * 0.75)
    badge_h = int(height * 0.055)
    bx1 = (width - badge_w) // 2
    by1 = int(height * 0.04)
    bx2 = bx1 + badge_w
    by2 = by1 + badge_h

    badge_fill = COLOR_CORAL if script.mode == "country_meme" else COLOR_PURPLE
    if is_bw:
        badge_fill = (20, 20, 24)

    draw.rounded_rectangle([bx1 + 4, by1 + 4, bx2 + 4, by2 + 4], radius=18, fill=(0, 0, 0, 180))
    draw.rounded_rectangle(
        [bx1, by1, bx2, by2], radius=18, fill=badge_fill, outline=COLOR_WHITE, width=4
    )

    badge_label = f"🔥 {script.category.upper()} MEMES" if script.category else "🔥 VIRAL MEMES"
    if script.mode == "country_meme" and best_seg:
        badge_label = f"🌍 {best_seg.name.upper()} VS THE WORLD"

    f_badge = get_font("impact", max(24, int(badge_h * 0.52)))
    draw_text_with_outline(
        draw,
        (width // 2, (by1 + by2) // 2),
        badge_label,
        font=f_badge,
        fill=COLOR_WHITE,
        outline_color=(0, 0, 0),
        outline_width=3,
        anchor="mm",
    )

    # 5. Punchy Bold Text Cascade
    prefix, main_subject, punchline = _parse_title_cascade(script.title, script.topic, script.mode)

    # Positioning:
    # In vertical 9:16: Text at upper third (y ~ 14% to 32%), above character face
    # In horizontal 16:9: Text at left or top
    if is_vertical:
        y_text_start = int(height * 0.13)
        # Line 1: Prefix (e.g. "EVERY COUNTRY'S")
        f_prefix = get_font("impact", 54)
        draw_text_with_outline(
            draw,
            (width // 2, y_text_start),
            prefix,
            font=f_prefix,
            fill=COLOR_WHITE,
            outline_color=(0, 0, 0),
            outline_width=6,
            anchor="mm",
        )

        # Line 2: Massive Main Subject (e.g. "PUBLIC TRANSPORT" or "GAMERS")
        # Scale font to fit width
        main_font_size = 76 if len(main_subject) < 16 else 60
        f_main = get_font("impact", main_font_size)
        y_main = y_text_start + 75
        draw_text_with_outline(
            draw,
            (width // 2, y_main),
            main_subject,
            font=f_main,
            fill=COLOR_YELLOW if not is_bw else COLOR_WHITE,
            outline_color=(0, 0, 0),
            outline_width=8,
            anchor="mm",
        )

        # Line 3: Punchline Badge Pill ("BE LIKE!" or "RANKED!")
        pill_text = punchline or "BE LIKE!"
        f_pill = get_font("impact", 48)
        y_pill = y_main + 75

        p_bbox = draw.textbbox((0, 0), pill_text, font=f_pill)
        pw = (p_bbox[2] - p_bbox[0]) + 60
        ph = 55
        px1 = (width - pw) // 2
        py1 = y_pill - ph // 2
        draw.rounded_rectangle(
            [px1 + 4, py1 + 4, px1 + pw + 4, py1 + ph + 4], radius=14, fill=(0, 0, 0, 200)
        )
        draw.rounded_rectangle(
            [px1, py1, px1 + pw, py1 + ph],
            radius=14,
            fill=COLOR_CORAL if not is_bw else (40, 40, 45),
            outline=COLOR_WHITE,
            width=4,
        )
        draw_text_with_outline(
            draw,
            (width // 2, y_pill),
            pill_text,
            font=f_pill,
            fill=COLOR_WHITE,
            outline_color=(0, 0, 0),
            outline_width=4,
            anchor="mm",
        )

        # Bottom Callout Sticker (e.g., "WAIT TILL THE END 💀")
        bot_y = int(height * 0.88)
        f_bot = get_font("impact", 44)
        draw.rounded_rectangle(
            [int(width * 0.08), bot_y - 30, int(width * 0.92), bot_y + 35],
            radius=18,
            fill=(0, 0, 0, 220),
            outline=COLOR_YELLOW if not is_bw else COLOR_WHITE,
            width=4,
        )
        draw_text_with_outline(
            draw,
            (width // 2, bot_y + 2),
            "WAIT FOR THE END! 💀",
            font=f_bot,
            fill=COLOR_YELLOW if not is_bw else COLOR_WHITE,
            outline_color=(0, 0, 0),
            outline_width=4,
            anchor="mm",
        )

    else:
        # Horizontal 16:9 layout
        f_prefix = get_font("impact", 56)
        draw_text_with_outline(
            draw,
            (int(width * 0.32), int(height * 0.22)),
            prefix,
            font=f_prefix,
            fill=COLOR_WHITE,
            outline_color=(0, 0, 0),
            outline_width=6,
            anchor="mm",
        )

        f_main = get_font("impact", 80)
        draw_text_with_outline(
            draw,
            (int(width * 0.32), int(height * 0.38)),
            main_subject,
            font=f_main,
            fill=COLOR_YELLOW if not is_bw else COLOR_WHITE,
            outline_color=(0, 0, 0),
            outline_width=8,
            anchor="mm",
        )

        f_pill = get_font("impact", 54)
        draw_text_with_outline(
            draw,
            (int(width * 0.32), int(height * 0.54)),
            punchline,
            font=f_pill,
            fill=COLOR_CYAN if not is_bw else COLOR_WHITE,
            outline_color=(0, 0, 0),
            outline_width=6,
            anchor="mm",
        )

    # 6. Vibrant border vignette around outer perimeter
    draw.rectangle(
        [0, 0, width, height], outline=COLOR_YELLOW if not is_bw else (255, 255, 255), width=8
    )

    # Convert to RGB and save
    thumb_rgb = thumb.convert("RGB")
    fmt = "JPEG" if str(out_path).lower().endswith((".jpg", ".jpeg")) else "PNG"
    if fmt == "JPEG":
        thumb_rgb.save(out_path, format="JPEG", quality=95)
    else:
        thumb_rgb.save(out_path, format="PNG")
    return out_path
