"""Unit tests for offline resilience, YouTube outage detection, and PC sleep recovery."""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from autoshorts.core.config import ActiveWindowConfig, SchedulerConfig, UploadConfig
from autoshorts.core.models import Job, JobStatus, UploadResult
from autoshorts.core.resilience import (
    ConnectivityStatus,
    OfflineRecoveryManager,
    classify_network_or_outage_error,
    probe_connectivity,
)
from autoshorts.core.state_manager import StateManager
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.spread_scheduler import SpreadScheduler


class FakeUploader:
    """Mock uploader for scheduler outage tests."""

    def __init__(self, should_fail_with: Exception | None = None) -> None:
        self.upload_calls: list[dict[str, Any]] = []
        self.should_fail_with = should_fail_with

    def upload(
        self,
        path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "unlisted",
        **kwargs: Any,
    ) -> UploadResult:
        if self.should_fail_with:
            raise self.should_fail_with
        call = {
            "path": path,
            "title": title,
            "description": description,
            "tags": tags,
            "privacy": privacy,
        }
        self.upload_calls.append(call)
        vid_id = f"yt_mock_{len(self.upload_calls)}"
        return UploadResult(
            video_id=vid_id,
            url=f"https://youtube.com/shorts/{vid_id}",
            status="uploaded",
            verified=True,
            quota_spent=1,
            uploaded_at=datetime.now(UTC),
        )

    def verify(self, video_id: str) -> bool:
        return True


@pytest.fixture
def state_mgr(tmp_path: Path) -> StateManager:
    """Provides an isolated SQLite StateManager."""
    return StateManager(db_path=tmp_path / "test_resilience_state.db")


@pytest.fixture
def quota_tracker(state_mgr: StateManager) -> QuotaTracker:
    """Provides an isolated QuotaTracker backed by state_mgr."""
    return QuotaTracker(state_mgr)


# =============================================================================
# 1. probe_connectivity tests
# =============================================================================


def test_probe_connectivity_online() -> None:
    """Verify probe_connectivity reports online when DNS, YouTube, and SponsorBlock respond."""
    with (
        patch("socket.create_connection") as mock_conn,
        patch("autoshorts.core.resilience._check_http_youtube", return_value=204),
    ):
        mock_conn.return_value = MagicMock()
        status = probe_connectivity()

        assert isinstance(status, ConnectivityStatus)
        assert status.internet_online is True
        assert status.youtube_reachable is True
        assert status.sponsorblock_reachable is True
        assert status.status_mode == "online"
        assert "online" in status.message.lower()
        assert status.latency_ms is not None
        assert status.latency_ms >= 0


def test_probe_connectivity_youtube_outage_socket() -> None:
    """Verify probe_connectivity detects YouTube outage when DNS connects but YouTube socket fails."""

    def fake_create_connection(address: tuple[str, int], timeout: float = 2.5) -> MagicMock:
        host, _port = address
        if host in ("1.1.1.1", "8.8.8.8"):
            return MagicMock()
        elif host == "www.youtube.com":
            raise OSError("Connection refused")
        return MagicMock()

    with patch("socket.create_connection", side_effect=fake_create_connection):
        status = probe_connectivity()

        assert status.internet_online is True
        assert status.youtube_reachable is False
        assert status.status_mode == "youtube_outage"
        assert "youtube is unreachable" in status.message.lower()


def test_probe_connectivity_youtube_outage_5xx() -> None:
    """Verify probe_connectivity detects YouTube outage when socket connects but HTTP returns 503."""
    with (
        patch("socket.create_connection") as mock_conn,
        patch("autoshorts.core.resilience._check_http_youtube", return_value=503),
    ):
        mock_conn.return_value = MagicMock()
        status = probe_connectivity()

        assert status.internet_online is True
        assert status.youtube_reachable is False
        assert status.status_mode == "youtube_outage"


def test_probe_connectivity_pc_offline() -> None:
    """Verify probe_connectivity reports pc_offline when gateway/DNS cannot be reached."""
    with patch("socket.create_connection", side_effect=OSError("Network is unreachable")):
        status = probe_connectivity()

        assert status.internet_online is False
        assert status.youtube_reachable is False
        assert status.sponsorblock_reachable is False
        assert status.status_mode == "pc_offline"
        assert "pc is offline" in status.message.lower()


