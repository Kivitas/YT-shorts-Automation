"""Foundation tests covering configuration, state manager, redaction, atomic writes, and contracts."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoshorts.core.config import (
    AudioMasteringConfig,
    Settings,
    VisualBrandingConfig,
    load_config,
)
from autoshorts.core.contracts import (
    LLMProvider,
)
from autoshorts.core.exceptions import InvalidStateTransitionError, PathSecurityError
from autoshorts.core.logging_setup import redact, register_secret
from autoshorts.core.models import (
    ChannelProfile,
    EditDecisionList,
    Job,
    JobStatus,
    JumpCut,
    ZoomPunchIn,
)
from autoshorts.core.paths import (
    PROJECT_ROOT,
    assert_safe_to_delete,
    atomic_write,
    escape_ffmpeg_filter_path,
    get_output_dir,
    get_work_dir,
)
from autoshorts.core.settings_manager import (
    get_channel_profiles,
    get_studio_profiles,
    save_channel_profile,
    save_studio_profile,
)
from autoshorts.core.state_manager import StateManager, get_current_pt_date, get_pt_timezone

# -----------------------------------------------------------------------------
# 1. Config Loading & Zero Hardcoded Models
# -----------------------------------------------------------------------------


def test_config_loading_with_model_null():
    """Verify config.yaml loads correctly with model: null everywhere."""
    settings = load_config()
    assert isinstance(settings, Settings)

    # Rule: model must be configured or null default
    assert settings.llm.model in (None, "gemini-3.5-flash-lite"), "LLM model must be null or default gemini-3.5-flash-lite"
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

    sched_time = datetime.now(UTC)
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
    sm.update_scheduled_slot_status(
        job_id, "COMPLETED", youtube_video_id="yt_vid_abc", quota_spent=1
    )
    assert len(sm.get_pending_scheduled_slots()) == 0

    # Count rolling hour and count for PT date
    count_hour = sm.count_uploads_since(datetime(2020, 1, 1, tzinfo=UTC))
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
        zoom_punch_ins=[
            ZoomPunchIn(timestamp=5.0, scale=1.15, duration=0.8, emphasis_word="AMAZING")
        ],
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

        def list_models(self):
            return ["m1"]

        def complete(self, prompt, **kwargs):
            return "done"

        def score_options(self, prompt, options, **kwargs):
            return {"A": 1.0}

    dummy = DummyLLM()
    assert isinstance(dummy, LLMProvider)


# -----------------------------------------------------------------------------
# 9. Startup Stranded Job Reconciliation
# -----------------------------------------------------------------------------


def test_reconcile_stranded_jobs(tmp_path: Path):
    """Verify reconcile_stranded_jobs transitions transient jobs to FAILED and leaves finished jobs untouched."""
    db_path = tmp_path / "stranded_test.db"
    sm = StateManager(db_path)

    # 1. Create transient jobs
    transient_statuses = [
        JobStatus.DOWNLOADING_SECTION,
        JobStatus.COMPOSITING,
        JobStatus.UPLOADING,
        JobStatus.VERIFYING,
    ]
    for i, st in enumerate(transient_statuses):
        job = Job(id=f"job_transient_{i}", source_url=f"https://youtu.be/{i}")
        job.status = st
        sm.save_job(job)

    # Add custom string transient jobs directly into DB
    conn = sm._get_connection()
    now_str = datetime.now(UTC).isoformat()
    conn.execute(
        """
        INSERT INTO jobs (id, source_url, status, payload_json, created_at, updated_at)
        VALUES ('job_custom_str_1', 'https://youtu.be/custom1', 'FETCHING_METADATA', '{"id":"job_custom_str_1","source_url":"https://youtu.be/custom1","status":"FETCHING_METADATA"}', ?, ?),
               ('job_custom_str_2', 'https://youtu.be/custom2', 'PLANNING_EDITS', '{"id":"job_custom_str_2","source_url":"https://youtu.be/custom2","status":"PLANNING_EDITS"}', ?, ?);
        """,
        (now_str, now_str, now_str, now_str),
    )
    conn.close()

    # 2. Create non-transient jobs (should NOT be reconciled)
    queued_job = Job(id="job_queued", source_url="https://youtu.be/queued", status=JobStatus.QUEUED)
    completed_job = Job(
        id="job_completed", source_url="https://youtu.be/comp", status=JobStatus.COMPLETED
    )
    cleaned_job = Job(
        id="job_cleaned", source_url="https://youtu.be/clean", status=JobStatus.CLEANED
    )
    sm.save_job(queued_job)
    sm.save_job(completed_job)
    sm.save_job(cleaned_job)

    # Reconcile stranded jobs
    reconciled_count = sm.reconcile_stranded_jobs()
    assert reconciled_count == 6

    # Verify all transient jobs are now FAILED with expected restart message
    for i in range(len(transient_statuses)):
        j = sm.get_job(f"job_transient_{i}")
        assert j is not None
        assert j.status == JobStatus.FAILED
        assert j.error_message == "Interrupted by application restart; ready for retry"

    custom_1 = sm.get_job("job_custom_str_1")
    assert custom_1 is not None
    assert custom_1.status == JobStatus.FAILED
    assert custom_1.error_message == "Interrupted by application restart; ready for retry"

    # Verify non-transient jobs untouched
    assert sm.get_job("job_queued").status == JobStatus.QUEUED
    assert sm.get_job("job_completed").status == JobStatus.COMPLETED
    assert sm.get_job("job_cleaned").status == JobStatus.CLEANED

    # Subsequent run finds 0 stranded jobs
    assert sm.reconcile_stranded_jobs() == 0


# -----------------------------------------------------------------------------
# 10. VisualBrandingConfig & AudioMasteringConfig Validation
# -----------------------------------------------------------------------------


def test_visual_branding_config_defaults_and_validation():
    """Verify VisualBrandingConfig defaults and Field constraints."""
    vb = VisualBrandingConfig()
    assert vb.layout_mode == "split_50_50"
    assert vb.color_grading == "none"
    assert vb.watermark_text is None
    assert vb.watermark_position == "top_right"
    assert vb.watermark_opacity == 0.65
    assert vb.progress_bar_position == "seam"
    assert vb.progress_bar_color == "#FFE600"
    assert vb.progress_bar_height == 12
    assert vb.hook_opener == "none"
    assert vb.outro_cta_text is None

    # Custom valid values
    custom_vb = VisualBrandingConfig(
        layout_mode="full_blur_bg",
        color_grading="vibrant",
        watermark_text="@AutoShorts",
        watermark_position="bottom_right",
        watermark_opacity=0.8,
        progress_bar_position="bottom",
        progress_bar_color="#00FF66",
        progress_bar_height=8,
        hook_opener="zoom_burst",
        outro_cta_text="Subscribe for more!",
    )
    assert custom_vb.layout_mode == "full_blur_bg"
    assert custom_vb.watermark_text == "@AutoShorts"
    assert custom_vb.progress_bar_height == 8

    # Validation bounds: opacity ge 0.1, le 1.0
    with pytest.raises(ValidationError):
        VisualBrandingConfig(watermark_opacity=0.05)
    with pytest.raises(ValidationError):
        VisualBrandingConfig(watermark_opacity=1.5)

    # Validation bounds: progress_bar_height ge 4, le 24
    with pytest.raises(ValidationError):
        VisualBrandingConfig(progress_bar_height=2)
    with pytest.raises(ValidationError):
        VisualBrandingConfig(progress_bar_height=30)


def test_audio_mastering_config_defaults_and_validation():
    """Verify AudioMasteringConfig defaults and Field constraints."""
    am = AudioMasteringConfig()
    assert am.voice_boost is True
    assert am.target_lufs == -14.0
    assert am.bg_music_volume == 0.20
    assert am.enable_sfx is True

    # Custom valid values
    custom_am = AudioMasteringConfig(
        voice_boost=False,
        target_lufs=-16.0,
        bg_music_volume=0.35,
        enable_sfx=False,
    )
    assert custom_am.voice_boost is False
    assert custom_am.target_lufs == -16.0

    # Validation bounds: target_lufs ge -24.0, le -9.0
    with pytest.raises(ValidationError):
        AudioMasteringConfig(target_lufs=-28.0)
    with pytest.raises(ValidationError):
        AudioMasteringConfig(target_lufs=-5.0)

    # Validation bounds: bg_music_volume ge 0.0, le 0.50
    with pytest.raises(ValidationError):
        AudioMasteringConfig(bg_music_volume=-0.1)
    with pytest.raises(ValidationError):
        AudioMasteringConfig(bg_music_volume=0.75)


# -----------------------------------------------------------------------------
# 11. ChannelProfile Model
# -----------------------------------------------------------------------------


def test_channel_profile_defaults():
    """Verify ChannelProfile defaults and custom overrides."""
    cp = ChannelProfile()
    assert cp.id == "default"
    assert cp.name == "Default Channel"
    assert cp.handle == "@MyChannel"
    assert cp.token_path == "data/token.json"
    assert cp.edge_profile_dir == "data/edge_profile"
    assert cp.default_privacy_status == "public"
    assert cp.default_category_id == "24"
    assert cp.watermark_text is None

    custom = ChannelProfile(
        id="gaming_channel",
        name="Gaming Shorts",
        handle="@GamingShorts",
        token_path="data/tokens/gaming.json",
        edge_profile_dir="data/edge_profiles/gaming",
        default_privacy_status="unlisted",
        default_category_id="20",
        watermark_text="@GamingShorts",
    )
    assert custom.id == "gaming_channel"
    assert custom.handle == "@GamingShorts"
    assert custom.default_category_id == "20"


# -----------------------------------------------------------------------------
# 12. Extended Job Model Defaults & Backward Compatibility
# -----------------------------------------------------------------------------


def test_job_model_extended_defaults():
    """Verify Job model contains all new Track 1 fields with correct defaults."""
    job = Job(source_url="https://youtube.com/watch?v=dQw4w9WgXcQ")
    assert job.layout_mode == "split_50_50"
    assert job.color_grading == "none"
    assert job.watermark_text is None
    assert job.watermark_position == "top_right"
    assert job.progress_bar_position == "seam"
    assert job.text_transform == "uppercase"
    assert job.subtitle_position == "center"
    assert job.hook_banner_style == "neon_box"
    assert job.enable_emojis is True
    assert job.category_id == "24"
    assert job.default_language == "en"
    assert job.altered_content is False
    assert job.custom_tags == []
    assert job.auto_pin_comment is False
    assert job.link_related_video is True
    assert job.publish_at is None
    assert job.channel_profile_id == "default"

    # Backward compatibility with existing fields
    assert job.style_preset == "neon"
    assert job.privacy_status is None


# -----------------------------------------------------------------------------
# 13. Studio Profiles & Channel Profiles Persistence
# -----------------------------------------------------------------------------


def test_studio_profiles_factory_and_persistence(tmp_path: Path):
    """Verify built-in factory studio profiles and custom profile saving."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # 1. Initially returns 5 factory profiles
    profiles = get_studio_profiles(data_dir=data_dir)
    assert len(profiles) >= 5
    for expected_key in (
        "viral_split",
        "podcast_blur",
        "gaming_dopamine",
        "cinematic_full",
        "pip_reaction",
    ):
        assert expected_key in profiles
        assert profiles[expected_key]["is_factory"] is True

    # 2. Save a custom studio profile
    custom_profile = {
        "id": "my_tech_review",
        "name": "Tech Review Custom",
        "layout_mode": "full_blur_bg",
        "style_preset": "cyberpunk",
        "color_grading": "crisp",
        "progress_bar_position": "top",
    }
    saved = save_studio_profile(custom_profile, data_dir=data_dir)
    assert saved["id"] == "my_tech_review"
    assert saved["is_factory"] is False

    # 3. Reload profiles and verify custom profile is present alongside factory profiles
    reloaded = get_studio_profiles(data_dir=data_dir)
    assert "my_tech_review" in reloaded
    assert reloaded["my_tech_review"]["name"] == "Tech Review Custom"
    assert reloaded["my_tech_review"]["is_factory"] is False
    assert "viral_split" in reloaded


