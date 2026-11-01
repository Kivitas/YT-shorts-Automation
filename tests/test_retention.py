"""Tests for work directory retention policy, SafetyCleaner pruning, and CLI."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.cleanup import SafetyCleaner, is_job_locked, prune_work_dir
from autoshorts.cli import main
from autoshorts.core.config import RetentionConfig, Settings
from autoshorts.core.job_lock import job_lock
from autoshorts.core.models import Job, JobStatus, UploadResult, VideoMetadata
from autoshorts.core.state_manager import StateManager


def test_retention_config_defaults() -> None:
    """Verify RetentionConfig default values conform to safe-by-default requirements."""
    cfg = RetentionConfig()
    assert cfg.enabled is True
    assert cfg.work_max_age_days == 14
    assert cfg.work_max_total_gb == 20.0
    assert cfg.keep_last_n == 10
    assert cfg.dry_run is True

    settings = Settings()
    assert hasattr(settings, "retention")
    assert settings.retention.enabled is True
    assert settings.retention.work_max_age_days == 14
    assert settings.retention.work_max_total_gb == 20.0
    assert settings.retention.keep_last_n == 10
    assert settings.retention.dry_run is True


def test_prune_work_dir_age(tmp_path: Path) -> None:
    """Verify directories older than work_max_age_days beyond keep_last_n are pruned."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    state_mgr = StateManager(db_path=tmp_path / "state.db")

    old_dir_1 = work_dir / "story_old_1"
    old_dir_1.mkdir()
    (old_dir_1 / "data.wav").write_text("old wav 1")
    state_mgr.save_job(
        Job(id="story_old_1", source_url="https://youtube.com/v=1", status=JobStatus.COMPLETED)
    )

    old_dir_2 = work_dir / "longform_old_2"
    old_dir_2.mkdir()
    (old_dir_2 / "data.wav").write_text("old wav 2")
    state_mgr.save_job(
        Job(id="longform_old_2", source_url="https://youtube.com/v=2", status=JobStatus.COMPLETED)
    )

    new_dir = work_dir / "story_new"
    new_dir.mkdir()
    (new_dir / "data.wav").write_text("new wav")
    state_mgr.save_job(
        Job(id="story_new", source_url="https://youtube.com/v=3", status=JobStatus.COMPLETED)
    )

    # Set timestamps: old dirs 20 days ago, new dir 1 day ago
    now = time.time()
    past_20_days = now - (20 * 86400)
    past_1_day = now - (1 * 86400)

    os.utime(old_dir_1, (past_20_days, past_20_days))
    os.utime(old_dir_2, (past_20_days, past_20_days))
    os.utime(new_dir, (past_1_day, past_1_day))

    pruned = prune_work_dir(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        state_manager=state_mgr,
        max_age_days=14,
        keep_last_n=1,  # story_new is protected as the 1 most recent dir
        dry_run=False,
        permanent=True,
    )

    assert old_dir_1 in pruned
    assert old_dir_2 in pruned
    assert new_dir not in pruned

    assert not old_dir_1.exists()
    assert not old_dir_2.exists()
    assert new_dir.is_dir()


def test_prune_work_dir_fresh_dirs_never_deleted_by_count(tmp_path: Path) -> None:
    """15 dirs created today with keep_last_n=10 -> none deleted (count alone does not prune fresh dirs)."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    state_mgr = StateManager(db_path=tmp_path / "state.db")

    dirs: list[Path] = []
    now = time.time()

    for i in range(15):
        d = work_dir / f"run_{i:02d}"
        d.mkdir()
        (d / "info.txt").write_text(f"run {i}")
        ts = now - ((14 - i) * 3600)  # within today
        os.utime(d, (ts, ts))
        dirs.append(d)
        state_mgr.save_job(
            Job(id=f"run_{i:02d}", source_url="https://youtube.com/v=x", status=JobStatus.COMPLETED)
        )

    pruned = prune_work_dir(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        state_manager=state_mgr,
        max_age_days=14,
        keep_last_n=10,
        dry_run=False,
        permanent=True,
    )

    # 15 dirs created today: none deleted
    assert pruned == []
    for d in dirs:
        assert d.is_dir()


def test_prune_work_dir_old_dirs_exempt_by_keep_last_n(tmp_path: Path) -> None:
    """3 dirs all 15 days old with keep_last_n=10 -> none deleted."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    state_mgr = StateManager(db_path=tmp_path / "state.db")

    dirs: list[Path] = []
    now = time.time()
    past_15_days = now - (15 * 86400)

    for i in range(3):
        d = work_dir / f"old_run_{i:02d}"
        d.mkdir()
        (d / "info.txt").write_text(f"old run {i}")
        os.utime(d, (past_15_days, past_15_days))
        dirs.append(d)
        state_mgr.save_job(
            Job(
                id=f"old_run_{i:02d}",
                source_url="https://youtube.com/v=x",
                status=JobStatus.COMPLETED,
            )
        )

    pruned = prune_work_dir(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        state_manager=state_mgr,
        max_age_days=14,
        keep_last_n=10,
        dry_run=False,
        permanent=True,
    )

    assert pruned == []
    for d in dirs:
        assert d.is_dir()


