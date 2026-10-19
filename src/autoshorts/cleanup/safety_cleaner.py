"""Transactional, path-guarded cleanup worker with Recycle Bin retention.

Enforces strict path safety assertions (intermediate files inside work/ and output/ only),
guarantees non-deletion of protected directories (assets, data, src, config, logs),
requires verified upload confirmation before deleting, supports dry-run mode,
and implements Windows file-lock retry with exponential backoff.
"""

from __future__ import annotations

import gc
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from send2trash import send2trash

from autoshorts.core.config import CleanupConfig, Settings
from autoshorts.core.contracts import Cleaner
from autoshorts.core.exceptions import CleanupError, PathSecurityError
from autoshorts.core.models import JobStatus, UploadResult
from autoshorts.core.paths import (
    ALLOWED_CLEANUP_ROOTS,
    FORBIDDEN_PROTECTED_PATHS,
    PROJECT_ROOT,
    assert_safe_to_delete as core_assert_safe_to_delete,
    get_output_dir,
    get_work_dir,
)

logger = logging.getLogger(__name__)


class SafetyCleaner(Cleaner):
    """Path-guarded transactional cleaner implementing contracts.Cleaner."""

    def __init__(
        self,
        config: CleanupConfig | Settings | None = None,
        use_recycle_bin: bool = True,
        dry_run: bool = False,
        keep_final_days: int | None = None,
        keep_master_mp4: bool = True,
        purge_intermediate_work: bool = True,
        permanent: bool = False,
        work_dir: Path | str | None = None,
        output_dir: Path | str | None = None,
        allowed_roots: Sequence[Path | str] | None = None,
        state_manager: Any | None = None,
        backoff_delays: tuple[float, ...] = (0.5, 1.5, 3.0),
    ) -> None:
        """Initialize SafetyCleaner with configuration and safety boundaries.

        Args:
            config: Optional CleanupConfig or Settings instance.
            use_recycle_bin: Whether to use send2trash instead of permanent unlink.
            dry_run: If True, simulate and return list of paths without deleting.
            keep_final_days: If set, retain master MP4s until older than this many days.
            keep_master_mp4: If True and keep_final_days is None, retain master MP4s.
            purge_intermediate_work: If True, delete work/ intermediates.
            permanent: If True, permanently delete without Recycle Bin.
            work_dir: Override directory for intermediate work files.
            output_dir: Override directory for rendered output files.
            allowed_roots: Override allowed cleanup roots (for custom environments/tests).
            state_manager: Optional StateManager instance to record status updates.
            backoff_delays: Retry delay intervals in seconds for locked files.
        """
        # Load from config if provided
        if config is not None:
            cleanup_cfg = config.cleanup if isinstance(config, Settings) else config
            self.use_recycle_bin = cleanup_cfg.use_recycle_bin
            self.dry_run = cleanup_cfg.dry_run
            self.keep_master_mp4 = cleanup_cfg.keep_master_mp4
            self.purge_intermediate_work = cleanup_cfg.purge_intermediate_work
            self.keep_final_days = getattr(cleanup_cfg, "keep_final_days", keep_final_days)
        else:
            self.use_recycle_bin = use_recycle_bin
            self.dry_run = dry_run
            self.keep_master_mp4 = keep_master_mp4
            self.purge_intermediate_work = purge_intermediate_work
            self.keep_final_days = keep_final_days

        self.permanent = permanent
        self.state_manager = state_manager
        self.backoff_delays = backoff_delays

        self.work_dir = Path(work_dir).resolve() if work_dir else get_work_dir()
        self.output_dir = Path(output_dir).resolve() if output_dir else get_output_dir()

        if allowed_roots is not None:
            self.allowed_roots = [Path(r).resolve() for r in allowed_roots]
        elif work_dir is not None or output_dir is not None:
            self.allowed_roots = [self.work_dir, self.output_dir]
        else:
            self.allowed_roots = [Path(r).resolve() for r in ALLOWED_CLEANUP_ROOTS]

    def assert_safe_to_delete(self, path: Path | str) -> None:
        """Validate target path is within allowed cleanup roots and not protected.

        Raises:
            PathSecurityError: If path violates security constraints or symlink escapes.
        """
        raw_path = Path(path)
        abs_target = raw_path.resolve()

        # Check symlink escape
        if raw_path.is_symlink() or os.path.islink(raw_path):
            try:
                resolved_link = raw_path.resolve(strict=False)
                # Verify that resolved link target is within allowed roots
                if not any(
                    resolved_link.is_relative_to(allowed) and resolved_link != allowed
                    for allowed in self.allowed_roots
                ):
                    raise PathSecurityError(
                        f"Path-guard violation: Symlink target '{resolved_link}' escapes allowed cleanup roots."
                    )
            except (OSError, RuntimeError) as e:
                raise PathSecurityError(f"Path-guard violation on unresolvable symlink '{raw_path}': {e}") from e

        # Rule 1: Cannot delete allowed roots or project root itself
        for root_dir in self.allowed_roots + [PROJECT_ROOT.resolve()]:
            if abs_target == root_dir:
                raise PathSecurityError(f"CRITICAL: Attempted deletion of root directory: {abs_target}")

        # Rule 2: Must be strictly inside an allowed cleanup root
        is_allowed = any(
            abs_target.is_relative_to(allowed) and abs_target != allowed
            for allowed in self.allowed_roots
        )
        if not is_allowed:
            raise PathSecurityError(
                f"Path-guard violation: {abs_target} is outside allowed cleanup roots ({self.allowed_roots})"
            )

        # Rule 3: Must NOT be inside any forbidden protected subdirectory
        for protected in FORBIDDEN_PROTECTED_PATHS:
            if protected == PROJECT_ROOT.resolve():
                continue
            if abs_target == protected or abs_target.is_relative_to(protected):
                raise PathSecurityError(
                    f"CRITICAL: Attempted deletion inside protected directory {protected}: {abs_target}"
                )

    def cleanup_job(
        self,
        job_id: str,
        force: bool = False,
        verified: bool | None = None,
        upload_result: UploadResult | None = None,
        dry_run: bool | None = None,
        keep_final_days: int | None = None,
        keep_master_mp4: bool | None = None,
        permanent: bool | None = None,
    ) -> list[Path]:
        """Safely purge intermediate working files for a confirmed job.

        Only deletes if upload has been confirmed verified, unless force=True.
        Intermediate work files (source download, wav, ass, tmp) are deleted to the Recycle Bin.
        Master MP4 in output/ is retained or purged according to retention policy.

        Args:
            job_id: Unique job identifier.
            force: If True, bypass verification requirement.
            verified: Explicit boolean flag indicating upload verification status.
            upload_result: UploadResult object from uploader with .verified attribute.
            dry_run: Override instance dry_run setting if provided.
            keep_final_days: Override retention days for master MP4.
            keep_master_mp4: Override keep_master_mp4 flag.
            permanent: Override permanent deletion flag (bypasses Recycle Bin).

        Returns:
            List of Path objects that were deleted (or would be deleted in dry_run).

        Raises:
            CleanupError: If upload is not verified and force=False, or on deletion failure.
            PathSecurityError: If any target file path violates security guards.
        """
        # Verification check
        is_verified = False
        if verified is True:
            is_verified = True
        elif upload_result is not None and getattr(upload_result, "verified", False):
            is_verified = True
        elif self.state_manager is not None:
            job = self.state_manager.get_job(job_id)
            if job is not None:
                if job.status in (JobStatus.COMPLETED, JobStatus.CLEANED):
                    is_verified = True
                elif job.upload_result is not None and job.upload_result.verified:
                    is_verified = True

        if not is_verified and not force:
            raise CleanupError(
                f"Cleanup refused for job '{job_id}': upload has not been verified. "
                "Pass verified=True or a verified UploadResult, or use force=True to override."
            )

        eff_dry_run = dry_run if dry_run is not None else self.dry_run
        eff_keep_days = keep_final_days if keep_final_days is not None else self.keep_final_days
        eff_keep_master = keep_master_mp4 if keep_master_mp4 is not None else self.keep_master_mp4
        eff_permanent = permanent if permanent is not None else self.permanent

        files_to_delete: list[Path] = []
        directories_to_delete: list[Path] = []

        # 1. Collect intermediate files inside work_dir / job_id
        job_work_dir = self.work_dir / job_id
        if job_work_dir.exists() and self.purge_intermediate_work:
            self.assert_safe_to_delete(job_work_dir)
            if job_work_dir.is_dir():
                for root, dirs, files in os.walk(job_work_dir, followlinks=False):
                    root_path = Path(root)
                    for file_name in files:
                        file_path = root_path / file_name
                        self.assert_safe_to_delete(file_path)
                        files_to_delete.append(file_path)
                    for dir_name in dirs:
                        sub_dir = root_path / dir_name
                        self.assert_safe_to_delete(sub_dir)
                        directories_to_delete.append(sub_dir)
                directories_to_delete.append(job_work_dir)
            else:
                files_to_delete.append(job_work_dir)

        # 2. Collect any loose intermediate files in work_dir with prefix {job_id}
        if self.work_dir.exists() and self.purge_intermediate_work:
            for item in self.work_dir.iterdir():
                if item.name.startswith(f"{job_id}.") or item.name.startswith(f"{job_id}_"):
                    self.assert_safe_to_delete(item)
                    if item.is_file() or item.is_symlink():
                        if item not in files_to_delete:
                            files_to_delete.append(item)

        # 3. Handle final output MP4 retention
        if self.output_dir.exists():
            for item in self.output_dir.iterdir():
                if (
                    item.name == f"{job_id}_master.mp4"
                    or item.name == f"{job_id}.mp4"
                    or item.name.startswith(f"{job_id}_")
                ):
                    is_master = "_master" in item.name or item.name == f"{job_id}.mp4"
                    is_preview = "_preview" in item.name

                    should_delete = False
                    if is_preview:
                        should_delete = True
                    elif is_master:
                        if eff_keep_days is not None:
                            if eff_keep_days == 0:
                                should_delete = True
                            else:
                                try:
                                    mtime = item.stat().st_mtime
                                    age_days = (
                                        datetime.now(timezone.utc)
                                        - datetime.fromtimestamp(mtime, tz=timezone.utc)
                                    ).total_seconds() / 86400.0
                                    if age_days >= eff_keep_days:
                                        should_delete = True
                                except OSError:
                                    pass
                        elif not eff_keep_master:
                            should_delete = True

                    if should_delete:
                        self.assert_safe_to_delete(item)
                        if item not in files_to_delete:
                            files_to_delete.append(item)

        # In dry run mode, return the list of targets that would be removed
        if eff_dry_run:
            logger.info("Dry run cleanup for job %s: would delete %d items", job_id, len(files_to_delete))
            return files_to_delete

        # Execute deletions with Windows backoff/retries
        deleted_paths: list[Path] = []
        for file_path in files_to_delete:
            if file_path.exists() or file_path.is_symlink() or os.path.islink(file_path):
                self._delete_path_with_backoff(file_path, permanent=eff_permanent)
                deleted_paths.append(file_path)

        # Remove now-empty intermediate directories bottom-up
        directories_to_delete.sort(key=lambda p: len(str(p)), reverse=True)
        for dir_path in directories_to_delete:
            if dir_path.exists() and dir_path.is_dir():
                try:
                    self._delete_path_with_backoff(dir_path, permanent=eff_permanent)
                    deleted_paths.append(dir_path)
                except Exception as e:
                    logger.debug("Non-critical: Directory %s not removed: %s", dir_path, e)

        # Update state manager status if job exists
        if self.state_manager is not None:
            try:
                self.state_manager.update_job_status(job_id, JobStatus.CLEANED)
            except Exception as e:
                logger.debug("Could not transition job status to CLEANED: %s", e)

        logger.info("Cleaned up %d paths for job %s", len(deleted_paths), job_id)
        return deleted_paths

    def _delete_path_with_backoff(self, target: Path, permanent: bool) -> None:
        """Delete a file or directory with exponential retry on Windows locks."""
        self.assert_safe_to_delete(target)

        is_link = target.is_symlink() or os.path.islink(target)
        delays = (0.0,) + self.backoff_delays
        last_error: Exception | None = None

        for delay in delays:
            if delay > 0:
                gc.collect()
                time.sleep(delay)

            try:
                if not target.exists() and not is_link:
                    return

                if permanent or not self.use_recycle_bin:
                    if is_link or target.is_file():
                        target.unlink()
                    elif target.is_dir():
                        shutil.rmtree(target)
                else:
                    send2trash(str(target))
                return
            except (PermissionError, OSError) as err:
                last_error = err
                logger.warning(
                    "Windows file lock encountered on '%s' (delay=%.2fs): %s",
                    target,
                    delay,
                    err,
                )

        raise CleanupError(
            f"Failed to delete '{target}' after {len(delays)} attempts due to file lock: {last_error}"
        )
