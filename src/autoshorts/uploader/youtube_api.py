"""YouTube Data API v3 uploader implementing resumable video upload and verification.

Implements contracts.Uploader via chunked resumable upload with retry/backoff,
OAuth Desktop credentials handling, and post-upload processing verification (PLAN.md section 8.1 & 8.4).
"""

from __future__ import annotations

import logging
import random
import re
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoshorts.core.config import Settings, UploadConfig
from autoshorts.core.credential_guardian import classify_api_error
from autoshorts.core.exceptions import QuotaExhaustedError
from autoshorts.core.models import UploadResult
from autoshorts.core.paths import atomic_write, get_data_dir
from autoshorts.core.resilience import classify_network_or_outage_error
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.uploader.description_builder import truncate_on_word
from autoshorts.uploader.errors import (
    AuthError,
    PermanentUploadError,
    ProcessingError,
    QuotaExceededError,
    RateLimitError,
    TransientUploadError,
    UploadError,
    classify_google_error,
)

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS: int = 100
MAX_DESCRIPTION_CHARS: int = 5000
MAX_TAGS_CHARS: int = 500
THUMBNAIL_MAX_BYTES: int = 2 * 1024 * 1024
_SHORTS_SUFFIX: str = " #Shorts"

SCOPES: list[str] = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

QUOTA_COSTS: dict[str, int] = {
    "videos.insert": 1600,
    "videos.list": 1,
    "search.list": 100,
    "thumbnails.set": 50,
    "playlistItems.insert": 50,
    "commentThreads.insert": 50,
}
DAILY_QUOTA_CAP: int = 10000


class CredentialsError(AuthError):
    """Raised when OAuth credentials cannot be loaded or refreshed. Not retryable."""


class UploadFailedError(PermanentUploadError):
    """Raised when an upload fails permanently: a non-retryable 4xx, or retries exhausted."""


def build_compliant_tags(
    base_tags: Sequence[str] | None,
    custom_tags: Sequence[str] | None = None,
) -> list[str]:
    """Merge and sanitize tags for YouTube API compliance.

    Deduplicates tags while preserving order, strips '<' and '>', ensures '#Shorts' / 'Shorts'
    tag presence, and enforces YouTube API's 500-character combined length cap.
    """
    raw_list: list[str] = []
    if base_tags:
        raw_list.extend(base_tags)
    if custom_tags:
        raw_list.extend(custom_tags)

    cleaned_tags: list[str] = []
    seen: set[str] = set()
    has_shorts = False

    for t in raw_list:
        if not t:
            continue
        cleaned = str(t).replace("<", "").replace(">", "").strip()
        if not cleaned:
            continue
        low = cleaned.lower()
        if low in seen:
            continue
        seen.add(low)
        if low in ("shorts", "#shorts"):
            has_shorts = True
        cleaned_tags.append(cleaned)

    if not has_shorts:
        cleaned_tags.append("Shorts")

    # Enforce YouTube API's 500-character combined length cap (tags separated by commas)
    compliant: list[str] = []
    current_len = 0
    shorts_tag = "Shorts"

    for tag in cleaned_tags:
        cost = len(tag) + (1 if compliant else 0)
        if current_len + cost <= 500:
            compliant.append(tag)
            current_len += cost
        else:
            continue

    # Ensure Shorts is retained even if list was truncated
    if not any(t.lower() in ("shorts", "#shorts") for t in compliant):
        while compliant and (current_len + len(shorts_tag) + 1 > 500):
            removed = compliant.pop()
            current_len -= len(removed) + 1
        compliant.append(shorts_tag)

    return compliant


def build_upload_title(title: str) -> str:
    """Return a YouTube-valid Shorts title.

    Guarantees: at most 100 characters (including the ' #Shorts' tag), no '<' or '>',
    no control characters (YouTube rejects newlines in titles), exactly one trailing
    '#Shorts', and never an empty title (falls back to 'Short').
    """
    cleaned = str(title or "").replace("<", "").replace(">", "")
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"(?i)\s*#shorts\s*$", "", cleaned).strip()
    if not cleaned:
        cleaned = "Short"

    max_core = MAX_TITLE_CHARS - len(_SHORTS_SUFFIX)
    if len(cleaned) > max_core:
        head = truncate_on_word(cleaned, max_core - 3).rstrip(" -|:,.!?")
        cleaned = f"{head or cleaned[: max_core - 3]}..."
    return f"{cleaned}{_SHORTS_SUFFIX}"


