"""FastAPI Web Dashboard and REST API for AutoShorts.

Provides real-time job queue monitoring, quota meters, setting controls,
Spark import parsing, and video previews.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from autoshorts.core.config import Settings, load_config
from autoshorts.core.logging_setup import get_log_stream_handler
from autoshorts.core.models import EditDecisionList, Job, JobStatus
from autoshorts.core.paths import get_assets_dir, get_output_dir
from autoshorts.core.settings_manager import (
    apply_settings_update,
    get_public_settings_payload,
    get_youtube_oauth_status,
    run_youtube_oauth_flow,
    save_youtube_client_secret,
)
from autoshorts.core.state_manager import StateManager, get_pt_timezone
from autoshorts.discovery.url_validator import parse_links_input
from autoshorts.integrations.spark import parse_spark_import
from autoshorts.media.encoder_probe import probe_encoder
from autoshorts.pipeline import ShortsPipeline
from autoshorts.uploader.description_builder import build_short_description, build_short_title

logger = logging.getLogger("autoshorts.dashboard")

DASHBOARD_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"


# -----------------------------------------------------------------------------
# Metadata Enrichment Helper
# -----------------------------------------------------------------------------


def enrich_job_dict(job: Job, cfg: Settings) -> dict[str, Any]:
    """Augment job dictionary with SEO title, full description, tags, and video ID."""
    data = job.model_dump()

    # YouTube short ID if uploaded
    yt_id = (
        job.upload_result.video_id if (job.upload_result and job.upload_result.video_id) else None
    )
    data["youtube_video_id"] = yt_id

    # Formatting attributes
    channel = job.video_metadata.channel if job.video_metadata else "AutoShorts"
    v_title = (
        job.video_metadata.title
        if job.video_metadata
        else (job.custom_instructions or "AutoShort Clip")
    )
    v_url = job.video_metadata.video_url if job.video_metadata else job.source_url
    ch_url = job.video_metadata.channel_url if job.video_metadata else "https://youtube.com"

    hook = "Must Watch"
    start_sec = 0.0
    end_sec = 45.0
    if job.selected_clips:
        sel = job.selected_clips[0]
        hook = sel.hook_text or sel.title or hook
        start_sec = sel.candidate.start
        end_sec = sel.candidate.end
    elif job.edl and job.edl.recommended_hook:
        hook = job.edl.recommended_hook

    seo_title = build_short_title(
        hook=hook,
        channel=channel,
        title=v_title,
        template=cfg.upload.title_template,
    )
    full_description = build_short_description(
        title=v_title,
        channel=channel,
        channel_url=ch_url,
        video_url=v_url,
        hook=hook,
        start=start_sec,
        end=end_sec,
        template=cfg.upload.description_template,
    )

    data["seo_title"] = seo_title
    data["full_description"] = full_description
    data["hashtags"] = "#Shorts #Podcasts #Education"
    return data


# -----------------------------------------------------------------------------
# Background Scheduler Worker
# -----------------------------------------------------------------------------


async def scheduler_worker(
    pipe: ShortsPipeline,
    stop_event: asyncio.Event,
    interval_sec: float = 15.0,
    inbox_watcher: Any | None = None,
) -> None:
    """Async background worker that calls pipe.scheduler.run_tick() and polls inbox every interval_sec."""
    logger.info("Scheduler background worker started (interval=%.1fs)", interval_sec)
    while not stop_event.is_set():
        try:
            if hasattr(pipe, "scheduler") and pipe.scheduler is not None:
                await asyncio.to_thread(pipe.scheduler.run_tick)
        except Exception as e:
            logger.warning("Error during scheduler background run_tick: %s", e)
        try:
            if inbox_watcher is not None and hasattr(inbox_watcher, "poll_once"):
                await asyncio.to_thread(inbox_watcher.poll_once)
        except Exception as e:
            logger.warning("Error during inbox watcher poll_once: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
        except TimeoutError:
            pass
    logger.info("Scheduler background worker stopped")


# -----------------------------------------------------------------------------
# Request & Response Models
# -----------------------------------------------------------------------------


def set_job_attr(job: Job, attr: str, val: Any) -> None:
    """Safely set an attribute on a Job instance, supporting dynamic fallback."""
    try:
        setattr(job, attr, val)
    except (ValueError, TypeError, AttributeError):
        if hasattr(job, "__dict__"):
            job.__dict__[attr] = val


DEFAULT_STUDIO_PROFILES = [
    {
        "id": "viral_split",
        "name": "⚡ Viral Split",
        "description": "50/50 split with neon karaoke pop captions and vibrant color grade",
        "layout_mode": "split_50_50",
        "style_preset": "neon",
        "color_grading": "vibrant",
        "dopamine_level": "high",
        "background_category": "minecraft",
        "subtitle_position": "center",
        "text_transform": "uppercase",
    },
    {
        "id": "podcast_blur",
        "name": "🎙️ Podcast Blur",
        "description": "Blurred background with centered 16:9 video and clean minimal captions",
        "layout_mode": "full_blur_bg",
        "style_preset": "minimal",
        "color_grading": "warm",
        "dopamine_level": "low",
        "background_category": "none",
        "subtitle_position": "bottom_safe",
        "text_transform": "title",
    },
    {
        "id": "gaming_max",
        "name": "🎮 Gaming Max Dopamine",
        "description": "Explosive Hormozi-style captions with subway surfers background",
        "layout_mode": "split_50_50",
        "style_preset": "hormozi",
        "color_grading": "vibrant",
        "dopamine_level": "ultra",
        "background_category": "subway",
        "subtitle_position": "center",
        "text_transform": "uppercase",
    },
    {
        "id": "cinematic",
        "name": "🎬 Cinematic Full Crop",
        "description": "Immersive 9:16 vertical crop with elegant cinematic lower-third captions",
        "layout_mode": "full_center_crop",
        "style_preset": "cinematic",
        "color_grading": "cinematic",
        "dopamine_level": "none",
        "background_category": "none",
        "subtitle_position": "bottom_safe",
        "text_transform": "none",
    },
    {
        "id": "pip_reaction",
        "name": "📱 PiP Reaction",
        "description": "Picture-in-picture commentary style with MrBeast tilted pop captions",
        "layout_mode": "pip_gameplay",
        "style_preset": "mrbeast",
        "color_grading": "crisp",
        "dopamine_level": "medium",
        "background_category": "minecraft",
        "subtitle_position": "center",
        "text_transform": "uppercase",
    },
]


def _get_studio_profiles_file() -> Path:
    from autoshorts.core.paths import get_data_dir

    return get_data_dir() / "studio_profiles.json"


def _load_studio_profiles() -> list[dict[str, Any]]:
    import autoshorts.core.settings_manager as sm

    if hasattr(sm, "get_studio_profiles"):
        try:
            res = sm.get_studio_profiles()
            if isinstance(res, dict):
                return list(res.values())
            elif isinstance(res, list):
                return res
        except Exception:
            pass

    prof_file = _get_studio_profiles_file()
    if prof_file.is_file():
        try:
            with open(prof_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    return list(data.values())
        except Exception as e:
            logger.debug("Failed loading studio profiles: %s", e)

    return list(DEFAULT_STUDIO_PROFILES)


def _save_studio_profile(profile: dict[str, Any]) -> dict[str, Any]:
    import autoshorts.core.settings_manager as sm

    if hasattr(sm, "save_studio_profile"):
        try:
            return sm.save_studio_profile(profile)
        except Exception:
            pass

    prof_file = _get_studio_profiles_file()
    prof_file.parent.mkdir(parents=True, exist_ok=True)
    current = _load_studio_profiles()

    prof_id = profile.get("id") or f"profile_{int(time.time())}"
    profile["id"] = prof_id

    updated = False
    for idx, p in enumerate(current):
        if p.get("id") == prof_id:
            current[idx] = {**p, **profile}
            updated = True
            break
    if not updated:
        current.append(profile)

    with open(prof_file, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)

    return profile


def _get_channel_profiles_file() -> Path:
    from autoshorts.core.paths import get_data_dir

    return get_data_dir() / "channel_profiles.json"


def _load_channel_profiles() -> list[dict[str, Any]]:
    import autoshorts.core.settings_manager as sm

    if hasattr(sm, "get_channel_profiles"):
        try:
            return sm.get_channel_profiles()
        except Exception:
            pass

    chan_file = _get_channel_profiles_file()
    if chan_file.is_file():
        try:
            with open(chan_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.debug("Failed loading channel profiles: %s", e)
    return []


def _save_channel_profile(channel: dict[str, Any]) -> dict[str, Any]:
    import autoshorts.core.settings_manager as sm

    if hasattr(sm, "save_channel_profile"):
        try:
            return sm.save_channel_profile(channel)
        except Exception:
            pass

    chan_file = _get_channel_profiles_file()
    chan_file.parent.mkdir(parents=True, exist_ok=True)
    current = _load_channel_profiles()

    chan_id = channel.get("id") or f"channel_{int(time.time())}"
    channel["id"] = chan_id

    updated = False
    for idx, c in enumerate(current):
        if c.get("id") == chan_id:
            current[idx] = {**c, **channel}
            updated = True
            break
    if not updated:
        current.append(channel)

    with open(chan_file, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)

    return channel


class JobSubmitRequest(BaseModel):
    links: str = Field(..., description="Newline separated links with optional : instructions")
    custom_instructions: str | None = Field(default=None)
    provider: str | None = Field(default=None)
    background: str | None = Field(default=None)
    dopamine: str | None = Field(default=None)
    upload_mode: str | None = Field(default=None)
    auto_upload: bool = Field(default=False)
    immediate_upload: bool = Field(default=False)
    style_preset: str | None = Field(default=None)
    privacy_status: str | None = Field(default=None)
    # Advanced customization and upload fields
    layout_mode: str | None = Field(default=None)
    color_grading: str | None = Field(default=None)
    watermark_text: str | None = Field(default=None)
    watermark_position: str | None = Field(default=None)
    progress_bar_position: str | None = Field(default=None)
    text_transform: str | None = Field(default=None)
    subtitle_position: str | None = Field(default=None)
    hook_banner_style: str | None = Field(default=None)
    enable_emojis: bool | None = Field(default=None)
    category_id: str | None = Field(default=None)
    altered_content: bool | None = Field(default=None)
    custom_tags: list[str] | str | None = Field(default=None)
    auto_pin_comment: bool | None = Field(default=None)
    pin_comment_text: str | None = Field(default=None)
    link_related_video: bool | None = Field(default=None)
    related_video_url: str | None = Field(default=None)
    publish_at: str | None = Field(default=None)
    channel_profile_id: str | None = Field(default=None)


SubmitJobsRequest = JobSubmitRequest


class LinkPreviewRequest(BaseModel):
    links_text: str = Field(..., description="Raw text containing one or more video URLs")


class JobPatchRequest(BaseModel):
    clip_title: str | None = Field(default=None, description="Updated clip title")
    hook_text: str | None = Field(default=None, description="Updated hook text")
    background_category: str | None = Field(default=None, description="Updated background category")
    dopamine_level: str | None = Field(default=None, description="Updated dopamine level")
    custom_instructions: str | None = Field(default=None, description="Updated custom instructions")
    # Advanced customization and upload fields
    layout_mode: str | None = Field(default=None)
    style_preset: str | None = Field(default=None)
    color_grading: str | None = Field(default=None)
    watermark_text: str | None = Field(default=None)
    watermark_position: str | None = Field(default=None)
    progress_bar_position: str | None = Field(default=None)
    text_transform: str | None = Field(default=None)
    subtitle_position: str | None = Field(default=None)
    hook_banner_style: str | None = Field(default=None)
    enable_emojis: bool | None = Field(default=None)
    category_id: str | None = Field(default=None)
    altered_content: bool | None = Field(default=None)
    custom_tags: list[str] | str | None = Field(default=None)
    auto_pin_comment: bool | None = Field(default=None)
    pin_comment_text: str | None = Field(default=None)
    link_related_video: bool | None = Field(default=None)
    related_video_url: str | None = Field(default=None)
    publish_at: str | None = Field(default=None)
    channel_profile_id: str | None = Field(default=None)
    privacy_status: str | None = Field(default=None)


class TestProviderRequest(BaseModel):
    provider: str = Field(..., description="LLM provider name: 'gemini', 'openrouter', or 'local'")
    api_key: str | None = Field(default=None, description="Optional temporary API key")


class SparkImportRequest(BaseModel):
    text: str = Field(..., description="Markdown or text containing Spark clips")


class SchedulerToggleRequest(BaseModel):
    paused: bool | None = None


class BackgroundDownloadRequest(BaseModel):
    categories: list[str] = Field(default_factory=lambda: ["minecraft", "subway"])
    min_clips: int = Field(default=1)


class MusicDownloadPayload(BaseModel):
    track_id: str = Field(..., description="Target track ID")
    custom_url: str | None = Field(default=None, description="Optional custom source URL")


class ResearchBriefPayload(BaseModel):
    niche: str = Field(default="ai_tech", description="Niche identifier")
    custom_query: str | None = Field(default=None, description="Optional search query")


class PipelineHandoffPayload(BaseModel):
    url: str = Field(..., description="YouTube video URL")
    niche: str = Field(default="ai_tech", description="Niche category")
    focus_prompt: str | None = Field(default=None, description="Focus prompt for clipping")
    recommended_studio_profile: str = Field(
        default="hormozi", description="Recommended Studio Profile"
    )
    recommended_background: str = Field(
        default="minecraft", description="Recommended background category"
    )
    auto_upload: bool = Field(default=False, description="Whether to trigger auto-upload")


class MoneyPrinterScriptPayload(BaseModel):
    niche: str = Field(default="ai_tech", description="Target niche")
    topic: str | None = Field(default=None, description="Custom topic prompt")
    language: str = Field(default="en", description="Language code")


class AnalyticsSyncPayload(BaseModel):
    force: bool = Field(default=False, description="Force sync flag")


# -----------------------------------------------------------------------------
# Browser Launcher Helper
# -----------------------------------------------------------------------------


def launch_browser(url: str, prefer_edge: bool = True) -> None:
    """Launch Microsoft Edge or default browser on startup (safely guarded)."""
    if prefer_edge and os.name == "nt":
        edge_candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
        ]
        for p in edge_candidates:
            if Path(p).is_file():
                try:
                    subprocess.Popen([p, url])
                    return
                except Exception as e:
                    logger.debug("Edge launch failed on %s: %s", p, e)

    try:
        webbrowser.open(url)
    except Exception as e:
        logger.debug("Standard browser open failed: %s", e)


# -----------------------------------------------------------------------------
# FastAPI Application Factory
# -----------------------------------------------------------------------------


def create_app(
    pipeline: ShortsPipeline | None = None,
    state_manager: StateManager | None = None,
    config: Settings | None = None,
    run_scheduler_worker: bool = True,
    scheduler_interval_sec: float = 15.0,
    inbox_watcher: Any | None = None,
) -> FastAPI:
    """Factory to instantiate the AutoShorts FastAPI dashboard app."""
    cfg = config or load_config()
    state_mgr = state_manager or (pipeline.state_manager if pipeline else StateManager())
    pipe = pipeline or ShortsPipeline(config=cfg, state_manager=state_mgr)

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        stop_event = asyncio.Event()
        worker_task = None

        watcher = inbox_watcher
        if watcher is None:
            try:
                from autoshorts.integrations.spark import SparkInboxWatcher

                inbox_dir = (
                    getattr(cfg.spark, "inbox_dir", "inbox") if hasattr(cfg, "spark") else "inbox"
                )
                watcher = SparkInboxWatcher(
                    inbox_dir=inbox_dir,
                    state_manager=state_mgr,
                    poll_interval_sec=getattr(cfg.spark, "poll_interval_sec", 10)
                    if hasattr(cfg, "spark")
                    else 10,
                )
            except Exception as e:
                logger.warning("Failed to initialize SparkInboxWatcher in lifespan: %s", e)

        app_instance.state.inbox_watcher = watcher
        if run_scheduler_worker:
            worker_task = asyncio.create_task(
                scheduler_worker(
                    pipe,
                    stop_event,
                    interval_sec=scheduler_interval_sec,
                    inbox_watcher=watcher,
                )
            )
            app_instance.state.scheduler_worker_task = worker_task
            app_instance.state.scheduler_stop_event = stop_event
        try:
            yield
        finally:
            if worker_task:
                stop_event.set()
                worker_task.cancel()
                try:
                    await worker_task
                except (asyncio.CancelledError, Exception):
                    pass

    app = FastAPI(
        title="AutoShorts Dashboard",
        version="1.0.0",
        description="On-Device Video Clipping and Quota Pacing Orchestrator",
        lifespan=lifespan,
    )

    # Allow CORS for development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount static assets if directory exists
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR)) if TEMPLATES_DIR.is_dir() else None

    # -------------------------------------------------------------------------
    # UI Endpoint
    # -------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Any:
        if templates and (TEMPLATES_DIR / "index.html").is_file():
            return templates.TemplateResponse(
                request=request, name="index.html", context={"config": cfg}
            )
        return HTMLResponse(
            "<html><body><h1>AutoShorts Dashboard</h1><p>Templates not found. Use API endpoints.</p></body></html>"
        )

    # -------------------------------------------------------------------------
    # Jobs Management API
    # -------------------------------------------------------------------------

    @app.post("/api/links/preview")
    async def preview_links(req: LinkPreviewRequest) -> dict[str, Any]:
        """Parse up to 5 links and fetch title, channel, duration and thumbnail metadata."""
        if not req.links_text or not req.links_text.strip():
            return {"previews": []}

        try:
            parsed = parse_links_input(req.links_text)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse links: {e}") from e

        previews = []
        for p in parsed[:5]:
            vid = p.video_id
            url = p.canonical_url
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
            title = f"YouTube Video ({vid})"
            channel = "YouTube Creator"
            duration = 0.0

            # 1. Try pipeline downloader fetch_metadata
            meta_fetched = False
            if hasattr(pipe, "downloader") and pipe.downloader is not None:
                try:
                    meta = await asyncio.to_thread(pipe.downloader.fetch_metadata, url)
                    title = meta.title or title
                    channel = meta.channel or channel
                    duration = meta.duration or duration
                    meta_fetched = True
                except Exception as e:
                    logger.debug("Downloader metadata fetch failed for %s: %s", vid, e)

            # 2. Fallback to YouTube oEmbed API if downloader was not available or failed
            if not meta_fetched:
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=4.0) as client:
                        resp = await client.get(
                            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
                        )
                        if resp.status_code == 200:
                            odata = resp.json()
                            title = odata.get("title", title)
                            channel = odata.get("author_name", channel)
                except Exception as e:
                    logger.debug("oEmbed fallback fetch failed for %s: %s", vid, e)

            custom_instr = p.custom_instructions or p.focus_prompt
            previews.append(
                {
                    "video_id": vid,
                    "url": url,
                    "title": title,
                    "channel": channel,
                    "duration": duration,
                    "thumbnail_url": thumb,
                    "custom_instructions": custom_instr,
                }
            )

        return {"previews": previews}

    @app.post("/api/jobs/submit")
    async def submit_jobs(
        req: JobSubmitRequest, background_tasks: BackgroundTasks
    ) -> dict[str, Any]:
        """Submit links, parse them, and kick off background processing."""
        if not req.links.strip():
            raise HTTPException(status_code=400, detail="No links provided")

        if req.upload_mode:
            cfg.upload.mode = req.upload_mode
            if pipe and pipe.config:
                pipe.config.upload.mode = req.upload_mode
                if req.upload_mode == "studio":
                    from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader

                    pipe.uploader = PlaywrightStudioUploader(config=pipe.config)
                elif req.upload_mode == "local":
                    from autoshorts.uploader.local_exporter import LocalExportUploader

                    pipe.uploader = LocalExportUploader(export_dir=get_output_dir())
                elif req.upload_mode in ("api", "youtube"):
                    from autoshorts.uploader.youtube_api import YouTubeApiUploader

                    try:
                        pipe.uploader = YouTubeApiUploader(
                            config=pipe.config,
                            quota_tracker=pipe.quota_tracker,
                        )
                    except Exception as e:
                        logger.warning("Could not switch to YouTube API uploader: %s", e)
                if hasattr(pipe, "scheduler") and pipe.scheduler is not None:
                    pipe.scheduler.uploader = pipe.uploader

        if req.privacy_status:
            cfg.upload.privacy_status = req.privacy_status
            cfg.upload.default_privacy_status = req.privacy_status
            if pipe and pipe.config:
                pipe.config.upload.privacy_status = req.privacy_status
                pipe.config.upload.default_privacy_status = req.privacy_status

        if req.style_preset:
            cfg.subtitles.style_preset = req.style_preset
            if pipe and pipe.config:
                pipe.config.subtitles.style_preset = req.style_preset

        jobs = pipe.submit_links(
            links_input=req.links,
            custom_instructions=req.custom_instructions,
        )

        # Forward any custom customization fields onto each Job
        for job in jobs:
            for field_name in (
                "layout_mode",
                "style_preset",
                "color_grading",
                "watermark_text",
                "watermark_position",
                "progress_bar_position",
                "text_transform",
                "subtitle_position",
                "hook_banner_style",
                "enable_emojis",
                "category_id",
                "altered_content",
                "custom_tags",
                "auto_pin_comment",
                "pin_comment_text",
                "link_related_video",
                "related_video_url",
                "publish_at",
                "channel_profile_id",
                "privacy_status",
            ):
                val = getattr(req, field_name, None)
                if val is not None:
                    set_job_attr(job, field_name, val)
            state_mgr.save_job(job)

        # Trigger processing in background thread
        for job in jobs:
            background_tasks.add_task(
                pipe.process_job,
                job_or_id=job,
                auto_upload=req.auto_upload,
                immediate_upload=req.immediate_upload,
                background_category=req.background,
                dopamine_level=req.dopamine,
            )

        return {
            "success": True,
            "count": len(jobs),
            "jobs": [j.model_dump() for j in jobs],
        }

    @app.get("/api/jobs")
    async def list_jobs(limit: int = 50) -> dict[str, Any]:
        """Return list of most recent clipping jobs enriched with SEO copy metadata."""
        jobs = state_mgr.list_jobs(limit=limit)
        return {
            "jobs": [enrich_job_dict(j, cfg) for j in jobs],
        }

    @app.get("/api/jobs/{job_id}")
    async def get_job_details(job_id: str) -> dict[str, Any]:
        """Return specific job state and recorded execution stages."""
        job = state_mgr.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        stages = state_mgr.get_job_stages(job_id)
        return {
            "job": enrich_job_dict(job, cfg),
            "stages": stages,
        }

    @app.patch("/api/jobs/{job_id}")
    async def update_job(job_id: str, req: JobPatchRequest) -> dict[str, Any]:
        """Update editable metadata of a job prior to rendering or upload."""
        job = state_mgr.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        if req.custom_instructions is not None:
            job.custom_instructions = req.custom_instructions

        if job.selected_clips:
            for sel in job.selected_clips:
                if req.clip_title is not None:
                    sel.title = req.clip_title
                if req.hook_text is not None:
                    sel.hook_text = req.hook_text
                if req.background_category is not None:
                    sel.background_style = req.background_category

        if job.edl:
            if req.clip_title is not None:
                job.edl.recommended_title = req.clip_title
            if req.hook_text is not None:
                job.edl.recommended_hook = req.hook_text
            if req.background_category is not None and req.background_category in (
                "minecraft",
                "subway",
                "auto",
            ):
                job.edl.background_style_recommendation = req.background_category
        elif not job.selected_clips and (
            req.clip_title or req.hook_text or req.background_category
        ):
            bg_cat = (
                req.background_category
                if req.background_category in ("minecraft", "subway", "auto")
                else "minecraft"
            )
            job.edl = EditDecisionList(
                recommended_title=req.clip_title or "",
                recommended_hook=req.hook_text or "",
                background_style_recommendation=bg_cat,
            )

        # Forward any custom customization fields onto the Job
        for field_name in (
            "layout_mode",
            "style_preset",
            "color_grading",
            "watermark_text",
            "watermark_position",
            "progress_bar_position",
            "text_transform",
            "subtitle_position",
            "hook_banner_style",
            "enable_emojis",
            "category_id",
            "altered_content",
            "custom_tags",
            "auto_pin_comment",
            "pin_comment_text",
            "link_related_video",
            "related_video_url",
            "publish_at",
            "channel_profile_id",
            "privacy_status",
        ):
            val = getattr(req, field_name, None)
            if val is not None:
                set_job_attr(job, field_name, val)

        job.updated_at = datetime.now(UTC)
        state_mgr.save_job(job)

        current_cfg = pipe.config if hasattr(pipe, "config") else cfg
        return {
            "success": True,
            "job": enrich_job_dict(job, current_cfg),
        }

    # -------------------------------------------------------------------------
    # Word-Level Transcript & EDL Timeline Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/jobs/{job_id}/transcript")
    async def get_job_transcript(job_id: str) -> dict[str, Any]:
        """Read aligned words or raw transcript for word-level editing."""
        from autoshorts.core.paths import get_work_dir

        work_dir = get_work_dir() / job_id
        aligned_cache = work_dir / "aligned_words.json"
        raw_cache = work_dir / "transcript.json"

        if aligned_cache.is_file():
            try:
                words = json.loads(aligned_cache.read_text(encoding="utf-8"))
                return {
                    "success": True,
                    "job_id": job_id,
                    "words": words,
                    "source": "aligned_words",
                }
            except Exception as e:
                logger.debug("Failed reading aligned_words.json: %s", e)

        if raw_cache.is_file():
            try:
                segments = json.loads(raw_cache.read_text(encoding="utf-8"))
                flat_words = []
                for s in segments:
                    text = s.get("text", "")
                    toks = text.split()
                    st = float(s.get("start", 0.0))
                    dur = float(s.get("duration", s.get("end", st + 2.0) - st))
                    step = dur / max(1, len(toks))
                    for i, t in enumerate(toks):
                        flat_words.append(
                            {
                                "word": t,
                                "start": st + i * step,
                                "end": st + (i + 1) * step,
                                "probability": 1.0,
                            }
                        )
                return {
                    "success": True,
                    "job_id": job_id,
                    "words": flat_words,
                    "source": "transcript",
                }
            except Exception as e:
                logger.debug("Failed reading transcript.json: %s", e)

        raise HTTPException(status_code=404, detail=f"No transcript found for job {job_id}")

    @app.put("/api/jobs/{job_id}/transcript")
    async def update_job_transcript(job_id: str, req: dict[str, Any]) -> dict[str, Any]:
        """Update aligned words transcript for a job."""
        from autoshorts.core.paths import get_work_dir

        work_dir = get_work_dir() / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        aligned_cache = work_dir / "aligned_words.json"

        words = req.get("words")
        if not isinstance(words, list):
            raise HTTPException(
                status_code=400, detail="'words' must be a list of word dictionaries"
            )

        try:
            aligned_cache.write_text(json.dumps(words, indent=2), encoding="utf-8")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save aligned words: {e}") from e

        return {
            "success": True,
            "job_id": job_id,
            "count": len(words),
            "message": "Transcript updated successfully.",
        }

    @app.post("/api/jobs/{job_id}/trim")
    async def trim_job(job_id: str, req: dict[str, Any]) -> dict[str, Any]:
        """Update start_sec and end_sec and optional jump_cuts / zoom_punch_ins on job and EDL."""
        job = state_mgr.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        if "start_sec" not in req or "end_sec" not in req:
            raise HTTPException(status_code=400, detail="start_sec and end_sec are required")

        try:
            start_sec = float(req["start_sec"])
            end_sec = float(req["end_sec"])
        except (ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400, detail=f"start_sec and end_sec must be floats: {e}"
            ) from e

        if start_sec < 0 or start_sec >= end_sec:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid trim range: start_sec ({start_sec}) must be >= 0 and < end_sec ({end_sec})",
            )

        duration = end_sec - start_sec
        if duration > 60.0:
            raise HTTPException(
                status_code=400,
                detail=f"Trimmed duration ({duration:.2f}s) exceeds maximum YouTube Shorts duration of 60.0 seconds",
            )

        # Update selected_clips
        if job.selected_clips:
            for sel in job.selected_clips:
                sel.candidate.start = start_sec
                sel.candidate.end = end_sec
                sel.target_duration = duration

        # Update EDL
        if job.edl is None:
            from autoshorts.edit_planner.edl_schema import create_empty_edl

            job.edl = create_empty_edl()

        job.edl.hook_in_sec = start_sec
        job.edl.hook_out_sec = end_sec

        if "jump_cuts" in req and req["jump_cuts"] is not None:
            from autoshorts.core.models import JumpCut

            try:
                job.edl.jump_cuts = [JumpCut(**jc) for jc in req["jump_cuts"]]
            except Exception as jc_err:
                logger.debug("Failed parsing jump cuts: %s", jc_err)

        if "zoom_punch_ins" in req and req["zoom_punch_ins"] is not None:
            from autoshorts.core.models import ZoomPunchIn

            try:
                job.edl.zoom_punch_ins = [ZoomPunchIn(**zp) for zp in req["zoom_punch_ins"]]
            except Exception as zp_err:
                logger.debug("Failed parsing zoom punch ins: %s", zp_err)

        job.updated_at = datetime.now(UTC)
        state_mgr.save_job(job)

        return {
            "success": True,
            "job_id": job_id,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "duration": round(duration, 2),
            "edl": job.edl.model_dump(),
        }

    @app.post("/api/jobs/{job_id}/rerender")
    async def rerender_job_endpoint(
        job_id: str,
        req: dict[str, Any] | None = None,
        background_tasks: BackgroundTasks = None,
    ) -> dict[str, Any]:
        """Trigger fast re-render of subtitles and compositing stages."""
        job = state_mgr.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        overrides = req or {}
        try:
            updated_job = await asyncio.to_thread(pipe.rerender_job, job_id, **overrides)
            current_cfg = pipe.config if hasattr(pipe, "config") else cfg
            return {
                "success": True,
                "job": enrich_job_dict(updated_job, current_cfg),
                "message": "Re-render completed successfully.",
            }
        except Exception as e:
            logger.error("Fast re-render failed for job %s: %s", job_id, e)
            raise HTTPException(status_code=400, detail=str(e)) from e

    # -------------------------------------------------------------------------
    # 1-Click Studio Profiles & Multi-Channel Endpoints
    # -------------------------------------------------------------------------

    @app.get("/api/studio/profiles")
    async def get_studio_profiles() -> dict[str, Any]:
        """Return 1-Click Studio Template Profiles."""
        profiles = _load_studio_profiles()
        return {"success": True, "profiles": profiles, "count": len(profiles)}

    @app.post("/api/studio/profiles")
    async def save_studio_profile_endpoint(req: dict[str, Any]) -> dict[str, Any]:
        """Save or update a 1-Click Studio Template Profile."""
        if not req.get("name"):
            raise HTTPException(status_code=400, detail="Profile name is required")
        saved = _save_studio_profile(req)
        return {"success": True, "profile": saved}

    @app.get("/api/channels")
    async def get_channels() -> dict[str, Any]:
        """List Multi-Channel profiles."""
        channels = _load_channel_profiles()
        return {"success": True, "channels": channels, "count": len(channels)}

    @app.post("/api/channels")
    async def save_channel(req: dict[str, Any]) -> dict[str, Any]:
        """Save or update Multi-Channel profile."""
        if not req.get("name") and not req.get("handle"):
            raise HTTPException(status_code=400, detail="Channel name or handle is required")
        saved = _save_channel_profile(req)
        return {"success": True, "channel": saved}

    @app.post("/api/jobs/{job_id}/retry")
    async def retry_job(job_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
        """Retry a failed job from the queue."""
        job = state_mgr.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        background_tasks.add_task(pipe.retry_job, job_id=job_id)
        return {"success": True, "message": f"Job {job_id} scheduled for retry."}

    @app.post("/api/jobs/{job_id}/upload-now")
    async def upload_now(job_id: str) -> dict[str, Any]:
        """Dispatch manual immediate upload for a job."""
        job = state_mgr.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        try:
            # If job is already enqueued in scheduler, use scheduler.upload_now
            pending = state_mgr.get_pending_scheduled_slots()
            is_scheduled = any(s["job_id"] == job_id for s in pending)

            if is_scheduled:
                result = pipe.scheduler.upload_now(job_id)
            else:
                # Direct upload via pipeline
                result = pipe._run_immediate_upload(job)

            return {
                "success": True,
                "upload_result": result.model_dump(),
            }
        except Exception as e:
            logger.error("Upload now failed for job %s: %s", job_id, e)
            raise HTTPException(status_code=400, detail=str(e)) from e

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: str, force: bool = True) -> dict[str, Any]:
        """Purge intermediate work files and clean up a job."""
        job = state_mgr.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        deleted_paths: list[str] = []
        if hasattr(pipe, "cleaner") and pipe.cleaner is not None:
            try:
                paths = pipe.cleaner.cleanup_job(job_id, force=force)
                deleted_paths = [str(p) for p in paths]
            except Exception as e:
                logger.warning("Error purging files for job %s: %s", job_id, e)

        return {
            "success": True,
            "job_id": job_id,
            "deleted_files": deleted_paths,
            "count": len(deleted_paths),
        }

    @app.post("/api/jobs/batch-retry")
    async def batch_retry_jobs(background_tasks: BackgroundTasks) -> dict[str, Any]:
        """Retry all failed jobs in background worker tasks."""
        failed_jobs = state_mgr.list_jobs(status=JobStatus.FAILED)
        for j in failed_jobs:
            background_tasks.add_task(pipe.retry_job, job_id=j.id)
        return {
            "success": True,
            "count": len(failed_jobs),
            "message": f"Queued {len(failed_jobs)} failed jobs for retry.",
        }

    @app.post("/api/jobs/batch-clean")
    async def batch_clean_jobs() -> dict[str, Any]:
        """Run safety cleaner on all COMPLETED jobs and purge intermediate work files."""
        completed_jobs = state_mgr.list_jobs(status=JobStatus.COMPLETED)
        cleaned_jobs_count = 0
        cleaned_files_count = 0
        cleaned_files: list[str] = []

        if hasattr(pipe, "cleaner") and pipe.cleaner is not None:
            for j in completed_jobs:
                try:
                    paths = pipe.cleaner.cleanup_job(j.id, force=True)
                    cleaned_files_count += len(paths)
                    cleaned_files.extend(str(p) for p in paths)
                    cleaned_jobs_count += 1
                    try:
                        j.transition_to(JobStatus.CLEANED)
                        state_mgr.save_job(j)
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning("Error cleaning job %s: %s", j.id, e)

        return {
            "success": True,
            "cleaned_jobs_count": cleaned_jobs_count,
            "cleaned_files_count": cleaned_files_count,
            "count": cleaned_files_count,
            "cleaned_files": cleaned_files,
        }

    # -------------------------------------------------------------------------
    # Quota & Scheduler API
    # -------------------------------------------------------------------------

    @app.get("/api/quota")
    async def get_quota() -> dict[str, Any]:
        """Return current Pacific-Time daily quota usage, rolling hourly rate limit, and reset countdown."""
        status = pipe.quota_tracker.get_status()
        upload_b = status.get("upload_bucket", {})
        general_b = status.get("general_bucket", {})
        daily_c = status.get("daily_cap", {})

        # Rolling hour rate limit calculations
        now_utc = datetime.now(UTC)
        one_hour_ago = now_utc - timedelta(hours=1)
        rolling_count = state_mgr.count_uploads_since(one_hour_ago)
        rolling_limit = 7
        if hasattr(pipe, "scheduler") and pipe.scheduler is not None:
            sched_cfg = getattr(pipe.scheduler, "config", None)
            if (
                sched_cfg
                and hasattr(sched_cfg, "max_per_rolling_hour")
                and isinstance(sched_cfg.max_per_rolling_hour, int)
            ):
                rolling_limit = sched_cfg.max_per_rolling_hour

        # Pacific-Time reset countdown calculation
        pt_tz = get_pt_timezone()
        now_pt = datetime.now(pt_tz)
        next_reset_pt = (now_pt + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        seconds_until_reset = max(0, int((next_reset_pt - now_pt).total_seconds()))

        return {
            "date_pt": status.get("date_pt"),
            "upload_spent": upload_b.get("spent", 0),
            "upload_limit": upload_b.get("daily_allocation", 100),
            "upload_remaining": upload_b.get("remaining", 100),
            "general_spent": general_b.get("spent", 0),
            "general_limit": general_b.get("daily_allocation", 10000),
            "general_remaining": general_b.get("remaining", 10000),
            "completed_today": daily_c.get("completed", 0),
            "daily_cap": daily_c.get("cap", 50),
            "rolling_hour_count": rolling_count,
            "rolling_hour_limit": rolling_limit,
            "seconds_until_reset": seconds_until_reset,
            "reset_time_pt": next_reset_pt.isoformat(),
            "can_upload": upload_b.get("can_upload_fresh", True),
            "upload_bucket": upload_b,
            "general_bucket": general_b,
            "daily_cap_info": daily_c,
            "is_throttled": status.get("is_throttled", False),
            "fallback_mode": status.get("fallback_mode", "queue_next_day"),
        }

    @app.get("/api/scheduler/status")
    async def get_scheduler_status() -> dict[str, Any]:
        """Return scheduler pause flag and pending slots."""
        paused = pipe.scheduler.is_paused()
        pending = state_mgr.get_pending_scheduled_slots()
        return {
            "paused": paused,
            "pending_count": len(pending),
            "pending_slots": pending,
        }

    @app.post("/api/scheduler/toggle")
    async def toggle_scheduler(req: SchedulerToggleRequest | None = None) -> dict[str, Any]:
        """Pause or resume upload scheduler."""
        current = pipe.scheduler.is_paused()
        target = req.paused if (req and req.paused is not None) else not current

        if target:
            pipe.scheduler.pause()
        else:
            pipe.scheduler.resume()

        return {
            "paused": pipe.scheduler.is_paused(),
        }

    @app.get("/api/scheduler/slots")
    async def get_scheduler_slots() -> dict[str, Any]:
        """Return pending scheduled upload slots enriched with job metadata."""
        getter = getattr(
            state_mgr,
            "get_pending_slots",
            getattr(state_mgr, "get_pending_scheduled_slots", None),
        )
        slots = getter() if getter else []
        enriched_slots = []
        current_cfg = pipe.config if hasattr(pipe, "config") else cfg
        for s in slots:
            slot_dict = dict(s)
            job_id = s.get("job_id")
            if job_id:
                job = state_mgr.get_job(job_id)
                if job:
                    enriched = enrich_job_dict(job, current_cfg)
                    slot_dict["title"] = enriched.get("seo_title") or (
                        job.video_metadata.title if job.video_metadata else "Shorts Clip"
                    )
                    slot_dict["video_id"] = (
                        job.video_metadata.video_id
                        if job.video_metadata
                        else (job.upload_result.video_id if job.upload_result else None)
                    )
                    slot_dict["source_url"] = job.source_url
            enriched_slots.append(slot_dict)
        return {
            "slots": enriched_slots,
            "count": len(enriched_slots),
        }

    # -------------------------------------------------------------------------
    # Spark Companion Import API
    # -------------------------------------------------------------------------

    @app.post("/api/spark/import")
    async def import_spark(req: SparkImportRequest) -> dict[str, Any]:
        """Parse Gemini Spark output text into clip candidates."""
        hints = parse_spark_import(req.text)
        return {
            "success": True,
            "count": len(hints),
            "hints": [h.model_dump() for h in hints],
        }

    @app.get("/api/spark/prompts")
    async def get_spark_prompts(
        topic: str = "{TOPIC}",
        target_background: str = "minecraft",
        duration_min: int = 30,
        duration_max: int = 55,
    ) -> dict[str, Any]:
        """Return ready-to-copy Gemini Spark skill prompt template."""
        from autoshorts.integrations.spark import generate_spark_skill_prompt

        prompt_text = generate_spark_skill_prompt(
            topic=topic,
            target_background=target_background,
            duration_min=duration_min,
            duration_max=duration_max,
        )
        return {
            "success": True,
            "prompt": prompt_text,
            "topic": topic,
            "target_background": target_background,
        }

    # -------------------------------------------------------------------------
    # Background Assets Management API
    # -------------------------------------------------------------------------

    @app.get("/api/backgrounds")
    async def get_backgrounds() -> dict[str, Any]:
        """Return inventory of available background clips grouped by category."""
        from autoshorts.media.background_fetcher import get_backgrounds_inventory

        inventory = get_backgrounds_inventory()
        total_clips = sum(len(clips) for clips in inventory.values())
        return {
            "success": True,
            "total_clips": total_clips,
            "categories": list(inventory.keys()),
            "inventory": inventory,
            **inventory,
        }

    @app.post("/api/backgrounds/download")
    async def download_backgrounds(
        background_tasks: BackgroundTasks,
        req: BackgroundDownloadRequest | None = None,
    ) -> dict[str, Any]:
        """Trigger downloading sample gameplay background clips in the background."""
        from autoshorts.media.background_fetcher import (
            download_preset_backgrounds,
            get_backgrounds_inventory,
        )

        cats = req.categories if (req and req.categories) else ["minecraft", "subway"]
        background_tasks.add_task(download_preset_backgrounds, categories=cats)
        inventory = get_backgrounds_inventory()
        return {
            "success": True,
            "status": "downloading",
            "message": f"Downloading background clips for categories: {', '.join(cats)}",
            "categories": cats,
            "inventory": inventory,
        }

    @app.post("/api/backgrounds/upload")
    async def upload_background(
        request: Request,
        file: UploadFile,
        category: str | None = Form(None),
    ) -> dict[str, Any]:
        """Upload custom background MP4 clip into specified category directory."""
        from autoshorts.media.background_fetcher import normalize_category
        from autoshorts.media.compositor import probe_media_duration

        raw_cat = category or request.query_params.get("category") or "minecraft"
        clean_cat = normalize_category(raw_cat)

        if not clean_cat.replace("_", "").replace("-", "").isalnum() or ".." in clean_cat:
            raise HTTPException(status_code=400, detail="Invalid category name")

        filename = Path(file.filename or "uploaded_bg.mp4").name
        ext = Path(filename).suffix.lower()
        if ext not in {".mp4", ".mov", ".mkv", ".webm"}:
            raise HTTPException(
                status_code=400,
                detail="Only video files (.mp4, .mov, .mkv, .webm) are supported",
            )

        target_dir = get_assets_dir() / "backgrounds" / clean_cat
        target_dir.mkdir(parents=True, exist_ok=True)
        dest_path = target_dir / filename

        try:
            with open(dest_path, "wb") as f:  # noqa: ASYNC230
                while chunk := await file.read(1024 * 1024):
                    f.write(chunk)
        except Exception as e:
            logger.error("Failed to save uploaded background file: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to save file: {e}") from e

        file_size = dest_path.stat().st_size
        duration = probe_media_duration(dest_path, default=0.0)

        return {
            "success": True,
            "message": f"Successfully uploaded {filename} to {clean_cat}",
            "category": clean_cat,
            "filename": filename,
            "path": str(dest_path.resolve()),
            "size_bytes": file_size,
            "size_mb": round(file_size / (1024 * 1024), 2),
            "duration": round(duration, 2),
        }

    # -------------------------------------------------------------------------
    # Authentication & Studio Edge Automation API
    # -------------------------------------------------------------------------

    @app.post("/api/auth/edge-login")
    async def edge_login(background_tasks: BackgroundTasks) -> dict[str, Any]:
        """Trigger interactive Microsoft Edge login session for YouTube Studio."""
        from autoshorts.uploader.studio_playwright import (
            PlaywrightStudioUploader,
            update_login_session_state,
        )

        update_login_session_state(
            status="running",
            message="Microsoft Edge opened. Please sign in to Google/YouTube Studio...",
            error=None,
            channel_name=None,
        )
        uploader = PlaywrightStudioUploader(config=cfg, headless=False)
        background_tasks.add_task(uploader.open_login_session, 300.0)
        return {
            "success": True,
            "message": "Microsoft Edge sign-in session launched. Please complete sign-in in the browser window.",
        }

    @app.get("/api/auth/edge-login/status")
    async def get_edge_login_status() -> dict[str, Any]:
        """Return real-time Edge login session status and credential check."""
        from autoshorts.uploader.studio_playwright import (
            PlaywrightStudioUploader,
            get_login_session_state,
        )

        session_state = get_login_session_state()

        # If an interactive login session is actively running, avoid launching a headless check
        if session_state.get("status") == "running":
            login_check = {"signed_in": False, "channel_name": None}
        else:
            uploader = PlaywrightStudioUploader(config=cfg)
            login_check = await asyncio.to_thread(uploader.check_login_status)

        status_val = session_state.get("status", "idle")
        channel = session_state.get("channel_name") or login_check.get("channel_name")
        signed_in = bool(login_check.get("signed_in") or status_val == "signed_in")

        if status_val == "idle" and signed_in:
            status_val = "signed_in"

        return {
            **session_state,
            "status": status_val,
            "channel_name": channel,
            "signed_in": signed_in,
            "session": session_state,
            "login_status": login_check,
        }

    @app.get("/api/auth/edge-status")
    async def get_edge_status_alias() -> dict[str, Any]:
        """Alias for /api/auth/edge-login/status."""
        return await get_edge_login_status()

    # -------------------------------------------------------------------------
    # System Health API
    # -------------------------------------------------------------------------

    @app.get("/api/health")
    async def get_health() -> dict[str, Any]:
        """Return system CPU, provider, and encoder health telemetry."""
        # Probe encoder
        probe = probe_encoder()
        encoder_str = f"{probe.codec} ({probe.description})"

        # LLM Provider info
        provider_name = pipe.llm_provider.name if pipe.llm_provider else "none"

        return {
            "status": "healthy",
            "cpu_mode": "CPU int8 (Zero CUDA)",
            "llm_provider": provider_name,
            "ffmpeg_encoder": encoder_str,
            "scheduler_paused": pipe.scheduler.is_paused(),
        }

    # -------------------------------------------------------------------------
    # Real-Time Log Streaming (SSE) API
    # -------------------------------------------------------------------------

    @app.get("/api/logs/stream")
    async def stream_logs(request: Request) -> StreamingResponse:
        """Stream system logs in real-time using Server-Sent Events (SSE)."""
        handler = get_log_stream_handler()
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)

        # Preload initial history so the client receives buffered logs upon connect
        history = handler.get_history(limit=100)
        for entry in history:
            q.put_nowait(entry)

        handler.subscribe(loop, q)

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=12.0)
                        yield f"data: {json.dumps({'message': msg})}\n\n"
                    except TimeoutError:
                        # Keep-alive comment for SSE connection stability
                        yield ": ping\n\n"
            except (asyncio.CancelledError, GeneratorExit):
                pass
            finally:
                handler.unsubscribe(loop, q)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/logs/clear")
    async def clear_logs() -> dict[str, Any]:
        """Clear the in-memory log stream buffer."""
        handler = get_log_stream_handler()
        handler.clear()
        return {"success": True, "message": "Log buffer cleared."}

    # -------------------------------------------------------------------------
    # Video Streaming & Download API
    # -------------------------------------------------------------------------

    @app.get("/api/video/{job_id}/preview")
    async def get_preview_video(job_id: str) -> FileResponse:
        """Stream low-resolution preview or master MP4 for scrub preview."""
        job = state_mgr.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        path_str = job.preview_video_path or job.rendered_video_path or job.raw_video_path
        if not path_str or not Path(path_str).is_file():
            raise HTTPException(status_code=404, detail="Video media file not found on disk")

        return FileResponse(path=path_str, media_type="video/mp4")

    @app.get("/api/video/{job_id}/master")
    async def get_master_video(job_id: str) -> FileResponse:
        """Download high-resolution rendered master Short MP4."""
        job = state_mgr.get_job(job_id)
        if not job or not job.rendered_video_path:
            raise HTTPException(status_code=404, detail="Rendered master video not found")

        path = Path(job.rendered_video_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Master video file not found on disk")

        return FileResponse(
            path=str(path),
            media_type="video/mp4",
            filename=f"{job_id}_master.mp4",
        )

    # -------------------------------------------------------------------------
    # Settings & YouTube Data API v3 Management API
    # -------------------------------------------------------------------------

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        """Return current application settings with masked secrets."""
        active_cfg = pipe.config if isinstance(getattr(pipe, "config", None), Settings) else cfg
        return get_public_settings_payload(active_cfg)

    @app.post("/api/settings")
    async def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
        """Apply updates to config.yaml and .env, reloading subsystems in place."""
        nonlocal cfg

        try:
            new_settings = apply_settings_update(payload)
        except Exception as e:
            logger.error("Failed to apply settings update: %s", e)
            raise HTTPException(status_code=400, detail=f"Failed to update settings: {e}") from e

        cfg = new_settings
        if pipe:
            pipe.config = new_settings

            # 1. LLM Provider
            try:
                from autoshorts.core.providers import get_provider

                pipe.llm_provider = get_provider(settings=pipe.config)
            except Exception as e:
                logger.warning("Could not re-initialize LLM provider: %s", e)

            # 2. Uploader
            try:
                mode = getattr(pipe.config.upload, "mode", "api")
                if mode == "studio":
                    from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader

                    pipe.uploader = PlaywrightStudioUploader(config=pipe.config)
                elif mode == "local":
                    from autoshorts.uploader.local_exporter import LocalExportUploader

                    pipe.uploader = LocalExportUploader(export_dir=get_output_dir())
                elif mode in ("api", "youtube"):
                    from autoshorts.uploader.youtube_api import YouTubeApiUploader

                    pipe.uploader = YouTubeApiUploader(
                        config=pipe.config,
                        quota_tracker=getattr(pipe, "quota_tracker", None),
                    )
                if hasattr(pipe, "scheduler") and pipe.scheduler is not None:
                    pipe.scheduler.uploader = pipe.uploader
                    if hasattr(pipe.config, "scheduler"):
                        pipe.scheduler.config = pipe.config.scheduler
            except Exception as e:
                logger.warning("Could not re-initialize uploader: %s", e)

            # 3. Safety Cleaner
            try:
                from autoshorts.cleanup.safety_cleaner import SafetyCleaner

                pipe.cleaner = SafetyCleaner(
                    config=pipe.config,
                    state_manager=getattr(pipe, "state_manager", state_mgr),
                )
                if hasattr(pipe, "scheduler") and pipe.scheduler is not None:
                    pipe.scheduler.cleaner = pipe.cleaner
            except Exception as e:
                logger.warning("Could not re-initialize safety cleaner: %s", e)

        active_cfg = pipe.config if isinstance(getattr(pipe, "config", None), Settings) else cfg
        return {
            "success": True,
            "settings": get_public_settings_payload(active_cfg),
        }

    @app.post("/api/settings/youtube-client-secret")
    async def upload_youtube_client_secret(
        request: Request,
        file: UploadFile | None = None,
    ) -> dict[str, Any]:
        """Save and validate YouTube Data API v3 OAuth client_secret.json."""
        raw_content: str | bytes = ""
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            try:
                body = await request.json()
                if isinstance(body, dict):
                    if "client_secret_json" in body:
                        val = body["client_secret_json"]
                        raw_content = json.dumps(val) if isinstance(val, dict) else str(val)
                    else:
                        raw_content = json.dumps(body)
                else:
                    raw_content = str(body)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {e}") from e
        elif file is not None:
            raw_content = await file.read()
        else:
            try:
                form = await request.form()
                if "file" in form and hasattr(form["file"], "read"):
                    raw_content = await form["file"].read()
                elif "client_secret_json" in form:
                    raw_content = str(form["client_secret_json"])
                else:
                    body_bytes = await request.body()
                    if body_bytes:
                        raw_content = body_bytes
                    else:
                        raise HTTPException(
                            status_code=400, detail="No client secret data provided"
                        )
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to parse upload: {e}") from e

        from autoshorts.core.paths import get_data_dir

        dest_path = get_data_dir() / "client_secret.json"
        try:
            meta = save_youtube_client_secret(raw_content, dest_path=dest_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error("Failed to save YouTube client secret: %s", e)
            raise HTTPException(status_code=500, detail=f"Failed to save client secret: {e}") from e

        active_cfg = pipe.config if isinstance(getattr(pipe, "config", None), Settings) else cfg
        return {
            "success": True,
            "youtube_oauth": get_youtube_oauth_status(active_cfg),
            "client_id_masked": meta.get("client_id_masked", ""),
            "project_id": meta.get("project_id", ""),
        }

    @app.post("/api/settings/youtube-oauth")
    async def start_youtube_oauth(background_tasks: BackgroundTasks) -> dict[str, Any]:
        """Start interactive Google OAuth flow in the background."""
        from autoshorts.core.paths import get_data_dir

        c_path = get_data_dir() / "client_secret.json"
        t_path = get_data_dir() / "token.json"
        if not c_path.is_file():
            raise HTTPException(
                status_code=400,
                detail="Client secret file not found. Upload client_secret.json first.",
            )

        background_tasks.add_task(
            run_youtube_oauth_flow,
            client_secret_path=c_path,
            token_path=t_path,
        )
        return {
            "success": True,
            "status": "running",
            "message": "Google OAuth consent opened in browser.",
        }

    @app.get("/api/settings/youtube-oauth/status")
    async def get_youtube_oauth_status_endpoint() -> dict[str, Any]:
        """Return real-time YouTube Data API v3 OAuth configuration status."""
        active_cfg = pipe.config if isinstance(getattr(pipe, "config", None), Settings) else cfg
        return get_youtube_oauth_status(active_cfg)

    @app.post("/api/settings/test-provider")
    async def test_provider(req: TestProviderRequest) -> dict[str, Any]:
        """Test connectivity and list available models for an LLM provider."""
        from autoshorts.core.providers import get_provider

        active_cfg = pipe.config if isinstance(getattr(pipe, "config", None), Settings) else cfg
        try:
            provider = get_provider(
                name=req.provider,
                api_key=req.api_key,
                settings=active_cfg,
            )
            models = await asyncio.to_thread(provider.list_models)
            return {
                "success": True,
                "provider": provider.name,
                "models": models,
                "privacy_badge": provider.privacy_badge,
            }
        except Exception as e:
            logger.error("Provider test failed for %s: %s", req.provider, e)
            raise HTTPException(status_code=400, detail=str(e)) from e

    # -------------------------------------------------------------------------
    # In-App Analytics & Telemetry API
    # -------------------------------------------------------------------------

    @app.get("/api/analytics/overview")
    async def get_analytics_overview(days: int = 30) -> dict[str, Any]:
        """Return 30-day KPI aggregates, retention metrics, and winning style recipe."""
        active_cfg = pipe.config if isinstance(getattr(pipe, "config", None), Settings) else cfg
        try:
            from autoshorts.analytics.attribution import PerformanceAttributionEngine
            from autoshorts.analytics.engine import AnalyticsEngine

            engine = AnalyticsEngine(state_manager=state_mgr, config=active_cfg)
            attr_engine = PerformanceAttributionEngine(state_manager=state_mgr)

            if hasattr(engine, "get_overview"):
                overview = engine.get_overview(days=days)
            elif hasattr(engine, "compute_overview"):
                overview = engine.compute_overview(days=days)
            else:
                overview = {}

            if hasattr(attr_engine, "get_winning_recipe"):
                winning = attr_engine.get_winning_recipe()
            elif hasattr(attr_engine, "get_attribution"):
                winning = attr_engine.get_attribution()
            else:
                winning = None

            return {
                "success": True,
                "overview": overview.model_dump() if hasattr(overview, "model_dump") else overview,
                "winning_recipe": winning.model_dump()
                if hasattr(winning, "model_dump")
                else winning,
            }
        except Exception as e:
            logger.debug("Analytics overview fallback: %s", e)
            return {
                "success": True,
                "overview": {
                    "total_views": 0,
                    "total_likes": 0,
                    "avg_retention_pct": 0.0,
                    "avg_svs": 0.0,
                    "estimated_revenue_usd": 0.0,
                    "video_count": 0,
                },
                "winning_recipe": None,
            }

    @app.get("/api/analytics/breakdown")
    async def get_analytics_breakdown() -> dict[str, Any]:
        """Return SVS viral score breakdowns by layout, caption preset, and background."""
        try:
            from autoshorts.analytics.attribution import PerformanceAttributionEngine

            attr_engine = PerformanceAttributionEngine(state_manager=state_mgr)
            if hasattr(attr_engine, "get_breakdowns"):
                b = attr_engine.get_breakdowns()
            elif hasattr(attr_engine, "get_breakdown"):
                b = attr_engine.get_breakdown()
            elif hasattr(attr_engine, "compute_breakdown"):
                b = attr_engine.compute_breakdown()
            else:
                b = {}

            return {
                "success": True,
                "breakdowns": b.model_dump() if hasattr(b, "model_dump") else b,
            }
        except Exception as e:
            logger.debug("Analytics breakdown fallback: %s", e)
            return {
                "success": True,
                "breakdowns": {
                    "layout_modes": {},
                    "style_presets": {},
                    "background_categories": {},
                },
            }

    @app.get("/api/analytics/videos")
    async def get_analytics_videos(limit: int = 50) -> dict[str, Any]:
        """Return per-video telemetry and retention data."""
        try:
            from autoshorts.analytics.engine import AnalyticsEngine

            active_cfg = pipe.config if isinstance(getattr(pipe, "config", None), Settings) else cfg
            engine = AnalyticsEngine(state_manager=state_mgr, config=active_cfg)
            if hasattr(engine, "get_videos"):
                videos = engine.get_videos(limit=limit)
            elif hasattr(engine, "get_all_videos"):
                videos = engine.get_all_videos(limit=limit)
            elif hasattr(engine, "list_videos"):
                videos = engine.list_videos(limit=limit)
            else:
                videos = []

            return {
                "success": True,
                "videos": [v.model_dump() if hasattr(v, "model_dump") else v for v in videos],
            }
        except Exception as e:
            logger.debug("Analytics videos fallback: %s", e)
            return {"success": True, "videos": []}

    @app.post("/api/analytics/sync")
    async def sync_analytics_endpoint(
        payload: AnalyticsSyncPayload | None = None,
    ) -> dict[str, Any]:
        """Trigger live YouTube stats sync for all uploaded videos."""
        try:
            from autoshorts.analytics.engine import AnalyticsEngine

            active_cfg = pipe.config if isinstance(getattr(pipe, "config", None), Settings) else cfg
            engine = AnalyticsEngine(state_manager=state_mgr, config=active_cfg)
            force = payload.force if payload else False
            if hasattr(engine, "sync_all"):
                synced = engine.sync_all(force=force)
            elif hasattr(engine, "sync"):
                synced = engine.sync(force=force)
            else:
                synced = {"status": "ok", "synced": 0}

            return {
                "success": True,
                "synced": synced.model_dump() if hasattr(synced, "model_dump") else synced,
            }
        except Exception as e:
            logger.debug("Analytics sync fallback: %s", e)
            return {"success": True, "synced": {"updated": 0, "message": str(e)}}

    @app.post("/api/analytics/clone-style/{job_id}")
    async def clone_style_recipe(job_id: str) -> dict[str, Any]:
        """Extract winning layout, caption preset and dopamine level from a top-performing job."""
        try:
            from autoshorts.analytics.attribution import PerformanceAttributionEngine

            attr_engine = PerformanceAttributionEngine(state_manager=state_mgr)
            if hasattr(attr_engine, "clone_style"):
                recipe = attr_engine.clone_style(job_id)
                return {"success": True, "job_id": job_id, "style": recipe}
        except Exception:
            pass

        job = state_mgr.get_job(job_id) if hasattr(state_mgr, "get_job") else None
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        bg_cat = getattr(job, "background_category", None)
        if not bg_cat and job.selected_clips:
            bg_cat = job.selected_clips[0].background_style

        recipe = {
            "layout_mode": getattr(job, "layout_mode", "split_50_50"),
            "style_preset": getattr(job, "style_preset", "hormozi"),
            "background_category": bg_cat or "minecraft",
            "dopamine_level": getattr(job, "dopamine_level", "medium"),
            "color_grading": getattr(job, "color_grading", "none"),
            "text_transform": getattr(job, "text_transform", "uppercase"),
            "subtitle_position": getattr(job, "subtitle_position", "center"),
            "hook_banner_style": getattr(job, "hook_banner_style", "neon_box"),
        }
        return {"success": True, "job_id": job_id, "style": recipe}

    # -------------------------------------------------------------------------
    # Built-In Viral Research Agent API
    # -------------------------------------------------------------------------

    @app.get("/api/research/trending")
    async def get_trending_sources_endpoint(
        niche: str = "ai_tech",
        query: str | None = None,
        limit: int = 9,
    ) -> dict[str, Any]:
        """Discover live trending videos and podcasts with viral potential scores."""
        from autoshorts.research_agent.trending_sources import discover_trending_sources

        videos = await asyncio.to_thread(
            discover_trending_sources,
            niche=niche,
            custom_query=query,
            limit=limit,
        )
        return {
            "success": True,
            "niche": niche,
            "count": len(videos),
            "videos": [v.model_dump() for v in videos],
        }

    @app.get("/api/research/niches")
    async def get_niche_payouts_endpoint(
        views: int | None = None,
        ctr: float = 2.5,
        niche_id: str | None = None,
    ) -> dict[str, Any]:
        """Return highest payout niche matrix and optional projected revenue calculation."""
        from autoshorts.research_agent.payout_matrix import (
            calculate_projected_payout,
            get_niche_payout_matrix,
        )

        matrix = get_niche_payout_matrix()
        projection = None
        if views is not None:
            target_niche = niche_id or "ai_tech"
            projection = calculate_projected_payout(
                niche_id=target_niche,
                projected_views=views,
                related_video_ctr_pct=ctr,
            )

        return {
            "success": True,
            "niches": [n.model_dump() for n in matrix],
            "projection": projection,
        }

    @app.get("/api/research/music")
    async def get_viral_music_endpoint(genre: str = "all") -> dict[str, Any]:
        """Return curated royalty-free BGM catalog and live trending commercial Shorts sounds."""
        from autoshorts.research_agent.song_recommender import (
            get_trending_shorts_audio,
            get_viral_bgm_catalog,
        )

        bgm = get_viral_bgm_catalog()
        trending = get_trending_shorts_audio(genre=genre)
        return {
            "success": True,
            "bgm_catalog": [b.model_dump() for b in bgm],
            "trending_shorts_audio": [t.model_dump() for t in trending],
        }

    @app.post("/api/research/music/download")
    async def download_viral_bgm_endpoint(payload: MusicDownloadPayload) -> dict[str, Any]:
        """Download or procedural-synthesize viral BGM into assets/music/."""
        from autoshorts.research_agent.song_recommender import download_viral_bgm

        res = await asyncio.to_thread(
            download_viral_bgm,
            track_id=payload.track_id,
            custom_url=payload.custom_url,
        )
        return res

    @app.post("/api/research/brief")
    async def generate_strategy_brief_endpoint(payload: ResearchBriefPayload) -> dict[str, Any]:
        """Generate bespoke AI viral strategy brief combining sources, payout, and audio."""
        from autoshorts.research_agent.research_brief_agent import generate_viral_strategy_brief

        active_cfg = pipe.config if isinstance(getattr(pipe, "config", None), Settings) else cfg
        brief = await asyncio.to_thread(
            generate_viral_strategy_brief,
            niche=payload.niche,
            custom_query=payload.custom_query,
            config=active_cfg,
        )
        return {
            "success": True,
            "brief": brief.model_dump(),
        }

    @app.post("/api/research/pipeline-handoff")
    async def pipeline_handoff_endpoint(payload: PipelineHandoffPayload) -> dict[str, Any]:
        """⚡ 1-Click Send to Pipeline: queue trending video URL with recommended profile & related video link."""
        if not payload.url or not payload.url.strip():
            raise HTTPException(status_code=400, detail="Video URL is required")

        focus_prompt = (
            payload.focus_prompt or f"Focus on peak high-impact insight in {payload.niche}"
        )
        jobs = pipe.submit_links(
            links_input=payload.url,
            custom_instructions=focus_prompt,
        )

        for job in jobs:
            job.style_preset = payload.recommended_studio_profile
            job.layout_mode = (
                "split_50_50"
                if payload.recommended_studio_profile == "hormozi"
                else "podcast_70_30"
            )
            set_job_attr(job, "background_category", payload.recommended_background)
            if job.selected_clips:
                job.selected_clips[0].background_style = payload.recommended_background
            job.link_related_video = True
            set_job_attr(job, "related_video_url", payload.url)
            if payload.auto_upload:
                set_job_attr(job, "auto_upload", True)
            if hasattr(state_mgr, "save_job"):
                state_mgr.save_job(job)

        return {
            "success": True,
            "job_id": jobs[0].id if jobs else None,
            "jobs": [j.model_dump() for j in jobs],
        }

    # -------------------------------------------------------------------------
    # Autonomous Faceless AI Shorts Studio Engine API
    # -------------------------------------------------------------------------

    @app.get("/api/moneyprinter/voices")
    @app.get("/api/faceless/voices")
    async def get_moneyprinter_voices_endpoint(niche: str | None = None) -> dict[str, Any]:
        """Return free Microsoft Edge Neural TTS voice catalog and historical topics."""
        from autoshorts.research_agent.moneyprinter import VOICE_CATALOG

        past_topics: list[str] = []
        if hasattr(state_mgr, "get_topic_history"):
            try:
                past_topics = (
                    state_mgr.get_topic_history(niche=niche)
                    if niche
                    else state_mgr.get_topic_history()
                )
            except Exception as e:
                logger.debug("Failed fetching topic history: %s", e)

        return {
            "success": True,
            "voices": VOICE_CATALOG,
            "past_topics": past_topics,
        }

    @app.post("/api/moneyprinter/generate-script")
    @app.post("/api/faceless/generate-script")
    async def preview_moneyprinter_script(payload: MoneyPrinterScriptPayload) -> dict[str, Any]:
        """Preview viral faceless script, hook, and keywords before rendering."""
        from autoshorts.research_agent.moneyprinter import generate_faceless_script

        active_cfg = pipe.config if isinstance(getattr(pipe, "config", None), Settings) else cfg
        past_topics: list[str] = []
        if hasattr(state_mgr, "get_topic_history"):
            try:
                past_topics = state_mgr.get_topic_history(niche=payload.niche)
            except Exception as e:
                logger.debug("Failed fetching topic history: %s", e)

        res = await asyncio.to_thread(
            generate_faceless_script,
            niche=payload.niche,
            topic=payload.topic,
            language=payload.language,
            config=active_cfg,
            past_topics=past_topics,
        )
        return {
            "success": True,
            "script": res.model_dump(),
        }

    @app.post("/api/moneyprinter/create")
    @app.post("/api/faceless/create")
    async def create_faceless_short_endpoint(
        request: Request,
        background_tasks: BackgroundTasks,
        async_mode: bool = False,
    ) -> dict[str, Any]:
        """Create and render a complete faceless AI Short using edge-tts and FFmpeg."""
        from autoshorts.research_agent.models import FacelessShortRequest
        from autoshorts.research_agent.moneyprinter import create_faceless_short_job

        body = await request.json()
        req = FacelessShortRequest(**body)

        if async_mode:
            background_tasks.add_task(create_faceless_short_job, req, pipe, state_mgr)
            return {
                "success": True,
                "status": "queued",
                "message": "Faceless Short generation task running in background.",
            }

        job_data = await asyncio.to_thread(create_faceless_short_job, req, pipe, state_mgr)
        return {
            "success": True,
            "status": "completed",
            "job": job_data,
        }

    @app.get("/api/moneyprinter/topics")
    @app.get("/api/faceless/topics")
    async def get_faceless_topics_endpoint(niche: str | None = None) -> dict[str, Any]:
        """Return historical generated topics to prevent duplication."""
        past_topics: list[dict[str, Any]] = []
        if hasattr(state_mgr, "get_topic_history"):
            try:
                raw_topics = (
                    state_mgr.get_topic_history(niche=niche)
                    if niche
                    else state_mgr.get_topic_history()
                )
                past_topics = [
                    {"topic": t, "niche": niche or "general", "status": "✓ Cached in SQLite"}
                    for t in raw_topics
                ]
            except Exception as e:
                logger.debug("Failed fetching topics: %s", e)
        return {"success": True, "topics": past_topics}

    return app


# Default singleton app for uvicorn run
app = create_app()


def run_dashboard(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    open_edge: bool = True,
) -> None:
    """Run uvicorn server and optionally launch browser tab."""
    import uvicorn

    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(1.2, launch_browser, args=(url, open_edge)).start()

    logger.info("Starting AutoShorts Dashboard at %s", url)
    uvicorn.run(app, host=host, port=port, log_level="info")
