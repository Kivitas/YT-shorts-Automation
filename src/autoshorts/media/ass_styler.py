"""High-Dopamine ASS Subtitle Generator with Word-Level Karaoke Pop Animations.

Produces SubStation Alpha (.ass) scripts formatted for 1080x1920 vertical Shorts:
- 2-4 word bursts (word chunker)
- Word-level active karaoke highlight with \\t scaling animation (100% -> 115% -> 100%)
- First-3s hook title card with fade effects
- Safe-zone positioning avoiding YouTube Shorts UI chrome
- Keyword and emoji emphasis styling
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoshorts.core.config import SubtitlesConfig
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


def build_ass_script(
    words: Sequence[TimedWord | dict[str, Any]],
    hook_title: str | None = None,
    hook_duration_sec: float = 2.8,
    config: SubtitlesConfig | None = None,
    onscreen_credit: str | None = None,
    total_duration_sec: float | None = None,
    active_color_override: str | None = None,
    hook_in_sec: float = 0.0,
) -> str:
    """Generate complete SubStation Alpha (.ass) file contents for a 1080x1920 Short.

    Includes:
    - [Script Info] for 1080x1920
    - [V4+ Styles] with DopamineYellow, HookCard, and Watermark
    - [Events] with active-word pop animation, safe-zone positioning, and hook card
    """
    if config is None:
        config = SubtitlesConfig()

    font_name = config.font_name
    font_size = config.font_size
    # Enforce mobile Shorts UI safe zones: clear of bottom 350px and right 150px
    margin_v = max(350, config.safe_zone_margin_v)
    margin_r = 150
    margin_l = 80
    primary_color = config.primary_color
    active_color = active_color_override or config.active_color
    outline_color = config.outline_color

    # If hook_in trimming was applied, offset word timestamps
    if hook_in_sec > 0:
        words = offset_words_for_hook(words, hook_in_sec)

    # Clean and chunk the words
    chunks = chunk_words(
        words,
        words_per_burst=config.words_per_burst,
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
            f"Style: DopamineYellow,{font_name},{font_size},{primary_color},{active_color},"
            f"{outline_color},&H80000000,-1,0,0,0,100,100,2,0,1,8,4,2,{margin_l},{margin_r},{margin_v},1"
        ),
        (
            f"Style: HookCard,Montserrat Black,64,{active_color},{primary_color},"
            f"{outline_color},&H90000000,-1,0,0,0,100,100,1,0,1,6,3,8,{margin_l},{margin_r},220,1"
        ),
        (
            f"Style: Watermark,Montserrat Medium,36,&H80FFFFFF,&H00FFFFFF,&H80000000,"
            f"&H80000000,0,0,0,0,100,100,1,0,1,3,1,2,{margin_l},{margin_r},380,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # Hook title card in the first few seconds (Layer 1)
    if hook_title:
        clean_hook = hook_title.strip().upper()
        hook_end = min(hook_duration_sec, 3.0)
        hook_start_str = format_ass_timestamp(0.0)
        hook_end_str = format_ass_timestamp(hook_end)
        lines.append(
            f"Dialogue: 1,{hook_start_str},{hook_end_str},HookCard,,0,0,0,,"
            f"{{\\fad(200,300)}}{clean_hook}"
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
                w_text = w.word.strip().upper()
                if j == idx:
                    # Active word: pop animation + active color highlight
                    # If high-emphasis keyword, use special emphasis scale/color
                    emphasis = is_emphasis_word(w_text)
                    scale_max = 125 if emphasis else 115
                    color_tag = active_color
                    word_line = (
                        f"{{\\t(0,80,\\fscx{scale_max}\\fscy{scale_max})"
                        f"\\t(80,160,\\fscx100\\fscy100)\\c{color_tag}&}}{w_text}"
                    )
                else:
                    # Inactive word in this chunk
                    word_line = f"{{\\c{primary_color}&}}{w_text}"
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
        clean_credit = onscreen_credit.strip()
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
    )
    return atomic_write(dest, content, mode="w", encoding="utf-8")
