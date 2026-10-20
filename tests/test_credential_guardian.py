"""Unit tests for Credential Guardian (Track 7).

Tests API error classification, OAuth token quarantine, API key format validation,
and health auditing across all services.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoshorts.core.credential_guardian import (
    CredentialHealthReport,
    audit_all_credentials,
    classify_api_error,
    quarantine_revoked_oauth_token,
    validate_key_format,
)
from autoshorts.core.exceptions import (
    AutoShortsError,
    CredentialInvalidError,
    CredentialRevokedError,
)

# =============================================================================
# 1. Exception Hierarchy Tests
# =============================================================================


def test_exception_inheritance() -> None:
    """Verify CredentialRevokedError and CredentialInvalidError subclass AutoShortsError."""
    assert issubclass(CredentialRevokedError, AutoShortsError)
    assert issubclass(CredentialInvalidError, AutoShortsError)

    rev_err = CredentialRevokedError("Token was revoked")
    inv_err = CredentialInvalidError("Key is malformed")

    assert "Token was revoked" in str(rev_err)
    assert "Key is malformed" in str(inv_err)


# =============================================================================
# 2. classify_api_error Tests
# =============================================================================


def test_classify_revoked_oauth_invalid_grant_string() -> None:
    """Test OAuth revocation detected from string error with 'invalid_grant'."""
    raw_error = "google.auth.exceptions.RefreshError: ('invalid_grant: Token has been expired or revoked.', {'error': 'invalid_grant', 'error_description': 'Token has been expired or revoked.'})"
    report = classify_api_error("youtube_oauth", raw_error)

    assert isinstance(report, CredentialHealthReport)
    assert report.service == "youtube_oauth"
    assert report.status == "revoked"
    assert report.is_usable is False
    assert report.requires_settings_action is True
    assert report.settings_tab == "tab-yt-api"
    assert report.error_code == "invalid_grant"
    assert any("Authorize YouTube Account (OAuth 2.0)" in step for step in report.remediation_steps)


def test_classify_revoked_oauth_unauthorized_client() -> None:
    """Test OAuth revocation detected from unauthorized_client."""
    raw_error = "unauthorized_client: The OAuth client was not found."
    report = classify_api_error("youtube_oauth", raw_error)

    assert report.service == "youtube_oauth"
    assert report.status == "revoked"
    assert report.is_usable is False
    assert report.requires_settings_action is True
    assert report.settings_tab == "tab-yt-api"
    assert report.error_code == "unauthorized_client"


def test_classify_revoked_oauth_dict() -> None:
    """Test OAuth revocation detected from dict payload."""
    payload = {
        "error": "invalid_grant",
        "error_description": "Token has been expired or revoked.",
    }
    report = classify_api_error("youtube_oauth", payload)

    assert report.status == "revoked"
    assert report.settings_tab == "tab-yt-api"
    assert report.is_usable is False


def test_classify_invalid_gemini_key_string() -> None:
    """Test Gemini invalid key detected from string."""
    err = "google.api_core.exceptions.InvalidArgument: 400 API_KEY_INVALID: API key not valid. Please pass a valid API key."
    report = classify_api_error("gemini", err)

    assert report.service == "gemini"
    assert report.status == "invalid_key"
    assert report.is_usable is False
    assert report.requires_settings_action is True
    assert report.settings_tab == "tab-ai-keys"
    assert report.error_code == "API_KEY_INVALID"
    assert any("tab-ai-keys" in step for step in report.remediation_steps)


def test_classify_invalid_gemini_key_dict() -> None:
    """Test Gemini invalid key detected from Google Cloud error dictionary."""
    err = {
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "status": "INVALID_ARGUMENT",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}
            ],
        }
    }
    report = classify_api_error("gemini", err)

    assert report.service == "gemini"
    assert report.status == "invalid_key"
    assert report.settings_tab == "tab-ai-keys"
    assert report.error_code == "API_KEY_INVALID"


def test_classify_invalid_openrouter_key() -> None:
    """Test OpenRouter 401 Unauthorized detected as invalid_key."""
    err = "401 Unauthorized: Invalid API key"
    report = classify_api_error("openrouter", err)

    assert report.service == "openrouter"
    assert report.status == "invalid_key"
    assert report.is_usable is False
    assert report.requires_settings_action is True
    assert report.settings_tab == "tab-ai-keys"
    assert report.error_code == "401"


def test_classify_openrouter_insufficient_credits() -> None:
    """Test OpenRouter HTTP 402 Insufficient credits detected as quota_exhausted."""
    err = {"error": {"code": 402, "message": "Insufficient credits"}}
    report = classify_api_error("openrouter", err)

    assert report.service == "openrouter"
    assert report.status == "quota_exhausted"
    assert report.is_usable is False
    assert report.requires_settings_action is True
    assert report.settings_tab == "tab-ai-keys"
    assert report.error_code == "402"
    assert any("openrouter.ai/credits" in step for step in report.remediation_steps)


def test_classify_youtube_quota_exceeded() -> None:
    """Test YouTube quotaExceeded detected with studio fallback and PT reset remediation."""
    err = (
        "googleapiclient.errors.HttpError: <HttpError 403 when requesting "
        "https://youtube.googleapis.com/youtube/v3/videos?part=snippet returned "
        "'The request cannot be completed because you have exceeded your quota.'>. "
        "Details: [{'message': 'The request cannot be completed because you have exceeded your quota.', "
        "'domain': 'youtube.quota', 'reason': 'quotaExceeded'}]"
    )
    report = classify_api_error("youtube_api_key", err)

    assert report.service == "youtube_api_key"
    assert report.status == "quota_exhausted"
    assert report.is_usable is False
    assert report.requires_settings_action is True
    assert report.settings_tab == "tab-yt-api"
    assert report.error_code == "quotaExceeded"
    # Verify fallback to studio or waiting for Midnight PT reset is suggested
    assert any("studio" in step.lower() for step in report.remediation_steps)
    assert any(
        "midnight" in step.lower() or "pt" in step.lower() for step in report.remediation_steps
    )


def test_classify_youtube_daily_limit_exceeded() -> None:
    """Test YouTube dailyLimitExceeded error classification."""
    err = {"error": {"code": 403, "message": "dailyLimitExceeded"}}
    report = classify_api_error("youtube_oauth", err)

    assert report.status == "quota_exhausted"
    assert report.settings_tab == "tab-yt-api"
    assert report.is_usable is False


def test_classify_youtube_invalid_key() -> None:
    """Test YouTube Data API keyInvalid error classification."""
    err = "keyInvalid: Bad API key provided."
    report = classify_api_error("youtube_api_key", err)

    assert report.status == "invalid_key"
    assert report.settings_tab == "tab-yt-api"
    assert report.is_usable is False


# =============================================================================
# 3. quarantine_revoked_oauth_token Tests
# =============================================================================


def test_quarantine_revoked_oauth_token_success(tmp_path: Path) -> None:
    """Test moving revoked token.json to token.revoked.json.bak."""
    token_file = tmp_path / "token.json"
    dummy_payload = {"token": "expired_abc", "refresh_token": "revoked_def"}
    token_file.write_text(json.dumps(dummy_payload), encoding="utf-8")

    assert token_file.is_file()

    backup_path = quarantine_revoked_oauth_token(token_file)

    assert backup_path is not None
    assert backup_path.name == "token.revoked.json.bak"
    assert backup_path.is_file()
    assert not token_file.exists()

    # Verify content preserved
    data = json.loads(backup_path.read_text(encoding="utf-8"))
    assert data["token"] == "expired_abc"


def test_quarantine_revoked_oauth_token_overwrites_existing_backup(tmp_path: Path) -> None:
    """Test that existing .bak file is overwritten cleanly."""
    token_file = tmp_path / "token.json"
    bak_file = tmp_path / "token.revoked.json.bak"

    token_file.write_text(json.dumps({"token": "new_revoked"}), encoding="utf-8")
    bak_file.write_text(json.dumps({"token": "old_backup"}), encoding="utf-8")

    backup_path = quarantine_revoked_oauth_token(token_file)

    assert backup_path == bak_file
    data = json.loads(backup_path.read_text(encoding="utf-8"))
    assert data["token"] == "new_revoked"


def test_quarantine_non_existent_file(tmp_path: Path) -> None:
    """Test quarantine on a non-existent file returns None without raising."""
    token_file = tmp_path / "does_not_exist.json"
    result = quarantine_revoked_oauth_token(token_file)
    assert result is None


# =============================================================================
# 4. validate_key_format Tests
# =============================================================================


def test_validate_key_format_clean_key() -> None:
    """Test valid API key format."""
    valid, cleaned = validate_key_format("GEMINI_API_KEY", "AIzaSyD1234567890abcdefghijklmnopq")
    assert valid is True
    assert cleaned == "AIzaSyD1234567890abcdefghijklmnopq"


def test_validate_key_format_strips_whitespace_and_quotes() -> None:
    """Test stripping whitespace and surrounding quotes."""
    valid, cleaned = validate_key_format(
        "YOUTUBE_API_KEY", '  "AIzaSyD1234567890abcdefghijklmnopq"  '
    )
    assert valid is True
    assert cleaned == "AIzaSyD1234567890abcdefghijklmnopq"

    valid, cleaned = validate_key_format(
        "YOUTUBE_API_KEY", "  'AIzaSyD1234567890abcdefghijklmnopq'  "
    )
    assert valid is True
    assert cleaned == "AIzaSyD1234567890abcdefghijklmnopq"


def test_validate_key_format_strips_bearer_prefix() -> None:
    """Test stripping accidental 'Bearer ' prefix."""
    raw = "Bearer sk-or-v1-abcdef1234567890abcdef1234567890"
    valid, cleaned = validate_key_format("OPENROUTER_API_KEY", raw)
    assert valid is True
    assert cleaned == "sk-or-v1-abcdef1234567890abcdef1234567890"

    raw_quoted = 'bearer "sk-or-v1-abcdef1234567890abcdef1234567890"'
    valid, cleaned = validate_key_format("OPENROUTER_API_KEY", raw_quoted)
    assert valid is True
    assert cleaned == "sk-or-v1-abcdef1234567890abcdef1234567890"


def test_validate_key_format_rejects_none_and_empty() -> None:
    """Test rejection of None and blank strings."""
    valid, reason = validate_key_format("GEMINI_API_KEY", None)
    assert valid is False
    assert "empty" in reason.lower()

    valid, reason = validate_key_format("GEMINI_API_KEY", "   ")
    assert valid is False
    assert "empty" in reason.lower() or "blank" in reason.lower()


def test_validate_key_format_rejects_placeholders() -> None:
    """Test rejection of placeholder strings."""
    placeholders = [
        "your_api_key_here",
        "YOUR_API_KEY_HERE",
        "enter_api_key",
        "placeholder",
        "changeme",
        "sk-...",
        "none",
        "null",
    ]
    for ph in placeholders:
        valid, reason = validate_key_format("GEMINI_API_KEY", ph)
        assert valid is False, f"Expected {ph} to be rejected"
        assert "placeholder" in reason.lower()


def test_validate_key_format_rejects_masked_bullets() -> None:
    """Test rejection of keys with masked bullet characters (••••)."""
    valid, reason = validate_key_format("GEMINI_API_KEY", "••••••••abcd")
    assert valid is False
    assert "•" in reason or "masked" in reason.lower()

    valid, reason = validate_key_format("GEMINI_API_KEY", "••••")
    assert valid is False


def test_validate_key_format_rejects_internal_whitespace() -> None:
    """Test rejection of keys with embedded whitespace or newlines."""
    valid, reason = validate_key_format("GEMINI_API_KEY", "AIzaSy1234 567890abcdef")
    assert valid is False
    assert "whitespace" in reason.lower()


def test_validate_key_format_rejects_too_short() -> None:
    """Test rejection of unreasonably short keys."""
    valid, reason = validate_key_format("GEMINI_API_KEY", "abc")
    assert valid is False
    assert "short" in reason.lower()


# =============================================================================
# 5. audit_all_credentials Tests
# =============================================================================


def test_audit_all_credentials_all_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test audit_all_credentials when no credentials are configured."""
    monkeypatch.setattr("autoshorts.core.credential_guardian.get_data_dir", lambda: tmp_path)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LOCAL_GGUF_PATH", raising=False)

    mock_config = MagicMock()
    mock_config.get_youtube_api_key_str.return_value = None
    mock_config.get_gemini_api_key_str.return_value = None
    mock_config.get_openrouter_api_key_str.return_value = None
    mock_config.local_gguf_path = None
    mock_config.llm = MagicMock(local_model_path=None)

    reports = audit_all_credentials(mock_config)

    assert set(reports.keys()) == {
        "youtube_oauth",
        "youtube_api_key",
        "gemini",
        "openrouter",
        "local_gguf",
    }
    assert reports["youtube_oauth"].status == "missing"
    assert reports["youtube_api_key"].status == "missing"
    assert reports["gemini"].status == "missing"
    assert reports["openrouter"].status == "missing"
    assert reports["local_gguf"].status == "missing"

    for r in reports.values():
        assert r.is_usable is False
        assert r.requires_settings_action is True


