"""Unit tests for YouTube API uploader, chunked resumable protocol, verifier, and local exporter."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.core.contracts import Uploader
from autoshorts.core.exceptions import UploadError
from autoshorts.core.state_manager import StateManager
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.uploader.local_exporter import LocalExportUploader
from autoshorts.uploader.studio_playwright import (
    _NAV_TIMEOUT_MS,
    LOGIN_SESSION_STATE,
    PlaywrightStudioUploader,
    update_login_session_state,
)
from autoshorts.uploader.verifier import YouTubeVerifier
from autoshorts.uploader.youtube_api import (
    UploadFailedError,
    YouTubeApiUploader,
    build_compliant_tags,
)


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


def test_uploader_protocol_conformance(tmp_path: Path):
    """Verify all upload engines satisfy contracts.Uploader protocol."""
    yt_uploader = YouTubeApiUploader(youtube_service=MagicMock())
    local_uploader = LocalExportUploader(export_dir=tmp_path / "export")
    studio_uploader = PlaywrightStudioUploader()

    assert isinstance(yt_uploader, Uploader)
    assert isinstance(local_uploader, Uploader)
    assert isinstance(studio_uploader, Uploader)


def test_youtube_api_resumable_upload_chunking(dummy_video: Path, state_mgr: StateManager):
    """Test chunked resumable upload with mocked Google API client and #Shorts title injection."""
    tracker = QuotaTracker(state_mgr)

    # Mock Google service
    mock_service = MagicMock()
    mock_insert_request = MagicMock()

    # Simulate next_chunk: chunk 1 progress 50%, chunk 2 completes with response
    mock_progress = MagicMock()
    mock_progress.progress.return_value = 0.5
    final_response = {
        "id": "abc123XYZ00",
        "status": {"privacyStatus": "unlisted"},
    }
    mock_insert_request.next_chunk.side_effect = [
        (mock_progress, None),
        (None, final_response),
    ]
    mock_service.videos().insert.return_value = mock_insert_request

    uploader = YouTubeApiUploader(
        quota_tracker=tracker,
        youtube_service=mock_service,
        category_id="27",
    )

    # Title without #Shorts -> should be appended
    res = uploader.upload(
        path=dummy_video,
        title="Fascinating Science Facts",
        description="Check this out!",
        tags=["science", "facts"],
        privacy="unlisted",
    )

    assert res.video_id == "abc123XYZ00"
    assert res.url == "https://youtube.com/shorts/abc123XYZ00"
    assert res.status == "uploaded"
    assert res.privacy_status == "unlisted"
    assert res.quota_spent == 1

    # Verify upload quota was recorded in tracker
    assert tracker.get_upload_spend() == 1

    # Verify request body snippet and title
    call_kwargs = mock_service.videos().insert.call_args[1]
    snippet = call_kwargs["body"]["snippet"]
    assert snippet["title"] == "Fascinating Science Facts #Shorts"
    assert snippet["categoryId"] == "27"
    assert snippet["tags"] == ["science", "facts"]


def test_youtube_api_resumable_retry_backoff(dummy_video: Path):
    """Test chunk retry backoff on transient network error."""
    mock_service = MagicMock()
    mock_insert_request = MagicMock()

    # Fail on first chunk attempt, succeed on retry
    mock_insert_request.next_chunk.side_effect = [
        ConnectionError("Transient network blip"),
        (None, {"id": "retrySuccess1", "status": {"privacyStatus": "unlisted"}}),
    ]
    mock_service.videos().insert.return_value = mock_insert_request

    uploader = YouTubeApiUploader(
        youtube_service=mock_service,
        max_retries=2,
    )

    with patch("time.sleep") as mock_sleep:
        res = uploader.upload(
            path=dummy_video,
            title="Retry Test #Shorts",
            description="desc",
            tags=[],
        )
        assert res.video_id == "retrySuccess1"
        mock_sleep.assert_called_once()


def test_youtube_api_resumable_permanent_failure(dummy_video: Path):
    """Test upload raises UploadFailedError after max retries are exceeded."""
    mock_service = MagicMock()
    mock_insert_request = MagicMock()

    mock_insert_request.next_chunk.side_effect = OSError("Connection refused")
    mock_service.videos().insert.return_value = mock_insert_request

    uploader = YouTubeApiUploader(
        youtube_service=mock_service,
        max_retries=2,
    )

    with patch("time.sleep"), pytest.raises(UploadFailedError):
        uploader.upload(
            path=dummy_video,
            title="Fail Test",
            description="desc",
            tags=[],
        )


