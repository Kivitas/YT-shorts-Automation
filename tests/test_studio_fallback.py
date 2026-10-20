"""Unit tests for YouTube Studio auto-fallback, Edge/Chromium launcher fallback, and Public visibility publishing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.core.config import Settings, UploadConfig
from autoshorts.core.exceptions import QuotaExhaustedError
from autoshorts.core.models import ClipCandidate, ClipSelection, Job, JobStatus, UploadResult
from autoshorts.core.state_manager import StateManager
from autoshorts.pipeline import ShortsPipeline
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.scheduler import SpreadScheduler
from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader
from autoshorts.uploader.youtube_api import YouTubeApiUploader


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_state.db"


@pytest.fixture
def state_mgr(temp_db: Path) -> StateManager:
    return StateManager(temp_db)


@pytest.fixture
def dummy_video(tmp_path: Path) -> Path:
    video_file = tmp_path / "dummy_short.mp4"
    video_file.write_bytes(b"\x00\x00\x00 ftypisom" + b"\x00" * 1024)
    return video_file


# -----------------------------------------------------------------------------
# 1. Studio Uploader Visibility & Public Publishing Tests
# -----------------------------------------------------------------------------


def test_studio_uploader_selects_public_radio_and_publishes(dummy_video: Path):
    """Verify that when privacy_status == 'public', it explicitly selects PUBLIC radio and clicks #done-button."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.url = "https://studio.youtube.com"

    locators: dict[str, MagicMock] = {}

    def get_locator(selector: str) -> MagicMock:
        loc = MagicMock()
        loc.is_visible.return_value = True
        loc.first = loc
        loc.nth.return_value = loc
        loc.get_attribute.return_value = "https://youtu.be/pubVid12345"
        locators[selector] = loc
        return loc

    mock_page.locator.side_effect = get_locator

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory)

    result = uploader.upload(
        video_path=dummy_video,
        title="Epic Public Short",
        description="Public video description",
        privacy_status="public",
    )

    assert result.video_id == "pubVid12345"
    assert result.url == "https://youtube.com/shorts/pubVid12345"
    assert result.privacy_status == "public"
    assert result.quota_spent == 0

    # Verify PUBLIC radio was selected
    matched_public_radio = any(
        'tp-yt-paper-radio-button[name="PUBLIC"]' in sel or '[name="PUBLIC"]' in sel
        for sel in locators
    )
    assert matched_public_radio, f"Expected PUBLIC radio selector, got: {list(locators.keys())}"

    # Verify #done-button was clicked (which publishes in Studio)
    matched_done_button = any("#done-button" in sel for sel in locators)
    assert matched_done_button, f"Expected #done-button in locators, got: {list(locators.keys())}"
    done_loc = next(loc for sel, loc in locators.items() if "#done-button" in sel)
    assert done_loc.click.called


