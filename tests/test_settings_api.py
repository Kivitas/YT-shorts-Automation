"""Unit and integration tests for Settings Manager, YouTube Data API v3 setup, and Dashboard APIs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autoshorts.core.config import load_config
from autoshorts.core.models import (
    ClipCandidate,
    ClipSelection,
    Job,
    JobStatus,
    VideoMetadata,
)
from autoshorts.core.settings_manager import (
    apply_settings_update,
    get_public_settings_payload,
    get_youtube_oauth_status,
    mask_secret,
    read_env_file,
    save_youtube_client_secret,
    update_env_file,
)
from autoshorts.core.state_manager import StateManager
from autoshorts.dashboard.app import create_app
from autoshorts.quota.quota_tracker import QuotaTracker

# =============================================================================
# Unit Tests for Settings Manager Helpers
# =============================================================================


def test_mask_secret():
    """Verify secret masking behavior."""
    assert mask_secret(None) == ""
    assert mask_secret("") == ""
    assert mask_secret("   ") == ""
    assert mask_secret("abcd") == "••••••••abcd"
    assert mask_secret("AIzaSy1234567890abcdef") == "••••••••cdef"
    assert mask_secret("sk-or-v1-9988776655") == "••••••••6655"


def test_read_and_update_env_file(tmp_path: Path, monkeypatch):
    """Verify reading and updating .env file atomically without corrupting secrets."""
    env_file = tmp_path / ".env"
    env_file.write_text('EXISTING_VAR="hello"\nGEMINI_API_KEY="old_key"\n', encoding="utf-8")

    # Read existing
    data = read_env_file(env_file)
    assert data["EXISTING_VAR"] == "hello"
    assert data["GEMINI_API_KEY"] == "old_key"

    # Update with new secret
    update_env_file(
        env_file,
        {
            "GEMINI_API_KEY": "AIzaSyNewKey1234",
            "OPENROUTER_API_KEY": "sk-or-v1-newsecret",
            "LOCAL_GGUF_PATH": "data/models/test.gguf",
        },
    )

    data_updated = read_env_file(env_file)
    assert data_updated["EXISTING_VAR"] == "hello"
    assert data_updated["GEMINI_API_KEY"] == "AIzaSyNewKey1234"
    assert data_updated["OPENROUTER_API_KEY"] == "sk-or-v1-newsecret"
    assert data_updated["LOCAL_GGUF_PATH"] == "data/models/test.gguf"

    # Verify masked bullet strings are ignored and do NOT overwrite real secrets
    update_env_file(env_file, {"GEMINI_API_KEY": "••••••••1234"})
    data_after_mask = read_env_file(env_file)
    assert data_after_mask["GEMINI_API_KEY"] == "AIzaSyNewKey1234"


def test_save_youtube_client_secret_valid(tmp_path: Path):
    """Verify validation and saving of valid client secret JSON."""
    dest_path = tmp_path / "client_secret.json"
    valid_payload = {
        "installed": {
            "client_id": "123456789.apps.googleusercontent.com",
            "project_id": "my-autoshorts-project",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": "GOCSPX-supersecretkey123",
        }
    }

    meta = save_youtube_client_secret(json.dumps(valid_payload), dest_path=dest_path)
    assert dest_path.is_file()
    assert meta["client_id_masked"] == "••••••••.com"
    assert meta["project_id"] == "my-autoshorts-project"

    # Verify saved content matches
    saved = json.loads(dest_path.read_text(encoding="utf-8"))
    assert saved["installed"]["client_id"] == "123456789.apps.googleusercontent.com"


def test_save_youtube_client_secret_invalid(tmp_path: Path):
    """Verify rejection of invalid JSON and missing keys."""
    dest_path = tmp_path / "client_secret.json"

    # Invalid JSON string
    with pytest.raises(ValueError, match="Invalid JSON"):
        save_youtube_client_secret("not-json", dest_path=dest_path)

    # Missing installed/web root key
    with pytest.raises(ValueError, match="must contain an 'installed' or 'web'"):
        save_youtube_client_secret(json.dumps({"other": {}}), dest_path=dest_path)

    # Missing client_secret
    with pytest.raises(ValueError, match="missing required 'client_id' or 'client_secret'"):
        save_youtube_client_secret(
            json.dumps({"installed": {"client_id": "123"}}), dest_path=dest_path
        )


def test_get_youtube_oauth_status(tmp_path: Path, monkeypatch):
    """Verify OAuth status reporting based on presence of client_secret and token files."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr("autoshorts.core.settings_manager.get_data_dir", lambda: data_dir)

    status_initial = get_youtube_oauth_status()
    assert status_initial["client_secret_configured"] is False
    assert status_initial["token_configured"] is False

    # Create valid client secret
    cs_file = data_dir / "client_secret.json"
    cs_file.write_text(
        json.dumps({"installed": {"client_id": "id123", "client_secret": "sec456"}}),
        encoding="utf-8",
    )

    status_with_secret = get_youtube_oauth_status()
    assert status_with_secret["client_secret_configured"] is True
    assert status_with_secret["token_configured"] is False

    # Create valid token
    tok_file = data_dir / "token.json"
    tok_file.write_text(
        json.dumps({"token": "ya29.test", "refresh_token": "ref123"}), encoding="utf-8"
    )

    status_with_token = get_youtube_oauth_status()
    assert status_with_token["client_secret_configured"] is True
    assert status_with_token["token_configured"] is True


