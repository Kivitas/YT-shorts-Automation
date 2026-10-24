"""Cross-process job lock: a second holder of the same job must be refused."""

from __future__ import annotations

import pytest

from autoshorts.core.job_lock import JobLockedError, job_lock


def test_second_holder_of_same_job_is_refused(tmp_path):
    with job_lock(tmp_path, "job1"), pytest.raises(JobLockedError), job_lock(tmp_path, "job1"):
        pass


def test_lock_is_released_after_block(tmp_path):
    with job_lock(tmp_path, "job1"):
        pass
    with job_lock(tmp_path, "job1"):
        pass


def test_lock_is_released_when_block_raises(tmp_path):
    with pytest.raises(RuntimeError), job_lock(tmp_path, "job1"):
        raise RuntimeError("stage failed")
    with job_lock(tmp_path, "job1"):
        pass


def test_different_jobs_do_not_conflict(tmp_path):
    with job_lock(tmp_path, "job1"), job_lock(tmp_path, "job2"):
        pass
