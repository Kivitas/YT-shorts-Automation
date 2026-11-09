"""YouTube Shorts (9:16 Vertical) Video Generation Engine for Wojak Meme Country Comparisons.

Matches YouTube Shorts vertical mobile format and the viral SapX geopolitical meme style:
- Resolution: 1080x1920 (9:16 Vertical Portrait).
- Duration: 30 to 60 seconds (typically 6 to 10 country segments, 4.0 to 4.5s each).
- Mobile Safe Zones:
  - Avoids right margin (x > 920px) where YouTube Shorts like/share/comment icons sit.
  - Avoids bottom 20% (y > 1530px) where video title, channel, and audio ticker sit.
  - Top White Banner Strip (height 150px) with centered bold country name & flag.
  - Realistic photographic background fitted to vertical frame.
  - Expressive procedural Wojak character scaled and framed for mobile impact (~750-880px tall).
  - Floating white joke card (rounded rectangle with soft drop shadow, bold black font 40-52px)
    positioned in the upper-mid safe zone.
- Pure-PIL rendering of 9:16 frames with raw RGB24 streaming directly to FFmpeg `pipe:0`.
- Continuous bouncy comedic meme BGM track + comedic SFX cues (cash register, flatline, record scratch).
- Opening 2.5s quick parody disclaimer / hook banner ("⚠️ COMEDY PARODY: Exaggerated country stereotypes!").
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
import time
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from autoshorts.core.paths import get_output_dir, get_work_dir
from autoshorts.longform.country_photos import pick_photo
from autoshorts.longform.flags import get_country_flag
from autoshorts.longform.types import LongformResult
from autoshorts.longform.wojak_compositor import wrap_text
from autoshorts.longform.wojak_generator import draw_wojak, spec_from_country_and_context
from autoshorts.media.audio_master import normalize_two_pass
from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)

# ============================================================================
# Vertical 9:16 Geometry & Safe Zone Specifications
# ============================================================================

SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
FPS = 30

HEADER_HEIGHT = 150
HEADER_RULE_PX = 2

DISCLAIMER_DURATION = 2.5
DEFAULT_SEGMENT_DURATION = 4.25

# Safe Zone Boundaries
# YouTube Shorts UI covers:
# - Right margin (x: 920 to 1080) for Like, Dislike, Comments, Share, Remix icons.
# - Bottom ~20% (y: 1536 to 1920) for Channel, Subscribe button, Title, Sound ticker.
SAFE_LEFT = 100
SAFE_RIGHT = 920
SAFE_WIDTH = SAFE_RIGHT - SAFE_LEFT  # 820px
SAFE_TOP = 180
SAFE_BOTTOM_OVERLAY = 1530

CHARACTER_HEIGHT = 1050
CARD_Y0 = 260
CARD_MAX_HEIGHT = 280

# Colors
COLOR_WHITE = (255, 255, 255)
COLOR_BLACK = (16, 16, 16)
COLOR_HEADER_BORDER = (210, 215, 222)
COLOR_SHADOW = (0, 0, 0, 85)
COLOR_CARD_BORDER = (228, 232, 238)

COLOR_DISCLAIMER_BG = (16, 18, 24)
COLOR_DISCLAIMER_CARD = (25, 30, 40)
COLOR_DISCLAIMER_BORDER = (55, 65, 80)
COLOR_GOLD = (245, 180, 28)
COLOR_GOLD_BG = (45, 36, 18)
COLOR_NEON_GLOW = (255, 225, 0)  # Dopamine neon yellow glow border
COLOR_TEXT_STROKE = (12, 14, 18, 255)

# Rapid Kinetic Subtitles
WORDS_PER_BURST = 2
KINETIC_FONT_SIZE = 84
KINETIC_CENTER_Y = 530

SAMPLE_RATE = 48000
_MAX_INT16 = 32767

# Country to Flag Emoji Mapping
COUNTRY_FLAGS: dict[str, str] = {
    "USA": "\U0001f1fa\U0001f1f8",
    "UNITED STATES": "\U0001f1fa\U0001f1f8",
    "AMERICA": "\U0001f1fa\U0001f1f8",
    "UK": "\U0001f1ec\U0001f1e7",
    "UNITED KINGDOM": "\U0001f1ec\U0001f1e7",
    "BRITAIN": "\U0001f1ec\U0001f1e7",
    "ENGLAND": "\U0001f1ec\U0001f1e7",
    "GERMANY": "\U0001f1e9\U0001f1ea",
    "CANADA": "\U0001f1e8\U0001f1e6",
    "JAPAN": "\U0001f1ef\U0001f1f5",
    "FRANCE": "\U0001f1eb\U0001f1f7",
    "AUSTRALIA": "\U0001f1e6\U0001f1fa",
    "INDIA": "\U0001f1ee\U0001f1f3",
    "ITALY": "\U0001f1ee\U0001f1f9",
    "BRAZIL": "\U0001f1e7\U0001f1f7",
    "SWITZERLAND": "\U0001f1e8\U0001f1ed",
    "SWEDEN": "\U0001f1f8\U0001f1ea",
    "POLAND": "\U0001f1f5\U0001f1f1",
    "NORWAY": "\U0001f1f3\U0001f1f4",
    "SPAIN": "\U0001f1ea\U0001f1f8",
    "NETHERLANDS": "\U0001f1f3\U0001f1f1",
    "NEW ZEALAND": "\U0001f1f3\U0001f1ff",
    "MEXICO": "\U0001f1f2\U0001f1fd",
    "SOUTH KOREA": "\U0001f1f0\U0001f1f7",
    "KOREA": "\U0001f1f0\U0001f1f7",
    "CHINA": "\U0001f1e8\U0001f1f3",
    "FINLAND": "\U0001f1eb\U0001f1ee",
    "GREECE": "\U0001f1ec\U0001f1f7",
    "ARGENTINA": "\U0001f1e6\U0001f1f7",
    "IRELAND": "\U0001f1ee\U0001f1ea",
    "AUSTRIA": "\U0001f1e6\U0001f1f9",
    "BELGIUM": "\U0001f1e7\U0001f1ea",
    "PORTUGAL": "\U0001f1f5\U0001f1f9",
    "DENMARK": "\U0001f1e9\U0001f1f0",
}

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

EMOJI_FONT_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf",
    "/System/Library/Fonts/Apple Color Emoji.ttc",
)


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass
class WojakShortsSegment:
    """One country comparison segment in a 9:16 vertical Short."""

    country: str
    caption: str
    flag: str = ""
    expression: str = "neutral"
    duration: float = DEFAULT_SEGMENT_DURATION
    sfx_cue: str = "none"
    photo_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.flag:
            cleaned = self.country.strip().upper()
            self.flag = COUNTRY_FLAGS.get(cleaned, "")


@dataclass
class WojakShortsConfig:
    """Configuration for a Wojak meme YouTube Shorts generation run."""

    topic: str
    segments: list[WojakShortsSegment]
    disclaimer_duration: float = DISCLAIMER_DURATION
    width: int = SHORTS_WIDTH
    height: int = SHORTS_HEIGHT
    fps: int = FPS
    bgm_mood: str = "playful"
    enable_voiceover: bool = False
    voice: str = "en-US-ChristopherNeural"
    subtitle_mode: str = "kinetic"
    words_per_burst: int = WORDS_PER_BURST
    font_size: int = KINETIC_FONT_SIZE
    render_method: str = "ffmpeg"


# ============================================================================
# Typography Helpers
# ============================================================================


def _resolve_font(bold: bool = True) -> str | None:
    for c in FONT_CANDIDATES:
        if c and os.path.isfile(c):
            return c
    return None


def _resolve_emoji_font() -> str | None:
    for c in EMOJI_FONT_CANDIDATES:
        if c and os.path.isfile(c):
            return c
    return None


@lru_cache(maxsize=128)
def get_font(size: int, bold: bool = True) -> Any:
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


@lru_cache(maxsize=64)
def get_emoji_font(size: int) -> Any:
    path = _resolve_emoji_font()
    if path is not None:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return get_font(size, bold=True)


def _ink_metrics(font: Any, sample_text: str = "H") -> tuple[int, int]:
    bbox = font.getbbox(sample_text)
    if bbox:
        return int(bbox[1]), int(bbox[3] - bbox[1])
    return 0, 28


def fit_punchline(
    text: str,
    max_w: int = 740,
    max_h: int = 240,
    max_lines: int = 3,
    initial_size: int = 50,
    min_size: int = 38,
) -> tuple[Any, list[str], int]:
    """Fit punchline text into mobile safe zone with bold 40-52px font."""
    clean_text = " ".join(text.split())
    size = initial_size
    while size >= min_size:
        font = get_font(size, bold=True)
        lines = wrap_text(font, clean_text, max_w)
        line_height = math.ceil(size * 1.30)
        total_h = len(lines) * line_height
        if len(lines) <= max_lines and total_h <= max_h:
            return font, lines, line_height
        size -= 2

    font = get_font(min_size, bold=True)
    lines = wrap_text(font, clean_text, max_w)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1 :])]
        last = lines[-1]
        while last and font.getlength(last + "...") > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    line_height = math.ceil(min_size * 1.30)
    return font, lines, line_height


# ============================================================================
# Visual & Shadow Compositors
# ============================================================================


def split_into_bursts(text: str, words_per_burst: int = WORDS_PER_BURST) -> list[list[str]]:
    """Split caption text into punchy uppercase word bursts (typically 2 words per burst)."""
    cleaned = " ".join(text.split()).upper()
    words = cleaned.split()
    if not words:
        return []
    bursts: list[list[str]] = []
    for i in range(0, len(words), max(1, words_per_burst)):
        bursts.append(words[i : i + words_per_burst])
    return bursts


@lru_cache(maxsize=128)
def get_kinetic_burst_layer(
    burst_tuple: tuple[str, ...],
    font_size: int = KINETIC_FONT_SIZE,
    glow_color_rgb: tuple[int, int, int] = COLOR_NEON_GLOW,
    max_safe_width: int = SAFE_WIDTH - 40,
) -> tuple[Image.Image, int, int]:
    """Pre-render a 2-word kinetic burst with neon glowing border and dark outline."""
    words = list(burst_tuple)
    font = get_font(font_size, bold=True)

    # Check single-line vs stacked 2-line layout
    single_line = " ".join(words)
    if font.getlength(single_line) <= max_safe_width:
        lines = [single_line]
    else:
        # If single line is too wide, stack words on 2 lines
        lines = words

    # Check if stacked words fit; if any word still exceeds max_safe_width, scale font size
    max_w = max(int(font.getlength(word_line)) for word_line in lines)
    effective_font = font
    current_size = font_size
    while max_w > max_safe_width and current_size > 54:
        current_size -= 4
        effective_font = get_font(current_size, bold=True)
        max_w = max(int(effective_font.getlength(word_line)) for word_line in lines)

    line_h = math.ceil(current_size * 1.15)
    total_h = len(lines) * line_h
    pad = 44
    layer_w = max_w + pad * 2
    layer_h = total_h + pad * 2

    # 1. Multi-pass Gaussian Glow Border
    glow = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    for idx, line in enumerate(lines):
        lw = effective_font.getlength(line)
        lx = (layer_w - lw) / 2
        ly = pad + idx * line_h
        # Heavy neon stroke
        gdraw.text(
            (lx, ly),
            line,
            font=effective_font,
            fill=(*glow_color_rgb, 255),
            stroke_width=18,
            stroke_fill=(*glow_color_rgb, 255),
        )
    glow = glow.filter(ImageFilter.GaussianBlur(12))

    # 2. Crisp Black Outline + Luminous Inner Text
    txt = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(txt)
    for idx, line in enumerate(lines):
        lw = effective_font.getlength(line)
        lx = (layer_w - lw) / 2
        ly = pad + idx * line_h
        # Color accents: last word is vibrant neon yellow, first word is crisp white
        is_accent = idx == len(lines) - 1
        fill_color = (*glow_color_rgb, 255) if is_accent else (255, 255, 255, 255)
        tdraw.text(
            (lx, ly),
            line,
            font=effective_font,
            fill=fill_color,
            stroke_width=8,
            stroke_fill=COLOR_TEXT_STROKE,
        )

    composite = Image.alpha_composite(glow, txt)
    return composite, layer_w, layer_h


def draw_kinetic_subtitles(
    canvas: Image.Image,
    caption: str,
    t_seg: float,
    duration: float,
    words_per_burst: int = WORDS_PER_BURST,
    font_size: int = KINETIC_FONT_SIZE,
    center_y: int = KINETIC_CENTER_Y,
    pop_scale: bool = True,
) -> None:
    """Draw rapid kinetic 2-word burst subtitle with glowing border and scale pop."""
    bursts = split_into_bursts(caption, words_per_burst=words_per_burst)
    if not bursts:
        return

    burst_duration = duration / len(bursts)
    current_idx = min(len(bursts) - 1, int(t_seg / max(0.01, burst_duration)))
    active_burst = bursts[current_idx]

    # Time within this burst
    time_in_burst = t_seg - current_idx * burst_duration
    pop_dur = 0.12  # 120ms snappy entrance scale pop
    scale = 1.0
    if pop_scale and time_in_burst < pop_dur:
        p = time_in_burst / pop_dur
        scale = 1.0 + 0.10 * ((1.0 - p) ** 2)

    layer, lw, lh = get_kinetic_burst_layer(tuple(active_burst), font_size=font_size)

    if abs(scale - 1.0) > 0.01:
        nw = max(1, round(lw * scale))
        nh = max(1, round(lh * scale))
        scaled_layer = layer.resize((nw, nh), Image.Resampling.BILINEAR)
        px = (SHORTS_WIDTH - nw) // 2
        py = center_y - nh // 2
        canvas.paste(scaled_layer, (px, py), scaled_layer)
    else:
        px = (SHORTS_WIDTH - lw) // 2
        py = center_y - lh // 2
        canvas.paste(layer, (px, py), layer)


@lru_cache(maxsize=64)
def get_country_header_badge_layer(
    country: str,
    flag: str = "",
    width: int = SHORTS_WIDTH,
    height: int = HEADER_HEIGHT,
) -> Image.Image:
    header = Image.new("RGB", (width, height), COLOR_WHITE)
    hdraw = ImageDraw.Draw(header)

    # Bottom 2px subtle divider rule
    hdraw.rectangle((0, height - HEADER_RULE_PX, width - 1, height - 1), fill=COLOR_HEADER_BORDER)

    title_text = country.strip().upper()
    font_text = get_font(50, bold=True)
    text_w = font_text.getlength(title_text)
    ink_top, ink_h = _ink_metrics(font_text, title_text)

    flag_img = get_country_flag(country, target_height=48)
    flag_w = flag_img.width if flag_img else 60
    pad_x = 28
    spacing = 18

    badge_w = int(pad_x * 2 + flag_w + spacing + text_w)
    badge_h = 72
    bx0 = (width - badge_w) // 2
    by0 = (height - badge_h) // 2

    # Draw refined pill badge container
    hdraw.rounded_rectangle(
        (bx0, by0, bx0 + badge_w, by0 + badge_h),
        radius=22,
        fill=(245, 248, 252),
        outline=(195, 205, 220),
        width=2,
    )

    if flag_img:
        fx = bx0 + pad_x
        fy = by0 + (badge_h - flag_img.height) // 2
        header.paste(flag_img, (fx, fy), flag_img)
        tx = fx + flag_w + spacing
    else:
        tx = bx0 + pad_x

    ty = by0 + (badge_h - ink_h) // 2 - ink_top
    hdraw.text((round(tx), round(ty)), title_text, font=font_text, fill=COLOR_BLACK)
    return header


def draw_country_header_badge(
    canvas: Image.Image,
    country: str,
    flag: str = "",
    width: int = SHORTS_WIDTH,
    height: int = HEADER_HEIGHT,
) -> None:
    """Draw Top Country Header Badge (150px) with stylized pill container and real flag."""
    header = get_country_header_badge_layer(country, flag, width, height)
    canvas.paste(header, (0, 0))


draw_country_header_banner = draw_country_header_badge


def build_shorts_remotion_plan(
    segments: list[WojakShortsSegment],
    output_plan_path: Path,
    disclaimer_duration: float = DISCLAIMER_DURATION,
    words_per_burst: int = WORDS_PER_BURST,
    font_size: int = KINETIC_FONT_SIZE,
    fps: int = FPS,
) -> Path:
    """Build a Remotion-compatible rapid caption plan JSON for 9:16 Shorts."""
    from autoshorts.media.ass_styler import TimedWord
    from autoshorts.media.remotion_overlay import write_caption_plan

    timed_words: list[TimedWord] = []
    curr_t = disclaimer_duration
    for seg in segments:
        words = [w for w in seg.caption.strip().split() if w]
        if words:
            word_dur = seg.duration / len(words)
            for i, w in enumerate(words):
                w_start = curr_t + i * word_dur
                w_end = w_start + word_dur
                timed_words.append(TimedWord(word=w, start=w_start, end=w_end))
        curr_t += seg.duration

    total_dur = curr_t
    return write_caption_plan(
        path=output_plan_path,
        words=timed_words,
        duration_sec=total_dur,
        style_preset="rapid",
        fps=fps,
        top_percent=28.0,
    )


def create_soft_drop_shadow(
    width: int,
    height: int,
    radius: int = 22,
    blur: int = 22,
    offset_y: int = 14,
    shadow_color: tuple[int, int, int, int] = COLOR_SHADOW,
) -> tuple[Image.Image, int, int]:
    """Create a soft Gaussian-blurred drop shadow for the floating joke card."""
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


@lru_cache(maxsize=32)
def _cached_procedural_background(
    country: str, width: int = SHORTS_WIDTH, height: int = 1770
) -> Image.Image:
    """Generate high-contrast atmospheric background when no photo is on disk."""
    try:
        from autoshorts.longform.wojak_backgrounds import render_procedural_hospital

        return render_procedural_hospital(country, width=width, height=height)
    except Exception:
        logger.warning("Procedural hospital background failed; using fallback", exc_info=True)

    slug = country.strip().lower()
    # Seed based on country for determinism
    hue_map = {
        "usa": ((35, 45, 65), (75, 90, 120)),
        "germany": ((40, 42, 45), (70, 75, 80)),
        "japan": ((45, 35, 50), (95, 75, 90)),
        "uk": ((30, 40, 48), (65, 80, 95)),
        "canada": ((30, 45, 55), (70, 95, 115)),
        "france": ((45, 38, 48), (90, 75, 85)),
        "australia": ((55, 40, 30), (110, 85, 60)),
        "india": ((55, 42, 28), (115, 85, 55)),
    }
    top_c, bot_c = hue_map.get(slug, ((32, 38, 48), (68, 76, 92)))

    img = Image.new("RGB", (width, height), top_c)
    draw = ImageDraw.Draw(img)

    # Vertical gradient with atmospheric perspective
    for y in range(0, height, 4):
        frac = y / height
        r = int(top_c[0] * (1 - frac) + bot_c[0] * frac)
        g = int(top_c[1] * (1 - frac) + bot_c[1] * frac)
        b = int(top_c[2] * (1 - frac) + bot_c[2] * frac)
        draw.line([(0, y), (width, y)], fill=(r, g, b), width=4)

    # Architectural room grid lines (hospital corridor or subway tunnel depth)
    depth_color = (max(0, top_c[0] - 8), max(0, top_c[1] - 8), max(0, top_c[2] - 8))
    center_x = width // 2
    vanish_y = int(height * 0.42)
    for x in range(0, width + 1, 160):
        draw.line([(x, height), (center_x, vanish_y)], fill=depth_color, width=2)

    return img


def generate_procedural_background(
    country: str, width: int = SHORTS_WIDTH, height: int = 1770
) -> Image.Image:
    """Return a fresh copy of the cached procedural background."""
    return _cached_procedural_background(country.strip().lower(), width=width, height=height).copy()


# ============================================================================
# Opening Scene 0: 2.5s Parody Disclaimer / Hook Banner
# ============================================================================


def render_shorts_disclaimer_frame(
    size: tuple[int, int] = (SHORTS_WIDTH, SHORTS_HEIGHT),
    badge_text: str = "⚠️  COMEDY PARODY",
    headline: str = "EXAGGERATED COUNTRY STEREOTYPES!",
    sub_notice: str = "CREATED FOR SATIRICAL & ENTERTAINMENT PURPOSES ONLY • ALL TROPES ARE INTERNET MEMES",
) -> Image.Image:
    """Render Scene 0 (2.5s) Parody Disclaimer & Hook frame in sleek vertical layout."""
    w, h = size
    canvas = Image.new("RGB", (w, h), COLOR_DISCLAIMER_BG)
    draw = ImageDraw.Draw(canvas)

    # Sleek dark vertical lines / subtle vignette
    for i in range(0, h, 6):
        shade = max(12, int(20 - (i / h) * 8))
        draw.line([(0, i), (w, i)], fill=(shade, shade + 2, shade + 6), width=1)

    # Centered Vertical Disclaimer Card (safe from margins)
    card_w = SAFE_WIDTH
    card_h = 760
    card_x0 = SAFE_LEFT
    card_y0 = (h - card_h) // 2 - 40
    card_y1 = card_y0 + card_h

    # Card Drop Shadow
    shadow_img, pad_x, pad_y = create_soft_drop_shadow(
        card_w, card_h, radius=24, blur=28, offset_y=16, shadow_color=(0, 0, 0, 150)
    )
    canvas.paste(shadow_img, (card_x0 - pad_x, card_y0 - pad_y), shadow_img)

    # Card Background & Border
    card_canvas = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(card_canvas)
    cdraw.rounded_rectangle(
        (0, 0, card_w - 1, card_h - 1),
        radius=24,
        fill=(*COLOR_DISCLAIMER_CARD, 248),
        outline=(*COLOR_DISCLAIMER_BORDER, 255),
        width=2,
    )
    canvas.paste(card_canvas, (card_x0, card_y0), card_canvas)
    draw = ImageDraw.Draw(canvas)

    # Warning Gold Pill Badge
    badge_font = get_font(34, bold=True)
    badge_text_w = badge_font.getlength(badge_text)
    badge_pad_x = 36
    badge_h = 60
    badge_w = int(badge_text_w + badge_pad_x * 2)
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

    # Hook Headline (Bold White, 46px)
    headline_font = get_font(44, bold=True)
    head_lines = wrap_text(headline_font, headline, card_w - 120)
    line_h = 56
    hy = badge_y1 + 45
    for line in head_lines:
        lw = headline_font.getlength(line)
        lx = (w - lw) / 2
        draw.text((lx, hy), line, font=headline_font, fill=COLOR_WHITE)
        hy += line_h

    # Parody Explanation Text
    body_font = get_font(32, bold=False)
    body_text = (
        "This video is strictly a parody created for comedic & entertainment purposes.\n\n"
        "All situations, healthcare tropes, and cultural habits are exaggerated memes.\n\n"
        "Please enjoy the humor and take everything with a grain of salt!"
    )
    paragraphs = body_text.split("\n\n")
    by_text = hy + 20
    b_line_h = 44
    for p in paragraphs:
        p_lines = wrap_text(body_font, p, card_w - 140)
        for pline in p_lines:
            pw = body_font.getlength(pline)
            px = (w - pw) / 2
            draw.text((px, by_text), pline, font=body_font, fill=(215, 222, 235))
            by_text += b_line_h
        by_text += 16

    # Bottom Sub-Notice
    sub_font = get_font(20, bold=True)
    sub_lines = wrap_text(sub_font, sub_notice, card_w - 80)
    sy = card_y1 - 65
    for sline in sub_lines:
        sw = sub_font.getlength(sline)
        sx = (w - sw) / 2
        draw.text((sx, sy), sline, font=sub_font, fill=(130, 140, 155))
        sy += 26

    return canvas


# ============================================================================
# Country Meme Segment Frame Compositor (Pure PIL)
# ============================================================================


def render_shorts_country_frame(
    country: str,
    caption: str,
    flag: str = "",
    background_img: Image.Image | None = None,
    character_img: Image.Image | None = None,
    expression: str = "neutral",
    size: tuple[int, int] = (SHORTS_WIDTH, SHORTS_HEIGHT),
    character_height: int = CHARACTER_HEIGHT,
    progress: float = 0.0,
    t_seg: float = 0.0,
    duration: float = DEFAULT_SEGMENT_DURATION,
    subtitle_mode: str = "kinetic",
    words_per_burst: int = WORDS_PER_BURST,
    font_size: int = KINETIC_FONT_SIZE,
) -> Image.Image:
    """Render a 1080x1920 YouTube Shorts frame with kinetic subtitles or joke card."""
    w, h = size
    canvas = Image.new("RGB", (w, h), COLOR_WHITE)

    # 1. Background (Fills y: HEADER_HEIGHT to h = 1770px)
    bg_h = h - HEADER_HEIGHT
    if background_img is None:
        bg = generate_procedural_background(country, width=w, height=bg_h)
    else:
        bg = background_img.convert("RGB")

    # Ken-Burns subtle camera push (1.00 -> 1.03)
    zoom = 1.0 + 0.03 * max(0.0, min(1.0, progress))
    scale = max(w / bg.width, bg_h / bg.height) * zoom
    new_w = max(w, round(bg.width * scale))
    new_h = max(bg_h, round(bg.height * scale))
    bg = bg.resize((new_w, new_h), Image.Resampling.BILINEAR)
    left = (new_w - w) // 2
    top = (new_h - bg_h) // 2
    bg = bg.crop((left, top, left + w, top + bg_h))

    # Subtle contrast treatment so text cards and characters pop
    canvas.paste(bg, (0, HEADER_HEIGHT))

    # 2. Floating White Joke Card (when card or both subtitle mode is active)
    clean_caption = " ".join(caption.split())
    if clean_caption and subtitle_mode in ("card", "both"):
        card_w = SAFE_WIDTH  # 820px (Safe from right action buttons x > 920)
        card_x0 = SAFE_LEFT  # 100px (Centered inside safe horizontal corridor)
        pad_x = 42
        pad_y = 30

        font, lines, line_h = fit_punchline(
            clean_caption,
            max_w=card_w - 2 * pad_x,
            max_h=CARD_MAX_HEIGHT - 2 * pad_y,
            max_lines=3,
            initial_size=50,
            min_size=38,
        )

        text_total_h = len(lines) * line_h
        card_h = text_total_h + pad_y * 2
        card_y0 = CARD_Y0

        # Drop Shadow
        shadow_img, s_pad_x, s_pad_y = create_soft_drop_shadow(
            card_w,
            card_h,
            radius=22,
            blur=22,
            offset_y=14,
            shadow_color=COLOR_SHADOW,
        )
        canvas.paste(shadow_img, (card_x0 - s_pad_x, card_y0 - s_pad_y), shadow_img)

        # White Rounded Card with crisp subtle border
        card_canvas = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        cdraw = ImageDraw.Draw(card_canvas)
        cdraw.rounded_rectangle(
            (0, 0, card_w - 1, card_h - 1),
            radius=22,
            fill=(*COLOR_WHITE, 255),
            outline=(*COLOR_CARD_BORDER, 255),
            width=2,
        )
        canvas.paste(card_canvas, (card_x0, card_y0), card_canvas)

        # Punchline Text (Bold Black Sans, Centered)
        draw = ImageDraw.Draw(canvas)
        ink_top, ink_h = _ink_metrics(font)
        center_x = card_x0 + card_w / 2
        for idx, line in enumerate(lines):
            lw = font.getlength(line)
            lx = center_x - lw / 2
            row_y = card_y0 + pad_y + idx * line_h
            ly = row_y + (line_h - ink_h) / 2 - ink_top
            draw.text((lx, ly), line, font=font, fill=COLOR_BLACK)

    # 3. Expressive Procedural Wojak Character
    # Scaled to ~750-880px tall, base anchored to bottom, upper body/head in y: 1050 to 1500
    if character_img is None:
        try:
            spec = spec_from_country_and_context(
                country=country,
                setting="hospital",
                scenario=caption,
                expression=expression,
            )
            char_img = draw_wojak(spec)
        except Exception:
            spec = spec_from_country_and_context(
                country="generic",
                setting="hospital",
                scenario="",
                expression="neutral",
            )
            char_img = draw_wojak(spec)
    else:
        char_img = character_img

    target_h = min(character_height, bg_h)
    target_w = max(1, round(char_img.width * (target_h / char_img.height)))
    if char_img.mode == "RGBA" and char_img.size == (target_w, target_h):
        char = char_img
    else:
        char = char_img.convert("RGBA").resize((target_w, target_h), Image.Resampling.BILINEAR)

    # Center character horizontally in safe zone (e.g. x: 100 to 920)
    char_x = (w - target_w) // 2
    char_y = h - target_h + 30  # Slight ground overlap for grounded look
    canvas.paste(char, (char_x, char_y), char)

    # 4. Top White Header Banner Strip & Country Header Badge
    draw_country_header_badge(canvas, country, flag=flag, width=w, height=HEADER_HEIGHT)

    # 5. Rapid Kinetic Subtitles (2 words/burst, 84pt font, glowing borders)
    if clean_caption and subtitle_mode in ("kinetic", "both"):
        draw_kinetic_subtitles(
            canvas,
            caption=clean_caption,
            t_seg=t_seg,
            duration=duration,
            words_per_burst=words_per_burst,
            font_size=font_size,
        )

    return canvas


# ============================================================================
# Procedural Comedic SFX Synthesis (Pure Numpy, 48kHz Mono)
# ============================================================================


def _write_wav(path: Path | str, samples: np.ndarray, sr: int = SAMPLE_RATE) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(samples, dtype=np.float32)
    peak = float(np.max(np.abs(arr))) if len(arr) > 0 else 0.0
    if peak > 1.0:
        arr = arr / peak
    int_samples = np.clip(np.round(arr * _MAX_INT16), -_MAX_INT16, _MAX_INT16).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(int_samples.tobytes())
    return path


def synth_cash_register(path: Path, duration: float = 0.85) -> Path:
    """Synthesize authentic 'Ka-Ching!' cash register bell ding and coin rattle."""
    sr = SAMPLE_RATE
    n = int(round(duration * sr))
    t = np.linspace(0.0, duration, n, endpoint=False)

    # 1. Dual Bell Ding (Ka-Ching!) at 2180 Hz and 4360 Hz with exponential decay
    bell = (
        0.68 * np.sin(2.0 * np.pi * 2180.0 * t) + 0.32 * np.sin(2.0 * np.pi * 4360.0 * t)
    ) * np.exp(-t / 0.14)

    # 2. Mechanical drawer spring & metallic latch clack at 0.18s
    t_clack = np.maximum(0.0, t - 0.18)
    clack = 0.45 * np.sin(2.0 * np.pi * 320.0 * t_clack) * np.exp(-t_clack / 0.04) * (t >= 0.18)

    # 3. Coins rattling in metal drawer (sparkling high-frequency clicks)
    rng = np.random.RandomState(42)
    coins = np.zeros(n, dtype=np.float32)
    for _ in range(24):
        ct = rng.uniform(0.20, 0.65)
        cf = rng.uniform(3200.0, 7800.0)
        tc = np.maximum(0.0, t - ct)
        coins += (
            rng.uniform(0.12, 0.35)
            * np.sin(2.0 * np.pi * cf * tc)
            * np.exp(-tc / 0.015)
            * (t >= ct)
        )

    mix = bell + clack + coins
    return _write_wav(path, np.tanh(1.5 * mix), sr)


def synth_flatline(path: Path, duration: float = 1.2) -> Path:
    """Synthesize medical ECG monitor flatline tone ('Beeeeep')."""
    sr = SAMPLE_RATE
    n = int(round(duration * sr))
    t = np.linspace(0.0, duration, n, endpoint=False)

    tone = 0.82 * np.sin(2.0 * np.pi * 1040.0 * t) + 0.18 * np.sin(2.0 * np.pi * 2080.0 * t)
    env = np.ones(n, dtype=np.float32)
    att = int(0.006 * sr)
    env[:att] = np.linspace(0.0, 1.0, att)
    rel = int(0.12 * sr)
    env[-rel:] = np.linspace(1.0, 0.0, rel)

    return _write_wav(path, np.tanh(1.2 * tone * env), sr)


def write_record_scratch_sfx(path: Path, duration: float = 0.45) -> Path:
    """Write a record-scratch SFX WAV for the Shorts renderer.

    Note: a differently-tuned record scratch also exists in ``wojak_audio``
    (array-returning) and ``meme.audio``; they are intentionally not merged,
    since merging would change the audio of existing renders.
    """
    sr = SAMPLE_RATE
    n = int(round(duration * sr))
    t = np.linspace(0.0, duration, n, endpoint=False)

    freq = np.zeros(n, dtype=np.float32)
    p1 = t < 0.20
    freq[p1] = 900.0 + 2600.0 * (t[p1] / 0.20) ** 1.8
    p2 = (t >= 0.20) & (t < 0.38)
    freq[p2] = 3500.0 * np.exp(-12.0 * (t[p2] - 0.20)) + 300.0
    p3 = t >= 0.38
    freq[p3] = 250.0 * np.exp(-16.0 * (t[p3] - 0.38))

    phase = 2.0 * np.pi * np.cumsum(freq) / sr
    carrier = np.sin(phase) + 0.35 * np.sin(2.0 * phase)

    rng = np.random.RandomState(42)
    friction = rng.uniform(-1.0, 1.0, n).astype(np.float32) * np.sin(phase)
    env = np.ones(n, dtype=np.float32)
    att_len = int(0.015 * sr)
    if att_len > 0:
        env[:att_len] = np.linspace(0.0, 1.0, att_len)
    tail = int(0.38 * sr)
    if tail < n:
        env[tail:] = np.maximum(0.0, 1.0 - (t[tail:] - 0.38) / 0.07)

    mix = (0.55 * carrier + 0.45 * friction) * env
    return _write_wav(path, np.tanh(1.8 * mix), sr)


def synth_whoosh(path: Path, duration: float = 0.25) -> Path:
    """Fast energetic whoosh stinger."""
    sr = SAMPLE_RATE
    n = int(round(duration * sr))
    t = np.linspace(0.0, duration, n, endpoint=False)
    rng = np.random.RandomState(42)
    noise = rng.uniform(-1.0, 1.0, n).astype(np.float32)
    env = np.sin(np.pi * t / duration) ** 2
    f_center = 400.0 + 1600.0 * np.sin(np.pi * t / duration)
    carrier = np.sin(2.0 * np.pi * f_center * t)
    return _write_wav(path, np.tanh(1.6 * (0.6 * noise + 0.4 * carrier) * env), sr)


def synth_punch(path: Path, duration: float = 0.35) -> Path:
    """Cartoon punch impact."""
    sr = SAMPLE_RATE
    n = int(round(duration * sr))
    t = np.linspace(0.0, duration, n, endpoint=False)
    f = 160.0 * np.exp(-18.0 * t) + 45.0
    body = np.sin(2.0 * np.pi * f * t) * np.exp(-8.0 * t)
    rng = np.random.RandomState(42)
    trans = rng.uniform(-1.0, 1.0, n).astype(np.float32) * np.exp(-35.0 * t)
    return _write_wav(path, np.tanh(2.0 * (body + 0.5 * trans)), sr)


# ============================================================================
# Procedural BGM Synthesizer (Continuous Bouncy Meme Track)
# ============================================================================


def synth_bouncy_meme_bgm(path: Path, duration: float, bpm: float = 126.0) -> Path:
    """Synthesize unbroken, upbeat 126 BPM bouncy comedy walking bass and synth stabs."""
    sr = SAMPLE_RATE
    n = int(round(duration * sr))

    beat_dur = 60.0 / bpm

    output = np.zeros(n, dtype=np.float32)

    # 1. Bouncy walking bassline (Roots: C, E, F, G in loop)
    bass_notes = [65.41, 82.41, 87.31, 98.00]  # C2, E2, F2, G2
    for b_idx in range(int(duration / beat_dur) + 2):
        t_start = b_idx * beat_dur
        if t_start >= duration:
            break
        note_freq = bass_notes[b_idx % len(bass_notes)]
        sample_start = int(t_start * sr)
        note_len = min(int(beat_dur * 0.85 * sr), n - sample_start)
        if note_len <= 0:
            continue
        sub_t = np.linspace(0.0, note_len / sr, note_len, endpoint=False)
        # Warm plucked bass waveform
        bass_env = np.exp(-sub_t / (beat_dur * 0.45))
        bass_wave = (
            np.sin(2.0 * np.pi * note_freq * sub_t)
            + 0.40 * np.sin(4.0 * np.pi * note_freq * sub_t)
            + 0.15 * np.sin(6.0 * np.pi * note_freq * sub_t)
        )
        output[sample_start : sample_start + note_len] += 0.38 * bass_wave * bass_env

    # 2. Syncopated comedic chord stabs (on off-beats 2 & 4)
    # Triads: C-maj (261.6, 329.6, 392.0), F-maj (349.2, 440.0, 523.2), G-maj (392.0, 493.8, 587.3)
    chords = [
        [261.63, 329.63, 392.00],  # C
        [329.63, 392.00, 493.88],  # Em
        [349.23, 440.00, 523.25],  # F
        [392.00, 493.88, 587.33],  # G
    ]
    for b_idx in range(int(duration / beat_dur) + 2):
        # Offbeats (beats 2 and 4 in each bar)
        if b_idx % 2 == 1:
            t_start = b_idx * beat_dur
            if t_start >= duration:
                break
            sample_start = int(t_start * sr)
            chord = chords[(b_idx // 2) % len(chords)]
            stab_len = min(int(beat_dur * 0.40 * sr), n - sample_start)
            if stab_len <= 0:
                continue
            sub_t = np.linspace(0.0, stab_len / sr, stab_len, endpoint=False)
            stab_env = np.exp(-sub_t / 0.08)
            stab_wave = np.zeros(stab_len, dtype=np.float32)
            for f in chord:
                stab_wave += np.sin(2.0 * np.pi * f * sub_t) + 0.25 * np.sin(
                    4.0 * np.pi * f * sub_t
                )
            output[sample_start : sample_start + stab_len] += 0.22 * stab_wave * stab_env

    # 3. Brush drum groove (Kick on 1 & 3, light snare brush on 2 & 4, hihat swing)
    rng = np.random.RandomState(1337)
    for b_idx in range(int(duration / beat_dur) + 2):
        t_start = b_idx * beat_dur
        if t_start >= duration:
            break
        sample_start = int(t_start * sr)
        is_downbeat = b_idx % 2 == 0
        drum_len = min(int(0.12 * sr), n - sample_start)
        if drum_len <= 0:
            continue
        dt = np.linspace(0.0, drum_len / sr, drum_len, endpoint=False)
        if is_downbeat:
            # Kick punch (sub sweep)
            kick = np.sin(2.0 * np.pi * (110.0 * np.exp(-32.0 * dt) + 42.0) * dt) * np.exp(
                -18.0 * dt
            )
            output[sample_start : sample_start + drum_len] += 0.32 * kick
        else:
            # Snare brush tap
            noise = rng.uniform(-1.0, 1.0, drum_len).astype(np.float32) * np.exp(-22.0 * dt)
            output[sample_start : sample_start + drum_len] += 0.20 * noise

    # Master soft limiter
    master = np.tanh(1.4 * output)
    return _write_wav(path, master, sr)


def assemble_shorts_audio(
    segments: list[WojakShortsSegment],
    total_duration: float,
    work_dir: Path,
    disclaimer_duration: float = DISCLAIMER_DURATION,
    bgm_mood: str = "playful",
) -> Path:
    """Generate continuous bouncy meme BGM and mix in surgical comedic SFX cues."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    master_sr = SAMPLE_RATE
    total_samples = int(round(total_duration * master_sr))
    mix_buf = np.zeros(total_samples, dtype=np.float32)

    # 1. Synthesize Continuous BGM matching mood (Production Bible Section 5)
    from autoshorts.longform.wojak_audio import generate_procedural_wojak_bgm

    bgm_path = work_dir / f"bgm_{uuid4().hex[:8]}.wav"
    try:
        bgm_data = generate_procedural_wojak_bgm(total_duration, sr=master_sr, mood=bgm_mood)
        _write_wav(bgm_path, bgm_data, master_sr)
    except Exception as exc:
        logger.warning("Procedural genre BGM generation fallback: %s", exc)
        synth_bouncy_meme_bgm(bgm_path, duration=total_duration)

    with wave.open(str(bgm_path), "rb") as w:
        bgm_data = (
            np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
            / _MAX_INT16
        )
    bgm_len = min(len(bgm_data), total_samples)
    mix_buf[:bgm_len] += 0.70 * bgm_data[:bgm_len]

    # 2. Add Surgical Comedic SFX Stingers at segment transitions
    sfx_cache: dict[str, Path] = {}
    current_time = disclaimer_duration

    for seg in segments:
        cue = seg.sfx_cue.lower()
        if cue and cue != "none":
            sfx_file = sfx_cache.get(cue)
            if not sfx_file:
                sfx_file = work_dir / f"sfx_{cue}.wav"
                if cue == "cash_register":
                    synth_cash_register(sfx_file)
                elif cue == "flatline":
                    synth_flatline(sfx_file)
                elif cue == "record_scratch":
                    write_record_scratch_sfx(sfx_file)
                elif cue == "whoosh":
                    synth_whoosh(sfx_file)
                elif cue == "punch":
                    synth_punch(sfx_file)
                sfx_cache[cue] = sfx_file

            if sfx_file and sfx_file.is_file():
                try:
                    with wave.open(str(sfx_file), "rb") as sw:
                        sfx_raw = (
                            np.frombuffer(sw.readframes(sw.getnframes()), dtype=np.int16).astype(
                                np.float32
                            )
                            / _MAX_INT16
                        )
                    # Trigger SFX 0.25s into the segment
                    sfx_start = int((current_time + 0.25) * master_sr)
                    sfx_len = min(len(sfx_raw), total_samples - sfx_start)
                    if sfx_start < total_samples and sfx_len > 0:
                        mix_buf[sfx_start : sfx_start + sfx_len] += 0.85 * sfx_raw[:sfx_len]
                except Exception as exc:
                    logger.warning("Could not mix SFX %s: %s", cue, exc)

        current_time += seg.duration

    # Master peak limiter & write output (-1.0 dBFS ceiling)
    raw_path = work_dir / "shorts_audio_raw.wav"
    master = np.tanh(1.2 * mix_buf)
    ceiling_linear = 10.0 ** (-1.0 / 20.0)
    peak = float(np.max(np.abs(master))) if len(master) > 0 else 0.0
    if peak > ceiling_linear:
        master = (master / peak) * ceiling_linear
    _write_wav(raw_path, master, master_sr)

    # Apply EBU R128 loudness normalization (-14.0 LUFS, -1.0 dBFS true peak)
    out_path = work_dir / "shorts_audio_master.wav"
    try:
        normalize_two_pass(raw_path, out_path, target_lufs=-14.0, true_peak_db=-1.0)
    except Exception as norm_err:
        logger.warning("Shorts audio loudness normalization fallback: %s", norm_err)
        return raw_path
    return out_path