def test_apply_settings_update_config_and_env(tmp_path: Path, monkeypatch):
    """Verify apply_settings_update updates config.yaml and .env and reloads Settings."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        """
upload:
  mode: "api"
  privacy_status: "unlisted"
scheduler:
  daily_upload_cap: 50
  max_per_rolling_hour: 7
subtitles:
  font_name: "Montserrat ExtraBold"
""",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"

    payload = {
        "upload": {"mode": "studio", "privacy_status": "public"},
        "scheduler": {"daily_upload_cap": 25, "active_hours_start": "09:00"},
        "subtitles": {"style_preset": "neon"},
        "secrets": {"gemini_api_key": "AIzaSyUpdatedKey9876"},
    }

    new_settings = apply_settings_update(payload, config_path=cfg_file, env_path=env_file)
    assert new_settings.upload.mode == "studio"
    assert new_settings.upload.privacy_status == "public"
    assert new_settings.scheduler.daily_upload_cap == 25
    assert new_settings.scheduler.active_window.start == "09:00"

    # Check .env was written and masked payload works
    env_data = read_env_file(env_file)
    assert env_data["GEMINI_API_KEY"] == "AIzaSyUpdatedKey9876"

    public = get_public_settings_payload(new_settings, env_path=env_file)
    assert public["version"] == "1.0.0"
    assert public["upload"]["mode"] == "studio"
    assert public["upload"]["privacy_status"] == "public"
    assert public["scheduler"]["daily_upload_cap"] == 25
    assert public["secrets"]["gemini_api_key_set"] is True
    assert public["secrets"]["gemini_api_key_masked"] == "••••••••9876"


# =============================================================================
# Dashboard Client Fixture for API Endpoint Tests
# =============================================================================


@pytest.fixture
def settings_client(tmp_path: Path, monkeypatch):
    """Create test environment and client for settings and new API endpoints."""
    db_path = tmp_path / "test_settings.db"
    state_mgr = StateManager(db_path=db_path)

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text(
        """
app:
  name: "AutoShorts-OnDevice"
  version: "1.0.0"
llm:
  provider: "local"
  model: null
job_defaults:
  clips_per_video: 1
  min_duration_seconds: 30
  max_duration_seconds: 50
  background_category: "minecraft"
scheduler:
  enabled: true
  daily_upload_cap: 50
  max_per_rolling_hour: 7
upload:
  mode: "api"
  privacy_status: "unlisted"
subtitles:
  font_name: "Montserrat ExtraBold"
  font_size: 76
cleanup:
  enabled: true
  use_recycle_bin: true