def test_youtube_api_verification_success_and_general_quota(state_mgr: StateManager):
    """Test verify() returns True for uploadStatus='uploaded' and processingStatus='succeeded'."""
    tracker = QuotaTracker(state_mgr)
    mock_service = MagicMock()

    mock_list_req = MagicMock()
    mock_list_req.execute.return_value = {
        "items": [
            {
                "id": "vid123",
                "status": {"uploadStatus": "uploaded", "privacyStatus": "unlisted"},
                "processingDetails": {"processingStatus": "succeeded"},
            }
        ]
    }
    mock_service.videos().list.return_value = mock_list_req

    uploader = YouTubeApiUploader(
        quota_tracker=tracker,
        youtube_service=mock_service,
    )

    assert uploader.verify("vid123") is True
    # Verify 1 general quota unit consumed
    assert tracker.get_general_spend() == 1


def test_youtube_api_verification_still_processing():
    """Test verify() returns False when video is still processing."""
    mock_service = MagicMock()
    mock_list_req = MagicMock()
    mock_list_req.execute.return_value = {
        "items": [
            {
                "id": "vid_proc",
                "status": {"uploadStatus": "uploaded", "privacyStatus": "unlisted"},
                "processingDetails": {"processingStatus": "processing"},
            }
        ]
    }
    mock_service.videos().list.return_value = mock_list_req

    uploader = YouTubeApiUploader(youtube_service=mock_service)
    assert uploader.verify("vid_proc") is False


def test_youtube_api_verification_privacy_lock_warning(caplog: pytest.LogCaptureFixture):
    """Test verify() detects privacyStatus='private' and logs a warning."""
    mock_service = MagicMock()
    mock_list_req = MagicMock()
    mock_list_req.execute.return_value = {
        "items": [
            {
                "id": "vid_priv",
                "status": {"uploadStatus": "uploaded", "privacyStatus": "private"},
                "processingDetails": {"processingStatus": "succeeded"},
            }
        ]
    }
    mock_service.videos().list.return_value = mock_list_req

    uploader = YouTubeApiUploader(youtube_service=mock_service)
    with caplog.at_level(logging.WARNING):
        verified = uploader.verify("vid_priv")
        assert verified is True
        assert any("locked until compliance audit" in record.message for record in caplog.records)


def test_youtube_verifier_poll_loop():
    """Test YouTubeVerifier.poll_until_verified retries until video is verified."""
    mock_uploader = MagicMock()
    mock_uploader.verify.side_effect = [False, False, True]

    verifier = YouTubeVerifier(mock_uploader)

    with patch("time.sleep") as mock_sleep:
        success = verifier.poll_until_verified("vid_poll", max_retries=5, delay_sec=1.0)
        assert success is True
        assert mock_uploader.verify.call_count == 3
        assert mock_sleep.call_count == 2


def test_local_exporter_copy_and_verify(dummy_video: Path, tmp_path: Path):
    """Test LocalExportUploader copies file to export dir, creates sidecar, and verifies."""
    export_dir = tmp_path / "output" / "export"
    exporter = LocalExportUploader(export_dir=export_dir)

    result = exporter.upload(
        path=dummy_video,
        title="Local Export Test",
        description="Local Export Description",
        tags=["local", "export"],
        privacy="unlisted",
    )

    assert result.status == "exported"
    assert result.verified is True
    assert result.quota_spent == 0
    assert result.video_id.startswith("local_")

    # Verify exported file exists in export_dir
    dest_path = Path(result.raw_response["export_path"])
    assert dest_path.is_file()
    assert dest_path.stat().st_size == dummy_video.stat().st_size

    # Verify metadata sidecar JSON
    sidecar_path = Path(result.raw_response["sidecar_path"])
    assert sidecar_path.is_file()

    # Verify verify() returns True
    assert exporter.verify(result.video_id) is True


def test_studio_playwright_open_login_session_detected():
    """Verify open_login_session returns True when authenticated Studio DOM elements appear."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.is_closed.return_value = False
    mock_page.url = "https://studio.youtube.com"

    mock_loc = MagicMock()
    mock_loc.is_visible.return_value = True
    mock_page.locator.return_value = mock_loc

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory, headless=False)

    success = uploader.open_login_session(timeout_sec=5.0)
    assert success is True
    factory.assert_called_once()
    mock_page.goto.assert_called_with("https://studio.youtube.com", wait_until="domcontentloaded")


def test_studio_playwright_open_login_session_window_closed():
    """Verify open_login_session terminates and returns False if user closes Edge window."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.is_closed.return_value = True

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory, headless=False)

    success = uploader.open_login_session(timeout_sec=5.0)
    assert success is False


