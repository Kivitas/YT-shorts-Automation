"""Age- and size-based retention for rendered files in output/.

``select_expired_outputs`` is a pure selection function: it inspects the directory
and returns the files that should go, oldest first, without touching anything.
``delete_output_files`` applies such a selection through send2trash (Recycle Bin)
and defaults to dry-run. Nothing in this module runs automatically.

Only ``{job_id}_master.mp4`` and ``{job_id}_preview.mp4`` files are managed. Any
other file in output/ is ignored. Files whose job id is in ``protected_job_ids``
(jobs not yet uploaded/verified) are never selected.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from autoshorts.cleanup.safety_cleaner import _resolve_send2trash
from autoshorts.core.exceptions import CleanupError, PathSecurityError

logger = logging.getLogger(__name__)

_GIB = 1024**3
_SECONDS_PER_DAY = 86400.0
_MANAGED_SUFFIXES = ("_master.mp4", "_preview.mp4")


def job_id_from_output_name(name: str) -> str | None:
    """Return the job id for a managed output file name, or None if not managed.

    Job ids may contain underscores, so only the known suffixes are stripped.
    """
    for suffix in _MANAGED_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return None


def select_expired_outputs(
    output_dir: Path | str,
    now: datetime,
    max_age_days: float | None,
    max_total_gb: float | None,
    protected_job_ids: Iterable[str],
) -> list[Path]:
    """Select managed output files that should be deleted.

    Rules, applied in order:
      1. Any file whose mtime is more than ``max_age_days`` before ``now`` is selected
         (``None`` disables the age rule).
      2. If the managed files still total more than ``max_total_gb`` GiB, the oldest
         remaining files are selected until the total is at or under the cap
         (``None`` disables the size rule). The total counts protected files too,
         since they still occupy disk, but protected files are never selected.

    Only regular files named ``{job_id}_master.mp4`` or ``{job_id}_preview.mp4`` are
    considered. Symlinks, directories and other files are ignored.

    Args:
        output_dir: Directory holding rendered outputs. A missing directory yields [].
        now: Reference time. Naive datetimes are interpreted in local time.
        max_age_days: Maximum file age in days, or None to skip the age rule.
        max_total_gb: Maximum total size of managed files in GiB, or None to skip.
        protected_job_ids: Job ids whose files must never be selected.

    Returns:
        Paths to delete, oldest first.

    Raises:
        ValueError: If a limit is negative.
    """
    if max_age_days is not None and max_age_days < 0:
        raise ValueError("max_age_days must be >= 0")
    if max_total_gb is not None and max_total_gb < 0:
        raise ValueError("max_total_gb must be >= 0")

    out = Path(output_dir)
    if not out.is_dir():
        return []

    protected = set(protected_job_ids)

    # (mtime, size, job_id, path) for every managed regular file.
    managed: list[tuple[float, int, str, Path]] = []
    for entry in out.iterdir():
        job_id = job_id_from_output_name(entry.name)
        if job_id is None or entry.is_symlink() or not entry.is_file():
            continue
        st = entry.stat()
        managed.append((st.st_mtime, st.st_size, job_id, entry))

    # Oldest first; name as a deterministic tie-breaker.
    managed.sort(key=lambda item: (item[0], item[3].name))

    selected: set[int] = set()  # indices into `managed`

    if max_age_days is not None:
        cutoff = now.timestamp() - max_age_days * _SECONDS_PER_DAY
        for idx, (mtime, _size, job_id, _path) in enumerate(managed):
            if mtime < cutoff and job_id not in protected:
                selected.add(idx)

    if max_total_gb is not None:
        limit_bytes = max_total_gb * _GIB
        total = sum(size for _mtime, size, _jid, _p in managed)
        total -= sum(managed[idx][1] for idx in selected)
        for idx, (_mtime, size, job_id, _path) in enumerate(managed):
            if total <= limit_bytes:
                break
            if idx in selected or job_id in protected:
                continue
            selected.add(idx)
            total -= size

    return [managed[idx][3] for idx in sorted(selected)]


def delete_output_files(
    output_dir: Path | str,
    paths: Iterable[Path | str],
    *,
    dry_run: bool = True,
) -> list[Path]:
    """Move previously selected output files to the Recycle Bin.

    Every path must be a managed file directly inside ``output_dir``; anything else
    raises ``PathSecurityError`` before any file is touched. Deletion goes through
    send2trash. Permanent deletion is not offered here.

    Args:
        output_dir: The output directory the paths were selected from.
        paths: Files to remove, typically the result of ``select_expired_outputs``.
        dry_run: If True (the default), only report what would be trashed.

    Returns:
        Paths that were trashed, or that would be trashed in dry-run mode.

    Raises:
        PathSecurityError: If a path is outside output_dir or not a managed file.
        CleanupError: If send2trash is not installed and dry_run is False.
    """
    out_root = Path(output_dir).resolve()
    targets = [Path(p) for p in paths]

    for target in targets:
        resolved = target.resolve()
        if resolved.parent != out_root or job_id_from_output_name(resolved.name) is None:
            raise PathSecurityError(
                f"Refusing to delete '{target}': not a managed file directly inside {out_root}."
            )

    if dry_run:
        logger.info("Dry run output retention: would trash %d files", len(targets))
        return targets

    trash_fn = _resolve_send2trash()
    if trash_fn is None:
        raise CleanupError("send2trash is not installed, so output files cannot be trashed.")

    trashed: list[Path] = []
    for target in targets:
        if not target.exists():
            continue
        try:
            trash_fn(str(target))
            trashed.append(target)
        except OSError as exc:
            logger.warning("Could not trash output file '%s': %s", target, exc)
    logger.info("Output retention trashed %d files", len(trashed))
    return trashed


__all__ = [
    "delete_output_files",
    "job_id_from_output_name",
    "select_expired_outputs",
]
