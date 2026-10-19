"""Foundation tests covering configuration, state manager, redaction, atomic writes, and contracts."""

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoshorts.core.config import Settings, load_config
from autoshorts.core.contracts import (
    Cleaner,
    Compositor,
    Decider,
    Downloader,
    EditPlanner,
    LLMProvider,
    Transcriber,
    Uploader,
)
from autoshorts.core.exceptions import InvalidStateTransitionError, PathSecurityError
from autoshorts.core.logging_setup import redact, register_secret
from autoshorts.core.models import (
    ClipCandidate,
    EditDecisionList,
    Job,
    JobStatus,
    JumpCut,
    VideoMetadata,
    ZoomPunchIn,
    can_transition,
)
from autoshorts.core.paths import (
    ALLOWED_CLEANUP_ROOTS,
    PROJECT_ROOT,
    assert_safe_to_delete,
    atomic_write,
    escape_ffmpeg_filter_path,
    get_output_dir,
    get_work_dir,
)
from autoshorts.core.state_manager import StateManager, get_current_pt_date, get_pt_timezone


# -----------------------------------------------------------------------------
# 1. Config Loading & Zero Hardcoded Models
# -----------------------------------------------------------------------------

def test_config_loading_with_model_null():
    """Verify config.yaml loads correctly with model: null everywhere."""
    settings = load_config()
    assert isinstance(settings, Settings)

    # Rule: model must be None (zero hardcoded models)
    assert settings.llm.model is None, "LLM model must be null by default"
    assert settings.edit_planner.model is None, "Edit planner model must be null by default"

    # Verify key default constraints
    assert settings.scheduler.daily_upload_cap == 50
    assert settings.scheduler.max_per_rolling_hour == 7
    assert settings.scheduler.min_gap_minutes == 5
    assert settings.scheduler.max_gap_minutes == 25
    assert settings.quota.upload_bucket_daily == 100
    assert settings.quota.general_bucket_daily == 10000
    assert settings.quota.reserve_retries == 50
    assert settings.job_defaults.clips_per_video == 1
    assert settings.media.whisper_model == "base.en"


# -----------------------------------------------------------------------------
# 2. State Manager: Idempotency & Migrations
# -----------------------------------------------------------------------------

def test_state_manager_idempotency_and_persistence(tmp_path: Path):
    """Verify StateManager initializes cleanly, persists data, and handles re-initialization."""
    db_path = tmp_path / "test_state.db"

    # First initialization
    sm1 = StateManager(db_path)
    job = Job(
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        custom_instructions="Focus on hook",
    )
    sm1.save_job(job)

    # Save a stage
    stage_id = sm1.record_stage_start(job.id, "METADATA_FETCH")
    sm1.record_stage_complete(stage_id, "COMPLETED", {"title": "Test Title"})

    # Second initialization on same DB file (must be idempotent)
    sm2 = StateManager(db_path)
    loaded_job = sm2.get_job(job.id)
    assert loaded_job is not None
    assert loaded_job.id == job.id
    assert loaded_job.source_url == job.source_url
    assert loaded_job.status == JobStatus.QUEUED

    stages = sm2.get_job_stages(job.id)
    assert len(stages) == 1
    assert stages[0]["stage_name"] == "METADATA_FETCH"
    assert stages[0]["artifacts"]["title"] == "Test Title"


# -----------------------------------------------------------------------------
# 3. Dual-Bucket Quota Tracking & Pacific Time Reset Date
# -----------------------------------------------------------------------------

