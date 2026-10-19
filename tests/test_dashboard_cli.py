"""Unit tests for AutoShorts FastAPI Dashboard endpoints and CLI argument parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoshorts.cli import build_parser, cmd_clean, cmd_quota, cmd_scheduler, main
from autoshorts.core.config import Settings
from autoshorts.core.models import Job, JobStatus, UploadResult
from autoshorts.core.state_manager import StateManager
from autoshorts.dashboard.app import create_app
from autoshorts.quota.quota_tracker import QuotaTracker


@pytest.fixture
def dashboard_client(tmp_path: Path):
    """Create a FastAPI TestClient with an isolated test SQLite database."""
    db_path = tmp_path / "dashboard_test.db"
    state_mgr = StateManager(db_path=db_path)
    mock_pipeline = MagicMock()
    mock_pipeline.state_manager = state_mgr

    # Mock quota tracker
    quota_tracker = QuotaTracker(state_manager=state_mgr)
    mock_pipeline.quota_tracker = quota_tracker

    # Mock scheduler
    mock_scheduler = MagicMock()
    mock_scheduler.is_paused.return_value = False
    mock_pipeline.scheduler = mock_scheduler

    app = create_app(pipeline=mock_pipeline, state_manager=state_mgr)
    client = TestClient(app)
    return {
        "client": client,
        "state_mgr": state_mgr,
        "pipeline": mock_pipeline,
        "scheduler": mock_scheduler,
    }


# =============================================================================
# Dashboard API Endpoint Tests
# =============================================================================

def test_dashboard_index_html(dashboard_client: dict[str, Any]):
    """Verify GET / returns 200 and loads HTML template."""
    client: TestClient = dashboard_client["client"]
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "AutoShorts" in response.text


def test_dashboard_health_endpoint(dashboard_client: dict[str, Any]):
    """Verify GET /api/health returns system hardware and provider telemetry."""
    client: TestClient = dashboard_client["client"]
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "CPU" in data["cpu_mode"]
    assert "ffmpeg_encoder" in data


def test_dashboard_quota_endpoint(dashboard_client: dict[str, Any]):
    """Verify GET /api/quota returns dual-bucket metrics and daily cap."""
    client: TestClient = dashboard_client["client"]
    response = client.get("/api/quota")
    assert response.status_code == 200
    data = response.json()
    assert "upload_spent" in data
    assert "upload_limit" in data
    assert data["upload_limit"] == 100
    assert "general_spent" in data
    assert "general_limit" in data
    assert data["general_limit"] == 10000
    assert "completed_today" in data


def test_dashboard_scheduler_toggle(dashboard_client: dict[str, Any]):
    """Verify POST /api/scheduler/toggle pauses and resumes scheduler."""
    client: TestClient = dashboard_client["client"]
    scheduler: MagicMock = dashboard_client["scheduler"]

    paused_state = {"val": False}
    scheduler.is_paused.side_effect = lambda: paused_state["val"]

    def do_pause():
        paused_state["val"] = True

    def do_resume():
        paused_state["val"] = False

    scheduler.pause.side_effect = do_pause
    scheduler.resume.side_effect = do_resume

    # Toggle to paused
    res1 = client.post("/api/scheduler/toggle", json={"paused": True})
    assert res1.status_code == 200
    assert res1.json()["paused"] is True
    scheduler.pause.assert_called_once()

    # Toggle to resumed
    res2 = client.post("/api/scheduler/toggle", json={"paused": False})
    assert res2.status_code == 200
    assert res2.json()["paused"] is False
    scheduler.resume.assert_called_once()


def test_dashboard_submit_jobs_and_list(dashboard_client: dict[str, Any]):
    """Verify POST /api/jobs/submit enqueues jobs and GET /api/jobs lists them."""
    client: TestClient = dashboard_client["client"]
    pipeline: MagicMock = dashboard_client["pipeline"]
    state_mgr: StateManager = dashboard_client["state_mgr"]

    # Mock submit_links
    dummy_job = Job(id="job_api_test", source_url="https://youtube.com/watch?v=1234567890a")
    state_mgr.save_job(dummy_job)
    pipeline.submit_links.return_value = [dummy_job]

    payload = {
        "links": "https://youtube.com/watch?v=1234567890a : Test debate",
        "background": "subway",
        "dopamine": "high",
        "upload_mode": "local",
        "auto_upload": False,
    }

    res_submit = client.post("/api/jobs/submit", json=payload)
    assert res_submit.status_code == 200
    data = res_submit.json()
    assert data["success"] is True
    assert data["count"] == 1

    # List jobs
    res_list = client.get("/api/jobs")
    assert res_list.status_code == 200
    jobs_data = res_list.json()["jobs"]
    assert len(jobs_data) >= 1
    assert any(j["id"] == "job_api_test" for j in jobs_data)


def test_dashboard_job_details(dashboard_client: dict[str, Any]):
    """Verify GET /api/jobs/{id} returns job details and stages."""
    client: TestClient = dashboard_client["client"]
    state_mgr: StateManager = dashboard_client["state_mgr"]

    job = Job(id="job_detail_1", source_url="https://youtu.be/det1")
    state_mgr.save_job(job)
    stage_id = state_mgr.record_stage_start("job_detail_1", "metadata_fetch")
    state_mgr.record_stage_complete(stage_id, "COMPLETED", {"title": "Test Title"})

    res = client.get("/api/jobs/job_detail_1")
    assert res.status_code == 200
    data = res.json()
    assert data["job"]["id"] == "job_detail_1"
    assert len(data["stages"]) == 1
    assert data["stages"][0]["stage_name"] == "metadata_fetch"

    # Nonexistent job
    res_404 = client.get("/api/jobs/nonexistent_id")
    assert res_404.status_code == 404


def test_dashboard_spark_import_endpoint(dashboard_client: dict[str, Any]):
    """Verify POST /api/spark/import parses Spark text input."""
    client: TestClient = dashboard_client["client"]

    spark_text = """```json
{
  "source_url": "https://www.youtube.com/watch?v=spark123456",
  "clip_title": "Coding secrets",
  "start_time": "01:15",
  "end_time": "01:55",
  "hook_text": "Stop scrolling right now!"
}
```"""
    res = client.post("/api/spark/import", json={"text": spark_text})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["count"] == 1
    hint = data["hints"][0]
    assert hint["start_sec"] == 75.0
    assert hint["end_sec"] == 115.0


def test_dashboard_video_streaming_404_when_missing(dashboard_client: dict[str, Any]):
    """Verify GET /api/video/{id}/preview returns 404 when file does not exist."""
    client: TestClient = dashboard_client["client"]
    state_mgr: StateManager = dashboard_client["state_mgr"]

    job = Job(id="job_no_file", source_url="https://youtu.be/test")
    state_mgr.save_job(job)

    res = client.get("/api/video/job_no_file/preview")
    assert res.status_code == 404


def test_dashboard_upload_now_endpoint(dashboard_client: dict[str, Any]):
    """Verify POST /api/jobs/{id}/upload-now triggers immediate upload."""
    client: TestClient = dashboard_client["client"]
    pipeline: MagicMock = dashboard_client["pipeline"]
    state_mgr: StateManager = dashboard_client["state_mgr"]

    job = Job(
        id="job_upload_btn",
        source_url="https://youtu.be/test",
        status=JobStatus.READY_FOR_REVIEW,
        rendered_video_path="work/test.mp4",
    )
    state_mgr.save_job(job)

    mock_res = UploadResult(
        video_id="vid_uploaded_btn",
        url="https://youtube.com/shorts/vid_uploaded_btn",
        status="uploaded",
        verified=True,
    )
    pipeline._run_immediate_upload.return_value = mock_res

    res = client.post("/api/jobs/job_upload_btn/upload-now")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["upload_result"]["video_id"] == "vid_uploaded_btn"


# =============================================================================
# CLI Command Argument Parsing & Execution Tests
# =============================================================================

def test_cli_parser_run_subcommand():
    """Verify argument parsing for 'autoshorts run'."""
    parser = build_parser()
    args = parser.parse_args([
        "run",
        "--links", "https://youtube.com/watch?v=1234567890a",
        "--bg", "subway",
        "--preset", "high",
        "--upload",
        "--instructions", "Focus on humor",
    ])
    assert args.command == "run"
    assert args.links == "https://youtube.com/watch?v=1234567890a"
    assert args.bg == "subway"
    assert args.preset == "high"
    assert args.upload is True
    assert args.instructions == "Focus on humor"


def test_cli_parser_dashboard_subcommand():
    """Verify argument parsing for 'autoshorts dashboard'."""
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--port", "9000", "--host", "0.0.0.0", "--no-browser"])
    assert args.command == "dashboard"
    assert args.port == 9000
    assert args.host == "0.0.0.0"
    assert args.no_browser is True


def test_cli_parser_scheduler_subcommand():
    """Verify argument parsing for 'autoshorts scheduler'."""
    parser = build_parser()
    args = parser.parse_args(["scheduler", "--daemon", "--interval", "45"])
    assert args.command == "scheduler"
    assert args.daemon is True
    assert args.interval == 45


def test_cli_parser_quota_subcommand():
    """Verify argument parsing for 'autoshorts quota'."""
    parser = build_parser()
    args = parser.parse_args(["quota", "--json"])
    assert args.command == "quota"
    assert args.json is True


def test_cli_parser_clean_subcommand():
    """Verify argument parsing for 'autoshorts clean'."""
    parser = build_parser()
    args = parser.parse_args(["clean", "--days", "3", "--dry-run", "--job-id", "job_123", "--force"])
    assert args.command == "clean"
    assert args.days == 3
    assert args.dry_run is True
    assert args.job_id == "job_123"
    assert args.force is True


def test_cli_cmd_quota_execution(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify execution of cmd_quota."""
    db_path = tmp_path / "test_state.db"
    state_mgr = StateManager(db_path=db_path)
    config = Settings()

    parser = build_parser()
    args = parser.parse_args(["quota"])

    exit_code = cmd_quota(args, config, state_mgr)
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "QUOTA STATUS" in captured.out
    assert "Upload Bucket" in captured.out


def test_cli_cmd_clean_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """Verify execution of cmd_clean in dry-run mode."""
    db_path = tmp_path / "test_state.db"
    state_mgr = StateManager(db_path=db_path)
    config = Settings()

    parser = build_parser()
    args = parser.parse_args(["clean", "--dry-run"])

    exit_code = cmd_clean(args, config, state_mgr)
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "Cleanup summary: Would delete 0 items" in captured.out


def test_cli_cmd_scheduler_once(tmp_path: Path):
    """Verify execution of cmd_scheduler with --once flag."""
    db_path = tmp_path / "test_state.db"
    state_mgr = StateManager(db_path=db_path)
    config = Settings()

    parser = build_parser()
    args = parser.parse_args(["scheduler", "--once"])

    with patch("autoshorts.cli.SpreadScheduler.run_tick", return_value=None) as mock_tick:
        exit_code = cmd_scheduler(args, config, state_mgr)
        assert exit_code == 0
        mock_tick.assert_called_once()


def test_cli_main_help(capsys: pytest.CaptureFixture[str]):
    """Verify main([]) prints help and exits with code 0."""
    exit_code = main([])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "AutoShorts v3.2" in captured.out
