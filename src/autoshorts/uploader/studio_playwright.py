"""Playwright Studio Browser Automation Fallback Stub.

Provides an honest fallback stub for uploading via YouTube Studio web interface
using Microsoft Edge and a dedicated browser profile (PLAN.md section 8.1 & 8.4).
"""

from __future__ import annotations

import logging
from pathlib import Path

from autoshorts.core.models import UploadResult
from autoshorts.core.paths import get_data_dir

logger = logging.getLogger(__name__)


class PlaywrightStudioUploader:
    """YouTube Studio Playwright browser upload engine fallback stub.

    Uses dedicated profile in ./data/edge_profile and msedge channel.
    Marked as fallback for scenarios where API quota is exhausted or project is restricted.
    """

    def __init__(
        self,
        edge_profile_dir: Path | str | None = None,
        channel: str = "msedge",
        headless: bool = False,
    ) -> None:
        """Initialize Studio uploader stub with dedicated profile path.

        Args:
            edge_profile_dir: Path to isolated Edge automation user data directory.
            channel: Browser channel (default 'msedge').
            headless: Whether to run Edge headless (False for first-run login).
        """
        self.profile_dir = (
            Path(edge_profile_dir).resolve()
            if edge_profile_dir
            else (get_data_dir() / "edge_profile").resolve()
        )
        self.channel = channel
        self.headless = headless

    def _get_playwright(self):
        """Lazy import playwright sync API."""
        try:
            from playwright.sync_api import sync_playwright

            return sync_playwright
        except ImportError as e:
            raise ImportError(
                "Playwright is not installed. To use YouTube Studio browser automation fallback, "
                f"install playwright: 'uv pip install playwright' and 'playwright install msedge': {e}"
            ) from e

    def upload(
        self,
        path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "unlisted",
    ) -> UploadResult:
        """Upload via YouTube Studio web interface.

        Note:
            This is currently a documented architectural fallback stub.
            When triggered, raises NotImplementedError directing the user to
            configure API mode or use local export fallback.
        """
        logger.warning(
            "Playwright Studio fallback invoked. YouTube Studio browser automation "
            f"is configured for profile at {self.profile_dir} using channel '{self.channel}'."
        )
        raise NotImplementedError(
            "YouTube Studio Playwright upload fallback is currently an architectural stub. "
            "For active publishing, configure upload.mode: 'api' or use 'local' export."
        )

    def verify(self, video_id: str) -> bool:
        """Verify video processing in YouTube Studio DOM."""
        raise NotImplementedError(
            "YouTube Studio DOM verification is not implemented in this fallback stub."
        )
