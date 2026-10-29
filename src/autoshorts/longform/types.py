"""Shared data types for the long-form animated faceless studio video pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_VIDEO_SECONDS = 1200.0  # hard cap: 20 minutes (1200 seconds)
TARGET_VIDEO_SECONDS = 180.0  # default target: 3 minutes

VALID_GENRES = (
    "parody",
    "history",
    "science",
    "comparison",
    "finance",
    "self_improvement",
    "motivation",
)

VALID_VISUAL_MODES = (
    "auto",
    "character",
    "parody_duet",
    "history_map",
    "history_timeline",
    "science_chemistry",
    "science_physics",
    "science_biology",
    "science_astrophysics",
    "comparison_vs",
    "comparison_tier",
    "finance_curve",
    "motivation_loop",
    "motivation_value",
)

VISEME_SHAPES = ("REST", "AI", "O", "E", "MBP")


@dataclass
class Section:
    """One part of the script: a heading shown on screen, spoken narration, and visual cues."""

    heading: str
    narration: str
    visual_mode: str = "auto"
    speaker: str = "narrator"
    sub_domain: str = ""
    comparison_data: dict[str, Any] | None = None
    hook_callout: str = ""


@dataclass
class LongformScript:
    topic: str
    title: str
    hook: str
    sections: list[Section]
    provider: str  # "api" or "local"
    hashtags: list[str] = field(default_factory=list)
    genre: str = ""

    @property
    def narration(self) -> str:
        return " ".join(s.narration for s in self.sections)


@dataclass
class SectionAudio:
    """Narration audio for one section, with word timings relative to that section's start."""

    section: Section
    audio_path: Path
    words: list[dict[str, Any]]  # each: {"word": str, "start": float, "end": float}
    duration: float


@dataclass
class LongformResult:
    topic: str
    title: str
    video_path: Path
    thumbnail_path: Path
    duration: float
    description: str
    hashtags: list[str]
    provider: str
