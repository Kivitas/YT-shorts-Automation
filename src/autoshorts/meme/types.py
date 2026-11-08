"""Data types and schemas for Country Meme and Types Of... meme video generation.

Provides structured representations for meme segments, scripts, generation configs,
and pipeline results.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MemeSegment:
    """Represents a single segment/scene within a meme video.

    Attributes:
        id: Unique identifier for the segment (e.g., 'seg_1', 'japan', 'procrastinator').
        name: Name of the subject (e.g., country name 'Japan' or type 'The Procrastinator').
        heading: On-screen heading or title card (e.g., 'JAPAN 🇯🇵' or 'THE PROCRASTINATOR').
        narration: Spoken voiceover narration script for this segment.
        scenario: Brief description of the situational comedy / scene context.
        character_description: Visual description of the character representing the trope.
        pose: Character pose keyword (e.g., 'sleeping_desk', 'gaming_intense', 'smug_crossed_arms').
        expression: Facial expression keyword (e.g., 'shocked', 'smug', 'deadpan', 'panicked').
        background_theme: Background environment keyword (e.g., 'train_station', 'classroom', 'office').
        animation_type: Motion/camera effect (e.g., 'zoom_in', 'zoom_out', 'shake', 'parallax', 'impact_pop').
        music_mood: Segment music mood ('playful', 'quirky', 'epic_orchestral', 'suspense', 'punchline_cut', 'unexpected_reveal', 'lofi_chill').
        sfx_cue: Sound effect cue ('record_scratch', 'bass_drop', 'cartoon_impact', 'whoosh', 'rimshot', 'stinger', 'none').
        subtitle_text: Cleaned subtitle text displayed during narration.
        speech_bubble_text: Punchy anime-style speech bubble line spoken by the character.
    """

    id: str
    name: str
    heading: str
    narration: str
    scenario: str
    character_description: str
    pose: str
    expression: str
    background_theme: str
    animation_type: str
    music_mood: str
    sfx_cue: str
    subtitle_text: str
    speech_bubble_text: str

    def to_dict(self) -> dict[str, Any]:
        """Convert segment to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemeSegment:
        """Create segment from dictionary."""
        return cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            heading=str(data.get("heading", "")),
            narration=str(data.get("narration", "")),
            scenario=str(data.get("scenario", "")),
            character_description=str(data.get("character_description", "")),
            pose=str(data.get("pose", "dramatic_heroic")),
            expression=str(data.get("expression", "smug")),
            background_theme=str(data.get("background_theme", "classroom")),
            animation_type=str(data.get("animation_type", "zoom_in")),
            music_mood=str(data.get("music_mood", "playful")),
            sfx_cue=str(data.get("sfx_cue", "cartoon_impact")),
            subtitle_text=str(data.get("subtitle_text", data.get("narration", ""))),
            speech_bubble_text=str(data.get("speech_bubble_text", "")),
        )