def test_studio_playwright_upload_file_not_found(tmp_path: Path):
    """Verify upload raises UploadError if video file does not exist."""
    uploader = PlaywrightStudioUploader()
    missing_path = tmp_path / "nonexistent.mp4"
    with pytest.raises(UploadError) as exc_info:
        uploader.upload(video_path=missing_path, title="Short", description="Desc")
    assert "does not exist" in str(exc_info.value)


def test_studio_playwright_upload_unauthenticated_redirect(dummy_video: Path):
    """Verify upload raises clear error prompting edge-login when redirected to accounts.google.com."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.url = "https://accounts.google.com/signin/v2/identifier?service=youtube"

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory)

    with pytest.raises(UploadError) as exc_info:
        uploader.upload(
            video_path=dummy_video,
            title="Unauthenticated Test",
            description="Desc",
        )
    assert "autoshorts edge-login" in str(exc_info.value)


def test_studio_playwright_upload_success_flow(dummy_video: Path):
    """Verify successful studio upload steps: file picker, title, desc, not-MFK, next clicks, visibility, video ID extraction."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.url = "https://studio.youtube.com"

    mock_loc = MagicMock()
    mock_loc.is_visible.return_value = True
    mock_loc.first = mock_loc
    mock_loc.nth.return_value = mock_loc
    mock_loc.get_attribute.return_value = "https://youtu.be/edgeVid1234"

    mock_page.locator.return_value = mock_loc

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory)

    result = uploader.upload(
        video_path=dummy_video,
        title="Edge Automated Short",
        description="Uploaded via Playwright Edge persistent profile",
        tags=["edge", "shorts"],
        privacy_status="unlisted",
    )

    assert result.video_id == "edgeVid1234"
    assert result.url == "https://youtube.com/shorts/edgeVid1234"
    # Studio automation is a weaker signal than the Data API: the id was scraped from the
    # DOM, so the result must not claim to be YouTube-confirmed.
    assert result.status == "uploaded_unconfirmed"
    assert result.verified is False
    assert result.verification_kind == "studio_dom"
    assert result.verification_note is not None
    assert result.privacy_status == "unlisted"
    assert result.quota_spent == 0
    assert result.raw_response["mode"] == "studio_playwright"

    # Verify key steps were called (file attach carries an explicit timeout)
    mock_loc.set_input_files.assert_called_with(str(dummy_video.resolve()), timeout=_NAV_TIMEOUT_MS)
    mock_loc.fill.assert_any_call("Edge Automated Short #Shorts")
    mock_loc.fill.assert_any_call("Uploaded via Playwright Edge persistent profile")


def test_studio_playwright_verify():
    """Verify verify() returns True for valid Short and False for unavailable."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]

    # 1. Successful check
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_page.goto.return_value = mock_resp
    mock_page.title.return_value = "Active Video Title - YouTube"
    mock_page.content.return_value = "<html><body>Playing short</body></html>"

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory)

    assert uploader.verify("edgeVid12345") is True

    # 2. Unavailable video
    mock_page.title.return_value = "Video unavailable - YouTube"
    assert uploader.verify("edgeVid12345") is False

    # 3. HTTP 404
    mock_resp.status = 404
    assert uploader.verify("edgeVid12345") is False

    # 4. Empty ID
    assert uploader.verify("") is False


def test_studio_check_login_status_missing_profile(tmp_path: Path):
    """Verify check_login_status returns signed_in=False when profile dir does not exist."""
    uploader = PlaywrightStudioUploader(edge_profile_dir=tmp_path / "nonexistent_profile")
    res = uploader.check_login_status()
    assert res == {"signed_in": False, "channel_name": None}


def test_studio_check_login_status_redirect_accounts_google():
    """Verify check_login_status returns signed_in=False when redirected to accounts.google.com."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.url = "https://accounts.google.com/ServiceLogin?service=youtube"

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory)

    res = uploader.check_login_status()
    assert res == {"signed_in": False, "channel_name": None}


def test_studio_check_login_status_signed_in_with_extracted_channel_name():
    """Verify check_login_status returns signed_in=True with extracted creator/channel name."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.url = "https://studio.youtube.com/channel/UC12345"

    mock_loc = MagicMock()
    mock_loc.first = mock_loc
    mock_loc.is_visible.return_value = True
    mock_loc.inner_text.return_value = "Tech Creator HQ"
    mock_page.locator.return_value = mock_loc

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory)

    res = uploader.check_login_status()
    assert res == {"signed_in": True, "channel_name": "Tech Creator HQ"}


def test_studio_check_login_status_signed_in_fallback_active_creator():
    """Verify check_login_status returns signed_in=True and 'Active Creator' fallback."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.url = "https://studio.youtube.com"

    mock_loc = MagicMock()
    mock_loc.first = mock_loc
    mock_loc.is_visible.return_value = True
    mock_loc.inner_text.return_value = ""
    mock_loc.get_attribute.return_value = ""
    mock_page.locator.return_value = mock_loc

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory)

    res = uploader.check_login_status()
    assert res == {"signed_in": True, "channel_name": "Active Creator"}


