"""Core Protocol interfaces defining formal contracts across AutoShorts modules.

Upcoming modules and engineers implement these interfaces in parallel:
- LLMProvider: providers/
- Decider: decision/ + semif
- Downloader: media/ (downloader.py) + discovery/
- Transcriber: media/ (whisper_aligner.py) + transcript/
- Compositor: media/ (compositor.py)
- EditPlanner: edit_planner/
- Uploader: uploader/
- Cleaner: cleanup/
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from autoshorts.core.models import (
    ClipCandidate,
    ClipSelection,
    EditDecisionList,
    UploadResult,
    VideoMetadata,
)


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM inference, zero hardcoded models, and SemIf logit evaluation."""

    @property
    def name(self) -> str:
        """Provider identifier (e.g. 'local', 'gemini', 'openrouter')."""
        ...

    @property
    def supports_logprobs(self) -> bool:
        """True if this provider supports direct logprob / logit evaluation."""
        ...

    def list_models(self) -> list[str]:
        """Dynamically list available models from local files or provider API.

        Never returns hardcoded model strings.
        """
        ...

    def complete(self, prompt: str, **kwargs: Any) -> str:
        """Generate text completion from a prompt string."""
        ...

    def score_options(
        self,
        prompt: str,
        options: Sequence[str] | Mapping[str, str],
        **kwargs: Any,
    ) -> dict[str, float]:
        """Compute categorical probability distribution across discrete option tokens.

        Returns a dictionary mapping option label (e.g. 'A', 'B') to normalized probability (sum ~ 1.0).
        Evaluated via single forward pass logits where supported, or single-token constrained decoding.
        """
        ...


@runtime_checkable
class Decider(Protocol):
    """Protocol for SemIf-driven sliding-window scoring and pipeline action orchestration."""

    def score_candidates(
        self,
        candidates: Sequence[ClipCandidate],
        instructions: str | None = None,
    ) -> list[ClipCandidate]:
        """Evaluate and rank candidate transcript windows using SemIf Softmax probabilities."""
        ...

    def select_clips(
        self,
        candidates: Sequence[ClipCandidate],
        count: int = 1,
    ) -> list[ClipSelection]:
        """Select top non-overlapping candidate segments and attach styling parameters."""
        ...

    def decide_action(
        self,
        action_key: str,
        context: Mapping[str, Any],
    ) -> str:
        """Execute a Semantic-IF decision prompt and return the chosen action identifier."""
        ...


@runtime_checkable
class Downloader(Protocol):
    """Protocol for yt-dlp non-scraping metadata and precision section cutting."""

    def fetch_metadata(self, url: str) -> VideoMetadata:
        """Fetch title, author, duration, and channel metadata via yt-dlp --dump-json or oEmbed.

        Does NOT download video media bytes.
        """
        ...

    def download_section(
        self,
        url: str,
        start_sec: float,
        end_sec: float,
        output_path: Path,
    ) -> Path:
        """Download strictly the requested timestamp slice using yt-dlp --download-sections."""
        ...

    def download_audio_only(
        self,
        url: str,
        output_path: Path,
    ) -> Path:
        """Download audio-only stream for Whisper transcription when captions are missing."""
        ...


@runtime_checkable
class Transcriber(Protocol):
    """Protocol for audio transcription and word-level alignment (faster-whisper CPU int8)."""

    def transcribe(
        self,
        audio_path: Path,
    ) -> list[dict[str, Any]]:
        """Transcribe an audio file into timed segments."""
        ...

    def align_words(
        self,
        audio_path: Path,
        text: str | None = None,
    ) -> list[dict[str, Any]]:
        """Extract precise word-level start and end timestamps for karaoke subtitle styling."""
        ...


@runtime_checkable
class EditPlanner(Protocol):
    """Protocol for AI multimodal edit planning generating an Edit Decision List (EDL)."""

    def generate_edl(
        self,
        video_clip_path: Path,
        transcript_text: str | None = None,
        instructions: str | None = None,
    ) -> EditDecisionList:
        """Analyze video clip and generate symbolic EDL (hook trims, jump cuts, zoom punch-ins)."""
        ...


@runtime_checkable
class Compositor(Protocol):
    """Protocol for deterministic FFmpeg video compositing and hardware encoding."""

    def compose(
        self,
        job_id: str,
        source_clip: Path,
        edl: EditDecisionList | None,
        background_path: Path,
        subtitles_path: Path,
        output_path: Path,
        **kwargs: Any,
    ) -> Path:
        """Render a vertical 1080x1920 Short MP4 via FFmpeg filtergraph executing the EDL."""
        ...

    def generate_preview(
        self,
        source_clip: Path,
        output_path: Path,
        **kwargs: Any,
    ) -> Path:
        """Render a rapid 540x960 low-resolution preview MP4 in <6s for dashboard scrub."""
        ...


@runtime_checkable
class Uploader(Protocol):
    """Protocol for YouTube Data API v3 and Studio Playwright upload engines."""

    def upload(
        self,
        path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "unlisted",
    ) -> UploadResult:
        """Upload master Short MP4 to YouTube."""
        ...

    def verify(
        self,
        video_id: str,
    ) -> bool:
        """Poll YouTube Data API or Studio DOM to confirm video processing succeeds."""
        ...


@runtime_checkable
class Cleaner(Protocol):
    """Protocol for transactional path-guarded file removal and safety retention."""

    def cleanup_job(
        self,
        job_id: str,
        force: bool = False,
    ) -> list[Path]:
        """Safely purge intermediate working files for a confirmed job to the Recycle Bin."""
        ...

    def assert_safe_to_delete(
        self,
        path: Path | str,
    ) -> None:
        """Validate target path is within allowed cleanup roots and not in protected directories."""
        ...
