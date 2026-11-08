"""Pure-PIL compositor for country meme frames.

Layout (top to bottom), all sizes derived from the frame size:

- White title bar (``title_bar_frac`` of frame height) with the country name in a
  heavy bold sans, auto-fit to the bar width and centred.
- A 1 px black rule under the title bar.
- A full-width photo below the bar, centre cover-cropped to fill the rest of the frame.
- A character (optional RGBA image) pasted bottom-left at 45% of the frame height.
  It is drawn before the caption box so the box stays on top of it.
- A white rounded caption box, horizontally centred, its top at ``caption_box_y_frac``
  of the frame height. Text is bold black, centred, at most two lines, with the font
  size auto-fit to the largest size (min 28 px) that fits.

Font lookup order: Montserrat-ExtraBold, Arial Bold, DejaVuSans-Bold. If none is found
the PIL default font is used and a warning is logged. No font binaries are bundled.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

RULE_PX = 1
CHARACTER_HEIGHT_FRAC = 0.45
CAPTION_BOX_WIDTH_FRAC = 0.84
CAPTION_BOX_MAX_HEIGHT_FRAC = 0.22
CAPTION_BOX_BOTTOM_MARGIN_FRAC = 0.03
CAPTION_MAX_FONT_FRAC = 0.055  # of frame height
CAPTION_MIN_FONT_PX = 28
CAPTION_MAX_LINES = 2
CAPTION_LINE_HEIGHT = 1.2
CAPTION_PAD_X_FRAC = 0.03  # of frame width
CAPTION_PAD_Y_FRAC = 0.025  # of frame height
CAPTION_RADIUS_FRAC = 0.025  # of the shorter frame side
TITLE_MIN_FONT_PX = 16
TITLE_WIDTH_FRAC = 0.9  # of frame width
TITLE_HEIGHT_FRAC = 0.8  # of title bar height

FONT_CANDIDATES: tuple[str, ...] = (
    "C:/Windows/Fonts/Montserrat-ExtraBold.ttf",
    "C:/Windows/Fonts/Montserrat-ExtraBold.otf",
    os.path.expandvars("%LOCALAPPDATA%/Microsoft/Windows/Fonts/Montserrat-ExtraBold.ttf"),
    "/usr/share/fonts/truetype/montserrat/Montserrat-ExtraBold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

_fallback_warned = False


@dataclass(frozen=True)
class CountryMemeLayout:
    """Resolved geometry and text fit for one frame (pixel coordinates)."""

    width: int
    height: int
    bar_height: int
    rule_y: int
    photo_top: int
    title_text: str
    title_font_px: int
    caption_lines: tuple[str, ...]
    caption_font_px: int
    caption_box: tuple[int, int, int, int] | None
    caption_radius: int
    caption_line_height: int
    font_path: str | None


def _resolve_font_path() -> str | None:
    global _fallback_warned
    for candidate in FONT_CANDIDATES:
        if candidate and os.path.isfile(candidate):
            return candidate
    if not _fallback_warned:
        logger.warning(
            "No bold sans font found (tried %d candidates); using PIL default font",
            len(FONT_CANDIDATES),
        )
        _fallback_warned = True
    return None


@lru_cache(maxsize=256)
def _font(path: str | None, size: int) -> Any:
    if path is not None:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            logger.warning("Could not load font %s; using PIL default font", path)
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):
        return ImageFont.load_default()


def _ink_metrics(font: Any) -> tuple[int, int]:
    """Return (ink top offset, ink height) of a capital letter for vertical centring."""
    left, top, right, bottom = font.getbbox("H")
    return int(top), int(bottom - top)


def _wrap(font: Any, text: str, max_w: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if not current or font.getlength(candidate) <= max_w:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _clip(font: Any, text: str, max_w: float) -> str:
    """Trim text with an ellipsis until it fits in max_w."""
    if font.getlength(text) <= max_w:
        return text
    trimmed = text
    while trimmed and font.getlength(trimmed + "...") > max_w:
        trimmed = trimmed[:-1]
    return trimmed.rstrip() + "..."


def _largest_fitting(lo: int, hi: int, fits: Callable[[int], bool]) -> int | None:
    """Largest size in [lo, hi] for which ``fits`` holds (fit is monotone in size)."""
    if hi < lo or not fits(lo):
        return None
    best = lo
    low, high = lo, hi
    while low <= high:
        mid = (low + high) // 2
        if fits(mid):
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


@lru_cache(maxsize=256)
def _fit_caption(
    text: str, path: str | None, max_w: int, max_h: int, max_size: int
) -> tuple[int, tuple[str, ...]]:
    def lines_at(size: int) -> list[str]:
        return _wrap(_font(path, size), text, max_w)

    def fits(size: int) -> bool:
        font = _font(path, size)
        lines = lines_at(size)
        line_h = math.ceil(size * CAPTION_LINE_HEIGHT)
        return (
            len(lines) <= CAPTION_MAX_LINES
            and all(font.getlength(line) <= max_w for line in lines)
            and len(lines) * line_h <= max_h
        )

    best = _largest_fitting(CAPTION_MIN_FONT_PX, max(CAPTION_MIN_FONT_PX, max_size), fits)
    if best is not None:
        return best, tuple(lines_at(best))

    # Nothing fits at the minimum size: keep the text, but hard-cap at two lines.
    font = _font(path, CAPTION_MIN_FONT_PX)
    lines = lines_at(CAPTION_MIN_FONT_PX)
    if len(lines) > CAPTION_MAX_LINES:
        kept = lines[: CAPTION_MAX_LINES - 1]
        kept.append(" ".join(lines[CAPTION_MAX_LINES - 1 :]))
        lines = kept
    lines = [_clip(font, line, max_w) for line in lines]
    return CAPTION_MIN_FONT_PX, tuple(lines)


@lru_cache(maxsize=256)
def _fit_title(text: str, path: str | None, max_w: int, max_h: int) -> tuple[int, str]:
    def fits(size: int) -> bool:
        font = _font(path, size)
        _, top, _, bottom = font.getbbox(text)
        return font.getlength(text) <= max_w and (bottom - top) <= max_h

    best = _largest_fitting(TITLE_MIN_FONT_PX, max(TITLE_MIN_FONT_PX, max_h), fits)
    if best is not None:
        return best, text
    font = _font(path, TITLE_MIN_FONT_PX)
    return TITLE_MIN_FONT_PX, _clip(font, text, max_w)


def compute_layout(
    size: tuple[int, int],
    country: str,
    caption: str,
    title_bar_frac: float = 0.09,
    caption_box_y_frac: float = 0.56,
) -> CountryMemeLayout:
    """Compute bar, rule, photo and caption geometry plus font fits for a frame."""
    width, height = int(size[0]), int(size[1])
    if width <= 0 or height <= 0:
        raise ValueError(f"frame size must be positive, got {size!r}")
    path = _resolve_font_path()

    bar_h = round(height * title_bar_frac)
    rule_y = bar_h
    photo_top = bar_h + RULE_PX
    title_max_w = round(width * TITLE_WIDTH_FRAC)
    title_max_h = round(bar_h * TITLE_HEIGHT_FRAC)
    title_px, title_text = _fit_title(country.strip(), path, title_max_w, title_max_h)

    caption_text = " ".join(caption.split())
    caption_px = CAPTION_MIN_FONT_PX
    caption_lines: tuple[str, ...] = ()
    caption_box: tuple[int, int, int, int] | None = None
    line_h = math.ceil(caption_px * CAPTION_LINE_HEIGHT)
    radius = round(min(width, height) * CAPTION_RADIUS_FRAC)
    if caption_text:
        box_w = round(width * CAPTION_BOX_WIDTH_FRAC)
        pad_x = round(width * CAPTION_PAD_X_FRAC)
        pad_y = round(height * CAPTION_PAD_Y_FRAC)
        text_max_w = box_w - 2 * pad_x
        text_max_h = round(height * CAPTION_BOX_MAX_HEIGHT_FRAC) - 2 * pad_y
        caption_px, caption_lines = _fit_caption(
            caption_text,
            path,
            text_max_w,
            text_max_h,
            max_size=round(height * CAPTION_MAX_FONT_FRAC),
        )
        line_h = math.ceil(caption_px * CAPTION_LINE_HEIGHT)
        box_h = len(caption_lines) * line_h + 2 * pad_y
        box_x0 = (width - box_w) // 2
        bottom_limit = height - round(height * CAPTION_BOX_BOTTOM_MARGIN_FRAC) - box_h
        box_y0 = min(round(caption_box_y_frac * height), bottom_limit)
        box_y0 = max(box_y0, photo_top)
        caption_box = (box_x0, box_y0, box_x0 + box_w, box_y0 + box_h)

    return CountryMemeLayout(
        width=width,
        height=height,
        bar_height=bar_h,
        rule_y=rule_y,
        photo_top=photo_top,
        title_text=title_text,
        title_font_px=title_px,
        caption_lines=caption_lines,
        caption_font_px=caption_px,
        caption_box=caption_box,
        caption_radius=radius,
        caption_line_height=line_h,
        font_path=path,
    )


@lru_cache(maxsize=16)
def _cover_photo(photo_path: str, width: int, height: int) -> Image.Image:
    """Decode and centre cover-crop a photo to exactly (width, height). Do not mutate."""
    with Image.open(photo_path) as src:
        img = src.convert("RGB")
    scale = max(width / img.width, height / img.height)
    new_w = max(width, round(img.width * scale))
    new_h = max(height, round(img.height * scale))
    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return img.crop((left, top, left + width, top + height))


def _paste_character(canvas: Image.Image, character_img: Image.Image) -> None:
    char = character_img.convert("RGBA")
    target_h = round(canvas.height * CHARACTER_HEIGHT_FRAC)
    target_w = max(1, round(char.width * target_h / char.height))
    char = char.resize((target_w, target_h), Image.Resampling.BILINEAR, reducing_gap=2.0)
    canvas.paste(char, (0, canvas.height - target_h), char)


def render_country_meme_frame(
    size: tuple[int, int],
    country: str,
    photo_path: str | os.PathLike[str],
    caption: str,
    character_img: Image.Image | None = None,
    title_bar_frac: float = 0.09,
    caption_box_y_frac: float = 0.56,
) -> Image.Image:
    """Render one country meme frame as an RGB image of ``size`` (width, height)."""
    layout = compute_layout(size, country, caption, title_bar_frac, caption_box_y_frac)
    width, height = layout.width, layout.height

    canvas = Image.new("RGB", (width, height), WHITE)
    photo = _cover_photo(os.fspath(photo_path), width, height - layout.photo_top)
    canvas.paste(photo, (0, layout.photo_top))

    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, layout.rule_y, width - 1, layout.rule_y + RULE_PX - 1), fill=BLACK)

    if character_img is not None:
        _paste_character(canvas, character_img)
        draw = ImageDraw.Draw(canvas)

    title_font = _font(layout.font_path, layout.title_font_px)
    title_w = title_font.getlength(layout.title_text)
    ink_top, ink_h = _ink_metrics(title_font)
    title_x = (width - title_w) / 2
    title_y = (layout.bar_height - ink_h) // 2 - ink_top
    draw.text((title_x, title_y), layout.title_text, font=title_font, fill=BLACK)

    if layout.caption_box is not None:
        x0, y0, x1, y1 = layout.caption_box
        draw.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=layout.caption_radius, fill=WHITE)
        caption_font = _font(layout.font_path, layout.caption_font_px)
        ink_top, ink_h = _ink_metrics(caption_font)
        pad_y = (y1 - y0 - len(layout.caption_lines) * layout.caption_line_height) // 2
        center_x = (x0 + x1) / 2
        for index, line in enumerate(layout.caption_lines):
            row_top = y0 + pad_y + index * layout.caption_line_height
            line_x = center_x - caption_font.getlength(line) / 2
            line_y = row_top + (layout.caption_line_height - ink_h) // 2 - ink_top
            draw.text((line_x, line_y), line, font=caption_font, fill=BLACK)

    return canvas