def test_studio_open_login_session_updates_login_session_state():
    """Verify open_login_session updates LOGIN_SESSION_STATE to running then signed_in."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.is_closed.return_value = False
    mock_page.url = "https://studio.youtube.com"

    mock_loc = MagicMock()
    mock_loc.first = mock_loc
    mock_loc.is_visible.return_value = True
    mock_loc.inner_text.return_value = "Channel Alpha"
    mock_page.locator.return_value = mock_loc

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory, headless=False)

    update_login_session_state(status="idle", message="", error=None, channel_name=None)
    success = uploader.open_login_session(timeout_sec=5.0)

    assert success is True
    assert LOGIN_SESSION_STATE["status"] == "signed_in"
    assert LOGIN_SESSION_STATE["channel_name"] == "Channel Alpha"
    assert "Successfully signed in" in LOGIN_SESSION_STATE["message"]


def test_studio_open_login_session_window_closed_state():
    """Verify open_login_session updates LOGIN_SESSION_STATE to not_signed_in on early close."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.is_closed.return_value = True

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory, headless=False)

    update_login_session_state(status="idle", message="", error=None, channel_name=None)
    success = uploader.open_login_session(timeout_sec=5.0)

    assert success is False
    assert LOGIN_SESSION_STATE["status"] == "not_signed_in"
    assert "Edge closed before sign-in completed" in LOGIN_SESSION_STATE["message"]


def test_studio_open_login_session_error_state():
    """Verify open_login_session updates LOGIN_SESSION_STATE to error on browser exception."""
    factory = MagicMock(side_effect=RuntimeError("Edge crash mock"))
    uploader = PlaywrightStudioUploader(context_factory=factory, headless=False)

    update_login_session_state(status="idle", message="", error=None, channel_name=None)
    success = uploader.open_login_session(timeout_sec=5.0)

    assert success is False
    assert LOGIN_SESSION_STATE["status"] == "error"
    assert "Edge crash mock" in LOGIN_SESSION_STATE["error"]


def test_build_compliant_tags():
    """Verify build_compliant_tags deduplicates, strips < >, enforces #Shorts and 500-char cap."""
    # 1. Deduplication preserving order, stripping < and >
    res1 = build_compliant_tags(["<tech>", "AI", "TECH", "coding>"], ["ai", "python"])
    assert "tech" in res1
    assert "coding" in res1
    assert "python" in res1
    assert "Shorts" in res1
    # Check deduplication
    assert len([t for t in res1 if t.lower() == "tech"]) == 1
    assert len([t for t in res1 if t.lower() == "ai"]) == 1

    # 2. If #Shorts already present, don't duplicate
    res2 = build_compliant_tags(["#Shorts", "gaming"])
    assert res2.count("#Shorts") == 1
    assert "Shorts" not in res2

    # 3. 500-char cap enforcement
    long_tags = [f"tag_number_{i:04d}_with_some_long_filler_text" for i in range(50)]
    res3 = build_compliant_tags(long_tags)
    total_len = sum(len(t) for t in res3) + len(res3) - 1
    assert total_len <= 500
    assert any(t.lower() in ("shorts", "#shorts") for t in res3)


def test_youtube_api_server_side_scheduling_publish_at(dummy_video: Path):
    """Verify publish_at sets privacyStatus='private' and publishAt in videos.insert body."""
    mock_service = MagicMock()
    mock_insert_req = MagicMock()
    mock_insert_req.next_chunk.return_value = (
        None,
        {"id": "sched_vid_123", "status": {"privacyStatus": "private"}},
    )
    mock_service.videos().insert.return_value = mock_insert_req

    uploader = YouTubeApiUploader(youtube_service=mock_service)
    res = uploader.upload(
        path=dummy_video,
        title="Scheduled Video",
        description="desc",
        tags=["test"],
        publish_at="2099-10-25T15:00:00Z",
    )

    assert res.video_id == "sched_vid_123"
    call_body = mock_service.videos().insert.call_args[1]["body"]
    assert call_body["status"]["privacyStatus"] == "private"
    assert call_body["status"]["publishAt"] == "2099-10-25T15:00:00Z"


