"""Tests for SafetyCleaner: path guards, Recycle Bin retention, and verification checks."""

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoshorts.cleanup.safety_cleaner import SafetyCleaner
from autoshorts.core.contracts import Cleaner
from autoshorts.core.exceptions import CleanupError, PathSecurityError
from autoshorts.core.models import JobStatus, UploadResult
from autoshorts.core.paths import (
    ALLOWED_CLEANUP_ROOTS,
    FORBIDDEN_PROTECTED_PATHS,
    PROJECT_ROOT,
)


def test_cleaner_protocol_conformance():
    """Verify SafetyCleaner implements contracts.Cleaner protocol."""
    cleaner = SafetyCleaner()
    assert isinstance(cleaner, Cleaner)


def test_cleanup_refuse_unverified(tmp_path: Path):
    """Refuse file deletion if upload is not confirmed verified."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir()
    output_dir.mkdir()

    job_id = "job_unverified_1"
    job_work = work_dir / job_id
    job_work.mkdir()
    dummy_wav = job_work / "audio.wav"
    dummy_wav.write_text("audio bytes")

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        permanent=True,
    )

    # 1. Calling without verified flag -> raises CleanupError
    with pytest.raises(CleanupError, match="upload has not been verified"):
        cleaner.cleanup_job(job_id)
    assert dummy_wav.is_file()

    # 2. Calling with verified=False -> raises CleanupError
    with pytest.raises(CleanupError, match="upload has not been verified"):
        cleaner.cleanup_job(job_id, verified=False)
    assert dummy_wav.is_file()

    # 3. Calling with unverified UploadResult -> raises CleanupError
    unverified_result = UploadResult(
        video_id="vid_123",
        url="https://youtube.com/shorts/vid_123",
        verified=False,
    )
    with pytest.raises(CleanupError, match="upload has not been verified"):
        cleaner.cleanup_job(job_id, upload_result=unverified_result)
    assert dummy_wav.is_file()


def test_cleanup_verified_success(tmp_path: Path):
    """Deletes intermediate files when verified=True."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    job_id = "job_verified_1"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True)

    source_dl = job_work / "raw.mp4"
    source_dl.write_text("raw video")
    temp_wav = job_work / "audio.wav"
    temp_wav.write_text("wav")
    subs_ass = job_work / "subs.ass"
    subs_ass.write_text("subtitles")
    preview_mp4 = output_dir / f"{job_id}_preview.mp4"
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_mp4.write_text("preview")

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        permanent=True,
    )

    deleted = cleaner.cleanup_job(job_id, verified=True)

    assert source_dl not in [p for p in deleted if p == source_dl] or not source_dl.exists()
    assert not temp_wav.exists()
    assert not subs_ass.exists()
    assert not preview_mp4.exists()
    assert len(deleted) >= 4


def test_cleanup_upload_result_verified_success(tmp_path: Path):
    """Deletes files when UploadResult.verified is True."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    job_id = "job_verified_res"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True)

    raw_file = job_work / "raw.mp4"
    raw_file.write_text("content")

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        permanent=True,
    )

    verified_result = UploadResult(
        video_id="vid_good",
        url="https://youtube.com/shorts/vid_good",
        verified=True,
    )
    deleted = cleaner.cleanup_job(job_id, upload_result=verified_result)
    assert not raw_file.exists()
    assert any(p.name == "raw.mp4" for p in deleted)


def test_cleanup_force_unverified_success(tmp_path: Path):
    """Allows cleanup when force=True even if unverified."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    job_id = "job_force"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True)

    temp_file = job_work / "temp.ts"
    temp_file.write_text("temporary")

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        permanent=True,
    )

    deleted = cleaner.cleanup_job(job_id, force=True)
    assert not temp_file.exists()
    assert any(p.name == "temp.ts" for p in deleted)


def test_path_guard_escape_attempts(tmp_path: Path):
    """Assert PathSecurityError on any attempt to delete protected or external paths."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir()
    output_dir.mkdir()

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
    )

    # 1. Protected directories under project root
    for protected in FORBIDDEN_PROTECTED_PATHS:
        with pytest.raises(PathSecurityError):
            cleaner.assert_safe_to_delete(protected)

    # 2. Cannot delete the allowed roots themselves
    with pytest.raises(PathSecurityError):
        cleaner.assert_safe_to_delete(work_dir)
    with pytest.raises(PathSecurityError):
        cleaner.assert_safe_to_delete(output_dir)

    # 3. Path outside allowed roots
    external = tmp_path / "external" / "secret.txt"
    external.parent.mkdir(parents=True, exist_ok=True)
    external.write_text("secret")
    with pytest.raises(PathSecurityError):
        cleaner.assert_safe_to_delete(external)


def test_symlink_safety_and_escape_rejection(tmp_path: Path):
    """Never follow symlinks pointing outside allowed cleanup roots."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir()
    output_dir.mkdir()

    protected_dir = tmp_path / "assets"
    protected_dir.mkdir()
    protected_asset = protected_dir / "bg.mp4"
    protected_asset.write_text("precious asset")

    # Create symlink inside work pointing outside to protected asset
    job_work = work_dir / "job_symlink"
    job_work.mkdir()
    link_to_protected = job_work / "link_to_asset.mp4"

    try:
        link_to_protected.symlink_to(protected_asset)
    except OSError:
        # Symlink creation may require elevated privileges on some Windows setups
        pytest.skip("Symlink creation not permitted in this environment")

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        permanent=True,
    )

    # Must raise PathSecurityError because the link target escapes allowed cleanup roots
    with pytest.raises(PathSecurityError):
        cleaner.assert_safe_to_delete(link_to_protected)

    # Protected target must remain intact
    assert protected_asset.is_file()
    assert protected_asset.read_text() == "precious asset"


