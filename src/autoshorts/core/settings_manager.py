"""Settings and YouTube Data API v3 configuration manager for AutoShorts.

Provides safe persistence for application configuration (config.yaml), environment secrets (.env),
OAuth client credentials verification and authorization flow, and sanitized payloads for the Dashboard UI.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from autoshorts.core.config import Settings, load_config
from autoshorts.core.logging_setup import register_secret
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
                if (v.startswith('"') and v.endswith('"')) or (
                    v.startswith("'") and v.endswith("'")
                ):
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
                val = clean_updates[k]
                new_lines.append(f'{k}="{val}"')
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    for k, val in clean_updates.items():
        if k not in existing_keys:
            new_lines.append(f'{k}="{val}"')

    out_text = "\n".join(new_lines) + "\n"
    atomic_write(target, out_text)


# -----------------------------------------------------------------------------
# YouTube Client Secret & OAuth Helpers
# -----------------------------------------------------------------------------


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
        ]

    if not c_path.is_file():
        err_msg = f"Client secret file not found: {c_path}"
        update_youtube_oauth_state("error", err_msg, err_msg)
        raise FileNotFoundError(err_msg)

    update_youtube_oauth_state("running", "Google OAuth consent opened in browser.", None)
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(c_path), scopes=scopes)
        creds = flow.run_local_server(port=0, open_browser=True)
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
            "karaoke_pop_scale": getattr(config.subtitles, "karaoke_pop_scale", 1.15),
            "style_preset": getattr(config.subtitles, "style_preset", "neon"),
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
            "keep_final_days": getattr(config.cleanup, "keep_final_days", 3),
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

    if env_updates:
        update_env_file(target_env, env_updates)

    # 2. Process subsystem sections into YAML dictionary

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
            "karaoke_pop_scale",
            "style_preset",
        ):
            if k in sub_data and sub_data[k] is not None:
                raw_yaml["subtitles"][k] = sub_data[k]

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
                raw_yaml["cleanup"][k] = cl_data[k]

    # Save to config.yaml atomically
    out_yaml = yaml.safe_dump(raw_yaml, sort_keys=False)
    atomic_write(target_config, out_yaml)

    # Reload new settings
    new_settings = load_config(target_config)
    # Ensure any non-model config fields (like style_preset or keep_final_days) remain accessible
    if "subtitles" in raw_yaml and "style_preset" in raw_yaml["subtitles"]:
        new_settings.subtitles.style_preset = raw_yaml["subtitles"]["style_preset"]
    if "cleanup" in raw_yaml and "keep_final_days" in raw_yaml["cleanup"]:
        new_settings.cleanup.keep_final_days = raw_yaml["cleanup"]["keep_final_days"]

    return new_settings


__all__ = [
    "YOUTUBE_OAUTH_STATE",
    "apply_settings_update",
    "get_public_settings_payload",
    "get_youtube_oauth_state",
    "get_youtube_oauth_status",
    "mask_secret",
    "read_env_file",
    "run_youtube_oauth_flow",
    "save_youtube_client_secret",
    "update_env_file",
    "update_youtube_oauth_state",
]
