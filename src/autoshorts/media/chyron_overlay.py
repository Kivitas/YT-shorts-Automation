"""Automated broadcast lower-third chyron overlay for vertical 9:16 YouTube Shorts.

Generates high-impact TV broadcast lower-third chyrons (category badge + headline banner)
via SubStation Alpha (.ass) subtitles and direct FFmpeg filtergraph (drawbox + drawtext) chains.

Key features:
- ChyronData dataclass with customizable badges, styles, positioning, and animation
- Automated extraction & sanitization from title, hook, or transcript snippet
- High-contrast broadcast styling presets ('broadcast_red', 'breaking_news', 'minimal_dark',
  'cyber_neon', 'alert_amber')
- Precise vertical Y-positioning tailored to split layouts ('split_50_50', 'split_70_30')
  and full-frame vertical crops ('full_center_crop', 'full_blur_bg')
- Seamless ASS document merging allowing a single subtitles filter to render both captions
  and lower-third broadcast chyrons
- Robust two-level escaping for FFmpeg drawtext / drawbox filter composition
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from autoshorts.media.ass_styler import escape_ass_text, format_ass_timestamp, strip_emojis

# ---------------------------------------------------------------------------
# Style definitions & color palettes
# ---------------------------------------------------------------------------

CHYRON_STYLES: dict[str, dict[str, Any]] = {
    "broadcast_red": {
        "badge_bg": "0xDC2626@1.0",
        "badge_fg": "white@1.0",
        "banner_bg": "0xFFFFFF@0.98",
        "banner_fg": "0x111827@1.0",
        "accent": "0xDC2626@1.0",
        "ass_badge_bg": "&H001414DC&",  # Red BGR
        "ass_badge_fg": "&H00FFFFFF&",  # White
        "ass_banner_bg": "&H00FFFFFF&",  # White
        "ass_banner_fg": "&H001B1811&",  # Dark charcoal BGR
        "ass_accent": "&H001414DC&",
    },
    "breaking_news": {
        "badge_bg": "0xB91C1C@1.0",
        "badge_fg": "0xFEF08A@1.0",
        "banner_bg": "0x0F172A@0.96",
        "banner_fg": "white@1.0",
        "accent": "0xEF4444@1.0",
        "ass_badge_bg": "&H001C1CB9&",  # Crimson BGR
        "ass_badge_fg": "&H008AF0FE&",  # Bright yellow BGR
        "ass_banner_bg": "&H002A170F&",  # Deep navy/slate BGR
        "ass_banner_fg": "&H00FFFFFF&",  # White
        "ass_accent": "&H004444EF&",
    },
    "minimal_dark": {
        "badge_bg": "0x27272A@1.0",
        "badge_fg": "0xF8FAFC@1.0",
        "banner_bg": "0x09090B@0.95",
        "banner_fg": "white@1.0",
        "accent": "0x71717A@1.0",
        "ass_badge_bg": "&H002A2727&",  # Dark zinc BGR
        "ass_badge_fg": "&H00FCFAF8&",  # Soft white
        "ass_banner_bg": "&H000B0909&",  # Dark onyx BGR
        "ass_banner_fg": "&H00FFFFFF&",  # White
        "ass_accent": "&H007A7171&",
    },
    "cyber_neon": {
        "badge_bg": "0x00F0FF@1.0",
        "badge_fg": "black@1.0",
        "banner_bg": "0x0A0A16@0.95",
        "banner_fg": "0x00F0FF@1.0",
        "accent": "0xFF0055@1.0",
        "ass_badge_bg": "&H00FFF000&",  # Cyan BGR
        "ass_badge_fg": "&H00000000&",  # Black
        "ass_banner_bg": "&H00160A0A&",  # Cyber midnight BGR
        "ass_banner_fg": "&H00FFF000&",  # Cyan BGR
        "ass_accent": "&H005500FF&",  # Neon magenta BGR
    },
    "alert_amber": {
        "badge_bg": "0xF59E0B@1.0",
        "badge_fg": "black@1.0",
        "banner_bg": "0x18181B@0.96",
        "banner_fg": "white@1.0",
        "accent": "0xF59E0B@1.0",
        "ass_badge_bg": "&H000B9EF5&",  # Amber BGR
        "ass_badge_fg": "&H00000000&",  # Black
        "ass_banner_bg": "&H001B1818&",  # Onyx BGR
        "ass_banner_fg": "&H00FFFFFF&",  # White
        "ass_accent": "&H000B9EF5&",
    },
}

SUPPORTED_STYLES = tuple(CHYRON_STYLES.keys())
SUPPORTED_POSITIONS = ("seam", "lower_third", "top")
SUPPORTED_ANIMATIONS = ("fade", "none")

# Regular expression to detect news broadcast badges in video titles/hooks
BADGE_PREFIX_RE = re.compile(
    r"^\s*(?:\[\s*(BREAKING\s+NEWS|BREAKING|EXCLUSIVE|SPECIAL\s+REPORT|DEVELOPING\s+STORY|DEVELOPING|LIVE|ALERT|WARNING|JUST\s+IN|UPDATE)\s*\]\s*[:-]?|"
    r"(BREAKING\s+NEWS|BREAKING|EXCLUSIVE|SPECIAL\s+REPORT|DEVELOPING\s+STORY|DEVELOPING|LIVE|ALERT|WARNING|JUST\s+IN|UPDATE)\s*[:-]\s*)",
    re.IGNORECASE,
)

BADGE_NORMALIZATION = {
    "BREAKING": "BREAKING NEWS",
    "BREAKING NEWS": "BREAKING NEWS",
    "DEVELOPING STORY": "DEVELOPING",
    "DEVELOPING": "DEVELOPING",
    "JUST IN": "BREAKING NEWS",
    "WARNING": "ALERT",
    "ALERT": "ALERT",
    "EXCLUSIVE": "EXCLUSIVE",
    "SPECIAL REPORT": "SPECIAL REPORT",
    "LIVE": "LIVE",
    "UPDATE": "SPECIAL REPORT",
}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class ChyronData:
    """Configuration and textual content for a broadcast lower-third chyron overlay."""

    category_badge: str = "BREAKING NEWS"
    headline_text: str = ""
    style: str = "broadcast_red"
    position: str = "seam"
    start_sec: float = 0.0
    duration_sec: float | None = None
    animation: str = "fade"

    def __post_init__(self) -> None:
        """Validate and normalize fields."""
        self.category_badge = (self.category_badge or "BREAKING NEWS").strip().upper()
        self.headline_text = (self.headline_text or "").strip()

        norm_style = (self.style or "broadcast_red").strip().lower()
        self.style = norm_style if norm_style in CHYRON_STYLES else "broadcast_red"

        norm_pos = (self.position or "seam").strip().lower()
        self.position = norm_pos if norm_pos in SUPPORTED_POSITIONS else "seam"

        self.start_sec = max(0.0, float(self.start_sec or 0.0))
        if self.duration_sec is not None:
            self.duration_sec = max(0.1, float(self.duration_sec))

        norm_anim = (self.animation or "fade").strip().lower()
        self.animation = norm_anim if norm_anim in SUPPORTED_ANIMATIONS else "fade"


# ---------------------------------------------------------------------------
# Text Extraction & Formatting
# ---------------------------------------------------------------------------


def _clean_broadcast_text(text: str) -> str:
    """Format and sanitize input text for maximum broadcast impact.

    Strips emojis, clickbait noise tags, quotes, extra punctuation, and uppercases.
    """
    if not text:
        return ""

    # Strip Unicode emojis
    cleaned = strip_emojis(text)

    # Strip hashtags (e.g. #shorts, #viral, #news)
    cleaned = re.sub(r"#\w+", "", cleaned)

    # Strip bracketed clickbait noise (e.g. [MUST SEE], (WATCH TILL END), [SHOCKING])
    cleaned = re.sub(
        r"(?i)\[\s*(must see|must watch|watch|shocking|viral|insane|update|exclusive|breaking|shorts|part \d+|full story)\s*\]",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\(\s*(must see|must watch|watch|shocking|viral|insane|update|exclusive|breaking|shorts|part \d+|watch until the end|watch till end|pov|full story)\s*\)",
        "",
        cleaned,
    )

    # Strip leading hook noise (e.g. POV:, WAIT FOR IT..., MUST WATCH:)
    cleaned = re.sub(
        r"(?i)^\s*(pov|watch until the end|wait for it|wait till the end|must watch|did you know that)[\s:!.\-]+",
        "",
        cleaned,
    )

    # Clean double quotes and smart quotes
    cleaned = cleaned.replace('"', "").replace("“", "").replace("”", "").replace("„", "")

    # Clean redundant punctuation at end/start
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip(" -:,;!?")

    return cleaned.upper()


def _truncate_or_wrap(text: str, max_chars: int = 65, wrap_two_lines: bool = False) -> str:
    """Intelligently truncate or wrap headline to fit horizontal width.

    If wrap_two_lines is True and text exceeds ~40 chars, wraps into two punchy lines
    separated by newline. Otherwise truncates cleanly at word boundary.
    """
    if not text or len(text) <= max_chars:
        return text

    if wrap_two_lines:
        words = text.split()
        midpoint = len(text) // 2
        line1_words: list[str] = []
        line2_words: list[str] = []
        current_len = 0

        for w in words:
            if current_len < midpoint or not line1_words:
                line1_words.append(w)
                current_len += len(w) + 1
            else:
                line2_words.append(w)

        line1 = " ".join(line1_words)
        line2 = " ".join(line2_words)

        # Truncate line2 if it still overflows
        max_line2 = max(24, max_chars - len(line1) + 10)
        if len(line2) > max_line2:
            sub = line2[: max_line2 - 3].rsplit(" ", 1)[0]
            line2 = (sub or line2[: max_line2 - 3]) + "..."

        return f"{line1}\n{line2}"

    # Single-line truncation at word boundary
    truncated = text[: max_chars - 3]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated.rstrip(" -:,;") + "..."


def derive_automated_chyron(
    title: str | None = None,
    hook_text: str | None = None,
    transcript_snippet: str | None = None,
    default_badge: str = "BREAKING NEWS",
    max_chars: int = 65,
    style: str = "broadcast_red",
    position: str = "seam",
    start_sec: float = 0.0,
    duration_sec: float | None = None,
    animation: str = "fade",
    wrap_two_lines: bool = False,
) -> ChyronData:
    """Derive an automated ChyronData instance from video title, hook, or transcript.

    Selects the best available candidate string, detects any broadcast category badge
    prefix (e.g. 'BREAKING:', 'EXCLUSIVE:'), cleans clickbait and emoji clutter,
    uppercases, and formats text to fit within standard 1080px lower-third bounds.
    """
    raw_source = (
        (title or "").strip() or (hook_text or "").strip() or (transcript_snippet or "").strip()
    )
    if not raw_source:
        return ChyronData(
            category_badge=default_badge.strip().upper(),
            headline_text="SPECIAL REPORT: LATEST DEVELOPMENTS",
            style=style,
            position=position,
            start_sec=start_sec,
            duration_sec=duration_sec,
            animation=animation,
        )

    # Check for category badge prefix in the source
    badge = default_badge.strip().upper()
    badge_match = BADGE_PREFIX_RE.match(raw_source)
    cleaned_source = raw_source
    if badge_match:
        matched_badge = (badge_match.group(1) or badge_match.group(2) or "").upper()
        # Collapse whitespace inside matched badge name (e.g. "BREAKING  NEWS")
        matched_badge = " ".join(matched_badge.split())
        badge = BADGE_NORMALIZATION.get(matched_badge, matched_badge)
        cleaned_source = raw_source[badge_match.end() :]

    # Clean text for broadcast presentation
    formatted = _clean_broadcast_text(cleaned_source)
    if not formatted:
        formatted = _clean_broadcast_text(raw_source) or "SPECIAL REPORT"

    headline = _truncate_or_wrap(formatted, max_chars=max_chars, wrap_two_lines=wrap_two_lines)

    return ChyronData(
        category_badge=badge,
        headline_text=headline,
        style=style,
        position=position,
        start_sec=start_sec,
        duration_sec=duration_sec,
        animation=animation,
    )


# ---------------------------------------------------------------------------
# Coordinate Calculations
# ---------------------------------------------------------------------------


def calculate_chyron_y(
    position: str = "seam",
    layout_mode: str = "split_50_50",
    height: int = 1920,
    banner_height: int = 72,
    seam_y: int | None = None,
) -> tuple[int, int]:
    """Calculate vertical (banner_top_y, badge_bottom_y) for chyron placement.

    Positions accurately across layout modes:
    - 'split_50_50' (seam): sits right above seam_y=960 (around Y=850-950)
    - 'split_70_30' (seam): sits right above seam_y=1344 (around Y=1240-1335)
    - 'lower_third' or full-frame crop: sits at standard lower-third (around Y=1450)
    - 'top': sits near top safe area (around Y=220)

    ``seam_y`` is the real seam row for layouts whose split is not at 50% or 70% (cam_gameplay
    with a custom top_ratio); when given, a seam-positioned chyron sits right above it.

    Returns:
        (banner_top_y, badge_bottom_y)
    """
    sy = height / 1920.0
    pos = (position or "seam").strip().lower()
    mode = (layout_mode or "split_50_50").strip().lower()

    if pos == "top":
        banner_y = int(220 * sy)
    elif pos == "lower_third" or mode in ("full_center_crop", "full_blur_bg", "full", "single"):
        banner_y = int(1450 * sy)
    elif pos == "seam":
        if seam_y is not None:
            banner_y = max(0, int(seam_y) - banner_height - int(14 * sy))
        elif mode == "split_70_30":
            seam_y = int(height * 0.70)  # 1344 for 1920
            banner_y = seam_y - banner_height - int(12 * sy)
        else:
            # Default split_50_50
            seam_y = height // 2  # 960 for 1920
            banner_y = seam_y - banner_height - int(14 * sy)
    else:
        banner_y = int(1450 * sy)

    # Badge sits right atop the headline banner
    badge_y = banner_y - int(4 * sy)
    return banner_y, badge_y


# ---------------------------------------------------------------------------
# ASS Subtitle Overlay Generator
# ---------------------------------------------------------------------------


def build_chyron_ass(
    chyron: ChyronData,
    total_duration_sec: float,
    width: int = 1080,
    height: int = 1920,
    layout_mode: str = "split_50_50",
    seam_y: int | None = None,
) -> str:
    """Generate a complete ASS document with styled category badge and headline banner.

    Uses BorderStyle=3 (opaque backing box) for both the badge and headline banner,
    creating a high-contrast news lower-third card with accurate Y positioning.
    """
    style_def = CHYRON_STYLES.get(chyron.style, CHYRON_STYLES["broadcast_red"])
    sx = width / 1080.0
    sy = height / 1920.0

    margin_x = int(60 * sx)

    # Check line count for height and font sizing
    text_lines = [
        escape_ass_text(ln.strip()) for ln in chyron.headline_text.splitlines() if ln.strip()
    ]
    num_lines = max(1, len(text_lines))
    banner_h = int((112 if num_lines > 1 else 72) * sy)

    banner_y, badge_y = calculate_chyron_y(
        position=chyron.position,
        layout_mode=layout_mode,
        height=height,
        banner_height=banner_h,
        seam_y=seam_y,
    )

    # Timing calculation
    start_sec = max(0.0, chyron.start_sec)
    if chyron.duration_sec is not None:
        end_sec = min(total_duration_sec, start_sec + chyron.duration_sec)
    else:
        end_sec = total_duration_sec
    end_sec = max(start_sec + 0.1, end_sec)

    start_ts = format_ass_timestamp(start_sec)
    end_ts = format_ass_timestamp(end_sec)

    fade_tag = r"\fad(200,200)" if chyron.animation == "fade" else ""

    badge_fontsize = max(20, int(30 * sx))
    headline_fontsize = max(24, int((34 if num_lines > 1 else 40) * sx))

    # Format headline with ASS \N line breaks
    formatted_headline = r"\N".join(text_lines) if text_lines else "SPECIAL REPORT"
    clean_badge = escape_ass_text(chyron.category_badge)

    lines: list[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            f"Style: ChyronBadge,Montserrat ExtraBold,{badge_fontsize},"
            f"{style_def['ass_badge_fg']},&H000000FF&,{style_def['ass_badge_bg']},"
            f"&H00000000&,-1,0,0,0,100,100,1,0,3,8,0,1,{margin_x},{margin_x},0,1"
        ),
        (
            f"Style: ChyronHeadline,Montserrat ExtraBold,{headline_fontsize},"
            f"{style_def['ass_banner_fg']},&H00000000&,{style_def['ass_banner_bg']},"
            f"&H80000000&,-1,0,0,0,100,100,1,0,3,12,0,7,{margin_x},{margin_x},0,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        f"Dialogue: 2,{start_ts},{end_ts},ChyronBadge,,0,0,0,,{{\\an1\\pos({margin_x},{badge_y}){fade_tag}}}{clean_badge}",
        f"Dialogue: 2,{start_ts},{end_ts},ChyronHeadline,,0,0,0,,{{\\an7\\pos({margin_x},{banner_y}){fade_tag}}}{formatted_headline}",
    ]

    return "\n".join(lines) + "\n"


def merge_ass_documents(base: str, overlay: str) -> str:
    """Add overlay styles and dialogue events to a base ASS document without duplication."""
    base_lines = base.rstrip("\n").splitlines()
    overlay_lines = overlay.splitlines()

    events_idx = next((i for i, line in enumerate(base_lines) if line.strip() == "[Events]"), None)
    if events_idx is None:
        raise ValueError("base ASS document has no [Events] section")

    existing_style_names = {
        line.split(",")[0].replace("Style:", "").strip()
        for line in base_lines
        if line.startswith("Style:")
    }

    styles: list[str] = []
    for line in overlay_lines:
        if line.startswith("Style:"):
            s_name = line.split(",")[0].replace("Style:", "").strip()
            if s_name not in existing_style_names:
                styles.append(line)
                existing_style_names.add(s_name)

    dialogues = [line for line in overlay_lines if line.startswith("Dialogue:")]
    merged = base_lines[:events_idx] + styles + [""] + base_lines[events_idx:] + dialogues
    return "\n".join(merged) + "\n"


def merge_chyron_into_ass(
    base_ass_text: str,
    chyron: ChyronData,
    total_duration_sec: float,
    width: int = 1080,
    height: int = 1920,
    layout_mode: str = "split_50_50",
    seam_y: int | None = None,
) -> str:
    """Merge chyron overlay styles and dialogues directly into an existing caption ASS document."""
    overlay = build_chyron_ass(
        chyron=chyron,
        total_duration_sec=total_duration_sec,
        width=width,
        height=height,
        layout_mode=layout_mode,
        seam_y=seam_y,
    )
    return merge_ass_documents(base_ass_text, overlay)


_CHYRON_STYLE_NAMES = ("ChyronBadge", "ChyronHeadline")
_CHYRON_POS_RE = re.compile(r"(\\pos\(\s*)(-?\d+)(\s*,\s*)(-?\d+)(\s*\))")


def _is_chyron_event(line: str) -> bool:
    return line.startswith("Dialogue:") and any(f",{name}," in line for name in _CHYRON_STYLE_NAMES)


def has_chyron_events(ass_text: str) -> bool:
    """True when an ASS document already carries the chyron badge/headline Dialogue events."""
    return any(_is_chyron_event(line) for line in ass_text.splitlines())


def retarget_chyron_ass(ass_text: str, delta_y: int) -> str:
    """Shift the chyron events' pos rows by ``delta_y`` px (other events are untouched).

    Used when the ASS was generated for a 50/50 seam but the final layout's seam sits elsewhere
    (cam_gameplay with a custom top_ratio).
    """
    if not delta_y:
        return ass_text

    def _shift(match: re.Match[str]) -> str:
        y = max(0, int(match.group(4)) + delta_y)
        return f"{match.group(1)}{match.group(2)}{match.group(3)}{y}{match.group(5)}"

    out: list[str] = []
    for line in ass_text.split("\n"):
        if _is_chyron_event(line):
            line = _CHYRON_POS_RE.sub(_shift, line, count=1)
        out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# FFmpeg Filtergraph Generator
# ---------------------------------------------------------------------------


def escape_ffmpeg_drawtext(text: str) -> str:
    """Apply two-level unquoted escaping for FFmpeg drawtext parameters.

    Option level: backslash, apostrophe, colon.
    Filtergraph level: backslash, apostrophe, brackets, comma, semicolon.
    expansion=none makes '%' literal.
    """
    bs = "\\"
    escaped = text.replace(bs, bs * 2).replace("'", bs + "'").replace(":", bs + ":")
    escaped = escaped.replace(bs, bs * 2).replace("'", bs + "'")
    for ch in "[],;":
        escaped = escaped.replace(ch, bs + ch)
    return escaped


def build_chyron_filter(
    chyron: ChyronData,
    input_label: str = "stacked",
    output_label: str = "with_chyron",
    width: int = 1080,
    height: int = 1920,
    layout_mode: str = "split_50_50",
    duration_sec: float = 30.0,
    fontfile: str | None = None,
    seam_y: int | None = None,
) -> tuple[str, str]:
    """Generate an FFmpeg drawbox + drawtext filtergraph chain for broadcast chyron overlay.

    Uses two-level escaped parameters and optional enable windowing.

    Returns:
        (filtergraph_chain_string, output_label)
    """
    if not chyron.headline_text and not chyron.category_badge:
        return f"[{input_label}]copy[{output_label}]", output_label

    style_def = CHYRON_STYLES.get(chyron.style, CHYRON_STYLES["broadcast_red"])
    sx = width / 1080.0
    sy = height / 1920.0

    margin_x = int(60 * sx)
    banner_w = width - (2 * margin_x)

    raw_lines = [ln.strip() for ln in chyron.headline_text.splitlines() if ln.strip()]
    num_lines = max(1, len(raw_lines))
    banner_h = int((112 if num_lines > 1 else 72) * sy)

    banner_y, _ = calculate_chyron_y(
        position=chyron.position,
        layout_mode=layout_mode,
        height=height,
        banner_height=banner_h,
        seam_y=seam_y,
    )

    badge_h = int(40 * sy)
    badge_y = banner_y - badge_h

    # Approximate badge box width based on text length with safety padding
    badge_text_len = len(chyron.category_badge)
    badge_w = min(banner_w, max(int(180 * sx), int((badge_text_len * 15 + 40) * sx)))

    # Timing / enable expression
    start_sec = max(0.0, chyron.start_sec)
    if chyron.duration_sec is not None:
        end_sec = min(duration_sec, start_sec + chyron.duration_sec)
    else:
        end_sec = duration_sec
    end_sec = max(start_sec + 0.1, end_sec)

    enable_clause = f":enable='between(t,{start_sec:.2f},{end_sec:.2f})'"

    # Font sizes
    badge_fontsize = max(18, int(24 * sx))
    headline_fontsize = max(20, int((32 if num_lines > 1 else 36) * sx))

    font_arg = f":fontfile={fontfile}" if fontfile else ""

    filters: list[str] = []

    # 1. Badge background box
    filters.append(
        f"drawbox=x={margin_x}:y={badge_y}:w={badge_w}:h={badge_h}:"
        f"color={style_def['badge_bg']}:t=fill{enable_clause}"
    )

    # 2. Main headline banner background box
    filters.append(
        f"drawbox=x={margin_x}:y={banner_y}:w={banner_w}:h={banner_h}:"
        f"color={style_def['banner_bg']}:t=fill{enable_clause}"
    )

    # 3. Left accent color stripe on headline banner
    accent_w = max(4, int(8 * sx))
    filters.append(
        f"drawbox=x={margin_x}:y={banner_y}:w={accent_w}:h={banner_h}:"
        f"color={style_def['accent']}:t=fill{enable_clause}"
    )

    # 4. Badge text
    escaped_badge = escape_ffmpeg_drawtext(chyron.category_badge)
    badge_tx = margin_x + int(14 * sx)
    badge_ty = badge_y + max(2, int((badge_h - badge_fontsize) / 2))
    filters.append(
        f"drawtext=text={escaped_badge}:expansion=none{font_arg}:"
        f"fontcolor={style_def['badge_fg']}:fontsize={badge_fontsize}:"
        f"x={badge_tx}:y={badge_ty}{enable_clause}"
    )

    # 5. Headline text (supports 1 or 2 lines)
    headline_tx = margin_x + accent_w + int(16 * sx)
    if num_lines == 1:
        escaped_head = escape_ffmpeg_drawtext(raw_lines[0] if raw_lines else "SPECIAL REPORT")
        head_ty = banner_y + max(4, int((banner_h - headline_fontsize) / 2))
        filters.append(
            f"drawtext=text={escaped_head}:expansion=none{font_arg}:"
            f"fontcolor={style_def['banner_fg']}:fontsize={headline_fontsize}:"
            f"x={headline_tx}:y={head_ty}{enable_clause}"
        )
    else:
        line_gap = int(6 * sy)
        total_text_h = (num_lines * headline_fontsize) + ((num_lines - 1) * line_gap)
        start_head_y = banner_y + max(4, int((banner_h - total_text_h) / 2))
        for i, line_str in enumerate(raw_lines):
            escaped_ln = escape_ffmpeg_drawtext(line_str)
            curr_ty = start_head_y + (i * (headline_fontsize + line_gap))
            filters.append(
                f"drawtext=text={escaped_ln}:expansion=none{font_arg}:"
                f"fontcolor={style_def['banner_fg']}:fontsize={headline_fontsize}:"
                f"x={headline_tx}:y={curr_ty}{enable_clause}"
            )

    filter_chain = ",".join(filters)
    full_filter = f"[{input_label}]{filter_chain}[{output_label}]"
    return full_filter, output_label


__all__ = [
    "BADGE_NORMALIZATION",
    "BADGE_PREFIX_RE",
    "CHYRON_STYLES",
    "SUPPORTED_ANIMATIONS",
    "SUPPORTED_POSITIONS",
    "SUPPORTED_STYLES",
    "ChyronData",
    "build_chyron_ass",
    "build_chyron_filter",
    "calculate_chyron_y",
    "derive_automated_chyron",
    "escape_ffmpeg_drawtext",
    "has_chyron_events",
    "merge_ass_documents",
    "merge_chyron_into_ass",
    "retarget_chyron_ass",
]