def test_youtube_api_altered_content_and_languages(dummy_video: Path):
    """Verify altered_content sets containsSyntheticMedia and defaultLanguage is populated."""
    mock_service = MagicMock()
    mock_insert_req = MagicMock()
    mock_insert_req.next_chunk.return_value = (
        None,
        {"id": "alt_vid_123", "status": {"privacyStatus": "unlisted"}},
    )
    mock_service.videos().insert.return_value = mock_insert_req

    uploader = YouTubeApiUploader(youtube_service=mock_service)
    uploader.upload(
        path=dummy_video,
        title="Altered Video",
        description="desc",
        tags=["test"],
        altered_content=True,
        default_language="es",
    )

    call_body = mock_service.videos().insert.call_args[1]["body"]
    assert call_body["status"]["containsSyntheticMedia"] is True
    assert call_body["snippet"]["defaultLanguage"] == "es"
    assert call_body["snippet"]["defaultAudioLanguage"] == "es"


def test_youtube_api_post_upload_actions(dummy_video: Path):
    """Verify add_to_playlist and post_comment are called after upload when configured."""
    mock_service = MagicMock()
    mock_insert_req = MagicMock()
    mock_insert_req.next_chunk.return_value = (
        None,
        {"id": "post_vid_123", "status": {"privacyStatus": "unlisted"}},
    )
    mock_service.videos().insert.return_value = mock_insert_req

    mock_playlist_req = MagicMock()
    mock_playlist_req.execute.return_value = {"id": "pl_item_1"}
    mock_service.playlistItems().insert.return_value = mock_playlist_req

    mock_comment_req = MagicMock()
    mock_comment_req.execute.return_value = {"id": "comm_1"}
    mock_service.commentThreads().insert.return_value = mock_comment_req

    uploader = YouTubeApiUploader(youtube_service=mock_service)

    # Direct helper test
    assert uploader.add_to_playlist("post_vid_123", "PL999") is True
    mock_service.playlistItems().insert.assert_called_with(
        part="snippet",
        body={
            "snippet": {
                "playlistId": "PL999",
                "resourceId": {"kind": "youtube#video", "videoId": "post_vid_123"},
            }
        },
    )

    assert uploader.post_comment("post_vid_123", "Hello world") == "comm_1"
    mock_service.commentThreads().insert.assert_called_with(
        part="snippet",
        body={
            "snippet": {
                "videoId": "post_vid_123",
                "topLevelComment": {"snippet": {"textOriginal": "Hello world"}},
            }
        },
    )

    # Post-upload actions integrated into upload()
    uploader.upload(
        path=dummy_video,
        title="Post Actions Test",
        description="desc",
        tags=["test"],
        playlist_id="PL_AUTO",
        auto_pin_comment=True,
        pin_comment_text="Pinned hook comment",
    )
    mock_service.playlistItems().insert.assert_called()
    mock_service.commentThreads().insert.assert_called()


def test_studio_playwright_related_video_and_schedule(dummy_video: Path):
    """Verify Studio uploader interacts with altered content, related video, and schedule picker."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.url = "https://studio.youtube.com"

    mock_loc = MagicMock()
    mock_loc.is_visible.return_value = True
    mock_loc.first = mock_loc
    mock_loc.nth.return_value = mock_loc
    mock_loc.get_attribute.return_value = "https://youtu.be/schedEdge11"
    mock_page.locator.return_value = mock_loc

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory)

    result = uploader.upload(
        video_path=dummy_video,
        title="Scheduled Edge Short",
        description="Studio automation schedule test",
        altered_content=True,
        related_video_url="https://youtube.com/watch?v=longVideo999",
        publish_at="2099-10-25T16:00:00Z",
    )

    assert result.video_id == "schedEdge11"
    assert result.privacy_status == "scheduled"
    assert result.raw_response["is_scheduled"] is True
    assert result.raw_response["altered_content"] is True
    assert result.raw_response["related_video_url"] == "https://youtube.com/watch?v=longVideo999"


def test_studio_playwright_diagnostic_screenshot_on_failure(
    dummy_video: Path, tmp_path: Path, monkeypatch
):
    """Verify diagnostic screenshot is captured when upload fails."""
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.goto.side_effect = RuntimeError("DOM timeout mock error")

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr("autoshorts.uploader.studio_playwright.get_data_dir", lambda: data_dir)

    factory = MagicMock(return_value=mock_ctx)
    uploader = PlaywrightStudioUploader(context_factory=factory)

    with pytest.raises(RuntimeError, match="DOM timeout mock error"):
        uploader.upload(video_path=dummy_video, title="Fail Short", description="Fail desc")

    mock_page.screenshot.assert_called_once()
    call_path = mock_page.screenshot.call_args[1]["path"]
    assert "studio_error_" in call_path


def test_generate_title_variants():
    """Verify generate_title_variants returns 3 <100-char variants with expected styles and #Shorts."""
    from autoshorts.uploader.metadata_builder import generate_title_variants

    variants = generate_title_variants(
        hook="Sleep deprivation <destroys> neural plasticity",
        channel="Huberman Lab",
        title="Master Your Sleep & Focus",
    )
    assert len(variants) == 3
    assert variants[0]["style"] == "curiosity"
    assert variants[0]["label"] == "Curiosity Gap"
    assert variants[1]["style"] == "contrarian"
    assert variants[1]["label"] == "Contrarian / Truth"
    assert variants[2]["style"] == "punchy"
    assert variants[2]["label"] == "Direct Viral Hook"

    for v in variants:
        t = v["title"]
        assert len(t) < 100
        assert "#shorts" in t.lower()
        assert "<" not in t and ">" not in t


