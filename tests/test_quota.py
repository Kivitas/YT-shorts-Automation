"""Unit tests for dual-bucket QuotaTracker and Pacific Time reset logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autoshorts.core.config import QuotaConfig, SchedulerConfig
from autoshorts.core.state_manager import StateManager, get_pt_timezone
from autoshorts.quota.quota_tracker import QuotaTracker


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_state.db"


@pytest.fixture
def state_mgr(temp_db: Path) -> StateManager:
    return StateManager(temp_db)


def test_dual_bucket_accounting(state_mgr: StateManager):
    """Test that upload and general buckets track separately."""
    fixed_time = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: fixed_time)

    # Initially empty
    assert tracker.get_upload_spend() == 0
    assert tracker.get_general_spend() == 0
    assert tracker.get_upload_remaining() == 100
    assert tracker.get_general_remaining() == 10000

    # Record upload spend
    tracker.record_upload(1)
    tracker.record_upload(1)
    assert tracker.get_upload_spend() == 2
    assert tracker.get_upload_remaining() == 98
    assert tracker.get_general_spend() == 0  # General bucket untouched

    # Record general spend
    tracker.record_general(5)
    assert tracker.get_general_spend() == 5
    assert tracker.get_general_remaining() == 9995
    assert tracker.get_upload_spend() == 2  # Upload bucket untouched


def test_reserve_retries_and_fresh_cap(state_mgr: StateManager):
    """Test fresh uploads halt at (100 - reserve_retries = 50), while retries can proceed up to 100."""
    fixed_time = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)
    quota_cfg = QuotaConfig(upload_bucket_daily=100, reserve_retries=50)
    sched_cfg = SchedulerConfig(daily_upload_cap=50)
    tracker = QuotaTracker(
        state_mgr,
        quota_config=quota_cfg,
        scheduler_config=sched_cfg,
        clock=lambda: fixed_time,
    )

    # Spend 49 fresh uploads
    for _ in range(49):
        tracker.record_upload(1)

    assert tracker.can_upload(is_retry=False) is True
    assert tracker.can_upload(is_retry=True) is True

    # Spend 50th
    tracker.record_upload(1)
    assert tracker.get_upload_spend() == 50

    # Fresh uploads must now be blocked to preserve reserve_retries headroom
    assert tracker.can_upload(is_retry=False) is False
    assert tracker.is_throttled() is True

    # But retries are still allowed!
    assert tracker.can_upload(is_retry=True) is True

    # Spend up to 100 with retries
    for _ in range(50):
        tracker.record_upload(1)

    assert tracker.get_upload_spend() == 100
    assert tracker.can_upload(is_retry=False) is False
    assert tracker.can_upload(is_retry=True) is False


def test_daily_upload_cap_respected(state_mgr: StateManager):
    """Test that completed uploads reaching daily_upload_cap (50) blocks further uploads."""
    fixed_time = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)
    sched_cfg = SchedulerConfig(daily_upload_cap=5)  # small cap for testing
    quota_cfg = QuotaConfig(upload_bucket_daily=100, reserve_retries=50)
    tracker = QuotaTracker(
        state_mgr,
        quota_config=quota_cfg,
        scheduler_config=sched_cfg,
        clock=lambda: fixed_time,
    )

    # Queue and complete 5 jobs
    from autoshorts.core.models import Job
    for i in range(5):
        job_id = f"job_{i}"
        state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=xyz"))
        state_mgr.enqueue_for_upload(
            job_id=job_id,
            video_path=f"work/{job_id}.mp4",
            title="Short",
            description="Desc",
            scheduled_time=fixed_time,
        )
        state_mgr.update_scheduled_slot_status(job_id=job_id, status="COMPLETED")

    # Completed count is now 5
    assert tracker.get_completed_uploads_today() == 5
    assert tracker.can_upload(is_retry=False) is False


def test_general_bucket_stop_threshold(state_mgr: StateManager):
    """Test general bucket stops verification when remaining falls below stop_threshold_units."""
    fixed_time = datetime(2026, 10, 19, 16, 0, tzinfo=UTC)
    quota_cfg = QuotaConfig(general_bucket_daily=1000, stop_threshold_units=100)
    tracker = QuotaTracker(state_mgr, quota_config=quota_cfg, clock=lambda: fixed_time)

    # Spend 900 units: exactly at limit
    tracker.record_general(900)
    assert tracker.get_general_spend() == 900
    assert tracker.can_verify(units=1) is False  # 900 + 1 > 1000 - 100 = 900

    tracker.state_manager.reset_quota_for_date()
    tracker.record_general(899)
    assert tracker.can_verify(units=1) is True


def test_midnight_pt_reset_and_dst(state_mgr: StateManager):
    """Test that quota counters reset across midnight Pacific Time, including Daylight Saving Time."""
    pt_tz = get_pt_timezone()

    # 1. Test normal midnight reset in PDT (Summer/Fall: UTC-7)
    # 23:59:50 PDT on 2026-10-19
    current_time = datetime(2026, 10, 19, 23, 59, 50, tzinfo=pt_tz)
    tracker = QuotaTracker(state_mgr, clock=lambda: current_time)

    tracker.record_upload(40)
    tracker.record_general(200)

    assert tracker.get_upload_spend() == 40
    assert tracker.get_general_spend() == 200
    assert tracker.get_current_pt_date() == "2026-10-19"

    # Advance clock across midnight PT by 20 seconds -> 00:00:10 PDT on 2026-10-20
    current_time = current_time + timedelta(seconds=20)
    assert tracker.get_current_pt_date() == "2026-10-20"
    assert tracker.get_upload_spend() == 0  # Automatically 0 for new date!
    assert tracker.get_general_spend() == 0
    assert tracker.get_upload_remaining() == 100

    # Old date's spend is preserved for historical auditing
    assert tracker.get_upload_spend(date_pt="2026-10-19") == 40

    # 2. Test across Daylight Saving Time transition in US Pacific (First Sunday in November)
    # In 2026, DST ends Sunday, Nov 1, 2026 (clocks fall back from 02:00 PDT to 01:00 PST)
    # Midnight PT on Nov 1 is still in PDT
    nov1_midnight = datetime(2026, 11, 1, 0, 0, 1, tzinfo=pt_tz)
    tracker = QuotaTracker(state_mgr, clock=lambda: nov1_midnight)
    assert tracker.get_current_pt_date() == "2026-11-01"
    tracker.record_upload(10)
    assert tracker.get_upload_spend() == 10

    # Advance to Nov 2 (PST, UTC-8)
    nov2_midnight = datetime(2026, 11, 2, 0, 0, 1, tzinfo=pt_tz)
    tracker = QuotaTracker(state_mgr, clock=lambda: nov2_midnight)
    assert tracker.get_current_pt_date() == "2026-11-02"
    assert tracker.get_upload_spend() == 0


def test_status_and_fallback_modes(state_mgr: StateManager):
    """Test get_status dictionary and fallback mode configuration."""
    quota_cfg = QuotaConfig(exhaustion_fallback="studio")
    tracker = QuotaTracker(state_mgr, quota_config=quota_cfg)

    status = tracker.get_status()
    assert status["fallback_mode"] == "studio"
    assert "upload_bucket" in status
    assert "general_bucket" in status
    assert "daily_cap" in status
    assert status["upload_bucket"]["daily_allocation"] == 100
    assert status["general_bucket"]["daily_allocation"] == 10000
