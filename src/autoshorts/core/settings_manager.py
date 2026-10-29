"""Settings and YouTube Data API v3 configuration manager for AutoShorts.

Provides safe persistence for application configuration (config.yaml), environment secrets (.env),
OAuth client credentials verification and authorization flow, and sanitized payloads for the Dashboard UI.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from autoshorts.core.config import Settings, load_config
from autoshorts.core.logging_setup import register_secret
from autoshorts.core.models import ChannelProfile
from autoshorts.core.paths import PROJECT_ROOT, atomic_write, get_config_dir, get_data_dir

logger = logging.getLogger("autoshorts.settings_manager")

# -----------------------------------------------------------------------------
# YouTube OAuth State
# -----------------------------------------------------------------------------

_OAUTH_LOCK = threading.Lock()
YOUTUBE_OAUTH_STATE: dict[str, Any] = {
    "status": "idle",
    "message": "",
    "error": None,
}


def get_youtube_oauth_state() -> dict[str, Any]:
    """Return a thread-safe copy of YOUTUBE_OAUTH_STATE."""
    with _OAUTH_LOCK:
        return dict(YOUTUBE_OAUTH_STATE)


def update_youtube_oauth_state(
    status: str, message: str = "", error: str | None = None
) -> dict[str, Any]:
    """Update YOUTUBE_OAUTH_STATE in a thread-safe manner."""
    with _OAUTH_LOCK:
        YOUTUBE_OAUTH_STATE["status"] = status
        YOUTUBE_OAUTH_STATE["message"] = message
        YOUTUBE_OAUTH_STATE["error"] = error
        return dict(YOUTUBE_OAUTH_STATE)


# -----------------------------------------------------------------------------
# Secret Masking Helper
# -----------------------------------------------------------------------------


def mask_secret(value: str | None) -> str:
    """Mask a secret key showing only the last 4 characters.

    Returns empty string if value is None or empty.
    Never exposes raw API keys in settings payloads or UI.
    """
    if not value or not str(value).strip():
        return ""
    val_str = str(value).strip()
    if len(val_str) <= 4:
        return "••••••••" + val_str
    return "••••••••" + val_str[-4:]


# -----------------------------------------------------------------------------
# Environment (.env) File Helpers
# -----------------------------------------------------------------------------


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Inside a double-quoted .env value, only \\ and \" are escapes (same as python-dotenv).
_ENV_DQ_ESCAPE_RE = re.compile(r'\\(["\\])')


def _format_env_line(key: str, value: str) -> str:
    """Render KEY="value" with backslashes and double quotes escaped."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def read_env_file(env_path: Path | str | None = None) -> dict[str, str]:
    """Read key-value pairs from .env file into a dictionary."""
    target = Path(env_path).resolve() if env_path else (PROJECT_ROOT / ".env")
    if not target.is_file():
        return {}
    res: dict[str, str] = {}
    try:
        content = target.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
                    v = _ENV_DQ_ESCAPE_RE.sub(r"\1", v[1:-1])
                elif len(v) >= 2 and v.startswith("'") and v.endswith("'"):
                    v = v[1:-1]
                res[k] = v
    except Exception as e:
        logger.warning("Failed to read env file %s: %s", target, e)
    return res


def update_env_file(
    env_path: Path | str | None,
    updates: Mapping[str, str | None],
) -> None:
    """Safely update key-value pairs in .env file atomically, updating os.environ and registering secrets."""
    target = Path(env_path).resolve() if env_path else (PROJECT_ROOT / ".env")
    lines: list[str] = []
    existing_keys: set[str] = set()

    if target.is_file():
        try:
            content = target.read_text(encoding="utf-8")
            lines = content.splitlines()
        except Exception as e:
            logger.warning("Failed to read existing env file %s: %s", target, e)
            lines = []

    # Reject anything that could start a new line in the .env file, before touching anything.
    for k, v in updates.items():
        if not _ENV_KEY_RE.match(k):
            raise ValueError(f"Invalid environment variable name: {k!r}")
        if v is not None and ("\n" in str(v) or "\r" in str(v)):
            raise ValueError(f"Value for {k} must not contain newline characters.")

    # Clean updates: discard masked strings or empty updates
    clean_updates: dict[str, str] = {}
    for k, v in updates.items():
        if v is not None:
            val_str = str(v).strip()
            if "•" in val_str or not val_str:
                continue
            clean_updates[k] = val_str
            os.environ[k] = val_str
            register_secret(val_str)

    if not clean_updates:
        return

    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            k = k.strip()
            existing_keys.add(k)
            if k in clean_updates:
                new_lines.append(_format_env_line(k, clean_updates[k]))
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    for k, val in clean_updates.items():
        if k not in existing_keys:
            new_lines.append(_format_env_line(k, val))

    out_text = "\n".join(new_lines) + "\n"
    atomic_write(target, out_text)


# -----------------------------------------------------------------------------
# YouTube Client Secret & OAuth Helpers
# -----------------------------------------------------------------------------