# =============================================================================
# 2. classify_network_or_outage_error tests
# =============================================================================


def test_classify_dns_failure() -> None:
    """Verify DNS resolution failure is classified as pc_offline."""
    exc = socket.gaierror(-3, "Temporary failure in name resolution")
    result = classify_network_or_outage_error(exc)

    assert result["is_transient_outage"] is True
    assert result["outage_type"] == "pc_offline"
    assert result["recommended_backoff_sec"] > 0
    assert "dns" in result["user_message"].lower()


def test_classify_connection_reset() -> None:
    """Verify connection reset error is classified as pc_offline / network drop."""
    exc = ConnectionResetError("Connection reset by peer")
    result = classify_network_or_outage_error(exc)

    assert result["is_transient_outage"] is True
    assert result["outage_type"] == "pc_offline"
    assert result["recommended_backoff_sec"] > 0


def test_classify_youtube_503_outage() -> None:
    """Verify HTTP 503 error is classified as youtube_outage."""
    exc = HTTPError(
        url="https://youtube.googleapis.com/upload",
        code=503,
        msg="Service Unavailable",
        hdrs={},  # type: ignore[arg-type]
        fp=None,
    )
    result = classify_network_or_outage_error(exc)

    assert result["is_transient_outage"] is True
    assert result["outage_type"] == "youtube_outage"
    assert result["recommended_backoff_sec"] >= 300
    assert "outage" in result["user_message"].lower()


def test_classify_429_rate_limit() -> None:
    """Verify HTTP 429 error is classified as rate_limited."""
    exc = HTTPError(
        url="https://youtube.googleapis.com/upload",
        code=429,
        msg="Too Many Requests",
        hdrs={},  # type: ignore[arg-type]
        fp=None,
    )
    result = classify_network_or_outage_error(exc)

    assert result["is_transient_outage"] is True
    assert result["outage_type"] == "rate_limited"
    assert result["recommended_backoff_sec"] > 0
    assert "rate limit" in result["user_message"].lower()


def test_classify_non_transient_error() -> None:
    """Verify non-network exceptions are classified as non-transient."""
    exc = ValueError("Corrupt metadata payload")
    result = classify_network_or_outage_error(exc)

    assert result["is_transient_outage"] is False
    assert result["outage_type"] == "none"
    assert result["recommended_backoff_sec"] == 0


# =============================================================================
# 3. detect_clock_jump_or_sleep & recover_overdue_scheduled_slots tests
# =============================================================================


def test_detect_clock_jump_or_sleep() -> None:
    """Verify clock jump detection handles normal progression vs sleep gap."""
    now = datetime(2026, 10, 20, 15, 0, 0, tzinfo=UTC)

    # 1. Normal gap (30 seconds)
    last_normal = now - timedelta(seconds=30)
    res_normal = OfflineRecoveryManager.detect_clock_jump_or_sleep(last_normal, now_utc=now)
    assert res_normal["jump_detected"] is False
    assert res_normal["elapsed_sec"] == 30.0

    # 2. PC sleep jump (3 hours = 10,800 seconds)
    last_sleep = now - timedelta(hours=3)
    res_sleep = OfflineRecoveryManager.detect_clock_jump_or_sleep(last_sleep, now_utc=now)
    assert res_sleep["jump_detected"] is True
    assert res_sleep["elapsed_sec"] == 10800.0
    assert "jump detected" in res_sleep["message"].lower()


