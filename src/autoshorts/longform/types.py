"""Shared data types for the long-form (not Shorts) stick-figure video pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_VIDEO_SECONDS = 240.0  # hard cap: 4 minutes
TARGET_VIDEO_SECONDS = 180.0  # default target: under 3 minutes


@dataclass
class Section:
    """One part of the script: a heading shown on screen and the narration spoken."""

    heading: str
    narration: str


@dataclass
class LongformScript:
    topic: str
    title: str
    hook: str
    sections: list[Section]
    provider: str  # "api" or "local"
    hashtags: list[str] = field(default_factory=list)

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
