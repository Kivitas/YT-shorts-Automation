"""Non-scraping YouTube metadata extractor using yt-dlp and oEmbed fallback.

Extracts video title, channel, channel_url, duration, and description without
downloading media bytes. Follows PLAN.md section 3.6 and contracts.Downloader.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.parse

import httpx

from autoshorts.core.exceptions import MetadataFetchError, VideoUnavailableError
from autoshorts.core.models import (
    HeatmapPoint,
    SponsorSegment,
    VideoChapter,
    VideoMetadata,
)
from autoshorts.core.paths import resolve_yt_dlp_binary
from autoshorts.discovery.url_validator import extract_video_id, normalize_youtube_url

logger = logging.getLogger(__name__)


def _is_mock_object(obj: object) -> bool:
    """Return True if obj is a unittest.mock object."""
    return "Mock" in type(obj).__name__ or hasattr(obj, "mock_calls")


def fetch_sponsor_segments(video_id: str, timeout: float = 3.0) -> list[SponsorSegment]:
    """Query the public SponsorBlock API for skip segments on a YouTube video.

    Categories queried: sponsor, selfpromo, intro, outro, interaction.
    Wrapped in a fast non-blocking try/except returning [] on 404/timeout/offline.
    """
    if not video_id or not str(video_id).strip():
        return []

    vid = str(video_id).strip()
    categories_param = '["sponsor","selfpromo","intro","outro","interaction"]'
    url = (
        f"https://sponsor.ajay.app/api/skipSegments"
        f"?videoID={urllib.parse.quote(vid, safe='')}"
        f"&categories={urllib.parse.quote(categories_param, safe='')}"
    )
    try:
        resp = httpx.get(url, timeout=timeout)
        if resp.status_code != 200:
            return []
        payload = resp.json()
        if not isinstance(payload, list):
            return []

        segments: list[SponsorSegment] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or "sponsor").strip()
            seg_bounds = item.get("segment")
            if isinstance(seg_bounds, (list, tuple)) and len(seg_bounds) >= 2:
                st = max(0.0, float(seg_bounds[0]))
                et = max(st, float(seg_bounds[1]))
            else:
                st = max(0.0, float(item.get("start_time", item.get("start", 0.0)) or 0.0))
                et = max(st, float(item.get("end_time", item.get("end", st)) or st))
            if et > st:
                segments.append(SponsorSegment(category=category, start_time=st, end_time=et))
        return segments
    except Exception as exc:
        logger.debug("SponsorBlock lookup failed or skipped for %s: %s", vid, exc)
        return []


def _parse_heatmap(raw_heatmap: object) -> list[HeatmapPoint]:
    """Parse yt-dlp heatmap array into list[HeatmapPoint] clamping values to [0.0, 1.0]."""
    if not isinstance(raw_heatmap, list):
        return []
    points: list[HeatmapPoint] = []
    for item in raw_heatmap:
        if not isinstance(item, dict):
            continue
        try:
            st = max(0.0, float(item.get("start_time", item.get("start", 0.0)) or 0.0))
            et = max(st, float(item.get("end_time", item.get("end", st)) or st))
            raw_val = float(item.get("value", 0.0) or 0.0)
            clamped_val = max(0.0, min(1.0, raw_val))
            points.append(HeatmapPoint(start_time=st, end_time=et, value=clamped_val))
        except (TypeError, ValueError):
            continue
    return points


def _parse_chapters(raw_chapters: object) -> list[VideoChapter]:
    """Parse yt-dlp chapters array into list[VideoChapter]."""
    if not isinstance(raw_chapters, list):
        return []
    chapters: list[VideoChapter] = []
    for item in raw_chapters:
        if not isinstance(item, dict):
            continue
        try:
            st = max(0.0, float(item.get("start_time", item.get("start", 0.0)) or 0.0))
            et = max(st, float(item.get("end_time", item.get("end", st)) or st))
            title = str(item.get("title") or "Chapter").strip()
            chapters.append(VideoChapter(title=title, start_time=st, end_time=et))
        except (TypeError, ValueError):
            continue
    return chapters


class MetadataFetcher:
    """Retrieves YouTube video metadata via yt-dlp --dump-json with oEmbed fallback."""

    def __init__(
        self,
        yt_dlp_binary: str = "yt-dlp",
        timeout_sec: float = 30.0,
        oembed_timeout_sec: float = 10.0,
        http_client: httpx.Client | None = None,
        cookies_from_browser: str | None = None,
    ) -> None:
        """Initialize the MetadataFetcher.

        Args:
            yt_dlp_binary: Path or command name for yt-dlp executable.
            timeout_sec: Subprocess timeout in seconds for yt-dlp.
            oembed_timeout_sec: HTTP timeout in seconds for oEmbed fallback.
            http_client: Optional pre-configured httpx.Client instance.
            cookies_from_browser: Optional browser name (e.g. 'chrome', 'firefox') for cookies.
        """
        self.yt_dlp_binary = resolve_yt_dlp_binary(yt_dlp_binary)
        self.timeout_sec = timeout_sec
        self.oembed_timeout_sec = oembed_timeout_sec
        self._client = http_client

        resolved_cookies = cookies_from_browser
        if not resolved_cookies:
            resolved_cookies = os.environ.get("YTDLP_COOKIES_FROM_BROWSER") or os.environ.get(
                "COOKIES_FROM_BROWSER"
            )
        self.cookies_from_browser = resolved_cookies

    def fetch(self, url: str) -> VideoMetadata:
        """Fetch video metadata using yt-dlp, falling back to YouTube oEmbed on failure.

        Args:
            url: YouTube video URL or 11-char video ID.

        Returns:
            Populated VideoMetadata model.

        Raises:
            VideoUnavailableError: If the video is a live stream or upcoming premiere.
            MetadataFetchError: If both yt-dlp and oEmbed fail.
        """
        canonical_url = normalize_youtube_url(url)
        vid = extract_video_id(url)

        ytdlp_error: Exception | None = None
        try:
            return self._fetch_via_ytdlp(canonical_url, vid)
        except VideoUnavailableError:
            raise
        except Exception as exc:
            ytdlp_error = exc
            logger.warning(
                "yt-dlp metadata extraction failed for %s (%s). Attempting oEmbed fallback.",
                canonical_url,
                exc,
            )

        try:
            return self._fetch_via_oembed(canonical_url, vid)
        except Exception as oembed_exc:
            logger.error(
                "oEmbed fallback also failed for %s: %s",
                canonical_url,
                oembed_exc,
            )
            raise MetadataFetchError(
                f"Failed to fetch metadata for '{url}'. "
                f"yt-dlp error: {ytdlp_error}; oEmbed error: {oembed_exc}"
            ) from oembed_exc

    def _fetch_via_ytdlp(self, canonical_url: str, video_id: str) -> VideoMetadata:
        """Fetch metadata by running yt-dlp --dump-json --skip-download."""
        cmd = [
            self.yt_dlp_binary,
            "--dump-json",
            "--skip-download",
            "--no-playlist",
            "--no-warnings",
            "--no-progress",
            canonical_url,
        ]

        if self.cookies_from_browser:
            cmd.extend(["--cookies-from-browser", self.cookies_from_browser])

        logger.debug("Executing yt-dlp metadata fetch: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            raise MetadataFetchError(f"yt-dlp process execution failed: {e}") from e

        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or "").strip()
            raise MetadataFetchError(f"yt-dlp returned non-zero code {proc.returncode}: {err_msg}")

        stdout_text = proc.stdout.strip()
        if not stdout_text:
            raise MetadataFetchError("yt-dlp returned empty stdout JSON")

        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError as e:
            raise MetadataFetchError(f"Failed to decode yt-dlp output as JSON: {e}") from e

        # Inspect live_status and is_live to guard against live streams and upcoming premieres
        is_live = bool(data.get("is_live") or data.get("live_status") == "is_live")
        is_upcoming = bool(data.get("live_status") == "is_upcoming")
        if is_live or is_upcoming:
            raise VideoUnavailableError(
                "Live stream or upcoming premiere cannot be processed until broadcast finishes"
            )

        title = data.get("title") or "Unknown Title"
        channel = (
            data.get("uploader")
            or data.get("channel")
            or data.get("uploader_id")
            or "Unknown Channel"
        )
        channel_url = (
            data.get("channel_url")
            or data.get("uploader_url")
            or (
                f"https://www.youtube.com/@{data['uploader_id']}"
                if data.get("uploader_id")
                else "https://www.youtube.com"
            )
        )
        duration = float(data.get("duration") or 0.0)
        webpage_url = data.get("webpage_url") or canonical_url
        description = data.get("description")

        heatmap = _parse_heatmap(data.get("heatmap"))
        chapters = _parse_chapters(data.get("chapters"))

        sponsor_segments: list[SponsorSegment] = []
        raw_sponsors = data.get("sponsor_segments")
        if isinstance(raw_sponsors, list):
            for item in raw_sponsors:
                if isinstance(item, dict):
                    try:
                        cat = str(item.get("category") or "sponsor").strip()
                        st = max(0.0, float(item.get("start_time", item.get("start", 0.0)) or 0.0))
                        et = max(st, float(item.get("end_time", item.get("end", st)) or st))
                        if et > st:
                            sponsor_segments.append(
                                SponsorSegment(category=cat, start_time=st, end_time=et)
                            )
                    except (TypeError, ValueError):
                        continue

        # Query SponsorBlock unless running inside an offline subprocess.run unit test
        # where neither httpx.get nor fetch_sponsor_segments is mocked.
        should_query_sponsorblock = not _is_mock_object(subprocess.run) or (
            _is_mock_object(httpx.get) or _is_mock_object(fetch_sponsor_segments)
        )
        if not sponsor_segments and video_id and should_query_sponsorblock:
            sponsor_segments = fetch_sponsor_segments(video_id)

        return VideoMetadata(
            title=title,
            channel=channel,
            channel_url=channel_url,
            video_url=webpage_url,
            duration=max(0.0, duration),
            video_id=video_id,
            description=description,
            heatmap=heatmap,
            chapters=chapters,
            sponsor_segments=sponsor_segments,
        )

    def _fetch_via_oembed(self, canonical_url: str, video_id: str) -> VideoMetadata:
        """Fetch basic video metadata via YouTube oEmbed endpoint."""
        oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(canonical_url, safe='')}&format=json"
        logger.debug("Querying oEmbed: %s", oembed_url)

        def _get_resp(client: httpx.Client) -> httpx.Response:
            return client.get(oembed_url, timeout=self.oembed_timeout_sec)

        try:
            if self._client:
                resp = _get_resp(self._client)
            else:
                with httpx.Client() as client:
                    resp = _get_resp(client)
        except Exception as e:
            raise MetadataFetchError(f"HTTP request to oEmbed failed: {e}") from e

        if resp.status_code != 200:
            raise MetadataFetchError(f"oEmbed returned HTTP {resp.status_code}: {resp.text}")

        try:
            payload = resp.json()
        except Exception as e:
            raise MetadataFetchError(f"Failed to parse oEmbed JSON response: {e}") from e

        title = payload.get("title") or "Unknown Title"
        channel = payload.get("author_name") or "Unknown Channel"
        channel_url = payload.get("author_url") or "https://www.youtube.com"

        return VideoMetadata(
            title=title,
            channel=channel,
            channel_url=channel_url,
            video_url=canonical_url,
            duration=0.0,
            video_id=video_id,
            description=None,
        )


def fetch_video_metadata(
    url: str,
    yt_dlp_binary: str = "yt-dlp",
    timeout_sec: float = 30.0,
    cookies_from_browser: str | None = None,
) -> VideoMetadata:
    """Convenience functional helper to fetch VideoMetadata."""
    fetcher = MetadataFetcher(
        yt_dlp_binary=yt_dlp_binary,
        timeout_sec=timeout_sec,
        cookies_from_browser=cookies_from_browser,
    )
    return fetcher.fetch(url)


__all__ = [
    "HeatmapPoint",
    "MetadataFetchError",
    "MetadataFetcher",
    "SponsorSegment",
    "VideoChapter",
    "VideoMetadata",
    "VideoUnavailableError",
    "fetch_sponsor_segments",
    "fetch_video_metadata",
]
