"""Daily Cap and Persistent Spread Scheduler for AutoShorts.

Enforces 50 Shorts/day cap, rolling max 7 uploads/hour limit, uniform random gaps,
active daytime window, sleep prevention, and catch-up rescheduling without bursting
(PLAN.md section 8.2 & 8.3).
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from autoshorts.core.config import SchedulerConfig, Settings
from autoshorts.core.contracts import Cleaner, Uploader
from autoshorts.core.exceptions import AutoShortsError
from autoshorts.core.models import JobStatus, UploadResult
from autoshorts.core.state_manager import StateManager
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.power_management import allow_system_sleep, prevent_system_sleep
from autoshorts.scheduler.slot_generator import SlotGenerator

logger = logging.getLogger(__name__)


class SchedulerRateLimitError(AutoShortsError):
    """Raised when an operation would violate the rolling hourly rate limit."""


class SchedulerCapError(AutoShortsError):
    """Raised when an operation would violate the daily upload ceiling."""


class SchedulerQuotaError(AutoShortsError):
    """Raised when an operation is attempted without available quota."""


class SpreadScheduler:
    """Persistent upload pacing and dispatch engine."""

    def __init__(
        self,
        state_manager: StateManager | None = None,
        quota_tracker: QuotaTracker | None = None,
        uploader: Uploader | None = None,
        config: Settings | None = None,
        scheduler_config: SchedulerConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        rng: random.Random | None = None,
        state_mgr: StateManager | None = None,
        cleaner: Cleaner | None = None,
    ) -> None:
        """Initialize SpreadScheduler.

        Args:
            state_manager: SQLite StateManager for persistent queue storage.
            quota_tracker: Dual-bucket QuotaTracker for quota accounting.
            uploader: Configured Uploader implementation.
            config: Full application Settings.
            scheduler_config: Dedicated SchedulerConfig (optional).
            clock: Injectable clock returning datetime (defaults to UTC now).
            rng: Injectable random.Random instance for deterministic jitter.
            state_mgr: Alias for state_manager.
            cleaner: Optional transactional cleaner for post-upload deletion.
        """
        resolved_mgr = state_manager or state_mgr
        if resolved_mgr is None:
            raise ValueError("state_manager must be provided to SpreadScheduler")
        self.state_manager = resolved_mgr
        if quota_tracker is None:
            raise ValueError("quota_tracker must be provided to SpreadScheduler")
        self.quota_tracker = quota_tracker
        self.uploader = uploader
        self.cleaner = cleaner
        self.clock = clock or (lambda: datetime.now(UTC))
        self.rng = rng or random.Random()

        if scheduler_config is not None:
            self.config = scheduler_config
        elif config is not None:
            self.config = config.scheduler
        else:
            self.config = SchedulerConfig()

        self.slot_generator = SlotGenerator(
            config=self.config,
            min_gap_minutes=self.config.min_gap_minutes,
            max_gap_minutes=self.config.max_gap_minutes,
            active_window=self.config.active_window,
            rng=self.rng,
            clock=self.clock,
        )

    # -------------------------------------------------------------------------
    # Core Constraints Checks
    # -------------------------------------------------------------------------

    def check_rolling_hour_limit(self, now: datetime | None = None) -> bool:
        """Return True if completed uploads in the preceding 60 minutes are < max_per_rolling_hour (7)."""
        current_time = now or self.clock()
        one_hour_ago = current_time - timedelta(hours=1)
        recent_count = self.state_manager.count_uploads_since(one_hour_ago)
        return recent_count < self.config.max_per_rolling_hour

    def check_daily_cap(self, date_pt: str | None = None) -> bool:
        """Return True if uploads on current Pacific Time date are < daily_upload_cap (50)."""
        completed = self.quota_tracker.get_completed_uploads_today(date_pt)
        return completed < self.config.daily_upload_cap

    def is_within_active_window(self, dt: datetime | None = None) -> bool:
        """Return True if given timestamp falls within active daytime window."""
        return self.slot_generator.is_within_active_window(dt or self.clock())

    # -------------------------------------------------------------------------
    # Queue Management
    # -------------------------------------------------------------------------

    def enqueue(
        self,
        job_id: str,
        video_path: Path | str,
        title: str,
        description: str,
        tags: list[str] | None = None,
        privacy_status: str = "unlisted",
        target_time: datetime | None = None,
    ) -> datetime:
        """Enqueue a rendered Short for upload.

        If target_time is None, computes a jittered slot after the last pending slot or now.
        Returns the assigned scheduled_time.
        """
        now = self.clock()
        if target_time is None:
            pending = self.state_manager.get_pending_scheduled_slots()
            if pending:
                # Anchor after the last scheduled slot
                last_slot_str = pending[-1]["scheduled_time"]
                last_time = datetime.fromisoformat(last_slot_str)
                scheduled_time = self.slot_generator.next_slot_after(last_time)
            else:
                scheduled_time = self.slot_generator.next_slot_after(now)
        else:
            scheduled_time = target_time

        jitter_min = int((self.config.max_gap_minutes + self.config.min_gap_minutes) / 2)
        self.state_manager.enqueue_for_upload(
            job_id=job_id,
            video_path=str(video_path),
            title=title,
            description=description,
            scheduled_time=scheduled_time,
            slot_jitter_minutes=jitter_min,
            privacy_status=privacy_status,
            tags=tags,
        )
        logger.info(f"Enqueued job {job_id} for upload at {scheduled_time.isoformat()}.")
        return scheduled_time

    # -------------------------------------------------------------------------
    # Controls (Pause, Resume, Upload Now)
    # -------------------------------------------------------------------------

    def is_paused(self) -> bool:
        """Return True if scheduler is paused."""
        return self.state_manager.is_scheduler_paused()

    def pause(self) -> None:
        """Pause scheduler and release OS sleep lock."""
        self.state_manager.set_scheduler_paused(True)
        allow_system_sleep()
        logger.info("Scheduler paused; system sleep allowed.")

    def resume(self) -> None:
        """Resume scheduler."""
        self.state_manager.set_scheduler_paused(False)
        logger.info("Scheduler resumed.")

    def upload_now(self, job_id: str) -> UploadResult:
        """Force-dispatch a queued upload immediately, strictly respecting caps."""
        now = self.clock()

        # Enforce rate limits and caps
        if not self.check_daily_cap():
            raise SchedulerCapError(
                f"Cannot upload now: Daily cap ({self.config.daily_upload_cap}) reached for today."
            )

        if not self.check_rolling_hour_limit(now):
            raise SchedulerRateLimitError(
                f"Cannot upload now: Rolling hourly limit ({self.config.max_per_rolling_hour}/hr) reached."
            )

        if not self.quota_tracker.can_upload():
            raise SchedulerQuotaError(
                "Cannot upload now: Upload quota bucket exhausted or reserved for retries."
            )

        if not self.uploader:
            raise AutoShortsError("No Uploader configured for scheduler.")

        # Find slot in database
        pending = self.state_manager.get_pending_scheduled_slots()
        slot = next((s for s in pending if s["job_id"] == job_id), None)
        if not slot:
            raise AutoShortsError(f"Job {job_id} not found in pending scheduler queue.")

        return self._dispatch_slot(slot)

    # -------------------------------------------------------------------------
    # Catch-Up Logic After PC Sleep / Wake
    # -------------------------------------------------------------------------

    def sanitize_missed_slots(self, now: datetime | None = None) -> int:
        """Identify slots that expired in the past and reschedule without bursting.

        Returns:
            Number of overdue slots adjusted.
        """
        current_time = now or self.clock()
        pending = self.state_manager.get_pending_scheduled_slots()
        if not pending:
            return 0

        # Define expiration threshold: slot is > 15 minutes in the past
        threshold = current_time - timedelta(minutes=15)
        overdue_slots = []
        for s in pending:
            slot_time = datetime.fromisoformat(s["scheduled_time"])
            if slot_time.tzinfo is None and current_time.tzinfo is not None:
                slot_time = slot_time.replace(tzinfo=UTC)
            elif slot_time.tzinfo is not None and current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=UTC)
            if slot_time < threshold:
                overdue_slots.append(s)

        if not overdue_slots:
            return 0

        logger.warning(
            f"Detected {len(overdue_slots)} overdue slot(s) after system downtime or sleep. "
            f"Applying catch-up policy '{self.config.catch_up_policy}'."
        )

        if self.config.catch_up_policy == "drop_expired":
            for s in overdue_slots:
                self.state_manager.update_scheduled_slot_status(s["job_id"], "SKIPPED")
            return len(overdue_slots)

        # Reschedule policy: spread overdue items into future slots starting from now + random gap
        rescheduled_anchor = current_time
        for s in overdue_slots:
            next_time = self.slot_generator.next_slot_after(rescheduled_anchor)
            # Re-enqueue with updated scheduled_time
            tags_list = s["tags"].split(",") if s.get("tags") else []
            self.state_manager.enqueue_for_upload(
                job_id=s["job_id"],
                video_path=s["video_path"],
                title=s["title"],
                description=s["description"],
                scheduled_time=next_time,
                slot_jitter_minutes=s["slot_jitter_minutes"],
                privacy_status=s["privacy_status"],
                tags=tags_list,
            )
            rescheduled_anchor = next_time

        return len(overdue_slots)

    # -------------------------------------------------------------------------
    # Scheduler Tick
    # -------------------------------------------------------------------------

    def run_tick(self) -> UploadResult | None:
        """Execute a single scheduler loop iteration.

        Checks power management, active window, rate limits, quota, and dispatches due slot.
        """
        # 1. Check if paused
        if self.is_paused():
            allow_system_sleep()
            return None

        pending = self.state_manager.get_pending_scheduled_slots()
        if not pending:
            allow_system_sleep()
            return None

        now = self.clock()

        # 2. Check active daytime window
        if not self.is_within_active_window(now):
            logger.debug("Outside active daytime window; idling.")
            allow_system_sleep()
            return None

        # 3. Inside active window with pending slots: prevent PC sleep
        if self.config.prevent_sleep:
            prevent_system_sleep()

        # 4. Handle any overdue slots (catch-up logic after PC sleep)
        self.sanitize_missed_slots(now)

        # Refresh pending slots after sanitization
        pending = self.state_manager.get_pending_scheduled_slots()
        if not pending:
            return None

        next_slot = pending[0]
        slot_time = datetime.fromisoformat(next_slot["scheduled_time"])
        if slot_time.tzinfo is None and now.tzinfo is not None:
            slot_time = slot_time.replace(tzinfo=UTC)
        elif slot_time.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        # Check if earliest slot is due
        if now < slot_time:
            logger.debug(f"Earliest slot {next_slot['job_id']} not due yet ({slot_time} > {now}).")
            return None

        # Slot is due! Check constraints before dispatch
        if not self.check_daily_cap():
            logger.warning("Daily upload cap (50) reached for quota day. Holding queue.")
            return None

        if not self.check_rolling_hour_limit(now):
            logger.info("Rolling hourly rate limit (7/hr) reached. Deferring dispatch.")
            return None

        if not self.quota_tracker.can_upload():
            logger.warning(
                f"Upload bucket budget unavailable. Fallback: {self.quota_tracker.fallback_mode}."
            )
            return None

        if not self.uploader:
            logger.error("No Uploader configured for scheduler. Cannot dispatch slot.")
            return None

        # Dispatch the upload
        return self._dispatch_slot(next_slot)

    def _dispatch_slot(self, slot: dict[str, Any]) -> UploadResult:
        """Internal worker method to upload and record results for a slot."""
        job_id = slot["job_id"]
        video_path = Path(slot["video_path"])
        title = slot["title"]
        description = slot["description"]
        tags = slot["tags"].split(",") if slot.get("tags") else []
        privacy = slot["privacy_status"]

        self.state_manager.update_scheduled_slot_status(job_id, "UPLOADING")
        job = self.state_manager.get_job(job_id)
        if job:
            if job.status == JobStatus.SCHEDULED:
                self.state_manager.update_job_status(job_id, JobStatus.UPLOADING)
            elif job.status != JobStatus.UPLOADING:
                # Force status to SCHEDULED then UPLOADING if test or queue set it otherwise
                job.status = JobStatus.SCHEDULED
                self.state_manager.save_job(job)
                self.state_manager.update_job_status(job_id, JobStatus.UPLOADING)

        try:
            result = self.uploader.upload(
                path=video_path,
                title=title,
                description=description,
                tags=tags,
                privacy=privacy,
            )

            # Record quota spend if uploader does not have its own tracker
            if getattr(self.uploader, "quota_tracker", None) is None:
                self.quota_tracker.record_upload(1)

            # Verify if applicable
            job = self.state_manager.get_job(job_id)
            if job and job.status == JobStatus.UPLOADING:
                self.state_manager.update_job_status(job_id, JobStatus.VERIFYING)

            verified = self.uploader.verify(result.video_id)
            if verified:
                result.verified = True
                if getattr(self.uploader, "quota_tracker", None) is None:
                    self.quota_tracker.record_general(1)

            # Mark slot and job completed
            self.state_manager.update_scheduled_slot_status(
                job_id=job_id,
                status="COMPLETED",
                youtube_video_id=result.video_id,
                quota_spent=1,
            )
            job = self.state_manager.get_job(job_id)
            if job and job.status == JobStatus.VERIFYING:
                self.state_manager.update_job_status(job_id, JobStatus.COMPLETED)
            elif job and job.status != JobStatus.COMPLETED:
                job.status = JobStatus.COMPLETED
                self.state_manager.save_job(job)

            # Post-upload cleanup: delete video files from PC once verified
            if verified and self.cleaner:
                try:
                    self.cleaner.cleanup_job(job_id, verified=True, upload_result=result)
                    job = self.state_manager.get_job(job_id)
                    if job and job.status == JobStatus.COMPLETED:
                        self.state_manager.update_job_status(job_id, JobStatus.CLEANED)
                    elif job and job.status != JobStatus.CLEANED:
                        job.status = JobStatus.CLEANED
                        self.state_manager.save_job(job)
                    logger.info(f"Post-upload cleanup completed for job {job_id}.")
                except Exception as c_err:
                    logger.warning(
                        f"Post-upload cleanup encountered error for job {job_id}: {c_err}"
                    )

            logger.info(f"Successfully uploaded and verified job {job_id} as {result.video_id}.")
            return result
        except Exception as e:
            self.state_manager.update_scheduled_slot_status(job_id, "FAILED")
            job = self.state_manager.get_job(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                self.state_manager.save_job(job)
            logger.error(f"Failed to upload scheduled slot for job {job_id}: {e}")
            raise
