"""Parallel Job Execution Manager for Concurrent Multi-Video Processing.

Dispatches queued or submitted jobs into a bounded thread pool executor,
enabling parallel ingestion, slicing, transcription, compositing, and rendering
without blocking the FastAPI event loop or starving single jobs.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from autoshorts.core.models import JobStatus

if TYPE_CHECKING:
    from autoshorts.core.models import Job
    from autoshorts.pipeline import ShortsPipeline
    from autoshorts.storage.state_manager import StateManager

logger = logging.getLogger(__name__)


class ParallelJobExecutor:
    """Manages concurrent execution of AutoShorts render pipeline jobs."""

    def __init__(
        self,
        pipeline: ShortsPipeline,
        state_manager: StateManager | None = None,
        max_workers: int | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.state_manager = state_manager or pipeline.state_manager
        if max_workers is None:
            max_workers = int(os.environ.get("AUTOSHORTS_CONCURRENCY", "3"))
        self.max_workers = max(1, max_workers)
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="autoshorts-worker",
        )
        self._lock = threading.Lock()
        self._active_jobs: set[str] = set()
        self._futures: dict[str, Future[Any]] = {}

    @property
    def active_job_ids(self) -> set[str]:
        """Return the set of currently running job IDs."""
        with self._lock:
            return set(self._active_jobs)

    @property
    def active_count(self) -> int:
        """Return count of currently running jobs."""
        with self._lock:
            return len(self._active_jobs)

    def is_job_active(self, job_id: str) -> bool:
        """Check if a specific job ID is currently running."""
        with self._lock:
            return job_id in self._active_jobs

    def submit_job(
        self,
        job_or_id: Job | str,
        auto_upload: bool = False,
        immediate_upload: bool = False,
        background_category: str | None = None,
        dopamine_level: str | None = None,
        style_preset: str | None = None,
        privacy_status: str | None = None,
        on_complete: Callable[[Any], None] | None = None,
    ) -> Future[Any] | None:
        """Submit a job for concurrent background processing if not already running."""
        job_id = job_or_id.id if hasattr(job_or_id, "id") else str(job_or_id)

        with self._lock:
            if job_id in self._active_jobs:
                logger.info(
                    "Job %s is already running in parallel executor; skipping duplicate submission.",
                    job_id,
                )
                return self._futures.get(job_id)

            self._active_jobs.add(job_id)

        def _worker() -> Any:
            try:
                logger.info(
                    "Parallel worker starting job %s (concurrent active: %d/%d)",
                    job_id,
                    self.active_count,
                    self.max_workers,
                )
                result = self.pipeline.process_job(
                    job_or_id,
                    auto_upload=auto_upload,
                    immediate_upload=immediate_upload,
                    background_category=background_category,
                    dopamine_level=dopamine_level,
                    style_preset=style_preset,
                    privacy_status=privacy_status,
                )
                if on_complete:
                    try:
                        on_complete(result)
                    except Exception as exc:
                        logger.warning("on_complete callback failed for %s: %s", job_id, exc)
                return result
            except Exception:
                logger.exception("Parallel execution of job %s failed", job_id)
                raise
            finally:
                with self._lock:
                    self._active_jobs.discard(job_id)
                    self._futures.pop(job_id, None)
                logger.info(
                    "Parallel worker finished job %s (active remaining: %d)",
                    job_id,
                    self.active_count,
                )

        future = self._executor.submit(_worker)
        with self._lock:
            self._futures[job_id] = future
        return future

    def poll_queued_jobs(
        self,
        auto_upload: bool = False,
        immediate_upload: bool = False,
        background_category: str | None = None,
        dopamine_level: str | None = None,
        style_preset: str | None = None,
    ) -> list[str]:
        """Scan state database for QUEUED jobs and submit up to available worker capacity."""
        if self.state_manager is None:
            return []

        try:
            queued = self.state_manager.list_jobs(status=JobStatus.QUEUED)
        except Exception as exc:
            logger.warning("Failed listing queued jobs: %s", exc)
            return []

        submitted: list[str] = []

        with self._lock:
            available_slots = self.max_workers - len(self._active_jobs)

        if available_slots <= 0:
            return []

        for job in queued:
            if available_slots <= 0:
                break
            with self._lock:
                if job.id in self._active_jobs:
                    continue
            fut = self.submit_job(
                job,
                auto_upload=auto_upload,
                immediate_upload=immediate_upload,
                background_category=background_category,
                dopamine_level=dopamine_level,
                style_preset=style_preset,
            )
            if fut is not None:
                submitted.append(job.id)
                available_slots -= 1

        if submitted:
            logger.info(
                "Parallel executor automatically dispatched %d queued jobs: %s",
                len(submitted),
                submitted,
            )
        return submitted

    def shutdown(self, wait: bool = False) -> None:
        """Shut down the underlying executor."""
        self._executor.shutdown(wait=wait)