@dataclass
class MemeScript:
    """Full script structure for a meme short video.

    Attributes:
        mode: Generation mode ('country_meme' or 'types_of').
        category: Thematic category ('countries', 'school', 'food', 'relatable', 'dark_comedy', 'anime', 'custom').
        topic: Specific topic title (e.g., 'Every Country\\'s Public Transport Be Like').
        title: High-engagement video title with emojis.
        hook: 0-3 second opening hook sentence.
        intro: 3-6 second topic introduction sentence.
        segments: Sequence of 5-10 meme segments.
        ending: Punchline closing with a strong viewer engagement call-to-action.
        hashtags: Curated list of 3-5 high-performing hashtags.
        estimated_duration: Estimated total video duration in seconds.
    """

    mode: str
    category: str
    topic: str
    title: str
    hook: str
    intro: str
    segments: list[MemeSegment] = field(default_factory=list)
    ending: str = ""
    hashtags: list[str] = field(default_factory=list)
    estimated_duration: float = 60.0

    def to_dict(self) -> dict[str, Any]:
        """Convert script to dictionary."""
        return {
            "mode": self.mode,
            "category": self.category,
            "topic": self.topic,
            "title": self.title,
            "hook": self.hook,
            "intro": self.intro,
            "segments": [s.to_dict() for s in self.segments],
            "ending": self.ending,
            "hashtags": list(self.hashtags),
            "estimated_duration": self.estimated_duration,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemeScript:
        """Create script from dictionary."""
        segments_data = data.get("segments", [])
        segments = [MemeSegment.from_dict(s) if isinstance(s, dict) else s for s in segments_data]
        return cls(
            mode=str(data.get("mode", "country_meme")),
            category=str(data.get("category", "countries")),
            topic=str(data.get("topic", "")),
            title=str(data.get("title", "")),
            hook=str(data.get("hook", "")),
            intro=str(data.get("intro", "")),
            segments=segments,
            ending=str(data.get("ending", "")),
            hashtags=list(data.get("hashtags", [])),
            estimated_duration=float(data.get("estimated_duration", 60.0)),
        )


@dataclass
class MemeConfig:
    """Configuration options for meme video generation.

    Attributes:
        mode: Generation mode ('country_meme' or 'types_of').
        topic: Specific topic string, or empty string for auto-selection.
        category: Content category ('countries', 'school', 'food', 'relatable', 'dark_comedy', 'anime', 'custom').
        segment_count: Target number of segments (typically 5 to 10).
        target_duration: Desired video duration in seconds.
        aspect_ratio: Video frame aspect ratio ('9:16' for Shorts/TikTok, '16:9' for landscape).
        visual_style: Visual rendering aesthetic ('anime_color' or 'manga_bw').
        voice: Edge-TTS voice identifier.
        music_style: Primary background music style / vibe.
        humor_intensity: Level of comedic exaggeration ('mild', 'moderate', 'exaggerated', 'chaotic').
        publish: Whether to automatically publish to YouTube when rendering finishes.
    """

    mode: str = "country_meme"
    topic: str = ""
    category: str = "countries"
    segment_count: int = 6
    target_duration: float = 60.0
    aspect_ratio: str = "9:16"
    visual_style: str = "anime_color"
    voice: str = "en-US-ChristopherNeural"
    music_style: str = "playful"
    humor_intensity: str = "exaggerated"
    publish: bool = False

    def validate(self) -> None:
        """Validate configuration settings and normalize values."""
        valid_modes = {"country_meme", "types_of"}
        if self.mode not in valid_modes:
            raise ValueError(f"Invalid mode '{self.mode}'. Must be one of {valid_modes}")

        valid_aspects = {"9:16", "16:9"}
        if self.aspect_ratio not in valid_aspects:
            raise ValueError(
                f"Invalid aspect_ratio '{self.aspect_ratio}'. Must be one of {valid_aspects}"
            )

        valid_styles = {"anime_color", "manga_bw"}
        if self.visual_style not in valid_styles:
            raise ValueError(
                f"Invalid visual_style '{self.visual_style}'. Must be one of {valid_styles}"
            )

        if not (5 <= self.segment_count <= 10):
            # Clamp or allow with warning; clamp to 5-10
            self.segment_count = max(5, min(10, self.segment_count))


@dataclass
class MemeSegmentAudio:
    """Audio stems and timing metadata for a single meme segment.

    Attributes:
        segment: Associated MemeSegment instance.
        audio_path: Path to rendered narration speech file.
        words: Word-level alignment timestamps list.
        start_time: Segment onset timestamp in seconds on global timeline.
        duration: Narration duration in seconds.
        music_mood: Resolved music mood name for BGM matching.
        sfx_cue: Triggered sound effect cue identifier.
        sfx_time: Exact timestamp in seconds when the SFX fires on global timeline.
    """

    segment: MemeSegment
    audio_path: Path
    words: list[dict[str, Any]]
    start_time: float
    duration: float
    music_mood: str
    sfx_cue: str
    sfx_time: float


@dataclass
class MemeResult:
    """Final output result of the meme generation pipeline.

    Attributes:
        script: Generated or utilized MemeScript.
        video_path: Absolute path to the rendered video file.
        thumbnail_path: Absolute path to the rendered thumbnail image.
        title: YouTube title.
        description: Formatted YouTube description text including hashtags.
        hashtags: Selected hashtags list.
        duration: Actual rendered duration in seconds.
        aspect_ratio: Video aspect ratio ('9:16' or '16:9').
        qc_passed: Whether the video passed quality control verification.
        youtube_url: Published YouTube URL if upload was performed, else None.
    """

    script: MemeScript
    video_path: Path
    thumbnail_path: Path
    title: str
    description: str
    hashtags: list[str]
    duration: float
    aspect_ratio: str
    qc_passed: bool
    youtube_url: str | None = None