""",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    monkeypatch.setattr("autoshorts.core.paths.get_data_dir", lambda: data_dir)
    monkeypatch.setattr("autoshorts.core.settings_manager.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("autoshorts.core.settings_manager.get_config_dir", lambda: cfg_dir)
    monkeypatch.setattr("autoshorts.core.settings_manager.get_data_dir", lambda: data_dir)
    monkeypatch.setattr("autoshorts.core.config.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("autoshorts.core.config.get_config_dir", lambda: cfg_dir)

    cfg = load_config(cfg_file)
    quota_tracker = QuotaTracker(state_manager=state_mgr)

    mock_pipeline = MagicMock()
    mock_pipeline.config = cfg
    mock_pipeline.state_manager = state_mgr
    mock_pipeline.quota_tracker = quota_tracker

    mock_scheduler = MagicMock()
    mock_scheduler.is_paused.return_value = False
    mock_pipeline.scheduler = mock_scheduler

    mock_cleaner = MagicMock()
    mock_cleaner.cleanup_job.return_value = [tmp_path / "work" / "test.mp4"]
    mock_pipeline.cleaner = mock_cleaner

    # Mock downloader
    mock_downloader = MagicMock()
    mock_metadata = VideoMetadata(
        title="Sample YouTube Podcast",
        channel="Tech Insider",
        channel_url="https://youtube.com/@techinsider",
        video_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
        duration=180.0,
        video_id="dQw4w9WgXcQ",
    )
    mock_downloader.fetch_metadata.return_value = mock_metadata
    mock_pipeline.downloader = mock_downloader

    app = create_app(
        pipeline=mock_pipeline,
        state_manager=state_mgr,
        config=cfg,
        run_scheduler_worker=False,
    )
    client = TestClient(app, base_url="http://localhost:8765")

    return {
        "client": client,
        "app": app,
        "pipeline": mock_pipeline,
        "state_mgr": state_mgr,
        "cfg_file": cfg_file,
        "env_file": env_file,
        "data_dir": data_dir,
    }


# =============================================================================
# Dashboard API Endpoints Tests
# =============================================================================


def test_api_get_settings(settings_client: dict[str, Any]):
    """Verify GET /api/settings returns complete public payload with masked secrets."""
    client: TestClient = settings_client["client"]
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "1.0.0"
    assert "llm" in data
    assert "upload" in data
    assert "scheduler" in data
    assert "secrets" in data
    assert "youtube_oauth" in data
    assert data["upload"]["mode"] == "api"
    assert data["upload"]["privacy_status"] == "unlisted"


def test_api_post_settings_update(settings_client: dict[str, Any]):
    """Verify POST /api/settings updates config, masks secrets, and re-initializes subsystems."""
    client: TestClient = settings_client["client"]
    settings_client["pipeline"]

    update_payload = {
        "upload": {"mode": "local", "privacy_status": "public"},
        "scheduler": {"daily_upload_cap": 35, "max_per_rolling_hour": 10},
        "subtitles": {"style_preset": "cyberpunk"},
        "secrets": {"gemini_api_key": "AIzaSySecretFromApi1234"},
    }

    resp = client.post("/api/settings", json=update_payload)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["success"] is True

    settings = res_data["settings"]
    assert settings["upload"]["mode"] == "local"
    assert settings["upload"]["privacy_status"] == "public"
    assert settings["scheduler"]["daily_upload_cap"] == 35
    assert settings["scheduler"]["max_per_rolling_hour"] == 10
    assert settings["subtitles"]["style_preset"] == "cyberpunk"
    assert settings["secrets"]["gemini_api_key_set"] is True
    assert settings["secrets"]["gemini_api_key_masked"] == "••••••••1234"


def test_api_post_settings_complete_modal_payload(settings_client: dict[str, Any]):
    """Verify POST /api/settings processes the full Settings Modal payload with all sub-configs."""
    client: TestClient = settings_client["client"]

    complete_modal_payload = {
        "cleanup": {
            "enabled": True,
            "keep_final_days": 14,
            "keep_master_mp4": True,
            "purge_intermediate_work": True,
            "use_recycle_bin": True,
        },
        "branding": {
            "layout_mode": "full_blur_bg",
            "color_grading": "cinematic",
            "watermark_text": "@AutoShortsHub",
            "watermark_position": "top_right",
            "watermark_opacity": 0.8,
            "progress_bar_position": "bottom",
            "progress_bar_color": "#00FFCC",
            "progress_bar_height": 14,
            "hook_opener": "flash",
        },
        "audio_mastering": {
            "voice_boost": True,
            "target_lufs": -16.0,
            "bg_music_volume": 0.15,
            "enable_sfx": True,
        },
        "subtitles": {
            "font_name": "Montserrat ExtraBold",
            "font_size": 80,
            "words_per_burst": 2,
            "style_preset": "hormozi",
            "text_transform": "uppercase",
            "position": "center",
            "hook_banner_style": "yellow_highlight",
            "enable_emojis": True,
        },
        "upload": {
            "mode": "local",
            "privacy_status": "unlisted",
            "category_id": "27",
            "default_language": "en",
            "altered_content": True,
            "custom_tags": ["Shorts", "Viral", "Education"],
            "auto_pin_comment": True,
            "link_related_video": True,
        },
        "scheduler": {
            "enabled": True,
            "daily_upload_cap": 20,
            "max_per_rolling_hour": 3,
            "min_gap_minutes": 90,
            "max_gap_minutes": 180,
            "active_hours_start": "09:00",
            "active_hours_end": "22:00",
            "prevent_system_sleep": True,
        },
        "secrets": {
            "gemini_api_key": "AIzaSyCompleteModalKey9999",
        },
    }

    resp = client.post("/api/settings", json=complete_modal_payload)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["success"] is True

    settings = res_data["settings"]
    assert settings["cleanup"]["keep_final_days"] == 14
    assert settings["branding"]["layout_mode"] == "full_blur_bg"
    assert settings["branding"]["color_grading"] == "cinematic"
    assert settings["branding"]["watermark_text"] == "@AutoShortsHub"
    assert settings["audio_mastering"]["voice_boost"] is True
    assert settings["audio_mastering"]["target_lufs"] == -16.0
    assert settings["subtitles"]["style_preset"] == "hormozi"
    assert settings["upload"]["mode"] == "local"
    assert settings["upload"]["category_id"] == "27"
    assert settings["scheduler"]["daily_upload_cap"] == 20
    assert settings["secrets"]["gemini_api_key_set"] is True


def test_api_youtube_client_secret_json_and_multipart(settings_client: dict[str, Any]):
    """Verify POST /api/settings/youtube-client-secret handles JSON and file upload."""
    client: TestClient = settings_client["client"]
    data_dir: Path = settings_client["data_dir"]

    # 1. Valid JSON payload
    secret_payload = {
        "installed": {
            "client_id": "test_client_id_123.apps.googleusercontent.com",
            "project_id": "test_project",
            "client_secret": "GOCSPX-secretkey999",
        }
    }
    resp1 = client.post(
        "/api/settings/youtube-client-secret",
        json={"client_secret_json": secret_payload},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["success"] is True
    assert data1["project_id"] == "test_project"
    assert data1["client_id_masked"] == "••••••••.com"
    assert data1["youtube_oauth"]["client_secret_configured"] is True
    assert (data_dir / "client_secret.json").is_file()

    # 2. Invalid JSON payload rejection
    resp_invalid = client.post(
        "/api/settings/youtube-client-secret",
        json={"client_secret_json": "invalid_not_json"},
    )
    assert resp_invalid.status_code == 400

    # 3. Multipart form file upload
    file_bytes = json.dumps(secret_payload).encode("utf-8")
    resp_file = client.post(
        "/api/settings/youtube-client-secret",
        files={"file": ("client_secret.json", file_bytes, "application/json")},
    )
    assert resp_file.status_code == 200
    assert resp_file.json()["success"] is True


def test_api_youtube_oauth_trigger_and_status(settings_client: dict[str, Any]):
    """Verify POST /api/settings/youtube-oauth and GET /api/settings/youtube-oauth/status."""
    client: TestClient = settings_client["client"]
    data_dir: Path = settings_client["data_dir"]

    # When client_secret.json is missing, should reject with 400
    if (data_dir / "client_secret.json").is_file():
        (data_dir / "client_secret.json").unlink()

    resp_missing = client.post("/api/settings/youtube-oauth")
    assert resp_missing.status_code == 400
    assert "Client secret file not found" in resp_missing.json()["detail"]

    # Create dummy client secret
    cs_file = data_dir / "client_secret.json"
    cs_file.write_text(
        json.dumps({"installed": {"client_id": "id", "client_secret": "sec"}}),
        encoding="utf-8",
    )

    with patch("autoshorts.dashboard.app.run_youtube_oauth_flow"):
        resp_oauth = client.post("/api/settings/youtube-oauth")
        assert resp_oauth.status_code == 200
        data = resp_oauth.json()
        assert data["success"] is True
        assert data["status"] == "running"

    # Status check endpoint
    resp_status = client.get("/api/settings/youtube-oauth/status")
    assert resp_status.status_code == 200
    assert resp_status.json()["client_secret_configured"] is True


def test_api_test_provider(settings_client: dict[str, Any]):
    """Verify POST /api/settings/test-provider lists models and returns privacy badge."""
    client: TestClient = settings_client["client"]

    # Mock provider list_models
    with patch("autoshorts.core.providers.gemini_provider.GeminiProvider.list_models") as mock_list:
        mock_list.return_value = ["gemini-2.0-flash", "gemini-1.5-pro"]
        resp = client.post(
            "/api/settings/test-provider",
            json={"provider": "gemini", "api_key": "AIzaSyTest12345678"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["provider"] == "gemini"
        assert "gemini-2.0-flash" in data["models"]
        assert "Cloud API Active" in data["privacy_badge"]

    # Test error handling when provider throws
    with patch(
        "autoshorts.core.providers.gemini_provider.GeminiProvider.list_models",
        side_effect=RuntimeError("Invalid API Key"),
    ):
        resp_err = client.post(
            "/api/settings/test-provider",
            json={"provider": "gemini", "api_key": "invalid_key"},
        )
        assert resp_err.status_code == 400
        assert "Invalid API Key" in resp_err.json()["detail"]


def test_api_links_preview(settings_client: dict[str, Any]):
    """Verify POST /api/links/preview parses links and returns metadata previews."""
    client: TestClient = settings_client["client"]

    payload = {
        "links_text": "https://www.youtube.com/watch?v=dQw4w9WgXcQ | focus on retention | bg subway"
    }
    resp = client.post("/api/links/preview", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "previews" in data
    assert len(data["previews"]) == 1

    preview = data["previews"][0]
    assert preview["video_id"] == "dQw4w9WgXcQ"
    assert preview["title"] == "Sample YouTube Podcast"
    assert preview["channel"] == "Tech Insider"
    assert preview["thumbnail_url"] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    assert "on retention" in preview["custom_instructions"]


def test_api_patch_job(settings_client: dict[str, Any]):
    """Verify PATCH /api/jobs/{job_id} updates metadata on an existing job."""
    client: TestClient = settings_client["client"]
    state_mgr: StateManager = settings_client["state_mgr"]

    # Create and save a dummy job
    job = Job(
        source_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
        custom_instructions="Initial instructions",
        status=JobStatus.READY_FOR_REVIEW,
        selected_clips=[
            ClipSelection(
                candidate=ClipCandidate(start=10.0, end=45.0, text="Sample text", score=0.9),
                title="Original Title",
                hook_text="Original Hook",
                background_style="minecraft",
            )
        ],
    )
    state_mgr.save_job(job)

    # Patch job
    patch_payload = {
        "clip_title": "Patched Viral Clip",
        "hook_text": "Mind-blowing revelation!",
        "background_category": "subway",
        "custom_instructions": "Focus on high retention",
    }
    resp = client.patch(f"/api/jobs/{job.id}", json=patch_payload)
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["success"] is True

    # Check updated job in database
    updated = state_mgr.get_job(job.id)
    assert updated is not None
    assert updated.custom_instructions == "Focus on high retention"
    assert updated.selected_clips[0].title == "Patched Viral Clip"
    assert updated.selected_clips[0].hook_text == "Mind-blowing revelation!"
    assert updated.selected_clips[0].background_style == "subway"

    # Test 404 for unknown job
    resp_404 = client.patch("/api/jobs/nonexistent_job", json=patch_payload)
    assert resp_404.status_code == 404


def test_api_scheduler_slots(settings_client: dict[str, Any]):
    """Verify GET /api/scheduler/slots returns queued slots enriched with metadata."""
    client: TestClient = settings_client["client"]
    state_mgr: StateManager = settings_client["state_mgr"]

    # Create a job and enqueue for upload
    job = Job(
        source_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
        status=JobStatus.SCHEDULED,
        video_metadata=VideoMetadata(
            title="Scheduled Masterpiece",
            channel="Tech Insider",
            channel_url="https://youtube.com/@techinsider",
            video_url="https://youtube.com/watch?v=dQw4w9WgXcQ",
            duration=120.0,
            video_id="dQw4w9WgXcQ",
        ),
    )
    state_mgr.save_job(job)

    from datetime import UTC, datetime, timedelta

    slot_time = datetime.now(UTC) + timedelta(hours=2)
    state_mgr.enqueue_for_upload(
        job_id=job.id,
        video_path="output/test.mp4",
        title="Scheduled Short",
        description="Sample Description",
        scheduled_time=slot_time,
    )

    resp = client.get("/api/scheduler/slots")
    assert resp.status_code == 200
    data = resp.json()
    assert "slots" in data
    assert data["count"] >= 1

    slot = next(s for s in data["slots"] if s["job_id"] == job.id)
    assert slot["title"] is not None
    assert slot["video_id"] == "dQw4w9WgXcQ"
    assert slot["source_url"] == job.source_url


def test_api_batch_retry_and_batch_clean(settings_client: dict[str, Any]):
    """Verify POST /api/jobs/batch-retry and POST /api/jobs/batch-clean."""
    client: TestClient = settings_client["client"]
    state_mgr: StateManager = settings_client["state_mgr"]
    settings_client["pipeline"]

    # Create failed job
    failed_job = Job(
        source_url="https://youtube.com/watch?v=failed123",
        status=JobStatus.FAILED,
        error_message="Simulated error",
    )
    state_mgr.save_job(failed_job)

    # Batch retry
    resp_retry = client.post("/api/jobs/batch-retry")
    assert resp_retry.status_code == 200
    retry_data = resp_retry.json()
    assert retry_data["success"] is True
    assert retry_data["count"] >= 1

    # Create completed job
    completed_job = Job(
        source_url="https://youtube.com/watch?v=completed123",
        status=JobStatus.COMPLETED,
    )
    state_mgr.save_job(completed_job)

    # Batch clean
    resp_clean = client.post("/api/jobs/batch-clean")
    assert resp_clean.status_code == 200
    clean_data = resp_clean.json()
    assert clean_data["success"] is True
    assert clean_data["cleaned_jobs_count"] >= 1


def test_api_submit_jobs_with_style_preset_and_privacy(settings_client: dict[str, Any]):
    """Verify submit_jobs updates style_preset and privacy_status on submission."""
    client: TestClient = settings_client["client"]
    pipe = settings_client["pipeline"]
    pipe.submit_links.return_value = []

    req_payload = {
        "links": "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "style_preset": "neon",
        "privacy_status": "public",
    }
    resp = client.post("/api/jobs/submit", json=req_payload)
    assert resp.status_code == 200
    assert pipe.config.upload.privacy_status == "public"
    assert getattr(pipe.config.subtitles, "style_preset", None) == "neon"


def test_api_studio_profiles(settings_client: dict[str, Any]):
    """Verify GET and POST /api/studio/profiles return and persist Studio templates."""
    client: TestClient = settings_client["client"]

    # 1. GET default profiles
    get_res = client.get("/api/studio/profiles")
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["success"] is True
    assert data["count"] >= 5
    preset_ids = [p["id"] for p in data["profiles"]]
    assert "viral_split" in preset_ids
    assert "podcast_blur" in preset_ids
    assert any("cinematic" in pid for pid in preset_ids)

    # 2. POST custom profile
    new_prof = {
        "id": "custom_cyberpunk",
        "name": "Neon Cyberpunk Studio",
        "layout_mode": "split_70_30",
        "style_preset": "cyberpunk",
        "color_grading": "vibrant",
        "dopamine_level": "ultra",
    }
    post_res = client.post("/api/studio/profiles", json=new_prof)
    assert post_res.status_code == 200
    assert post_res.json()["success"] is True

    # 3. Verify retrieved list includes new profile
    get_res2 = client.get("/api/studio/profiles")
    updated_ids = [p["id"] for p in get_res2.json()["profiles"]]
    assert "custom_cyberpunk" in updated_ids


def test_api_channels(settings_client: dict[str, Any]):
    """Verify GET and POST /api/channels manage Multi-Channel profiles."""
    client: TestClient = settings_client["client"]

    # 1. Initially empty or list
    get_res = client.get("/api/channels")
    assert get_res.status_code == 200
    assert get_res.json()["success"] is True

    # 2. Save new channel
    new_chan = {
        "name": "Tech Breakthroughs Daily",
        "handle": "@TechBreakthroughs",
        "upload_mode": "api",
        "default_preset": "viral_split",
        "daily_upload_cap": 25,
    }
    post_res = client.post("/api/channels", json=new_chan)
    assert post_res.status_code == 200
    assert post_res.json()["success"] is True
    saved_chan = post_res.json()["channel"]
    assert saved_chan["name"] == "Tech Breakthroughs Daily"
    assert saved_chan["handle"] == "@TechBreakthroughs"

    # 3. GET channels again
    get_res2 = client.get("/api/channels")
    chan_names = [c["name"] for c in get_res2.json()["channels"]]
    assert "Tech Breakthroughs Daily" in chan_names


def test_api_job_transcript_get_and_put(
    settings_client: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify GET and PUT /api/jobs/{job_id}/transcript persist and retrieve word-level captions."""
    client: TestClient = settings_client["client"]
    state_mgr: StateManager = settings_client["state_mgr"]

    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("autoshorts.core.paths.get_work_dir", lambda: work_dir)

    job = Job(
        id="job_transcript_test_123",
        source_url="https://youtube.com/watch?v=transcript123",
        status=JobStatus.SUBTITLES_GENERATED,
    )
    state_mgr.save_job(job)

    # 1. PUT transcript
    words_payload = [
        {"word": "Welcome", "start": 0.0, "end": 0.4, "probability": 0.99},
        {"word": "everyone", "start": 0.4, "end": 0.9, "probability": 0.99},
    ]
    put_res = client.put(f"/api/jobs/{job.id}/transcript", json={"words": words_payload})
    assert put_res.status_code == 200
    assert put_res.json()["success"] is True
    assert put_res.json()["count"] == 2

    # 2. GET transcript
    get_res = client.get(f"/api/jobs/{job.id}/transcript")
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["success"] is True
    assert len(data["words"]) == 2
    assert data["words"][0]["word"] == "Welcome"


