"""Regression tests for the upload pipeline: metadata limits, quota handling, thumbnails,
token refresh, verifier polling bounds, local export filenames and Studio timeouts.

All Google API and Playwright objects are mocked; no network access is performed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from autoshorts.core.config import UploadConfig
from autoshorts.core.state_manager import StateManager
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.uploader.description_builder import DescriptionBuilder, truncate_on_word
from autoshorts.uploader.errors import QuotaExceededError
from autoshorts.uploader.local_exporter import (
    LocalExportUploader,
    _safe_stem,
    _unique_path,
)
from autoshorts.uploader.metadata_builder import _format_bounded_shorts_title
from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader
from autoshorts.uploader.verifier import YouTubeVerifier
from autoshorts.uploader.youtube_api import (
    THUMBNAIL_MAX_BYTES,
    CredentialsError,
    UploadFailedError,
    YouTubeApiUploader,
    build_upload_title,
)


@pytest.fixture
def dummy_video(tmp_path: Path) -> Path:
    video_file = tmp_path / "clip.mp4"
    video_file.write_bytes(b"\x00\x00\x00 ftypisom" + b"\x00" * 1024)
    return video_file


@pytest.fixture
def state_mgr(tmp_path: Path) -> StateManager:
    return StateManager(tmp_path / "state.db")


def _http_error(status: int, content: bytes) -> HttpError:
    resp = httplib2.Response({"status": status, "reason": "err"})
    return HttpError(resp, content)


_QUOTA_EXCEEDED = (
    b'{"error": {"code": 403, "errors": [{"reason": "quotaExceeded", '
    b'"message": "The request cannot be completed because you have exceeded your quota."}]}}'
)
_RATE_LIMIT = (
    b'{"error": {"code": 403, "errors": [{"reason": "userRateLimitExceeded", '
    b'"message": "User Rate Limit Exceeded"}]}}'
)


# -----------------------------------------------------------------------------
# Title / metadata limits
# -----------------------------------------------------------------------------


def test_build_upload_title_never_exceeds_100_chars():
    out = build_upload_title("word " * 60)
    assert len(out) <= 100
    assert out.endswith(" #Shorts")
    assert out.count("#Shorts") == 1
    assert "..." in out
    # Cut on a word boundary: the text before the ellipsis ends with a whole word.
    assert out[: out.index("...")].endswith("word")


def test_build_upload_title_strips_angle_brackets_and_newlines():
    out = build_upload_title("Best <b>clip</b>\never")
    assert "<" not in out and ">" not in out and "\n" not in out
    assert out == "Best bclip/b ever #Shorts"


def test_build_upload_title_empty_falls_back_to_non_empty():
    assert build_upload_title("") == "Short #Shorts"
    assert build_upload_title("   #shorts ") == "Short #Shorts"


def test_build_upload_title_keeps_single_trailing_shorts_tag():
    assert build_upload_title("Cool thing #Shorts") == "Cool thing #Shorts"


def test_metadata_title_truncates_on_word_boundary():
    out = _format_bounded_shorts_title("alpha beta " * 30)
    assert len(out) < 100
    assert out.endswith(" #Shorts")
    core = out[: -len(" #Shorts")]
    assert core.endswith("...")
    assert core[:-3].split()[-1] in ("alpha", "beta")


def test_metadata_title_empty_core_is_not_blank():
    out = _format_bounded_shorts_title("   ")
    assert out == "Short #Shorts"


def test_truncate_on_word_helper():
    assert truncate_on_word("hello big world", 9) == "hello big"
    assert truncate_on_word("short", 50) == "short"
    assert truncate_on_word("abcdefghijklmnop", 5) == "abcde"
    assert truncate_on_word("anything", 0) == ""


def test_description_builder_title_bad_template_does_not_crash():
    builder = DescriptionBuilder(UploadConfig())
    title = builder.build_title(hook="Hook", channel="Chan", title="", template="{unknown}")
    assert title and len(title) <= 100
    assert title.lower().endswith("#shorts")


def test_description_builder_title_empty_hook_has_fallback():
    builder = DescriptionBuilder(UploadConfig())
    title = builder.build_title(hook="", channel="", title="", template="{hook}")
    assert title == "Short #Shorts"


def test_description_builder_long_title_stays_within_limit():
    builder = DescriptionBuilder(UploadConfig())
    title = builder.build_title(hook="lorem ipsum " * 40, channel="Channel")
    assert len(title) <= 100
    assert title.lower().endswith("#shorts")


def test_description_builder_long_hook_truncates_on_word_boundary():
    builder = DescriptionBuilder(UploadConfig())
    desc = builder.build_description(
        title="Source",
        channel="Chan",
        channel_url="https://example.com/chan",
        video_url="https://example.com/video",
        hook="abc " * 2000,
    )
    assert len(desc.encode("utf-8")) <= 5000
    assert "ORIGINAL SOURCE ATTRIBUTION" in desc
    assert desc.split("...", 1)[0].endswith("abc")


# -----------------------------------------------------------------------------
# Quota handling
# -----------------------------------------------------------------------------


def test_quota_exceeded_is_recorded_and_raised_without_retry(
    dummy_video: Path, state_mgr: StateManager
):
    tracker = QuotaTracker(state_mgr)
    service = MagicMock()
    req = MagicMock()
    req.next_chunk.side_effect = _http_error(403, _QUOTA_EXCEEDED)
    service.videos().insert.return_value = req

    uploader = YouTubeApiUploader(quota_tracker=tracker, youtube_service=service, max_retries=5)
    with patch("time.sleep") as mock_sleep, pytest.raises(QuotaExceededError):
        uploader.upload(path=dummy_video, title="Quota test")

    assert mock_sleep.call_count == 0
    # The exhaustion is recorded so the scheduler's throttle and fallback see it.
    assert tracker.get_upload_spend() == tracker.upload_bucket_daily
    assert tracker.can_upload() is False


def test_user_rate_limit_is_retried_not_treated_as_quota(
    dummy_video: Path, state_mgr: StateManager
):
    tracker = QuotaTracker(state_mgr)
    service = MagicMock()
    req = MagicMock()
    req.next_chunk.side_effect = [
        _http_error(403, _RATE_LIMIT),
        (None, {"id": "vid_rl", "status": {"privacyStatus": "unlisted"}}),
    ]
    service.videos().insert.return_value = req

    uploader = YouTubeApiUploader(quota_tracker=tracker, youtube_service=service, max_retries=3)
    with patch("time.sleep") as mock_sleep:
        res = uploader.upload(path=dummy_video, title="Rate limit")

    assert res.video_id == "vid_rl"
    assert mock_sleep.call_count == 1
    assert tracker.get_upload_spend() == 1  # only the successful insert


def test_permanent_403_still_fails_without_retry(dummy_video: Path):
    service = MagicMock()
    req = MagicMock()
    req.next_chunk.side_effect = _http_error(
        403, b'{"error": {"errors": [{"reason": "forbidden"}]}}'
    )
    service.videos().insert.return_value = req

    uploader = YouTubeApiUploader(youtube_service=service, max_retries=5)
    with patch("time.sleep") as mock_sleep, pytest.raises(UploadFailedError):
        uploader.upload(path=dummy_video, title="Forbidden")
    assert mock_sleep.call_count == 0


# -----------------------------------------------------------------------------
# Token handling
# -----------------------------------------------------------------------------


def _google_libs(creds_cls: MagicMock, flow_cls: MagicMock) -> tuple:
    return (MagicMock(), creds_cls, flow_cls, MagicMock(), MagicMock(), MagicMock())


def test_token_refresh_failure_raises_instead_of_interactive_flow(tmp_path: Path):
    token_file = tmp_path / "token.json"
    token_file.write_text('{"refresh_token": "r"}', encoding="utf-8")
    creds = MagicMock()
    creds.expired = True
    creds.refresh_token = "r"
    creds.refresh.side_effect = Exception("network unreachable")
    creds_cls = MagicMock()
    creds_cls.from_authorized_user_file.return_value = creds
    flow_cls = MagicMock()

    with patch(
        "autoshorts.uploader.youtube_api._get_google_libraries",
        return_value=_google_libs(creds_cls, flow_cls),
    ):
        uploader = YouTubeApiUploader(
            token_path=token_file, client_secret_path=tmp_path / "client_secret.json"
        )
        with pytest.raises(CredentialsError):
            uploader.get_service()

    flow_cls.from_client_secrets_file.assert_not_called()


def test_missing_client_secret_gives_clear_error(tmp_path: Path):
    creds_cls = MagicMock()
    creds_cls.from_authorized_user_file.side_effect = FileNotFoundError("no token")
    flow_cls = MagicMock()
    with patch(
        "autoshorts.uploader.youtube_api._get_google_libraries",
        return_value=_google_libs(creds_cls, flow_cls),
    ):
        uploader = YouTubeApiUploader(
            token_path=tmp_path / "missing_token.json",
            client_secret_path=tmp_path / "client_secret.json",
        )
        with pytest.raises(CredentialsError, match="client_secret.json"):
            uploader.get_service()
    flow_cls.from_client_secrets_file.assert_not_called()


def test_unusable_token_without_refresh_never_starts_interactive_flow(tmp_path: Path):
    """A stored token that is invalid and has no refresh_token must not open a browser flow.

    Regression: get_service() used to fall through to InstalledAppFlow.run_local_server()
    when the token was invalid and client_secret.json existed, blocking unattended runs.
    """
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "old"}', encoding="utf-8")
    client_secret = tmp_path / "client_secret.json"
    client_secret.write_text("{}", encoding="utf-8")
    creds = MagicMock()
    creds.expired = True
    creds.valid = False
    creds.refresh_token = None
    creds_cls = MagicMock()
    creds_cls.from_authorized_user_file.return_value = creds
    flow_cls = MagicMock()
    with patch(
        "autoshorts.uploader.youtube_api._get_google_libraries",
        return_value=_google_libs(creds_cls, flow_cls),
    ):
        uploader = YouTubeApiUploader(token_path=token_file, client_secret_path=client_secret)
        with pytest.raises(CredentialsError, match="Re-authorize"):
            uploader.get_service()
    flow_cls.from_client_secrets_file.assert_not_called()


# -----------------------------------------------------------------------------
# Thumbnails
# -----------------------------------------------------------------------------


def test_thumbnail_over_2mb_is_rejected(tmp_path: Path):
    thumb = tmp_path / "big.jpg"
    thumb.write_bytes(b"\xff\xd8\xff" + b"0" * (THUMBNAIL_MAX_BYTES + 1))
    service = MagicMock()
    assert YouTubeApiUploader(youtube_service=service).upload_thumbnail("vid", thumb) is False
    service.thumbnails.assert_not_called()


def test_thumbnail_unsupported_format_is_rejected(tmp_path: Path):
    thumb = tmp_path / "anim.gif"
    thumb.write_bytes(b"GIF89a" + b"\x00" * 32)
    service = MagicMock()
    assert YouTubeApiUploader(youtube_service=service).upload_thumbnail("vid", thumb) is False
    service.thumbnails.assert_not_called()


def test_thumbnail_png_is_sent_with_png_mimetype(tmp_path: Path):
    thumb = tmp_path / "t.png"
    thumb.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    service = MagicMock()
    service.thumbnails().set.return_value = MagicMock()
    assert YouTubeApiUploader(youtube_service=service).upload_thumbnail("vid_png", thumb) is True
    media = service.thumbnails().set.call_args[1]["media_body"]
    assert media.mimetype() == "image/png"


def test_thumbnail_jpeg_is_sent_with_jpeg_mimetype(tmp_path: Path):
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    service = MagicMock()
    service.thumbnails().set.return_value = MagicMock()
    assert YouTubeApiUploader(youtube_service=service).upload_thumbnail("vid_jpg", thumb) is True
    media = service.thumbnails().set.call_args[1]["media_body"]
    assert media.mimetype() == "image/jpeg"


# -----------------------------------------------------------------------------
# Verifier bounds
# -----------------------------------------------------------------------------


def test_verifier_zero_retries_still_checks_once():
    uploader = MagicMock()
    uploader.verify.return_value = True
    assert YouTubeVerifier(uploader).poll_until_verified("vid", max_retries=0, delay_sec=0) is True
    uploader.verify.assert_called_once_with("vid")


def test_verifier_polling_is_bounded():
    uploader = MagicMock()
    uploader.verify.return_value = False
    with patch("time.sleep"):
        assert (
            YouTubeVerifier(uploader).poll_until_verified("vid", max_retries=3, delay_sec=0)
            is False
        )
    assert uploader.verify.call_count == 3


# -----------------------------------------------------------------------------
# Local exporter
# -----------------------------------------------------------------------------


def test_safe_stem_handles_windows_reserved_and_trailing_dots():
    assert _safe_stem("CON") == "_CON"
    assert _safe_stem("nul.txt") == "_nul.txt"
    assert _safe_stem("COM1") == "_COM1"
    assert _safe_stem("video. ") == "video"
    assert _safe_stem("a<b>c:d") == "a_b_c_d"
    assert _safe_stem("...") == "video"
    assert _safe_stem("") == "video"


def test_unique_path_suffixes_instead_of_overwriting(tmp_path: Path):
    existing = tmp_path / "x.mp4"
    existing.write_bytes(b"a")
    assert _unique_path(existing) == tmp_path / "x (2).mp4"
    (tmp_path / "x (2).mp4").write_bytes(b"b")
    assert _unique_path(existing) == tmp_path / "x (3).mp4"
    fresh = tmp_path / "fresh.mp4"
    assert _unique_path(fresh) == fresh


def test_local_export_uses_proper_file_uri_and_safe_names(dummy_video: Path, tmp_path: Path):
    exporter = LocalExportUploader(export_dir=tmp_path / "export")
    res = exporter.upload(path=dummy_video, title="t", description="d", tags=[])
    export_path = Path(res.raw_response["export_path"])
    assert res.url == export_path.as_uri()
    assert res.url.startswith("file:///")
    assert export_path.is_file()
    assert export_path.parent == (tmp_path / "export").resolve()


# -----------------------------------------------------------------------------
# Studio Playwright timeouts
# -----------------------------------------------------------------------------


def test_studio_upload_uses_explicit_timeouts(dummy_video: Path):
    mock_ctx = MagicMock()
    mock_page = MagicMock()
    mock_ctx.pages = [mock_page]
    mock_page.url = "https://studio.youtube.com"
    mock_loc = MagicMock()
    mock_loc.is_visible.return_value = True
    mock_loc.first = mock_loc
    mock_loc.nth.return_value = mock_loc
    mock_loc.get_attribute.return_value = "https://youtu.be/edgeVid5678"
    mock_page.locator.return_value = mock_loc

    uploader = PlaywrightStudioUploader(context_factory=MagicMock(return_value=mock_ctx))
    uploader.upload(video_path=dummy_video, title="Timeout check", privacy_status="unlisted")

    goto_kwargs = mock_page.goto.call_args.kwargs
    assert isinstance(goto_kwargs.get("timeout"), int) and goto_kwargs["timeout"] > 0
    attach_kwargs = mock_loc.set_input_files.call_args.kwargs
    assert isinstance(attach_kwargs.get("timeout"), int) and attach_kwargs["timeout"] > 0
    mock_ctx.close.assert_called()
