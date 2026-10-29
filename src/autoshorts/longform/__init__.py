"""Full-length long-form video & animated faceless studio pipeline."""

from __future__ import annotations

from autoshorts.longform.publish import (
    LONGFORM_DAILY_CAP,
    longform_remaining_today,
    longform_uploads_today,
    publish_longform,
)
from autoshorts.longform.render import render_longform, render_script
from autoshorts.longform.script import build_script, description_for, estimate_duration
from autoshorts.longform.thumbnail import make_thumbnail
from autoshorts.longform.topics import (
    GENRE_KEYS,
    TOPIC_KEYS,
    TOPICS,
    TopicSpec,
    get_topic,
    hashtags_for,
    infer_genre,
    normalize_topic,
    resolve_topic_spec,
    title_for,
)
from autoshorts.longform.types import (
    MAX_VIDEO_SECONDS,
    TARGET_VIDEO_SECONDS,
    VALID_GENRES,
    VALID_VISUAL_MODES,
    VISEME_SHAPES,
    LongformResult,
    LongformScript,
    Section,
    SectionAudio,
)

__all__ = [
    "GENRE_KEYS",
    "LONGFORM_DAILY_CAP",
    "MAX_VIDEO_SECONDS",
    "TARGET_VIDEO_SECONDS",
    "TOPICS",
    "TOPIC_KEYS",
    "VALID_GENRES",
    "VALID_VISUAL_MODES",
    "VISEME_SHAPES",
    "LongformResult",
    "LongformScript",
    "Section",
    "SectionAudio",
    "TopicSpec",
    "build_script",
    "description_for",
    "estimate_duration",
    "get_topic",
    "hashtags_for",
    "infer_genre",
    "longform_remaining_today",
    "longform_uploads_today",
    "make_thumbnail",
    "normalize_topic",
    "publish_longform",
    "render_longform",
    "render_script",
    "resolve_topic_spec",
    "title_for",
]