def test_api_job_trim(settings_client: dict[str, Any]):
    """Verify POST /api/jobs/{job_id}/trim enforces valid bounds and updates candidate & EDL."""
    client: TestClient = settings_client["client"]
    state_mgr: StateManager = settings_client["state_mgr"]

    cand = ClipCandidate(start=0.0, end=45.0, text="Trim test transcript", score=0.88)
    sel = ClipSelection(candidate=cand, clip_index=0)
    job = Job(
        id="job_trim_test_456",
        source_url="https://youtube.com/watch?v=trim456",
        status=JobStatus.READY_FOR_REVIEW,
        selected_clips=[sel],
    )
    state_mgr.save_job(job)

    # 1. Valid trim
    trim_payload = {
        "start_sec": 5.0,
        "end_sec": 35.0,
        "jump_cuts": [{"cut_start": 12.0, "cut_end": 13.5, "reason": "pause"}],
    }
    trim_res = client.post(f"/api/jobs/{job.id}/trim", json=trim_payload)
    assert trim_res.status_code == 200
    trim_data = trim_res.json()
    assert trim_data["success"] is True
    assert trim_data["start_sec"] == 5.0
    assert trim_data["end_sec"] == 35.0
    assert trim_data["duration"] == 30.0

    reloaded = state_mgr.get_job(job.id)
    assert reloaded.selected_clips[0].candidate.start == 5.0
    assert reloaded.selected_clips[0].candidate.end == 35.0
    assert reloaded.edl.hook_in_sec == 5.0
    assert reloaded.edl.hook_out_sec == 35.0
    assert len(reloaded.edl.jump_cuts) == 1

    # 2. Invalid trim: start >= end
    bad_res1 = client.post(f"/api/jobs/{job.id}/trim", json={"start_sec": 40.0, "end_sec": 20.0})
    assert bad_res1.status_code == 400

    # 3. Invalid trim: duration > 60s
    bad_res2 = client.post(f"/api/jobs/{job.id}/trim", json={"start_sec": 0.0, "end_sec": 75.0})
    assert bad_res2.status_code == 400


