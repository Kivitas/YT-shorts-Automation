"""Full-length long-form video & animated faceless studio pipeline."""

from __future__ import annotations

from autoshorts.longform.publish import (
    LONGFORM_DAILY_CAP,
    enqueue_longform_on_quota,
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
from autoshorts.longform.wojak_script import (
    WojakLongformScript,
    WojakSegment,
    build_wojak_script,
    procedural_wojak_script,
)
from autoshorts.longform.wojak_audio import (
    assemble_wojak_audio,
    generate_procedural_wojak_bgm,
)
from autoshorts.longform.wojak_backgrounds import (
    get_wojak_background,
    render_procedural_hospital,
    render_procedural_school,
    render_procedural_transport,
)
from autoshorts.longform.wojak_compositor import (
    create_soft_drop_shadow,
    render_disclaimer_frame,
    render_wojak_scene_frame,
)
from autoshorts.longform.wojak_pipeline import (
    WojakPipelineConfig,
    WojakPipelineResult,
    WojakScene,
    resolve_topic_scenes,
    run_wojak_pipeline,
)
from autoshorts.longform.wojak_thumbnail import generate_wojak_thumbnail
from autoshorts.longform.wojak_generator import (
    WojakSpec,
    draw_wojak,
    spec_from_country_and_context,
    wojak_for_context,
)
from autoshorts.longform.wojak_shorts import (
    WojakShortsConfig,
    WojakShortsSegment,
    build_wojak_shorts_script,
    make_shorts_thumbnail,
    render_shorts_country_frame,
    render_shorts_disclaimer_frame,
    render_wojak_shorts_video,
    synth_cash_register,
    synth_flatline,
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
    "WojakLongformScript",
    "WojakPipelineConfig",
    "WojakPipelineResult",
    "WojakScene",
    "WojakSegment",
    "WojakShortsConfig",
    "WojakShortsSegment",
    "WojakSpec",
    "assemble_wojak_audio",
    "build_script",
    "build_wojak_script",
    "build_wojak_shorts_script",
    "create_soft_drop_shadow",
    "description_for",
    "draw_wojak",
    "enqueue_longform_on_quota",
    "estimate_duration",
    "generate_procedural_wojak_bgm",
    "generate_wojak_thumbnail",
    "get_topic",
    "get_wojak_background",
    "hashtags_for",
    "infer_genre",
    "longform_remaining_today",
    "longform_uploads_today",
    "make_shorts_thumbnail",
    "make_thumbnail",
    "normalize_topic",
    "procedural_wojak_script",
    "publish_longform",
    "render_disclaimer_frame",
    "render_longform",
    "render_procedural_hospital",
    "render_procedural_school",
    "render_procedural_transport",
    "render_script",
    "render_shorts_country_frame",
    "render_shorts_disclaimer_frame",
    "render_wojak_scene_frame",
    "render_wojak_shorts_video",
    "resolve_topic_scenes",
    "resolve_topic_spec",
    "run_wojak_pipeline",
    "spec_from_country_and_context",
    "synth_cash_register",
    "synth_flatline",
    "title_for",
    "wojak_for_context",
]