def test_studio_uploader_supports_unlisted_and_private_visibilities(dummy_video: Path):
    """Verify UNLISTED and PRIVATE radio buttons are also selected based on privacy_status."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.url = "https://studio.youtube.com"

    for target_privacy, expected_key in [("unlisted", "UNLISTED"), ("PRIVATE", "PRIVATE")]:
        locators_called: list[str] = []

        def get_locator(selector: str, calls: list[str] = locators_called) -> MagicMock:
            calls.append(selector)
            loc = MagicMock()
            loc.is_visible.return_value = True
            loc.first = loc
            loc.nth.return_value = loc
            loc.get_attribute.return_value = "https://youtu.be/vidXYZ99999"
            return loc

        mock_page.locator.side_effect = get_locator
        factory = MagicMock(return_value=mock_ctx)
        uploader = PlaywrightStudioUploader(context_factory=factory)

        res = uploader.upload(
            video_path=dummy_video,
            title="Visibility Test",
            privacy_status=target_privacy,
        )

        assert res.privacy_status == target_privacy.lower()
        matched = any(f'name="{expected_key}"' in sel for sel in locators_called)
        assert matched, f"Expected radio key {expected_key} in {locators_called}"


def test_studio_uploader_edge_channel_fallback_to_chromium(dummy_video: Path):
    """Verify that if Edge channel launch fails, it falls back cleanly to default chromium."""
    factory = MagicMock()

    # First call with channel='msedge' raises Exception (Edge binary not found)
    # Second call without channel succeeds
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.url = "https://studio.youtube.com"

    mock_loc = MagicMock()
    mock_loc.is_visible.return_value = True
    mock_loc.first = mock_loc
    mock_loc.nth.return_value = mock_loc
    mock_loc.get_attribute.return_value = "https://youtu.be/fallbackVid"
    mock_page.locator.return_value = mock_loc

    def mock_factory_call(**kwargs: Any) -> MagicMock:
        if kwargs.get("channel") == "msedge":
            raise RuntimeError("msedge executable not found on host")
        return mock_ctx

    factory.side_effect = mock_factory_call

    uploader = PlaywrightStudioUploader(context_factory=factory, channel="msedge")
    res = uploader.upload(video_path=dummy_video, title="Edge Fallback", privacy="public")

    assert res.video_id == "fallbackVid"
    assert factory.call_count == 2
    # First call had channel="msedge"
    assert factory.call_args_list[0][1].get("channel") == "msedge"
    # Second call had no channel (fallback)
    assert (
        factory.call_args_list[1][1].get("channel") is None
        or "channel" not in factory.call_args_list[1][1]
    )


# -----------------------------------------------------------------------------
# 2. Config & Defaults Tests
# -----------------------------------------------------------------------------


def test_upload_config_defaults_and_public_status():
    """Verify default quota_exhausted_fallback is 'studio' and default_privacy_status accepts 'public'."""
    cfg = UploadConfig()
    assert cfg.quota_exhausted_fallback == "studio"
    assert cfg.default_privacy_status == "unlisted"

    # Set default_privacy_status to public
    public_cfg = UploadConfig(default_privacy_status="public")
    assert public_cfg.default_privacy_status == "public"
    assert public_cfg.privacy_status == "public"

    # Set privacy_status to public
    public_cfg2 = UploadConfig(privacy_status="public")
    assert public_cfg2.default_privacy_status == "public"
    assert public_cfg2.privacy_status == "public"


# -----------------------------------------------------------------------------
# 3. SpreadScheduler Auto-Fallback Tests
# -----------------------------------------------------------------------------


def test_scheduler_fallback_to_studio_when_can_upload_is_false(
    state_mgr: StateManager, dummy_video: Path
):
    """When quota_tracker.can_upload() is False, SpreadScheduler dispatches via PlaywrightStudioUploader with public status."""
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)
    # Simulate exhausted upload bucket (100 calls used out of 100 limit)
    tracker.record_upload(100)
    assert tracker.can_upload() is False

    settings = Settings()
    settings.upload.quota_exhausted_fallback = "studio"
    settings.upload.default_privacy_status = "public"

    yt_api_uploader = YouTubeApiUploader(quota_tracker=tracker)

    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=yt_api_uploader,
        config=settings,
        clock=lambda: now,
    )

    # Enqueue a job for upload
    job_id = "job_quota_exhausted_1"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtu.be/123"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path=str(dummy_video),
        title="Public Short Under Zero API Quota",
        description="Uploaded via studio fallback",
        scheduled_time=now,
        privacy_status="unlisted",
    )

    studio_result = UploadResult(
        video_id="studioPub1234",
        url="https://youtube.com/shorts/studioPub1234",
        status="uploaded",
        verified=True,
        privacy_status="public",
        quota_spent=0,
        raw_response={"mode": "studio_playwright"},
    )

    with (
        patch.object(
            PlaywrightStudioUploader, "upload", return_value=studio_result
        ) as mock_studio_upload,
        patch.object(PlaywrightStudioUploader, "verify", return_value=True) as mock_studio_verify,
    ):
        res = scheduler.run_tick()

        assert res is not None
        assert res.video_id == "studioPub1234"
        assert res.privacy_status == "public"
        assert res.quota_spent == 0
        assert res.raw_response.get("fallback") == "studio_automation"

        # Verify Studio uploader was dispatched with public privacy status
        mock_studio_upload.assert_called_once()
        upload_kwargs = mock_studio_upload.call_args[1]
        assert (
            upload_kwargs.get("privacy") == "public"
            or upload_kwargs.get("privacy_status") == "public"
        )

        mock_studio_verify.assert_called_once_with("studioPub1234")

    # Verify state and slot
    job = state_mgr.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.upload_result is not None
    assert job.upload_result.video_id == "studioPub1234"

    # Quota spend should remain 100 (zero additional quota consumed)
    assert tracker.get_upload_spend() == 100


def test_scheduler_fallback_when_api_raises_quota_exhausted_error(
    state_mgr: StateManager, dummy_video: Path
):
    """When YouTubeApiUploader.upload() raises QuotaExhaustedError, scheduler seamlessly falls back to Studio."""
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)
    assert tracker.can_upload() is True

    settings = Settings()
    settings.upload.quota_exhausted_fallback = "studio"
    settings.upload.default_privacy_status = "public"

    mock_api_uploader = MagicMock(spec=YouTubeApiUploader)
    mock_api_uploader.upload.side_effect = QuotaExhaustedError(
        "YouTube Data API daily limit reached (403 quotaExceeded)"
    )

    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=mock_api_uploader,
        config=settings,
        clock=lambda: now,
    )

    job_id = "job_quota_err_fallback"
    state_mgr.save_job(Job(id=job_id, source_url="https://youtu.be/456"))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path=str(dummy_video),
        title="API 403 Fallback",
        description="Desc",
        scheduled_time=now,
        privacy_status="unlisted",
    )

    studio_result = UploadResult(
        video_id="studioFromApi403",
        url="https://youtube.com/shorts/studioFromApi403",
        status="uploaded",
        verified=True,
        privacy_status="public",
        quota_spent=0,
        raw_response={"mode": "studio_playwright"},
    )

    with (
        patch.object(
            PlaywrightStudioUploader, "upload", return_value=studio_result
        ) as mock_studio_upload,
        patch.object(PlaywrightStudioUploader, "verify", return_value=True),
    ):
        res = scheduler.upload_now(job_id)

        assert res.video_id == "studioFromApi403"
        assert res.privacy_status == "public"
        assert res.raw_response.get("fallback") == "studio_automation"
        mock_studio_upload.assert_called_once()


# -----------------------------------------------------------------------------
# 4. ShortsPipeline Immediate Upload Auto-Fallback Tests
# -----------------------------------------------------------------------------


def test_pipeline_immediate_upload_studio_fallback_when_quota_exhausted(
    state_mgr: StateManager, dummy_video: Path
):
    """ShortsPipeline immediate upload automatically falls back to PlaywrightStudioUploader when quota is exhausted."""
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)
    tracker.record_upload(100)
    assert tracker.can_upload() is False

    settings = Settings()
    settings.upload.quota_exhausted_fallback = "studio"
    settings.upload.default_privacy_status = "public"

    yt_api_uploader = YouTubeApiUploader(quota_tracker=tracker)

    pipeline = ShortsPipeline(
        config=settings,
        state_manager=state_mgr,
        uploader=yt_api_uploader,
        quota_tracker=tracker,
    )

    clip_cand = ClipCandidate(start=0.0, end=45.0, text="Test clip", score=0.9)
    selection = ClipSelection(candidate=clip_cand, hook_text="Awesome Hook", title="Awesome Short")
    job = Job(
        id="job_pipe_fallback",
        source_url="https://youtu.be/pipe1",
        status=JobStatus.READY_FOR_REVIEW,
        rendered_video_path=str(dummy_video),
        selected_clips=[selection],
    )
    state_mgr.save_job(job)

    studio_result = UploadResult(
        video_id="pipeStudio1",
        url="https://youtube.com/shorts/pipeStudio1",
        status="uploaded",
        verified=True,
        privacy_status="public",
        quota_spent=0,
        raw_response={"mode": "studio_playwright"},
    )

    with (
        patch.object(
            PlaywrightStudioUploader, "upload", return_value=studio_result
        ) as mock_studio_upload,
        patch.object(PlaywrightStudioUploader, "verify", return_value=True),
    ):
        result = pipeline._run_immediate_upload(job)

        assert result.video_id == "pipeStudio1"
        assert result.privacy_status == "public"
        assert result.raw_response.get("fallback") == "studio_automation"

        # Verify Studio uploader received public status
        mock_studio_upload.assert_called_once()
        kwargs = mock_studio_upload.call_args[1]
        assert kwargs.get("privacy") == "public" or kwargs.get("privacy_status") == "public"

    # Verify stage recorded in state_manager
    stages = state_mgr.get_job_stages("job_pipe_fallback")
    stage_names = [s["stage_name"] for s in stages]
    assert "studio_fallback" in stage_names


def test_pipeline_immediate_upload_studio_fallback_on_api_quota_exception(
    state_mgr: StateManager, dummy_video: Path
):
    """ShortsPipeline immediate upload catches QuotaExhaustedError and falls back to Studio."""
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    tracker = QuotaTracker(state_mgr, clock=lambda: now)

    settings = Settings()
    settings.upload.quota_exhausted_fallback = "studio"
    settings.upload.default_privacy_status = "public"

    mock_uploader = MagicMock(spec=YouTubeApiUploader)
    mock_uploader.upload.side_effect = QuotaExhaustedError(
        "403 quotaExceeded: The request cannot be completed because you have exceeded your quota."
    )

    pipeline = ShortsPipeline(
        config=settings,
        state_manager=state_mgr,
        uploader=mock_uploader,
        quota_tracker=tracker,
    )

    clip_cand = ClipCandidate(start=0.0, end=45.0, text="Test clip", score=0.9)
    selection = ClipSelection(candidate=clip_cand, hook_text="Awesome Hook", title="Awesome Short")
    job = Job(
        id="job_pipe_ex_fallback",
        source_url="https://youtu.be/pipe2",
        status=JobStatus.READY_FOR_REVIEW,
        rendered_video_path=str(dummy_video),
        selected_clips=[selection],
    )
    state_mgr.save_job(job)

    studio_result = UploadResult(
        video_id="pipeStudio2",
        url="https://youtube.com/shorts/pipeStudio2",
        status="uploaded",
        verified=True,
        privacy_status="public",
        quota_spent=0,
        raw_response={"mode": "studio_playwright"},
    )

    with (
        patch.object(
            PlaywrightStudioUploader, "upload", return_value=studio_result
        ) as mock_studio_upload,
        patch.object(PlaywrightStudioUploader, "verify", return_value=True),
    ):
        result = pipeline._run_immediate_upload(job)

        assert result.video_id == "pipeStudio2"
        assert result.privacy_status == "public"
        mock_studio_upload.assert_called_once()
