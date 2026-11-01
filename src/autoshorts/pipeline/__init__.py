"""End-to-End Pipeline Orchestrator for AutoShorts.

Coordinates the complete clipping lifecycle:
Input links -> Validate & Metadata -> Fetch transcript (with Whisper fallback) ->
Window slicing -> SemIf scoring & selection -> Multimodal EDL planning ->
Downloader precision cut (yt-dlp --download-sections) -> Subtitles (.ass generation) ->
FFmpeg Compositor (split-screen + dopamine effects) -> Optional preview generation ->
Enqueue to Scheduler or Immediate Upload -> Post-upload Verification -> Path-guarded Cleanup.

Includes stage artifact caching, failure resumption, state transitions, and concurrency throttling.
"""

from __future__ import annotations

import logging

from autoshorts.core.paths import get_output_dir, get_work_dir
from autoshorts.media.compositor import count_decode_errors
from autoshorts.media.dopamine_effects import pick_background_asset
from autoshorts.pipeline.common import (
    _env_int,
    _is_nonempty_file,
    _job_lock_is_held,
    _resolve_local_source,
    _upload_succeeded,
)
from autoshorts.pipeline.orchestrator import ShortsPipeline
from autoshorts.pipeline.rerender import RerenderMixin
from autoshorts.pipeline.stages_ingest import IngestMixin, generate_candidate_windows
from autoshorts.pipeline.stages_media import MediaMixin, select_gameplay_background
from autoshorts.pipeline.stages_upload import UploadMixin

logger = logging.getLogger("autoshorts.pipeline")

__all__ = [
    "IngestMixin",
    "MediaMixin",
    "RerenderMixin",
    "ShortsPipeline",
    "UploadMixin",
    "_env_int",
    "_is_nonempty_file",
    "_job_lock_is_held",
    "_resolve_local_source",
    "_upload_succeeded",
    "count_decode_errors",
    "generate_candidate_windows",
    "get_output_dir",
    "get_work_dir",
    "logger",
    "pick_background_asset",
    "select_gameplay_background",
]
