"""Unit tests for ParallelJobExecutor and Parallel Job Dashboard API endpoints."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from autoshorts.core.models import Job, JobStatus
from autoshorts.core.parallel_runner import ParallelJobExecutor
from autoshorts.core.state_manager import StateManager
from autoshorts.dashboard.app import create_app


@pytest.fixture
def test_env(tmp_path: Path):
    """Fixture providing isolated StateManager, mock ShortsPipeline, and ParallelJobExecutor."""
    db_path = tmp_path / "test_parallel.db"
    state_mgr = StateManager(db_path=db_path)

    mock_pipeline = MagicMock()
    mock_pipeline.state_manager = state_mgr

    executor = ParallelJobExecutor(
        pipeline=mock_pipeline,
        state_manager=state_mgr,
        max_workers=2,
    )

    return {
        "state_mgr": state_mgr,
        "pipeline": mock_pipeline,
        "executor": executor,
        "db_path": db_path,
    }


def test_concurrent_execution_up_to_max_workers(test_env: dict):
    """Verify that multiple jobs run concurrently up to max_workers capacity."""
    executor: ParallelJobExecutor = test_env["executor"]
    pipeline: MagicMock = test_env["pipeline"]
    state_mgr: StateManager = test_env["state_mgr"]

    # Slow down mock pipeline process_job to verify concurrency
    started_jobs: list[str] = []

    def mock_process(job_or_id, **kwargs):
        job_id = job_or_id.id if hasattr(job_or_id, "id") else str(job_or_id)
        started_jobs.append(job_id)
        time.sleep(0.15)
        return job_or_id

    pipeline.process_job.side_effect = mock_process

    job1 = Job(id="job_c1", source_url="https://youtube.com/watch?v=1", status=JobStatus.QUEUED)
    job2 = Job(id="job_c2", source_url="https://youtube.com/watch?v=2", status=JobStatus.QUEUED)
    state_mgr.save_job(job1)
    state_mgr.save_job(job2)

    fut1 = executor.submit_job(job1)
    fut2 = executor.submit_job(job2)

    assert fut1 is not None
    assert fut2 is not None

    # Both should be active concurrently
    active = executor.active_job_ids
    assert "job_c1" in active
    assert "job_c2" in active
    assert executor.active_count == 2
    assert executor.is_job_active("job_c1") is True

    # Wait for completion
    fut1.result(timeout=2.0)
    fut2.result(timeout=2.0)

    # After completion, active count should drop back to 0
    assert executor.active_count == 0
    assert len(executor.active_job_ids) == 0
    executor.shutdown(wait=True)


def test_duplicate_submission_prevention(test_env: dict):
    """Verify that submitting an already active job returns existing future and doesn't spawn redundant workers."""
    executor: ParallelJobExecutor = test_env["executor"]
    pipeline: MagicMock = test_env["pipeline"]
    state_mgr: StateManager = test_env["state_mgr"]

    call_count = 0

    def mock_process(job_or_id, **kwargs):
        nonlocal call_count
        call_count += 1
        time.sleep(0.2)
        return job_or_id

    pipeline.process_job.side_effect = mock_process

    job = Job(id="job_dup", source_url="https://youtube.com/watch?v=dup", status=JobStatus.QUEUED)
    state_mgr.save_job(job)

    fut1 = executor.submit_job(job)
    fut2 = executor.submit_job(job)  # Duplicate submission

    assert fut1 is not None
    assert fut2 is fut1
    assert executor.active_count == 1

    fut1.result(timeout=2.0)
    assert call_count == 1
    executor.shutdown(wait=True)


def test_poll_queued_jobs_picks_up_queued_jobs(test_env: dict):
    """Verify poll_queued_jobs queries StateManager for QUEUED jobs up to available slots."""
    executor: ParallelJobExecutor = test_env["executor"]
    pipeline: MagicMock = test_env["pipeline"]
    state_mgr: StateManager = test_env["state_mgr"]

    def mock_process(job_or_id, **kwargs):
        time.sleep(0.2)
        return job_or_id

    pipeline.process_job.side_effect = mock_process

    # Create 3 queued jobs, but executor max_workers is 2
    for i in range(1, 4):
        j = Job(
            id=f"job_q{i}", source_url=f"https://youtube.com/watch?v=q{i}", status=JobStatus.QUEUED
        )
        state_mgr.save_job(j)

    dispatched = executor.poll_queued_jobs()
    # Should only dispatch up to max_workers = 2 slots
    assert len(dispatched) == 2
    assert executor.active_count == 2

    # A second poll while 2 are running should dispatch 0 because slots are full
    second_poll = executor.poll_queued_jobs()
    assert len(second_poll) == 0

    executor.shutdown(wait=True)


