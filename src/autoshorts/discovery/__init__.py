"""Discovery package for AutoShorts.

Provides YouTube URL validation, normalization, custom instruction parsing,
and non-scraping metadata extraction.
"""

from autoshorts.discovery.metadata_fetcher import (
    MetadataFetcher,
    MetadataFetchError,
    fetch_video_metadata,
)
from autoshorts.discovery.url_validator import (
    ALLOWED_YOUTUBE_HOSTS,
    YOUTUBE_VIDEO_ID_REGEX,
    InvalidURLError,
    ParsedLink,
    extract_video_id,
    is_valid_youtube_url,
    normalize_youtube_url,
    parse_link_line,
    parse_links_input,
)

__all__ = [
    "ALLOWED_YOUTUBE_HOSTS",
    "YOUTUBE_VIDEO_ID_REGEX",
    "InvalidURLError",
    "MetadataFetchError",
    "MetadataFetcher",
    "ParsedLink",
    "extract_video_id",
    "fetch_video_metadata",
    "is_valid_youtube_url",
    "normalize_youtube_url",
    "parse_link_line",
    "parse_links_input",
]
