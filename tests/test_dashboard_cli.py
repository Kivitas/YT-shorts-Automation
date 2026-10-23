"""Unit tests for AutoShorts FastAPI Dashboard endpoints and CLI argument parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoshorts.cli import build_parser, cmd_clean, cmd_edge_login, cmd_quota, cmd_scheduler, main
from autoshorts.core.config import Settings
from autoshorts.core.models import Job, JobStatus, UploadResult
from autoshorts.core.state_manager import StateManager
from autoshorts.dashboard.app import create_app
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.scheduler import SpreadScheduler
from autoshorts.uploader.studio_playwright import (
    LOGIN_SESSION_STATE,
    update_login_session_state,
)


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

    # Mock cleaner
    mock_cleaner = MagicMock()
    mock_cleaner.cleanup_job.return_value = [tmp_path / "work" / "test_file.mp4"]
    mock_pipeline.cleaner = mock_cleaner

    app = create_app(pipeline=mock_pipeline, state_manager=state_mgr)
    client = TestClient(app, base_url="http://localhost:8765")
    return {
        "client": client,
        "state_mgr": state_mgr,
        "pipeline": mock_pipeline,
        "scheduler": mock_scheduler,
        "cleaner": mock_cleaner,
    }


# =============================================================================
# Dashboard API Endpoint Tests
# =============================================================================


def test_dashboard_index_html(dashboard_client: dict[str, Any]):
    """Verify GET / returns 200 and loads HTML template with UI 2.0 controls."""
    client: TestClient = dashboard_client["client"]
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "AutoShorts" in html
    assert 'id="crop-focus-select"' in html
    assert 'id="skip-sponsors-toggle"' in html
    assert 'id="auto-trim-silence-toggle"' in html
    assert 'id="btn-auto-trim-silence"' in html
    assert 'id="btn-analytics-autotune"' in html
    assert 'id="btn-export-csv"' in html
    assert 'id="btn-export-json"' in html
    assert 'id="edit-job-crop-focus"' in html
    assert 'id="edit-job-title-variants"' in html
    assert 'id="edit-job-candidates-list"' in html


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
    args = parser.parse_args(
        [
            "run",
            "--links",
            "https://youtube.com/watch?v=1234567890a",
            "--bg",
            "subway",
            "--preset",
            "high",
            "--upload",
            "--instructions",
            "Focus on humor",
        ]
    )
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
    args = parser.parse_args(
        ["clean", "--days", "3", "--dry-run", "--job-id", "job_123", "--force"]
    )
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


def test_cli_cmd_scheduler_has_active_uploader(tmp_path: Path):
    """Verify that SpreadScheduler created by cmd_scheduler has an active uploader."""
    from autoshorts.uploader.local_exporter import LocalExportUploader

    db_path = tmp_path / "test_state.db"
    state_mgr = StateManager(db_path=db_path)
    config = Settings()
    config.upload.mode = "local"

    parser = build_parser()
    args = parser.parse_args(["scheduler", "--once"])

    captured_schedulers: list[Any] = []
    orig_init = SpreadScheduler.__init__

    def spy_init(self, *s_args, **s_kwargs):
        orig_init(self, *s_args, **s_kwargs)
        captured_schedulers.append(self)

    with (
        patch.object(SpreadScheduler, "__init__", spy_init),
        patch("autoshorts.cli.SpreadScheduler.run_tick", return_value=None),
    ):
        exit_code = cmd_scheduler(args, config, state_mgr)
        assert exit_code == 0
        assert len(captured_schedulers) == 1
        scheduler = captured_schedulers[0]
        assert scheduler.uploader is not None
        assert isinstance(scheduler.uploader, LocalExportUploader)


def test_cli_main_help(capsys: pytest.CaptureFixture[str]):
    """Verify main([]) prints help and exits with code 0."""
    exit_code = main([])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "AutoShorts v3.2" in captured.out


# =============================================================================
# Background Worker, SSE Log Streaming & Enhanced UI Endpoint Tests
# =============================================================================


@pytest.mark.asyncio
async def test_scheduler_background_worker_lifecycle(tmp_path: Path):
    """Verify background scheduler worker starts in FastAPI lifespan, executes run_tick, and cleanly shuts down."""
    import time

    db_path = tmp_path / "lifecycle_test.db"
    state_mgr = StateManager(db_path=db_path)
    mock_pipeline = MagicMock()
    mock_pipeline.state_manager = state_mgr
    mock_pipeline.scheduler = MagicMock()
    mock_pipeline.scheduler.run_tick = MagicMock(return_value=None)
    mock_pipeline.quota_tracker = QuotaTracker(state_manager=state_mgr)

    # Fast 0.04s interval for testing
    app = create_app(
        pipeline=mock_pipeline,
        state_manager=state_mgr,
        run_scheduler_worker=True,
        scheduler_interval_sec=0.04,
    )

    with TestClient(app, base_url="http://localhost:8765"):
        # Worker should be active
        assert hasattr(app.state, "scheduler_worker_task")
        task = app.state.scheduler_worker_task
        assert not task.done()

        # Allow worker loop to execute tick
        time.sleep(0.12)
        assert mock_pipeline.scheduler.run_tick.call_count >= 1

    # After lifespan context exit, worker task should be cleanly completed/cancelled
    assert task.done()


async def test_realtime_log_stream_sse_and_clear(dashboard_client: dict[str, Any]):
    """Verify GET /api/logs/stream emits a valid SSE frame and POST /api/logs/clear flushes.

    The stream endpoint is intentionally infinite, so it must not be consumed through
    TestClient.stream()/iter_lines(): httpx buffers a streaming body until it completes and
    the test would block forever. Pull exactly one frame from the response iterator instead.
    """
    import logging

    from autoshorts.core.logging_setup import get_log_stream_handler
    from autoshorts.dashboard.app import create_app

    client: TestClient = dashboard_client["client"]
    handler = get_log_stream_handler()
    handler.clear()

    test_logger = logging.getLogger("autoshorts.test")
    test_logger.info("Test SSE log line alpha")

    # The buffered record must be captured before the stream is opened (deterministic).
    assert any("Test SSE log line alpha" in item for item in handler.get_history())

    app = client.app if hasattr(client, "app") else create_app()
    stream_route = next(r for r in app.routes if getattr(r, "path", None) == "/api/logs/stream")

    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/logs/stream",
        "raw_path": b"/api/logs/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"localhost:8765")],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 8765),
    }

    async def _receive() -> dict[str, Any]:
        # A non-disconnect message keeps request.is_disconnected() False so the generator
        # proceeds to emit the buffered log frame.
        return {"type": "http.request", "body": b"", "more_body": False}

    response = await stream_route.endpoint(Request(scope, receive=_receive))
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    iterator = response.body_iterator
    try:
        frame = await anext(iterator)
    finally:
        await iterator.aclose()  # type: ignore[attr-defined]

    frame_text = frame.decode() if isinstance(frame, bytes) else str(frame)
    assert frame_text.startswith(("data: ", ": ping"))
    assert "Test SSE log line alpha" in frame_text

    # Test clearing logs
    res_clear = client.post("/api/logs/clear")
    assert res_clear.status_code == 200
    assert res_clear.json()["success"] is True
    assert not any("Test SSE log line alpha" in item for item in handler.get_history())


def test_in_memory_log_stream_handler_ring_buffer_and_redaction():
    """Verify LogStreamHandler enforces max capacity, redacts secrets, and provides history."""
    import logging

    from autoshorts.core.logging_setup import (
        LogStreamHandler,
        RedactingFilter,
        RedactingFormatter,
        register_secret,
    )

    handler = LogStreamHandler(capacity=5)
    fmt = "%(message)s"
    formatter = RedactingFormatter(fmt)
    handler.setFormatter(formatter)
    handler.addFilter(RedactingFilter())

    test_logger = logging.getLogger("test.ringbuffer")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)

    secret_key = "sk-supersecretkey12345678"
    register_secret(secret_key)

    # Emit 7 logs into capacity=5 handler
    for i in range(7):
        test_logger.info(f"Log {i} with key {secret_key}")

    history = handler.get_history()
    assert len(history) == 5
    # Should only retain the latest 5 logs (2..6)
    assert "Log 0" not in history[0]
    assert "Log 6" in history[-1]
    # Secrets must be redacted
    assert secret_key not in history[-1]
    assert "***REDACTED***" in history[-1]

    handler.clear()
    assert len(handler.get_history()) == 0


def test_dashboard_quota_rolling_rate_and_countdown(dashboard_client: dict[str, Any]):
    """Verify GET /api/quota includes rolling hour uploads gauge and Pacific-Time reset countdown."""
    client: TestClient = dashboard_client["client"]
    res = client.get("/api/quota")
    assert res.status_code == 200
    data = res.json()

    assert "rolling_hour_count" in data
    assert isinstance(data["rolling_hour_count"], int)
    assert "rolling_hour_limit" in data
    assert data["rolling_hour_limit"] == 7
    assert "seconds_until_reset" in data
    assert isinstance(data["seconds_until_reset"], int)
    assert data["seconds_until_reset"] > 0
    assert "reset_time_pt" in data


def test_dashboard_jobs_enriched_with_seo_metadata_and_youtube_link(
    dashboard_client: dict[str, Any],
):
    """Verify GET /api/jobs and GET /api/jobs/{id} enrich job objects with SEO title, description, tags, and video ID."""
    client: TestClient = dashboard_client["client"]
    state_mgr: StateManager = dashboard_client["state_mgr"]

    uploaded_job = Job(
        id="job_seo_test",
        source_url="https://youtube.com/watch?v=1234567890b",
        status=JobStatus.COMPLETED,
        video_metadata={
            "title": "Quantum Computing Breakthrough",
            "channel": "Tech Channel",
            "channel_url": "https://youtube.com/@tech",
            "video_url": "https://youtube.com/watch?v=1234567890b",
            "duration": 120.0,
        },
        upload_result=UploadResult(
            video_id="yt_short_123",
            url="https://youtube.com/shorts/yt_short_123",
            status="uploaded",
            verified=True,
        ),
    )
    state_mgr.save_job(uploaded_job)

    res = client.get("/api/jobs")
    assert res.status_code == 200
    jobs = res.json()["jobs"]
    target = next((j for j in jobs if j["id"] == "job_seo_test"), None)
    assert target is not None

    # Check copy metadata & YouTube link ID
    assert target["youtube_video_id"] == "yt_short_123"
    assert "seo_title" in target
    assert "#Shorts" in target["seo_title"]
    assert "full_description" in target
    assert "ORIGINAL SOURCE ATTRIBUTION" in target["full_description"]
    assert "hashtags" in target
    assert "#Shorts" in target["hashtags"]


def test_cli_parser_edge_login_subcommand():
    """Verify argument parsing for 'autoshorts edge-login'."""
    parser = build_parser()
    args = parser.parse_args(["edge-login", "--timeout", "120"])
    assert args.command == "edge-login"
    assert args.timeout == 120


def test_cli_cmd_edge_login_execution():
    """Verify execution of cmd_edge_login."""
    config = Settings()
    state_mgr = StateManager()
    parser = build_parser()
    args = parser.parse_args(["edge-login", "--timeout", "60"])

    with patch(
        "autoshorts.uploader.studio_playwright.PlaywrightStudioUploader.open_login_session",
        return_value=True,
    ) as mock_login:
        exit_code = cmd_edge_login(args, config, state_mgr)
        assert exit_code == 0
        mock_login.assert_called_once_with(timeout_sec=60.0)

    with patch(
        "autoshorts.uploader.studio_playwright.PlaywrightStudioUploader.open_login_session",
        return_value=False,
    ):
        exit_code = cmd_edge_login(args, config, state_mgr)
        assert exit_code == 1


def test_cli_cmd_scheduler_studio_uploader(tmp_path: Path):
    """Verify cmd_scheduler instantiates PlaywrightStudioUploader when mode == 'studio'."""
    from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader

    db_path = tmp_path / "test_state.db"
    state_mgr = StateManager(db_path=db_path)
    config = Settings()
    config.upload.mode = "studio"

    parser = build_parser()
    args = parser.parse_args(["scheduler", "--once"])

    captured_schedulers: list[Any] = []
    orig_init = SpreadScheduler.__init__

    def spy_init(self, *s_args, **s_kwargs):
        orig_init(self, *s_args, **s_kwargs)
        captured_schedulers.append(self)

    with (
        patch.object(SpreadScheduler, "__init__", spy_init),
        patch("autoshorts.cli.SpreadScheduler.run_tick", return_value=None),
    ):
        exit_code = cmd_scheduler(args, config, state_mgr)
        assert exit_code == 0
        assert len(captured_schedulers) == 1
        scheduler = captured_schedulers[0]
        assert scheduler.uploader is not None
        assert isinstance(scheduler.uploader, PlaywrightStudioUploader)


def test_dashboard_auth_edge_login_endpoint(dashboard_client: dict[str, Any]):
    """Verify POST /api/auth/edge-login triggers background login session."""
    client: TestClient = dashboard_client["client"]
    with patch(
        "autoshorts.uploader.studio_playwright.PlaywrightStudioUploader.open_login_session",
        return_value=True,
    ):
        res = client.post("/api/auth/edge-login")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "sign-in session launched" in data["message"].lower()


def test_dashboard_spark_prompts_endpoint(dashboard_client: dict[str, Any]):
    """Verify GET /api/spark/prompts returns formatted prompt containing expected skill keywords."""
    client: TestClient = dashboard_client["client"]
    res = client.get("/api/spark/prompts")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "prompt" in data
    assert "[GEMINI SPARK SKILL: VIRAL SHORT CURATOR]" in data["prompt"]
    assert "minecraft" in data["prompt"]


def test_dashboard_delete_job_endpoint(dashboard_client: dict[str, Any]):
    """Verify DELETE /api/jobs/{id} triggers cleaner.cleanup_job and returns deleted file info."""
    client: TestClient = dashboard_client["client"]
    state_mgr: StateManager = dashboard_client["state_mgr"]
    cleaner: MagicMock = dashboard_client["cleaner"]

    job = Job(id="job_to_delete", source_url="https://youtu.be/delete_me")
    state_mgr.save_job(job)

    res = client.delete("/api/jobs/job_to_delete")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["job_id"] == "job_to_delete"
    assert data["count"] == 1
    cleaner.cleanup_job.assert_called_once_with("job_to_delete", force=True)

    # 404 for missing job
    res_404 = client.delete("/api/jobs/nonexistent_id")
    assert res_404.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_inbox_watcher_lifespan_polling(tmp_path: Path):
    """Verify SparkInboxWatcher poll_once is executed by scheduler worker during lifespan."""
    import time

    db_path = tmp_path / "inbox_lifespan_test.db"
    state_mgr = StateManager(db_path=db_path)
    mock_pipeline = MagicMock()
    mock_pipeline.state_manager = state_mgr
    mock_pipeline.scheduler = MagicMock()
    mock_pipeline.scheduler.run_tick = MagicMock(return_value=None)
    mock_pipeline.quota_tracker = QuotaTracker(state_manager=state_mgr)

    mock_watcher = MagicMock()
    mock_watcher.poll_once = MagicMock(return_value=[])

    app = create_app(
        pipeline=mock_pipeline,
        state_manager=state_mgr,
        run_scheduler_worker=True,
        scheduler_interval_sec=0.03,
        inbox_watcher=mock_watcher,
    )

    with TestClient(app, base_url="http://localhost:8765"):
        assert app.state.inbox_watcher is mock_watcher
        time.sleep(0.1)
        assert mock_watcher.poll_once.call_count >= 1


def test_dashboard_edge_login_status_endpoint_idle(dashboard_client: dict[str, Any]):
    """Verify GET /api/auth/edge-login/status returns idle state and login_status."""
    client: TestClient = dashboard_client["client"]
    update_login_session_state(status="idle", message="", error=None, channel_name=None)

    with patch(
        "autoshorts.uploader.studio_playwright.PlaywrightStudioUploader.check_login_status",
        return_value={"signed_in": False, "channel_name": None},
    ):
        res = client.get("/api/auth/edge-login/status")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "idle"
        assert data["signed_in"] is False
        assert "login_status" in data
        assert data["login_status"]["signed_in"] is False


def test_dashboard_edge_login_status_endpoint_running(dashboard_client: dict[str, Any]):
    """Verify GET /api/auth/edge-login/status returns running and skips headless check."""
    client: TestClient = dashboard_client["client"]
    update_login_session_state(
        status="running",
        message="Microsoft Edge opened. Please sign in to Google/YouTube Studio...",
        error=None,
        channel_name=None,
    )

    with patch(
        "autoshorts.uploader.studio_playwright.PlaywrightStudioUploader.check_login_status"
    ) as mock_check:
        res = client.get("/api/auth/edge-login/status")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "running"
        assert "Microsoft Edge opened" in data["message"]
        # check_login_status should NOT be called to avoid profile lock collision
        mock_check.assert_not_called()


def test_dashboard_edge_login_status_endpoint_signed_in(dashboard_client: dict[str, Any]):
    """Verify GET /api/auth/edge-login/status returns signed_in state and channel name."""
    client: TestClient = dashboard_client["client"]
    update_login_session_state(
        status="signed_in",
        message="Successfully signed in to YouTube Studio! Session saved.",
        error=None,
        channel_name="Creator Channel X",
    )

    with patch(
        "autoshorts.uploader.studio_playwright.PlaywrightStudioUploader.check_login_status",
        return_value={"signed_in": True, "channel_name": "Creator Channel X"},
    ):
        res = client.get("/api/auth/edge-login/status")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "signed_in"
        assert data["signed_in"] is True
        assert data["channel_name"] == "Creator Channel X"


def test_dashboard_edge_login_status_detected_via_check_login_status(
    dashboard_client: dict[str, Any],
):
    """Verify GET /api/auth/edge-login/status detects authentication from persistent profile check."""
    client: TestClient = dashboard_client["client"]
    update_login_session_state(status="idle", message="", error=None, channel_name=None)

    with patch(
        "autoshorts.uploader.studio_playwright.PlaywrightStudioUploader.check_login_status",
        return_value={"signed_in": True, "channel_name": "Saved Studio Profile"},
    ):
        res = client.get("/api/auth/edge-login/status")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "signed_in"
        assert data["signed_in"] is True
        assert data["channel_name"] == "Saved Studio Profile"


def test_dashboard_edge_login_post_trigger(dashboard_client: dict[str, Any]):
    """Verify POST /api/auth/edge-login sets running state and launches session task."""
    client: TestClient = dashboard_client["client"]
    update_login_session_state(status="idle", message="", error=None, channel_name=None)

    with patch("autoshorts.uploader.studio_playwright.PlaywrightStudioUploader.open_login_session"):
        res = client.post("/api/auth/edge-login")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert LOGIN_SESSION_STATE["status"] == "running"


def test_dashboard_edge_status_alias_endpoint(dashboard_client: dict[str, Any]):
    """Verify GET /api/auth/edge-status returns authenticated status alias."""
    client: TestClient = dashboard_client["client"]
    update_login_session_state(status="idle", message="", error=None, channel_name=None)
    with patch(
        "autoshorts.uploader.studio_playwright.PlaywrightStudioUploader.check_login_status",
        return_value={"signed_in": True, "channel_name": "TestChannel"},
    ):
        res = client.get("/api/auth/edge-status")
        assert res.status_code == 200
        data = res.json()
        assert data["signed_in"] is True
        assert data["channel_name"] == "TestChannel"


def test_dashboard_backgrounds_endpoints(dashboard_client: dict[str, Any], tmp_path: Path):
    """Verify GET /api/backgrounds and POST /api/backgrounds/download endpoints."""
    client: TestClient = dashboard_client["client"]

    # 1. GET /api/backgrounds
    res_get = client.get("/api/backgrounds")
    assert res_get.status_code == 200
    data_get = res_get.json()
    assert "categories" in data_get
    assert "minecraft" in data_get["categories"]
    assert "subway" in data_get["categories"]

    # 2. POST /api/backgrounds/download
    with patch(
        "autoshorts.media.background_fetcher.download_preset_backgrounds",
        return_value={"minecraft": ["mock/mc.mp4"], "subway": ["mock/subway.mp4"]},
    ):
        res_dl = client.post(
            "/api/backgrounds/download", json={"categories": ["minecraft", "subway"]}
        )
        assert res_dl.status_code == 200
        data_dl = res_dl.json()
        assert data_dl["success"] is True
        assert "minecraft" in data_dl["categories"]


def test_dashboard_backgrounds_upload_endpoint(dashboard_client: dict[str, Any]):
    """Verify POST /api/backgrounds/upload handles file uploads cleanly."""
    client: TestClient = dashboard_client["client"]
    dummy_mp4_bytes = b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 100

    files = {"file": ("custom_parkour.mp4", dummy_mp4_bytes, "video/mp4")}
    data = {"category": "minecraft"}

    res = client.post("/api/backgrounds/upload", files=files, data=data)
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["success"] is True
    assert res_data["category"] == "minecraft"
    assert res_data["filename"] == "custom_parkour.mp4"


def test_get_ffmpeg_path_detection():
    """Verify get_ffmpeg_path detects binary via system or imageio_ffmpeg."""
    from autoshorts.media.compositor import get_ffmpeg_path as comp_get_ffmpeg
    from autoshorts.media.encoder_probe import get_ffmpeg_path

    p1 = get_ffmpeg_path()
    p2 = comp_get_ffmpeg()
    assert p1 is not None and len(p1) > 0
    assert p2 is not None and len(p2) > 0
    assert p1 == p2
