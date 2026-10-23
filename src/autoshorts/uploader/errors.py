"""Typed upload failures, so callers branch on error type instead of message text.

Hierarchy (all subclasses of ``core.exceptions.UploadError``):

- ``AuthError``: OAuth credentials missing, revoked, or not refreshable. Not retryable.
  ``CredentialsError`` (youtube_api) is a subclass, so existing imports keep working.
- ``QuotaExceededError``: daily API quota exhausted (403 quotaExceeded / uploadLimitExceeded).
  The only error that triggers the Studio fallback.
- ``RateLimitError``: per-second or per-user throttling (403/429). Retryable with backoff.
- ``PermanentUploadError``: 400/404/other 4xx that will never succeed as sent. Not retryable.
- ``TransientUploadError``: 5xx, 408, network timeouts. Retryable.
- ``ProcessingError``: the video was uploaded but YouTube reports a processing failure or a
  privacy mismatch. Not retryable.
"""

from __future__ import annotations

import json
from typing import Any

from autoshorts.core.exceptions import UploadError, UploadVerificationError

__all__ = [
    "AuthError",
    "PermanentUploadError",
    "ProcessingError",
    "QuotaExceededError",
    "RateLimitError",
    "TransientUploadError",
    "UploadError",
    "classify_google_error",
]

_QUOTA_REASONS: frozenset[str] = frozenset(
    {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"}
)
_RATE_LIMIT_REASONS: frozenset[str] = frozenset({"userRateLimitExceeded", "rateLimitExceeded"})
_AUTH_MARKERS: tuple[str, ...] = (
    "invalid_grant",
    "unauthorized_client",
    "expired or revoked",
    "token has been revoked",
)


class AuthError(UploadError):
    """OAuth credentials are missing, revoked, or cannot be refreshed. Not retryable."""


class QuotaExceededError(UploadError):
    """Daily YouTube Data API quota is exhausted (403 quotaExceeded / uploadLimitExceeded)."""


class RateLimitError(UploadError):
    """Per-second or per-user throttling (403 userRateLimitExceeded / 429). Retryable."""


class PermanentUploadError(UploadError):
    """The request will never succeed as sent (400/404/other 4xx, invalid title, missing file)."""


class TransientUploadError(UploadError):
    """Temporary failure (5xx, 408, network timeout). Retryable."""


class ProcessingError(UploadVerificationError):
    """The upload was accepted but YouTube reports a processing failure or privacy mismatch."""


def _http_status(ex: BaseException) -> int | None:
    status = getattr(getattr(ex, "resp", None), "status", None)
    if status is None:
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _response_body(ex: BaseException) -> str:
    content: Any = getattr(ex, "content", b"") or b""
    if isinstance(content, bytes):
        return content.decode("utf-8", "ignore")
    return str(content)


def _reasons(ex: BaseException, body: str) -> set[str]:
    """Collect Google API error reasons from the JSON body, error_details, and message text."""
    found: set[str] = set()
    try:
        payload = json.loads(body) if body.strip().startswith("{") else None
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        err = payload["error"]
        for item in list(err.get("errors") or []) + list(err.get("details") or []):
            if isinstance(item, dict) and item.get("reason"):
                found.add(str(item["reason"]))

    details = getattr(ex, "error_details", None)
    if isinstance(details, list):
        for item in details:
            if isinstance(item, dict) and item.get("reason"):
                found.add(str(item["reason"]))

    text = f"{ex} {body}"
    found.update(r for r in (_QUOTA_REASONS | _RATE_LIMIT_REASONS) if r in text)
    return found


def classify_google_error(ex: BaseException) -> UploadError:
    """Return (do not raise) the typed UploadError that matches a Google API or network failure.

    HTTP status and the Google error ``reason`` are the source of truth. Unknown non-HTTP
    exceptions are classified as transient, matching the previous retry behaviour.
    """
    if isinstance(ex, UploadError):
        return ex

    message = str(ex) or type(ex).__name__
    body = _response_body(ex)
    lowered = f"{ex} {body}".lower()
    status = _http_status(ex)
    reasons = _reasons(ex, body)

    if status == 401 or any(marker in lowered for marker in _AUTH_MARKERS):
        return AuthError(message)
    if reasons & _QUOTA_REASONS:
        return QuotaExceededError(message)
    if status == 429 or reasons & _RATE_LIMIT_REASONS:
        return RateLimitError(message)
    if status == 408 or (status is not None and status >= 500):
        return TransientUploadError(message)
    if status is not None and 400 <= status < 500:
        return PermanentUploadError(message)
    return TransientUploadError(message)
