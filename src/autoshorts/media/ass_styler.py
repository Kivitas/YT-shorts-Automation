"""High-Dopamine ASS Subtitle Generator with Word-Level Karaoke Pop Animations.

Produces SubStation Alpha (.ass) scripts formatted for 1080x1920 vertical Shorts:
- 2-4 word bursts (word chunker)
- Word-level active karaoke highlight with \\t scaling animation (100% -> 115% -> 100%)
- 7 Built-in Studio Presets (neon, minimal, pill, hormozi, mrbeast, cyberpunk, cinematic)
- Typography controls: text transforms (uppercase, title, none), custom hex colors
- Safe-zone positioning avoiding YouTube Shorts UI chrome (center, bottom_safe, top_safe)
- Hook title banner styling (neon_box, pill, minimal, none)
- Emoji rendering toggle and Segoe UI Emoji font wrapper
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoshorts.core.config import SubtitlesConfig
from autoshorts.core.models import JumpCut
from autoshorts.core.paths import atomic_write


@dataclass
class TimedWord:
    """A single word with speech recognition start and end timestamps."""

    word: str
    start: float
    end: float
    probability: float = 1.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimedWord:
        """Create TimedWord from dictionary representation."""
        return cls(
            word=str(data.get("word", "")).strip(),
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            probability=float(data.get("probability", 1.0)),
        )


@dataclass
class WordChunk:
    """A burst of 2-4 timed words displayed together as a single subtitle unit."""

    words: list[TimedWord] = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0

    @property
    def text(self) -> str:
        """Return the concatenated plain text of the chunk."""
        return " ".join(w.word for w in self.words)


# Prominent keywords that receive dynamic color/emphasis pop
EMPHASIS_KEYWORDS = {
    "SECRET",
    "INSANE",
    "CRAZY",
    "NEVER",
    "ALWAYS",
    "SHOCKING",
    "MILLION",
    "BILLION",
    "TRUTH",
    "HACK",
    "STOP",
    "WARN",
    "DANGER",
    "MONEY",
    "HOW",
    "WHY",
    "NOW",
    "HUGE",
    "BEST",
    "WORST",
    "ERROR",
    "MISTAKE",
    "EXPOSED",
    "MAGIC",
    "POWER",
    "GOD",
    "DEAD",
}

EMOJI_REGEX = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\ud83c-\ud83e][\ud000-\udfff]",
    flags=re.UNICODE,
)


def hex_to_ass_color(color_str: str) -> str:
    """Convert hex color (#RRGGBB, #AARRGGBB, #RGB) to ASS color format (&HAABBGGRR / &H00BBGGRR).

    If color_str starts with '#' (e.g. '#FFE600'), converts '#RRGGBB' to '&H00BBGGRR'.
    If already starts with '&H' or '&h', returns as-is.
    """
    if not color_str:
        return "&H00FFFFFF"
    s = color_str.strip()
    if s.upper().startswith("&H"):
        return s.upper() if s.startswith("&h") else s
    if s.startswith("#"):
        hex_val = s.lstrip("#")
        if len(hex_val) == 6:
            r = hex_val[0:2]
            g = hex_val[2:4]
            b = hex_val[4:6]
            return f"&H00{b.upper()}{g.upper()}{r.upper()}"
        elif len(hex_val) == 8:
            r = hex_val[0:2]
            g = hex_val[2:4]
            b = hex_val[4:6]
            a = hex_val[6:8]
            return f"&H{a.upper()}{b.upper()}{g.upper()}{r.upper()}"
        elif len(hex_val) == 3:
            r = hex_val[0] * 2
            g = hex_val[1] * 2
            b = hex_val[2] * 2
            return f"&H00{b.upper()}{g.upper()}{r.upper()}"
    return s


def escape_ass_text(text: str) -> str:
    """Make arbitrary speech text safe for an ASS Dialogue Text field.

    '{' and '}' delimit override blocks and a backslash starts an override code such as
    \\N or \\h, so an unmatched brace would swallow the rest of the line. They are replaced
    with look-alike characters, and line breaks are flattened to spaces. Commas need no
    escaping because Text is the last field of a Dialogue line.
    """
    cleaned = text.replace("\\", "＼").replace("{", "(").replace("}", ")")
    return cleaned.replace("\r", " ").replace("\n", " ")


def strip_emojis(text: str) -> str:
    """Remove Unicode emojis from text cleanly."""
    cleaned = EMOJI_REGEX.sub("", text)
    return re.sub(r" +", " ", cleaned).strip()


# 7 Distinct Caption Style Presets per PLAN.md & visual polish specification
CAPTION_PRESETS: dict[str, dict[str, Any]] = {
    "neon": {
        "font_name": "Montserrat ExtraBold",
        "font_size": 76,
        "active_color": "&H0000E6FF",  # Yellow #FFE600 (BGR)
        "emphasis_color": "&H00FFF000",  # Cyan #00F0FF (BGR)
        "primary_color": "&H00FFFFFF",  # Crisp White
        "outline_color": "&H00000000",  # Heavy black outline
        "back_color": "&H80000000",
        "border_style": 1,
        "outline": 8,  # Heavy black outline
        "shadow": 4,
    },
    "minimal": {
        "font_name": "Inter SemiBold",
        "font_size": 64,
        "active_color": "&H0066FF00",  # Emerald #00FF66 (BGR)
        "emphasis_color": "&H0066FF00",  # Emerald #00FF66 (BGR)
        "primary_color": "&H00FFFFFF",  # Crisp White
        "outline_color": "&H00000000",
        "back_color": "&H40000000",
        "border_style": 1,
        "outline": 2,  # Subtle outline
        "shadow": 1,
    },
    "pill": {
        "font_name": "Montserrat Bold",
        "font_size": 68,
        "active_color": "&H0000E6FF",  # Yellow #FFE600 (BGR)
        "emphasis_color": "&H00FFF000",  # Cyan #00F0FF (BGR)
        "primary_color": "&H00FFFFFF",  # Crisp White
        "outline_color": "&HA0000000",  # Translucent dark pill box
        "back_color": "&HA0000000",
        "border_style": 3,  # BorderStyle=3 opaque backing box behind words
        "outline": 6,  # Box padding
        "shadow": 0,
    },
    "hormozi": {
        "font_name": "Impact",
        "font_size": 86,
        "active_color": "&H0055FF00",  # Active Neon Green #00FF55 (BGR)
        "emphasis_color": "&H0000F0FF",  # Emphasis Bright Yellow #FFF000 (BGR)
        "primary_color": "&H00FFFFFF",  # Crisp White
        "outline_color": "&H00000000",  # Heavy 12px Black outline
        "back_color": "&H80000000",
        "border_style": 1,
        "outline": 12,
        "shadow": 5,
    },
    "mrbeast": {
        "font_name": "Montserrat ExtraBold",
        "font_size": 82,
        "active_color": "&H0000B8FF",  # Active Vivid Orange #FFB800 (BGR)
        "emphasis_color": "&H0077FF00",  # Emphasis Lime #00FF77 (BGR)
        "primary_color": "&H00FFFFFF",  # Crisp White
        "outline_color": "&H00000000",  # 10px Black stroke
        "back_color": "&H80000000",
        "border_style": 1,
        "outline": 10,
        "shadow": 4,
    },
    "cyberpunk": {
        "font_name": "Orbitron",
        "font_size": 74,
        "active_color": "&H007F00FF",  # Active Magenta #FF007F (BGR)
        "emphasis_color": "&H0000FFFF",  # Emphasis Acid Yellow #FFFF00 (BGR)
        "primary_color": "&H00FFF000",  # Cyan #00F0FF (BGR)
        "outline_color": "&H00330011",  # 8px Dark Violet outline
        "back_color": "&H80000000",
        "border_style": 1,
        "outline": 8,
        "shadow": 6,
    },
    "cinematic": {
        "font_name": "Georgia",
        "font_size": 58,
        "active_color": "&H0037AFD4",  # Active Gold #D4AF37 (BGR)
        "emphasis_color": "&H002B91C4",  # Emphasis Warm Ochre #C4912B (BGR)
        "primary_color": "&H00F0F5F5",  # Off-White
        "outline_color": "&H00141E28",  # 2px Umber outline
        "back_color": "&H40000000",
        "border_style": 1,
        "outline": 2,
        "shadow": 3,
    },
    "rapid": {
        "font_name": "Montserrat ExtraBold",
        "font_size": 84,
        "active_color": "&H0000FFFF",  # Neon Electric Yellow #FFFF00 (BGR)
        "emphasis_color": "&H0000FF66",  # Intense Spring Green #66FF00 (BGR)
        "primary_color": "&H00FFFFFF",  # Crisp White
        "outline_color": "&H00000000",  # 10px Deep Black stroke
        "back_color": "&H80000000",
        "border_style": 1,
        "outline": 10,
        "shadow": 4,
    },
}


def format_emojis_for_ass(text: str, base_font: str = "Montserrat ExtraBold") -> str:
    """Safely format raw Unicode emojis by wrapping with inline Segoe UI Emoji font override on Windows.

    Prevents raw emojis from rendering as missing glyph tofu boxes in standard TTF fonts.
    """
    if not EMOJI_REGEX.search(text):
        return text

    def _replace_emoji(match: re.Match[str]) -> str:
        emoji_str = match.group(0)
        return f"{{\\fnSegoe UI Emoji}}{emoji_str}{{\\fn{base_font}}}"

    return EMOJI_REGEX.sub(_replace_emoji, text)


def format_ass_timestamp(seconds: float) -> str:
    """Format floating-point seconds into ASS timestamp format: H:MM:SS.cs (centiseconds)."""
    seconds = max(seconds, 0.0)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = round((seconds - int(seconds)) * 100)
    if centis >= 100:
        secs += 1
        centis = 0
        if secs >= 60:
            minutes += 1
            secs = 0
            if minutes >= 60:
                hours += 1
                minutes = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def is_emphasis_word(word_clean: str) -> bool:
    """Return True if word is considered high-emphasis (all-caps keyword, emoji, etc.)."""
    upper_stripped = re.sub(r"[^\w]", "", word_clean).upper()
    if upper_stripped in EMPHASIS_KEYWORDS:
        return True
    if word_clean.endswith("!"):
        return True
    return bool(EMOJI_REGEX.search(word_clean))


def chunk_words(
    words: Sequence[TimedWord | dict[str, Any]],
    words_per_burst: int = 3,
    min_words: int = 2,
    max_words: int = 4,
    max_gap_sec: float = 0.6,
) -> list[WordChunk]:
    """Group word-level timestamps into readable bursts of 2-4 words.

    Snaps chunks at sentence/clause punctuation (., ?, !, :, ;) or when speech
    pauses exceed max_gap_sec.
    """
    normalized_words: list[TimedWord] = []
    for item in words:
        if isinstance(item, TimedWord):
            w = item
        else:
            w = TimedWord.from_dict(item)
        if w.word:
            normalized_words.append(w)

    if not normalized_words:
        return []

    chunks: list[WordChunk] = []
    current_chunk_words: list[TimedWord] = []

    for i, word in enumerate(normalized_words):
        current_chunk_words.append(word)
        word_text = word.word.strip()

        # Check if punctuation signals a sentence/clause boundary
        ends_with_terminal = word_text.endswith((".", "?", "!"))
        ends_with_clause = word_text.endswith((",", ";", ":"))

        # Check for pause gap between current and next word
        has_large_gap = False
        if i + 1 < len(normalized_words):
            next_start = normalized_words[i + 1].start
            if (next_start - word.end) >= max_gap_sec:
                has_large_gap = True

        count = len(current_chunk_words)

        should_split = False
        if (
            count >= max_words
            or count >= min_words
            and (ends_with_terminal or ends_with_clause or has_large_gap)
        ):
            should_split = True
        elif count >= words_per_burst and not (ends_with_terminal or ends_with_clause):
            # Normal burst size reached
            should_split = True

        if should_split or (i == len(normalized_words) - 1):
            chunks.append(
                WordChunk(
                    words=current_chunk_words,
                    start=current_chunk_words[0].start,
                    end=current_chunk_words[-1].end,
                )
            )
            current_chunk_words = []

    return chunks


def offset_words_for_hook(
    words: Sequence[TimedWord | dict[str, Any]],
    hook_in_sec: float,
) -> list[TimedWord]:
    """Offset subtitle word timestamps when hook_in trimming is applied.

    Words ending at or before hook_in_sec are discarded.
    Words overlapping hook_in_sec are clamped to start at 0.0.
    Subsequent words are shifted earlier by hook_in_sec.
    """
    if hook_in_sec <= 0.0:
        res: list[TimedWord] = []
        for w in words:
            if isinstance(w, TimedWord):
                res.append(w)
            else:
                res.append(TimedWord.from_dict(w))
        return res

    offset_words: list[TimedWord] = []
    for item in words:
        if isinstance(item, TimedWord):
            w = item
        else:
            w = TimedWord.from_dict(item)

        if w.end <= hook_in_sec:
            continue

        new_start = max(0.0, w.start - hook_in_sec)
        new_end = max(new_start + 0.05, w.end - hook_in_sec)
        offset_words.append(
            TimedWord(
                word=w.word,
                start=round(new_start, 3),
                end=round(new_end, 3),
                probability=w.probability,
            )
        )
    return offset_words


def shift_jump_cuts_for_hook(
    jump_cuts: Sequence[JumpCut] | None,
    hook_in_sec: float,
) -> list[JumpCut]:
    """Express clip-timeline jump cuts on the hook-trimmed timeline.

    The EDL stores cuts in source-clip time (between hook_in and hook_out), but the render trims
    the input with ``-ss hook_in`` first, so filters, captions and the Remotion plan all work on
    a timeline that starts at hook_in. Cuts entirely before hook_in vanish; one straddling it is
    clamped to start at 0.
    """
    if not jump_cuts:
        return []
    if hook_in_sec <= 0.0:
        return list(jump_cuts)
    shifted: list[JumpCut] = []
    for c in jump_cuts:
        new_end = c.cut_end - hook_in_sec
        if new_end <= 0.0:
            continue
        new_start = max(0.0, c.cut_start - hook_in_sec)
        if new_end > new_start:
            shifted.append(
                JumpCut(
                    cut_start=round(new_start, 3),
                    cut_end=round(new_end, 3),
                    reason=getattr(c, "reason", "") or "",
                )
            )
    return shifted


def remap_words_for_jump_cuts(
    words: Sequence[TimedWord | dict[str, Any]],
    jump_cuts: Sequence[JumpCut] | None = None,
    fps: float | None = None,
) -> list[TimedWord]:
    """Remap word timestamps when jump cuts excise dead air intervals.

    Removes words that fall inside a jump cut ([cut_start, cut_end]).
    Shifts all subsequent word timestamps earlier by the cumulative duration of preceding jump cuts.
    Optionally quantizes timestamps to frame boundaries when fps is provided.
    """
    if not words:
        return []
    if not jump_cuts:
        res: list[TimedWord] = []
        for w in words:
            if isinstance(w, TimedWord):
                res.append(w)
            else:
                res.append(TimedWord.from_dict(w))
        if fps is not None and fps > 0:
            frame_dur = 1.0 / fps
            return [
                TimedWord(
                    word=w.word,
                    start=round(round(w.start / frame_dur) * frame_dur, 3),
                    end=round(
                        max(
                            round(w.start / frame_dur) * frame_dur + frame_dur,
                            round(w.end / frame_dur) * frame_dur,
                        ),
                        3,
                    ),
                    probability=w.probability,
                )
                for w in res
            ]
        return res

    sorted_cuts = sorted(
        [c for c in jump_cuts if c.cut_end > c.cut_start >= 0],
        key=lambda c: c.cut_start,
    )
    if not sorted_cuts:
        res = [w if isinstance(w, TimedWord) else TimedWord.from_dict(w) for w in words]
        if fps is not None and fps > 0:
            frame_dur = 1.0 / fps
            return [
                TimedWord(
                    word=w.word,
                    start=round(round(w.start / frame_dur) * frame_dur, 3),
                    end=round(
                        max(
                            round(w.start / frame_dur) * frame_dur + frame_dur,
                            round(w.end / frame_dur) * frame_dur,
                        ),
                        3,
                    ),
                    probability=w.probability,
                )
                for w in res
            ]
        return res

    remapped: list[TimedWord] = []
    for item in words:
        w = item if isinstance(item, TimedWord) else TimedWord.from_dict(item)

        midpoint = (w.start + w.end) / 2.0
        inside_cut = any(
            (c.cut_start <= midpoint <= c.cut_end)
            or (w.start >= c.cut_start and w.end <= c.cut_end)
            for c in sorted_cuts
        )
        if inside_cut:
            continue

        cum_shift = 0.0
        w_start = w.start
        w_end = w.end
        for c in sorted_cuts:
            cut_dur = max(0.0, c.cut_end - c.cut_start)
            if c.cut_end <= w_start:
                cum_shift += cut_dur
            elif c.cut_start < w_start < c.cut_end:
                # Kept part starts at cut_end, so the whole cut is removed before it.
                cum_shift += cut_dur
                w_start = c.cut_end
            elif c.cut_start < w_end < c.cut_end:
                w_end = c.cut_start

        new_start = max(0.0, w_start - cum_shift)
        new_end = max(new_start + 0.05, w_end - cum_shift)

        if fps is not None and fps > 0:
            frame_dur = 1.0 / fps
            q_start = round(round(new_start / frame_dur) * frame_dur, 3)
            q_end = round(max(q_start + frame_dur, round(new_end / frame_dur) * frame_dur), 3)
            new_start = q_start
            new_end = q_end
        else:
            new_start = round(new_start, 3)
            new_end = round(new_end, 3)

        remapped.append(
            TimedWord(
                word=w.word,
                start=new_start,
                end=new_end,
                probability=w.probability,
            )
        )

    return remapped


def build_spring_bounce_tags(
    scale_overshoot: int = 125,
    scale_rebound: int = 96,
    t_rise_ms: int = 70,
    t_rebound_ms: int = 130,
    t_settle_ms: int = 180,
    rot_tilt: int = -2,
) -> str:
    """Generate ASS override tags for spring-physics overshoot bounce animation.

    Phases:
    1. Rise (0 -> t_rise_ms): Fast scale pop overshoot past target.
    2. Rebound (t_rise_ms -> t_rebound_ms): Spring recoil dip below 100%.
    3. Settle (t_rebound_ms -> t_settle_ms): Return smoothly to 100% rest.
    """
    tilt_str = f"\\frz{rot_tilt}" if rot_tilt != 0 else ""
    settle_rot = f"\\t({t_rise_ms},{t_settle_ms},\\frz0)" if rot_tilt != 0 else ""
    return (
        f"{tilt_str}"
        f"\\t(0,{t_rise_ms},\\fscx{scale_overshoot}\\fscy{scale_overshoot})"
        f"\\t({t_rise_ms},{t_rebound_ms},\\fscx{scale_rebound}\\fscy{scale_rebound})"
        f"\\t({t_rebound_ms},{t_settle_ms},\\fscx100\\fscy100)"
        f"{settle_rot}"
    )


def build_ass_script(
    words: Sequence[TimedWord | dict[str, Any]],
    hook_title: str | None = None,
    hook_duration_sec: float = 2.8,
    config: SubtitlesConfig | None = None,
    onscreen_credit: str | None = None,
    total_duration_sec: float | None = None,
    active_color_override: str | None = None,
    hook_in_sec: float = 0.0,
    jump_cuts: Sequence[JumpCut] | None = None,
    style_preset: str = "neon",
    text_transform: str = "uppercase",
    subtitle_position: str = "center",
    hook_banner_style: str = "neon_box",
    enable_emojis: bool = True,
    primary_color: str | None = None,
    active_color: str | None = None,
    emphasis_color: str | None = None,
    outline_color: str | None = None,
    font_name: str | None = None,
    font_size: int | None = None,
    **kwargs: Any,
) -> str:
    """Generate complete SubStation Alpha (.ass) file contents for a 1080x1920 Short.

    Includes:
    - [Script Info] for 1080x1920
    - [V4+ Styles] supporting 7 presets ('neon', 'minimal', 'pill', 'hormozi', 'mrbeast', 'cyberpunk', 'cinematic')
    - [Events] with active-word pop animation, safe-zone positioning, typography transforms, and hook card
    """
    if config is None:
        config = SubtitlesConfig()

    preset_name = (
        (style_preset or getattr(config, "style_preset", "neon") or "neon").strip().lower()
    )
    preset = CAPTION_PRESETS.get(preset_name, CAPTION_PRESETS["neon"])

    # Determine font name: explicit param > config override > preset > default
    if font_name is not None:
        resolved_font_name = font_name
    elif config.font_name != "Montserrat ExtraBold":
        resolved_font_name = config.font_name
    elif preset_name != "neon" and "font_name" in preset:
        resolved_font_name = preset["font_name"]
    else:
        resolved_font_name = config.font_name

    # Determine font size: explicit param > config override > preset > default
    if font_size is not None:
        resolved_font_size = font_size
    elif config.font_size != 76:
        resolved_font_size = config.font_size
    elif preset_name != "neon" and "font_size" in preset:
        resolved_font_size = preset["font_size"]
    else:
        resolved_font_size = config.font_size

    # Subtitle vertical position safe zones:
    # "center" -> default 780 or config.safe_zone_margin_v
    # "bottom_safe" -> 420
    # "top_safe" -> 1450
    sub_pos = (subtitle_position or "center").strip().lower()
    if sub_pos == "bottom_safe":
        margin_v = 420
    elif sub_pos == "top_safe":
        margin_v = 1450
    elif sub_pos == "center":
        margin_v = max(350, config.safe_zone_margin_v)
    else:
        try:
            margin_v = max(350, int(sub_pos))
        except (ValueError, TypeError):
            margin_v = max(350, config.safe_zone_margin_v)

    margin_r = 150
    margin_l = 80

    # Resolve colors with hex_to_ass_color support
    if active_color:
        resolved_active_color = hex_to_ass_color(active_color)
    elif active_color_override:
        resolved_active_color = hex_to_ass_color(active_color_override)
    elif config.active_color != "&H0000FFFF":
        resolved_active_color = hex_to_ass_color(config.active_color)
    else:
        resolved_active_color = preset["active_color"]

    if emphasis_color:
        resolved_emphasis_color = hex_to_ass_color(emphasis_color)
    else:
        resolved_emphasis_color = preset["emphasis_color"]

    if primary_color:
        resolved_primary_color = hex_to_ass_color(primary_color)
    elif config.primary_color != "&H00FFFFFF":
        resolved_primary_color = hex_to_ass_color(config.primary_color)
    else:
        resolved_primary_color = preset["primary_color"]

    if outline_color:
        resolved_outline_color = hex_to_ass_color(outline_color)
    elif config.outline_color != "&H00000000":
        resolved_outline_color = hex_to_ass_color(config.outline_color)
    else:
        resolved_outline_color = preset["outline_color"]

    back_color = preset["back_color"]
    border_style = preset["border_style"]
    outline = preset["outline"]
    shadow = preset["shadow"]

    # Hook card banner style definition
    hb_style = (hook_banner_style or "neon_box").strip().lower()
    if hb_style == "pill":
        hook_style_def = (
            f"Style: HookCard,Montserrat Black,64,{resolved_active_color},{resolved_primary_color},"
            f"{resolved_outline_color},&HA0000000,-1,0,0,0,100,100,1,0,3,8,0,8,{margin_l},{margin_r},220,1"
        )
    elif hb_style == "minimal":
        hook_style_def = (
            f"Style: HookCard,Montserrat Bold,60,{resolved_primary_color},{resolved_active_color},"
            f"{resolved_outline_color},&H40000000,-1,0,0,0,100,100,1,0,1,2,1,8,{margin_l},{margin_r},220,1"
        )
    else:  # neon_box (default)
        hook_style_def = (
            f"Style: HookCard,Montserrat Black,64,{resolved_active_color},{resolved_primary_color},"
            f"{resolved_outline_color},&H90000000,-1,0,0,0,100,100,1,0,1,6,3,8,{margin_l},{margin_r},220,1"
        )

    # If hook_in trimming was applied, offset word timestamps
    if hook_in_sec > 0:
        words = offset_words_for_hook(words, hook_in_sec)
        jump_cuts = shift_jump_cuts_for_hook(jump_cuts, hook_in_sec)

    # If jump cuts exist, remap word timestamps to close excised dead-air gaps
    if jump_cuts:
        words = remap_words_for_jump_cuts(words, jump_cuts)
        if total_duration_sec is not None:
            total_cut_dur = sum(
                max(0.0, c.cut_end - c.cut_start) for c in jump_cuts if c.cut_end > c.cut_start >= 0
            )
            total_duration_sec = max(1.0, total_duration_sec - total_cut_dur)

    # Clean and chunk the words
    wpb = kwargs.get("words_per_burst", (2 if preset_name == "rapid" else config.words_per_burst))
    min_w = 1 if (preset_name == "rapid" or wpb <= 2) else 2
    chunks = chunk_words(
        words,
        words_per_burst=wpb,
        min_words=min_w,
    )

    # Calculate total duration if not provided
    if total_duration_sec is None:
        if chunks:
            total_duration_sec = max(chunks[-1].end, hook_duration_sec)
        else:
            total_duration_sec = hook_duration_sec

    lines: list[str] = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            f"Style: DopamineYellow,{resolved_font_name},{resolved_font_size},{resolved_primary_color},{resolved_active_color},"
            f"{resolved_outline_color},{back_color},-1,0,0,0,100,100,2,0,{border_style},{outline},{shadow},2,{margin_l},{margin_r},{margin_v},1"
        ),
        (
            f"Style: ActiveBox,{resolved_font_name},{resolved_font_size},{resolved_active_color},{resolved_primary_color},"
            f"&HA0000000,&HA0000000,-1,0,0,0,100,100,2,0,3,6,0,2,{margin_l},{margin_r},{margin_v},1"
        ),
        hook_style_def,
        (
            f"Style: Watermark,Montserrat Medium,36,&H80FFFFFF,&H00FFFFFF,&H80000000,"
            f"&H80000000,0,0,0,0,100,100,1,0,1,3,1,2,{margin_l},{margin_r},380,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # Hook title card in the first few seconds (Layer 1) - skip if hook_banner_style == "none"
    if hook_title and hb_style != "none":
        raw_hook = escape_ass_text(hook_title.strip())
        if text_transform == "uppercase":
            clean_hook = raw_hook.upper()
        elif text_transform == "title":
            clean_hook = raw_hook.title()
        else:
            clean_hook = raw_hook

        hook_end = min(hook_duration_sec, 3.0)
        hook_start_str = format_ass_timestamp(0.0)
        hook_end_str = format_ass_timestamp(hook_end)
        if not enable_emojis:
            formatted_hook = strip_emojis(clean_hook)
        else:
            formatted_hook = format_emojis_for_ass(clean_hook, base_font="Montserrat Black")

        lines.append(
            f"Dialogue: 1,{hook_start_str},{hook_end_str},HookCard,,0,0,0,,"
            f"{{\\fad(200,300)}}{formatted_hook}"
        )

    # Word-level karaoke dialogue events (Layer 0)
    for chunk in chunks:
        num_words = len(chunk.words)
        for idx, active_word in enumerate(chunk.words):
            event_start = active_word.start
            # Event ends when next word begins, or at chunk end
            if idx + 1 < num_words:
                event_end = chunk.words[idx + 1].start
            else:
                event_end = max(active_word.end, chunk.end)

            # Ensure minimum duration so brief speech isn't invisible
            if event_end <= event_start:
                event_end = event_start + 0.15

            start_str = format_ass_timestamp(event_start)
            end_str = format_ass_timestamp(event_end)

            # Assemble full chunk line with active word styled
            rendered_words: list[str] = []
            for j, w in enumerate(chunk.words):
                raw_w = escape_ass_text(w.word.strip())
                if text_transform == "uppercase":
                    w_text = raw_w.upper()
                elif text_transform == "title":
                    w_text = raw_w.title()
                else:
                    w_text = raw_w

                if not enable_emojis:
                    w_formatted = strip_emojis(w_text)
                else:
                    w_formatted = format_emojis_for_ass(w_text, base_font=resolved_font_name)

                if j == idx:
                    emphasis = is_emphasis_word(w_text)
                    color_tag = resolved_emphasis_color if emphasis else resolved_active_color

                    # Preset-specific animation and scale pops
                    if preset_name == "rapid":
                        scale_val = 135 if emphasis else 122
                        word_line = (
                            f"{{\\t(0,45,\\fscx{scale_val}\\fscy{scale_val})"
                            f"\\t(45,90,\\fscx100\\fscy100)\\c{color_tag}&}}{w_formatted}"
                        )
                    elif preset_name == "hormozi":
                        scale_val = 138 if emphasis else 130
                        word_line = (
                            f"{{\\t(0,70,\\fscx{scale_val}\\fscy{scale_val})"
                            f"\\t(70,140,\\fscx100\\fscy100)\\c{color_tag}&}}{w_formatted}"
                        )
                    elif preset_name == "mrbeast":
                        scale_val = 125 if emphasis else 118
                        word_line = (
                            f"{{\\frz-3\\t(0,80,\\fscx{scale_val}\\fscy{scale_val}\\frz3)"
                            f"\\t(80,160,\\fscx100\\fscy100\\frz0)\\c{color_tag}&}}{w_formatted}"
                        )
                    elif preset_name == "minimal":
                        scale_val = 110 if emphasis else 106
                        word_line = (
                            f"{{\\t(0,80,\\fscx{scale_val}\\fscy{scale_val})"
                            f"\\t(80,160,\\fscx100\\fscy100)\\c{color_tag}&}}{w_formatted}"
                        )
                    elif preset_name == "cinematic":
                        scale_val = 112 if emphasis else 108
                        word_line = (
                            f"{{\\t(0,90,\\fscx{scale_val}\\fscy{scale_val})"
                            f"\\t(90,180,\\fscx100\\fscy100)\\c{color_tag}&}}{w_formatted}"
                        )
                    else:  # neon, pill, cyberpunk
                        scale_max = 125 if emphasis else 118
                        word_line = (
                            f"{{\\frz-2\\t(0,80,\\fscx{scale_max}\\fscy{scale_max})"
                            f"\\t(80,160,\\fscx100\\fscy100)\\t(80,160,\\frz0)\\c{color_tag}&}}{w_formatted}"
                        )
                else:
                    # Inactive word in this chunk
                    word_line = f"{{\\c{resolved_primary_color}&}}{w_formatted}"
                rendered_words.append(word_line)

            dialogue_text = " ".join(rendered_words)
            lines.append(
                f"Dialogue: 0,{start_str},{end_str},DopamineYellow,,0,0,0,,{dialogue_text}"
            )

    # Optional on-screen watermark/credit in the final 4 seconds (Layer 2)
    if onscreen_credit and total_duration_sec and total_duration_sec > 4.0:
        watermark_start = max(0.0, total_duration_sec - 4.0)
        wm_start_str = format_ass_timestamp(watermark_start)
        wm_end_str = format_ass_timestamp(total_duration_sec)
        clean_credit = escape_ass_text(onscreen_credit.strip())
        lines.append(
            f"Dialogue: 2,{wm_start_str},{wm_end_str},Watermark,,0,0,0,,"
            f"{{\\fad(300,200)}}Source: {clean_credit}"
        )

    return "\n".join(lines) + "\n"


def write_ass_file(
    output_path: Path | str,
    words: Sequence[TimedWord | dict[str, Any]],
    hook_title: str | None = None,
    hook_duration_sec: float = 2.8,
    config: SubtitlesConfig | None = None,
    onscreen_credit: str | None = None,
    total_duration_sec: float | None = None,
    hook_in_sec: float = 0.0,
    jump_cuts: Sequence[JumpCut] | None = None,
    style_preset: str = "neon",
    text_transform: str = "uppercase",
    subtitle_position: str = "center",
    hook_banner_style: str = "neon_box",
    enable_emojis: bool = True,
    primary_color: str | None = None,
    active_color: str | None = None,
    emphasis_color: str | None = None,
    outline_color: str | None = None,
    font_name: str | None = None,
    font_size: int | None = None,
    **kwargs: Any,
) -> Path:
    """Generate and atomically write an ASS subtitle file to disk."""
    dest = Path(output_path).resolve()
    content = build_ass_script(
        words=words,
        hook_title=hook_title,
        hook_duration_sec=hook_duration_sec,
        config=config,
        onscreen_credit=onscreen_credit,
        total_duration_sec=total_duration_sec,
        hook_in_sec=hook_in_sec,
        jump_cuts=jump_cuts,
        style_preset=style_preset,
        text_transform=text_transform,
        subtitle_position=subtitle_position,
        hook_banner_style=hook_banner_style,
        enable_emojis=enable_emojis,
        primary_color=primary_color,
        active_color=active_color,
        emphasis_color=emphasis_color,
        outline_color=outline_color,
        font_name=font_name,
        font_size=font_size,
        **kwargs,
    )
    return atomic_write(dest, content, mode="w", encoding="utf-8")