def test_api_job_rerender(settings_client: dict[str, Any]):
    """Verify POST /api/jobs/{job_id}/rerender triggers pipe.rerender_job with overrides."""
    client: TestClient = settings_client["client"]
    state_mgr: StateManager = settings_client["state_mgr"]
    pipe = settings_client["pipeline"]

    cand = ClipCandidate(start=0.0, end=30.0, text="Rerender test", score=0.85)
    sel = ClipSelection(candidate=cand, clip_index=0)
    job = Job(
        id="job_rerender_api_789",
        source_url="https://youtube.com/watch?v=rerender789",
        status=JobStatus.READY_FOR_REVIEW,
        selected_clips=[sel],
    )
    state_mgr.save_job(job)

    # Mock pipe.rerender_job return
    job.style_preset = "hormozi"
    pipe.rerender_job.return_value = job

    rerender_payload = {
        "layout_mode": "split_50_50",
        "style_preset": "hormozi",
        "color_grading": "vibrant",
        "hook_text": "HOOK FX",
    }
    resp = client.post(f"/api/jobs/{job.id}/rerender", json=rerender_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["job"]["id"] == job.id

    pipe.rerender_job.assert_called_once_with(
        job.id,
        layout_mode="split_50_50",
        style_preset="hormozi",
        color_grading="vibrant",
        hook_text="HOOK FX",
    )


def test_api_post_settings_noop_keeps_config_file_byte_identical(
    settings_client: dict[str, Any],
) -> None:
    """A save that changes nothing must not rewrite config.yaml (yaml.safe_dump drops comments)."""
    client: TestClient = settings_client["client"]
    cfg_file: Path = settings_client["cfg_file"]
    cfg_file.write_text("# hand-written note\n" + cfg_file.read_text(encoding="utf-8"), "utf-8")
    before = cfg_file.read_bytes()

    res = client.post("/api/settings", json={"llm": {"provider": "local"}})
    assert res.status_code == 200
    assert cfg_file.read_bytes() == before

    # A real change still persists.
    res = client.post("/api/settings", json={"llm": {"temperature": 0.3}})
    assert res.status_code == 200
    import yaml

    assert yaml.safe_load(cfg_file.read_text(encoding="utf-8"))["llm"]["temperature"] == 0.3


def test_api_post_settings_rejected_value_writes_neither_config_nor_env(
    settings_client: dict[str, Any],
) -> None:
    """A value the schema rejects must not reach config.yaml, or the next start fails to load it."""
    client: TestClient = settings_client["client"]
    cfg_file: Path = settings_client["cfg_file"]
    env_file: Path = settings_client["env_file"]
    before_yaml = cfg_file.read_bytes()
    env_before = env_file.read_bytes() if env_file.exists() else None

    res = client.post(
        "/api/settings",
        json={
            "upload": {"mode": "bogus"},
            "secrets": {"gemini_api_key": "AIzaSyBrandNewKey1234567890"},
        },
    )
    assert res.status_code == 400, res.text

    assert cfg_file.read_bytes() == before_yaml
    env_after = env_file.read_bytes() if env_file.exists() else None
    assert env_after == env_before
