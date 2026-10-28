"""Ranking countdown overlay for vertical Shorts (#5 -> #1).

Builds an ASS (SubStation Alpha) document with a large animated rank card ("#N") that
pops in for each item's [start, end] window, plus an optional label below it and an
optional title banner. Reuses the timestamp, escaping and colour helpers from ass_styler.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from autoshorts.media.ass_styler import escape_ass_text, format_ass_timestamp, hex_to_ass_color

ACCENT_COLOR = hex_to_ass_color("#FFE600")
PRIMARY_COLOR = "&H00FFFFFF"
OUTLINE_COLOR = "&H00000000"
BACK_COLOR = "&H80000000"
FONT_NAME = "Montserrat ExtraBold"


@dataclass
class RankItem:
    """One ranked entry shown from start to end (seconds)."""

    rank: int
    label: str
    start: float
    end: float


def _validate_items(items: list[RankItem]) -> list[RankItem]:
    if not items:
        raise ValueError("items must not be empty")
    for item in items:
        if isinstance(item.rank, bool) or not isinstance(item.rank, int) or item.rank < 1:
            raise ValueError(f"rank must be a positive int, got {item.rank!r}")
        if item.end <= item.start:
            raise ValueError(f"rank {item.rank} has end <= start")
    for prev, cur in itertools.pairwise(items):
        if cur.start < prev.start:
            raise ValueError("items must be sorted by start")
        if cur.start < prev.end:
            raise ValueError(f"rank {cur.rank} overlaps rank {prev.rank}")
    return items


def build_ranking_ass(
    items: list[RankItem],
    width: int = 1080,
    height: int = 1920,
    title: str | None = None,
) -> str:
    """Return a complete ASS document with animated rank cards for the given items."""
    ordered = _validate_items(list(items))
    cx = width // 2
    card_y = int(height * 0.40)
    label_y = int(height * 0.52)
    title_y = int(height * 0.12)

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
            f"Style: RankCard,{FONT_NAME},300,{ACCENT_COLOR},{PRIMARY_COLOR},{OUTLINE_COLOR},"
            f"{BACK_COLOR},-1,0,0,0,100,100,0,0,1,12,6,5,40,40,0,1"
        ),
        (
            f"Style: RankLabel,{FONT_NAME},80,{PRIMARY_COLOR},{ACCENT_COLOR},{OUTLINE_COLOR},"
            f"{BACK_COLOR},-1,0,0,0,100,100,0,0,1,8,4,8,80,80,0,1"
        ),
        (
            f"Style: RankTitle,{FONT_NAME},84,{PRIMARY_COLOR},{ACCENT_COLOR},{OUTLINE_COLOR},"
            f"{BACK_COLOR},-1,0,0,0,100,100,0,0,1,8,4,8,80,80,0,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    if title:
        clean_title = escape_ass_text(title.strip()).upper()
        end_all = format_ass_timestamp(ordered[-1].end)
        lines.append(
            f"Dialogue: 0,{format_ass_timestamp(0.0)},{end_all},RankTitle,,0,0,0,,"
            f"{{\\an8\\pos({cx},{title_y})\\fad(200,200)}}{clean_title}"
        )

    for item in ordered:
        start = format_ass_timestamp(item.start)
        end = format_ass_timestamp(item.end)
        # Pop-in: scale 0 -> 115% -> 100% over the first 0.3s of the card's window.
        card_text = (
            f"{{\\an5\\pos({cx},{card_y})\\fscx0\\fscy0"
            f"\\t(0,180,\\fscx115\\fscy115)\\t(180,300,\\fscx100\\fscy100)}}#{item.rank}"
        )
        lines.append(f"Dialogue: 1,{start},{end},RankCard,,0,0,0,,{card_text}")

        label = escape_ass_text((item.label or "").strip())
        if label:
            lines.append(
                f"Dialogue: 1,{start},{end},RankLabel,,0,0,0,,"
                f"{{\\an8\\pos({cx},{label_y})\\fad(150,150)}}{label}"
            )

    return "\n".join(lines) + "\n"


def merge_ass_documents(base: str, overlay: str) -> str:
    """Add the overlay's styles and dialogue lines to a caption ASS document.

    The overlay styles go into the base style block (before [Events]); its dialogue
    lines are appended to the base events, so one subtitles filter draws both.
    """
    base_lines = base.rstrip("\n").splitlines()
    overlay_lines = overlay.splitlines()
    events_idx = next((i for i, line in enumerate(base_lines) if line.strip() == "[Events]"), None)
    if events_idx is None:
        raise ValueError("base ASS document has no [Events] section")
    styles = [line for line in overlay_lines if line.startswith("Style:")]
    dialogues = [line for line in overlay_lines if line.startswith("Dialogue:")]
    merged = base_lines[:events_idx] + styles + [""] + base_lines[events_idx:] + dialogues
    return "\n".join(merged) + "\n"


def ranking_timeline(rank_count: int, total_sec: float) -> list[RankItem]:
    """Split total_sec evenly into rank_count windows, counting down from rank_count to 1."""
    if isinstance(rank_count, bool) or not isinstance(rank_count, int) or rank_count < 1:
        raise ValueError(f"rank_count must be a positive int, got {rank_count!r}")
    if total_sec <= 0:
        raise ValueError("total_sec must be positive")
    window = total_sec / rank_count
    items: list[RankItem] = []
    for i in range(rank_count):
        start = i * window
        end = total_sec if i == rank_count - 1 else (i + 1) * window
        items.append(RankItem(rank=rank_count - i, label="", start=start, end=end))
    return items
