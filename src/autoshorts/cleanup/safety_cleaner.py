"""Transactional, path-guarded cleanup worker with Recycle Bin retention.

Enforces strict path safety assertions (intermediate files inside work/ and output/ only),
guarantees non-deletion of protected directories (assets, data, src, config, logs),
requires verified upload confirmation before deleting, supports dry-run mode,
and implements Windows file-lock retry with exponential backoff.
"""

from __future__ import annotations

import errno
import gc
import logging
import os
import shutil
import stat
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoshorts.core.config import CleanupConfig, RetentionConfig, Settings
from autoshorts.core.contracts import Cleaner
from autoshorts.core.exceptions import CleanupError, PathSecurityError
from autoshorts.core.job_lock import JobLockedError, job_lock
from autoshorts.core.models import JobStatus, UploadResult
from autoshorts.core.paths import (
    ALLOWED_CLEANUP_ROOTS,
    FORBIDDEN_PROTECTED_PATHS,
    PROJECT_ROOT,
    get_output_dir,
    get_work_dir,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - presence depends on the installed environment
    from send2trash import send2trash

    _SEND2TRASH_IMPORT_ERROR: str | None = None
except ImportError as _send2trash_exc:  # pragma: no cover
    send2trash = None  # type: ignore[assignment]
    _SEND2TRASH_IMPORT_ERROR = str(_send2trash_exc)


def _resolve_send2trash() -> Any | None:
    """Return the ``send2trash`` callable, or ``None`` when unavailable.

    ``send2trash`` is a declared dependency but must not be required at import time:
    importing it eagerly made every consumer of the pipeline (dashboard, CLI, tests)
    fail to collect when the package was absent from the environment. The module-level
    name is kept patchable so callers/tests can still inject a stub.
    """
    global send2trash, _SEND2TRASH_IMPORT_ERROR
    if send2trash is not None:
        return send2trash
    try:
        from send2trash import send2trash as _send2trash

        send2trash = _send2trash
        _SEND2TRASH_IMPORT_ERROR = None
        return send2trash
    except ImportError as exc:
        _SEND2TRASH_IMPORT_ERROR = str(exc)
        return None


def _handle_remove_readonly(func: Any, path: str, exc: Any) -> None:
    """Clear the read-only bit and retry removal on Windows."""
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def is_job_locked(target_dir: Path, job_id: str | None = None) -> bool:
    """Check if a job directory is currently locked by a live process."""
    jid = job_id or target_dir.name
    candidate_dirs: list[Path] = [target_dir]
    if target_dir.parent != target_dir:
        candidate_dirs.append(target_dir.parent)

    for c_dir in candidate_dirs:
        lock_file = c_dir / f".{jid}.lock"
        if lock_file.exists():
            try:
                with job_lock(c_dir, jid):
                    pass
            except JobLockedError:
                return True
            except OSError:
                return True
    return False


def _calculate_dir_size(path: Path) -> int:
    """Recursively calculate the total byte size of files within path."""
    total = 0
    try:
        if path.is_file() or path.is_symlink():
            return path.stat().st_size
        for root, _, files in os.walk(path, followlinks=False):
            root_path = Path(root)
            for f in files:
                try:
                    fp = root_path / f
                    if not fp.is_symlink():
                        total += fp.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


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
        retention_config: RetentionConfig | None = None,
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
            if isinstance(config, Settings):
                self.retention_config = getattr(config, "retention", RetentionConfig())
            elif isinstance(config, RetentionConfig):
                self.retention_config = config
            else:
                self.retention_config = retention_config or RetentionConfig()
        else:
            self.use_recycle_bin = use_recycle_bin
            self.dry_run = dry_run
            self.keep_master_mp4 = keep_master_mp4
            self.purge_intermediate_work = purge_intermediate_work
            self.keep_final_days = keep_final_days
            self.retention_config = retention_config or RetentionConfig()

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
                raise PathSecurityError(
                    f"Path-guard violation on unresolvable symlink '{raw_path}': {e}"
                ) from e

        # Rule 1: Cannot delete allowed roots or project root itself
        for root_dir in self.allowed_roots + [PROJECT_ROOT.resolve()]:
            if abs_target == root_dir:
                raise PathSecurityError(
                    f"CRITICAL: Attempted deletion of root directory: {abs_target}"
                )

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
        if upload_result is not None:
            is_verified = bool(getattr(upload_result, "verified", False))
        elif verified is not None:
            is_verified = bool(verified)
        elif self.state_manager is not None:
            job = self.state_manager.get_job(job_id)
            if job is not None:
                if job.upload_result is not None:
                    is_verified = bool(getattr(job.upload_result, "verified", False))
                elif job.status in (JobStatus.COMPLETED, JobStatus.CLEANED):
                    is_verified = True

        if not is_verified and not force:
            raise CleanupError(
                f"Cleanup refused for job '{job_id}': upload has not been verified. "
                "Pass verified=True or a verified UploadResult, or use force=True to override."
            )

        eff_dry_run = dry_run if dry_run is not None else self.dry_run
        eff_keep_days = keep_final_days if keep_final_days is not None else self.keep_final_days
        eff_keep_master = (
            keep_master_mp4
            if keep_master_mp4 is not None
            else (False if force else self.keep_master_mp4)
        )
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
                                        datetime.now(UTC) - datetime.fromtimestamp(mtime, tz=UTC)
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
            logger.info(
                "Dry run cleanup for job %s: would delete %d items", job_id, len(files_to_delete)
            )
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

        # Update state manager status only if job exists as a real tracked job
        if self.state_manager is not None:
            try:
                if self.state_manager.get_job(job_id) is not None:
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
                        try:
                            target.unlink()
                        except PermissionError:
                            try:
                                os.chmod(target, stat.S_IWRITE)
                            except OSError:
                                pass
                            target.unlink()
                    elif target.is_dir():
                        if sys.version_info >= (3, 12):  # noqa: UP036
                            shutil.rmtree(target, onexc=_handle_remove_readonly)
                        else:
                            shutil.rmtree(target, onerror=_handle_remove_readonly)
                else:
                    trash_fn = _resolve_send2trash()
                    if trash_fn is None:
                        raise CleanupError(
                            "send2trash is not installed, so the Recycle Bin is unavailable. "
                            f"Install it (pip install send2trash) or run cleanup with "
                            f"permanent=True / use_recycle_bin=false. ({_SEND2TRASH_IMPORT_ERROR})"
                        )
                    trash_fn(str(target))
                return
            except OSError as err:
                # Only lock-like errors are retried; anything else (ENOENT, EINVAL, ...) is raised now.
                is_lock = (
                    isinstance(err, PermissionError)
                    or err.errno in {errno.EACCES, errno.EBUSY, 32, 33}
                    or getattr(err, "winerror", None) in {32, 33}
                )
                if not is_lock:
                    raise
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

    def prune_work_dir(
        self,
        *,
        max_age_days: int | None = None,
        max_total_gb: float | None = None,
        max_total_bytes: int | None = None,
        keep_last_n: int | None = None,
        dry_run: bool | None = None,
        permanent: bool | None = None,
        force: bool = False,
    ) -> list[Path]:
        """Prune work/ directory according to retention policy (age, size, count).

        Guarantees that directories of active or not-yet-uploaded jobs are NEVER deleted:
        - Checks job lock via core.job_lock.
        - Checks state_manager for job status (only COMPLETED/CLEANED or verified uploaded jobs may be pruned).
        - Untracked or unclassified jobs are skipped with a warning log.
        - The N most recent eligible dirs (keep_last_n) are exempt from ALL pruning (age, count, size).
        Uses send2trash or permanent deletion according to cleaner configuration.

        Args:
            max_age_days: Delete job directories older than this many days (default from config: 14).
            max_total_gb: Ceiling for total work/ directory size in GB (default from config: 20).
            max_total_bytes: Explicit byte ceiling overriding max_total_gb (useful for tests).
            keep_last_n: Always retain at least N most recent run folders (default from config: 10).
            dry_run: If True, simulate and return target paths without deleting.
            permanent: If True, delete permanently instead of using Recycle Bin.
            force: If True, bypass retention.enabled check.

        Returns:
            List of Path objects that were deleted (or would be deleted in dry_run).
        """
        ret_cfg = getattr(self, "retention_config", None)
        if not force and ret_cfg is not None and not ret_cfg.enabled:
            logger.info("Work directory retention policy is disabled.")
            return []

        eff_dry_run = (
            dry_run if dry_run is not None else (ret_cfg.dry_run if ret_cfg else self.dry_run)
        )
        eff_max_age_days = (
            max_age_days
            if max_age_days is not None
            else (ret_cfg.work_max_age_days if ret_cfg else 14)
        )
        eff_max_total_gb = (
            max_total_gb
            if max_total_gb is not None
            else (ret_cfg.work_max_total_gb if ret_cfg else 20.0)
        )
        eff_keep_last_n = (
            keep_last_n if keep_last_n is not None else (ret_cfg.keep_last_n if ret_cfg else 10)
        )
        eff_permanent = permanent if permanent is not None else self.permanent

        if max_total_bytes is not None:
            eff_max_bytes: int | None = max_total_bytes
        elif eff_max_total_gb is not None and eff_max_total_gb >= 0:
            eff_max_bytes = int(eff_max_total_gb * (1024**3))
        else:
            eff_max_bytes = None

        if not self.work_dir.exists() or not self.work_dir.is_dir():
            return []

        # Find all run folders inside work_dir (e.g. story_*, longform_*, job_*)
        subdirs: list[Path] = [
            p for p in self.work_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
        ]
        if not subdirs:
            return []

        # 1. Identify protected directories (active, untracked, unclassified, or not yet uploaded)
        protected_dirs: set[Path] = set()
        for d in subdirs:
            job_id = d.name
            # Check A: Job lock
            if is_job_locked(d, job_id):
                logger.debug(
                    "Work directory '%s' is locked by an active process; protecting.", job_id
                )
                protected_dirs.add(d)
                continue

            # Check B: StateManager status
            if self.state_manager is None:
                logger.warning(
                    "Work directory '%s' cannot be classified (no state manager configured); skipping pruning for safety.",
                    job_id,
                )
                protected_dirs.add(d)
                continue

            job = self.state_manager.get_job(job_id)
            if job is None:
                logger.warning(
                    "Work directory '%s' has untracked job state; skipping pruning for safety.",
                    job_id,
                )
                protected_dirs.add(d)
                continue

            # Check upload verification status:
            # If upload_result exists, its .verified flag is authoritative.
            # Even if status is COMPLETED, if upload_result.verified is False, it MUST be protected.
            if job.upload_result is not None:
                is_uploaded = bool(getattr(job.upload_result, "verified", False))
            else:
                is_uploaded = job.status in (JobStatus.COMPLETED, JobStatus.CLEANED)

            if not is_uploaded:
                logger.debug(
                    "Job '%s' has status %s and unverified upload; protecting.",
                    job_id,
                    job.status,
                )
                protected_dirs.add(d)
                continue

        # Calculate sizes and modification times
        dir_sizes: dict[Path, int] = {d: _calculate_dir_size(d) for d in subdirs}
        total_work_size = sum(dir_sizes.values())

        # Sort eligible directories by mtime descending (newest first)
        eligible = [d for d in subdirs if d not in protected_dirs]
        eligible.sort(key=lambda d: d.stat().st_mtime, reverse=True)

        to_prune: list[Path] = []
        to_prune_set: set[Path] = set()

        def _mark_for_prune(d: Path) -> None:
            if d not in to_prune_set:
                to_prune.append(d)
                to_prune_set.add(d)

        now_ts = datetime.now(UTC).timestamp()

        # Semantics: keep_last_n most recent eligible dirs are exempt from ALL pruning (age, count, size)
        keep_count = max(0, eff_keep_last_n) if eff_keep_last_n is not None else 0
        candidates = eligible[keep_count:]

        # Step 1: Prune candidates by age (work_max_age_days)
        if eff_max_age_days is not None and eff_max_age_days >= 0:
            for d in candidates:
                try:
                    mtime = d.stat().st_mtime
                    age_days = (now_ts - mtime) / 86400.0
                    if age_days >= eff_max_age_days:
                        _mark_for_prune(d)
                except OSError:
                    pass

        # Step 2: Prune candidates by size (work_max_total_gb / eff_max_bytes)
        # Note: count-based pruning does not delete fresh dirs by itself; only candidates beyond keep_last_n can be pruned for age or size.
        if eff_max_bytes is not None and eff_max_bytes >= 0:
            pruned_size = sum(dir_sizes.get(d, 0) for d in to_prune)
            projected_size = total_work_size - pruned_size
            if projected_size > eff_max_bytes:
                remaining_candidates = [d for d in candidates if d not in to_prune_set]
                for d in reversed(remaining_candidates):
                    if projected_size <= eff_max_bytes:
                        break
                    _mark_for_prune(d)
                    projected_size -= dir_sizes.get(d, 0)

        # In dry run mode, return the list of targets that would be removed
        if eff_dry_run:
            logger.info(
                "Dry run retention pruning: would delete %d work directories", len(to_prune)
            )
            return to_prune

        deleted_paths: list[Path] = []
        for d in to_prune:
            if not d.exists() and not (d.is_symlink() or os.path.islink(d)):
                continue
            job_id = d.name

            # Re-check job lock right before deleting
            if is_job_locked(d, job_id):
                logger.warning(
                    "Work directory '%s' is locked by an active process; skipping deletion.",
                    job_id,
                )
                continue

            try:
                with job_lock(self.work_dir, job_id):
                    self.assert_safe_to_delete(d)
                    self._delete_path_with_backoff(d, permanent=eff_permanent)
                    deleted_paths.append(d)

                    # Remove associated loose files in work_dir with prefix {job_id}. or {job_id}_
                    if self.work_dir.exists():
                        for loose in list(self.work_dir.iterdir()):
                            if loose.name == f".{job_id}.lock":
                                continue
                            if loose.name.startswith(f"{job_id}.") or loose.name.startswith(
                                f"{job_id}_"
                            ):
                                if loose.is_file() or loose.is_symlink():
                                    try:
                                        self.assert_safe_to_delete(loose)
                                        self._delete_path_with_backoff(
                                            loose, permanent=eff_permanent
                                        )
                                        deleted_paths.append(loose)
                                    except Exception as e:
                                        logger.debug("Could not remove loose file %s: %s", loose, e)
            except JobLockedError:
                logger.warning(
                    "Work directory '%s' was acquired by another process before deletion; skipping.",
                    job_id,
                )
                continue
            except Exception as e:
                logger.warning("Failed to prune work directory '%s': %s", d, e)
                continue

            # Clean dangling lock file if present
            lock_file = self.work_dir / f".{job_id}.lock"
            if lock_file.exists() and lock_file.is_file():
                try:
                    self.assert_safe_to_delete(lock_file)
                    self._delete_path_with_backoff(lock_file, permanent=eff_permanent)
                except Exception:
                    pass

            # Update StateManager status to CLEANED only for real tracked job ids
            if self.state_manager is not None:
                try:
                    if self.state_manager.get_job(job_id) is not None:
                        self.state_manager.update_job_status(job_id, JobStatus.CLEANED)
                except Exception as e:
                    logger.debug("Could not transition job %s status to CLEANED: %s", job_id, e)

        logger.info("Retention policy pruned %d paths from work directory.", len(deleted_paths))
        return deleted_paths


def prune_work_dir(
    work_dir: Path | str | None = None,
    output_dir: Path | str | None = None,
    config: Settings | RetentionConfig | CleanupConfig | None = None,
    state_manager: Any | None = None,
    max_age_days: int | None = None,
    max_total_gb: float | None = None,
    max_total_bytes: int | None = None,
    keep_last_n: int | None = None,
    dry_run: bool | None = None,
    permanent: bool | None = None,
    allowed_roots: Sequence[Path | str] | None = None,
    force: bool = False,
) -> list[Path]:
    """Safely prune work/ directory according to retention policy."""
    cleaner = SafetyCleaner(
        config=config if isinstance(config, (Settings, CleanupConfig)) else None,
        retention_config=config if isinstance(config, RetentionConfig) else None,
        work_dir=work_dir,
        output_dir=output_dir,
        state_manager=state_manager,
        allowed_roots=allowed_roots,
        dry_run=dry_run if dry_run is not None else False,
        permanent=permanent if permanent is not None else False,
    )
    return cleaner.prune_work_dir(
        max_age_days=max_age_days,
        max_total_gb=max_total_gb,
        max_total_bytes=max_total_bytes,
        keep_last_n=keep_last_n,
        dry_run=dry_run,
        permanent=permanent,
        force=force,
    )


__all__ = [
    "SafetyCleaner",
    "is_job_locked",
    "prune_work_dir",
]
