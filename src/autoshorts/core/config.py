"""Configuration system using Pydantic Settings and YAML.

Loads settings from config.yaml and environment variables (.env).
Enforces zero hardcoded models (model: None by default), strictly manages secrets,
and provides redaction capabilities.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from autoshorts.core.exceptions import ConfigError
from autoshorts.core.logging_setup import redact, register_secret
from autoshorts.core.paths import PROJECT_ROOT, get_config_dir

LayoutMode = Literal[
    "split_50_50",
    "split_70_30",
    "full_blur_bg",
    "full_center_crop",
    "pip_gameplay",
    "cam_gameplay",
]
SubtitlePreset = Literal[
    "neon", "minimal", "pill", "hormozi", "mrbeast", "cyberpunk", "cinematic", "rapid", "kurz"
]
ColorGradingPreset = Literal["none", "vibrant", "cinematic", "warm", "crisp"]
ProgressBarPosition = Literal["top", "bottom", "seam", "none"]
TextTransform = Literal["uppercase", "title", "none"]
SubtitlePosition = Literal["bottom_safe", "center", "top_safe"]
HookBannerStyle = Literal["neon_box", "pill", "minimal", "none"]


class AppConfig(BaseModel):
    """Application web server and UI parameters."""

    model_config = ConfigDict(extra="ignore")

    name: str = "AutoShorts-OnDevice"
    version: str = "1.0.0"
    port: int = 8765
    host: str = "127.0.0.1"
    auto_open_browser: bool = True


class LLMConfig(BaseModel):
    """LLM provider parameters. Model MUST default to None (zero hardcoding)."""

    model_config = ConfigDict(extra="ignore")

    provider: Literal["local", "gemini", "openrouter"] = "local"
    model: str | None = None  # None = dynamically resolved at runtime
    local_model_path: str = "data/models/Qwen3-0.6B-Instruct-Q8_0.gguf"
    n_threads: int = Field(default=4, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class JobDefaultsConfig(BaseModel):
    """Default parameters applied to newly submitted video clipping jobs."""

    model_config = ConfigDict(extra="ignore")

    clips_per_video: int = Field(default=1, ge=1)
    min_duration_seconds: int = Field(default=30, ge=10)
    max_duration_seconds: int = Field(default=50, le=60)
    window_stride_seconds: int = Field(default=15, ge=5)
    focus_prompt: str = "Identify the most compelling, high-retention standalone insight or moment."
    background_category: str = "minecraft"
    background_style: str = "minecraft"
    dopamine_level: Literal["low", "medium", "high"] = "medium"
    auto_approve: bool = False
    enable_chyron: bool = False
    chyron_badge: str = "BREAKING NEWS"
    chyron_style: str = "broadcast_red"


class EditPlannerConfig(BaseModel):
    """Multimodal AI edit planning stage configuration."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    provider: str = "gemini_video"
    model: str | None = None  # None = resolved dynamically at runtime
    fallback_to_transcript: bool = True
    max_video_duration_for_analysis_sec: int = 180


class ActiveWindowConfig(BaseModel):
    """Daily daytime active window during which uploads may be dispatched."""

    model_config = ConfigDict(extra="ignore")

    start: str = "08:00"
    end: str = "23:59"
    timezone: str = "local"


class UploadConfig(BaseModel):
    """YouTube upload protocol, visibility, and attribution templates."""

    model_config = ConfigDict(extra="ignore")

    mode: Literal["api", "studio", "local"] = "api"
    longform_daily_cap: int = 20
    privacy_status: Literal["private", "unlisted", "public"] = "unlisted"
    default_privacy_status: str = "unlisted"
    quota_exhausted_fallback: str = "studio"
    category_id: str = "24"
    default_language: str = "en"
    altered_content: bool = False
    # "auto" discloses when the job records realistic synthetic media; see scheduler/originality.py
    synthetic_disclosure: Literal["auto", "always", "never"] = "auto"
    custom_tags: list[str] = Field(default_factory=list)
    auto_pin_comment: bool = False
    pin_comment_template: str = "📌 Watch the full video by {channel}: {video_url}"
    playlist_id: str | None = None
    link_related_video: bool = True
    schedule_strategy: str = "local_scheduler"
    active_channel_profile: str = "default"
    made_for_kids: bool = False
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
        "#Podcasts #Education {hashtags}"
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


