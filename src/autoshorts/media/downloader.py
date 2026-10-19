"""Precision video and audio section downloader implementing contracts.Downloader.

Strictly uses yt-dlp subprocess commands with --download-sections and --force-keyframes-at-cuts.
Enforces CPU-friendly format selection (<=1080p), atomic file operations, backoff retries,
timeouts, and optional browser cookies.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from autoshorts.core.config import Settings, load_config
from autoshorts.core.contracts import Downloader as DownloaderProtocol
from autoshorts.core.exceptions import DownloadError
from autoshorts.core.models import VideoMetadata
from autoshorts.core.paths import get_work_dir
from autoshorts.discovery.metadata_fetcher import MetadataFetcher
from autoshorts.discovery.url_validator import extract_video_id, normalize_youtube_url

logger = logging.getLogger(__name__)

# Sensible CPU-capped format string: max 1080p height, prefers MP4
DEFAULT_FORMAT_SPEC = (
    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "best[height<=1080][ext=mp4]/"
    "best[height<=1080]/"
    "best"
)


def format_section_arg(start_sec: float, end_sec: float) -> str:
    """Format start and end timestamps into yt-dlp --download-sections string.

    Produces '*MM:SS-MM:SS' format (or '*HH:MM:SS-HH:MM:SS' for videos over 1 hour).

    Examples:
        format_section_arg(0, 45) -> '*00:00-00:45'
        format_section_arg(65, 110) -> '*01:05-01:50'
        format_section_arg(3665, 3710) -> '*01:01:05-01:01:50'

    Args:
        start_sec: Section start timestamp in seconds.
        end_sec: Section end timestamp in seconds.

    Returns:
        Formatted argument string for yt-dlp --download-sections.
    """
    s_sec = max(0.0, float(start_sec))
    e_sec = max(s_sec, float(end_sec))

    if s_sec >= 3600.0 or e_sec >= 3600.0:
        h_s, rem_s = divmod(int(s_sec), 3600)
        m_s, sec_s = divmod(rem_s, 60)
        h_e, rem_e = divmod(int(e_sec), 3600)
        m_e, sec_e = divmod(rem_e, 60)
        return f"*{h_s:02d}:{m_s:02d}:{sec_s:02d}-{h_e:02d}:{m_e:02d}:{sec_e:02d}"

    m_s, sec_s = divmod(int(s_sec), 60)
    m_e, sec_e = divmod(int(e_sec), 60)
    return f"*{m_s:02d}:{sec_s:02d}-{m_e:02d}:{sec_e:02d}"


class Downloader:
    """Downloader implementation compliant with contracts.Downloader.

    Strictly uses yt-dlp to download only precision timestamp slices without
    scraping or full-video downloading.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        yt_dlp_binary: str = "yt-dlp",
        format_spec: str = DEFAULT_FORMAT_SPEC,
        cookies_from_browser: str | None = None,
        timeout_sec: float = 180.0,
        max_retries: int = 3,
        retry_delay_sec: float = 1.0,
    ) -> None:
        """Initialize Downloader.

        Args:
            settings: Optional loaded application Settings.
            yt_dlp_binary: yt-dlp binary name or path.
            format_spec: Quality capping format selector (default <=1080p).
            cookies_from_browser: Browser name (e.g. 'chrome', 'edge', 'firefox') for cookies.
            timeout_sec: Subprocess timeout in seconds.
            max_retries: Number of retry attempts on transient network/download failures.
            retry_delay_sec: Initial delay between retries.
        """
        self.settings = settings or load_config()
        self.yt_dlp_binary = yt_dlp_binary
        self.format_spec = format_spec
        self.timeout_sec = timeout_sec
        self.max_retries = max(1, max_retries)
        self.retry_delay_sec = max(0.1, retry_delay_sec)

        # Resolve cookies from browser: argument overrides config / env
        resolved_cookies = cookies_from_browser
        if not resolved_cookies:
            # Check environment variables
            resolved_cookies = os.environ.get("YTDLP_COOKIES_FROM_BROWSER") or os.environ.get("COOKIES_FROM_BROWSER")
        if not resolved_cookies and hasattr(self.settings, "media"):
            resolved_cookies = getattr(self.settings.media, "cookies_from_browser", None)
        self.cookies_from_browser = resolved_cookies

        self._metadata_fetcher = MetadataFetcher(
            yt_dlp_binary=self.yt_dlp_binary,
            timeout_sec=min(self.timeout_sec, 30.0),
        )

    def fetch_metadata(self, url: str) -> VideoMetadata:
        """Fetch title, author, duration, and channel metadata without downloading video bytes."""
        return self._metadata_fetcher.fetch(url)

    def download_section(
        self,
        url: str,
        start_sec: float,
        end_sec: float,
        output_path: Path,
    ) -> Path:
        """Download strictly the requested timestamp slice using yt-dlp --download-sections.

        Enforces --force-keyframes-at-cuts and outputs to work/<video_id>/ atomically.

        Args:
            url: YouTube watch URL or 11-char ID.
            start_sec: Cut start in seconds.
            end_sec: Cut end in seconds.
            output_path: Destination file path.

        Returns:
            Resolved Path of the saved cut MP4.

        Raises:
            DownloadError: If yt-dlp fails after all retry attempts.
        """
        canonical_url = normalize_youtube_url(url)
        vid = extract_video_id(url)

        dest = Path(output_path).resolve()
        # Ensure parent directory exists (e.g. work/<video_id>/)
        dest.parent.mkdir(parents=True, exist_ok=True)

        section_arg = format_section_arg(start_sec, end_sec)
        unique_token = uuid4().hex[:8]
        temp_template = dest.parent / f".tmp_{unique_token}_%(ext)s"

        cmd = [
            self.yt_dlp_binary,
            "--no-playlist",
            "--no-warnings",
            "--download-sections", section_arg,
            "--force-keyframes-at-cuts",
            "-f", self.format_spec,
            "--merge-output-format", "mp4",
            "-o", str(temp_template),
            canonical_url,
        ]

        if self.cookies_from_browser:
            cmd.extend(["--cookies-from-browser", self.cookies_from_browser])

        logger.info(
            "Downloading section %s for %s (%s to %s) -> %s",
            section_arg, vid, start_sec, end_sec, dest
        )

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_sec,
                    check=False,
                )
                if proc.returncode == 0:
                    # Locate the created temporary file
                    pattern = f".tmp_{unique_token}_*"
                    matching = list(dest.parent.glob(pattern))
                    if matching:
                        downloaded_file = matching[0]
                        # Atomically rename to requested destination
                        os.replace(downloaded_file, dest)
                        logger.info("Successfully downloaded section to %s", dest)
                        return dest
                    # If temp file wasn't found under pattern, check if destination was directly written
                    if dest.is_file():
                        return dest
                    raise DownloadError(f"yt-dlp exited with 0 but output file matching {pattern} was not found")

                err_msg = (proc.stderr or proc.stdout or "").strip()
                raise DownloadError(f"yt-dlp exited with code {proc.returncode}: {err_msg}")

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "yt-dlp download section attempt %d/%d failed: %s",
                    attempt, self.max_retries, exc
                )
                # Cleanup any leftover partial temp files
                for f in dest.parent.glob(f".tmp_{unique_token}_*"):
                    try:
                        f.unlink(missing_ok=True)
                    except OSError:
                        pass
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_sec * (2 ** (attempt - 1)))

        raise DownloadError(
            f"Failed to download section {section_arg} for {canonical_url} after {self.max_retries} attempts: {last_error}"
        ) from last_error

    def download_audio_only(
        self,
        url: str,
        output_path: Path,
    ) -> Path:
        """Download audio-only stream for Whisper transcription when captions are missing.

        Args:
            url: YouTube video URL or 11-char ID.
            output_path: Destination path for extracted audio file.

        Returns:
            Resolved Path of the downloaded audio file.

        Raises:
            DownloadError: If yt-dlp audio extraction fails.
        """
        canonical_url = normalize_youtube_url(url)
        vid = extract_video_id(url)

        dest = Path(output_path).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)

        unique_token = uuid4().hex[:8]
        temp_template = dest.parent / f".tmp_audio_{unique_token}_%(ext)s"

        cmd = [
            self.yt_dlp_binary,
            "--no-playlist",
            "--no-warnings",
            "-f", "bestaudio/best",
            "-x",
            "-o", str(temp_template),
            canonical_url,
        ]

        if self.cookies_from_browser:
            cmd.extend(["--cookies-from-browser", self.cookies_from_browser])

        logger.info("Downloading audio-only stream for %s -> %s", vid, dest)

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_sec,
                    check=False,
                )
                if proc.returncode == 0:
                    matching = list(dest.parent.glob(f".tmp_audio_{unique_token}_*"))
                    if matching:
                        downloaded_file = matching[0]
                        os.replace(downloaded_file, dest)
                        logger.info("Successfully downloaded audio to %s", dest)
                        return dest
                    if dest.is_file():
                        return dest
                    raise DownloadError("Audio file was not found after successful yt-dlp execution")

                err_msg = (proc.stderr or proc.stdout or "").strip()
                raise DownloadError(f"yt-dlp audio download failed with code {proc.returncode}: {err_msg}")

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "yt-dlp audio download attempt %d/%d failed: %s",
                    attempt, self.max_retries, exc
                )
                for f in dest.parent.glob(f".tmp_audio_{unique_token}_*"):
                    try:
                        f.unlink(missing_ok=True)
                    except OSError:
                        pass
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay_sec * (2 ** (attempt - 1)))

        raise DownloadError(
            f"Failed to download audio for {canonical_url} after {self.max_retries} attempts: {last_error}"
        ) from last_error


__all__ = [
    "DEFAULT_FORMAT_SPEC",
    "DownloadError",
    "Downloader",
    "format_section_arg",
]
