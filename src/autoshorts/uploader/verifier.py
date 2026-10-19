"""Post-upload verification polling engine for YouTube uploads (PLAN.md section 8.4)."""

from __future__ import annotations

import logging
import time

from autoshorts.core.contracts import Uploader

logger = logging.getLogger(__name__)


class YouTubeVerifier:
    """Polls uploader verification until processing succeeds, fails, or times out."""

    def __init__(self, uploader: Uploader) -> None:
        """Initialize verifier with an Uploader instance."""
        self.uploader = uploader

    def verify(self, video_id: str) -> bool:
        """Check verification once via underlying uploader."""
        return self.uploader.verify(video_id)

    def poll_until_verified(
        self,
        video_id: str,
        max_retries: int = 10,
        delay_sec: float = 3.0,
    ) -> bool:
        """Poll verification repeatedly until video processing completes.

        Args:
            video_id: YouTube 11-char video ID.
            max_retries: Maximum polling attempts (default 10).
            delay_sec: Delay between polls in seconds (default 3.0s).

        Returns:
            True if video verified successfully, False if timed out or failed.
        """
        for attempt in range(1, max_retries + 1):
            logger.debug(
                f"Polling verification for {video_id} (attempt {attempt}/{max_retries})..."
            )
            if self.uploader.verify(video_id):
                logger.info(f"Video {video_id} confirmed verified after {attempt} poll(s).")
                return True

            if attempt < max_retries:
                time.sleep(delay_sec)

        logger.warning(f"Video {video_id} failed to verify within {max_retries} attempts.")
        return False