def _read_thumbnail_mimetype(path: Path) -> str | None:
    """Return 'image/jpeg' or 'image/png' by magic bytes, or None if neither."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def _get_google_libraries() -> tuple[Any, Any, Any, Any, Any, Any]:
    """Lazy import Google API client libraries."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload

        return Request, Credentials, InstalledAppFlow, build, HttpError, MediaFileUpload
    except ImportError as e:
        raise ImportError(
            f"Google API libraries not installed. Run 'uv pip install google-api-python-client google-auth-oauthlib': {e}"
        ) from e


class YouTubeApiUploader:
    """Uploader implementing YouTube Data API v3 chunked resumable protocol."""

    def __init__(
        self,
        config: Settings | None = None,
        upload_config: UploadConfig | None = None,
        quota_tracker: QuotaTracker | None = None,
        client_secret_path: Path | str | None = None,
        token_path: Path | str | None = None,
        youtube_service: Any = None,
        category_id: str = "24",
        chunk_size_bytes: int = 1024 * 1024,  # 1 MB chunks
        max_retries: int = 5,
    ) -> None:
        """Initialize YouTube API uploader.

        Args:
            config: Full application Settings (optional).
            upload_config: Dedicated UploadConfig (optional).
            quota_tracker: QuotaTracker for recording unit consumption (optional).
            client_secret_path: Path to client_secret.json (defaults to data/client_secret.json).
            token_path: Path to token.json (defaults to data/token.json).
            youtube_service: Injected mock/custom YouTube service resource (for testing).
            category_id: YouTube video category ID (default '24' Entertainment).
            chunk_size_bytes: Resumable chunk size in bytes (multiple of 256KB).
            max_retries: Maximum resumable upload chunk retry attempts.
        """
        self.config = upload_config or (config.upload if config else UploadConfig())
        self.quota_tracker = quota_tracker
        self.category_id = category_id
        self.chunk_size_bytes = chunk_size_bytes
        self.max_retries = max_retries

        data_dir = get_data_dir()
        self.client_secret_path = (
            Path(client_secret_path) if client_secret_path else (data_dir / "client_secret.json")
        )
        self.token_path = Path(token_path) if token_path else (data_dir / "token.json")

        self._service = youtube_service
        # Set by verify(); the pipeline reads this to label the verification strength.
        self.last_verification_kind: str = "unverified"
        # Privacy this uploader requested per video ID, so verify() can detect a mismatch.
        self._requested_privacy: dict[str, str] = {}

    def get_service(self) -> Any:
        """Return or lazily initialize the authenticated YouTube service."""
        if self._service is not None:
            return self._service

        Request, Credentials, _installed_app_flow, build, _, _ = _get_google_libraries()

        creds = None
        if self.token_path.is_file():
            try:
                # Use the scopes the token was granted: refreshing with more scopes than were
                # granted fails with invalid_scope.
                creds = Credentials.from_authorized_user_file(str(self.token_path))
            except Exception as e:
                logger.warning(f"Failed to read existing token file: {e}")

        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired YouTube OAuth access token...")
            try:
                creds.refresh(Request())
                # Persist refreshed token without logging credentials
                self.token_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(self.token_path, creds.to_json(), encoding="utf-8")
                logger.info("Successfully refreshed and saved OAuth token.")
            except Exception as e:
                report = classify_api_error("youtube_oauth", e)
                logger.error(f"Failed to refresh OAuth token: {report.message} ({e})")
                # Never fall through to the interactive browser OAuth flow here: an
                # unattended pipeline would block on it. Surface a clear error instead.
                raise CredentialsError(
                    f"YouTube OAuth token refresh failed ({report.status}): {report.message} "
                    f"Re-authorize via Settings or check network access."
                ) from e

        if not creds or not creds.valid:
            # Never start the interactive browser flow from the uploader: the pipeline and
            # scheduler run unattended and would block on it. Authorization happens only
            # through the Settings flow (settings_manager), which the operator starts.
            if creds is not None:
                raise CredentialsError(
                    "YouTube OAuth token is invalid and cannot be refreshed. "
                    "Re-authorize via Settings."
                )
            if not self.client_secret_path.is_file():
                raise CredentialsError(
                    f"OAuth credentials missing. Place client_secret.json in '{self.client_secret_path}'."
                )
            raise CredentialsError(
                "No YouTube OAuth token found. Authorize via Settings to create token.json."
            )

        self._service = build("youtube", "v3", credentials=creds, cache_discovery=False)
        return self._service

    # -------------------------------------------------------------------------
    # Upload Protocol Implementation
    # -------------------------------------------------------------------------

    def _mark_upload_quota_exhausted(self) -> None:
        """Record a 403 quotaExceeded in the tracker by consuming today's remaining upload budget.

        This makes can_upload() / is_throttled() report the exhaustion, so the scheduler
        stops sending uploads and the configured fallback takes over.
        """
        if self.quota_tracker is None:
            return
        try:
            remaining = int(self.quota_tracker.get_upload_remaining())
            if remaining > 0:
                self.quota_tracker.record_upload(remaining)
        except Exception as e:
            logger.warning("Could not record upload quota exhaustion in tracker: %s", e)

    def add_to_playlist(self, video_id: str, playlist_id: str) -> bool:
        """Add an uploaded video to a YouTube playlist via playlistItems.insert.

        Non-fatal: logs a warning on error and returns False.
        """
        if not video_id or not playlist_id:
            return False
        try:
            service = self.get_service()
            body = {
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                }
            }
            req = service.playlistItems().insert(part="snippet", body=body)
            req.execute()
            if self.quota_tracker:
                self.quota_tracker.record_general(50)
            logger.info("Video %s successfully added to playlist %s.", video_id, playlist_id)
            return True
        except Exception as e:
            logger.warning("Failed to add video %s to playlist %s: %s", video_id, playlist_id, e)
            return False

    def post_comment(self, video_id: str, comment_text: str) -> str | None:
        """Post a top-level comment to an uploaded video via commentThreads.insert.

        Non-fatal: logs a warning on error and returns None.
        """
        if not video_id or not comment_text:
            return None
        try:
            service = self.get_service()
            body = {
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {
                            "textOriginal": comment_text,
                        }
                    },
                }
            }
            req = service.commentThreads().insert(part="snippet", body=body)
            resp = req.execute()
            comment_id = resp.get("id") if isinstance(resp, dict) else None
            if self.quota_tracker:
                self.quota_tracker.record_general(50)
            logger.info("Comment successfully posted to video %s (id: %s).", video_id, comment_id)
            return comment_id
        except Exception as e:
            logger.warning("Failed to post comment to video %s: %s", video_id, e)
            return None

    def upload_thumbnail(self, video_id: str, thumbnail_path: Path) -> bool:
        """Upload a custom JPEG thumbnail for a video via thumbnails.set.

        Consumes 50 units from general quota bucket.
        Wrapped in non-fatal try/except so unverified channels without custom thumbnail
        permission simply log a warning and never fail the video upload.
        """
        if not video_id or not thumbnail_path:
            return False
        thumb_file = Path(thumbnail_path)
        if not thumb_file.is_file():
            logger.warning("Thumbnail file does not exist for video %s: %s", video_id, thumb_file)
            return False
        # thumbnails.set accepts only JPEG/PNG up to 2 MB; reject anything else up front.
        try:
            size_bytes = thumb_file.stat().st_size
        except OSError as e:
            logger.warning("Cannot stat thumbnail %s for video %s: %s", thumb_file, video_id, e)
            return False
        if size_bytes > THUMBNAIL_MAX_BYTES:
            logger.warning(
                "Thumbnail %s is %d bytes; YouTube limit is %d. Skipping thumbnail for video %s.",
                thumb_file,
                size_bytes,
                THUMBNAIL_MAX_BYTES,
                video_id,
            )
            return False
        mimetype = _read_thumbnail_mimetype(thumb_file)
        if mimetype is None:
            logger.warning(
                "Thumbnail %s is not a JPEG or PNG file; skipping thumbnail for video %s.",
                thumb_file,
                video_id,
            )
            return False
        try:
            service = self.get_service()
            _, _, _, _, _, MediaFileUpload = _get_google_libraries()
            req = service.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumb_file), mimetype=mimetype),
            )
            req.execute()
            if self.quota_tracker:
                self.quota_tracker.record_general(QUOTA_COSTS["thumbnails.set"])
            logger.info("Custom thumbnail uploaded for video %s (%s).", video_id, thumb_file)
            return True
        except Exception as e:
            typed = classify_google_error(e)
            logger.warning(
                "Failed to upload custom thumbnail for video %s (%s): %s",
                video_id,
                type(typed).__name__,
                e,
            )
            return False

    def batch_get_videos(self, video_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch statistics, snippet, and contentDetails for up to 50 videos per call via videos.list.

        Consumes 1 general quota unit per batch of up to 50 video IDs.
        """
        results: dict[str, dict[str, Any]] = {}
        if not video_ids:
            return results

        service = self.get_service()
        unique_ids = list(dict.fromkeys(video_ids))

        for i in range(0, len(unique_ids), 50):
            batch = unique_ids[i : i + 50]
            try:
                req = service.videos().list(
                    part="snippet,statistics,contentDetails",
                    id=",".join(batch),
                )
                resp = req.execute()
                if self.quota_tracker:
                    self.quota_tracker.record_general(QUOTA_COSTS["videos.list"])

                for item in resp.get("items", []):
                    vid = item.get("id")
                    if not vid:
                        continue
                    snippet = item.get("snippet", {})
                    stats = item.get("statistics", {})
                    details = item.get("contentDetails", {})
                    results[vid] = {
                        "id": vid,
                        "title": snippet.get("title", ""),
                        "description": snippet.get("description", ""),
                        "categoryId": snippet.get("categoryId", ""),
                        "tags": snippet.get("tags", []),
                        "viewCount": int(stats.get("viewCount", 0)),
                        "likeCount": int(stats.get("likeCount", 0)),
                        "commentCount": int(stats.get("commentCount", 0)),
                        "duration": details.get("duration", ""),
                    }
            except Exception as e:
                logger.warning(f"Failed to batch fetch videos {batch[:3]}...: {e}")

        return results

    def search(
        self,
        query: str,
        max_results: int = 10,
        order: str = "relevance",
        type_: str = "video",
        video_duration: str | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Search videos via search.list, consuming 100 units from general quota."""
        if not query:
            return []
        service = self.get_service()
        params: dict[str, Any] = {
            "part": "snippet",
            "q": query,
            "maxResults": min(max(1, max_results), 50),
            "order": order,
            "type": type_,
        }
        if video_duration:
            params["videoDuration"] = video_duration
        params.update(kwargs)

        req = service.search().list(**params)
        resp = req.execute()
        if self.quota_tracker:
            self.quota_tracker.record_general(QUOTA_COSTS["search.list"])

        items = resp.get("items", [])
        return items if isinstance(items, list) else []

    def upload(
        self,
        path: Path | None = None,
        title: str = "",
        description: str = "",
        tags: list[str] | Sequence[str] | None = None,
        privacy: str = "unlisted",
        category_id: str | None = None,
        default_language: str | None = None,
        altered_content: bool | None = None,
        publish_at: str | None = None,
        playlist_id: str | None = None,
        auto_pin_comment: bool | None = None,
        pin_comment_text: str | None = None,
        thumbnail_path: Path | str | None = None,
        *,
        video_path: Path | None = None,
        privacy_status: str | None = None,
        custom_tags: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> UploadResult:
        """Upload a video MP4 using chunked resumable insert with advanced metadata & scheduling."""
        target_path = Path(video_path if video_path is not None else (path or "")).resolve()
        if not target_path.is_file():
            raise FileNotFoundError(f"Video file not found for upload: {target_path}")

        if self.quota_tracker is not None and not self.quota_tracker.can_upload():
            raise QuotaExhaustedError(
                bucket="upload",
                spent=self.quota_tracker.get_upload_spend(),
                limit=self.quota_tracker.daily_upload_budget,
            )

        # Title: <=100 chars, no < or >, single trailing #Shorts, never empty
        final_title = build_upload_title(title)

        service = self.get_service()
        _, _, _, _, _, MediaFileUpload = _get_google_libraries()

        effective_privacy = (
            privacy_status or privacy or getattr(self.config, "privacy_status", "unlisted")
        )
        effective_category = (
            category_id or self.category_id or getattr(self.config, "category_id", None) or "24"
        )
        if custom_tags is not None or kwargs.get("custom_tags") is not None:
            effective_tags = build_compliant_tags(
                tags, custom_tags=custom_tags or kwargs.get("custom_tags")
            )
        else:
            # Sanitize tags and enforce YouTube API's 500-character combined length cap
            raw_tags = list(tags) if tags is not None else []
            effective_tags = []
            curr_len = 0
            for t in raw_tags:
                cleaned = str(t).replace("<", "").replace(">", "").strip()
                if not cleaned:
                    continue
                cost = len(cleaned) + (1 if effective_tags else 0)
                if curr_len + cost <= 500:
                    effective_tags.append(cleaned)
                    curr_len += cost

        # Enforce YouTube API's 5000-character description cap (word-boundary cut)
        final_description = truncate_on_word(description or "", MAX_DESCRIPTION_CHARS)

        snippet: dict[str, Any] = {
            "title": final_title,
            "description": final_description,
            "tags": effective_tags,
            "categoryId": str(effective_category),
        }

        lang = default_language or getattr(self.config, "default_language", None)
        if lang:
            snippet["defaultLanguage"] = lang
            snippet["defaultAudioLanguage"] = (
                getattr(self.config, "default_audio_language", None) or lang
            )

        status: dict[str, Any] = {
            "privacyStatus": effective_privacy,
            "selfDeclaredMadeForKids": False,
        }

        eff_altered = (
            altered_content
            if altered_content is not None
            else getattr(self.config, "altered_content", None)
        )
        if eff_altered is not None:
            status["containsSyntheticMedia"] = bool(eff_altered)

        # Native Server-Side Scheduling (publish_at ISO-8601 UTC string)
        # YouTube Data API v3 requires privacyStatus to be 'private' when publishAt is set
        if publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = str(publish_at)

        body = {
            "snippet": snippet,
            "status": status,
        }

        media = MediaFileUpload(
            str(target_path),
            mimetype="video/mp4",
            chunksize=self.chunk_size_bytes,
            resumable=True,
        )

        insert_request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        logger.info(
            f"Initiating resumable upload for '{final_title}' ({target_path.stat().st_size} bytes)..."
        )

        response = None
        retry_count = 0

        while response is None:
            try:
                status_res, response = insert_request.next_chunk()
                if status_res:
                    pct = int(status_res.progress() * 100)
                    logger.debug(f"Resumable upload progress: {pct}%")
                retry_count = 0  # Reset retry counter on successful chunk
            except Exception as ex:
                typed = classify_google_error(ex)
                if isinstance(typed, AuthError):
                    logger.error("OAuth token revoked or expired during upload: %s", typed)
                    raise CredentialsError(str(typed)) from ex

                if isinstance(typed, QuotaExceededError):
                    logger.warning("YouTube API quota exceeded: %s", typed)
                    self._mark_upload_quota_exhausted()
                    raise QuotaExceededError(str(typed)) from ex

                if isinstance(typed, PermanentUploadError):
                    logger.error(f"Permanent HTTP client error during upload: {ex}")
                    raise UploadFailedError(
                        f"Upload failed permanently with HTTP client error: {ex}"
                    ) from ex

                # RateLimitError and TransientUploadError fall through to retry with backoff.
                retry_count += 1
                if retry_count > self.max_retries:
                    logger.error(f"Upload failed after {self.max_retries} chunk retries: {ex}")
                    raise UploadFailedError(f"Upload failed permanently: {ex}") from ex

                outage_info = classify_network_or_outage_error(ex)
                backoff_sec = min(60.0, (2**retry_count) + random.uniform(0.1, 1.0))
                logger.warning(
                    f"Upload chunk error ({ex}); retrying in {backoff_sec:.1f}s "
                    f"(attempt {retry_count}/{self.max_retries}, outage: {outage_info.get('outage_type', 'none')})..."
                )
                time.sleep(backoff_sec)

        video_id = response.get("id")
        if not video_id:
            raise UploadFailedError(f"YouTube response missing video ID: {response}")

        self._requested_privacy[video_id] = str(status["privacyStatus"])
        returned_privacy = response.get("status", {}).get("privacyStatus", status["privacyStatus"])

        # Persist credentials if refreshed during operation
        try:
            if hasattr(service, "_http") and hasattr(service._http, "credentials"):
                srv_creds = service._http.credentials
                if srv_creds and srv_creds.valid and self.token_path.is_file():
                    atomic_write(self.token_path, srv_creds.to_json(), encoding="utf-8")
        except Exception as cred_err:
            logger.debug("Non-fatal error persisting refreshed credentials: %s", cred_err)

        # Post-upload actions
        if thumbnail_path is not None:
            thumb_p = Path(thumbnail_path)
            if thumb_p.is_file():
                self.upload_thumbnail(video_id, thumb_p)

        eff_playlist = playlist_id or getattr(self.config, "playlist_id", None)
        if eff_playlist:
            self.add_to_playlist(video_id, str(eff_playlist))

        eff_auto_pin = (
            auto_pin_comment
            if auto_pin_comment is not None
            else getattr(self.config, "auto_pin_comment", False)
        )
        if eff_auto_pin:
            comment_text = (
                pin_comment_text
                or getattr(self.config, "pin_comment_text", None)
                or "Watch more clips on our channel! #Shorts"
            )
            self.post_comment(video_id, str(comment_text))

        if self.quota_tracker:
            self.quota_tracker.record_upload(1)

        result = UploadResult(
            video_id=video_id,
            url=f"https://youtube.com/shorts/{video_id}",
            status="uploaded",
            verified=False,
            uploaded_at=datetime.now(UTC),
            quota_spent=1,
            privacy_status=returned_privacy,
            raw_response=response,
            verification_kind="unverified",
            verification_note=(
                "Accepted by videos.insert; not yet confirmed by videos.list processing status."
            ),
        )

        logger.info(f"Upload complete for video ID: {video_id} (privacy: {returned_privacy}).")
        return result

    # -------------------------------------------------------------------------
    # Verification Implementation
    # -------------------------------------------------------------------------

    def verify(self, video_id: str, expected_privacy: str | None = None) -> bool:
        """Query videos.list to check processing status and privacy.

        Consumes 1 general quota unit if quota tracker is attached.

        Returns:
            True only when processing succeeded and the privacy matches the requested value
            (``expected_privacy``, or the value this uploader sent for ``video_id``).
            False while the video is still processing or on a transient error (poll again).

        Raises:
            ProcessingError: processing failed or was rejected, or privacy does not match
                the request. Retrying cannot change these outcomes.
            AuthError / PermanentUploadError: the request can never succeed as sent.
        """
        service = self.get_service()
        logger.debug(f"Checking processing status for video {video_id}...")
        self.last_verification_kind = "unverified"
        expected = expected_privacy or self._requested_privacy.get(video_id)
        expected = expected.lower() if expected else None

        try:
            req = service.videos().list(
                part="status,processingDetails",
                id=video_id,
            )
            response = req.execute()

            if self.quota_tracker:
                self.quota_tracker.record_general(1)

            items = response.get("items", [])
            if not items:
                logger.warning(f"Video {video_id} not found in YouTube query.")
                return False

            item = items[0]
            status_obj = item.get("status", {})
            proc_obj = item.get("processingDetails", {})

            upload_status = status_obj.get("uploadStatus")
            processing_status = proc_obj.get("processingStatus")
            privacy_status = status_obj.get("privacyStatus")

            # Check for privacy lock enforcement
            if privacy_status == "private":
                logger.warning(
                    f"Video {video_id} has privacyStatus='private'. "
                    "If unverified API project restrictions are enforced by YouTube, "
                    "video viewing mode may be locked until compliance audit."
                )

            # Definitive processing failure: stop polling immediately.
            if processing_status == "failed" or upload_status in ("rejected", "failed"):
                reason = (
                    proc_obj.get("processingFailureReason")
                    or status_obj.get("rejectionReason")
                    or status_obj.get("failureReason")
                    or "unknown"
                )
                logger.error(f"Video {video_id} processing failed: {reason}")
                raise ProcessingError(f"Video {video_id} processing failed: {reason}")

            # A privacy mismatch is a clear failure, never a silent success.
            if expected and privacy_status is not None:
                if str(privacy_status).lower() != expected:
                    logger.error(
                        "Video %s privacy mismatch: requested '%s', YouTube reports '%s'.",
                        video_id,
                        expected,
                        privacy_status,
                    )
                    raise ProcessingError(
                        f"Video {video_id} privacy mismatch: requested '{expected}' "
                        f"but YouTube reports '{privacy_status}'."
                    )

            if upload_status == "uploaded" and processing_status == "succeeded":
                logger.info(
                    f"Video {video_id} verified: uploadStatus=uploaded, processingStatus=succeeded."
                )
                self.last_verification_kind = "api_confirmed"
                return True

            logger.info(
                f"Video {video_id} still processing: uploadStatus={upload_status}, "
                f"processingStatus={processing_status}. Needs retry."
            )
            return False
        except UploadError:
            raise
        except Exception as e:
            typed = classify_google_error(e)
            if isinstance(typed, TransientUploadError | RateLimitError):
                logger.warning(f"Transient error verifying video {video_id}; will retry: {e}")
                return False
            logger.error(f"Failed to verify video {video_id}: {typed}")
            raise typed from e