def test_prune_work_dir_size(tmp_path: Path) -> None:
    """Verify oldest directories beyond keep_last_n are pruned when total size exceeds max threshold."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    state_mgr = StateManager(db_path=tmp_path / "state.db")

    now = time.time()
    dirs: list[Path] = []
    payload = "X" * 100_000  # 100 KB per dir

    for i in range(4):
        jid = f"size_job_{i}"
        d = work_dir / jid
        d.mkdir()
        (d / "video.mp4").write_text(payload)
        ts = now - ((3 - i) * 3600)
        os.utime(d, (ts, ts))
        dirs.append(d)
        state_mgr.save_job(
            Job(id=jid, source_url="https://youtube.com/v=x", status=JobStatus.COMPLETED)
        )

    # Total size is ~400 KB. Cap at 250 KB, keep_last_n=2 (size_job_2 and size_job_3 protected)
    # size_job_0 and size_job_1 are candidates -> should prune oldest
    pruned = prune_work_dir(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        state_manager=state_mgr,
        max_total_bytes=250_000,
        max_age_days=14,
        keep_last_n=2,
        dry_run=False,
        permanent=True,
    )

    assert dirs[0] in pruned
    assert dirs[1] in pruned
    assert not dirs[0].exists()
    assert not dirs[1].exists()
    assert dirs[2].is_dir()
    assert dirs[3].is_dir()


def test_prune_work_dir_active_job_protection_state_manager(tmp_path: Path) -> None:
    """Verify directories for jobs that are active or not-yet-uploaded are NEVER pruned."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    db_path = tmp_path / "state.db"
    state_mgr = StateManager(db_path=db_path)

    # 1. Job A is in progress (COMPOSITING)
    job_active = Job(
        id="job_active_123",
        source_url="https://youtube.com/watch?v=active",
        status=JobStatus.COMPOSITING,
        video_metadata=VideoMetadata(
            title="Active Job",
            channel="Channel",
            channel_url="https://c",
            video_url="https://v",
            duration=60.0,
        ),
    )
    state_mgr.save_job(job_active)

    # 2. Job B is rendered and awaiting user review (READY_FOR_REVIEW)
    job_pending_review = Job(
        id="job_pending_456",
        source_url="https://youtube.com/watch?v=pending",
        status=JobStatus.READY_FOR_REVIEW,
        video_metadata=VideoMetadata(
            title="Pending Job",
            channel="Channel",
            channel_url="https://c",
            video_url="https://v",
            duration=60.0,
        ),
    )
    state_mgr.save_job(job_pending_review)

    # 3. Job C is completed and uploaded (COMPLETED)
    job_completed = Job(
        id="job_completed_789",
        source_url="https://youtube.com/watch?v=done",
        status=JobStatus.COMPLETED,
        video_metadata=VideoMetadata(
            title="Done Job",
            channel="Channel",
            channel_url="https://c",
            video_url="https://v",
            duration=60.0,
        ),
    )
    state_mgr.save_job(job_completed)

    # Create directories for all three, with mtime 30 days ago (> 14 days)
    now = time.time()
    past_30_days = now - (30 * 86400)

    dir_active = work_dir / "job_active_123"
    dir_active.mkdir()
    (dir_active / "tmp.ts").write_text("active data")
    os.utime(dir_active, (past_30_days, past_30_days))

    dir_pending = work_dir / "job_pending_456"
    dir_pending.mkdir()
    (dir_pending / "tmp.ts").write_text("pending data")
    os.utime(dir_pending, (past_30_days, past_30_days))

    dir_completed = work_dir / "job_completed_789"
    dir_completed.mkdir()
    (dir_completed / "tmp.ts").write_text("done data")
    os.utime(dir_completed, (past_30_days, past_30_days))

    pruned = prune_work_dir(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        state_manager=state_mgr,
        max_age_days=14,
        keep_last_n=0,
        dry_run=False,
        permanent=True,
    )

    # Only completed job should be pruned; active and pending must be protected!
    assert dir_completed in pruned
    assert not dir_completed.exists()

    assert dir_active not in pruned
    assert dir_active.is_dir()

    assert dir_pending not in pruned
    assert dir_pending.is_dir()

    # StateManager should record CLEANED status for the pruned completed job
    refreshed_completed = state_mgr.get_job("job_completed_789")
    assert refreshed_completed is not None
    assert refreshed_completed.status == JobStatus.CLEANED


