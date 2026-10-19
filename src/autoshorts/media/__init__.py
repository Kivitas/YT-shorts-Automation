"""Media processing, video compositing, hardware probing, and subtitle generation for AutoShorts."""

from autoshorts.media.ass_styler import (
    TimedWord,
    WordChunk,
    build_ass_script,
    chunk_words,
    format_ass_timestamp,
    is_emphasis_word,
    write_ass_file,
)
from autoshorts.media.compositor import (
    FasterWhisperTranscriber,
    FFmpegCompositor,
    extract_16khz_mono_wav,
    has_audio_stream,
    probe_media_duration,
)
from autoshorts.media.dopamine_effects import (
    DopaminePreset,
    build_complex_filtergraph,
    build_jumpcut_select_filter,
    build_progress_bar_filter,
    build_zoom_punch_filter,
    find_user_audio_assets,
    find_user_background_videos,
    get_dopamine_preset,
    pick_background_asset,
)
from autoshorts.media.downloader import (
    DEFAULT_FORMAT_SPEC,
    Downloader,
    DownloadError,
    format_section_arg,
)
from autoshorts.media.encoder_probe import (
    EncoderInfo,
    is_ffmpeg_available,
    is_ffprobe_available,
    probe_encoder,
)

__all__ = [
    "DEFAULT_FORMAT_SPEC",
    "DopaminePreset",
    "DownloadError",
    "Downloader",
    "EncoderInfo",
    "FFmpegCompositor",
    "FasterWhisperTranscriber",
    "TimedWord",
    "WordChunk",
    "build_ass_script",
    "build_complex_filtergraph",
    "build_jumpcut_select_filter",
    "build_progress_bar_filter",
    "build_zoom_punch_filter",
    "chunk_words",
    "extract_16khz_mono_wav",
    "find_user_audio_assets",
    "find_user_background_videos",
    "format_ass_timestamp",
    "format_section_arg",
    "get_dopamine_preset",
    "has_audio_stream",
    "is_emphasis_word",
    "is_ffmpeg_available",
    "is_ffprobe_available",
    "pick_background_asset",
    "probe_encoder",
    "probe_media_duration",
    "write_ass_file",
]
