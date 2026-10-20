"""Resilient YouTube metadata & section downloader with Heatmap, Chapters, and SponsorBlock."""

from __future__ import annotations

from autoshorts.core.exceptions import DownloadError, MetadataFetchError, VideoUnavailableError
from autoshorts.core.models import (
    HeatmapPoint,
    SponsorSegment,
    VideoChapter,
    VideoMetadata,
)
from autoshorts.discovery.metadata_fetcher import (
    MetadataFetcher,
    fetch_sponsor_segments,
    fetch_video_metadata,
)
from autoshorts.media.downloader import (
    DEFAULT_FORMAT_SPEC,
    Downloader,
    YtDlpDownloader,
    format_section_arg,
    probe_video_stream_info,
    run_command_with_cleanup,
    terminate_process_tree,
)

__all__ = [
    "DEFAULT_FORMAT_SPEC",
    "DownloadError",
    "Downloader",
    "HeatmapPoint",
    "MetadataFetchError",
    "MetadataFetcher",
    "SponsorSegment",
    "VideoChapter",
    "VideoMetadata",
    "VideoUnavailableError",
    "YtDlpDownloader",
    "fetch_sponsor_segments",
    "fetch_video_metadata",
    "format_section_arg",
    "probe_video_stream_info",
    "run_command_with_cleanup",
    "terminate_process_tree",
]