def test_prune_work_dir_unverified_upload_result_protected_even_if_completed(
    tmp_path: Path,
) -> None:
    """Jobs whose upload_result exists with verified False must be protected even if status is COMPLETED."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    state_mgr = StateManager(db_path=tmp_path / "state.db")

    jid = "job_unver_comp"
    d = work_dir / jid
    d.mkdir()
    (d / "data.wav").write_text("data")
    past_30_days = time.time() - (30 * 86400)
    os.utime(d, (past_30_days, past_30_days))

    job = Job(
        id=jid,
        source_url="https://youtube.com/watch?v=unver",
        status=JobStatus.COMPLETED,
        upload_result=UploadResult(
            video_id="v_unver",
            url="https://youtube.com/shorts/v_unver",
            verified=False,
        ),
    )
    state_mgr.save_job(job)

    pruned = prune_work_dir(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        state_manager=state_mgr,
        max_age_days=14,
        keep_last_n=0,
        dry_run=False,
        permanent=True,
    )

    assert pruned == []
    assert d.is_dir()


def test_prune_work_dir_active_job_protection_job_lock(tmp_path: Path) -> None:
    """Verify directories currently locked by live processes via core.job_lock are NEVER deleted."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    state_mgr = StateManager(db_path=tmp_path / "state.db")

    job_id = "job_locked_live"
    locked_dir = work_dir / job_id
    locked_dir.mkdir()
    (locked_dir / "stream.wav").write_text("audio in progress")
    state_mgr.save_job(
        Job(id=job_id, source_url="https://youtube.com/v=x", status=JobStatus.COMPLETED)
    )

    # Set timestamp to 40 days ago
    past_40_days = time.time() - (40 * 86400)
    os.utime(locked_dir, (past_40_days, past_40_days))

    # Hold the exclusive lock
    with job_lock(locked_dir, job_id):
        assert is_job_locked(locked_dir, job_id) is True

        pruned = prune_work_dir(
            work_dir=work_dir,
            output_dir=output_dir,
            allowed_roots=[work_dir, output_dir],
            state_manager=state_mgr,
            max_age_days=14,
            keep_last_n=0,
            dry_run=False,
            permanent=True,
        )

        assert locked_dir not in pruned
        assert locked_dir.is_dir()


def test_prune_work_dir_dry_run(tmp_path: Path) -> None:
    """Verify dry_run mode returns targets without modifying disk state."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    state_mgr = StateManager(db_path=tmp_path / "state.db")

    d1 = work_dir / "story_dry_1"
    d1.mkdir()
    (d1 / "file.wav").write_text("content")
    state_mgr.save_job(
        Job(id="story_dry_1", source_url="https://youtube.com/v=1", status=JobStatus.COMPLETED)
    )

    d2 = work_dir / "longform_dry_2"
    d2.mkdir()
    (d2 / "file.wav").write_text("content")
    state_mgr.save_job(
        Job(id="longform_dry_2", source_url="https://youtube.com/v=2", status=JobStatus.COMPLETED)
    )

    past_20_days = time.time() - (20 * 86400)
    os.utime(d1, (past_20_days, past_20_days))
    os.utime(d2, (past_20_days, past_20_days))

    pruned = prune_work_dir(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        state_manager=state_mgr,
        max_age_days=14,
        keep_last_n=0,
        dry_run=True,
    )

    assert d1 in pruned
    assert d2 in pruned
    # All files must still exist on disk!
    assert d1.is_dir()
    assert d2.is_dir()


def test_prune_work_dir_recycle_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify send2trash is used when use_recycle_bin=True and permanent=False."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    state_mgr = StateManager(db_path=tmp_path / "state.db")

    d = work_dir / "story_trash_test"
    d.mkdir()
    (d / "data.wav").write_text("trash me")
    state_mgr.save_job(
        Job(id="story_trash_test", source_url="https://youtube.com/v=1", status=JobStatus.COMPLETED)
    )

    past_20_days = time.time() - (20 * 86400)
    os.utime(d, (past_20_days, past_20_days))

    trashed: list[str] = []

    def mock_send2trash(path_str: str) -> None:
        trashed.append(path_str)
        if os.path.isdir(path_str):
            import shutil

            shutil.rmtree(path_str)

    monkeypatch.setattr("autoshorts.cleanup.safety_cleaner.send2trash", mock_send2trash)

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        state_manager=state_mgr,
        use_recycle_bin=True,
        permanent=False,
    )
    deleted = cleaner.prune_work_dir(max_age_days=14, keep_last_n=0, dry_run=False)

    assert d in deleted
    assert str(d) in trashed
    assert not d.exists()


def test_cli_cleanup_work_subcommand(tmp_path: Path) -> None:
    """Verify autoshorts cleanup-work CLI command operates correctly."""
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True)

    d = work_dir / "story_cli_test"
    d.mkdir()
    (d / "file.wav").write_text("test")

    past_30_days = time.time() - (30 * 86400)
    os.utime(d, (past_30_days, past_30_days))

    settings = Settings()
    settings.retention.work_max_age_days = 14

    with (
        patch("autoshorts.cli.load_config", return_value=settings),
        patch("autoshorts.cli.SafetyCleaner") as mock_cleaner_cls,
    ):
        mock_instance = MagicMock()
        mock_instance.prune_work_dir.return_value = [d]
        mock_cleaner_cls.return_value = mock_instance

        # Test with --dry-run
        rc_dry = main(["cleanup-work", "--dry-run"])
        assert rc_dry == 0
        mock_instance.prune_work_dir.assert_called_with(dry_run=True, force=True)

        # Test normal run
        rc = main(["cleanup-work"])
        assert rc == 0
        mock_instance.prune_work_dir.assert_called_with(dry_run=None, force=True)