def is_youtube_api_configured(config: Settings | None = None) -> bool:
    """Return True if YouTube Data API v3 key or OAuth token.json exists."""
    token_p = get_data_dir() / "token.json"
    if token_p.is_file() and token_p.stat().st_size > 0:
        return True
    key = ""
    if config is not None:
        key = config.get_youtube_api_key_str() or ""
    if not key:
        key = os.environ.get("YOUTUBE_API_KEY") or ""
    return bool(key.strip())


def check_llm_provider_configured(config: Settings | None) -> tuple[bool, str, str]:
    """Verify that the active LLM provider has an API key or local model file configured.

    Returns:
        (is_configured: bool, missing_key_name: str, user_facing_message: str)
    """
    if config is None or not hasattr(config, "llm"):
        return (
            False,
            "AI_PROVIDER",
            "No AI configuration loaded. Please open Settings -> AI Providers & Keys to configure a provider.",
        )

    provider = (config.llm.provider or "local").lower()

    if provider == "gemini":
        gemini_key = config.get_gemini_api_key_str() or os.environ.get("GEMINI_API_KEY")
        if not gemini_key or not str(gemini_key).strip():
            return (
                False,
                "GEMINI_API_KEY",
                "Gemini API Key is not configured. Please open Settings -> AI Providers & Keys and insert your GEMINI_API_KEY.",
            )
        return (True, "", "")

    if provider == "openrouter":
        openrouter_key = config.get_openrouter_api_key_str() or os.environ.get("OPENROUTER_API_KEY")
        if not openrouter_key or not str(openrouter_key).strip():
            return (
                False,
                "OPENROUTER_API_KEY",
                "OpenRouter API Key is not configured. Please open Settings -> AI Providers & Keys and insert your OPENROUTER_API_KEY.",
            )
        return (True, "", "")

    if provider == "local":
        local_p = (
            config.local_gguf_path
            or os.environ.get("LOCAL_GGUF_PATH")
            or getattr(config.llm, "local_model_path", None)
        )
        if local_p and Path(local_p).is_file():
            return (True, "", "")

        # Check data/models/*.gguf
        models_dir = PROJECT_ROOT / "data" / "models"
        if models_dir.is_dir():
            gguf_files = list(models_dir.glob("*.gguf"))
            if gguf_files:
                return (True, "", "")

        return (
            False,
            "LOCAL_GGUF_PATH",
            "No local GGUF model is configured and no cloud API key is set. Please open Settings -> AI Providers & Keys to add a Gemini/OpenRouter API key or set a local GGUF model path.",
        )

    return (True, "", "")