def test_api_parallel_status_and_process_queue(tmp_path: Path):
    """Test /api/jobs/parallel-status and /api/jobs/process-queue endpoints via FastAPI TestClient."""
    db_path = tmp_path / "dashboard_test.db"
    state_mgr = StateManager(db_path=db_path)

    mock_pipeline = MagicMock()
    mock_pipeline.state_manager = state_mgr
    mock_pipeline.process_job.side_effect = lambda j, **kwargs: j

    # Seed 2 queued jobs
    j1 = Job(id="job_api_1", source_url="https://youtube.com/watch?v=api1", status=JobStatus.QUEUED)
    j2 = Job(id="job_api_2", source_url="https://youtube.com/watch?v=api2", status=JobStatus.QUEUED)
    state_mgr.save_job(j1)
    state_mgr.save_job(j2)

    app = create_app(pipeline=mock_pipeline, state_manager=state_mgr, run_scheduler_worker=False)
    with TestClient(app, base_url="http://localhost:8765") as client:
        # Check parallel-status
        res = client.get("/api/jobs/parallel-status")
        assert res.status_code == 200
        data = res.json()
        assert "active_count" in data
        assert "max_workers" in data
        assert "active_job_ids" in data
        assert isinstance(data["active_job_ids"], list)

        # Trigger process-queue
        res_pq = client.post("/api/jobs/process-queue")
        assert res_pq.status_code == 200
        pq_data = res_pq.json()
        assert pq_data["success"] is True
        assert "dispatched_count" in pq_data
        assert "dispatched_job_ids" in pq_data
        assert len(pq_data["dispatched_job_ids"]) >= 1


def test_api_submit_and_retry_uses_parallel_runner(tmp_path: Path):
    """Test that /api/jobs/submit, /api/jobs/{id}/retry, and /api/jobs/batch-retry dispatch via parallel_runner."""
    db_path = tmp_path / "dashboard_submit_test.db"
    state_mgr = StateManager(db_path=db_path)

    mock_pipeline = MagicMock()
    mock_pipeline.state_manager = state_mgr

    # Pipeline submit_links returns Job objects
    created_job = Job(
        id="job_sub_1", source_url="https://youtube.com/watch?v=sub1", status=JobStatus.QUEUED
    )
    mock_pipeline.submit_links.return_value = [created_job]
    mock_pipeline.process_job.side_effect = lambda j, **kwargs: j

    app = create_app(pipeline=mock_pipeline, state_manager=state_mgr, run_scheduler_worker=False)
    with TestClient(app, base_url="http://localhost:8765") as client:
        # 1. Submit job
        res = client.post(
            "/api/jobs/submit",
            json={
                "links": "https://youtube.com/watch?v=sub1",
                "style_preset": "rapid",
                "dopamine": "extreme",
                "background": "subway",
            },
        )
        assert res.status_code == 200
        assert res.json()["success"] is True

        # 2. Retry single failed job
        failed_job = Job(
            id="job_fail_1", source_url="https://youtube.com/watch?v=fail1", status=JobStatus.FAILED
        )
        state_mgr.save_job(failed_job)

        res_retry = client.post(f"/api/jobs/{failed_job.id}/retry")
        assert res_retry.status_code == 200
        assert res_retry.json()["success"] is True

        # 3. Batch retry
        failed_job2 = Job(
            id="job_fail_2", source_url="https://youtube.com/watch?v=fail2", status=JobStatus.FAILED
        )
        state_mgr.save_job(failed_job2)

        res_batch = client.post("/api/jobs/batch-retry")
        assert res_batch.status_code == 200
        assert res_batch.json()["success"] is True
        assert res_batch.json()["count"] >= 1
