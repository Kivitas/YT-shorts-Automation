"""Exception hierarchy for the AutoShorts pipeline.

All domain exceptions inherit from AutoShortsError.
"""

from __future__ import annotations


class AutoShortsError(Exception):
    """Base exception for all AutoShorts errors."""

    def __init__(self, message: str, *args: object) -> None:
        super().__init__(message, *args)
        self.message = message


class ConfigError(AutoShortsError):
    """Raised when configuration validation or loading fails."""


class StateError(AutoShortsError):
    """Raised when database or state management encounters an error."""


class InvalidStateTransitionError(StateError):
    """Raised when an illegal JobStatus transition is attempted."""

    def __init__(self, current_status: str, target_status: str, reason: str | None = None) -> None:
        msg = f"Cannot transition job from '{current_status}' to '{target_status}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)
        self.current_status = current_status
        self.target_status = target_status


class PathSecurityError(AutoShortsError):
    """Raised when a path guard or directory containment check is violated."""


class CleanupError(AutoShortsError):
    """Raised when cleanup/retention routines fail."""


class ProviderError(AutoShortsError):
    """Base exception for LLM provider errors."""


class LogprobNotSupportedError(ProviderError):
    """Raised when logprob scoring is requested on a provider without logprob support."""


class ModelNotFoundError(ProviderError):
    """Raised when the specified or dynamically resolved model cannot be located."""


class ProviderRateLimitError(ProviderError):
    """Raised when provider API rate limits (HTTP 429) are encountered."""


class IngestError(AutoShortsError):
    """Base exception for video URL ingestion, discovery, and transcript errors."""


class InvalidURLError(IngestError):
    """Raised when an invalid or unsupported YouTube URL is provided."""


class MetadataFetchError(IngestError):
    """Raised when fetching video metadata via yt-dlp or oEmbed fails."""


class TranscriptNotFoundError(IngestError):
    """Raised when captions or transcripts are unavailable and audio fallback is required."""


class VideoUnavailableError(IngestError):
    """Raised when a video is unavailable, private, a live stream, or upcoming premiere."""


class DeciderError(AutoShortsError):
    """Raised when SemIf candidate evaluation or action decision fails."""


class MediaError(AutoShortsError):
    """Base exception for media processing, download, and rendering errors."""


class DownloadError(MediaError):
    """Raised when yt-dlp precision section downloading fails."""


class TranscribeError(MediaError):
    """Raised when Whisper speech recognition or word alignment fails."""


class CompositorError(MediaError):
    """Raised when FFmpeg filtergraph rendering or probe fails."""


class EncoderProbeError(MediaError):
    """Raised when probing hardware video encoders fails."""


class QuotaExhaustedError(AutoShortsError):
    """Raised when a YouTube Data API quota bucket is exhausted."""

    def __init__(
        self,
        bucket: str = "upload",
        spent: int = 100,
        limit: int = 100,
        reset_time_pt: str | None = None,
    ) -> None:
        if spent == 100 and limit == 100 and (" " in bucket or "quota" in bucket.lower()):
            msg = bucket
            self.bucket = "upload"
        else:
            msg = f"YouTube API quota bucket '{bucket}' exhausted: {spent}/{limit} calls used"
            if reset_time_pt:
                msg += f" (resets at {reset_time_pt} PT)"
            self.bucket = bucket
        super().__init__(msg)
        self.spent = spent
        self.limit = limit
        self.reset_time_pt = reset_time_pt


class SchedulerError(AutoShortsError):
    """Raised when scheduling, jitter calculation, or power state management fails."""


class UploadError(AutoShortsError):
    """Raised when YouTube API or Studio browser upload fails."""


class UploadVerificationError(UploadError):
    """Raised when post-upload verification fails or video is rejected/processing fails."""
