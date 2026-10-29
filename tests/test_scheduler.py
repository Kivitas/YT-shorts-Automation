"""Unit tests for SpreadScheduler, SlotGenerator, rate-limits, and sleep prevention."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.core.config import ActiveWindowConfig, SchedulerConfig, UploadConfig
from autoshorts.core.models import Job, JobStatus, UploadResult
from autoshorts.core.state_manager import StateManager
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.power_management import allow_system_sleep, prevent_system_sleep
from autoshorts.scheduler.scheduler import (
    SchedulerRateLimitError,
    SpreadScheduler,
)
from autoshorts.scheduler.slot_generator import SlotGenerator


class FakeUploader:
    """Mock uploader for scheduler tests."""

    def __init__(self, verify_result: bool = True) -> None:
        self.upload_calls: list[dict[str, Any]] = []
        self.verify_calls: list[str] = []
        self.verify_result = verify_result

    def upload(
        self,
        path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "unlisted",
    ) -> UploadResult:
        vid_id = f"mock_{len(self.upload_calls)}"
        self.upload_calls.append(
            {
                "path": path,
                "title": title,
                "description": description,
                "tags": tags,
                "privacy": privacy,
            }
        )
        return UploadResult(
            video_id=vid_id,
            url=f"https://youtube.com/shorts/{vid_id}",
            status="uploaded",
            verified=False,
            quota_spent=1,
            privacy_status=privacy,
        )

    def verify(self, video_id: str) -> bool:
        self.verify_calls.append(video_id)
        return self.verify_result


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_state.db"


@pytest.fixture
def state_mgr(temp_db: Path) -> StateManager:
    return StateManager(temp_db)


def test_slot_generator_gap_bounds_and_randomness():
    """Verify that every generated gap is uniformly bounded between min_gap and max_gap."""
    rng = random.Random(42)
    min_gap = 5
    max_gap = 25
    generator = SlotGenerator(
        min_gap_minutes=min_gap,
        max_gap_minutes=max_gap,
        rng=rng,
    )

    base = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
    slots = generator.generate_slots(count=30, start_time=base)
    assert len(slots) == 30

    prev = base
    gaps = []
    for s in slots:
        gap_min = (s - prev).total_seconds() / 60.0
        assert min_gap <= gap_min <= max_gap, f"Gap {gap_min} outside [{min_gap}, {max_gap}]"
        gaps.append(gap_min)
        prev = s

    # Ensure gaps are not identical (uniform randomness)
    assert len({round(g, 2) for g in gaps}) > 5


def test_active_window_sizing_for_50_shorts():
    """Verify active window (08:00 to 23:59 = 16 hours) fits 50 uploads."""
    # 16 hours = 960 minutes. 960 / 50 = 19.2 minutes nominal gap, well within [5, 25].
    rng = random.Random(123)
    generator = SlotGenerator(
        min_gap_minutes=5,
        max_gap_minutes=25,
        active_window=ActiveWindowConfig(start="08:00", end="23:59"),
        rng=rng,
    )
    window_start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    slots = generator.generate_slots(count=50, start_time=window_start, respect_daily_boundary=True)
    # With random seed 123, nearly all or all 50 fit comfortably in 16 hours
    assert len(slots) >= 40, f"Expected ~50 slots to fit, got {len(slots)}"


def test_rolling_hourly_rate_limit(state_mgr: StateManager):
    """Test max 7 uploads per rolling 60 minutes limit."""
    fixed_time = datetime.now(UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: fixed_time)
    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        clock=lambda: fixed_time,
    )

    assert scheduler.check_rolling_hour_limit(fixed_time) is True

    # Complete 6 uploads within the last 60 minutes
    for i in range(6):
        job_id = f"job_recent_{i}"
        state_mgr.save_job(Job(id=job_id, source_url="https://youtu.be/1"))
        state_mgr.enqueue_for_upload(
            job_id=job_id,
            video_path=f"work/{job_id}.mp4",
            title=f"Short {i}",
            description="desc",
            scheduled_time=fixed_time - timedelta(minutes=10 * i),
        )
        state_mgr.update_scheduled_slot_status(job_id, "COMPLETED")

    # 6 completed: still allows 1 more
    assert scheduler.check_rolling_hour_limit(fixed_time) is True

    # Complete 7th upload
    job_id = "job_recent_6"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtu.be/1"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path=f"work/{job_id}.mp4",
        title="Short 6",
        description="desc",
        scheduled_time=fixed_time - timedelta(minutes=5),
    )
    state_mgr.update_scheduled_slot_status(job_id, "COMPLETED")

    # 7 completed: limit reached!
    assert scheduler.check_rolling_hour_limit(fixed_time) is False


def test_catch_up_rescheduling_after_pc_sleep(state_mgr: StateManager):
    """Test catch-up logic when PC awakens after sleep: never bursts, reschedules overdue slots."""
    # PC woke up at 14:00. Two slots were due at 10:00 and 11:00 (past due by > 15 min).
    wake_time = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: wake_time)
    uploader = FakeUploader()
    rng = random.Random(10)

    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=uploader,
        clock=lambda: wake_time,
        rng=rng,
    )

    # Enqueue 3 jobs: 2 expired during sleep, 1 in future (15:00)
    for idx, t_str in enumerate(["10:00", "11:00", "15:00"]):
        job_id = f"job_sleep_{idx}"
        hour = int(t_str.split(":")[0])
        sched_time = datetime(2026, 10, 19, hour, 0, tzinfo=UTC)
        state_mgr.save_job(Job(id=job_id, source_url="https://youtu.be/1"))
        state_mgr.enqueue_for_upload(
            job_id=job_id,
            video_path=f"work/{job_id}.mp4",
            title=f"Short {idx}",
            description="desc",
            scheduled_time=sched_time,
        )

    # Sanitize overdue slots
    adjusted = scheduler.sanitize_missed_slots(wake_time)
    assert adjusted == 2

    # Check that the two overdue jobs were rescheduled strictly AFTER wake_time with random gap
    pending = state_mgr.get_pending_scheduled_slots()
    rescheduled = [p for p in pending if p["job_id"] in ("job_sleep_0", "job_sleep_1")]
    for r in rescheduled:
        t = datetime.fromisoformat(r["scheduled_time"])
        assert t > wake_time, f"Rescheduled slot {t} must be after wake time {wake_time}"

    # Also verify the two rescheduled slots are spaced apart by at least min_gap_minutes (5 min)
    t0 = datetime.fromisoformat(rescheduled[0]["scheduled_time"])
    t1 = datetime.fromisoformat(rescheduled[1]["scheduled_time"])
    assert abs((t1 - t0).total_seconds()) >= 5 * 60 - 1  # at least min gap

    # run_tick at wake_time should NOT burst upload them because neither is due yet (both shifted into future)
    result = scheduler.run_tick()
    assert result is None
    assert len(uploader.upload_calls) == 0


def test_pause_and_resume(state_mgr: StateManager):
    """Test scheduler pause and resume behavior."""
    now = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)
    uploader = FakeUploader()
    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=uploader,
        clock=lambda: now,
    )

    # Initially not paused
    assert scheduler.is_paused() is False

    scheduler.pause()
    assert scheduler.is_paused() is True

    # Enqueue a due job
    job_id = "job_paused"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtu.be/1"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path=f"work/{job_id}.mp4",
        title="Title",
        description="Desc",
        scheduled_time=now - timedelta(minutes=1),
    )

    # run_tick while paused should not upload
    result = scheduler.run_tick()
    assert result is None
    assert len(uploader.upload_calls) == 0

    # Resume
    scheduler.resume()
    assert scheduler.is_paused() is False

    # Now run_tick should dispatch
    result = scheduler.run_tick()
    assert result is not None
    assert len(uploader.upload_calls) == 1
    assert result.status == "uploaded"


def test_manual_upload_now_respects_caps(state_mgr: StateManager):
    """Test manual upload_now respects hourly limits and daily caps."""
    now = datetime.now(UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)
    uploader = FakeUploader()
    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=uploader,
        clock=lambda: now,
    )

    job_id = "job_manual"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtu.be/1"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path=f"work/{job_id}.mp4",
        title="Manual Short",
        description="Desc",
        scheduled_time=now + timedelta(hours=2),
    )

    # 1. Normal upload_now succeeds
    res = scheduler.upload_now(job_id)
    assert res.video_id == "mock_0"
    assert len(uploader.upload_calls) == 1

    # 2. Try upload_now when 7 rolling hour limit is reached
    for i in range(7):
        jid = f"fill_{i}"
        state_mgr.save_job(Job(id=jid, source_url="https://youtu.be/1"))
        state_mgr.enqueue_for_upload(
            job_id=jid,
            video_path=f"work/{jid}.mp4",
            title=f"Short {i}",
            description="desc",
            scheduled_time=now,
        )
        state_mgr.update_scheduled_slot_status(jid, "COMPLETED")

    job_blocked = "job_blocked"
    state_mgr.save_job(Job(id=job_blocked, source_url="https://youtu.be/1"))
    state_mgr.enqueue_for_upload(
        job_id=job_blocked,
        video_path=f"work/{job_blocked}.mp4",
        title="Blocked",
        description="desc",
        scheduled_time=now + timedelta(hours=3),
    )

    with pytest.raises(SchedulerRateLimitError):
        scheduler.upload_now(job_blocked)


def test_power_management_win32_calls():
    """Verify power management invokes kernel32 on Windows without errors."""
    with patch("ctypes.windll.kernel32.SetThreadExecutionState", return_value=1) as mock_win32:
        with patch("sys.platform", "win32"):
            assert prevent_system_sleep() is True
            mock_win32.assert_called_once()

        mock_win32.reset_mock()
        with patch("sys.platform", "win32"):
            assert allow_system_sleep() is True
            mock_win32.assert_called_once()

    # Test non-windows no-op
    with patch("sys.platform", "linux"):
        assert prevent_system_sleep() is False
        assert allow_system_sleep() is False


def test_scheduler_verified_upload_triggers_cleaner(state_mgr: StateManager):
    """Verify that SpreadScheduler calls cleaner.cleanup_job upon verified upload and transitions to CLEANED."""
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)
    uploader = FakeUploader(verify_result=True)
    mock_cleaner = MagicMock()

    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=uploader,
        cleaner=mock_cleaner,
        clock=lambda: now,
    )

    job_id = "job_clean_test_1"
    job = Job(
        id=job_id, source_url="https://youtube.com/watch?v=clean1", status=JobStatus.SCHEDULED
    )
    state_mgr.save_job(job)
    scheduler.enqueue(
        job_id=job_id,
        video_path="output/master.mp4",
        title="Clean Title",
        description="Clean Desc",
        target_time=now,
    )

    res = scheduler.upload_now(job_id)
    assert res.verified is True
    mock_cleaner.cleanup_job.assert_called_once_with(job_id, verified=True, upload_result=res)

    updated_job = state_mgr.get_job(job_id)
    assert updated_job is not None
    assert updated_job.status == JobStatus.CLEANED


def test_scheduler_unverified_upload_skips_cleaner(state_mgr: StateManager):
    """Verify that cleaner is not called if upload verification fails."""
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)
    uploader = FakeUploader(verify_result=False)
    mock_cleaner = MagicMock()

    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=uploader,
        cleaner=mock_cleaner,
        clock=lambda: now,
    )

    job_id = "job_unverified_clean"
    job = Job(
        id=job_id, source_url="https://youtube.com/watch?v=unver1", status=JobStatus.SCHEDULED
    )
    state_mgr.save_job(job)
    scheduler.enqueue(
        job_id=job_id,
        video_path="output/master.mp4",
        title="Unverified Title",
        description="Unverified Desc",
        target_time=now,
    )

    res = scheduler.upload_now(job_id)
    assert res.verified is False
    mock_cleaner.cleanup_job.assert_not_called()

    updated_job = state_mgr.get_job(job_id)
    assert updated_job is not None
    assert updated_job.status == JobStatus.COMPLETED


def test_scheduler_cleaner_failure_tolerated(state_mgr: StateManager):
    """Verify that cleanup errors do not fail the upload dispatch result."""
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)
    uploader = FakeUploader(verify_result=True)
    mock_cleaner = MagicMock()
    mock_cleaner.cleanup_job.side_effect = RuntimeError("Disk locked")

    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=uploader,
        cleaner=mock_cleaner,
        clock=lambda: now,
    )

    job_id = "job_clean_error"
    job = Job(
        id=job_id, source_url="https://youtube.com/watch?v=cleanerr", status=JobStatus.SCHEDULED
    )
    state_mgr.save_job(job)
    scheduler.enqueue(
        job_id=job_id,
        video_path="output/master.mp4",
        title="Error Title",
        description="Error Desc",
        target_time=now,
    )

    res = scheduler.upload_now(job_id)
    assert res.verified is True
    # Still completed despite cleanup error
    updated_job = state_mgr.get_job(job_id)
    assert updated_job is not None
    assert updated_job.status == JobStatus.COMPLETED


# -----------------------------------------------------------------------------
# Quota semantics for non-YouTube dispatch paths and exhaustion fallback
# -----------------------------------------------------------------------------


def test_local_export_does_not_spend_youtube_quota(state_mgr: StateManager, tmp_path: Path):
    """Regression: a local export is not a YouTube API call and must spend no quota."""
    from autoshorts.uploader.local_exporter import LocalExportUploader

    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)
    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 64)
    job_id = "job_local_no_quota"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=local1"))
    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=LocalExportUploader(export_dir=tmp_path / "export"),  # type: ignore[arg-type]
        clock=lambda: now,
    )
    scheduler.enqueue(
        job_id=job_id,
        video_path=str(video),
        title="Local Export",
        description="Desc",
        target_time=now,
    )

    res = scheduler.upload_now(job_id)

    assert res.status == "exported"
    assert tracker.get_upload_spend() == 0
    assert tracker.get_general_spend() == 0


def test_exhausted_upload_bucket_honors_queue_next_day_fallback(
    state_mgr: StateManager, tmp_path: Path
):
    """Regression: exhaustion_fallback=queue_next_day must hold the slot, not dispatch to Studio.

    The scheduler previously read a non-existent upload.quota_exhausted_fallback (default
    'studio') and dispatched anyway when the upload bucket was exhausted.
    """
    from autoshorts.core.config import QuotaConfig

    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(
        state_mgr,
        clock=lambda: now,
        quota_config=QuotaConfig(exhaustion_fallback="queue_next_day"),
    )
    tracker.record_upload(tracker.upload_bucket_daily)
    assert tracker.can_upload() is False

    fake = FakeUploader()
    job_id = "job_exhausted_hold"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=hold1"))
    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=fake,  # type: ignore[arg-type]
        scheduler_config=SchedulerConfig(
            active_window=ActiveWindowConfig(start_hour_utc=0, end_hour_utc=24),
            upload=UploadConfig(mode="api"),
        ),
        clock=lambda: now,
    )
    scheduler.enqueue(
        job_id=job_id,
        video_path=str(tmp_path / "master.mp4"),
        title="Held",
        description="Desc",
        target_time=now,
    )

    assert scheduler.run_tick() is None
    assert fake.upload_calls == []
    assert [s["job_id"] for s in state_mgr.get_pending_scheduled_slots()] == [job_id]


def test_local_mode_is_not_blocked_by_exhausted_youtube_quota(
    state_mgr: StateManager, tmp_path: Path
):
    """Local export never touches the YouTube quota, so an exhausted bucket must not stop it."""
    from autoshorts.core.config import QuotaConfig
    from autoshorts.uploader.local_exporter import LocalExportUploader

    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(
        state_mgr,
        clock=lambda: now,
        quota_config=QuotaConfig(exhaustion_fallback="queue_next_day"),
    )
    tracker.record_upload(tracker.upload_bucket_daily)
    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 64)
    job_id = "job_local_exhausted"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=localq"))
    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=LocalExportUploader(export_dir=tmp_path / "export"),  # type: ignore[arg-type]
        scheduler_config=SchedulerConfig(
            active_window=ActiveWindowConfig(start_hour_utc=0, end_hour_utc=24),
            upload=UploadConfig(mode="local"),
        ),
        clock=lambda: now,
    )
    scheduler.enqueue(
        job_id=job_id,
        video_path=str(video),
        title="Local Export",
        description="Desc",
        target_time=now,
    )

    res = scheduler.run_tick()

    assert res is not None
    assert res.status == "exported"


def test_slot_generator_active_window_respects_configured_timezone():
    """Window 08:00-23:59 in Asia/Kolkata: 03:00 UTC (08:30 IST) is inside, 12:00 IST is not 'outside'."""
    gen = SlotGenerator(
        min_gap_minutes=5,
        max_gap_minutes=25,
        active_window=ActiveWindowConfig(start="08:00", end="23:59", timezone="Asia/Kolkata"),
        rng=random.Random(1),
    )
    assert gen.is_within_active_window(datetime(2026, 10, 19, 3, 0, tzinfo=UTC))  # 08:30 IST
    assert not gen.is_within_active_window(datetime(2026, 10, 19, 1, 0, tzinfo=UTC))  # 06:30 IST
    # A slot rolled past midnight IST lands at the next IST window start, not at UTC 08:00.
    late = datetime(2026, 10, 19, 18, 25, tzinfo=UTC)  # 23:55 IST
    for seed in range(20):
        gen.rng = random.Random(seed)
        slot = gen.next_slot_after(late)
        assert gen.is_within_active_window(slot)
        assert slot > late


def test_enqueue_never_anchors_in_the_past(state_mgr: StateManager):
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)
    cfg = SchedulerConfig(
        active_window=ActiveWindowConfig(start="00:00", end="23:59", timezone="UTC")
    )
    sched = SpreadScheduler(
        state_mgr=state_mgr, quota_tracker=tracker, scheduler_config=cfg, clock=lambda: now
    )
    vid = Path("x.mp4")
    stale = now - timedelta(days=2)
    for jid in ("job_old", "job_new"):
        state_mgr.save_job(Job(id=jid, source_url="https://youtu.be/1"))
    sched.enqueue("job_old", vid, "t", "d", target_time=stale)
    new_time = sched.enqueue("job_new", vid, "t", "d")
    assert new_time > now