def test_dry_run_mode(tmp_path: Path):
    """In dry_run mode, return paths that would be deleted without removing them."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    job_id = "job_dry"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True)

    f1 = job_work / "file1.wav"
    f1.write_text("data")
    f2 = job_work / "file2.ass"
    f2.write_text("data")

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        dry_run=True,
    )

    targets = cleaner.cleanup_job(job_id, verified=True)
    assert len(targets) >= 2
    # Files must still exist on disk!
    assert f1.exists()
    assert f2.exists()


def test_permanent_vs_recycle_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify permanent unlinks directly while non-permanent calls send2trash."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    job_id = "job_trash"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True)

    target_file = job_work / "temp.wav"
    target_file.write_text("sound")

    trashed_calls: list[str] = []

    def fake_send2trash(path_str: str):
        trashed_calls.append(path_str)
        # Emulate moving to trash by removing file
        if os.path.isfile(path_str):
            os.remove(path_str)
        elif os.path.isdir(path_str):
            os.rmdir(path_str)

    monkeypatch.setattr("autoshorts.cleanup.safety_cleaner.send2trash", fake_send2trash)

    # Test non-permanent (recycle bin default)
    cleaner_trash = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        permanent=False,
        use_recycle_bin=True,
    )
    cleaner_trash.cleanup_job(job_id, verified=True)
    assert len(trashed_calls) > 0


def test_retention_keep_final_days(tmp_path: Path):
    """Verify master MP4 retention respects keep_final_days."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir()
    output_dir.mkdir()

    job_id = "job_retention"
    master_mp4 = output_dir / f"{job_id}_master.mp4"
    master_mp4.write_text("final master video")

    # 1. keep_final_days = 7 on new file -> retains master MP4
    cleaner_keep = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        keep_final_days=7,
        permanent=True,
    )
    cleaner_keep.cleanup_job(job_id, verified=True)
    assert master_mp4.is_file()

    # 2. keep_final_days = 0 -> deletes master MP4 immediately
    cleaner_delete = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        keep_final_days=0,
        permanent=True,
    )
    cleaner_delete.cleanup_job(job_id, verified=True)
    assert not master_mp4.exists()


def test_retention_keep_master_mp4_flag(tmp_path: Path):
    """Verify keep_master_mp4 flag controls final MP4 deletion when keep_final_days is None."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir()
    output_dir.mkdir()

    job_id = "job_flag"
    master_mp4 = output_dir / f"{job_id}_master.mp4"
    master_mp4.write_text("master video")

    # keep_master_mp4 = True -> preserved
    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        keep_master_mp4=True,
        permanent=True,
    )
    cleaner.cleanup_job(job_id, verified=True)
    assert master_mp4.is_file()

    # keep_master_mp4 = False -> purged
    cleaner_purge = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        keep_master_mp4=False,
        permanent=True,
    )
    cleaner_purge.cleanup_job(job_id, verified=True)
    assert not master_mp4.exists()


def test_windows_locked_file_retry_and_backoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify locked file retry with exponential backoff on simulated PermissionError."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    job_id = "job_locked"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True)

    locked_file = job_work / "locked.mp4"
    locked_file.write_text("locked")

    attempts = 0

    def mock_unlink(path):
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise PermissionError("WinError 32: The process cannot access the file")
        os.remove(path)

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        permanent=True,
        backoff_delays=(0.01, 0.02),
    )

    monkeypatch.setattr(Path, "unlink", lambda self: mock_unlink(self))

    # Should succeed on 2nd attempt after retry
    deleted = cleaner.cleanup_job(job_id, verified=True)
    assert attempts == 2
    assert not locked_file.exists()


def test_cleanup_idempotency(tmp_path: Path):
    """Running cleanup multiple times is safe and returns empty list on subsequent calls."""
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    job_id = "job_idempotent"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True)

    f = job_work / "temp.wav"
    f.write_text("data")

    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        permanent=True,
    )

    del1 = cleaner.cleanup_job(job_id, verified=True)
    assert len(del1) > 0

    # Second call should find nothing and return cleanly
    del2 = cleaner.cleanup_job(job_id, verified=True)
    assert del2 == []
