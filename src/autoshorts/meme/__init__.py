"""AutoShorts Meme Generation Engine.

Supports Country Memes and 'Types Of...' relational meme videos
with anime visual styles, punchy scripts, dynamic audio cues, and viral hashtags.
"""

from __future__ import annotations

from autoshorts.meme.audio import (
    MemeSegmentAudio,
    assemble_meme_audio,
    generate_meme_sfx_library,
    get_or_create_bgm,
    resolve_music_mood,
    resolve_sfx_cue,
    synth_awkward_tick,
    synth_bass_drop,
    synth_cartoon_impact,
    synth_dramatic_stinger,
    synth_procedural_bgm,
    synth_record_scratch,
    synth_rimshot,
    synth_whoosh,
    synthesize_meme_audio,
    synthesize_meme_narration,
)
from autoshorts.meme.pipeline import render_meme
from autoshorts.meme.publish import (
    MEME_DAILY_CAP,
    meme_remaining_today,
    meme_uploads_today,
    publish_meme_video,
)
from autoshorts.meme.qc import MemeQC, QCOutcome
from autoshorts.meme.script import (
    ANIMATION_TYPES,
    BACKGROUNDS,
    EXPRESSIONS,
    MUSIC_MOODS,
    POSES,
    SFX_CUES,
    build_description,
    build_meme_script,
    match_audio_and_sfx,
)
from autoshorts.meme.thumbnail import make_meme_thumbnail
from autoshorts.meme.topics import (
    COUNTRY_MEME_TOPICS,
    HASHTAG_LIBRARY,
    TYPES_OF_TOPICS,
    VALID_CATEGORIES,
    generate_topic_idea,
    normalize_category,
    resolve_topic_and_category,
    select_hashtags,
)
from autoshorts.meme.types import (
    MemeConfig,
    MemeResult,
    MemeScript,
    MemeSegment,
)
from autoshorts.meme.visuals import (
    generate_segment_frames,
    get_resolution,
    render_all_meme_frames,
    render_meme_frame,
)

__all__ = [
    # Types
    "MemeSegment",
    "MemeScript",
    "MemeConfig",
    "MemeResult",
    "MemeSegmentAudio",
    # Topics & Hashtags
    "HASHTAG_LIBRARY",
    "VALID_CATEGORIES",
    "COUNTRY_MEME_TOPICS",
    "TYPES_OF_TOPICS",
    "select_hashtags",
    "normalize_category",
    "resolve_topic_and_category",
    "generate_topic_idea",
    # Script Engine
    "build_meme_script",
    "build_description",
    "match_audio_and_sfx",
    "POSES",
    "EXPRESSIONS",
    "BACKGROUNDS",
    "ANIMATION_TYPES",
    "MUSIC_MOODS",
    "SFX_CUES",
    # Audio & SFX Engine
    "MemeSegmentAudio",
    "synthesize_meme_audio",
    "synthesize_meme_narration",
    "assemble_meme_audio",
    "generate_meme_sfx_library",
    "resolve_music_mood",
    "resolve_sfx_cue",
    "synth_procedural_bgm",
    "get_or_create_bgm",
    "synth_record_scratch",
    "synth_bass_drop",
    "synth_cartoon_impact",
    "synth_whoosh",
    "synth_rimshot",
    "synth_dramatic_stinger",
    "synth_awkward_tick",
    # Visuals & Thumbnail
    "get_resolution",
    "render_meme_frame",
    "generate_segment_frames",
    "render_all_meme_frames",
    "make_meme_thumbnail",
    # QC & Pipeline
    "MemeQC",
    "QCOutcome",
    "render_meme",
    "publish_meme_video",
    "MEME_DAILY_CAP",
    "meme_uploads_today",
    "meme_remaining_today",
]
