"""API Key & OAuth Revocation / Expiration Guardian.

Provides active error classification, format validation, safe token quarantine,
and health auditing across YouTube OAuth, YouTube Data API v3, Gemini, OpenRouter,
and local GGUF credentials.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from autoshorts.core.exceptions import (
    CredentialInvalidError,
    CredentialRevokedError,
)
from autoshorts.core.logging_setup import redact
from autoshorts.core.paths import PROJECT_ROOT, get_data_dir

logger = logging.getLogger("autoshorts.credential_guardian")

ServiceType = Literal["youtube_oauth", "youtube_api_key", "gemini", "openrouter", "local_gguf"]
StatusType = Literal["valid", "missing", "invalid_key", "expired", "revoked", "quota_exhausted"]
SettingsTabType = Literal["tab-yt-api", "tab-ai-keys"]

VALID_SERVICES: set[str] = {
    "youtube_oauth",
    "youtube_api_key",
    "gemini",
    "openrouter",
    "local_gguf",
}

SERVICE_SETTINGS_TABS: dict[str, SettingsTabType] = {
    "youtube_oauth": "tab-yt-api",
    "youtube_api_key": "tab-yt-api",
    "gemini": "tab-ai-keys",
    "openrouter": "tab-ai-keys",
    "local_gguf": "tab-ai-keys",
}


class CredentialHealthReport(BaseModel):
    """Structured health and remediation report for an external service credential."""

    service: ServiceType
    status: StatusType
    is_usable: bool
    requires_settings_action: bool
    settings_tab: SettingsTabType
    error_code: str | None = None
    message: str
    remediation_steps: list[str] = Field(default_factory=list)


def _normalize_service(service: str) -> ServiceType:
    """Normalize input service string to recognized ServiceType literal."""
    s = (service or "").strip().lower()
    if s in VALID_SERVICES:
        return s  # type: ignore[return-value]
    if "oauth" in s:
        return "youtube_oauth"
    if "yt" in s or "youtube" in s:
        return "youtube_api_key"
    if "gemini" in s:
        return "gemini"
    if "openrouter" in s:
        return "openrouter"
    if "gguf" in s or "local" in s:
        return "local_gguf"
    return "youtube_api_key"


def _extract_error_details(
    error: BaseException | str | dict[str, Any],
) -> tuple[str, str | None, int | None]:
    """Extract combined message string, code hint, and HTTP status code from any error object."""
    text_parts: list[str] = []
    code_hint: str | None = None
    http_status: int | None = None

    if isinstance(error, str):
        text_parts.append(error)
    elif isinstance(error, dict):
        text_parts.append(json.dumps(error))
        if "code" in error and isinstance(error["code"], int):
            http_status = error["code"]
        err_obj = error.get("error")
        if isinstance(err_obj, dict):
            if "code" in err_obj and isinstance(err_obj["code"], int):
                http_status = err_obj["code"]
            if "message" in err_obj:
                text_parts.append(str(err_obj["message"]))
            if "status" in err_obj:
                text_parts.append(str(err_obj["status"]))
            for item in err_obj.get("errors", []):
                if isinstance(item, dict) and "reason" in item:
                    code_hint = code_hint or str(item["reason"])
                    text_parts.append(str(item["reason"]))
        elif isinstance(err_obj, str):
            code_hint = code_hint or err_obj
            text_parts.append(err_obj)
        if "error_description" in error:
            text_parts.append(str(error["error_description"]))
    elif isinstance(error, BaseException):
        text_parts.append(str(error))
        text_parts.append(repr(error))
        for attr in ("code", "status_code"):
            if hasattr(error, attr):
                val = getattr(error, attr)
                if isinstance(val, int):
                    http_status = val
                elif isinstance(val, str) and not code_hint:
                    code_hint = val
        if hasattr(error, "content"):
            content = error.content
            if isinstance(content, bytes):
                text_parts.append(content.decode("utf-8", errors="ignore"))
            elif isinstance(content, str):
                text_parts.append(content)
        if hasattr(error, "response"):
            resp = error.response
            if hasattr(resp, "text"):
                text_parts.append(str(resp.text))
            if hasattr(resp, "status_code") and isinstance(resp.status_code, int):
                http_status = resp.status_code

    combined_text = " ".join(text_parts)
    if http_status is None:
        match = re.search(r"\b(400|401|402|403|429)\b", combined_text)
        if match:
            http_status = int(match.group(1))

    return combined_text, code_hint, http_status


def classify_api_error(
    service: str,
    error: BaseException | str | dict[str, Any],
) -> CredentialHealthReport:
    """Detect failure signatures and produce an actionable CredentialHealthReport.

    Handles Google/YouTube OAuth revocations, invalid API keys, OpenRouter credit
    exhaustion, and YouTube quota exceedance.
    """
    norm_service = _normalize_service(service)
    combined_text, code_hint, http_status = _extract_error_details(error)
    text_lower = combined_text.lower()

    # -------------------------------------------------------------------------
    # 1. Google / YouTube OAuth Revocation / Expiration
    # -------------------------------------------------------------------------
    is_oauth_revoked = any(
        sig in text_lower
        for sig in (
            "invalid_grant",
            "token has been expired or revoked",
            "token has been revoked",
            "unauthorized_client",
        )
    )
    if is_oauth_revoked or (norm_service == "youtube_oauth" and "revoked" in text_lower):
        matched_code = (
            "unauthorized_client" if "unauthorized_client" in text_lower else "invalid_grant"
        )
        return CredentialHealthReport(
            service="youtube_oauth",
            status="revoked",
            is_usable=False,
            requires_settings_action=True,
            settings_tab="tab-yt-api",
            error_code=code_hint or matched_code,
            message="YouTube OAuth token has been expired or revoked (invalid_grant). Re-authorization is required.",
            remediation_steps=[
                "Open ⚙️ Settings in the AutoShorts dashboard.",
                "Navigate to the YouTube API & Upload (tab-yt-api) tab.",
                "Click 'Authorize YouTube Account (OAuth 2.0)' to re-authenticate with Google.",
                "Complete the browser OAuth consent flow so a new token.json is generated.",
            ],
        )

    # -------------------------------------------------------------------------
    # 2. YouTube Quota Exceeded / Daily Limit
    # -------------------------------------------------------------------------
    is_yt_quota = any(
        sig in text_lower
        for sig in (
            "quotaexceeded",
            "dailylimitexceeded",
            "user_rate_limit_exceeded",
        )
    ) or (
        norm_service in ("youtube_api_key", "youtube_oauth")
        and "quota" in text_lower
        and ("exceeded" in text_lower or "limit" in text_lower)
    )
    if is_yt_quota:
        matched_code = (
            "dailyLimitExceeded" if "dailylimitexceeded" in text_lower else "quotaExceeded"
        )
        return CredentialHealthReport(
            service=norm_service,
            status="quota_exhausted",
            is_usable=False,
            requires_settings_action=True,
            settings_tab="tab-yt-api",
            error_code=code_hint or matched_code,
            message="YouTube Data API quota has been exhausted for the day.",
            remediation_steps=[
                "Switch upload mode to Playwright MS Edge Studio ('studio') in ⚙️ Settings -> YouTube API & Upload (tab-yt-api).",
                "Wait for the daily YouTube API quota to reset at Midnight Pacific Time (PT).",
                "Or apply for a YouTube Data API quota increase in the Google Cloud Console.",
            ],
        )

    # -------------------------------------------------------------------------
    # 3. OpenRouter Credit Exhaustion (HTTP 402 / Insufficient Credits)
    # -------------------------------------------------------------------------
    is_openrouter_quota = (norm_service == "openrouter" or "openrouter" in text_lower) and (
        http_status == 402
        or "insufficient credits" in text_lower
        or "insufficient_credits" in text_lower
        or "credits exhausted" in text_lower
    )
    if is_openrouter_quota:
        return CredentialHealthReport(
            service="openrouter",
            status="quota_exhausted",
            is_usable=False,
            requires_settings_action=True,
            settings_tab="tab-ai-keys",
            error_code="402",
            message="OpenRouter account credits are exhausted (HTTP 402: Insufficient credits).",
            remediation_steps=[
                "Visit openrouter.ai/credits to top up your account balance.",
                "Alternatively, switch your AI provider to Gemini or local GGUF in Settings -> AI Providers & Keys (tab-ai-keys).",
            ],
        )

    # -------------------------------------------------------------------------
    # 4. Invalid API Key Signatures (Gemini, OpenRouter, YouTube)
    # -------------------------------------------------------------------------
    is_invalid_key = (
        any(
            sig in text_lower
            for sig in (
                "api_key_invalid",
                "api key not valid",
                "keyinvalid",
                "keyexpired",
                "invalid api key",
                "invalid_api_key",
                "unauthorized",
                "401 unauthorized",
            )
        )
        or http_status in (400, 401, 403)
        and ("api key" in text_lower or "key" in text_lower or http_status == 401)
    )

    if is_invalid_key:
        if norm_service == "gemini":
            return CredentialHealthReport(
                service="gemini",
                status="invalid_key",
                is_usable=False,
                requires_settings_action=True,
                settings_tab="tab-ai-keys",
                error_code=code_hint or "API_KEY_INVALID",
                message="Gemini API key is invalid or rejected by Google AI Studio.",
                remediation_steps=[
                    "Open ⚙️ Settings -> AI Providers & Keys (tab-ai-keys).",
                    "Verify or generate a new API key in Google AI Studio (aistudio.google.com).",
                    "Paste the new GEMINI_API_KEY and save settings.",
                ],
            )
        elif norm_service == "openrouter":
            err_code = code_hint or (
                "401"
                if (http_status == 401 or "401" in text_lower or "unauthorized" in text_lower)
                else "INVALID_KEY"
            )
            return CredentialHealthReport(
                service="openrouter",
                status="invalid_key",
                is_usable=False,
                requires_settings_action=True,
                settings_tab="tab-ai-keys",
                error_code=err_code,
                message="OpenRouter authentication failed: Invalid API key or unauthorized (HTTP 401).",
                remediation_steps=[
                    "Open ⚙️ Settings -> AI Providers & Keys (tab-ai-keys).",
                    "Generate a valid API key at openrouter.ai/keys.",
                    "Update the OPENROUTER_API_KEY field and save settings.",
                ],
            )
        elif norm_service in ("youtube_api_key", "youtube_oauth"):
            return CredentialHealthReport(
                service=norm_service,
                status="invalid_key",
                is_usable=False,
                requires_settings_action=True,
                settings_tab="tab-yt-api",
                error_code=code_hint or "API_KEY_INVALID",
                message="YouTube Data API key is invalid or rejected by Google Cloud.",
                remediation_steps=[
                    "Open ⚙️ Settings -> YouTube API & Upload (tab-yt-api).",
                    "Check that YouTube Data API v3 is enabled in Google Cloud Console.",
                    "Ensure YOUTUBE_API_KEY is copied accurately without extra quotes or spaces.",
                ],
            )

    # -------------------------------------------------------------------------
    # 5. Fallback for Unrecognized Errors
    # -------------------------------------------------------------------------
    tab = SERVICE_SETTINGS_TABS.get(norm_service, "tab-ai-keys")
    return CredentialHealthReport(
        service=norm_service,
        status="invalid_key",
        is_usable=False,
        requires_settings_action=True,
        settings_tab=tab,
        error_code=code_hint or (str(http_status) if http_status else None),
        # Raw error text can echo request URLs (?key=...); redact before it reaches UI or logs.
        message=f"Credential error encountered for {norm_service}: {redact(combined_text.strip()[:180])}",
        remediation_steps=[
            f"Open ⚙️ Settings and check the credentials in {tab}.",
            "Verify network access and API service status.",
        ],
    )


def quarantine_revoked_oauth_token(token_path: Path | str) -> Path | None:
    """Safely move a confirmed revoked OAuth token file to prevent looping.

    When token.json is confirmed revoked (invalid_grant), moves it to
    token.revoked.json.bak and logs a clear warning prompting re-authorization.

    Args:
        token_path: Path to token.json.

    Returns:
        The backup Path if moved, or None if token_path did not exist.
    """
    p = Path(token_path).resolve()
    if not p.is_file():
        logger.debug("quarantine_revoked_oauth_token: %s is not an existing file.", p)
        return None

    if p.name == "token.json":
        backup_path = p.parent / "token.revoked.json.bak"
    else:
        backup_path = p.parent / f"{p.stem}.revoked.json.bak"

    try:
        if backup_path.exists():
            backup_path.unlink()
        p.replace(backup_path)
        logger.warning(
            "YouTube OAuth token at %s was revoked (invalid_grant) and has been quarantined to %s. "
            "Please open ⚙️ Settings -> YouTube API & Upload (tab-yt-api) and click "
            "'Authorize YouTube Account (OAuth 2.0)' to re-authenticate.",
            p,
            backup_path,
        )
        return backup_path
    except Exception as e:
        logger.error("Failed to quarantine revoked OAuth token %s to %s: %s", p, backup_path, e)
        try:
            import shutil

            shutil.copy2(p, backup_path)
            p.unlink()
            return backup_path
        except Exception:
            return None


def validate_key_format(key_name: str, raw_value: str | None) -> tuple[bool, str]:
    """Validate common formatting mistakes before saving or calling external APIs.

    Strips accidental quotes, whitespace, and 'Bearer ' prefix. Rejects placeholders
    and masked characters ('••••').

    Returns:
        (is_valid: bool, cleaned_key_or_reason: str)
    """
    if raw_value is None:
        return False, f"{key_name} is empty or not provided."

    val_str = str(raw_value).strip()
    if not val_str:
        return False, f"{key_name} is empty or blank."

    # Strip surrounding single or double quotes
    while (val_str.startswith('"') and val_str.endswith('"')) or (
        val_str.startswith("'") and val_str.endswith("'")
    ):
        val_str = val_str[1:-1].strip()

    # Strip accidental Bearer prefix
    if val_str.lower().startswith("bearer "):
        val_str = val_str[7:].strip()
        while (val_str.startswith('"') and val_str.endswith('"')) or (
            val_str.startswith("'") and val_str.endswith("'")
        ):
            val_str = val_str[1:-1].strip()

    if not val_str:
        return False, f"{key_name} is empty after stripping quotes/prefix."

    # Reject masked bullet characters
    if "•" in val_str or "\u2022" in val_str:
        return (
            False,
            f"{key_name} contains masked placeholder characters ('•'). Please enter the full key.",
        )

    # Reject placeholder texts
    lower_val = val_str.lower()
    placeholder_signatures = [
        "your_api_key",
        "your-api-key",
        "your_key",
        "enter_api_key",
        "insert_api_key",
        "placeholder",
        "api_key_here",
        "sk-...",
        "changeme",
        "test_key",
    ]
    if (
        any(sig in lower_val for sig in placeholder_signatures)
        or lower_val in ("none", "null", "undefined", "...", "xxxx", "apikey")
        or lower_val.startswith(("your_", "your-"))
    ):
        return False, f"{key_name} contains placeholder text ('{val_str}')."

    # Reject internal whitespace or newlines
    if any(c.isspace() for c in val_str):
        return False, f"{key_name} contains internal whitespace or newlines."

    # Reject unreasonably short keys (valid keys for Gemini, OpenRouter, YouTube are 15+ chars)
    if len(val_str) < 8:
        return False, f"{key_name} is too short ({len(val_str)} chars) to be a valid API key."

    return True, val_str


def audit_all_credentials(config: Any) -> dict[str, CredentialHealthReport]:
    """Audit all external credentials without making destructive network calls.

    Audits:
      - youtube_oauth (client_secret.json & token.json structure and validity)
      - youtube_api_key
      - gemini
      - openrouter
      - local_gguf

    Returns:
        Complete health map: {service_name: CredentialHealthReport}
    """
    import os

    reports: dict[str, CredentialHealthReport] = {}
    data_dir = get_data_dir()

    # -------------------------------------------------------------------------
    # 1. youtube_oauth
    # -------------------------------------------------------------------------
    client_secret_p = data_dir / "client_secret.json"
    token_p = data_dir / "token.json"

    if not client_secret_p.is_file() or client_secret_p.stat().st_size == 0:
        reports["youtube_oauth"] = CredentialHealthReport(
            service="youtube_oauth",
            status="missing",
            is_usable=False,
            requires_settings_action=True,
            settings_tab="tab-yt-api",
            message="Google OAuth client_secret.json is not configured.",
            remediation_steps=[
                "Download client_secret.json from the Google Cloud Console.",
                "Upload it in ⚙️ Settings -> YouTube API & Upload (tab-yt-api).",
            ],
        )
    else:
        # Validate client_secret.json structure
        cs_valid = False
        try:
            cs_data = json.loads(client_secret_p.read_text(encoding="utf-8"))
            if isinstance(cs_data, dict):
                inner = cs_data.get("installed") or cs_data.get("web")
                if (
                    isinstance(inner, dict)
                    and inner.get("client_id")
                    and inner.get("client_secret")
                ):
                    cs_valid = True
        except Exception:
            cs_valid = False

        if not cs_valid:
            reports["youtube_oauth"] = CredentialHealthReport(
                service="youtube_oauth",
                status="invalid_key",
                is_usable=False,
                requires_settings_action=True,
                settings_tab="tab-yt-api",
                message="client_secret.json is malformed or missing client_id/client_secret.",
                remediation_steps=[
                    "Download a fresh client_secret.json (Desktop app type) from Google Cloud.",
                    "Save it via ⚙️ Settings -> YouTube API & Upload (tab-yt-api).",
                ],
            )
        elif not token_p.is_file() or token_p.stat().st_size == 0:
            reports["youtube_oauth"] = CredentialHealthReport(
                service="youtube_oauth",
                status="missing",
                is_usable=False,
                requires_settings_action=True,
                settings_tab="tab-yt-api",
                message="OAuth client secret is installed, but account is not authorized (token.json missing).",
                remediation_steps=[
                    "Open ⚙️ Settings -> YouTube API & Upload (tab-yt-api).",
                    "Click 'Authorize YouTube Account (OAuth 2.0)' to grant access.",
                ],
            )
        else:
            # Check token.json
            try:
                t_data = json.loads(token_p.read_text(encoding="utf-8"))
                has_token = isinstance(t_data, dict) and bool(
                    t_data.get("token") or t_data.get("refresh_token")
                )
                has_refresh = isinstance(t_data, dict) and bool(t_data.get("refresh_token"))
                expiry_str = t_data.get("expiry") if isinstance(t_data, dict) else None

                expired = False
                if expiry_str:
                    try:
                        exp_dt = datetime.fromisoformat(str(expiry_str))
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=UTC)
                        if exp_dt < datetime.now(UTC):
                            expired = True
                    except Exception:
                        pass

                if not has_token:
                    reports["youtube_oauth"] = CredentialHealthReport(
                        service="youtube_oauth",
                        status="invalid_key",
                        is_usable=False,
                        requires_settings_action=True,
                        settings_tab="tab-yt-api",
                        message="token.json is malformed or missing credentials.",
                        remediation_steps=[
                            "Click 'Authorize YouTube Account (OAuth 2.0)' in ⚙️ Settings -> YouTube API & Upload (tab-yt-api).",
                        ],
                    )
                elif expired and not has_refresh:
                    reports["youtube_oauth"] = CredentialHealthReport(
                        service="youtube_oauth",
                        status="expired",
                        is_usable=False,
                        requires_settings_action=True,
                        settings_tab="tab-yt-api",
                        message="YouTube OAuth access token has expired and has no refresh token.",
                        remediation_steps=[
                            "Click 'Authorize YouTube Account (OAuth 2.0)' in ⚙️ Settings -> YouTube API & Upload (tab-yt-api) to re-authorize.",
                        ],
                    )
                else:
                    reports["youtube_oauth"] = CredentialHealthReport(
                        service="youtube_oauth",
                        status="valid",
                        is_usable=True,
                        requires_settings_action=False,
                        settings_tab="tab-yt-api",
                        message="YouTube OAuth credentials and token are configured and active.",
                        remediation_steps=[],
                    )
            except Exception as e:
                reports["youtube_oauth"] = CredentialHealthReport(
                    service="youtube_oauth",
                    status="invalid_key",
                    is_usable=False,
                    requires_settings_action=True,
                    settings_tab="tab-yt-api",
                    message=f"Failed to read token.json: {e}",
                    remediation_steps=[
                        "Click 'Authorize YouTube Account (OAuth 2.0)' in ⚙️ Settings -> YouTube API & Upload (tab-yt-api).",
                    ],
                )

    # -------------------------------------------------------------------------
    # 2. youtube_api_key
    # -------------------------------------------------------------------------
    yt_key: str | None = None
    if hasattr(config, "get_youtube_api_key_str"):
        yt_key = config.get_youtube_api_key_str()
    elif isinstance(config, dict) and config.get("youtube_api_key"):
        yt_key = str(config["youtube_api_key"])
    if not yt_key:
        yt_key = os.environ.get("YOUTUBE_API_KEY")

    if not yt_key or not yt_key.strip():
        reports["youtube_api_key"] = CredentialHealthReport(
            service="youtube_api_key",
            status="missing",
            is_usable=False,
            requires_settings_action=True,
            settings_tab="tab-yt-api",
            message="YouTube Data API key is not configured.",
            remediation_steps=[
                "Enter your YOUTUBE_API_KEY in ⚙️ Settings -> YouTube API & Upload (tab-yt-api).",
            ],
        )
    else:
        is_valid, cleaned_or_reason = validate_key_format("YOUTUBE_API_KEY", yt_key)
        if not is_valid:
            reports["youtube_api_key"] = CredentialHealthReport(
                service="youtube_api_key",
                status="invalid_key",
                is_usable=False,
                requires_settings_action=True,
                settings_tab="tab-yt-api",
                message=f"YouTube API key formatting error: {cleaned_or_reason}",
                remediation_steps=[
                    "Open ⚙️ Settings -> YouTube API & Upload (tab-yt-api).",
                    "Fix formatting issues (remove quotes, prefixes, or placeholder text).",
                ],
            )
        else:
            reports["youtube_api_key"] = CredentialHealthReport(
                service="youtube_api_key",
                status="valid",
                is_usable=True,
                requires_settings_action=False,
                settings_tab="tab-yt-api",
                message="YouTube Data API key is configured and format is valid.",
                remediation_steps=[],
            )

    # -------------------------------------------------------------------------
    # 3. gemini
    # -------------------------------------------------------------------------
    gemini_key: str | None = None
    if hasattr(config, "get_gemini_api_key_str"):
        gemini_key = config.get_gemini_api_key_str()
    elif isinstance(config, dict) and config.get("gemini_api_key"):
        gemini_key = str(config["gemini_api_key"])
    if not gemini_key:
        gemini_key = os.environ.get("GEMINI_API_KEY")

    if not gemini_key or not gemini_key.strip():
        reports["gemini"] = CredentialHealthReport(
            service="gemini",
            status="missing",
            is_usable=False,
            requires_settings_action=True,
            settings_tab="tab-ai-keys",
            message="Gemini API key is not configured.",
            remediation_steps=[
                "Enter your GEMINI_API_KEY in ⚙️ Settings -> AI Providers & Keys (tab-ai-keys).",
            ],
        )
    else:
        is_valid, cleaned_or_reason = validate_key_format("GEMINI_API_KEY", gemini_key)
        if not is_valid:
            reports["gemini"] = CredentialHealthReport(
                service="gemini",
                status="invalid_key",
                is_usable=False,
                requires_settings_action=True,
                settings_tab="tab-ai-keys",
                message=f"Gemini API key formatting error: {cleaned_or_reason}",
                remediation_steps=[
                    "Open ⚙️ Settings -> AI Providers & Keys (tab-ai-keys).",
                    "Fix formatting issues (remove quotes, prefixes, or placeholder text).",
                ],
            )
        else:
            reports["gemini"] = CredentialHealthReport(
                service="gemini",
                status="valid",
                is_usable=True,
                requires_settings_action=False,
                settings_tab="tab-ai-keys",
                message="Gemini API key is configured and format is valid.",
                remediation_steps=[],
            )

    # -------------------------------------------------------------------------
    # 4. openrouter
    # -------------------------------------------------------------------------
    or_key: str | None = None
    if hasattr(config, "get_openrouter_api_key_str"):
        or_key = config.get_openrouter_api_key_str()
    elif isinstance(config, dict) and config.get("openrouter_api_key"):
        or_key = str(config["openrouter_api_key"])
    if not or_key:
        or_key = os.environ.get("OPENROUTER_API_KEY")

    if not or_key or not or_key.strip():
        reports["openrouter"] = CredentialHealthReport(
            service="openrouter",
            status="missing",
            is_usable=False,
            requires_settings_action=True,
            settings_tab="tab-ai-keys",
            message="OpenRouter API key is not configured.",
            remediation_steps=[
                "Enter your OPENROUTER_API_KEY in ⚙️ Settings -> AI Providers & Keys (tab-ai-keys).",
            ],
        )
    else:
        is_valid, cleaned_or_reason = validate_key_format("OPENROUTER_API_KEY", or_key)
        if not is_valid:
            reports["openrouter"] = CredentialHealthReport(
                service="openrouter",
                status="invalid_key",
                is_usable=False,
                requires_settings_action=True,
                settings_tab="tab-ai-keys",
                message=f"OpenRouter API key formatting error: {cleaned_or_reason}",
                remediation_steps=[
                    "Open ⚙️ Settings -> AI Providers & Keys (tab-ai-keys).",
                    "Fix formatting issues (remove quotes, prefixes, or placeholder text).",
                ],
            )
        else:
            reports["openrouter"] = CredentialHealthReport(
                service="openrouter",
                status="valid",
                is_usable=True,
                requires_settings_action=False,
                settings_tab="tab-ai-keys",
                message="OpenRouter API key is configured and format is valid.",
                remediation_steps=[],
            )

    # -------------------------------------------------------------------------
    # 5. local_gguf
    # -------------------------------------------------------------------------
    local_p = (
        getattr(config, "local_gguf_path", None)
        or os.environ.get("LOCAL_GGUF_PATH")
        or getattr(getattr(config, "llm", None), "local_model_path", None)
    )
    if isinstance(config, dict) and not local_p:
        local_p = config.get("local_gguf_path")

    models_dir = PROJECT_ROOT / "data" / "models"
    has_local_file = bool(local_p and Path(local_p).is_file())
    has_dir_gguf = bool(models_dir.is_dir() and list(models_dir.glob("*.gguf")))

    if has_local_file:
        reports["local_gguf"] = CredentialHealthReport(
            service="local_gguf",
            status="valid",
            is_usable=True,
            requires_settings_action=False,
            settings_tab="tab-ai-keys",
            message=f"Local GGUF model found at {local_p}.",
            remediation_steps=[],
        )
    elif has_dir_gguf:
        reports["local_gguf"] = CredentialHealthReport(
            service="local_gguf",
            status="valid",
            is_usable=True,
            requires_settings_action=False,
            settings_tab="tab-ai-keys",
            message="Local GGUF model found in data/models directory.",
            remediation_steps=[],
        )
    else:
        reports["local_gguf"] = CredentialHealthReport(
            service="local_gguf",
            status="missing",
            is_usable=False,
            requires_settings_action=True,
            settings_tab="tab-ai-keys",
            message="No local GGUF model file configured or found.",
            remediation_steps=[
                "Download a GGUF model (e.g. Qwen 2.5) into data/models/",
                "Or specify LOCAL_GGUF_PATH in ⚙️ Settings -> AI Providers & Keys (tab-ai-keys).",
            ],
        )

    return reports


__all__ = [
    "CredentialHealthReport",
    "CredentialInvalidError",
    "CredentialRevokedError",
    "ServiceType",
    "SettingsTabType",
    "StatusType",
    "audit_all_credentials",
    "classify_api_error",
    "quarantine_revoked_oauth_token",
    "validate_key_format",
]