def test_recover_overdue_scheduled_slots_no_burst(state_mgr: StateManager) -> None:
    """Verify 4 overdue slots while PC was off are cleanly re-spaced and never burst."""
    now = datetime(2026, 10, 20, 16, 0, 0, tzinfo=UTC)

    # Enqueue 4 jobs scheduled in the past (e.g. 4h, 3h, 2h, 1h ago)
    for i in range(4):
        job_id = f"job_overdue_{i}"
        state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=mock"))
        past_time = now - timedelta(hours=4 - i)
        state_mgr.enqueue_for_upload(
            job_id=job_id,
            video_path=f"/tmp/video_{i}.mp4",
            title=f"Short #{i}",
            description="Recovered upload",
            scheduled_time=past_time,
            tags=["resilience", "test"],
        )

    # Verify all 4 are initially in the past
    pending_before = state_mgr.get_pending_scheduled_slots()
    assert len(pending_before) == 4
    for s in pending_before:
        assert datetime.fromisoformat(s["scheduled_time"]) < now

    # Run recovery with min_gap_minutes = 12
    recovered = OfflineRecoveryManager.recover_overdue_scheduled_slots(
        state_manager=state_mgr,
        min_gap_minutes=12,
        now_utc=now,
    )

    assert len(recovered) == 4

    # Verify rescheduled slots in DB
    pending_after = state_mgr.get_pending_scheduled_slots()
    assert len(pending_after) == 4

    expected_times = [
        now + timedelta(minutes=2),  # now + 2m
        now + timedelta(minutes=14),  # now + 2m + 12m
        now + timedelta(minutes=26),  # now + 2m + 24m
        now + timedelta(minutes=38),  # now + 2m + 36m
    ]

    for i, s in enumerate(pending_after):
        slot_dt = datetime.fromisoformat(s["scheduled_time"])
        assert slot_dt == expected_times[i]
        assert slot_dt > now

    # Verify gap between any two consecutive slots is >= 12 minutes (never burst)
    for i in range(len(pending_after) - 1):
        t1 = datetime.fromisoformat(pending_after[i]["scheduled_time"])
        t2 = datetime.fromisoformat(pending_after[i + 1]["scheduled_time"])
        gap_minutes = (t2 - t1).total_seconds() / 60.0
        assert gap_minutes == 12.0


# =============================================================================
# 4. SpreadScheduler Outage Deferral Integration tests
# =============================================================================


def test_spread_scheduler_defers_upload_on_pc_offline(
    state_mgr: StateManager, quota_tracker: QuotaTracker
) -> None:
    """Verify SpreadScheduler defers upload by 10 minutes when PC is offline before dispatch."""
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=UTC)
    config = SchedulerConfig(
        active_window=ActiveWindowConfig(start_hour_utc=0, end_hour_utc=24),
        upload=UploadConfig(mode="api"),
    )

    fake_uploader = FakeUploader()
    scheduler = SpreadScheduler(
        state_manager=state_mgr,
        quota_tracker=quota_tracker,
        uploader=fake_uploader,  # type: ignore[arg-type]
        scheduler_config=config,
        clock=lambda: now,
        check_connectivity=True,
    )

    # Enqueue a slot due right now
    job_id = "job_offline_test"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=offline"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path="/tmp/offline.mp4",
        title="Offline Test",
        description="Testing offline deferral",
        scheduled_time=now,
    )

    # Mock probe_connectivity returning pc_offline
    offline_status = ConnectivityStatus(
        internet_online=False,
        youtube_reachable=False,
        sponsorblock_reachable=False,
        status_mode="pc_offline",
        message="PC is disconnected from internet.",
    )

    with patch(
        "autoshorts.scheduler.spread_scheduler.probe_connectivity", return_value=offline_status
    ):
        result = scheduler.run_tick()

    # Upload was not dispatched
    assert result is None
    assert len(fake_uploader.upload_calls) == 0

    # Slot remains in SCHEDULED status and is deferred by 10 minutes
    pending = state_mgr.get_pending_scheduled_slots()
    assert len(pending) == 1
    slot = pending[0]
    assert slot["status"] == "SCHEDULED"
    new_sched_time = datetime.fromisoformat(slot["scheduled_time"])
    assert new_sched_time == now + timedelta(minutes=10)

    # Job is NOT marked FAILED
    job = state_mgr.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.SCHEDULED


def test_spread_scheduler_defers_upload_on_youtube_outage(
    state_mgr: StateManager, quota_tracker: QuotaTracker
) -> None:
    """Verify SpreadScheduler defers upload by 10 minutes during a YouTube service outage."""
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=UTC)
    config = SchedulerConfig(
        active_window=ActiveWindowConfig(start_hour_utc=0, end_hour_utc=24),
        upload=UploadConfig(mode="api"),
    )

    fake_uploader = FakeUploader()
    scheduler = SpreadScheduler(
        state_manager=state_mgr,
        quota_tracker=quota_tracker,
        uploader=fake_uploader,  # type: ignore[arg-type]
        scheduler_config=config,
        clock=lambda: now,
        check_connectivity=True,
    )

    job_id = "job_yt_outage_test"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=outage"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path="/tmp/outage.mp4",
        title="Outage Test",
        description="Testing outage deferral",
        scheduled_time=now,
    )

    outage_status = ConnectivityStatus(
        internet_online=True,
        youtube_reachable=False,
        sponsorblock_reachable=True,
        status_mode="youtube_outage",
        message="YouTube returned 503 Service Unavailable.",
    )

    with patch(
        "autoshorts.scheduler.spread_scheduler.probe_connectivity", return_value=outage_status
    ):
        result = scheduler.run_tick()

    assert result is None
    assert len(fake_uploader.upload_calls) == 0

    pending = state_mgr.get_pending_scheduled_slots()
    assert len(pending) == 1
    assert pending[0]["status"] == "SCHEDULED"
    assert datetime.fromisoformat(pending[0]["scheduled_time"]) == now + timedelta(minutes=10)


