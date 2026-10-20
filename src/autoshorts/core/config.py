"""Configuration system using Pydantic Settings and YAML.

Loads settings from config.yaml and environment variables (.env).
Enforces zero hardcoded models (model: None by default), strictly manages secrets,
and provides redaction capabilities.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from autoshorts.core.exceptions import ConfigError
from autoshorts.core.logging_setup import redact, register_secret
from autoshorts.core.paths import PROJECT_ROOT, get_config_dir

LayoutMode = Literal[
    "split_50_50", "split_70_30", "full_blur_bg", "full_center_crop", "pip_gameplay"
]
SubtitlePreset = Literal["neon", "minimal", "pill", "hormozi", "mrbeast", "cyberpunk", "cinematic"]
ColorGradingPreset = Literal["none", "vibrant", "cinematic", "warm", "crisp"]
ProgressBarPosition = Literal["top", "bottom", "seam", "none"]
TextTransform = Literal["uppercase", "title", "none"]
SubtitlePosition = Literal["bottom_safe", "center", "top_safe"]
HookBannerStyle = Literal["neon_box", "pill", "minimal", "none"]


class AppConfig(BaseModel):
    """Application web server and UI parameters."""

    name: str = "AutoShorts-OnDevice"
    version: str = "1.0.0"
    port: int = 8765
    host: str = "127.0.0.1"
    auto_open_browser: bool = True


class LLMConfig(BaseModel):
    """LLM provider parameters. Model MUST default to None (zero hardcoding)."""

    provider: Literal["local", "gemini", "openrouter"] = "local"
    model: str | None = None  # None = dynamically resolved at runtime
    local_model_path: str = "data/models/Qwen3-0.6B-Instruct-Q8_0.gguf"
    n_threads: int = Field(default=4, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class JobDefaultsConfig(BaseModel):
    """Default parameters applied to newly submitted video clipping jobs."""

    clips_per_video: int = Field(default=1, ge=1)
    min_duration_seconds: int = Field(default=30, ge=10)
    max_duration_seconds: int = Field(default=50, le=60)
    window_stride_seconds: int = Field(default=15, ge=5)
    focus_prompt: str = "Identify the most compelling, high-retention standalone insight or moment."
    background_category: str = "minecraft"
    dopamine_level: Literal["low", "medium", "high"] = "medium"
    auto_approve: bool = False


class EditPlannerConfig(BaseModel):
    """Multimodal AI edit planning stage configuration."""

    enabled: bool = True
    provider: str = "gemini_video"
    model: str | None = None  # None = resolved dynamically at runtime
    fallback_to_transcript: bool = True
    max_video_duration_for_analysis_sec: int = 180


class ActiveWindowConfig(BaseModel):
    """Daily daytime active window during which uploads may be dispatched."""

    start: str = "08:00"
    end: str = "23:59"
    timezone: str = "local"


class UploadConfig(BaseModel):
    """YouTube upload protocol, visibility, and attribution templates."""

    mode: Literal["api", "studio", "local"] = "api"
    privacy_status: Literal["private", "unlisted", "public"] = "unlisted"
    default_privacy_status: str = "unlisted"
    quota_exhausted_fallback: str = "studio"
    category_id: str = "24"
    default_language: str = "en"
    altered_content: bool = False
    custom_tags: list[str] = Field(default_factory=list)
    auto_pin_comment: bool = False
    pin_comment_template: str = "📌 Watch the full video by {channel}: {video_url}"
    playlist_id: str | None = None
    link_related_video: bool = True
    schedule_strategy: str = "local_scheduler"
    active_channel_profile: str = "default"
    title_template: str = "{hook} | {channel} #Shorts"
    description_template: str = (
        "{hook}\n\n"
        "---\n"
        "📌 ORIGINAL SOURCE ATTRIBUTION:\n"
        "This video is an educational commentary clip excerpted from:\n"
        'Video Title: "{title}"\n'
        "Original Creator: {channel}\n"
        "Original Video: {video_url}\n"
        "Channel Link: {channel_url}\n\n"
        "All rights and intellectual property belong to {channel}. If you are the copyright owner "
        "and wish for this clip to be removed or modified, please contact the channel owner.\n\n"
        "#Shorts #Podcasts #Education {hashtags}"
    )

    @model_validator(mode="before")
    @classmethod
    def _sync_privacy_defaults(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "default_privacy_status" in data and "privacy_status" not in data:
                data["privacy_status"] = data["default_privacy_status"]
            elif "privacy_status" in data and "default_privacy_status" not in data:
                data["default_privacy_status"] = data["privacy_status"]
        return data


class SchedulerConfig(BaseModel):
    """Daily Cap and Persistent Spread Scheduler configuration."""

    enabled: bool = True
    daily_upload_cap: int = Field(default=50, ge=1, le=100)
    max_per_rolling_hour: int = Field(default=7, ge=1, le=20)
    min_gap_minutes: int = Field(default=5, ge=1)
    max_gap_minutes: int = Field(default=25, ge=2)
    active_window: ActiveWindowConfig = Field(default_factory=ActiveWindowConfig)
    quota_reset_timezone: str = "America/Los_Angeles"
    prevent_sleep: bool = True
    catch_up_policy: Literal["reschedule_respect_rate_limit", "drop_expired"] = (
        "reschedule_respect_rate_limit"
    )
    upload: UploadConfig = Field(default_factory=UploadConfig)


class QuotaConfig(BaseModel):
    """YouTube API granular quota bucket tracking parameters."""

    upload_bucket_daily: int = Field(default=100, description="Dedicated videos.insert calls/day")
    general_bucket_daily: int = Field(default=10000, description="Shared general units/day")
    reserve_retries: int = Field(
        default=50, description="Calls reserved for retries in upload bucket"
    )
    insert_cost_units: int = Field(default=1, description="videos.insert cost in upload bucket")
    list_cost_units: int = Field(
        default=1, description="videos.list verification cost in general bucket"
    )
    stop_threshold_units: int = Field(default=100, description="General bucket reserve threshold")
    exhaustion_fallback: Literal["queue_next_day", "studio", "local"] = "queue_next_day"


class SparkConfig(BaseModel):
    """Gemini Spark companion inbox configuration."""

    inbox_dir: str = "inbox"
    watch_inbox: bool = True
    poll_interval_sec: int = 10


class MediaConfig(BaseModel):
    """Video encoding, whisper transcription, and compositing parameters."""

    preferred_encoder: Literal["auto", "h264_qsv", "libx264"] = "auto"
    whisper_model: str = "base.en"
    fps: int = 30
    crf: int = 22
    preset: str = "veryfast"
    generate_lowres_preview: bool = True
    cookies_from_browser: str | None = None


class VisualBrandingConfig(BaseModel):
    """Visual framing, color grading, watermark, and progress bar branding options."""

    layout_mode: str = "split_50_50"
    color_grading: str = "none"
    watermark_text: str | None = None
    watermark_position: str = "top_right"  # top_right, top_left, bottom_right, bottom_center
    watermark_opacity: float = Field(default=0.65, ge=0.1, le=1.0)
    progress_bar_position: str = "seam"
    progress_bar_color: str = "#FFE600"
    progress_bar_height: int = Field(default=12, ge=4, le=24)
    hook_opener: str = "none"  # none, zoom_burst, flash
    outro_cta_text: str | None = None


class AudioMasteringConfig(BaseModel):
    """Audio mastering, voice boost EQ, and sound effect parameters."""

    voice_boost: bool = True
    target_lufs: float = Field(default=-14.0, ge=-24.0, le=-9.0)
    bg_music_volume: float = Field(default=0.20, ge=0.0, le=0.50)
    enable_sfx: bool = True


class SubtitlesConfig(BaseModel):
    """Karaoke ASS subtitle styling and safe-zone positioning."""

    font_name: str = "Montserrat ExtraBold"
    font_size: int = 76
    words_per_burst: int = 3
    karaoke_highlight: bool = True
    active_color: str = "&H0000FFFF"
    primary_color: str = "&H00FFFFFF"
    emphasis_color: str = "&H00FFF000"
    outline_color: str = "&H00000000"
    safe_zone_margin_v: int = 780
    style_preset: str = Field(
        default="neon",
        description="Subtitle visual preset: neon, minimal, pill, hormozi, mrbeast, cyberpunk, cinematic",
    )
    text_transform: str = "uppercase"
    position: str = "center"
    hook_banner_style: str = "neon_box"
    enable_emojis: bool = True
    karaoke_pop_scale: int = 118


class CleanupConfig(BaseModel):
    """Intermediate file purge and safety retention parameters."""

    enabled: bool = True
    use_recycle_bin: bool = True
    keep_master_mp4: bool = True
    purge_intermediate_work: bool = True
    dry_run: bool = False


class Settings(BaseSettings):
    """Master Application Settings loaded from config.yaml and environment."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Subsystem configurations
    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    job_defaults: JobDefaultsConfig = Field(default_factory=JobDefaultsConfig)
    edit_planner: EditPlannerConfig = Field(default_factory=EditPlannerConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    quota: QuotaConfig = Field(default_factory=QuotaConfig)
    spark: SparkConfig = Field(default_factory=SparkConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    subtitles: SubtitlesConfig = Field(default_factory=SubtitlesConfig)
    branding: VisualBrandingConfig = Field(default_factory=VisualBrandingConfig)
    audio_mastering: AudioMasteringConfig = Field(default_factory=AudioMasteringConfig)
    upload: UploadConfig = Field(default_factory=UploadConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)

    # Secrets (strictly from environment or .env, never hardcoded)
    app_env: str = Field(default="production", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    openrouter_api_key: SecretStr | None = Field(default=None, alias="OPENROUTER_API_KEY")
    youtube_api_key: SecretStr | None = Field(default=None, alias="YOUTUBE_API_KEY")
    local_gguf_path: str | None = Field(default=None, alias="LOCAL_GGUF_PATH")

    def _register_loaded_secrets(self) -> None:
        """Register loaded secrets for automatic redaction in logs."""
        if self.gemini_api_key:
            register_secret(self.gemini_api_key.get_secret_value())
        if self.openrouter_api_key:
            register_secret(self.openrouter_api_key.get_secret_value())
        if self.youtube_api_key:
            register_secret(self.youtube_api_key.get_secret_value())

    def model_post_init(self, context: Any, /) -> None:
        """Register loaded secrets for automatic redaction in logs."""
        self._register_loaded_secrets()

    def get_gemini_api_key_str(self) -> str | None:
        """Return the unmasked Gemini API key string, or None if not set."""
        return self.gemini_api_key.get_secret_value() if self.gemini_api_key else None

    def get_openrouter_api_key_str(self) -> str | None:
        """Return the unmasked OpenRouter API key string, or None if not set."""
        return self.openrouter_api_key.get_secret_value() if self.openrouter_api_key else None

    def get_youtube_api_key_str(self) -> str | None:
        """Return the unmasked YouTube API key string, or None if not set."""
        return self.youtube_api_key.get_secret_value() if self.youtube_api_key else None


def load_config(config_path: Path | str | None = None) -> Settings:
    """Load, parse, and validate application settings from YAML and environment.

    Args:
        config_path: Optional explicit path to config.yaml. Defaults to config/config.yaml.

    Returns:
        Validated Settings instance.
    """
    target = Path(config_path).resolve() if config_path else (get_config_dir() / "config.yaml")

    raw_yaml: dict[str, Any] = {}
    if target.is_file():
        try:
            with open(target, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    raw_yaml = loaded
        except Exception as e:
            raise ConfigError(f"Failed to read configuration YAML at {target}: {e}") from e

    # Instantiate Settings passing the parsed YAML dictionary
    try:
        settings = Settings(**raw_yaml)
        return settings
    except Exception as e:
        raise ConfigError(f"Configuration validation failed: {e}") from e


# Re-export redaction helper for callers
__all__ = [
    "ActiveWindowConfig",
    "AppConfig",
    "AudioMasteringConfig",
    "CleanupConfig",
    "ColorGradingPreset",
    "ConfigError",
    "EditPlannerConfig",
    "HookBannerStyle",
    "JobDefaultsConfig",
    "LLMConfig",
    "LayoutMode",
    "MediaConfig",
    "ProgressBarPosition",
    "QuotaConfig",
    "SchedulerConfig",
    "Settings",
    "SparkConfig",
    "SubtitlePosition",
    "SubtitlePreset",
    "SubtitlesConfig",
    "TextTransform",
    "UploadConfig",
    "VisualBrandingConfig",
    "load_config",
    "redact",
]
