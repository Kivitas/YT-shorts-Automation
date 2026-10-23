"""Typed upload errors, HTTP classification, and verify() privacy/processing semantics.

All Google API and network calls are mocked; no real YouTube requests are made.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from autoshorts.core.exceptions import UploadError
from autoshorts.uploader.errors import (
    AuthError,
    PermanentUploadError,
    ProcessingError,
    QuotaExceededError,
    RateLimitError,
    TransientUploadError,
    classify_google_error,
)
from autoshorts.uploader.verifier import YouTubeVerifier
from autoshorts.uploader.youtube_api import (
    CredentialsError,
    UploadFailedError,
    YouTubeApiUploader,
)


def _http_error(status: int, content: bytes = b"") -> HttpError:
    return HttpError(httplib2.Response({"status": status, "reason": "err"}), content)


def _reason_body(code: int, reason: str) -> bytes:
    return (
        f'{{"error": {{"code": {code}, "errors": [{{"reason": "{reason}", "message": "msg"}}]}}}}'
    ).encode()


@pytest.fixture
def dummy_video(tmp_path: Path) -> Path:
    video_file = tmp_path / "short.mp4"
    video_file.write_bytes(b"\x00\x00\x00 ftypisom" + b"\x00" * 1024)
    return video_file


# -----------------------------------------------------------------------------
# Hierarchy
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_cls",
    [
        AuthError,
        QuotaExceededError,
        RateLimitError,
        PermanentUploadError,
        TransientUploadError,
        ProcessingError,
    ],
)
def test_every_upload_error_derives_from_upload_error(error_cls: type[Exception]):
    assert issubclass(error_cls, UploadError)


def test_existing_names_keep_working_as_subclasses():
    assert issubclass(CredentialsError, AuthError)
    assert issubclass(UploadFailedError, PermanentUploadError)


# -----------------------------------------------------------------------------
# Classification
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_http_error(403, _reason_body(403, "quotaExceeded")), QuotaExceededError),
        (_http_error(403, _reason_body(403, "uploadLimitExceeded")), QuotaExceededError),
        (_http_error(403, _reason_body(403, "userRateLimitExceeded")), RateLimitError),
        (_http_error(429), RateLimitError),
        (_http_error(401), AuthError),
        (_http_error(403, _reason_body(403, "forbidden")), PermanentUploadError),
        (_http_error(400, b'{"error": {"message": "Invalid title"}}'), PermanentUploadError),
        (_http_error(404), PermanentUploadError),
        (_http_error(503, b""), TransientUploadError),
        (_http_error(500), TransientUploadError),
        (ConnectionError("reset by peer"), TransientUploadError),
        (TimeoutError("timed out"), TransientUploadError),
        (Exception('{"error": "invalid_grant"}'), AuthError),
    ],
)
def test_classify_google_error(error: BaseException, expected: type[Exception]):
    assert type(classify_google_error(error)) is expected


def test_classify_does_not_treat_arbitrary_403_text_as_quota():
    """A 403 without a quota reason must not be reported as quota exhaustion."""
    typed = classify_google_error(_http_error(403, b"forbidden"))
    assert not isinstance(typed, QuotaExceededError)


# -----------------------------------------------------------------------------
# Upload chunk loop
# -----------------------------------------------------------------------------


def test_upload_quota_reason_raises_quota_exceeded_without_retry(dummy_video: Path):
    service = MagicMock()
    req = MagicMock()
    req.next_chunk.side_effect = _http_error(403, _reason_body(403, "quotaExceeded"))
    service.videos().insert.return_value = req

    uploader = YouTubeApiUploader(youtube_service=service, max_retries=5)
    with patch("time.sleep") as mock_sleep, pytest.raises(QuotaExceededError):
        uploader.upload(path=dummy_video, title="Quota")
    assert mock_sleep.call_count == 0
    assert req.next_chunk.call_count == 1


def test_upload_transient_5xx_is_retried_then_succeeds(dummy_video: Path):
    service = MagicMock()
    req = MagicMock()
    req.next_chunk.side_effect = [
        _http_error(503),
        (None, {"id": "vid503", "status": {"privacyStatus": "unlisted"}}),
    ]
    service.videos().insert.return_value = req

    uploader = YouTubeApiUploader(youtube_service=service, max_retries=3)
    with patch("time.sleep") as mock_sleep:
        res = uploader.upload(path=dummy_video, title="Flaky")
    assert res.video_id == "vid503"
    assert mock_sleep.call_count == 1


def test_upload_auth_error_raises_credentials_error(dummy_video: Path):
    service = MagicMock()
    req = MagicMock()
    req.next_chunk.side_effect = _http_error(401, b'{"error": "unauthorized"}')
    service.videos().insert.return_value = req

    uploader = YouTubeApiUploader(youtube_service=service, max_retries=3)
    with patch("time.sleep") as mock_sleep, pytest.raises(CredentialsError):
        uploader.upload(path=dummy_video, title="Auth")
    assert mock_sleep.call_count == 0


# -----------------------------------------------------------------------------
# verify(): privacy and processing outcomes
# -----------------------------------------------------------------------------


def _list_service(*responses: object) -> tuple[MagicMock, MagicMock]:
    """Return (service, list_request) whose videos().list().execute() yields responses in order."""
    service = MagicMock()
    req = MagicMock()
    req.execute.side_effect = list(responses)
    service.videos().list.return_value = req
    return service, req


def _video_item(
    *,
    upload: str = "uploaded",
    processing: str = "succeeded",
    privacy: str = "unlisted",
) -> dict:
    return {
        "items": [
            {
                "id": "vidX",
                "status": {"uploadStatus": upload, "privacyStatus": privacy},
                "processingDetails": {"processingStatus": processing},
            }
        ]
    }


def test_verify_privacy_match_passes():
    service, _ = _list_service(_video_item(privacy="unlisted"))
    uploader = YouTubeApiUploader(youtube_service=service)
    assert uploader.verify("vidX", expected_privacy="unlisted") is True
    assert uploader.last_verification_kind == "api_confirmed"


def test_verify_uses_privacy_recorded_at_upload(dummy_video: Path):
    service = MagicMock()
    insert_req = MagicMock()
    insert_req.next_chunk.return_value = (
        None,
        {"id": "vidX", "status": {"privacyStatus": "unlisted"}},
    )
    service.videos().insert.return_value = insert_req
    list_req = MagicMock()
    list_req.execute.return_value = _video_item(privacy="private")
    service.videos().list.return_value = list_req

    uploader = YouTubeApiUploader(youtube_service=service)
    uploader.upload(path=dummy_video, title="Recorded", privacy="unlisted")
    with pytest.raises(ProcessingError, match="privacy mismatch"):
        uploader.verify("vidX")


def test_verify_privacy_mismatch_raises():
    service, _ = _list_service(_video_item(privacy="private"))
    uploader = YouTubeApiUploader(youtube_service=service)
    with pytest.raises(ProcessingError, match="requested 'public'.*'private'"):
        uploader.verify("vidX", expected_privacy="public")


def test_processing_failed_stops_polling_immediately():
    service, req = _list_service(
        {
            "items": [
                {
                    "id": "vidX",
                    "status": {"uploadStatus": "uploaded", "privacyStatus": "unlisted"},
                    "processingDetails": {
                        "processingStatus": "failed",
                        "processingFailureReason": "codec",
                    },
                }
            ]
        },
        _video_item(),
    )
    verifier = YouTubeVerifier(YouTubeApiUploader(youtube_service=service))
    with patch("time.sleep") as mock_sleep, pytest.raises(ProcessingError, match="codec"):
        verifier.poll_until_verified("vidX", max_retries=5, delay_sec=1.0)
    assert req.execute.call_count == 1
    assert mock_sleep.call_count == 0


def test_rejected_upload_status_is_definitive_failure():
    service, req = _list_service(
        {
            "items": [
                {
                    "id": "vidX",
                    "status": {"uploadStatus": "rejected", "rejectionReason": "copyright"},
                    "processingDetails": {"processingStatus": "processing"},
                }
            ]
        }
    )
    verifier = YouTubeVerifier(YouTubeApiUploader(youtube_service=service))
    with patch("time.sleep"), pytest.raises(ProcessingError, match="copyright"):
        verifier.poll_until_verified("vidX", max_retries=5, delay_sec=0)
    assert req.execute.call_count == 1


def test_verify_transient_5xx_is_retried_then_succeeds():
    service, req = _list_service(_http_error(503), _video_item())
    verifier = YouTubeVerifier(YouTubeApiUploader(youtube_service=service))
    with patch("time.sleep"):
        assert verifier.poll_until_verified("vidX", max_retries=3, delay_sec=0) is True
    assert req.execute.call_count == 2


def test_verify_permanent_error_is_not_retried():
    service, req = _list_service(_http_error(400, b'{"error": {"message": "bad id"}}'))
    verifier = YouTubeVerifier(YouTubeApiUploader(youtube_service=service))
    with patch("time.sleep") as mock_sleep, pytest.raises(PermanentUploadError):
        verifier.poll_until_verified("vidX", max_retries=5, delay_sec=0)
    assert req.execute.call_count == 1
    assert mock_sleep.call_count == 0