def test_spread_scheduler_defers_on_transient_network_exception_during_upload(
    state_mgr: StateManager, quota_tracker: QuotaTracker
) -> None:
    """Verify SpreadScheduler catches transient network errors during upload and defers instead of failing."""
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=UTC)
    config = SchedulerConfig(
        active_window=ActiveWindowConfig(start_hour_utc=0, end_hour_utc=24),
        upload=UploadConfig(mode="api"),
    )

    # Uploader fails with ConnectionResetError
    fake_uploader = FakeUploader(
        should_fail_with=ConnectionResetError("Connection lost mid-stream")
    )
    scheduler = SpreadScheduler(
        state_manager=state_mgr,
        quota_tracker=quota_tracker,
        uploader=fake_uploader,  # type: ignore[arg-type]
        scheduler_config=config,
        clock=lambda: now,
        check_connectivity=True,
    )

    job_id = "job_midstream_fail"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=midstream"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path="/tmp/midstream.mp4",
        title="Midstream Fail Test",
        description="Testing transient upload failure",
        scheduled_time=now,
    )

    online_status = ConnectivityStatus(
        internet_online=True,
        youtube_reachable=True,
        sponsorblock_reachable=True,
        status_mode="online",
        message="Network online",
    )

    with patch(
        "autoshorts.scheduler.spread_scheduler.probe_connectivity", return_value=online_status
    ):
        result = scheduler.run_tick()

    # Upload was deferred, not failed
    assert result is None

    # Slot remains SCHEDULED and deferred by 10 minutes
    pending = state_mgr.get_pending_scheduled_slots()
    assert len(pending) == 1
    assert pending[0]["status"] == "SCHEDULED"
    assert datetime.fromisoformat(pending[0]["scheduled_time"]) == now + timedelta(minutes=10)

    # Job status was reset to SCHEDULED (not FAILED)
    job = state_mgr.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.SCHEDULED


def test_spread_scheduler_sleep_recovery_on_tick(
    state_mgr: StateManager, quota_tracker: QuotaTracker
) -> None:
    """Verify that when SpreadScheduler wakes up from PC sleep, overdue slots are recovered smoothly."""
    wake_time = datetime(2026, 10, 20, 18, 0, 0, tzinfo=UTC)
    current_time = wake_time

    config = SchedulerConfig(
        active_window=ActiveWindowConfig(start_hour_utc=0, end_hour_utc=24),
        upload=UploadConfig(mode="api"),
        min_gap_minutes=15,
    )

    fake_uploader = FakeUploader()
    scheduler = SpreadScheduler(
        state_manager=state_mgr,
        quota_tracker=quota_tracker,
        uploader=fake_uploader,  # type: ignore[arg-type]
        scheduler_config=config,
        clock=lambda: current_time,
        check_connectivity=False,
    )

    # Simulate that scheduler last ticked 6 hours ago
    scheduler._last_tick_time = wake_time - timedelta(hours=6)

    # Enqueue 3 missed slots from during the sleep
    for i in range(3):
        job_id = f"job_sleep_{i}"
        state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=sleep"))
        state_mgr.enqueue_for_upload(
            job_id=job_id,
            video_path=f"/tmp/sleep_{i}.mp4",
            title=f"Sleep Short {i}",
            description="Testing sleep recovery",
            scheduled_time=wake_time - timedelta(hours=5 - i),
        )

    # Execute tick after waking up
    result = scheduler.run_tick()

    # The earliest slot was rescheduled to wake_time + 2m, so it is not due at wake_time
    assert result is None
    assert len(fake_uploader.upload_calls) == 0

    # Verify all slots were staggered by 15m
    pending = state_mgr.get_pending_scheduled_slots()
    assert len(pending) == 3

    t0 = datetime.fromisoformat(pending[0]["scheduled_time"])
    t1 = datetime.fromisoformat(pending[1]["scheduled_time"])
    t2 = datetime.fromisoformat(pending[2]["scheduled_time"])

    assert t0 == wake_time + timedelta(minutes=2)
    assert t1 == wake_time + timedelta(minutes=17)
    assert t2 == wake_time + timedelta(minutes=32)


