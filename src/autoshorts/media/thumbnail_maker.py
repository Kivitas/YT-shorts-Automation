"""High-CTR Thumbnail Maker for 9:16 Shorts and Vertical Video.

Creates high-retention, YouTube Shorts & TikTok compliant thumbnails with:
- Intelligent keyframe extraction from video source
- Bold multi-line typography with heavy stroke and drop shadows
- Safe-zone aware text placement (avoiding YouTube Shorts UI buttons & overlays)
- Dynamic neon keyword highlighting (using ASS emphasis keywords)
- Category/Hook badges (pill badges with rounded borders)
- Atmospheric vignette and dark gradient underlays
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from autoshorts.media.ass_styler import EMPHASIS_KEYWORDS
from autoshorts.media.compositor import extract_thumbnail, find_best_thumbnail_timestamp

logger = logging.getLogger(__name__)

THUMB_W = 1080
THUMB_H = 1920

# Colors (RGB)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
NEON_YELLOW = (255, 230, 0)
NEON_CYAN = (0, 240, 255)
NEON_GREEN = (0, 255, 102)
NEON_ORANGE = (255, 153, 0)
DARK_BG_TOP = (16, 20, 36)
DARK_BG_BOTTOM = (28, 18, 44)


@dataclass
class ThumbnailConfig:
    """Styling configuration for high-CTR vertical thumbnail generation."""

    width: int = THUMB_W
    height: int = THUMB_H
    style: str = "neon"  # "neon", "hormozi", "mrbeast", "minimal", "cinematic"
    badge_text: str | None = None
    primary_color: tuple[int, int, int] = WHITE
    accent_color: tuple[int, int, int] = NEON_YELLOW
    emphasis_color: tuple[int, int, int] = NEON_CYAN
    font_size: int = 100
    badge_font_size: int = 38
    stroke_width: int = 10
    safe_zone_margin_top: float = 0.16
    safe_zone_margin_bottom: float = 0.22


def _load_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    """Attempt to load a punchy system font (Montserrat, Impact, Arial), falling back to default."""
    candidates = [
        "Montserrat-Black.ttf",
        "Montserrat-ExtraBold.ttf",
        "Impact.ttf",
        "impact.ttf",
        "arialbd.ttf",
        "Arial Bold.ttf",
        "arial.ttf",
    ]
    for c in candidates:
        try:
            return ImageFont.truetype(c, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap_title(title: str, max_words_per_line: int = 3, max_lines: int = 4) -> list[str]:
    """Split title into 2-3 word high-impact lines."""
    clean = re.sub(r"[^\w\s\?!-]", "", title).strip().upper()
    words = clean.split()
    if not words:
        return ["MUST", "WATCH"]
    lines: list[str] = []
    for i in range(0, len(words), max_words_per_line):
        lines.append(" ".join(words[i : i + max_words_per_line]))
        if len(lines) >= max_lines:
            break
    return lines


def _draw_rounded_pill(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int] | tuple[int, int, int, int],
    outline: tuple[int, int, int] | None = None,
    width: int = 2,
) -> None:
    """Draw a smooth rounded rectangle / pill badge."""
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


class ThumbnailMaker:
    """Generates high-CTR 9:16 Shorts thumbnails."""

    def __init__(self, config: ThumbnailConfig | None = None) -> None:
        self.config = config or ThumbnailConfig()

    def create_thumbnail(
        self,
        title: str,
        output_path: Path | str,
        video_path: Path | str | None = None,
        base_image_path: Path | str | None = None,
        badge_text: str | None = None,
        keyframe_time: float | None = None,
    ) -> Path:
        """Create and write thumbnail image to output_path."""
        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        w = self.config.width
        h = self.config.height

        # 1. Base Image: extract from video, load image, or generate gradient
        base_img: Image.Image | None = None
        if video_path and Path(video_path).is_file():
            try:
                ts = keyframe_time or find_best_thumbnail_timestamp(Path(video_path))
                thumb_tmp = dest.with_suffix(".keyframe.jpg")
                extract_thumbnail(Path(video_path), thumb_tmp, target_ts=ts)
                if thumb_tmp.is_file() and thumb_tmp.stat().st_size > 0:
                    base_img = Image.open(thumb_tmp).convert("RGB")
                    thumb_tmp.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Could not extract video keyframe for thumbnail: %s", exc)

        if base_img is None and base_image_path and Path(base_image_path).is_file():
            try:
                base_img = Image.open(base_image_path).convert("RGB")
            except Exception as exc:
                logger.warning("Could not open base image: %s", exc)

        if base_img is None:
            # Generate vibrant dark backdrop
            base_img = Image.new("RGB", (w, h), color=DARK_BG_TOP)
            draw_bg = ImageDraw.Draw(base_img)
            # Subtle gradient
            for y in range(h):
                t = y / float(h)
                r = int(DARK_BG_TOP[0] * (1 - t) + DARK_BG_BOTTOM[0] * t)
                g = int(DARK_BG_TOP[1] * (1 - t) + DARK_BG_BOTTOM[1] * t)
                b = int(DARK_BG_TOP[2] * (1 - t) + DARK_BG_BOTTOM[2] * t)
                draw_bg.line([(0, y), (w, y)], fill=(r, g, b))
        else:
            # Resize and crop to 9:16 vertical cover
            src_w, src_h = base_img.size
            scale = max(w / src_w, h / src_h)
            new_w = int(src_w * scale)
            new_h = int(src_h * scale)
            resized = base_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            x0 = (new_w - w) // 2
            y0 = (new_h - h) // 2
            base_img = resized.crop((x0, y0, x0 + w, y0 + h))

        # 2. Dark contrast overlay / vignette for punchy typography readability
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw_ov = ImageDraw.Draw(overlay)
        # Dark vignette on top and middle where text sits
        center_y = int(h * 0.42)
        draw_ov.rectangle([(0, center_y - 300), (w, center_y + 400)], fill=(0, 0, 0, 140))
        # Top gradient for badge
        draw_ov.rectangle([(0, 0), (w, int(h * 0.22))], fill=(0, 0, 0, 100))

        base_img = Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(base_img)

        # 3. Top pill badge (e.g. "MUST WATCH", "EXPOSED")
        badge = badge_text or self.config.badge_text
        if badge:
            badge_font = _load_font(self.config.badge_font_size, bold=True)
            badge_clean = badge.strip().upper()
            bbox = badge_font.getbbox(badge_clean)
            bw = bbox[2] - bbox[0]
            bh = bbox[3] - bbox[1]
            pad_x = 24
            pad_y = 12
            pill_w = bw + pad_x * 2
            pill_h = bh + pad_y * 2
            pill_x0 = (w - pill_w) // 2
            pill_y0 = int(h * self.config.safe_zone_margin_top)
            # Draw pill with accent border
            _draw_rounded_pill(
                draw,
                (pill_x0, pill_y0, pill_x0 + pill_w, pill_y0 + pill_h),
                radius=18,
                fill=(0, 0, 0),
                outline=self.config.accent_color,
                width=3,
            )
            draw.text(
                (pill_x0 + pad_x, pill_y0 + pad_y - 2),
                badge_clean,
                font=badge_font,
                fill=self.config.accent_color,
            )

        # 4. Bold Hook Title
        lines = _wrap_title(title)
        title_font = _load_font(self.config.font_size, bold=True)
        line_height = int(self.config.font_size * 1.15)
        total_text_h = len(lines) * line_height
        start_y = int(h * 0.38) - (total_text_h // 2)

        stroke_w = self.config.stroke_width
        for i, line in enumerate(lines):
            line_y = start_y + i * line_height
            words = line.split()

            # Measure full line width
            line_bbox = title_font.getbbox(line)
            lw = line_bbox[2] - line_bbox[0]
            cur_x = (w - lw) // 2

            for word in words:
                w_bbox = title_font.getbbox(word)
                ww = w_bbox[2] - w_bbox[0]

                # Determine color: check if word is in EMPHASIS_KEYWORDS
                clean_word = word.strip(".,!?:;\"'")
                if clean_word in EMPHASIS_KEYWORDS:
                    word_color = self.config.emphasis_color
                elif i == len(lines) - 1:
                    word_color = self.config.accent_color
                else:
                    word_color = self.config.primary_color

                # Drop shadow
                draw.text(
                    (cur_x + 6, line_y + 8),
                    word,
                    font=title_font,
                    fill=BLACK,
                )
                # Heavy stroke + fill
                draw.text(
                    (cur_x, line_y),
                    word,
                    font=title_font,
                    fill=word_color,
                    stroke_width=stroke_w,
                    stroke_fill=BLACK,
                )

                space_bbox = title_font.getbbox(" ")
                space_w = space_bbox[2] - space_bbox[0]
                cur_x += ww + space_w

        # Save atomically
        tmp_out = dest.with_suffix(".tmp.png")
        base_img.save(str(tmp_out), format="PNG", optimize=True)
        tmp_out.replace(dest)
        return dest


def make_short_thumbnail(
    title: str,
    output_path: Path | str,
    video_path: Path | str | None = None,
    badge: str | None = None,
    style: str = "neon",
) -> Path:
    """Convenience function to generate a vertical Short thumbnail."""
    config = ThumbnailConfig(style=style, badge_text=badge)
    maker = ThumbnailMaker(config)
    return maker.create_thumbnail(title=title, output_path=output_path, video_path=video_path)


__all__ = ["ThumbnailConfig", "ThumbnailMaker", "make_short_thumbnail"]
