"""Dual-bucket YouTube Data API quota tracking and auto-throttling.

Tracks upload bucket (videos.insert, 100 calls/day) and general bucket
(videos.list, 10,000 units/day) keyed by Pacific Time date (PLAN.md section 8.1).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from autoshorts.core.config import QuotaConfig, SchedulerConfig, Settings
from autoshorts.core.state_manager import StateManager, get_current_pt_date

logger = logging.getLogger(__name__)


class QuotaTracker:
    """Manages dual quota buckets and daily upload limits for YouTube automation."""

    def __init__(
        self,
        state_manager: StateManager,
        config: Settings | None = None,
        quota_config: QuotaConfig | None = None,
        scheduler_config: SchedulerConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize QuotaTracker with state manager and configuration.

        Args:
            state_manager: Persistent SQLite StateManager instance.
            config: Full application Settings (optional).
            quota_config: Dedicated QuotaConfig (optional, extracted from config if provided).
            scheduler_config: Dedicated SchedulerConfig (optional, extracted from config if provided).
            clock: Injectable datetime provider for deterministic testing (defaults to UTC now).
        """
        self.state_manager = state_manager
        self.clock = clock or (lambda: datetime.now(UTC))
        self._api_exhausted_dates: set[str] = set()

        # Resolve quota config
        if quota_config is not None:
            self.quota_config = quota_config
        elif config is not None:
            self.quota_config = config.quota
        else:
            self.quota_config = QuotaConfig()

        # Resolve scheduler config
        if scheduler_config is not None:
            self.scheduler_config = scheduler_config
        elif config is not None:
            self.scheduler_config = config.scheduler
        else:
            self.scheduler_config = SchedulerConfig()

    @property
    def upload_bucket_daily(self) -> int:
        """Total daily calls allocated to the upload bucket (default 100)."""
        return self.quota_config.upload_bucket_daily

    @property
    def general_bucket_daily(self) -> int:
        """Total daily units allocated to the general bucket (default 10000)."""
        return self.quota_config.general_bucket_daily

    @property
    def reserve_retries(self) -> int:
        """Calls reserved strictly for retries in upload bucket (default 50)."""
        return self.quota_config.reserve_retries

    @property
    def daily_upload_cap(self) -> int:
        """Our internal ceiling for successful daily uploads (default 50)."""
        return self.scheduler_config.daily_upload_cap

    @property
    def stop_threshold_units(self) -> int:
        """General bucket reserve safety buffer (default 100 units)."""
        return self.quota_config.stop_threshold_units

    @property
    def fallback_mode(self) -> Literal["queue_next_day", "studio", "local"]:
        """Configured fallback behavior upon quota exhaustion."""
        return self.quota_config.exhaustion_fallback

    def mark_upload_exhausted(self, date_pt: str | None = None) -> None:
        """Flag quota or channel limit exhausted without corrupting actual spend counters."""
        target_date = date_pt or self.get_current_pt_date()
        self._api_exhausted_dates.add(target_date)
        logger.warning(
            f"Upload quota or channel daily limit flagged as exhausted for {target_date}."
        )

    def reset_exhaustion_flag(self, date_pt: str | None = None) -> None:
        """Clear the exhaustion flag (e.g. for testing or manual user reset)."""
        target_date = date_pt or self.get_current_pt_date()
        self._api_exhausted_dates.discard(target_date)

    def get_current_pt_date(self) -> str:
        """Return the current Pacific Time date string (YYYY-MM-DD) based on clock."""
        return get_current_pt_date(self.clock())

    # -------------------------------------------------------------------------
    # Spend & Balance Queries
    # -------------------------------------------------------------------------

    def get_upload_spend(self, date_pt: str | None = None) -> int:
        """Return total calls spent from the upload bucket on given PT date."""
        target_date = date_pt or self.get_current_pt_date()
        return self.state_manager.get_quota_spend("upload", target_date)

    def get_general_spend(self, date_pt: str | None = None) -> int:
        """Return total units spent from the general bucket on given PT date."""
        target_date = date_pt or self.get_current_pt_date()
        return self.state_manager.get_quota_spend("general", target_date)

    def get_upload_remaining(self, date_pt: str | None = None) -> int:
        """Return remaining calls available in the upload bucket today."""
        spent = self.get_upload_spend(date_pt)
        return max(0, self.upload_bucket_daily - spent)

    def get_general_remaining(self, date_pt: str | None = None) -> int:
        """Return remaining units available in the general bucket today."""
        spent = self.get_general_spend(date_pt)
        return max(0, self.general_bucket_daily - spent)

    def get_completed_uploads_today(self, date_pt: str | None = None) -> int:
        """Return count of successful uploads on given PT date."""
        target_date = date_pt or self.get_current_pt_date()
        return self.state_manager.count_uploads_for_pt_date(target_date)

    # -------------------------------------------------------------------------
    # Budget Authorization Checks
    # -------------------------------------------------------------------------

    def can_upload(self, is_retry: bool = False, date_pt: str | None = None) -> bool:
        """Check if an upload call is permitted within caps and bucket budget.

        Args:
            is_retry: True if this is a retry attempt of a failed upload.
                      Retries may consume into the reserve headroom.
            date_pt: Optional explicit PT date. Defaults to current PT date.

        Returns:
            True if upload is permitted, False otherwise.
        """
        target_date = date_pt or self.get_current_pt_date()
        if target_date in self._api_exhausted_dates:
            logger.warning(
                f"Upload quota / channel limit is currently marked exhausted for PT date {target_date}."
            )
            return False

        upload_spend = self.get_upload_spend(target_date)
        completed_today = self.get_completed_uploads_today(target_date)

        # 1. Enforce hard daily upload cap (completed uploads cannot exceed cap)
        if completed_today >= self.daily_upload_cap:
            logger.warning(
                f"Daily upload cap reached for PT date {target_date} "
                f"({completed_today}/{self.daily_upload_cap})."
            )
            return False

        # 2. Upload bucket check
        if is_retry:
            # Retries can use the full upload bucket (up to upload_bucket_daily)
            if upload_spend >= self.upload_bucket_daily:
                logger.warning(
                    f"Upload bucket completely exhausted ({upload_spend}/{self.upload_bucket_daily})."
                )
                return False
            return True
        else:
            # Fresh scheduled uploads must NOT eat into the reserved retry headroom
            fresh_budget = self.upload_bucket_daily - self.reserve_retries
            if upload_spend >= fresh_budget:
                logger.warning(
                    f"Fresh upload budget reached ({upload_spend}/{fresh_budget}); "
                    f"holding {self.reserve_retries} calls in reserve for retries."
                )
                return False
            return True

    def can_verify(self, units: int = 1, date_pt: str | None = None) -> bool:
        """Check if general bucket has budget for verification check (videos.list).

        Preserves stop_threshold_units safety margin.
        """
        target_date = date_pt or self.get_current_pt_date()
        spend = self.get_general_spend(target_date)
        allowed_max = self.general_bucket_daily - self.stop_threshold_units
        return (spend + units) <= allowed_max

    def is_throttled(self) -> bool:
        """True if either bucket or daily cap prevents further normal upload operations."""
        return not self.can_upload(is_retry=False)

    # -------------------------------------------------------------------------
    # Record Expenditure
    # -------------------------------------------------------------------------

    def record_upload(self, units: int = 1) -> int:
        """Record consumption of upload bucket calls (videos.insert)."""
        now = self.clock()
        new_spend = self.state_manager.record_quota_spend(
            bucket="upload",
            units=units,
            when=now,
        )
        logger.info(
            f"Recorded {units} call(s) to upload bucket. New daily total: "
            f"{new_spend}/{self.upload_bucket_daily}."
        )
        return new_spend

    def record_general(self, units: int = 1) -> int:
        """Record consumption of general bucket units (videos.list verification)."""
        now = self.clock()
        new_spend = self.state_manager.record_quota_spend(
            bucket="general",
            units=units,
            when=now,
        )
        logger.debug(
            f"Recorded {units} unit(s) to general bucket. New daily total: "
            f"{new_spend}/{self.general_bucket_daily}."
        )
        return new_spend

    # -------------------------------------------------------------------------
    # Status Summary
    # -------------------------------------------------------------------------

    def get_status(self, date_pt: str | None = None) -> dict:
        """Return a structured dictionary of quota usage and throttle status."""
        target_date = date_pt or self.get_current_pt_date()
        upload_spent = self.get_upload_spend(target_date)
        general_spent = self.get_general_spend(target_date)
        completed = self.get_completed_uploads_today(target_date)

        return {
            "date_pt": target_date,
            "upload_bucket": {
                "daily_allocation": self.upload_bucket_daily,
                "spent": upload_spent,
                "remaining": max(0, self.upload_bucket_daily - upload_spent),
                "reserve_retries": self.reserve_retries,
                "fresh_budget": max(0, self.upload_bucket_daily - self.reserve_retries),
                "can_upload_fresh": self.can_upload(is_retry=False, date_pt=target_date),
                "can_upload_retry": self.can_upload(is_retry=True, date_pt=target_date),
            },
            "general_bucket": {
                "daily_allocation": self.general_bucket_daily,
                "spent": general_spent,
                "remaining": max(0, self.general_bucket_daily - general_spent),
                "stop_threshold": self.stop_threshold_units,
                "can_verify": self.can_verify(units=1, date_pt=target_date),
            },
            "daily_cap": {
                "cap": self.daily_upload_cap,
                "completed": completed,
                "remaining": max(0, self.daily_upload_cap - completed),
            },
            "is_throttled": self.is_throttled(),
            "fallback_mode": self.fallback_mode,
        }
