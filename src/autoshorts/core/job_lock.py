"""Cross-process lock per job, so two processes never render the same job at once.

The CLI and the dashboard share one state database. A job enqueued by one process can be
auto-dispatched by the other, and both would then write the same temporary master file.
An OS-level file lock held for the whole job prevents that. The lock is released by the OS
if the process dies, so a crash never leaves a job permanently locked.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO


class JobLockedError(RuntimeError):
    """Raised when another process is already working on the job."""


def _lock_handle(handle: IO[bytes]) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_handle(handle: IO[bytes]) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def job_lock(work_dir: Path, job_id: str) -> Iterator[Path]:
    """Hold an exclusive lock on the job for the duration of the block.

    Raises JobLockedError immediately if another process holds it.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    lock_path = work_dir / f".{job_id}.lock"
    handle = open(lock_path, "a+b")  # noqa: SIM115 - held open for the whole block
    try:
        try:
            _lock_handle(handle)
        except OSError as exc:
            raise JobLockedError(
                f"Job {job_id} is already being processed by another AutoShorts process."
            ) from exc
        yield lock_path
    finally:
        try:
            _unlock_handle(handle)
        except OSError:
            pass
        handle.close()