def test_audit_all_credentials_configured_and_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test audit_all_credentials when valid credentials and files are present."""
    monkeypatch.setattr("autoshorts.core.credential_guardian.get_data_dir", lambda: tmp_path)

    # 1. Create valid client_secret.json
    cs_path = tmp_path / "client_secret.json"
    cs_path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "123456789.apps.googleusercontent.com",
                    "client_secret": "GOCSPX-validsecret12345",
                    "project_id": "autoshorts-test",
                }
            }
        ),
        encoding="utf-8",
    )

    # 2. Create valid active token.json
    token_path = tmp_path / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "token": "valid_access_token_12345",
                "refresh_token": "valid_refresh_token_67890",
                "expiry": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    # 3. Create mock GGUF model file
    gguf_path = tmp_path / "model.gguf"
    gguf_path.write_bytes(b"GGUF_TEST_BYTES")

    mock_config = MagicMock()
    mock_config.get_youtube_api_key_str.return_value = "AIzaSyD_ValidYouTubeApiKey123456789"
    mock_config.get_gemini_api_key_str.return_value = "AIzaSyD_ValidGeminiApiKey123456789"
    mock_config.get_openrouter_api_key_str.return_value = (
        "sk-or-v1-abcdef1234567890abcdef1234567890"
    )
    mock_config.local_gguf_path = str(gguf_path)
    mock_config.llm = MagicMock(local_model_path=str(gguf_path))

    reports = audit_all_credentials(mock_config)

    assert reports["youtube_oauth"].status == "valid"
    assert reports["youtube_oauth"].is_usable is True
    assert reports["youtube_oauth"].requires_settings_action is False

    assert reports["youtube_api_key"].status == "valid"
    assert reports["youtube_api_key"].is_usable is True

    assert reports["gemini"].status == "valid"
    assert reports["gemini"].is_usable is True

    assert reports["openrouter"].status == "valid"
    assert reports["openrouter"].is_usable is True

    assert reports["local_gguf"].status == "valid"
    assert reports["local_gguf"].is_usable is True


def test_audit_all_credentials_expired_token_without_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test OAuth token expired without refresh_token is flagged as expired."""
    monkeypatch.setattr("autoshorts.core.credential_guardian.get_data_dir", lambda: tmp_path)

    cs_path = tmp_path / "client_secret.json"
    cs_path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "123456789.apps.googleusercontent.com",
                    "client_secret": "GOCSPX-secret",
                }
            }
        ),
        encoding="utf-8",
    )

    token_path = tmp_path / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "token": "expired_token_only",
                "refresh_token": None,
                "expiry": (datetime.now(UTC) - timedelta(hours=2)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    mock_config = MagicMock()
    mock_config.get_youtube_api_key_str.return_value = None
    mock_config.get_gemini_api_key_str.return_value = None
    mock_config.get_openrouter_api_key_str.return_value = None
    mock_config.local_gguf_path = None
    mock_config.llm = MagicMock(local_model_path=None)

    reports = audit_all_credentials(mock_config)

    assert reports["youtube_oauth"].status == "expired"
    assert reports["youtube_oauth"].is_usable is False
    assert reports["youtube_oauth"].requires_settings_action is True


def test_audit_all_credentials_expired_token_with_valid_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test OAuth token expired in the past but having a refresh_token is marked valid."""
    monkeypatch.setattr("autoshorts.core.credential_guardian.get_data_dir", lambda: tmp_path)

    cs_path = tmp_path / "client_secret.json"
    cs_path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "123456789.apps.googleusercontent.com",
                    "client_secret": "GOCSPX-secret",
                }
            }
        ),
        encoding="utf-8",
    )

    token_path = tmp_path / "token.json"
    token_path.write_text(
        json.dumps(
            {
                "token": "expired_access_token",
                "refresh_token": "valid_refresh_token",
                "expiry": (datetime.now(UTC) - timedelta(hours=5)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    mock_config = MagicMock()
    mock_config.get_youtube_api_key_str.return_value = None
    mock_config.get_gemini_api_key_str.return_value = None
    mock_config.get_openrouter_api_key_str.return_value = None
    mock_config.local_gguf_path = None
    mock_config.llm = MagicMock(local_model_path=None)

    reports = audit_all_credentials(mock_config)

    assert reports["youtube_oauth"].status == "valid"
    assert reports["youtube_oauth"].is_usable is True


def test_audit_all_credentials_malformed_client_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test malformed client_secret.json is flagged as invalid_key."""
    monkeypatch.setattr("autoshorts.core.credential_guardian.get_data_dir", lambda: tmp_path)

    cs_path = tmp_path / "client_secret.json"
    cs_path.write_text("NOT_VALID_JSON", encoding="utf-8")

    reports = audit_all_credentials(None)

    assert reports["youtube_oauth"].status == "invalid_key"
    assert reports["youtube_oauth"].is_usable is False
    assert reports["youtube_oauth"].requires_settings_action is True


def test_audit_all_credentials_invalid_formatted_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test placeholder key in config is flagged as invalid_key."""
    monkeypatch.setattr("autoshorts.core.credential_guardian.get_data_dir", lambda: tmp_path)

    mock_config = MagicMock()
    mock_config.get_youtube_api_key_str.return_value = "your_api_key_here"
    mock_config.get_gemini_api_key_str.return_value = "••••••••abcd"
    mock_config.get_openrouter_api_key_str.return_value = None
    mock_config.local_gguf_path = None
    mock_config.llm = MagicMock(local_model_path=None)

    reports = audit_all_credentials(mock_config)

    assert reports["youtube_api_key"].status == "invalid_key"
    assert reports["gemini"].status == "invalid_key"


def test_classify_api_error_exception_instance() -> None:
    """Test classify_api_error when passed a BaseException object."""
    exc = RuntimeError(
        "google.auth.exceptions.RefreshError: ('invalid_grant', 'Token has been revoked')"
    )
    report = classify_api_error("youtube_oauth", exc)

    assert report.status == "revoked"
    assert report.service == "youtube_oauth"
    assert report.settings_tab == "tab-yt-api"


def test_quarantine_with_string_path(tmp_path: Path) -> None:
    """Test quarantine_revoked_oauth_token accepts string path."""
    token_file = tmp_path / "token.json"
    token_file.write_text('{"token": "test"}', encoding="utf-8")

    backup = quarantine_revoked_oauth_token(str(token_file))
    assert backup is not None
    assert backup.name == "token.revoked.json.bak"
    assert backup.is_file()