def test_youtube_api_upload_thumbnail_success_and_non_fatal_failure(
    dummy_video: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """Verify upload_thumbnail succeeds when permitted and logs non-fatal warning on failure."""
    thumb_file = tmp_path / "thumb.jpg"
    thumb_file.write_bytes(b"\xff\xd8\xff\xe0JPEG")

    mock_service = MagicMock()
    mock_thumb_req = MagicMock()
    mock_thumb_req.execute.return_value = {"items": [{}]}
    mock_service.thumbnails().set.return_value = mock_thumb_req

    uploader = YouTubeApiUploader(youtube_service=mock_service)
    assert uploader.upload_thumbnail("vid_thumb_1", thumb_file) is True
    mock_service.thumbnails().set.assert_called_once()
    call_kwargs = mock_service.thumbnails().set.call_args[1]
    assert call_kwargs["videoId"] == "vid_thumb_1"

    # Non-fatal failure when channel lacks custom thumbnail permission
    mock_thumb_req.execute.side_effect = RuntimeError(
        "403 Forbidden: custom thumbnails not enabled"
    )
    with caplog.at_level(logging.WARNING):
        assert uploader.upload_thumbnail("vid_thumb_2", thumb_file) is False
        assert "Failed to upload custom thumbnail" in caplog.text

    # Also verify upload() calls upload_thumbnail when thumbnail_path is provided
    mock_insert_req = MagicMock()
    mock_insert_req.next_chunk.return_value = (
        None,
        {"id": "vid_with_thumb", "status": {"privacyStatus": "unlisted"}},
    )
    mock_service.videos().insert.return_value = mock_insert_req
    mock_thumb_req.execute.side_effect = None

    res = uploader.upload(
        path=dummy_video,
        title="Thumb Integration",
        description="desc",
        tags=["test"],
        thumbnail_path=thumb_file,
    )
    assert res.video_id == "vid_with_thumb"
    assert mock_service.thumbnails().set.call_args[1]["videoId"] == "vid_with_thumb"


# =============================================================================
# YouTube Data API v3 Audit & Compliance Tests (Task 1)
# =============================================================================


def test_youtube_api_scopes():
    """Verify all required OAuth scopes are registered."""
    from autoshorts.uploader.youtube_api import SCOPES

    assert "https://www.googleapis.com/auth/youtube.upload" in SCOPES
    assert "https://www.googleapis.com/auth/youtube.readonly" in SCOPES
    assert "https://www.googleapis.com/auth/youtube" in SCOPES
    assert "https://www.googleapis.com/auth/youtube.force-ssl" in SCOPES
    assert "https://www.googleapis.com/auth/yt-analytics.readonly" in SCOPES


def test_youtube_api_quota_cost_accounting(state_mgr: StateManager, tmp_path: Path):
    """Verify quota accounting for thumbnails.set (50), videos.list batching (1/batch), and search.list (100)."""
    from autoshorts.uploader.youtube_api import QUOTA_COSTS

    assert QUOTA_COSTS["videos.insert"] == 1600
    assert QUOTA_COSTS["videos.list"] == 1
    assert QUOTA_COSTS["search.list"] == 100
    assert QUOTA_COSTS["thumbnails.set"] == 50
    assert QUOTA_COSTS["playlistItems.insert"] == 50
    assert QUOTA_COSTS["commentThreads.insert"] == 50

    tracker = QuotaTracker(state_mgr)
    mock_service = MagicMock()

    # 1. Test upload_thumbnail records 50 units
    thumb_file = tmp_path / "quota_thumb.jpg"
    thumb_file.write_bytes(b"\xff\xd8\xff\xe0JPEG")
    uploader = YouTubeApiUploader(quota_tracker=tracker, youtube_service=mock_service)
    assert uploader.upload_thumbnail("vid_q1", thumb_file) is True
    assert tracker.get_general_spend() == 50

    # 2. Test batch_get_videos batches by 50 and records 1 unit per 50 IDs
    ids = [f"vid_{i:03d}" for i in range(65)]
    mock_list_req = MagicMock()
    mock_list_req.execute.return_value = {
        "items": [
            {
                "id": vid,
                "snippet": {"title": f"T_{vid}", "categoryId": "24", "tags": ["tag"]},
                "statistics": {"viewCount": "100", "likeCount": "10", "commentCount": "2"},
                "contentDetails": {"duration": "PT30S"},
            }
            for vid in ids[:50]
        ]
    }
    mock_service.videos().list.return_value = mock_list_req
    batch_res = uploader.batch_get_videos(ids)
    assert len(batch_res) == 50
    assert mock_service.videos().list.call_count == 2  # 65 IDs -> 2 batches
    # 50 previous + 2 units for videos.list = 52
    assert tracker.get_general_spend() == 52

    # 3. Test search records 100 units
    mock_search_req = MagicMock()
    mock_search_req.execute.return_value = {"items": [{"id": {"videoId": "found_1"}}]}
    mock_service.search().list.return_value = mock_search_req
    items = uploader.search("stoicism podcast", max_results=5)
    assert len(items) == 1
    # 52 previous + 100 units = 152
    assert tracker.get_general_spend() == 152


def test_youtube_api_snippet_and_status_bounds(dummy_video: Path):
    """Verify title <= 100 chars, description <= 5000 chars, tags <= 500 chars, categoryId, defaultLanguage, publishAt -> private, containsSyntheticMedia."""
    mock_service = MagicMock()
    mock_insert_req = MagicMock()
    mock_insert_req.next_chunk.return_value = (
        None,
        {"id": "bounds_vid_1", "status": {"privacyStatus": "private"}},
    )
    mock_service.videos().insert.return_value = mock_insert_req

    uploader = YouTubeApiUploader(youtube_service=mock_service)

    long_title = "A" * 120 + " #Shorts"
    long_desc = "D" * 6000
    long_tags = ["tag_" + "x" * 50 for _ in range(20)]  # > 1000 chars

    res = uploader.upload(
        path=dummy_video,
        title=long_title,
        description=long_desc,
        tags=long_tags,
        category_id="27",
        default_language="en",
        altered_content=True,
        publish_at="2026-12-01T10:00:00Z",
    )
    assert res.video_id == "bounds_vid_1"

    call_kwargs = mock_service.videos().insert.call_args[1]
    snippet = call_kwargs["body"]["snippet"]
    status = call_kwargs["body"]["status"]

    # Title <= 100 chars and contains #Shorts
    assert len(snippet["title"]) <= 100
    assert "#shorts" in snippet["title"].lower()

    # Description <= 5000 chars
    assert len(snippet["description"]) <= 5000

    # Tags <= 500 chars total length
    total_tag_len = sum(len(t) for t in snippet["tags"]) + max(0, len(snippet["tags"]) - 1)
    assert total_tag_len <= 500

    # Snippet category and language
    assert snippet["categoryId"] == "27"
    assert snippet["defaultLanguage"] == "en"

    # Status: publishAt requires privacyStatus == private
    assert status["privacyStatus"] == "private"
    assert status["publishAt"] == "2026-12-01T10:00:00Z"
    assert status["containsSyntheticMedia"] is True
    assert status["selfDeclaredMadeForKids"] is False


def test_youtube_api_resumable_permanent_client_error(dummy_video: Path):
    """Verify non-retryable 4xx client errors (e.g. 400 Bad Request) fail immediately without retrying."""
    import httplib2
    from googleapiclient.errors import HttpError

    mock_service = MagicMock()
    mock_insert_req = MagicMock()

    resp = httplib2.Response({"status": 400, "reason": "Bad Request"})
    http_err = HttpError(resp, b'{"error": {"message": "Invalid video snippet"}}')
    mock_insert_req.next_chunk.side_effect = http_err
    mock_service.videos().insert.return_value = mock_insert_req

    uploader = YouTubeApiUploader(youtube_service=mock_service, max_retries=5)

    with patch("time.sleep") as mock_sleep, pytest.raises(UploadFailedError) as exc_info:
        uploader.upload(path=dummy_video, title="Bad Request Test")

    # Must NOT retry on permanent 400 client error
    assert mock_sleep.call_count == 0
    assert "HTTP client error" in str(exc_info.value)


def test_youtube_api_invalid_grant_handling(dummy_video: Path):
    """Verify invalid_grant / revoked OAuth tokens raise CredentialsError immediately."""
    from autoshorts.uploader.youtube_api import CredentialsError

    mock_service = MagicMock()
    mock_insert_req = MagicMock()
    mock_insert_req.next_chunk.side_effect = Exception(
        '{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}'
    )
    mock_service.videos().insert.return_value = mock_insert_req

    uploader = YouTubeApiUploader(youtube_service=mock_service, max_retries=3)

    with pytest.raises(CredentialsError) as exc_info:
        uploader.upload(path=dummy_video, title="Invalid Grant Test")

    assert (
        "invalid_grant" in str(exc_info.value).lower() or "revoked" in str(exc_info.value).lower()
    )


def test_youtube_api_token_refresh_persisted(tmp_path: Path):
    """Verify that expired OAuth credentials are refreshed and persisted to token_path."""
    token_file = tmp_path / "token.json"
    token_file.write_text(
        '{"token": "old_token", "refresh_token": "valid_refresh"}', encoding="utf-8"
    )

    mock_creds = MagicMock()
    mock_creds.expired = True
    mock_creds.refresh_token = "valid_refresh"
    mock_creds.valid = True
    mock_creds.to_json.return_value = (
        '{"token": "new_refreshed_token", "refresh_token": "valid_refresh"}'
    )

    with patch("autoshorts.uploader.youtube_api._get_google_libraries") as mock_libs:
        mock_req_cls = MagicMock()
        mock_creds_cls = MagicMock()
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds
        mock_build = MagicMock()
        mock_libs.return_value = (
            mock_req_cls,
            mock_creds_cls,
            MagicMock(),
            mock_build,
            MagicMock(),
            MagicMock(),
        )

        uploader = YouTubeApiUploader(token_path=token_file)
        uploader.get_service()

        mock_creds.refresh.assert_called_once()
        assert (
            token_file.read_text(encoding="utf-8")
            == '{"token": "new_refreshed_token", "refresh_token": "valid_refresh"}'
        )


def test_build_upload_description_strips_brackets_and_caps_bytes():
    from autoshorts.uploader.youtube_api import build_upload_description

    assert build_upload_description("a <b> c") == "a b c"
    # 3-byte chars: 2000 chars = 6000 bytes, must be cut to <= 5000 bytes, never mid-character.
    out = build_upload_description("\u0928" * 2000)
    assert len(out.encode("utf-8")) <= 5000
    assert out and set(out) == {"\u0928"}
    assert build_upload_description(None) == ""


def test_long_title_is_bounded_and_sanitized(dummy_video: Path):
    from autoshorts.uploader.youtube_api import build_long_title

    assert build_long_title("") == "Untitled video"
    t = build_long_title("Word " * 50 + "<x>\n")
    assert len(t) <= 100 and "<" not in t and "\n" not in t

    mock_service = MagicMock()
    req = MagicMock()
    req.next_chunk.return_value = (
        None,
        {"id": "longvid1", "status": {"privacyStatus": "unlisted"}},
    )
    mock_service.videos().insert.return_value = req
    uploader = YouTubeApiUploader(youtube_service=mock_service)
    uploader.upload(path=dummy_video, title="L" * 300, description="d", is_short=False)
    body = mock_service.videos().insert.call_args[1]["body"]
    assert len(body["snippet"]["title"]) <= 100


def test_publish_at_in_past_is_ignored(dummy_video: Path):
    mock_service = MagicMock()
    req = MagicMock()
    req.next_chunk.return_value = (None, {"id": "pastvid01", "status": {"privacyStatus": "public"}})
    mock_service.videos().insert.return_value = req
    uploader = YouTubeApiUploader(youtube_service=mock_service)
    uploader.upload(
        path=dummy_video,
        title="T",
        description="d",
        privacy="public",
        publish_at="2020-01-01T00:00:00Z",
    )
    status = mock_service.videos().insert.call_args[1]["body"]["status"]
    assert "publishAt" not in status and status["privacyStatus"] == "public"


def test_local_export_verify_empty_id_is_false(tmp_path: Path, dummy_video: Path):
    exporter = LocalExportUploader(export_dir=tmp_path / "exp")
    res = exporter.upload(dummy_video, "t", "d", [])
    assert exporter.verify(res.video_id) is True
    assert exporter.verify("") is False
    assert LocalExportUploader(export_dir=tmp_path / "exp").verify("") is False


def test_studio_publish_click_failure_raises(dummy_video: Path):
    from autoshorts.uploader.studio_playwright import StudioStepError

    mock_ctx = MagicMock()
    page = MagicMock()
    page.url = "https://studio.youtube.com/channel/x"
    done = MagicMock()
    done.click.side_effect = RuntimeError("button disabled")
    done.is_visible.return_value = True

    def locator(sel, *a, **k):
        m = MagicMock()
        m.first = done if "#done-button" in sel else MagicMock()
        return m

    page.locator.side_effect = locator
    mock_ctx.pages = [page]
    uploader = PlaywrightStudioUploader(context_factory=lambda **kw: mock_ctx)
    with pytest.raises(StudioStepError, match="publish"):
        uploader.upload(path=dummy_video, title="T", description="d")