class OriginalityConfig(BaseModel):
    """Channel-level guardrails against mass-produced / repetitive output."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # Effective daily cap is min(scheduler.daily_upload_cap, max_daily_uploads).
    max_daily_uploads: int = Field(default=12, ge=1, le=100)
    # Titles with content-word overlap at/above this score are flagged as repetitive.
    max_title_similarity: float = Field(default=0.7, ge=0.1, le=1.0)
    similarity_lookback: int = Field(default=30, ge=1, le=500)
    # True: delay near-duplicate titles out of the queue; False: only log and record a flag.
    block_similar: bool = False


class SchedulerConfig(BaseModel):
    """Daily Cap and Persistent Spread Scheduler configuration."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    daily_upload_cap: int = Field(default=40, ge=1, le=100)
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
    originality: OriginalityConfig = Field(default_factory=OriginalityConfig)


class QuotaConfig(BaseModel):
    """YouTube API granular quota bucket tracking parameters."""

    model_config = ConfigDict(extra="ignore")

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

    model_config = ConfigDict(extra="ignore")

    inbox_dir: str = "inbox"
    watch_inbox: bool = True
    poll_interval_sec: int = 10


class MediaConfig(BaseModel):
    """Video encoding, whisper transcription, and compositing parameters."""

    model_config = ConfigDict(extra="ignore")

    preferred_encoder: Literal["auto", "h264_qsv", "libx264"] = "auto"
    whisper_model: str = "base.en"
    fps: int = 30
    crf: int = 22
    preset: str = "veryfast"
    generate_lowres_preview: bool = True
    cookies_from_browser: str | None = None


def _check_norm_rect(value: tuple[float, float, float, float] | None, name: str):
    """A normalized (x, y, w, h) rect must lie inside the unit square with positive area."""
    if value is None:
        return None
    x, y, w, h = value
    if min(x, y) < 0 or w <= 0 or h <= 0 or x + w > 1.0 + 1e-6 or y + h > 1.0 + 1e-6:
        raise ValueError(f"{name} must be a normalized (x, y, w, h) inside 0..1, got {value}")
    return value


class CamLayoutConfig(BaseModel):
    """Streamer-cam (top) + gameplay (bottom) layout, used when layout_mode is cam_gameplay."""

    model_config = ConfigDict(extra="ignore")

    top_ratio: float = Field(default=0.5, ge=0.2, le=0.8)
    cam_source: Literal["crop", "file"] = "crop"
    cam_rect: tuple[float, float, float, float] | None = None
    cam_file: str | None = None
    gameplay_rect: tuple[float, float, float, float] | None = None
    mirror_cam: bool = False
    auto_detect: bool = True

    @model_validator(mode="after")
    def _validate_rects(self) -> CamLayoutConfig:
        _check_norm_rect(self.cam_rect, "cam_rect")
        _check_norm_rect(self.gameplay_rect, "gameplay_rect")
        if self.cam_source == "file" and not self.cam_file and self.cam_rect is None:
            raise ValueError("cam_source 'file' needs cam_file (or a cam_rect to crop instead)")
        return self


class VisualBrandingConfig(BaseModel):
    """Visual framing, color grading, watermark, and progress bar branding options."""

    model_config = ConfigDict(extra="ignore")

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
    enable_chyron: bool = False
    chyron_badge: str = "BREAKING NEWS"
    chyron_style: str = "broadcast_red"
    cam_layout: CamLayoutConfig = Field(default_factory=CamLayoutConfig)


BrandingConfig = VisualBrandingConfig


class AudioMasteringConfig(BaseModel):
    """Audio mastering, voice boost EQ, and sound effect parameters."""

    model_config = ConfigDict(extra="ignore")

    voice_boost: bool = True
    target_lufs: float = Field(default=-14.0, ge=-24.0, le=-9.0)
    bg_music_volume: float = Field(default=0.20, ge=0.0, le=0.50)
    enable_sfx: bool = True


class SubtitlesConfig(BaseModel):
    """Karaoke ASS subtitle styling and safe-zone positioning."""

    model_config = ConfigDict(extra="ignore")

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
        default="kurz",
        description="Subtitle visual preset: kurz, neon, minimal, pill, hormozi, mrbeast, cyberpunk, cinematic, rapid",
    )
    text_transform: str = "uppercase"
    position: str = "center"
    hook_banner_style: str = "neon_box"
    enable_emojis: bool = True
    karaoke_pop_scale: float = 1.18
    caption_engine: str = Field(
        default="ass",
        description=(
            "How word captions are drawn: 'ass' (FFmpeg burn-in, default) or 'remotion' "
            "(React caption overlay rendered by Remotion and composited with FFmpeg)."
        ),
    )