def test_state_manager_quota_tracking_and_pt_date(tmp_path: Path):
    """Verify dual-bucket quota tracking keyed by Pacific Time date."""
    db_path = tmp_path / "quota_test.db"
    sm = StateManager(db_path)

    today_pt = get_current_pt_date()
    assert len(today_pt) == 10  # YYYY-MM-DD
    assert today_pt.count("-") == 2

    # Initial spend should be 0
    assert sm.get_quota_spend("upload") == 0
    assert sm.get_quota_spend("general") == 0

    # Record spend in upload bucket (videos.insert costs 1 unit)
    spend_upload = sm.record_quota_spend("upload", 1)
    assert spend_upload == 1
    assert sm.get_quota_spend("upload") == 1

    # Record another spend in upload bucket
    spend_upload = sm.record_quota_spend("upload", 1)
    assert spend_upload == 2
    assert sm.get_quota_spend("upload") == 2

    # Record spend in general bucket (videos.list verification costs 1 unit per call)
    spend_general = sm.record_quota_spend("general", 3)
    assert spend_general == 3
    assert sm.get_quota_spend("general") == 3

    # Ensure buckets remain isolated
    assert sm.get_quota_spend("upload") == 2

    # Test timezone helper
    tz = get_pt_timezone()
    assert tz is not None


