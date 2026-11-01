"""Shared helper functions and common utilities for the ShortsPipeline package."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from autoshorts.core.job_lock import JobLockedError, job_lock
from autoshorts.core.models import Job


def _is_nonempty_file(path: Path | None) -> bool:
    """True when ``path`` names an existing, non-empty regular file."""
    if path is None:
        return False
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _job_lock_is_held(job_id: str) -> bool:
    """True when another process or thread currently holds the job's lock."""
    try:
        import autoshorts.pipeline as pipeline_mod

        with job_lock(pipeline_mod.get_work_dir() / job_id, job_id):
            return False
    except JobLockedError:
        return True


def _env_int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back to ``default`` on junk."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _upload_succeeded(result: Any) -> bool:
    """An uploader result counts as a success only if it is not flagged and has a video id."""
    if getattr(result, "success", True) is False:
        return False
    return bool(str(getattr(result, "video_id", "") or "").strip())


def _resolve_local_source(job: Job) -> Path | None:
    """Return the job's local source clip, but only when the job says it is local.

    Previously this inferred "local file" from ``Path(job.source_url).is_file()``, which
    meant a YouTube URL that happened to match a file on disk could silently be treated
    as the canonical source. Origin is now explicit via ``Job.origin_kind``.
    """
    if job.origin_kind == "youtube":
        return None

    # raw_video_path is always an explicit local path chosen by the caller.
    if job.raw_video_path:
        try:
            path = Path(job.raw_video_path)
            if path.is_file():
                return path.resolve()
        except OSError:  # pragma: no cover - malformed path
            pass

    # source_url is only usable as a local clip when it is plainly a path, never a URL.
    source = (job.source_url or "").strip()
    if source and not source.lower().startswith(("http://", "https://")):
        try:
            path = Path(source)
            if path.is_file():
                return path.resolve()
        except OSError:  # pragma: no cover - malformed path
            pass

    return None