class RetentionConfig(BaseModel):
    """Retention policy parameters for work directory and historical runs."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    work_max_age_days: int = Field(default=14, ge=0)
    work_max_total_gb: float = Field(default=20.0, ge=0.0)
    keep_last_n: int = Field(default=10, ge=0)
    dry_run: bool = True
    output_enabled: bool = False
    output_max_age_days: int = Field(default=14, ge=0)
    output_max_total_gb: float = Field(default=20.0, ge=0.0)


class CleanupConfig(BaseModel):
    """Intermediate file purge and safety retention parameters."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    use_recycle_bin: bool = False
    keep_master_mp4: bool = False
    keep_final_days: int = Field(default=0, ge=0)
    purge_intermediate_work: bool = True
    dry_run: bool = False


class RenderConfig(BaseModel):
    """Which renderer(s) produce a short and how many may run at once (see render_dispatch)."""

    model_config = ConfigDict(extra="ignore")

    method: str = Field(
        default="auto",
        description="auto (raea -> remotion -> ffmpeg), raea, remotion or ffmpeg.",
    )
    methods: list[str] = Field(
        default_factory=list,
        description="Methods used by multi-method batches (strategy round_robin / variants).",
    )
    strategy: str = Field(
        default="single",
        description="single | round_robin (one method per job) | variants (every method per clip).",
    )
    fallback: bool = Field(
        default=True, description="Fall through to the rest of the chain when a method fails."
    )
    max_parallel_per_method: dict[str, int] = Field(
        default_factory=lambda: {"raea": 1, "remotion": 1, "ffmpeg": 2},
        description="Concurrent renders allowed per method (heavy renderers must not oversubscribe).",
    )

    @field_validator("method")
    @classmethod
    def _check_method(cls, v: str) -> str:
        v = (v or "auto").strip().lower()
        if v not in {"auto", "raea", "remotion", "ffmpeg"}:
            raise ValueError("render.method must be auto, raea, remotion or ffmpeg")
        return v

    @field_validator("methods")
    @classmethod
    def _check_methods(cls, v: list[str]) -> list[str]:
        out = [str(m).strip().lower() for m in v if str(m).strip()]
        bad = [m for m in out if m not in {"auto", "raea", "remotion", "ffmpeg"}]
        if bad:
            raise ValueError(f"render.methods has unknown entries: {bad}")
        return out

    @field_validator("strategy")
    @classmethod
    def _check_strategy(cls, v: str) -> str:
        v = (v or "single").strip().lower()
        if v not in {"single", "round_robin", "variants"}:
            raise ValueError("render.strategy must be single, round_robin or variants")
        return v


class PipelineConfig(BaseModel):
    """Pipeline concurrency, caption engine, and delivery QC parameters."""

    model_config = ConfigDict(extra="ignore")

    concurrency: int = Field(default=3, ge=1)
    caption_engine: str = "ass"
    enable_delivery_qc: bool = True


