"""YouTube Data API v3 uploader implementing resumable video upload and verification.

Implements contracts.Uploader via chunked resumable upload with retry/backoff,
OAuth Desktop credentials handling, and post-upload processing verification (PLAN.md section 8.1 & 8.4).
"""

from __future__ import annotations

import logging
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoshorts.core.config import Settings, UploadConfig
from autoshorts.core.exceptions import AutoShortsError
from autoshorts.core.models import UploadResult
from autoshorts.core.paths import get_data_dir
from autoshorts.quota.quota_tracker import QuotaTracker

logger = logging.getLogger(__name__)

SCOPES: list[str] = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class CredentialsError(AutoShortsError):
    """Raised when OAuth credentials cannot be loaded or refreshed."""


class UploadFailedError(AutoShortsError):
    """Raised when a video upload fails permanently."""


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
        self.client_secret_path = Path(client_secret_path) if client_secret_path else (data_dir / "client_secret.json")
        self.token_path = Path(token_path) if token_path else (data_dir / "token.json")

        self._service = youtube_service

    def get_service(self) -> Any:
        """Return or lazily initialize the authenticated YouTube service."""
        if self._service is not None:
            return self._service

        Request, Credentials, InstalledAppFlow, build, _, _ = _get_google_libraries()

        creds = None
        if self.token_path.is_file():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to read existing token file: {e}")

        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired YouTube OAuth access token...")
            try:
                creds.refresh(Request())
                # Persist refreshed token without logging credentials
                self.token_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.token_path, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
                logger.info("Successfully refreshed and saved OAuth token.")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to refresh OAuth token: {e}")
                creds = None

        if not creds or not creds.valid:
            if not self.client_secret_path.is_file():
                raise CredentialsError(
                    f"OAuth credentials missing. Place client_secret.json in '{self.client_secret_path}'."
                )

            logger.info("Starting one-time Desktop OAuth browser authorization flow...")
            flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secret_path), SCOPES)
            creds = flow.run_local_server(port=0)

            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            logger.info("OAuth authorization successful; saved token.json.")

        self._service = build("youtube", "v3", credentials=creds, cache_discovery=False)
        return self._service

    # -------------------------------------------------------------------------
    # Upload Protocol Implementation
    # -------------------------------------------------------------------------

    def upload(
        self,
        path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "unlisted",
    ) -> UploadResult:
        """Upload a video MP4 using chunked resumable insert."""
        target_path = Path(path).resolve()
        if not target_path.is_file():
            raise FileNotFoundError(f"Video file not found for upload: {target_path}")

        # Ensure title contains #Shorts and does not exceed 100 characters
        final_title = title
        if "#shorts" not in final_title.lower():
            if len(final_title) + len(" #Shorts") <= 100:
                final_title = f"{final_title} #Shorts"
            else:
                final_title = f"{final_title[:91]}... #Shorts"

        service = self.get_service()
        _, _, _, _, HttpError, MediaFileUpload = _get_google_libraries()

        privacy_status = privacy or self.config.privacy_status

        body = {
            "snippet": {
                "title": final_title,
                "description": description,
                "tags": tags or [],
                "categoryId": str(self.category_id),
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
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

        logger.info(f"Initiating resumable upload for '{final_title}' ({target_path.stat().st_size} bytes)...")

        response = None
        retry_count = 0

        while response is None:
            try:
                status, response = insert_request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    logger.debug(f"Resumable upload progress: {pct}%")
                retry_count = 0  # Reset retry counter on successful chunk
            except (HttpError, OSError, ConnectionError) as ex:
                retry_count += 1
                if retry_count > self.max_retries:
                    logger.error(f"Upload failed after {self.max_retries} chunk retries: {ex}")
                    raise UploadFailedError(f"Upload failed permanently: {ex}") from ex

                backoff_sec = min(60.0, (2 ** retry_count) + random.uniform(0.1, 1.0))
                logger.warning(
                    f"Upload chunk error ({ex}); retrying in {backoff_sec:.1f}s (attempt {retry_count}/{self.max_retries})..."
                )
                time.sleep(backoff_sec)

        video_id = response.get("id")
        if not video_id:
            raise UploadFailedError(f"YouTube response missing video ID: {response}")

        returned_privacy = response.get("status", {}).get("privacyStatus", privacy_status)

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
        )

        logger.info(f"Upload complete for video ID: {video_id} (privacy: {returned_privacy}).")
        return result

    # -------------------------------------------------------------------------
    # Verification Implementation
    # -------------------------------------------------------------------------

    def verify(self, video_id: str) -> bool:
        """Query videos.list to check processingStatus and privacyStatus.

        Consumes 1 general quota unit if quota tracker is attached.
        Returns True if processing succeeded, False otherwise (pending or failed).
        """
        service = self.get_service()
        logger.debug(f"Checking processing status for video {video_id}...")

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

            # Processing outcome evaluation
            if processing_status == "failed":
                reason = proc_obj.get("processingFailureReason", "unknown")
                logger.error(f"Video {video_id} processing failed: {reason}")
                return False

            if upload_status == "uploaded" and processing_status == "succeeded":
                logger.info(f"Video {video_id} verified: uploadStatus=uploaded, processingStatus=succeeded.")
                return True

            logger.info(
                f"Video {video_id} still processing: uploadStatus={upload_status}, "
                f"processingStatus={processing_status}. Needs retry."
            )
            return False
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to verify video {video_id}: {e}")
            return False