def test_scheduler_queue_pacing_methods(tmp_path: Path):
    """Verify scheduler queue persistence and counting for rate limits."""
    db_path = tmp_path / "scheduler_test.db"
    sm = StateManager(db_path)

    job_id = "job_test_123"
    job = Job(id=job_id, source_url="https://youtube.com/watch?v=123")
    sm.save_job(job)

    sched_time = datetime.now(timezone.utc)
    sm.enqueue_for_upload(
        job_id=job_id,
        video_path="output/test.mp4",
        title="Test Short",
        description="Test Desc",
        scheduled_time=sched_time,
        slot_jitter_minutes=12,
    )

    pending = sm.get_pending_scheduled_slots()
    assert len(pending) == 1
    assert pending[0]["job_id"] == job_id
    assert pending[0]["status"] == "SCHEDULED"

    # Mark completed
    sm.update_scheduled_slot_status(job_id, "COMPLETED", youtube_video_id="yt_vid_abc", quota_spent=1)
    assert len(sm.get_pending_scheduled_slots()) == 0

    # Count rolling hour and count for PT date
    count_hour = sm.count_uploads_since(datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert count_hour == 1

    count_day = sm.count_uploads_for_pt_date()
    assert count_day == 1


# -----------------------------------------------------------------------------
# 4. Secret Redaction
# -----------------------------------------------------------------------------

def test_secret_redaction():
    """Verify that API keys, bearer tokens, and secrets are sanitized."""
    # Test registered secret
    register_secret("my_super_secret_token_12345")
    assert redact("my_super_secret_token_12345") == "***REDACTED***"
    assert redact("Authorization: my_super_secret_token_12345 in request") == (
        "Authorization: ***REDACTED*** in request"
    )

    # Test Bearer header regex
    bearer_sample = "Bearer ya29.a0AfH6SMD_random_access_token_value"
    assert "ya29" not in redact(bearer_sample)
    assert "***REDACTED***" in redact(bearer_sample)

    # Test Google AI API key pattern
    gemini_key = "AIzaSyD-sample-google-gemini-api-key-123456"
    redacted_key = redact(gemini_key)
    assert "AIzaSy" not in redacted_key
    assert "***REDACTED***" in redacted_key

    # Test OpenRouter API key pattern
    openrouter_key = "sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    redacted_or = redact(openrouter_key)
    assert "sk-or-v1" not in redacted_or
    assert "***REDACTED***" in redacted_or

    # Test dictionary recursion
    sensitive_dict = {
        "api_key": "some_api_key_value",
        "nested": {"token": "secret_nested_token"},
        "public": "safe_information",
    }
    redacted_dict = redact(sensitive_dict)
    assert redacted_dict["api_key"] == "***REDACTED***"
    assert redacted_dict["nested"]["token"] == "***REDACTED***"
    assert redacted_dict["public"] == "safe_information"


# -----------------------------------------------------------------------------
# 5. Atomic Write Helper
# -----------------------------------------------------------------------------

def test_atomic_write(tmp_path: Path):
    """Verify atomic writing creates files cleanly and supports replacement."""
    target_file = tmp_path / "subfolder" / "atomic.txt"

    # Text write
    written_path = atomic_write(target_file, "Hello World\nLine 2")
    assert written_path.is_file()
    assert written_path.read_text(encoding="utf-8") == "Hello World\nLine 2"

    # Atomic replace
    atomic_write(target_file, "Overwritten Content")
    assert target_file.read_text(encoding="utf-8") == "Overwritten Content"

    # Binary write
    bin_file = tmp_path / "binary.bin"
    atomic_write(bin_file, b"\x00\x01\x02\x03", mode="wb")
    assert bin_file.read_bytes() == b"\x00\x01\x02\x03"


# -----------------------------------------------------------------------------
# 6. Path Safety Guards & FFmpeg Escaping
# -----------------------------------------------------------------------------

def test_path_guards():
    """Verify assert_safe_to_delete prevents accidental deletion of forbidden roots."""
    work_file = get_work_dir() / "job123" / "temp.mp4"
    output_file = get_output_dir() / "short.mp4"

    # Allowed paths
    assert_safe_to_delete(work_file)
    assert_safe_to_delete(output_file)

    # Forbidden paths
    with pytest.raises(PathSecurityError):
        assert_safe_to_delete(PROJECT_ROOT / "assets" / "fonts" / "test.ttf")

    with pytest.raises(PathSecurityError):
        assert_safe_to_delete(PROJECT_ROOT / "src" / "autoshorts" / "core" / "config.py")

    with pytest.raises(PathSecurityError):
        assert_safe_to_delete(PROJECT_ROOT / "config" / "config.yaml")

    with pytest.raises(PathSecurityError):
        assert_safe_to_delete(PROJECT_ROOT / "data" / "state.db")


def test_ffmpeg_filter_path_escaping():
    """Verify FFmpeg filter path escaping on Windows converts backslashes and escapes colons."""
    escaped = escape_ffmpeg_filter_path(r"C:\work\job123\subs.ass")
    # Must use forward slashes and escape colon
    assert r"C\:/work/job123/subs.ass" in escaped
    assert escaped.startswith("'") and escaped.endswith("'")
    assert "\\" not in escaped.replace(r"\:", "")


# -----------------------------------------------------------------------------
# 7. Domain Models & State Transitions
# -----------------------------------------------------------------------------

def test_job_state_machine():
    """Verify Job state transitions and validation."""
    job = Job(source_url="https://youtube.com/watch?v=xyz")
    assert job.status == JobStatus.QUEUED

    # Valid transitions
    job.transition_to(JobStatus.METADATA_FETCHED)
    assert job.status == JobStatus.METADATA_FETCHED

    job.transition_to(JobStatus.TRANSCRIBED)
    assert job.status == JobStatus.TRANSCRIBED

    # Invalid jump
    with pytest.raises(InvalidStateTransitionError):
        job.transition_to(JobStatus.COMPLETED)


def test_edl_model():
    """Verify EditDecisionList validation."""
    edl = EditDecisionList(
        hook_in_sec=1.5,
        hook_out_sec=42.0,
        jump_cuts=[JumpCut(cut_start=10.0, cut_end=12.0, reason="Silence")],
        zoom_punch_ins=[ZoomPunchIn(timestamp=5.0, scale=1.15, duration=0.8, emphasis_word="AMAZING")],
        recommended_title="Amazing Fact #Shorts",
        recommended_hook="Did you know this?",
    )
    assert edl.hook_in_sec == 1.5
    assert len(edl.jump_cuts) == 1
    assert edl.zoom_punch_ins[0].scale == 1.15


# -----------------------------------------------------------------------------
# 8. Protocol Runtime Checks
# -----------------------------------------------------------------------------

def test_protocols_runtime_checkable():
    """Verify that Protocols are @runtime_checkable."""
    class DummyLLM:
        name = "dummy"
        supports_logprobs = True
        def list_models(self): return ["m1"]
        def complete(self, prompt, **kwargs): return "done"
        def score_options(self, prompt, options, **kwargs): return {"A": 1.0}

    dummy = DummyLLM()
    assert isinstance(dummy, LLMProvider)
