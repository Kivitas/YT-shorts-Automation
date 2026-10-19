"""FastAPI Web Dashboard and REST API for AutoShorts.

Provides real-time job queue monitoring, quota meters, setting controls,
Spark import parsing, and video previews.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from autoshorts.core.config import Settings, load_config
from autoshorts.core.state_manager import StateManager
from autoshorts.integrations.spark import parse_spark_import
from autoshorts.media.encoder_probe import probe_encoder
from autoshorts.pipeline import ShortsPipeline

logger = logging.getLogger("autoshorts.dashboard")

DASHBOARD_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = DASHBOARD_DIR / "templates"
STATIC_DIR = DASHBOARD_DIR / "static"


# -----------------------------------------------------------------------------
# Request & Response Models
# -----------------------------------------------------------------------------

class JobSubmitRequest(BaseModel):
    links: str = Field(..., description="Newline separated links with optional : instructions")
    custom_instructions: str | None = Field(default=None)
    provider: str | None = Field(default=None)
    background: str | None = Field(default=None)
    dopamine: str | None = Field(default=None)
    upload_mode: str | None = Field(default=None)
    auto_upload: bool = Field(default=False)
    immediate_upload: bool = Field(default=False)


class SparkImportRequest(BaseModel):
    text: str = Field(..., description="Markdown or text containing Spark clips")


class SchedulerToggleRequest(BaseModel):
    paused: bool | None = None


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
) -> FastAPI:
    """Factory to instantiate the AutoShorts FastAPI dashboard app."""
    cfg = config or load_config()
    state_mgr = state_manager or (pipeline.state_manager if pipeline else StateManager())
    pipe = pipeline or ShortsPipeline(config=cfg, state_manager=state_mgr)

    app = FastAPI(
        title="AutoShorts Dashboard",
        version="3.2.0",
        description="On-Device Video Clipping and Quota Pacing Orchestrator",
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
            return templates.TemplateResponse(request=request, name="index.html", context={"config": cfg})
        return HTMLResponse(
            "<html><body><h1>AutoShorts Dashboard</h1><p>Templates not found. Use API endpoints.</p></body></html>"
        )

    # -------------------------------------------------------------------------
    # Jobs Management API
    # -------------------------------------------------------------------------

    @app.post("/api/jobs/submit")
    async def submit_jobs(req: JobSubmitRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
        """Submit links, parse them, and kick off background processing."""
        if not req.links.strip():
            raise HTTPException(status_code=400, detail="No links provided")

        jobs = pipe.submit_links(
            links_input=req.links,
            custom_instructions=req.custom_instructions,
        )

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
        """Return list of most recent clipping jobs."""
        jobs = state_mgr.list_jobs(limit=limit)
        return {
            "jobs": [j.model_dump() for j in jobs],
        }

    @app.get("/api/jobs/{job_id}")
    async def get_job_details(job_id: str) -> dict[str, Any]:
        """Return specific job state and recorded execution stages."""
        job = state_mgr.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        stages = state_mgr.get_job_stages(job_id)
        return {
            "job": job.model_dump(),
            "stages": stages,
        }

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

    # -------------------------------------------------------------------------
    # Quota & Scheduler API
    # -------------------------------------------------------------------------

    @app.get("/api/quota")
    async def get_quota() -> dict[str, Any]:
        """Return current Pacific-Time daily quota usage and limits."""
        status = pipe.quota_tracker.get_status()
        upload_b = status.get("upload_bucket", {})
        general_b = status.get("general_bucket", {})
        daily_c = status.get("daily_cap", {})

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
