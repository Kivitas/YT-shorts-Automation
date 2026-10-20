"""Ingestion module for AutoShorts."""

from autoshorts.ingestion.downloader import (
    DEFAULT_FORMAT_SPEC,
    Downloader,
    YtDlpDownloader,
    fetch_sponsor_segments,
    format_section_arg,
    probe_video_stream_info,
)

__all__ = [
    "DEFAULT_FORMAT_SPEC",
    "Downloader",
    "YtDlpDownloader",
    "fetch_sponsor_segments",
    "format_section_arg",
    "probe_video_stream_info",
]