class EachLabsConfig(BaseModel):
    """EachLabs music / audio generation configuration."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    model: str = "mureka-generate-music"


class IntegrationsConfig(BaseModel):
    """External third-party platform integrations."""

    model_config = ConfigDict(extra="ignore")

    eachlabs: EachLabsConfig = Field(default_factory=EachLabsConfig)


class AnalyticsPriorsConfig(BaseModel):
    """Feed historical Shorts analytics back into clip ranking (decider/performance_prior.py)."""

    model_config = ConfigDict(extra="ignore")

    # Off by default: when on, past SVS results can nudge candidate scores.
    enabled: bool = False
    # Minimum analytics rows with a usable clip before any prior is applied.
    min_videos: int = Field(default=10, ge=1)
    # Minimum rows in a feature group before that group contributes.
    min_group_samples: int = Field(default=3, ge=1)
    # Pseudo-count that pulls small groups toward the channel mean.
    shrinkage_m: float = Field(default=2.0, ge=0.0)
    # Hard cap on the additive change a prior can make to one candidate score.
    max_score_adjustment: float = Field(default=0.05, ge=0.0, le=0.25)
    # Advisory approve/patch hook thresholds fitted from history (reported, not enforced).
    adaptive_thresholds: bool = True
    threshold_max_shift: float = Field(default=5.0, ge=0.0, le=10.0)
    threshold_min_lift: float = Field(default=3.0, ge=0.0)


class RaeaConfig(BaseModel):
    """raea (Rust+Bevy React renderer) settings used by media/raea_renderer.py."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    # Path to the raea executable. None = AUTOSHORTS_RAEA_BIN, then PATH, then ~/.raea/bin.
    binary: str | None = None
    # Render timeout = max(120, clip_seconds * this multiplier + 60).
    timeout_sec_per_clip_sec: float = Field(default=12.0, gt=0.0)
    # Run `raea check` on the finished video and record its findings (never fails a render).
    run_check: bool = False
    # Extra CLI args appended to `raea render` (e.g. ["--quality", "high"]).
    extra_args: list[str] = Field(default_factory=list)


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
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    raea: RaeaConfig = Field(default_factory=RaeaConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    analytics_priors: AnalyticsPriorsConfig = Field(default_factory=AnalyticsPriorsConfig)

    # Secrets (strictly from environment or .env, never hardcoded)
    app_env: str = Field(default="production", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    gemini_api_key: SecretStr | None = Field(default=None, alias="GEMINI_API_KEY")
    openrouter_api_key: SecretStr | None = Field(default=None, alias="OPENROUTER_API_KEY")
    youtube_api_key: SecretStr | None = Field(default=None, alias="YOUTUBE_API_KEY")
    local_gguf_path: str | None = Field(default=None, alias="LOCAL_GGUF_PATH")
    eachlabs_api_key: SecretStr | None = Field(default=None, alias="EACHLABS_API_KEY")
    pexels_api_key: SecretStr | None = Field(default=None, alias="PEXELS_API_KEY")
    pixabay_api_key: SecretStr | None = Field(default=None, alias="PIXABAY_API_KEY")

    def _register_loaded_secrets(self) -> None:
        """Register loaded secrets for automatic redaction in logs."""
        if self.gemini_api_key:
            register_secret(self.gemini_api_key.get_secret_value())
        if self.openrouter_api_key:
            register_secret(self.openrouter_api_key.get_secret_value())
        if self.youtube_api_key:
            register_secret(self.youtube_api_key.get_secret_value())
        if self.eachlabs_api_key:
            register_secret(self.eachlabs_api_key.get_secret_value())
        if self.pexels_api_key:
            register_secret(self.pexels_api_key.get_secret_value())
        if self.pixabay_api_key:
            register_secret(self.pixabay_api_key.get_secret_value())

    def model_post_init(self, context: Any, /) -> None:
        """Register loaded secrets for automatic redaction in logs."""
        self._register_loaded_secrets()

    def get_gemini_api_key_str(self) -> str | None:
        """Return the unmasked Gemini API key string, or None if not set."""
        if self.gemini_api_key:
            return self.gemini_api_key.get_secret_value()
        return os.environ.get("GEMINI_API_KEY")

    def get_openrouter_api_key_str(self) -> str | None:
        """Return the unmasked OpenRouter API key string, or None if not set."""
        if self.openrouter_api_key:
            return self.openrouter_api_key.get_secret_value()
        return os.environ.get("OPENROUTER_API_KEY")

    def get_youtube_api_key_str(self) -> str | None:
        """Return the unmasked YouTube API key string, or None if not set."""
        if self.youtube_api_key:
            return self.youtube_api_key.get_secret_value()
        return os.environ.get("YOUTUBE_API_KEY")

    def get_eachlabs_api_key_str(self) -> str | None:
        """Return the unmasked EachLabs API key string, or None if not set."""
        if self.eachlabs_api_key:
            return self.eachlabs_api_key.get_secret_value()
        return os.environ.get("EACHLABS_API_KEY")

    def get_pexels_api_key_str(self) -> str | None:
        """Return the unmasked Pexels API key string, or None if not set."""
        if self.pexels_api_key:
            return self.pexels_api_key.get_secret_value()
        return os.environ.get("PEXELS_API_KEY")

    def get_pixabay_api_key_str(self) -> str | None:
        """Return the unmasked Pixabay API key string, or None if not set."""
        if self.pixabay_api_key:
            return self.pixabay_api_key.get_secret_value()
        return os.environ.get("PIXABAY_API_KEY")


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
            with open(target, encoding="utf-8") as f:
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
    "AnalyticsPriorsConfig",
    "AppConfig",
    "AudioMasteringConfig",
    "BrandingConfig",
    "CleanupConfig",
    "ColorGradingPreset",
    "ConfigError",
    "EachLabsConfig",
    "EditPlannerConfig",
    "HookBannerStyle",
    "IntegrationsConfig",
    "JobDefaultsConfig",
    "LLMConfig",
    "CamLayoutConfig",
    "LayoutMode",
    "MediaConfig",
    "PipelineConfig",
    "ProgressBarPosition",
    "QuotaConfig",
    "RetentionConfig",
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
