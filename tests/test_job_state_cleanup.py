"""A purged job must be able to reach CLEANED from any state where its files can exist."""

from __future__ import annotations

import pytest

from autoshorts.core.models import JobStatus, can_transition


@pytest.mark.parametrize(
    "status",
    [JobStatus.READY_FOR_REVIEW, JobStatus.FAILED, JobStatus.COMPLETED],
)
def test_purge_can_move_job_to_cleaned(status):
    assert can_transition(status, JobStatus.CLEANED)
