"""Unit tests for YouTube API uploader, chunked resumable protocol, verifier, and local exporter."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoshorts.core.contracts import Uploader
from autoshorts.core.state_manager import StateManager
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.uploader.local_exporter import LocalExportUploader
from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader
from autoshorts.uploader.verifier import YouTubeVerifier
from autoshorts.uploader.youtube_api import (
    UploadFailedError,
    YouTubeApiUploader,
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


def test_studio_playwright_stub_behavior():
    """Test PlaywrightStudioUploader raises NotImplementedError with clear documentation."""
    studio = PlaywrightStudioUploader()
    with pytest.raises(NotImplementedError) as exc_info:
        studio.upload(
            path=Path("dummy.mp4"),
            title="Studio Short",
            description="desc",
            tags=[],
        )
    assert "architectural stub" in str(exc_info.value)

    with pytest.raises(NotImplementedError):
        studio.verify("any_id")
