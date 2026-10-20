"""Offline-Aware and Wake-Safe Spread Scheduler for AutoShorts."""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from autoshorts.core.config import SchedulerConfig, Settings
from autoshorts.core.contracts import Cleaner, Uploader
from autoshorts.core.exceptions import AutoShortsError
from autoshorts.core.models import JobStatus, UploadResult
from autoshorts.core.resilience import (
    OfflineRecoveryManager,
    classify_network_or_outage_error,
    probe_connectivity,
)
from autoshorts.core.state_manager import StateManager
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.power_management import allow_system_sleep, prevent_system_sleep
from autoshorts.scheduler.scheduler import (
    SchedulerCapError,
    SchedulerQuotaError,
    SchedulerRateLimitError,
)
from autoshorts.scheduler.scheduler import (
    SpreadScheduler as BaseSpreadScheduler,
)
from autoshorts.scheduler.slot_generator import SlotGenerator

logger = logging.getLogger(__name__)


class SpreadScheduler(BaseSpreadScheduler):
    """Offline-aware, sleep-recovering, and YouTube-outage-resilient SpreadScheduler."""

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
        check_connectivity: bool = True,
        recovery_manager: OfflineRecoveryManager | None = None,
    ) -> None:
        super().__init__(
            state_manager=state_manager,
            quota_tracker=quota_tracker,
            uploader=uploader,
            config=config,
            scheduler_config=scheduler_config,
            clock=clock,
            rng=rng,
            state_mgr=state_mgr,
            cleaner=cleaner,
        )
        self.check_connectivity = check_connectivity
        self.recovery_manager = recovery_manager or OfflineRecoveryManager()
        self._last_tick_time: datetime | None = None

        # Check for overdue slots on startup so wake-up never bursts
        self._check_and_recover_on_startup()

    def _check_and_recover_on_startup(self) -> None:
        """Stagger overdue slots that expired while PC was offline before scheduler started."""
        try:
            now = self.clock()
            min_gap = getattr(self.config, "min_gap_minutes", 12)
            self.recovery_manager.recover_overdue_scheduled_slots(
                state_manager=self.state_manager,
                min_gap_minutes=min_gap,
                now_utc=now,
            )
        except Exception as e:
            logger.debug(f"Startup overdue check skipped or failed: {e}")

    def defer_slot(
        self,
        slot: dict[str, Any],
        defer_minutes: int = 10,
        reason: str = "transient_outage",
        now: datetime | None = None,
    ) -> datetime:
        """Defer a scheduled slot by defer_minutes, keeping it safely in SCHEDULED status."""
        current_time = now or self.clock()
        new_time = current_time + timedelta(minutes=defer_minutes)
        job_id = slot["job_id"]

        tags_list = slot["tags"].split(",") if slot.get("tags") else []
        self.state_manager.enqueue_for_upload(
            job_id=job_id,
            video_path=slot["video_path"],
            title=slot["title"],
            description=slot["description"],
            scheduled_time=new_time,
            slot_jitter_minutes=slot.get("slot_jitter_minutes", 0),
            privacy_status=slot.get("privacy_status", "unlisted"),
            tags=tags_list,
        )

        job = self.state_manager.get_job(job_id)
        if job:
            job.status = JobStatus.SCHEDULED
            job.error_message = f"Deferred by {defer_minutes}m: {reason}"
            self.state_manager.save_job(job)

        logger.info(
            f"Deferred slot for job {job_id} to {new_time.isoformat()} (+{defer_minutes}m) due to {reason}."
        )
        return new_time

    def sanitize_missed_slots(self, now: datetime | None = None) -> int:
        """Identify overdue slots and reschedule without bursting."""
        current_time = now or self.clock()
        if getattr(self.config, "catch_up_policy", "reschedule") == "drop_expired":
            return super().sanitize_missed_slots(current_time)

        min_gap = getattr(self.config, "min_gap_minutes", 12)
        recovered = self.recovery_manager.recover_overdue_scheduled_slots(
            state_manager=self.state_manager,
            min_gap_minutes=min_gap,
            now_utc=current_time,
        )
        return len(recovered)

    def run_tick(self) -> UploadResult | None:
        """Execute a single scheduler loop iteration with sleep detection and outage resilience."""
        # 1. Check if paused
        if self.is_paused():
            allow_system_sleep()
            return None

        now = self.clock()

        # Check sleep / clock jump
        if self._last_tick_time is not None:
            jump = self.recovery_manager.detect_clock_jump_or_sleep(
                last_tick_utc=self._last_tick_time,
                now_utc=now,
            )
            if jump["jump_detected"]:
                logger.warning(
                    f"Clock jump / sleep detected ({jump['elapsed_sec']:.1f}s elapsed). "
                    "Recovering overdue scheduled slots."
                )
                self.sanitize_missed_slots(now)
        else:
            # First tick: recover any overdue slots
            self.sanitize_missed_slots(now)

        self._last_tick_time = now

        pending = self.state_manager.get_pending_scheduled_slots()
        if not pending:
            allow_system_sleep()
            return None

        # 2. Check active daytime window
        if not self.is_within_active_window(now):
            logger.debug("Outside active daytime window; idling.")
            allow_system_sleep()
            return None

        # 3. Inside active window with pending slots: prevent PC sleep
        if self.config.prevent_sleep:
            prevent_system_sleep()

        # Re-fetch pending slots after sanitization
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
            logger.warning("Daily upload cap reached for quota day. Holding queue.")
            return None

        if not self.check_rolling_hour_limit(now):
            logger.info("Rolling hourly rate limit reached. Deferring dispatch.")
            return None

        if not self.quota_tracker.can_upload():
            upload_cfg = self.upload_config
            fallback_mode = getattr(upload_cfg, "quota_exhausted_fallback", "studio")
            if fallback_mode != "studio":
                logger.warning(
                    f"Upload bucket budget unavailable. Fallback: {self.quota_tracker.fallback_mode}."
                )
                return None
            logger.info(
                "Upload bucket quota exhausted, but fallback is 'studio'. Proceeding with Studio dispatch."
            )

        if not self.uploader:
            logger.error("No Uploader configured for scheduler. Cannot dispatch slot.")
            return None

        # Check network & outage resilience before dispatching
        upload_cfg = self.upload_config
        upload_mode = getattr(upload_cfg, "mode", "api")

        if upload_mode != "local" and self.check_connectivity:
            connectivity = probe_connectivity()
            if connectivity.status_mode in ("pc_offline", "youtube_outage"):
                logger.warning(
                    f"Connectivity check failed before dispatch ({connectivity.status_mode}): {connectivity.message}. "
                    f"Deferring slot {next_slot['job_id']} by 10 minutes."
                )
                self.defer_slot(
                    next_slot, defer_minutes=10, reason=connectivity.status_mode, now=now
                )
                return None

        # Dispatch the upload slot
        return self._dispatch_slot(next_slot)

    def _dispatch_slot(self, slot: dict[str, Any]) -> UploadResult | None:
        """Internal worker method to upload and record results for a slot with transient error resilience."""
        try:
            return super()._dispatch_slot(slot)
        except Exception as e:
            # Check if this was a transient network or YouTube outage error
            outage_info = classify_network_or_outage_error(e)
            if outage_info["is_transient_outage"]:
                logger.warning(
                    f"Upload encountered transient outage ({outage_info['outage_type']}): "
                    f"{outage_info['user_message']}. Deferring slot {slot['job_id']} by 10 minutes."
                )
                self.defer_slot(
                    slot=slot,
                    defer_minutes=10,
                    reason=outage_info["outage_type"],
                    now=self.clock(),
                )
                return None

            # Non-transient failure: re-raise (base class already set status to FAILED)
            raise

    def upload_now(self, job_id: str) -> UploadResult:
        """Force-dispatch a queued upload immediately, respecting outage resilience."""
        upload_cfg = self.upload_config
        upload_mode = getattr(upload_cfg, "mode", "api")

        if upload_mode != "local" and self.check_connectivity:
            connectivity = probe_connectivity()
            if connectivity.status_mode in ("pc_offline", "youtube_outage"):
                pending = self.state_manager.get_pending_scheduled_slots()
                slot = next((s for s in pending if s["job_id"] == job_id), None)
                if slot:
                    self.defer_slot(slot, defer_minutes=10, reason=connectivity.status_mode)
                raise AutoShortsError(
                    f"Cannot upload now: Network/YouTube outage ({connectivity.status_mode}): {connectivity.message}"
                )

        result = super().upload_now(job_id)
        if result is None:
            raise AutoShortsError("Upload was deferred due to transient network outage.")
        return result


__all__ = [
    "SchedulerCapError",
    "SchedulerQuotaError",
    "SchedulerRateLimitError",
    "SlotGenerator",
    "SpreadScheduler",
]