def get_youtube_oauth_status(config: Settings | None = None) -> dict[str, Any]:
    """Check configuration and token validity for YouTube Data API v3 OAuth."""
    data_dir = get_data_dir()
    client_secret_path = data_dir / "client_secret.json"
    token_path = data_dir / "token.json"

    client_secret_configured = False
    if client_secret_path.is_file():
        try:
            data = json.loads(client_secret_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                inner = data.get("installed") or data.get("web")
                if (
                    isinstance(inner, dict)
                    and inner.get("client_id")
                    and inner.get("client_secret")
                ):
                    client_secret_configured = True
        except Exception:
            client_secret_configured = False

    token_configured = False
    if token_path.is_file() and token_path.stat().st_size > 0:
        try:
            t_data = json.loads(token_path.read_text(encoding="utf-8"))
            if isinstance(t_data, dict) and (t_data.get("token") or t_data.get("refresh_token")):
                token_configured = True
        except Exception:
            token_configured = False

    return {
        "client_secret_configured": client_secret_configured,
        "client_secret_path": str(client_secret_path),
        "token_configured": token_configured,
        "token_path": str(token_path),
        "oauth_state": get_youtube_oauth_state(),
    }


def save_youtube_client_secret(
    raw_json_or_bytes: str | bytes,
    dest_path: Path | str | None = None,
) -> dict[str, Any]:
    """Parse, validate, and atomically save Google OAuth client_secret.json.

    Raises ValueError if invalid JSON or missing required OAuth keys.
    Registers client_secret with register_secret and returns masked metadata.
    """
    target = Path(dest_path).resolve() if dest_path else (get_data_dir() / "client_secret.json")

    raw_str = (
        raw_json_or_bytes.decode("utf-8")
        if isinstance(raw_json_or_bytes, bytes)
        else str(raw_json_or_bytes)
    )

    try:
        data = json.loads(raw_str)
    except Exception as e:
        raise ValueError(f"Invalid JSON provided for client secret: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("Client secret JSON must be a JSON object.")

    inner = data.get("installed") or data.get("web")
    if not isinstance(inner, dict):
        raise ValueError("Client secret JSON must contain an 'installed' or 'web' root key.")

    client_id = inner.get("client_id")
    client_secret = inner.get("client_secret")

    if not client_id or not client_secret:
        raise ValueError("Client secret JSON missing required 'client_id' or 'client_secret'.")

    # Register secret to redact from all future logs
    register_secret(str(client_secret))

    # Atomically write to destination
    atomic_write(target, json.dumps(data, indent=2))

    return {
        "client_id_masked": mask_secret(str(client_id)),
        "project_id": str(inner.get("project_id", "")),
    }


GOOGLE_OAUTH_CLIENT_ID_KEY = "GOOGLE_OAUTH_CLIENT_ID"
GOOGLE_OAUTH_CLIENT_SECRET_KEY = "GOOGLE_OAUTH_CLIENT_SECRET"


def get_google_oauth_client() -> tuple[str, str] | None:
    """Return (client_id, client_secret) from .env or the environment, or None if incomplete."""
    values = {
        **read_env_file(None),
        **{k: v for k, v in os.environ.items() if k.startswith("GOOGLE_")},
    }
    client_id = (values.get(GOOGLE_OAUTH_CLIENT_ID_KEY) or "").strip()
    client_secret = (values.get(GOOGLE_OAUTH_CLIENT_SECRET_KEY) or "").strip()
    if client_id and client_secret:
        return client_id, client_secret
    return None


def save_google_oauth_client(client_id: str, client_secret: str) -> None:
    """Store the OAuth client in .env, so the app never needs the JSON file uploaded again."""
    update_env_file(
        None,
        {
            GOOGLE_OAUTH_CLIENT_ID_KEY: client_id.strip(),
            GOOGLE_OAUTH_CLIENT_SECRET_KEY: client_secret.strip(),
        },
    )


def ensure_client_secret_file(client_secret_path: Path | str) -> bool:
    """Make sure data/client_secret.json exists, building it from .env credentials when needed.

    Returns True when a usable client file is present afterwards.
    """
    target = Path(client_secret_path).resolve()
    if target.is_file() and target.stat().st_size > 0:
        return True
    creds = get_google_oauth_client()
    if creds is None:
        return False
    client_id, client_secret = creds
    payload = {
        "installed": {
            "client_id": client_id,
            "project_id": "autoshorts",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": client_secret,
            "redirect_uris": ["http://localhost"],
        }
    }
    atomic_write(target, json.dumps(payload, indent=2), encoding="utf-8")
    return True


def run_youtube_oauth_flow(
    client_secret_path: Path | str,
    token_path: Path | str,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    """Run interactive Google OAuth consent flow and save credentials to token.json."""
    c_path = Path(client_secret_path).resolve()
    t_path = Path(token_path).resolve()

    if scopes is None:
        scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube",
            "https://www.googleapis.com/auth/youtube.force-ssl",
            "https://www.googleapis.com/auth/yt-analytics.readonly",
        ]

    if not ensure_client_secret_file(c_path):
        err_msg = f"Client secret file not found: {c_path}"
        update_youtube_oauth_state("error", err_msg, err_msg)
        raise FileNotFoundError(err_msg)

    update_youtube_oauth_state("running", "Google OAuth consent opened in browser.", None)
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(c_path), scopes=scopes)
        # access_type=offline + prompt=consent guarantees a refresh token, so later runs
        # refresh silently instead of asking for the Google sign-in again.
        creds = flow.run_local_server(
            port=0, open_browser=True, access_type="offline", prompt="consent"
        )
        atomic_write(t_path, creds.to_json())
        update_youtube_oauth_state(
            "authorized", "Successfully authorized YouTube Data API v3.", None
        )
        return {
            "status": "authorized",
            "message": "Successfully authorized YouTube Data API v3.",
        }
    except Exception as e:
        err_msg = str(e)
        logger.error("OAuth flow failed: %s", err_msg)
        update_youtube_oauth_state("error", f"OAuth flow failed: {err_msg}", err_msg)
        raise


# -----------------------------------------------------------------------------
# Public Settings Payload & Updates
# -----------------------------------------------------------------------------


def get_public_settings_payload(
    config: Settings,
    env_path: Path | str | None = None,
) -> dict[str, Any]:
    """Return a clean dictionary of application settings with secrets masked for the UI."""
    target_env = Path(env_path).resolve() if env_path else (PROJECT_ROOT / ".env")
    env_vars = read_env_file(target_env)

    gemini_key = (
        config.get_gemini_api_key_str()
        or os.environ.get("GEMINI_API_KEY")
        or env_vars.get("GEMINI_API_KEY")
    )
    openrouter_key = (
        config.get_openrouter_api_key_str()
        or os.environ.get("OPENROUTER_API_KEY")
        or env_vars.get("OPENROUTER_API_KEY")
    )
    youtube_key = os.environ.get("YOUTUBE_API_KEY") or env_vars.get("YOUTUBE_API_KEY")
    local_gguf = (
        config.local_gguf_path
        or os.environ.get("LOCAL_GGUF_PATH")
        or env_vars.get("LOCAL_GGUF_PATH")
        or getattr(config.llm, "local_model_path", "")
    )

    sched_start = "08:00"
    sched_end = "23:59"
    if hasattr(config.scheduler, "active_window") and config.scheduler.active_window is not None:
        sched_start = getattr(config.scheduler.active_window, "start", "08:00")
        sched_end = getattr(config.scheduler.active_window, "end", "23:59")

    prevent_sleep = getattr(
        config.scheduler,
        "prevent_sleep",
        getattr(config.scheduler, "prevent_system_sleep", True),
    )

    return {
        "version": "1.0.0",
        "llm": {
            "provider": config.llm.provider,
            "model": config.llm.model,
            "timeout_seconds": getattr(config.llm, "timeout_seconds", 30),
            "max_retries": getattr(config.llm, "max_retries", 3),
        },
        "edit_planner": {
            "enabled": config.edit_planner.enabled,
            "provider": config.edit_planner.provider,
            "model": config.edit_planner.model,
        },
        "secrets": {
            "gemini_api_key_set": bool(gemini_key),
            "gemini_api_key_masked": mask_secret(gemini_key),
            "openrouter_api_key_set": bool(openrouter_key),
            "openrouter_api_key_masked": mask_secret(openrouter_key),
            "youtube_api_key_set": bool(youtube_key),
            "youtube_api_key_masked": mask_secret(youtube_key),
            "local_gguf_path": str(local_gguf or ""),
        },
        "upload": {
            "mode": config.upload.mode,
            "privacy_status": config.upload.privacy_status,
            "default_privacy_status": getattr(
                config.upload, "default_privacy_status", config.upload.privacy_status
            ),
            "quota_exhausted_fallback": getattr(
                config.upload, "quota_exhausted_fallback", "studio"
            ),
            "category_id": getattr(config.upload, "category_id", "24"),
            "default_language": getattr(config.upload, "default_language", "en"),
            "altered_content": getattr(config.upload, "altered_content", False),
            "custom_tags": getattr(config.upload, "custom_tags", []),
            "auto_pin_comment": getattr(config.upload, "auto_pin_comment", False),
            "pin_comment_template": getattr(
                config.upload,
                "pin_comment_template",
                "📌 Watch the full video by {channel}: {video_url}",
            ),
            "playlist_id": getattr(config.upload, "playlist_id", None),
            "link_related_video": getattr(config.upload, "link_related_video", True),
            "schedule_strategy": getattr(config.upload, "schedule_strategy", "local_scheduler"),
            "active_channel_profile": getattr(config.upload, "active_channel_profile", "default"),
            "made_for_kids": getattr(config.upload, "made_for_kids", False),
            "title_template": config.upload.title_template,
            "description_template": config.upload.description_template,
        },
        "scheduler": {
            "enabled": config.scheduler.enabled,
            "daily_upload_cap": config.scheduler.daily_upload_cap,
            "max_per_rolling_hour": config.scheduler.max_per_rolling_hour,
            "min_gap_minutes": config.scheduler.min_gap_minutes,
            "max_gap_minutes": config.scheduler.max_gap_minutes,
            "active_hours_start": sched_start,
            "active_hours_end": sched_end,
            "prevent_system_sleep": prevent_sleep,
        },
        "subtitles": {
            "font_name": config.subtitles.font_name,
            "font_size": config.subtitles.font_size,
            "words_per_burst": config.subtitles.words_per_burst,
            "safe_zone_margin_v": config.subtitles.safe_zone_margin_v,
            "karaoke_highlight": getattr(config.subtitles, "karaoke_highlight", True),
            "karaoke_pop_scale": getattr(config.subtitles, "karaoke_pop_scale", 118),
            "style_preset": getattr(config.subtitles, "style_preset", "neon"),
            "primary_color": getattr(config.subtitles, "primary_color", "&H00FFFFFF"),
            "active_color": getattr(config.subtitles, "active_color", "&H0000FFFF"),
            "emphasis_color": getattr(config.subtitles, "emphasis_color", "&H00FFF000"),
            "outline_color": getattr(config.subtitles, "outline_color", "&H00000000"),
            "text_transform": getattr(config.subtitles, "text_transform", "uppercase"),
            "position": getattr(config.subtitles, "position", "center"),
            "hook_banner_style": getattr(config.subtitles, "hook_banner_style", "neon_box"),
            "enable_emojis": getattr(config.subtitles, "enable_emojis", True),
        },
        "branding": {
            "layout_mode": getattr(config.branding, "layout_mode", "split_50_50"),
            "color_grading": getattr(config.branding, "color_grading", "none"),
            "watermark_text": getattr(config.branding, "watermark_text", None),
            "watermark_position": getattr(config.branding, "watermark_position", "top_right"),
            "watermark_opacity": getattr(config.branding, "watermark_opacity", 0.65),
            "progress_bar_position": getattr(config.branding, "progress_bar_position", "seam"),
            "progress_bar_color": getattr(config.branding, "progress_bar_color", "#FFE600"),
            "progress_bar_height": getattr(config.branding, "progress_bar_height", 12),
            "hook_opener": getattr(config.branding, "hook_opener", "none"),
            "outro_cta_text": getattr(config.branding, "outro_cta_text", None),
        },
        "audio_mastering": {
            "voice_boost": getattr(config.audio_mastering, "voice_boost", True),
            "target_lufs": getattr(config.audio_mastering, "target_lufs", -14.0),
            "bg_music_volume": getattr(config.audio_mastering, "bg_music_volume", 0.20),
            "enable_sfx": getattr(config.audio_mastering, "enable_sfx", True),
        },
        "job_defaults": {
            "clips_per_video": config.job_defaults.clips_per_video,
            "min_duration_seconds": config.job_defaults.min_duration_seconds,
            "max_duration_seconds": config.job_defaults.max_duration_seconds,
            "background_style": getattr(
                config.job_defaults,
                "background_category",
                getattr(config.job_defaults, "background_style", "minecraft"),
            ),
            "dopamine_level": config.job_defaults.dopamine_level,
        },
        "cleanup": {
            "enabled": config.cleanup.enabled,
            "use_recycle_bin": config.cleanup.use_recycle_bin,
            "keep_final_days": getattr(config.cleanup, "keep_final_days", 1),
            "keep_master_mp4": config.cleanup.keep_master_mp4,
            "purge_intermediate_work": config.cleanup.purge_intermediate_work,
            "dry_run": config.cleanup.dry_run,
        },
        "youtube_oauth": get_youtube_oauth_status(config),
    }


def apply_settings_update(
    payload: Mapping[str, Any],
    config_path: Path | str | None = None,
    env_path: Path | str | None = None,
) -> Settings:
    """Merge updated fields into config.yaml and .env, reloading and returning new Settings."""
    target_config = (
        Path(config_path).resolve() if config_path else (get_config_dir() / "config.yaml")
    )
    target_env = Path(env_path).resolve() if env_path else (PROJECT_ROOT / ".env")

    # Read existing YAML
    raw_yaml: dict[str, Any] = {}
    if target_config.is_file():
        try:
            with open(target_config, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    raw_yaml = loaded
        except Exception as e:
            logger.warning("Failed to load config at %s: %s", target_config, e)
    original_yaml = copy.deepcopy(raw_yaml)

    # 1. Process and update secrets in .env
    env_updates: dict[str, str] = {}
    secrets_dict = payload.get("secrets")
    if isinstance(secrets_dict, dict):
        for skey, env_key in [
            ("gemini_api_key", "GEMINI_API_KEY"),
            ("openrouter_api_key", "OPENROUTER_API_KEY"),
            ("youtube_api_key", "YOUTUBE_API_KEY"),
            ("local_gguf_path", "LOCAL_GGUF_PATH"),
        ]:
            val = secrets_dict.get(skey)
            if val and isinstance(val, str) and "•" not in val and val.strip():
                env_updates[env_key] = val.strip()

    for skey, env_key in [
        ("gemini_api_key", "GEMINI_API_KEY"),
        ("openrouter_api_key", "OPENROUTER_API_KEY"),
        ("youtube_api_key", "YOUTUBE_API_KEY"),
        ("local_gguf_path", "LOCAL_GGUF_PATH"),
    ]:
        val = payload.get(skey)
        if val and isinstance(val, str) and "•" not in val and val.strip():
            env_updates[env_key] = val.strip()

    # 2. Process subsystem sections into YAML dictionary. .env is written only after the
    # merged YAML validates (below), so a rejected save changes neither file.

    # LLM
    if "llm" in payload and isinstance(payload["llm"], dict):
        raw_yaml.setdefault("llm", {})
        llm_data = payload["llm"]
        if "provider" in llm_data and llm_data["provider"] is not None:
            raw_yaml["llm"]["provider"] = str(llm_data["provider"])
        if "model" in llm_data:
            m = llm_data["model"]
            if m in (None, "", "null", "auto", "None"):
                raw_yaml["llm"]["model"] = None
            else:
                raw_yaml["llm"]["model"] = str(m)
        if "n_threads" in llm_data and llm_data["n_threads"] is not None:
            raw_yaml["llm"]["n_threads"] = int(llm_data["n_threads"])
        if "temperature" in llm_data and llm_data["temperature"] is not None:
            raw_yaml["llm"]["temperature"] = float(llm_data["temperature"])

    # Edit Planner
    if "edit_planner" in payload and isinstance(payload["edit_planner"], dict):
        raw_yaml.setdefault("edit_planner", {})
        ep_data = payload["edit_planner"]
        if "enabled" in ep_data and ep_data["enabled"] is not None:
            raw_yaml["edit_planner"]["enabled"] = bool(ep_data["enabled"])
        if "provider" in ep_data and ep_data["provider"] is not None:
            raw_yaml["edit_planner"]["provider"] = str(ep_data["provider"])
        if "model" in ep_data:
            m = ep_data["model"]
            if m in (None, "", "null", "auto", "None"):
                raw_yaml["edit_planner"]["model"] = None
            else:
                raw_yaml["edit_planner"]["model"] = str(m)

    # Upload
    if "upload" in payload and isinstance(payload["upload"], dict):
        raw_yaml.setdefault("upload", {})
        up_data = payload["upload"]
        for k in (
            "mode",
            "privacy_status",
            "default_privacy_status",
            "quota_exhausted_fallback",
            "category_id",
            "default_language",
            "altered_content",
            "custom_tags",
            "auto_pin_comment",
            "pin_comment_template",
            "playlist_id",
            "link_related_video",
            "schedule_strategy",
            "active_channel_profile",
            "made_for_kids",
            "title_template",
            "description_template",
        ):
            if k in up_data and up_data[k] is not None:
                raw_yaml["upload"][k] = up_data[k]
        if "privacy_status" in up_data and "default_privacy_status" not in up_data:
            raw_yaml["upload"]["default_privacy_status"] = up_data["privacy_status"]
        elif "default_privacy_status" in up_data and "privacy_status" not in up_data:
            raw_yaml["upload"]["privacy_status"] = up_data["default_privacy_status"]

    # Scheduler
    if "scheduler" in payload and isinstance(payload["scheduler"], dict):
        raw_yaml.setdefault("scheduler", {})
        sc_data = payload["scheduler"]
        for k in (
            "enabled",
            "daily_upload_cap",
            "max_per_rolling_hour",
            "min_gap_minutes",
            "max_gap_minutes",
            "prevent_sleep",
        ):
            if k in sc_data and sc_data[k] is not None:
                raw_yaml["scheduler"][k] = sc_data[k]
        if "prevent_system_sleep" in sc_data and sc_data["prevent_system_sleep"] is not None:
            raw_yaml["scheduler"]["prevent_sleep"] = bool(sc_data["prevent_system_sleep"])
        if "active_hours_start" in sc_data or "active_hours_end" in sc_data:
            raw_yaml["scheduler"].setdefault("active_window", {})
            if "active_hours_start" in sc_data and sc_data["active_hours_start"] is not None:
                raw_yaml["scheduler"]["active_window"]["start"] = str(sc_data["active_hours_start"])
            if "active_hours_end" in sc_data and sc_data["active_hours_end"] is not None:
                raw_yaml["scheduler"]["active_window"]["end"] = str(sc_data["active_hours_end"])

    # Subtitles
    if "subtitles" in payload and isinstance(payload["subtitles"], dict):
        raw_yaml.setdefault("subtitles", {})
        sub_data = payload["subtitles"]
        for k in (
            "font_name",
            "font_size",
            "words_per_burst",
            "safe_zone_margin_v",
            "karaoke_highlight",
            "karaoke_pop_scale",
            "style_preset",
            "primary_color",
            "active_color",
            "emphasis_color",
            "outline_color",
            "text_transform",
            "position",
            "hook_banner_style",
            "enable_emojis",
        ):
            if k in sub_data and sub_data[k] is not None:
                raw_yaml["subtitles"][k] = sub_data[k]

    # Branding
    if "branding" in payload and isinstance(payload["branding"], dict):
        raw_yaml.setdefault("branding", {})
        br_data = payload["branding"]
        for k in (
            "layout_mode",
            "color_grading",
            "watermark_text",
            "watermark_position",
            "watermark_opacity",
            "progress_bar_position",
            "progress_bar_color",
            "progress_bar_height",
            "hook_opener",
            "outro_cta_text",
        ):
            if k in br_data and br_data[k] is not None:
                raw_yaml["branding"][k] = br_data[k]

    # Audio Mastering
    if "audio_mastering" in payload and isinstance(payload["audio_mastering"], dict):
        raw_yaml.setdefault("audio_mastering", {})
        am_data = payload["audio_mastering"]
        for k in (
            "voice_boost",
            "target_lufs",
            "bg_music_volume",
            "enable_sfx",
        ):
            if k in am_data and am_data[k] is not None:
                raw_yaml["audio_mastering"][k] = am_data[k]

    # Job Defaults
    if "job_defaults" in payload and isinstance(payload["job_defaults"], dict):
        raw_yaml.setdefault("job_defaults", {})
        jd_data = payload["job_defaults"]
        for k in (
            "clips_per_video",
            "min_duration_seconds",
            "max_duration_seconds",
            "window_stride_seconds",
            "focus_prompt",
            "dopamine_level",
        ):
            if k in jd_data and jd_data[k] is not None:
                raw_yaml["job_defaults"][k] = jd_data[k]
        if "background_style" in jd_data and jd_data["background_style"] is not None:
            raw_yaml["job_defaults"]["background_category"] = str(jd_data["background_style"])
        if "background_category" in jd_data and jd_data["background_category"] is not None:
            raw_yaml["job_defaults"]["background_category"] = str(jd_data["background_category"])

    # Cleanup
    if "cleanup" in payload and isinstance(payload["cleanup"], dict):
        raw_yaml.setdefault("cleanup", {})
        cl_data = payload["cleanup"]
        for k in (
            "enabled",
            "use_recycle_bin",
            "keep_final_days",
            "keep_master_mp4",
            "purge_intermediate_work",
            "dry_run",
        ):
            if k in cl_data and cl_data[k] is not None:
                if k == "keep_final_days":
                    try:
                        raw_yaml["cleanup"][k] = int(cl_data[k])
                    except (ValueError, TypeError):
                        raw_yaml["cleanup"][k] = 1
                else:
                    raw_yaml["cleanup"][k] = cl_data[k]

    # Validate the merged settings before writing either file. A value the schema rejects
    # (for example upload.mode "bogus") would otherwise stay in config.yaml and make the
    # next start fail in load_config.
    # Validate a copy: UploadConfig's before-validator writes into the dict it receives.
    try:
        Settings(**copy.deepcopy(raw_yaml))
    except Exception as e:
        raise ValueError(f"Settings were not saved: {e}") from e

    if env_updates:
        update_env_file(target_env, env_updates)

    # Rewrite config.yaml only when a value changed. yaml.safe_dump drops every comment,
    # so a no-op save must leave the hand-written file byte-identical.
    if raw_yaml != original_yaml or not target_config.is_file():
        out_yaml = yaml.safe_dump(raw_yaml, sort_keys=False)
        atomic_write(target_config, out_yaml)

    # Reload new settings
    new_settings = load_config(target_config)
    # Ensure any non-model config fields remain accessible
    if "subtitles" in raw_yaml and "style_preset" in raw_yaml["subtitles"]:
        new_settings.subtitles.style_preset = raw_yaml["subtitles"]["style_preset"]
    if "cleanup" in raw_yaml and "keep_final_days" in raw_yaml["cleanup"]:
        new_settings.cleanup.keep_final_days = int(raw_yaml["cleanup"]["keep_final_days"])

    return new_settings


# -----------------------------------------------------------------------------
# Studio Profiles (Presets) & Channel Profiles Management
# -----------------------------------------------------------------------------

FACTORY_STUDIO_PROFILES: dict[str, dict[str, Any]] = {
    "viral_split": {
        "id": "viral_split",
        "name": "Viral Split",
        "description": "50/50 split-screen with neon pop subtitles and vibrant color grading",
        "layout_mode": "split_50_50",
        "style_preset": "neon",
        "color_grading": "vibrant",
        "progress_bar_position": "seam",
        "progress_bar_color": "#FFE600",
        "hook_opener": "zoom_burst",
        "text_transform": "uppercase",
        "subtitle_position": "center",
        "hook_banner_style": "neon_box",
        "enable_emojis": True,
        "is_factory": True,
    },
    "podcast_blur": {
        "id": "podcast_blur",
        "name": "Podcast Blur",
        "description": "Gaussian blurred backdrop with clean minimal captions and warm grading",
        "layout_mode": "full_blur_bg",
        "style_preset": "minimal",
        "color_grading": "warm",
        "progress_bar_position": "bottom",
        "progress_bar_color": "#00FF66",
        "hook_opener": "none",
        "text_transform": "title",
        "subtitle_position": "bottom_safe",
        "hook_banner_style": "minimal",
        "enable_emojis": False,
        "is_factory": True,
    },
    "gaming_dopamine": {
        "id": "gaming_dopamine",
        "name": "Gaming Dopamine",
        "description": "High retention 50/50 split with Hormozi captions and flash hook opener",
        "layout_mode": "split_50_50",
        "style_preset": "hormozi",
        "color_grading": "vibrant",
        "progress_bar_position": "seam",
        "progress_bar_color": "#FF0055",
        "hook_opener": "flash",
        "text_transform": "uppercase",
        "subtitle_position": "center",
        "hook_banner_style": "neon_box",
        "enable_emojis": True,
        "is_factory": True,
    },
    "cinematic_full": {
        "id": "cinematic_full",
        "name": "Cinematic Full",
        "description": "Full vertical center crop with cinematic serif captions and subtle tone",
        "layout_mode": "full_center_crop",
        "style_preset": "cinematic",
        "color_grading": "cinematic",
        "progress_bar_position": "none",
        "progress_bar_color": "#FFE600",
        "hook_opener": "none",
        "text_transform": "none",
        "subtitle_position": "bottom_safe",
        "hook_banner_style": "none",
        "enable_emojis": False,
        "is_factory": True,
    },
    "pip_reaction": {
        "id": "pip_reaction",
        "name": "PiP Reaction",
        "description": "Picture-in-picture gameplay overlay with MrBeast captions and crisp grade",
        "layout_mode": "pip_gameplay",
        "style_preset": "mrbeast",
        "color_grading": "crisp",
        "progress_bar_position": "bottom",
        "progress_bar_color": "#FFB800",
        "hook_opener": "zoom_burst",
        "text_transform": "uppercase",
        "subtitle_position": "center",
        "hook_banner_style": "pill",
        "enable_emojis": True,
        "is_factory": True,
    },
}


def get_studio_profiles(data_dir: Path | None = None) -> dict[str, Any]:
    """Return built-in factory studio profiles merged with custom user profiles."""
    d_dir = Path(data_dir).resolve() if data_dir else get_data_dir()
    profiles_file = d_dir / "studio_profiles.json"

    all_profiles: dict[str, Any] = {k: dict(v) for k, v in FACTORY_STUDIO_PROFILES.items()}

    if profiles_file.is_file():
        try:
            content = profiles_file.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict):
                        item = dict(v)
                        item.setdefault("id", k)
                        item["is_factory"] = False
                        all_profiles[k] = item
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "id" in item:
                        p = dict(item)
                        p["is_factory"] = False
                        all_profiles[p["id"]] = p
        except Exception as e:
            logger.warning("Failed to load studio profiles from %s: %s", profiles_file, e)

    return all_profiles


def save_studio_profile(
    profile_data: Mapping[str, Any], data_dir: Path | None = None
) -> dict[str, Any]:
    """Persist a custom studio profile to data/studio_profiles.json."""
    d_dir = Path(data_dir).resolve() if data_dir else get_data_dir()
    profiles_file = d_dir / "studio_profiles.json"

    custom_profiles: dict[str, Any] = {}
    if profiles_file.is_file():
        try:
            content = profiles_file.read_text(encoding="utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                custom_profiles = data
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "id" in item:
                        custom_profiles[item["id"]] = item
        except Exception as e:
            logger.warning("Failed to read existing studio profiles from %s: %s", profiles_file, e)
            custom_profiles = {}

    profile_dict = dict(profile_data)
    profile_id = str(profile_dict.get("id") or "").strip()
    if not profile_id:
        name = str(profile_dict.get("name", "custom_profile")).lower()
        profile_id = re.sub(r"[^a-z0-9_]+", "_", name).strip("_") or "custom_profile"

    profile_dict["id"] = profile_id
    profile_dict["is_factory"] = False
    custom_profiles[profile_id] = profile_dict

    atomic_write(profiles_file, json.dumps(custom_profiles, indent=2))
    return profile_dict


def get_channel_profiles(data_dir: Path | None = None) -> list[dict[str, Any]]:
    """Return all configured channel profiles from data/channel_profiles.json."""
    d_dir = Path(data_dir).resolve() if data_dir else get_data_dir()
    profiles_file = d_dir / "channel_profiles.json"

    default_profile = ChannelProfile().model_dump()

    if not profiles_file.is_file():
        return [default_profile]

    try:
        content = profiles_file.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, list) and len(data) > 0:
            profiles = []
            for item in data:
                if isinstance(item, dict):
                    try:
                        p = ChannelProfile(**item).model_dump()
                        profiles.append(p)
                    except Exception:
                        profiles.append(dict(item))
            if profiles:
                return profiles
        elif isinstance(data, dict):
            profiles = []
            for item in data.values():
                if isinstance(item, dict):
                    try:
                        p = ChannelProfile(**item).model_dump()
                        profiles.append(p)
                    except Exception:
                        profiles.append(dict(item))
            if profiles:
                return profiles
    except Exception as e:
        logger.warning("Failed to load channel profiles from %s: %s", profiles_file, e)

    return [default_profile]


def save_channel_profile(
    profile_data: Mapping[str, Any], data_dir: Path | None = None
) -> dict[str, Any]:
    """Persist or update a channel profile in data/channel_profiles.json."""
    d_dir = Path(data_dir).resolve() if data_dir else get_data_dir()
    profiles_file = d_dir / "channel_profiles.json"

    current_profiles = get_channel_profiles(data_dir=d_dir)

    new_profile = ChannelProfile(**profile_data).model_dump()
    target_id = new_profile["id"]

    updated = False
    for i, p in enumerate(current_profiles):
        if p.get("id") == target_id:
            current_profiles[i] = new_profile
            updated = True
            break

    if not updated:
        current_profiles.append(new_profile)

    atomic_write(profiles_file, json.dumps(current_profiles, indent=2))
    return new_profile


__all__ = [
    "FACTORY_STUDIO_PROFILES",
    "YOUTUBE_OAUTH_STATE",
    "apply_settings_update",
    "check_llm_provider_configured",
    "get_channel_profiles",
    "get_public_settings_payload",
    "get_studio_profiles",
    "get_youtube_oauth_state",
    "get_youtube_oauth_status",
    "is_youtube_api_configured",
    "mask_secret",
    "read_env_file",
    "run_youtube_oauth_flow",
    "save_channel_profile",
    "save_studio_profile",
    "save_youtube_client_secret",
    "update_env_file",
    "update_youtube_oauth_state",
]