# ============================================================================
# Curated Country Joke Database for Shorts
# ============================================================================

CURATED_SHORTS_TOPICS: dict[str, list[dict[str, Any]]] = {
    "healthcare": [
        {
            "country": "USA",
            "flag": "🇺🇸",
            "caption": "Got a minor paper cut at 3 AM. Total emergency room invoice: $84,200.",
            "expression": "shocked",
            "sfx": "cash_register",
        },
        {
            "country": "UK",
            "flag": "🇬🇧",
            "caption": "Free universal healthcare! Your routine MRI scan is scheduled for November 2029.",
            "expression": "tired",
            "sfx": "flatline",
        },
        {
            "country": "CANADA",
            "flag": "🇨🇦",
            "caption": "Doctor apologized 14 times, offered a maple syrup lozenge, and told me to wait outside.",
            "expression": "neutral",
            "sfx": "none",
        },
        {
            "country": "GERMANY",
            "flag": "🇩🇪",
            "caption": "Please submit Form 28-B in triplicate via fax before having a medical emergency.",
            "expression": "deadpan",
            "sfx": "whoosh",
        },
        {
            "country": "JAPAN",
            "flag": "🇯🇵",
            "caption": "Surgeon bowed 12 times, completed robotic procedure, and clocked in for 36-hour shift.",
            "expression": "tired",
            "sfx": "none",
        },
        {
            "country": "FRANCE",
            "flag": "🇫🇷",
            "caption": "Emergency wing closed until 3 PM due to shortage of artisanal baguettes.",
            "expression": "annoyed",
            "sfx": "record_scratch",
        },
        {
            "country": "AUSTRALIA",
            "flag": "🇦🇺",
            "caption": "Nurse: 'No worries mate, that venomous spider bite builds good character.'",
            "expression": "smug",
            "sfx": "punch",
        },
        {
            "country": "SWITZERLAND",
            "flag": "🇨🇭",
            "caption": "5-star clinic suite overlooking the Alps. Single aspirin tablet: $4,800 CHF.",
            "expression": "smug",
            "sfx": "cash_register",
        },
    ],
    "transport": [
        {
            "country": "JAPAN",
            "flag": "🇯🇵",
            "caption": "Bullet train delayed by 14 seconds. Railway CEO holds televised public apology.",
            "expression": "shocked",
            "sfx": "record_scratch",
        },
        {
            "country": "GERMANY",
            "flag": "🇩🇪",
            "caption": "Bahn App: 'Your 2:15 train is cancelled due to leaves on the tracks.'",
            "expression": "tired",
            "sfx": "flatline",
        },
        {
            "country": "USA",
            "flag": "🇺🇸",
            "caption": "Subway? We drive a 4-ton V8 pickup truck to fetch mail from across the street.",
            "expression": "smug",
            "sfx": "cash_register",
        },
        {
            "country": "UK",
            "flag": "🇬🇧",
            "caption": "Stand on the right of the escalator or face immediate societal exile.",
            "expression": "annoyed",
            "sfx": "whoosh",
        },
        {
            "country": "INDIA",
            "flag": "🇮🇳",
            "caption": "Train capacity: 400 people inside, 800 hanging outside holding hands happily.",
            "expression": "smug",
            "sfx": "punch",
        },
        {
            "country": "FRANCE",
            "flag": "🇫🇷",
            "caption": "Metro operating normally today... just kidding, surprise nationwide strike!",
            "expression": "annoyed",
            "sfx": "record_scratch",
        },
        {
            "country": "CANADA",
            "flag": "🇨🇦",
            "caption": "Subway delayed for 40 minutes while a moose safely crossed the platform.",
            "expression": "neutral",
            "sfx": "none",
        },
    ],
    "rent": [
        {
            "country": "USA",
            "flag": "🇺🇸",
            "caption": "NYC Studio: 180 sq ft, shared toilet in hallway. Rent: $4,900/month.",
            "expression": "crying",
            "sfx": "cash_register",
        },
        {
            "country": "UK",
            "flag": "🇬🇧",
            "caption": "London flat: Bedroom is under the staircase with authentic Victorian damp mold.",
            "expression": "tired",
            "sfx": "flatline",
        },
        {
            "country": "CANADA",
            "flag": "🇨🇦",
            "caption": "Vancouver basement closet: $2,800/month. No cooking or breathing allowed.",
            "expression": "annoyed",
            "sfx": "whoosh",
        },
        {
            "country": "GERMANY",
            "flag": "🇩🇪",
            "caption": "Rent is cheap, but you need 80 credit documents and bring your own kitchen sink.",
            "expression": "deadpan",
            "sfx": "record_scratch",
        },
        {
            "country": "JAPAN",
            "flag": "🇯🇵",
            "caption": "Room is 6 tatami mats. Fold your bed into the wall or sleep standing up.",
            "expression": "neutral",
            "sfx": "none",
        },
        {
            "country": "AUSTRALIA",
            "flag": "🇦🇺",
            "caption": "Sydney beach shack: $1,200/week. 3 roommates, 4 resident huntsman spiders.",
            "expression": "shocked",
            "sfx": "punch",
        },
    ],
}