def test_channel_profiles_persistence(tmp_path: Path):
    """Verify channel profiles load default and persist updates cleanly."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # 1. Defaults to default profile
    initial = get_channel_profiles(data_dir=data_dir)
    assert len(initial) == 1
    assert initial[0]["id"] == "default"
    assert initial[0]["handle"] == "@MyChannel"

    # 2. Save a new channel profile
    new_profile = {
        "id": "podcast_clips",
        "name": "Podcast Highlights",
        "handle": "@PodcastHighlights",
        "default_category_id": "22",
    }
    save_channel_profile(new_profile, data_dir=data_dir)

    # 3. Retrieve profiles and verify both exist
    profiles = get_channel_profiles(data_dir=data_dir)
    assert len(profiles) == 2
    ids = [p["id"] for p in profiles]
    assert "default" in ids
    assert "podcast_clips" in ids

    # 4. Update existing channel profile
    updated_profile = {
        "id": "podcast_clips",
        "name": "Podcast Highlights Official",
        "handle": "@PodcastHighlightsHQ",
        "default_category_id": "27",
    }
    save_channel_profile(updated_profile, data_dir=data_dir)

    # 5. Check updated in place
    profiles_after = get_channel_profiles(data_dir=data_dir)
    assert len(profiles_after) == 2
    match = next(p for p in profiles_after if p["id"] == "podcast_clips")
    assert match["name"] == "Podcast Highlights Official"
    assert match["handle"] == "@PodcastHighlightsHQ"
    assert match["default_category_id"] == "27"
