"""Post-upload verification polling engine for YouTube uploads (PLAN.md section 8.4)."""

from __future__ import annotations

import logging
import time

from autoshorts.core.contracts import Uploader
from autoshorts.uploader.errors import TransientUploadError

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
            True if video verified successfully, False if timed out.

        Raises:
            ProcessingError: definitive processing failure or privacy mismatch (stops at once).
            AuthError / PermanentUploadError: non-transient errors are never retried.
        """
        # At least one check always runs; a zero/negative budget must not skip verification.
        attempts = max(1, int(max_retries))
        delay = max(0.0, float(delay_sec))
        for attempt in range(1, attempts + 1):
            logger.debug(
                f"Polling verification for {video_id} (attempt {attempt}/{max_retries})..."
            )
            try:
                if self.uploader.verify(video_id):
                    logger.info(f"Video {video_id} confirmed verified after {attempt} poll(s).")
                    return True
            except TransientUploadError as exc:
                logger.warning(f"Transient verification error for {video_id}: {exc}")
            # Any other UploadError propagates immediately: polling cannot change it.

            if attempt < attempts:
                time.sleep(delay)

        logger.warning(f"Video {video_id} failed to verify within {attempts} attempts.")
        return False