def test_spread_scheduler_fatal_error_marks_failed(
    state_mgr: StateManager, quota_tracker: QuotaTracker
) -> None:
    """Verify non-transient upload errors still mark the slot and job FAILED."""
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=UTC)
    config = SchedulerConfig(
        active_window=ActiveWindowConfig(start_hour_utc=0, end_hour_utc=24),
        upload=UploadConfig(mode="api"),
    )

    fake_uploader = FakeUploader(should_fail_with=ValueError("Fatal: corrupt video file"))
    scheduler = SpreadScheduler(
        state_manager=state_mgr,
        quota_tracker=quota_tracker,
        uploader=fake_uploader,  # type: ignore[arg-type]
        scheduler_config=config,
        clock=lambda: now,
        check_connectivity=False,
    )

    job_id = "job_corrupt_test"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=corrupt"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path="/tmp/corrupt.mp4",
        title="Corrupt Video",
        description="Non-transient failure",
        scheduled_time=now,
    )

    with pytest.raises(ValueError, match="Fatal: corrupt video file"):
        scheduler.run_tick()

    job = state_mgr.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.FAILED


def test_spread_scheduler_upload_now_defers_during_outage(
    state_mgr: StateManager, quota_tracker: QuotaTracker
) -> None:
    """Verify upload_now raises error and defers slot when offline."""
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=UTC)
    config = SchedulerConfig(
        active_window=ActiveWindowConfig(start_hour_utc=0, end_hour_utc=24),
        upload=UploadConfig(mode="api"),
    )

    fake_uploader = FakeUploader()
    scheduler = SpreadScheduler(
        state_manager=state_mgr,
        quota_tracker=quota_tracker,
        uploader=fake_uploader,  # type: ignore[arg-type]
        scheduler_config=config,
        clock=lambda: now,
        check_connectivity=True,
    )

    job_id = "job_upload_now_test"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=upload_now"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path="/tmp/upload_now.mp4",
        title="Upload Now Test",
        description="Testing upload now outage",
        scheduled_time=now,
    )

    offline_status = ConnectivityStatus(
        internet_online=False,
        youtube_reachable=False,
        sponsorblock_reachable=False,
        status_mode="pc_offline",
        message="PC is disconnected.",
    )

    with (
        patch(
            "autoshorts.scheduler.spread_scheduler.probe_connectivity", return_value=offline_status
        ),
        pytest.raises(Exception, match="Network/YouTube outage"),
    ):
        scheduler.upload_now(job_id)

    pending = state_mgr.get_pending_scheduled_slots()
    assert len(pending) == 1
    assert pending[0]["status"] == "SCHEDULED"
    assert datetime.fromisoformat(pending[0]["scheduled_time"]) == now + timedelta(minutes=10)


def test_spread_scheduler_local_mode_bypasses_connectivity(
    state_mgr: StateManager, quota_tracker: QuotaTracker
) -> None:
    """Verify local upload mode proceeds even if PC is offline."""
    now = datetime(2026, 10, 20, 14, 0, 0, tzinfo=UTC)
    config = SchedulerConfig(
        active_window=ActiveWindowConfig(start_hour_utc=0, end_hour_utc=24),
        upload=UploadConfig(mode="local"),
    )

    fake_uploader = FakeUploader()
    scheduler = SpreadScheduler(
        state_manager=state_mgr,
        quota_tracker=quota_tracker,
        uploader=fake_uploader,  # type: ignore[arg-type]
        scheduler_config=config,
        clock=lambda: now,
        check_connectivity=True,
    )

    job_id = "job_local_mode_test"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtube.com/watch?v=local"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path="/tmp/local.mp4",
        title="Local Mode Test",
        description="Local bypass",
        scheduled_time=now,
    )

    result = scheduler.run_tick()
    assert result is not None
    assert len(fake_uploader.upload_calls) == 1