def build_wojak_shorts_script(topic: str, segment_count: int = 7) -> list[WojakShortsSegment]:
    """Select or generate punchy country comparison segments for vertical Shorts."""
    topic_clean = topic.strip().lower()

    if any(
        k in topic_clean for k in ("hospital", "health", "doctor", "medicine", "medical", "bill")
    ):
        key = "healthcare"
    elif any(k in topic_clean for k in ("transport", "subway", "train", "metro", "bus")):
        key = "transport"
    elif any(k in topic_clean for k in ("rent", "housing", "apartment", "landlord")):
        key = "rent"
    else:
        # Check if topic is covered by curated longform topics (e.g. school, transport, etc.)
        try:
            from autoshorts.longform.wojak_pipeline import resolve_topic_scenes

            _, p_scenes = resolve_topic_scenes(topic, scene_count=segment_count)
            if p_scenes:
                segments: list[WojakShortsSegment] = []
                for s in p_scenes:
                    photo = pick_photo(s.country, seed=topic)
                    segments.append(
                        WojakShortsSegment(
                            country=s.country,
                            flag=s.country,
                            caption=s.caption,
                            expression=s.emotion,
                            duration=DEFAULT_SEGMENT_DURATION,
                            sfx_cue="whoosh",
                            photo_path=photo,
                        )
                    )
                return segments
        except Exception:
            logger.warning("Curated topic lookup failed; using generic script", exc_info=True)
        key = "healthcare"

    pool = CURATED_SHORTS_TOPICS.get(key, CURATED_SHORTS_TOPICS["healthcare"])
    count = max(4, min(segment_count, len(pool)))
    chosen = pool[:count]

    segments: list[WojakShortsSegment] = []
    for item in chosen:
        photo = pick_photo(item["country"], seed=topic)
        seg = WojakShortsSegment(
            country=item["country"],
            flag=item["flag"],
            caption=item["caption"],
            expression=item.get("expression", "neutral"),
            duration=DEFAULT_SEGMENT_DURATION,
            sfx_cue=item.get("sfx", "none"),
            photo_path=photo,
        )
        segments.append(seg)

    return segments


# ============================================================================
# High-CTR YouTube Shorts Thumbnail Generator
# ============================================================================


def make_shorts_thumbnail(
    topic: str,
    top_country: str = "USA",
    out_path: Path | str = "thumb.png",
    expression: str = "shocked",
) -> Path:
    """Generate high-contrast 1080x1920 YouTube Shorts thumbnail cover."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    canvas = Image.new("RGB", (SHORTS_WIDTH, SHORTS_HEIGHT), COLOR_DISCLAIMER_BG)
    draw = ImageDraw.Draw(canvas)

    # Ambient backdrop gradient
    bg = generate_procedural_background(top_country, width=SHORTS_WIDTH, height=SHORTS_HEIGHT)
    canvas.paste(bg, (0, 0))

    # Top Viral Hook Badge
    badge_font = get_font(42, bold=True)
    badge_text = "EVERY COUNTRY BE LIKE"
    bw = badge_font.getlength(badge_text)
    bx0 = (SHORTS_WIDTH - bw) // 2 - 30
    by0 = 220
    draw.rounded_rectangle(
        (bx0, by0, bx0 + bw + 60, by0 + 80),
        radius=18,
        fill=COLOR_GOLD,
        outline=COLOR_WHITE,
        width=3,
    )
    draw.text((bx0 + 30, by0 + 16), badge_text, font=badge_font, fill=COLOR_BLACK)

    # Topic Banner Box
    topic_font = get_font(64, bold=True)
    t_clean = topic.strip().upper()
    tw = topic_font.getlength(t_clean)
    if tw > SHORTS_WIDTH - 120:
        topic_font = get_font(48, bold=True)
        tw = topic_font.getlength(t_clean)
    tx = (SHORTS_WIDTH - tw) // 2
    ty = by0 + 110
    draw.text(
        (tx, ty),
        t_clean,
        font=topic_font,
        fill=COLOR_WHITE,
        stroke_width=4,
        stroke_fill=COLOR_BLACK,
    )

    # Wojak Character (Shocked / Smug)
    try:
        spec = spec_from_country_and_context(
            country=top_country,
            setting="hospital",
            scenario=topic,
            expression=expression,
        )
        char_img = draw_wojak(spec)
        char = char_img.convert("RGBA").resize((600, 1000), Image.Resampling.LANCZOS)
        cx = (SHORTS_WIDTH - 600) // 2
        cy = SHORTS_HEIGHT - 1040
        canvas.paste(char, (cx, cy), char)
    except Exception:
        # Character is decorative; keep the thumbnail, but never fail silently again.
        logger.warning("Shorts thumbnail: Wojak character failed to render", exc_info=True)

    canvas.save(str(out_path), "PNG")
    return out_path


# ============================================================================
# Streaming Pipeline & Master Video Generator
# ============================================================================


def render_wojak_shorts_video(
    topic: str = "Every Country's Hospital Be Like",
    segment_count: int = 7,
    output_dir: Path | str | None = None,
    config: Any = None,
    state_manager: Any = None,
    subtitle_mode: str = "kinetic",
    words_per_burst: int = WORDS_PER_BURST,
    font_size: int = KINETIC_FONT_SIZE,
    render_method: str = "ffmpeg",
) -> LongformResult:
    """Build and render a complete YouTube Shorts (9:16) Wojak country comparison video.

    Features:
    - Rapid kinetic subtitles (words_per_burst=2, 84pt font, glowing borders)
    - Stylized Top Country Header Badges with real national flags
    - Pure-PIL streaming directly to FFmpeg pipe:0 or Remotion overlay compositing
    """
    start_time = time.monotonic()
    run_id = uuid4().hex[:8]
    work_dir = get_work_dir() / f"wojak_shorts_{run_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    out_base = Path(output_dir) if output_dir else get_output_dir()
    out_base.mkdir(parents=True, exist_ok=True)
    video_out = out_base / f"shorts_wojak_{run_id}.mp4"
    thumb_out = out_base / f"shorts_wojak_{run_id}_thumb.png"

    # Check Remotion availability if requested
    use_remotion = False
    if render_method in ("remotion", "auto"):
        try:
            from autoshorts.media.remotion_overlay import is_remotion_ready

            if is_remotion_ready():
                use_remotion = render_method == "remotion"
        except Exception:
            use_remotion = False

    # 1. Build script segments
    segments = build_wojak_shorts_script(topic, segment_count=segment_count)
    total_duration = DISCLAIMER_DURATION + sum(s.duration for s in segments)

    logger.info(
        "Rendering Wojak Shorts (9:16) for topic %r: %d segments, %.1fs duration, subtitles=%s (burst=%d, font=%dpt), method=%s",
        topic,
        len(segments),
        total_duration,
        subtitle_mode,
        words_per_burst,
        font_size,
        "remotion" if use_remotion else "ffmpeg",
    )

    # 2. Assemble Master Audio Track
    audio_path = assemble_shorts_audio(
        segments=segments,
        total_duration=total_duration,
        work_dir=work_dir,
        disclaimer_duration=DISCLAIMER_DURATION,
    )

    # 3. Stream Raw RGB24 Frames to FFmpeg pipe:0
    ffmpeg_bin = get_ffmpeg_path()
    pipe_target = work_dir / "base_video.mp4" if use_remotion else video_out
    cmd = [
        ffmpeg_bin,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{SHORTS_WIDTH}x{SHORTS_HEIGHT}",
        "-r",
        str(FPS),
        "-i",
        "pipe:0",
        "-i",
        str(audio_path),
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(pipe_target),
    ]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None and proc.stderr is not None

    stderr_chunks: list[bytes] = []
    writer_errors: list[BaseException] = []

    effective_subtitles = "none" if use_remotion else subtitle_mode

    def _drain_stderr() -> None:
        try:
            stderr_chunks.append(proc.stderr.read())  # type: ignore[union-attr]
        except (OSError, ValueError):
            pass

    def _feed_frames() -> None:
        try:
            # A. Scene 0: Parody Disclaimer Card (2.5s)
            disclaimer_frame = render_shorts_disclaimer_frame()
            disc_bytes = disclaimer_frame.tobytes()
            n_disc_frames = int(round(DISCLAIMER_DURATION * FPS))
            for _ in range(n_disc_frames):
                proc.stdin.write(disc_bytes)  # type: ignore[union-attr]

            # B. Scenes 1 to N: Country Meme Segments (4.25s each)
            for seg in segments:
                # Load optional photo, or generate background once per segment
                bg_img: Image.Image | None = None
                if seg.photo_path and Path(seg.photo_path).is_file():
                    try:
                        with Image.open(str(seg.photo_path)) as p_img:
                            bg_img = p_img.convert("RGB")
                    except Exception:
                        bg_img = None
                if bg_img is None:
                    bg_img = generate_procedural_background(
                        seg.country, width=SHORTS_WIDTH, height=SHORTS_HEIGHT - HEADER_HEIGHT
                    )

                # Pre-render character once per segment for maximum performance
                try:
                    spec = spec_from_country_and_context(
                        country=seg.country,
                        setting="hospital",
                        scenario=seg.caption,
                        expression=seg.expression,
                    )
                    char_img = draw_wojak(spec)
                except Exception:
                    spec = spec_from_country_and_context(
                        country="generic",
                        setting="hospital",
                        scenario="",
                        expression="neutral",
                    )
                    char_img = draw_wojak(spec)

                target_h = min(CHARACTER_HEIGHT, SHORTS_HEIGHT - HEADER_HEIGHT)
                target_w = max(1, round(char_img.width * (target_h / char_img.height)))
                char_img = char_img.convert("RGBA").resize(
                    (target_w, target_h), Image.Resampling.BILINEAR
                )

                n_seg_frames = int(round(seg.duration * FPS))
                f_idx = 0
                while f_idx < n_seg_frames:
                    t_seg = f_idx / FPS
                    prog = f_idx / max(1, n_seg_frames - 1)
                    seg_frame = render_shorts_country_frame(
                        country=seg.country,
                        caption=seg.caption,
                        flag=seg.flag,
                        background_img=bg_img,
                        character_img=char_img,
                        expression=seg.expression,
                        progress=prog,
                        t_seg=t_seg,
                        duration=seg.duration,
                        subtitle_mode=effective_subtitles,
                        words_per_burst=words_per_burst,
                        font_size=font_size,
                    )
                    raw_bytes = seg_frame.tobytes()
                    proc.stdin.write(raw_bytes)  # type: ignore[union-attr]
                    f_idx += 1

        except (BrokenPipeError, OSError, ValueError):
            pass
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            try:
                proc.stdin.close()  # type: ignore[union-attr]
            except (OSError, ValueError):
                pass

    drainer = threading.Thread(target=_drain_stderr, name="wojak-shorts-stderr", daemon=True)
    writer = threading.Thread(target=_feed_frames, name="wojak-shorts-stdin", daemon=True)
    drainer.start()
    writer.start()

    writer.join(timeout=300.0)
    rc = proc.wait(timeout=60.0)
    drainer.join(timeout=10.0)

    if writer_errors:
        pipe_target.unlink(missing_ok=True)
        raise writer_errors[0]
    if rc != 0:
        err_msg = b"".join(stderr_chunks).decode("utf-8", "replace")
        pipe_target.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg encoding failed: {err_msg[-600:]}")

    # 4. Optional Remotion Overlay Compositing
    if use_remotion:
        try:
            from autoshorts.media.remotion_overlay import render_caption_overlay

            plan_path = work_dir / "remotion_plan.json"
            track_path = work_dir / "remotion_track.webm"
            build_shorts_remotion_plan(
                segments=segments,
                output_plan_path=plan_path,
                disclaimer_duration=DISCLAIMER_DURATION,
                words_per_burst=words_per_burst,
                font_size=font_size,
                fps=FPS,
            )
            render_caption_overlay(plan_path, track_path)

            # Composite transparent Remotion overlay onto base video
            comp_cmd = [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(pipe_target),
                "-c:v",
                "libvpx-vp9",
                "-i",
                str(track_path),
                "-filter_complex",
                "[0:v][1:v]overlay=0:0[v]",
                "-map",
                "[v]",
                "-map",
                "0:a",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(video_out),
            ]
            subprocess.run(comp_cmd, check=True, timeout=120)
            pipe_target.unlink(missing_ok=True)
        except Exception as r_err:
            logger.warning("Remotion overlay compositing failed (%s); using base video", r_err)
            if pipe_target != video_out and pipe_target.is_file():
                pipe_target.replace(video_out)

    # 5. Generate Thumbnail
    make_shorts_thumbnail(
        topic, top_country=segments[0].country if segments else "USA", out_path=thumb_out
    )

    elapsed = time.monotonic() - start_time
    logger.info("Wojak Shorts rendered successfully in %.2fs -> %s", elapsed, video_out)

    return LongformResult(
        topic=topic,
        title=f"{topic} #Shorts",
        video_path=video_out,
        thumbnail_path=thumb_out,
        duration=total_duration,
        description=f"Every country compared in memes: {topic}! Created for satirical entertainment.",
        hashtags=["#Shorts", "#Memes", "#Wojak", "#CountryMemes", "#Comedy"],
        provider="wojak_shorts",
    )
