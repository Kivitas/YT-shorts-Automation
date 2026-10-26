"""End-to-End Pipeline Orchestrator for AutoShorts.

Coordinates the complete clipping lifecycle:
Input links -> Validate & Metadata -> Fetch transcript (with Whisper fallback) ->
Window slicing -> SemIf scoring & selection -> Multimodal EDL planning ->
Downloader precision cut (yt-dlp --download-sections) -> Subtitles (.ass generation) ->
FFmpeg Compositor (split-screen + dopamine effects) -> Optional preview generation ->
Enqueue to Scheduler or Immediate Upload -> Post-upload Verification -> Path-guarded Cleanup.

Includes stage artifact caching, failure resumption, state transitions, and concurrency throttling.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import autoshorts.decision.window_slicer as _window_slicer
from autoshorts.cleanup.safety_cleaner import SafetyCleaner
from autoshorts.core.config import Settings, load_config
from autoshorts.core.contracts import (
    Cleaner,
    Compositor,
    Decider,
    Downloader,
    EditPlanner,
    LLMProvider,
    Transcriber,
    Uploader,
)
from autoshorts.core.exceptions import (
    AutoShortsError,
    DownloadError,
    MetadataFetchError,
    QuotaExhaustedError,
    TranscriptNotFoundError,
    UploadError,
)
from autoshorts.core.job_lock import JobLockedError, job_lock
from autoshorts.core.models import (
    Job,
    JobStatus,
    JumpCut,
    UploadResult,
    VideoMetadata,
    resolve_verification,
)
from autoshorts.core.paths import get_output_dir, get_work_dir
from autoshorts.core.providers import get_provider
from autoshorts.core.state_manager import StateManager
from autoshorts.decision.orchestrator import SemIfDecider
from autoshorts.decision.window_slicer import slice_transcript_windows

generate_candidate_windows = getattr(
    _window_slicer, "generate_candidate_windows", slice_transcript_windows
)
from autoshorts.discovery.url_validator import ParsedLink, parse_links_input
from autoshorts.edit_planner.edl_schema import create_empty_edl
from autoshorts.edit_planner.multimodal_planner import MultimodalEditPlanner
from autoshorts.media.ass_styler import write_ass_file
from autoshorts.media.audio_master import normalize_two_pass
from autoshorts.media.compositor import (
    FasterWhisperTranscriber,
    FFmpegCompositor,
    count_decode_errors,
    extract_16khz_mono_wav,
)
from autoshorts.media.dopamine_effects import pick_background_asset
from autoshorts.media.downloader import Downloader as MediaDownloader
from autoshorts.media.encoder_probe import get_ffmpeg_path
from autoshorts.media.ffmpeg_tools import check_delivery
from autoshorts.media.remotion_overlay import (
    REMOTION_PROJECT_DIR,
    RemotionRenderError,
    RemotionUnavailableError,
    build_caption_timeline,
    is_remotion_ready,
    overlay_captions,
    render_caption_overlay,
    write_caption_plan,
)
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.scheduler import SpreadScheduler
from autoshorts.transcript.fetcher import TranscriptFetcher
from autoshorts.uploader.description_builder import (
    build_short_description,
    build_short_title,
    build_viral_hashtags,
)
from autoshorts.uploader.errors import QuotaExceededError
from autoshorts.uploader.local_exporter import LocalExportUploader
from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader
from autoshorts.uploader.youtube_api import YouTubeApiUploader

logger = logging.getLogger("autoshorts.pipeline")


def select_gameplay_background(
    category: str | None = None,
    assets_dir: Path | None = None,
    job_index: int = 0,
) -> tuple[Path | None, float]:
    """Select gameplay background from assets/backgrounds/<category>/.

    Reads manifest.json / manifest.jsonl if present, rotates/randomizes among available
    candidates, and gracefully falls back with a clear warning log when empty.
    """
    import random

    import autoshorts.media.dopamine_effects as _de
    from autoshorts.core.paths import get_assets_dir
    from autoshorts.media.background_fetcher import normalize_category

    # If pick_background_asset has been monkeypatched in tests, honor the mock
    if globals().get("pick_background_asset") is not _de.pick_background_asset:
        try:
            return globals()["pick_background_asset"](category=category, assets_dir=assets_dir)
        except TypeError:
            return globals()["pick_background_asset"]()

    raw_cat = (category or "minecraft").strip().lower()
    if raw_cat == "none":
        return None, 0.0

    base_assets = assets_dir or get_assets_dir()
    clean_cat = normalize_category(raw_cat) if raw_cat != "auto" else "minecraft"

    # Category directory
    bg_dir = base_assets / "backgrounds" / clean_cat
    if not bg_dir.is_dir():
        bg_dir = base_assets / "backgrounds" / raw_cat

    candidates: list[Path] = []

    # 1. Check manifests if present in category folder
    if bg_dir.is_dir():
        manifest_file = bg_dir / "manifest.json"
        manifest_l = bg_dir / "manifest.jsonl"
        if manifest_file.is_file():
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                items = (
                    data.get("videos")
                    or data.get("files")
                    or (data if isinstance(data, list) else [])
                )
                for item in items:
                    fname = (
                        item.get("filename") or item.get("path")
                        if isinstance(item, dict)
                        else str(item)
                    )
                    if fname:
                        fp = bg_dir / fname if not Path(fname).is_absolute() else Path(fname)
                        if fp.is_file() and fp.stat().st_size > 1024:
                            candidates.append(fp)
            except Exception as m_err:
                logger.debug("Failed to parse background manifest.json in %s: %s", bg_dir, m_err)
        elif manifest_l.is_file():
            try:
                for line in manifest_l.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        item = json.loads(line.strip())
                        fname = (
                            item.get("filename") or item.get("path")
                            if isinstance(item, dict)
                            else str(item)
                        )
                        if fname:
                            fp = bg_dir / fname if not Path(fname).is_absolute() else Path(fname)
                            if fp.is_file() and fp.stat().st_size > 1024:
                                candidates.append(fp)
            except Exception as ml_err:
                logger.debug("Failed to parse background manifest.jsonl in %s: %s", bg_dir, ml_err)

        # 2. If no manifest entries, scan directory directly for video files
        if not candidates:
            valid_exts = {".mp4", ".mov", ".mkv", ".webm"}
            candidates = [
                p
                for p in bg_dir.iterdir()
                if p.is_file() and p.suffix.lower() in valid_exts and p.stat().st_size > 1024
            ]

    # 3. If candidates found, rotate / randomize selection
    if candidates:
        chosen = candidates[(job_index or random.randint(0, len(candidates) - 1)) % len(candidates)]
        offset = round(random.uniform(0.0, 120.0), 2)
        logger.info(
            "Selected gameplay background for '%s' (%s, offset=%.1fs) from %d candidate(s).",
            clean_cat,
            chosen.name,
            offset,
            len(candidates),
        )
        return chosen, offset

    # 4. Graceful fallback when empty with a clear log
    logger.warning(
        "No gameplay background video found in assets/backgrounds/%s (manifest/directory empty). "
        "Gracefully falling back to procedural/lavfi background.",
        clean_cat,
    )
    return pick_background_asset(category=clean_cat, assets_dir=assets_dir, auto_fetch=False)


def _is_nonempty_file(path: Path | None) -> bool:
    """True when ``path`` names an existing, non-empty regular file."""
    if path is None:
        return False
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _upload_succeeded(result: Any) -> bool:
    """An uploader result counts as a success only if it is not flagged and has a video id."""
    if getattr(result, "success", True) is False:
        return False
    return bool(str(getattr(result, "video_id", "") or "").strip())


class ShortsPipeline:
    """The master pipeline orchestrator for AutoShorts."""

    def __init__(
        self,
        config: Settings | None = None,
        state_manager: StateManager | None = None,
        llm_provider: LLMProvider | None = None,
        decider: Decider | None = None,
        downloader: Downloader | None = None,
        transcript_fetcher: TranscriptFetcher | None = None,
        transcriber: Transcriber | None = None,
        edit_planner: EditPlanner | None = None,
        compositor: Compositor | None = None,
        uploader: Uploader | None = None,
        cleaner: Cleaner | None = None,
        scheduler: SpreadScheduler | None = None,
        quota_tracker: QuotaTracker | None = None,
        concurrency: int | None = None,
    ) -> None:
        """Initialize pipeline with injected or default components."""
        self.config = config or load_config()
        self.state_manager = state_manager or StateManager()
        if concurrency is None:
            resolved_concurrency = int(os.environ.get("AUTOSHORTS_CONCURRENCY", "3"))
        else:
            resolved_concurrency = concurrency
        self.concurrency = max(1, resolved_concurrency)
        self._render_lock = threading.Semaphore(self.concurrency)
        self._render_tls = threading.local()

        # Quota tracker
        self.quota_tracker = quota_tracker or QuotaTracker(
            state_manager=self.state_manager,
            config=self.config,
        )

        # LLM Provider
        self.llm_provider = llm_provider
        if self.llm_provider is None:
            try:
                self.llm_provider = get_provider(settings=self.config)
            except Exception as e:
                logger.warning("Could not initialize default LLM provider: %s", e)

        # Decider
        self.decider = decider or SemIfDecider(
            provider=self.llm_provider,
            settings=self.config,
        )

        # Downloader & Ingest
        self.downloader = downloader or MediaDownloader(settings=self.config)
        self.transcript_fetcher = transcript_fetcher or TranscriptFetcher(
            cookies_from_browser=getattr(self.config.media, "cookies_from_browser", None),
        )

        # Transcriber (Whisper CPU int8)
        self.transcriber = transcriber or FasterWhisperTranscriber(
            model_name=self.config.media.whisper_model,
        )

        # Edit Planner
        self.edit_planner = edit_planner or MultimodalEditPlanner(
            provider=self.llm_provider,
            config=self.config,
        )

        # FFmpeg Compositor
        self.compositor = compositor or FFmpegCompositor(config=self.config)

        # Uploader
        if uploader is not None:
            self.uploader = uploader
        elif self.config.upload.mode == "local":
            self.uploader = LocalExportUploader()
        elif self.config.upload.mode == "studio":
            self.uploader = PlaywrightStudioUploader(config=self.config)
        else:
            try:
                self.uploader = YouTubeApiUploader(
                    config=self.config,
                    quota_tracker=self.quota_tracker,
                )
            except Exception as e:
                logger.warning(
                    "YouTube API uploader initialization deferred or failed: %s. Using LocalExportUploader fallback.",
                    e,
                )
                self.uploader = LocalExportUploader()

        # Safety Cleaner
        self.cleaner = cleaner or SafetyCleaner(
            config=self.config,
            state_manager=self.state_manager,
        )

        # Spread Scheduler
        self.scheduler = scheduler or SpreadScheduler(
            state_mgr=self.state_manager,
            quota_tracker=self.quota_tracker,
            uploader=self.uploader,
            config=self.config,
            cleaner=self.cleaner,
        )

        # Reconcile stranded jobs from prior interrupted processes
        try:
            reconciled = self.state_manager.reconcile_stranded_jobs()
            if reconciled > 0:
                logger.info(
                    "Reconciled %d stranded job(s) from prior session to FAILED.", reconciled
                )
        except Exception as r_err:
            logger.warning("Failed to reconcile stranded jobs on startup: %s", r_err)

    # -------------------------------------------------------------------------
    # Public Ingestion API
    # -------------------------------------------------------------------------

    def submit_links(
        self,
        links_input: str | Sequence[str] | Path,
        custom_instructions: str | None = None,
        dedupe: bool = True,
    ) -> list[Job]:
        """Parse, validate, and enqueue raw links input into the SQLite database."""
        # Normalize lines: if line has ' : ' but no '|', convert to ' | ' for url_validator compatibility
        normalized_input = links_input
        if isinstance(links_input, str):
            lines = []
            for l in links_input.splitlines():
                stripped = l.strip()
                if stripped and "|" not in stripped and " : " in stripped:
                    parts = stripped.split(" : ", 1)
                    lines.append(f"{parts[0].strip()} | {parts[1].strip()}")
                else:
                    lines.append(l)
            normalized_input = "\n".join(lines)
        elif isinstance(links_input, Sequence) and not isinstance(links_input, (str, bytes)):
            lines = []
            for l in links_input:
                if isinstance(l, str) and "|" not in l and " : " in l:
                    parts = l.split(" : ", 1)
                    lines.append(f"{parts[0].strip()} | {parts[1].strip()}")
                else:
                    lines.append(l)
            normalized_input = lines

        parsed_links: list[ParsedLink] = parse_links_input(
            input_data=normalized_input,
            defaults=self.config.job_defaults,
            global_instructions=custom_instructions,
            dedupe=dedupe,
        )

        jobs: list[Job] = []
        for pl in parsed_links:
            job = Job(
                source_url=pl.canonical_url,
                custom_instructions=pl.custom_instructions or pl.focus_prompt,
                status=JobStatus.QUEUED,
            )
            self.state_manager.save_job(job)
            jobs.append(job)
            logger.info("Enqueued job %s for %s", job.id, job.source_url)

        return jobs

    # -------------------------------------------------------------------------
    # Execution & Lifecycle Orchestration
    # -------------------------------------------------------------------------

    def process_job(
        self,
        job_or_id: Job | str,
        auto_upload: bool = False,
        immediate_upload: bool = False,
        background_category: str | None = None,
        dopamine_level: str | None = None,
        style_preset: str | None = None,
        privacy_status: str | None = None,
    ) -> Job:
        """Run the pipeline for one job, unless another process is already working on it.

        The CLI and the dashboard share one database, so a queued job can be picked up by both.
        The per-job file lock makes the second process skip it instead of racing the first.
        """
        job = self._resolve_job(job_or_id)
        try:
            with job_lock(get_work_dir() / job.id, job.id):
                return self._process_job_locked(
                    job,
                    auto_upload=auto_upload,
                    immediate_upload=immediate_upload,
                    background_category=background_category,
                    dopamine_level=dopamine_level,
                    style_preset=style_preset,
                    privacy_status=privacy_status,
                )
        except JobLockedError as exc:
            logger.info("Skipping job %s: %s", job.id, exc)
            return self.state_manager.get_job(job.id) or job

    def _process_job_locked(
        self,
        job_or_id: Job | str,
        auto_upload: bool = False,
        immediate_upload: bool = False,
        background_category: str | None = None,
        dopamine_level: str | None = None,
        style_preset: str | None = None,
        privacy_status: str | None = None,
    ) -> Job:
        job = self._resolve_job(job_or_id)

        if style_preset is not None:
            job.style_preset = style_preset
        if privacy_status is not None:
            job.privacy_status = privacy_status

        # If job is already completed or cleaned, return immediately
        if job.status in (JobStatus.COMPLETED, JobStatus.CLEANED):
            return job

        # Only the local, CPU/IO-heavy stages (yt-dlp cut, EDL, ASS, ffmpeg) take a render
        # slot, one per stage. Metadata, transcript, SemIf and uploads run unthrottled, and
        # no stage holds the slot while calling another stage, so the semaphore is never
        # acquired twice on one thread.
        stage = "queued"
        try:
            # Stage 1: Metadata Fetch
            if job.status == JobStatus.QUEUED:
                stage = "metadata"
                self._run_metadata_stage(job)

            # Stage 2: Transcript Fetch (with Whisper Fallback)
            if job.status == JobStatus.METADATA_FETCHED:
                stage = "transcript"
                self._run_transcript_stage(job)

            # Stage 3: Window Slicing
            if job.status == JobStatus.TRANSCRIBED:
                stage = "slicing"
                self._run_slicing_stage(job)

            # Stage 4: SemIf Evaluation & Non-overlapping Selection
            if job.status == JobStatus.WINDOWS_SLICED:
                stage = "semif"
                self._run_semif_stage(job, background_category=background_category)

            # Stage 5: Downloader Precision Cut
            if job.status == JobStatus.SEMIF_EVALUATED:
                stage = "download"
                with self._render_slot():
                    self._run_download_stage(job)

            # Stage 6: Multimodal Edit Planning (EDL)
            if job.status == JobStatus.DOWNLOADING_SECTION and job.edl is None:
                stage = "edit_planning"
                with self._render_slot():
                    self._run_edl_stage(job)

            # Stage 7: Subtitle (.ass) Generation
            if job.status == JobStatus.DOWNLOADING_SECTION:
                stage = "subtitles"
                with self._render_slot():
                    self._run_subtitles_stage(job)

            # Stage 8: Compositing (split-screen + dopamine effects) & Preview
            if job.status == JobStatus.SUBTITLES_GENERATED:
                stage = "compositing"
                with self._render_slot():
                    self._run_compositing_stage(
                        job,
                        background_category=background_category,
                        dopamine_level=dopamine_level,
                    )

            # Stage 9: Upload / Schedule / Review (network only, no render slot)
            if job.status == JobStatus.READY_FOR_REVIEW:
                stage = "upload"
                if immediate_upload:
                    self._run_immediate_upload(job)
                elif auto_upload or self.config.job_defaults.auto_approve:
                    self._run_schedule_upload(job)

            return job

        except Exception as e:
            logger.exception("Job %s failed during pipeline execution", job.id)
            self._mark_failed(job, stage, e)
            raise

    @contextmanager
    def _render_slot(self) -> Iterator[None]:
        """Hold one render slot for CPU/IO-heavy local work.

        ``threading.Semaphore`` is not reentrant, so a thread that already holds the slot
        passes straight through instead of deadlocking on a nested acquire. The slot is
        released by ``with`` even when the body raises.
        """
        if getattr(self._render_tls, "held", False):
            yield
            return
        with self._render_lock:
            self._render_tls.held = True
            try:
                yield
            finally:
                self._render_tls.held = False

    def _mark_failed(self, job: Job, stage: str, err: BaseException) -> None:
        """Mark a job FAILED while keeping its artifacts, and remember which stage failed.

        Direct assignment is used so that a state-machine complaint can never hide the
        original error. ``failed_stage`` is what ``retry_job`` uses to pick a resume point.
        """
        try:
            job.record_diagnostic("pipeline", failed_stage=stage)
            job.status = JobStatus.FAILED
            job.error_message = str(err)
            self.state_manager.save_job(job)
        except Exception as save_err:  # pragma: no cover - best effort, original error wins
            logger.warning("Job %s: could not persist failure state: %s", job.id, save_err)

    def _resume_status_for(self, job: Job) -> JobStatus | None:
        """Choose the earliest stage whose inputs still exist after a failure.

        Returns None when nothing usable is left, so the job restarts from QUEUED. A missing
        or empty artifact always moves the resume point back, so the stage is re-run instead
        of crashing later.
        """
        bucket = job.diagnostics.get("pipeline") if isinstance(job.diagnostics, dict) else None
        failed_stage = bucket.get("failed_stage") if isinstance(bucket, dict) else None
        if not failed_stage or not job.selected_clips:
            return None

        if failed_stage == "download":
            return JobStatus.SEMIF_EVALUATED

        raw = Path(job.raw_video_path) if job.raw_video_path else None
        if not _is_nonempty_file(raw):
            return None

        if failed_stage == "upload" and _is_nonempty_file(
            Path(job.rendered_video_path) if job.rendered_video_path else None
        ):
            return JobStatus.READY_FOR_REVIEW
        if failed_stage == "compositing" and job.edl is not None:
            subs = Path(job.subtitles_path) if job.subtitles_path else None
            if _is_nonempty_file(subs):
                return JobStatus.SUBTITLES_GENERATED
        if failed_stage in ("edit_planning", "subtitles", "compositing", "upload"):
            # Subtitles and the render are cheap to redo; the chain skips EDL when it exists.
            return JobStatus.DOWNLOADING_SECTION
        return None

    def process_batch(
        self,
        jobs_or_links: str | Sequence[str] | Sequence[Job] | Path,
        auto_upload: bool = False,
        immediate_upload: bool = False,
        background_category: str | None = None,
        dopamine_level: str | None = None,
        custom_instructions: str | None = None,
        style_preset: str | None = None,
        privacy_status: str | None = None,
    ) -> list[Job]:
        """Process a batch of links or Job objects sequentially."""
        if isinstance(jobs_or_links, (str, Path)) or (
            isinstance(jobs_or_links, Sequence)
            and len(jobs_or_links) > 0
            and isinstance(jobs_or_links[0], str)
        ):
            jobs = self.submit_links(jobs_or_links, custom_instructions=custom_instructions)
        else:
            jobs = list(jobs_or_links)  # type: ignore[arg-type]

        results: list[Job] = []
        for job in jobs:
            try:
                processed = self.process_job(
                    job,
                    auto_upload=auto_upload,
                    immediate_upload=immediate_upload,
                    background_category=background_category,
                    dopamine_level=dopamine_level,
                    style_preset=style_preset,
                    privacy_status=privacy_status,
                )
                results.append(processed)
            except Exception as err:
                logger.warning("Batch processing encountered error on job %s: %s", job.id, err)
                reloaded = self.state_manager.get_job(job.id) or job
                results.append(reloaded)

        return results

    def retry_job(
        self,
        job_id: str,
        auto_upload: bool = False,
        immediate_upload: bool = False,
        background_category: str | None = None,
        dopamine_level: str | None = None,
        style_preset: str | None = None,
        privacy_status: str | None = None,
    ) -> Job:
        """Reset a failed job to QUEUED and re-execute pipeline."""
        job = self.state_manager.get_job(job_id)
        if not job:
            raise AutoShortsError(f"Job {job_id} not found for retry")

        if job.status == JobStatus.FAILED:
            resume_status = self._resume_status_for(job)
            job.transition_to(JobStatus.QUEUED)
            job.error_message = None
            if resume_status is not None:
                # FAILED may only move to QUEUED, so the resume point is set directly,
                # the same way the stage guards already re-enter mid-pipeline.
                job.status = resume_status
            job.record_diagnostic(
                "pipeline",
                resume_from=(resume_status or JobStatus.QUEUED).value,
            )
            self.state_manager.save_job(job)

        return self.process_job(
            job,
            auto_upload=auto_upload,
            immediate_upload=immediate_upload,
            background_category=background_category,
            dopamine_level=dopamine_level,
            style_preset=style_preset,
            privacy_status=privacy_status,
        )

    # -------------------------------------------------------------------------
    # Internal Stage Implementations
    # -------------------------------------------------------------------------

    @staticmethod
    def _resolve_local_source(job: Job) -> Path | None:
        """Return the job's local source clip, but only when the job says it is local.

        Previously this inferred "local file" from ``Path(job.source_url).is_file()``, which
        meant a YouTube URL that happened to match a file on disk could silently be treated
        as the canonical source. Origin is now explicit via ``Job.origin_kind``.
        """
        if job.origin_kind == "youtube":
            return None

        # raw_video_path is always an explicit local path chosen by the caller.
        if job.raw_video_path:
            try:
                path = Path(job.raw_video_path)
                if path.is_file():
                    return path.resolve()
            except OSError:  # pragma: no cover - malformed path
                pass

        # source_url is only usable as a local clip when it is plainly a path, never a URL.
        source = (job.source_url or "").strip()
        if source and not source.lower().startswith(("http://", "https://")):
            try:
                path = Path(source)
                if path.is_file():
                    return path.resolve()
            except OSError:  # pragma: no cover - malformed path
                pass

        return None

    def _resolve_job(self, job_or_id: Job | str) -> Job:
        if isinstance(job_or_id, Job):
            return job_or_id
        loaded = self.state_manager.get_job(job_or_id)
        if not loaded:
            raise AutoShortsError(f"Job {job_or_id} not found in state database")
        return loaded

    def _run_metadata_stage(self, job: Job) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "metadata_fetch")
        try:
            # Check if job points to a pre-existing local source clip
            local_src: Path | None = self._resolve_local_source(job)
            if local_src is not None:
                if job.origin_kind is None:
                    job.origin_kind = "local_file"
                if not job.raw_video_path:
                    job.raw_video_path = str(local_src)

            if local_src is not None:
                # Probe video stream info locally without calling network downloader
                from autoshorts.media.downloader import probe_video_stream_info

                probe = probe_video_stream_info(local_src)
                dur = float(probe.get("duration") or 45.0)
                metadata = VideoMetadata(
                    title=local_src.stem or "Local Video",
                    channel="Local Source",
                    channel_url="",
                    video_url=str(local_src),
                    duration=dur,
                    video_id=local_src.stem[:11],
                )
            else:
                metadata = self.downloader.fetch_metadata(job.source_url)

            job.video_metadata = metadata
            job.transition_to(JobStatus.METADATA_FETCHED)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={
                    "title": metadata.title,
                    "channel": metadata.channel,
                    "duration": metadata.duration,
                    "video_id": metadata.video_id,
                },
            )
            self.state_manager.save_job(job)
            logger.info(
                "Job %s: Metadata extracted: '%s' by %s", job.id, metadata.title, metadata.channel
            )
        except Exception as e:
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )
            raise MetadataFetchError(f"Failed to fetch metadata for {job.source_url}: {e}") from e

    def _run_transcript_stage(self, job: Job) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "transcript_fetch")
        work_dir = get_work_dir() / job.id
        work_dir.mkdir(parents=True, exist_ok=True)
        transcript_cache = work_dir / "transcript.json"

        segments: list[dict[str, Any]] = []

        local_src: Path | None = self._resolve_local_source(job)

        try:
            # 1. Try fetching online subtitles via TranscriptFetcher if not a local file
            if not local_src and self.transcript_fetcher:
                try:
                    segs, _signal = self.transcript_fetcher.fetch_or_signal(job.source_url)
                    if segs:
                        segments = [
                            {
                                "start": s.start,
                                "duration": s.duration,
                                "end": s.end,
                                "text": s.text,
                            }
                            for s in segs
                        ]
                except Exception as ex:
                    logger.debug(
                        "Transcript fetcher returned error: %s; falling back to Whisper", ex
                    )

            # 2. If no captions or fetch failed, fallback to local FasterWhisper
            if not segments:
                logger.info(
                    "Job %s: No online captions available. Falling back to Whisper audio transcription.",
                    job.id,
                )
                audio_path = work_dir / f"{job.id}_audio.m4a"
                if local_src is not None:
                    try:
                        extract_16khz_mono_wav(
                            local_src, audio_path, ffmpeg_binary=get_ffmpeg_path()
                        )
                    except Exception:
                        audio_path = local_src
                else:
                    self.downloader.download_audio_only(job.source_url, audio_path)
                whisper_segs = self.transcriber.transcribe(audio_path)
                segments = [
                    {
                        "start": float(ws["start"]),
                        "end": float(ws["end"]),
                        "duration": float(ws["end"] - ws["start"]),
                        "text": ws["text"].strip(),
                    }
                    for ws in whisper_segs
                ]

            if not segments:
                raise TranscriptNotFoundError(
                    f"No speech transcript could be extracted for {job.source_url}"
                )

            # Cache transcript to disk
            with open(transcript_cache, "w", encoding="utf-8") as f:
                json.dump(segments, f, indent=2)

            job.transition_to(JobStatus.TRANSCRIBED)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={
                    "transcript_path": str(transcript_cache),
                    "segment_count": len(segments),
                },
            )
            self.state_manager.save_job(job)
            logger.info("Job %s: Transcribed %d segments.", job.id, len(segments))
        except Exception as e:
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )
            raise

    def _load_transcript_segments(self, job: Job) -> list[dict[str, Any]]:
        work_dir = get_work_dir() / job.id
        transcript_cache = work_dir / "transcript.json"
        if transcript_cache.is_file():
            with open(transcript_cache, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _run_slicing_stage(self, job: Job) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "window_slicing")
        try:
            segments = self._load_transcript_segments(job)
            if not segments:
                raise AutoShortsError(
                    f"Cannot slice windows: No cached transcript found for job {job.id}"
                )

            slicer_fn = generate_candidate_windows
            if slicer_fn is slice_transcript_windows and hasattr(
                _window_slicer, "generate_candidate_windows"
            ):
                slicer_fn = _window_slicer.generate_candidate_windows

            slicer_kwargs: dict[str, Any] = {
                "transcript": segments,
                "window_duration_seconds": float(self.config.job_defaults.max_duration_seconds),
                "stride_seconds": float(self.config.job_defaults.window_stride_seconds),
                "min_duration_seconds": float(self.config.job_defaults.min_duration_seconds),
                "max_duration_seconds": float(self.config.job_defaults.max_duration_seconds),
            }
            meta = getattr(job, "video_metadata", None)
            extra_slicer_kwargs: dict[str, Any] = {
                "heatmap": getattr(meta, "heatmap", None) if meta else None,
                "chapters": getattr(meta, "chapters", None) if meta else None,
                "sponsor_segments": getattr(meta, "sponsor_segments", None) if meta else None,
                "skip_sponsors": getattr(job, "skip_sponsors", True),
            }
            try:
                s_sig = inspect.signature(slicer_fn)
                has_varkw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in s_sig.parameters.values()
                )
                for k, v in extra_slicer_kwargs.items():
                    if has_varkw or k in s_sig.parameters:
                        slicer_kwargs[k] = v
            except Exception:
                pass

            candidates = slicer_fn(**slicer_kwargs)

            if not candidates:
                raise AutoShortsError(
                    f"No valid candidate windows could be sliced for job {job.id}"
                )

            job.candidate_windows = candidates
            job.transition_to(JobStatus.WINDOWS_SLICED)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={"candidate_count": len(candidates)},
            )
            self.state_manager.save_job(job)
            logger.info("Job %s: Sliced %d candidate windows.", job.id, len(candidates))
        except Exception as e:
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )
            raise

    def _run_semif_stage(self, job: Job, background_category: str | None = None) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "semif_evaluation")
        try:
            prompt = job.custom_instructions or self.config.job_defaults.focus_prompt
            scored = self.decider.score_candidates(job.candidate_windows, instructions=prompt)
            job.candidate_windows = scored

            clips = self.decider.select_clips(
                scored,
                count=self.config.job_defaults.clips_per_video,
            )

            if not clips:
                raise AutoShortsError(f"SemIf decider selected 0 clips for job {job.id}")

            if background_category:
                for c in clips:
                    c.background_style = background_category

            job.selected_clips = clips
            job.transition_to(JobStatus.SEMIF_EVALUATED)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={
                    "selected_count": len(clips),
                    "best_score": clips[0].candidate.score,
                    "best_window": f"{clips[0].candidate.start:.1f}-{clips[0].candidate.end:.1f}s",
                },
            )
            self.state_manager.save_job(job)
            logger.info(
                "Job %s: SemIf selected %d clip(s). Top score: %.3f",
                job.id,
                len(clips),
                clips[0].candidate.score,
            )
        except Exception as e:
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )
            raise

    def _run_download_stage(self, job: Job) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "precision_download")
        try:
            job.transition_to(JobStatus.DOWNLOADING_SECTION)
            self.state_manager.save_job(job)

            sel = job.selected_clips[0]
            work_dir = get_work_dir() / job.id
            # The work dir can be gone after cleanup, so create it before writing the cut.
            work_dir.mkdir(parents=True, exist_ok=True)
            raw_cut_path = work_dir / f"{job.id}_raw.mp4"

            # Check if job points to a pre-existing local source clip (skip download!)
            local_src: Path | None = self._resolve_local_source(job)

            if local_src is not None:
                if job.origin_kind is None:
                    job.origin_kind = "local_file"
                logger.info(
                    "Job %s: Pre-existing local source clip detected at %s. Skipping download.",
                    job.id,
                    local_src,
                )
                action = "reused"
                if local_src.resolve() != raw_cut_path.resolve():
                    import shutil

                    action = "copied"
                    clip_dur = sel.candidate.duration
                    if sel.candidate.start > 0 or (
                        job.video_metadata and job.video_metadata.duration > clip_dur + 1.0
                    ):
                        import subprocess

                        ffmpeg_bin = get_ffmpeg_path()
                        trim_cmd = [
                            ffmpeg_bin,
                            "-y",
                            "-ss",
                            str(sel.candidate.start),
                            "-to",
                            str(sel.candidate.end),
                            "-i",
                            str(local_src),
                            "-c",
                            "copy",
                            str(raw_cut_path),
                        ]
                        res = subprocess.run(trim_cmd, capture_output=True)
                        if res.returncode != 0 or not raw_cut_path.is_file():
                            logger.warning(
                                "Job %s: Local stream-copy trim failed (code %s); falling back "
                                "to a full copy of the source clip.",
                                job.id,
                                res.returncode,
                            )
                            shutil.copy2(str(local_src), str(raw_cut_path))
                            action = "copied_full_fallback"
                        else:
                            action = "trimmed"
                    else:
                        shutil.copy2(str(local_src), str(raw_cut_path))

                job.raw_video_path = str(raw_cut_path)
                job.record_diagnostic(
                    "download",
                    mode="local_source",
                    source_file=str(local_src),
                    local_action=action,
                    fallback=False,
                )
                artifacts: dict[str, Any] = {
                    "raw_video_path": str(job.raw_video_path),
                    "mode": "local_source",
                    "local_action": action,
                }
            else:
                if job.origin_kind is None:
                    job.origin_kind = "youtube"
                # Call through the unchanged Downloader protocol, then read the attempt
                # trace the implementation records on itself (absent on stubs).
                self.downloader.download_section(
                    url=job.source_url,
                    start_sec=sel.candidate.start,
                    end_sec=sel.candidate.end,
                    output_path=raw_cut_path,
                )
                download_diagnostics: dict[str, Any] = dict(
                    getattr(self.downloader, "last_download_diagnostics", None) or {}
                )
                job.raw_video_path = str(raw_cut_path)
                job.record_diagnostic(
                    "download",
                    mode="yt_dlp",
                    fallback=bool(download_diagnostics.get("attempt_count", 1) > 1),
                    **download_diagnostics,
                )
                artifacts = {
                    "raw_video_path": str(job.raw_video_path),
                    "mode": "yt_dlp",
                    "attempts": download_diagnostics.get("attempts", []),
                    "validation": download_diagnostics.get("validation"),
                }

            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts=artifacts,
            )
            self.state_manager.save_job(job)
            logger.info("Job %s: Precision cut saved to %s", job.id, job.raw_video_path)
        except Exception as e:
            job.record_diagnostic(
                "download",
                mode="failed",
                error=str(e),
                error_type=type(e).__name__,
            )
            try:
                self.state_manager.save_job(job)
            except Exception:  # pragma: no cover - state write is best-effort here
                pass
            self.state_manager.record_stage_complete(
                stage_id,
                status="FAILED",
                artifacts={"error": str(e), "error_type": type(e).__name__},
            )
            raise DownloadError(f"Precision cut download failed for job {job.id}: {e}") from e

    def _run_edl_stage(self, job: Job) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "edit_planning")
        try:
            sel = job.selected_clips[0]
            clip_path = Path(job.raw_video_path) if job.raw_video_path else None
            if not clip_path or not clip_path.is_file():
                job.edl = create_empty_edl()
                self.state_manager.record_stage_complete(
                    stage_id, status="FAILED", artifacts={"error": "raw clip missing"}
                )
                return

            edl = self.edit_planner.generate_edl(
                video_clip_path=clip_path,
                transcript_text=sel.candidate.text,
                instructions=job.custom_instructions,
            )
            job.edl = edl

            # Attach recommended titles/hooks from EDL if provided
            if edl.recommended_title:
                sel.title = edl.recommended_title
            if edl.recommended_hook:
                sel.hook_text = edl.recommended_hook

            # Provenance: is this a real plan, a degraded plan, or a placeholder?
            # Values are type-checked so a stubbed planner cannot leak a non-serializable
            # object into the stage artifacts.
            raw_source = getattr(self.edit_planner, "last_source", None)
            edl_source_attr = getattr(edl, "source", None)
            planner_source = (
                raw_source
                if isinstance(raw_source, str)
                else (edl_source_attr if isinstance(edl_source_attr, str) else "unknown")
            )
            raw_reason = getattr(self.edit_planner, "last_fallback_reason", None)
            edl_reason_attr = getattr(edl, "fallback_reason", None)
            planner_reason = (
                raw_reason
                if isinstance(raw_reason, str)
                else (edl_reason_attr if isinstance(edl_reason_attr, str) else None)
            )
            placeholder = (
                getattr(edl, "is_placeholder", False) is True or planner_source == "empty_fallback"
            )
            has_micro_edits = getattr(edl, "has_micro_edits", None) is True
            job.record_diagnostic(
                "editing",
                edl_source=planner_source,
                edl_fallback_reason=planner_reason,
                edl_placeholder=placeholder,
                has_micro_edits=has_micro_edits,
                jump_cut_count=len(edl.jump_cuts),
                punch_in_count=len(edl.zoom_punch_ins),
                fallback=placeholder or planner_source == "transcript",
            )
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={
                    "edl": json.loads(edl.model_dump_json()),
                    "edl_source": planner_source,
                    "edl_placeholder": placeholder,
                },
            )
            self.state_manager.save_job(job)
            if placeholder:
                logger.warning(
                    "Job %s: Edit plan is a PLACEHOLDER (no micro-edits) - reason: %s",
                    job.id,
                    planner_reason,
                )
            else:
                logger.info(
                    "Job %s: EDL planned (%s) with %d jump cut(s) and %d punch-in(s).",
                    job.id,
                    planner_source,
                    len(edl.jump_cuts),
                    len(edl.zoom_punch_ins),
                )
        except Exception as e:
            reason = f"edit planning raised: {e}"
            logger.warning(
                "Edit planning failed for job %s: %s; falling back to clean EDL", job.id, e
            )
            job.edl = create_empty_edl(fallback_reason=reason)
            job.record_diagnostic(
                "editing",
                edl_source="empty_fallback",
                edl_fallback_reason=reason,
                edl_placeholder=True,
                has_micro_edits=False,
                fallback=True,
            )
            try:
                self.state_manager.save_job(job)
            except Exception:  # pragma: no cover
                pass
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )

    def _run_subtitles_stage(self, job: Job, use_cached_words: bool = False) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "subtitles_generation")
        try:
            sel = job.selected_clips[0]
            work_dir = get_work_dir() / job.id
            work_dir.mkdir(parents=True, exist_ok=True)
            raw_cut_path = Path(job.raw_video_path) if job.raw_video_path else None
            ass_path = work_dir / f"{job.id}.ass"
            aligned_words_cache = work_dir / "aligned_words.json"

            words: list[dict[str, Any]] = []
            # Provenance of the karaoke timing actually used for this render.
            timing_source = "none"

            # 0. Check for existing cached aligned_words.json (user transcript edits)
            if use_cached_words and aligned_words_cache.is_file():
                try:
                    with open(aligned_words_cache, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                        if isinstance(cached_data, list) and cached_data:
                            words = cached_data
                            timing_source = "cached_alignment"
                            logger.info(
                                "Job %s: Loaded %d words from cached aligned_words.json",
                                job.id,
                                len(words),
                            )
                except Exception as c_err:
                    logger.debug("Failed loading cached aligned_words.json: %s", c_err)

            # 1. Try extracting 16kHz WAV and running align_words for exact karaoke timing
            if not words and raw_cut_path and raw_cut_path.is_file():
                try:
                    wav_path = work_dir / f"{job.id}_cut.wav"
                    extract_16khz_mono_wav(raw_cut_path, wav_path, ffmpeg_binary=get_ffmpeg_path())
                    aligned = self.transcriber.align_words(wav_path)
                    if aligned:
                        words = aligned
                        timing_source = "aligned"
                except Exception as align_err:
                    logger.debug("Transcriber alignment skipped/failed: %s", align_err)

            # 2. Fallback: Heuristic timing distributed across duration
            if not words:
                text_tokens = sel.candidate.text.split()
                dur = sel.candidate.duration or 45.0
                step = dur / max(1, len(text_tokens))
                words = [
                    {"word": w, "start": i * step, "end": (i + 1) * step, "probability": 1.0}
                    for i, w in enumerate(text_tokens)
                ]
                timing_source = "heuristic_spread"
                logger.warning(
                    "Job %s: No word alignment available; subtitle timing fell back to a "
                    "duration-spread estimate (%d words over %.1fs).",
                    job.id,
                    len(words),
                    dur,
                )

            job.record_diagnostic(
                "subtitles",
                timing_source=timing_source,
                word_count=len(words),
                fallback=timing_source == "heuristic_spread",
            )

            # Save aligned_words.json for transcript editing & fast re-rendering
            if words:
                try:
                    with open(aligned_words_cache, "w", encoding="utf-8") as f:
                        json.dump(words, f, indent=2)
                except Exception as f_err:
                    logger.debug("Failed saving aligned_words.json: %s", f_err)

            channel_credit = job.video_metadata.channel if job.video_metadata else None
            hook = sel.hook_text or (job.edl.recommended_hook if job.edl else "") or None
            hook_in = job.edl.hook_in_sec if (job.edl and job.edl.hook_in_sec > 0) else 0.0
            total_dur = (
                max(1.0, sel.candidate.duration - hook_in) if sel.candidate.duration else None
            )
            jump_cuts = job.edl.jump_cuts if (job.edl and job.edl.jump_cuts) else None
            style_preset = getattr(job, "style_preset", None) or getattr(
                self.config.subtitles, "style_preset", "neon"
            )

            text_transform = getattr(job, "text_transform", None) or getattr(
                getattr(self.config, "subtitles", None), "text_transform", "uppercase"
            )
            subtitle_position = getattr(job, "subtitle_position", None) or getattr(
                getattr(self.config, "subtitles", None), "subtitle_position", "center"
            )
            hook_banner_style = getattr(job, "hook_banner_style", None) or getattr(
                getattr(self.config, "subtitles", None), "hook_banner_style", "neon_box"
            )
            enable_emojis = getattr(job, "enable_emojis", None)
            if enable_emojis is None:
                enable_emojis = getattr(
                    getattr(self.config, "subtitles", None), "enable_emojis", True
                )

            ass_words: list[Any] = words
            if self._caption_engine() == "remotion":
                # Word captions move to the Remotion track; ASS keeps the hook banner and credit.
                timed_words, timeline_dur = build_caption_timeline(
                    words,
                    hook_in_sec=hook_in,
                    jump_cuts=jump_cuts,
                    total_duration_sec=total_dur,
                )
                write_caption_plan(
                    work_dir / f"{job.id}_captions.json",
                    timed_words,
                    duration_sec=timeline_dur or float(sel.candidate.duration or 1.0),
                    style_preset=style_preset,
                )
                ass_words = []

            ass_kwargs: dict[str, Any] = {
                "output_path": ass_path,
                "words": ass_words,
                "hook_title": hook,
                "config": self.config.subtitles,
                "onscreen_credit": channel_credit,
                "total_duration_sec": total_dur,
                "hook_in_sec": hook_in,
                "jump_cuts": jump_cuts,
                "style_preset": style_preset,
            }
            extra_ass_kwargs = {
                "text_transform": text_transform,
                "subtitle_position": subtitle_position,
                "hook_banner_style": hook_banner_style,
                "enable_emojis": enable_emojis,
            }
            try:
                sig = inspect.signature(write_ass_file)
                has_varkw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                for k, v in extra_ass_kwargs.items():
                    if has_varkw or k in sig.parameters:
                        ass_kwargs[k] = v
            except Exception:
                ass_kwargs.update(extra_ass_kwargs)

            write_ass_file(**ass_kwargs)

            job.subtitles_path = str(ass_path)
            if job.status not in (JobStatus.DOWNLOADING_SECTION, JobStatus.SUBTITLES_GENERATED):
                job.status = JobStatus.DOWNLOADING_SECTION
            job.transition_to(JobStatus.SUBTITLES_GENERATED)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={
                    "subtitles_path": str(ass_path),
                    "words_count": len(words),
                    "timing_source": timing_source,
                },
            )
            self.state_manager.save_job(job)
            logger.info(
                "Job %s: Generated ASS subtitles (%d words, timing=%s).",
                job.id,
                len(words),
                timing_source,
            )
        except Exception as e:
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )
            raise

    @staticmethod
    def _build_editing_summary(job: Job, final_path: Path | None) -> dict[str, Any]:
        """Condense how this render was made into one serializable record."""
        editing = job.diagnostics.get("editing", {}) if isinstance(job.diagnostics, dict) else {}
        subtitles = (
            job.diagnostics.get("subtitles", {}) if isinstance(job.diagnostics, dict) else {}
        )
        download = job.diagnostics.get("download", {}) if isinstance(job.diagnostics, dict) else {}

        thumbnail_source = "none"
        if job.selected_clips:
            tp = getattr(job.selected_clips[0], "thumbnail_path", None)
            if isinstance(tp, str) and tp:
                thumbnail_source = "compositor_frame"

        window = None
        if job.selected_clips:
            cand = job.selected_clips[0].candidate
            window = {
                "start": round(cand.start, 2),
                "end": round(cand.end, 2),
                "duration": round(cand.duration, 2),
            }

        fallbacks = [
            name
            for name, section in (
                ("download", download),
                ("editing", editing),
                ("subtitles", subtitles),
            )
            if isinstance(section, dict) and section.get("fallback")
        ]

        return {
            "window": window,
            "edl_source": editing.get("edl_source"),
            "edl_placeholder": editing.get("edl_placeholder"),
            "edl_fallback_reason": editing.get("edl_fallback_reason"),
            "has_micro_edits": editing.get("has_micro_edits"),
            "jump_cut_count": editing.get("jump_cut_count"),
            "punch_in_count": editing.get("punch_in_count"),
            "subtitle_timing_source": subtitles.get("timing_source"),
            "thumbnail_source": thumbnail_source,
            "download_mode": download.get("mode"),
            "fallbacks": fallbacks,
            "render_path": str(final_path) if final_path else None,
        }

    def _validate_compositing_inputs(self, job: Job) -> None:
        """Fail early, with a clear message, when the compositor's inputs are unusable.

        A missing or empty section cut is an error here. A missing subtitle file is simply
        regenerated, since the subtitle stage is cheap and does not depend on the render.
        """
        raw = Path(job.raw_video_path) if job.raw_video_path else None
        if not _is_nonempty_file(raw):
            raise AutoShortsError(
                f"Job {job.id}: downloaded section is missing or empty ({raw}); "
                "re-run the download stage before compositing."
            )

        try:
            from autoshorts.media.downloader import probe_video_stream_info

            info: dict[str, Any] = dict(probe_video_stream_info(raw) or {})
        except Exception as probe_err:  # probe is best effort; ffmpeg reports real errors
            logger.debug("Job %s: stream probe unavailable: %s", job.id, probe_err)
            info = {}
        # The probe returns the same shape when ffprobe is missing, so only a probe that
        # read a duration and still found no video stream is treated as a hard failure.
        if info and not info.get("has_video") and float(info.get("duration") or 0.0) > 0.0:
            raise AutoShortsError(
                f"Job {job.id}: downloaded section has no video stream ({raw}); "
                "re-run the download stage."
            )

        subs = Path(job.subtitles_path) if job.subtitles_path else None
        if not _is_nonempty_file(subs):
            logger.warning(
                "Job %s: subtitles file missing; regenerating before compositing.", job.id
            )
            self._run_subtitles_stage(job)

    def _caption_engine(self) -> str:
        subtitles_cfg = getattr(self.config, "subtitles", None)
        return getattr(subtitles_cfg, "caption_engine", "ass") or "ass"

    def _apply_remotion_captions(self, job: Job, base_path: Path) -> Path:
        """Render the Remotion caption track and composite it onto the composed Short."""
        plan_path = get_work_dir() / job.id / f"{job.id}_captions.json"
        if not plan_path.is_file():
            raise RemotionRenderError(
                f"Caption plan missing for job {job.id}; rerun the subtitles stage."
            )
        if not is_remotion_ready(REMOTION_PROJECT_DIR):
            raise RemotionUnavailableError(
                "Caption engine is 'remotion' but the Remotion project is not installed. "
                f"Run `npm install` in {REMOTION_PROJECT_DIR}, or set subtitles.caption_engine "
                "to 'ass'."
            )
        track_path = plan_path.with_suffix(".webm")
        render_caption_overlay(plan_path, track_path, project_dir=REMOTION_PROJECT_DIR)
        captioned = base_path.with_name(f"{base_path.stem}_captioned{base_path.suffix}")
        overlay_captions(base_path, track_path, captioned, ffmpeg_path=get_ffmpeg_path())
        job.record_diagnostic(
            "compositing", caption_engine="remotion", caption_track=str(track_path)
        )
        return captioned

    def _run_compositing_stage(
        self,
        job: Job,
        background_category: str | None = None,
        dopamine_level: str | None = None,
        **kwargs: Any,
    ) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "compositing")
        try:
            self._validate_compositing_inputs(job)
            if job.status not in (JobStatus.SUBTITLES_GENERATED, JobStatus.COMPOSITING):
                job.status = JobStatus.SUBTITLES_GENERATED
            job.transition_to(JobStatus.COMPOSITING)
            self.state_manager.save_job(job)

            sel = job.selected_clips[0]
            output_dir = get_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            master_path = output_dir / f"{job.id}_master.mp4"

            dopamine = (
                dopamine_level
                or getattr(job, "dopamine_level", None)
                or self.config.job_defaults.dopamine_level
            )
            bg_style = (
                background_category
                or getattr(job, "background_category", None)
                or sel.background_style
                or self.config.job_defaults.background_category
            )
            bg_file, _ = select_gameplay_background(category=bg_style)

            branding_cfg = getattr(self.config, "branding", None)
            audio_cfg = getattr(self.config, "audio_mastering", None)
            layout_mode = (
                getattr(job, "layout_mode", None)
                or getattr(branding_cfg, "layout_mode", None)
                or getattr(self.config.media, "layout_mode", "split_50_50")
            )
            color_grading = (
                getattr(job, "color_grading", None)
                or getattr(branding_cfg, "color_grading", None)
                or "none"
            )
            watermark_text = getattr(job, "watermark_text", None) or getattr(
                branding_cfg, "watermark_text", None
            )
            watermark_position = getattr(job, "watermark_position", None) or getattr(
                branding_cfg, "watermark_position", "top_right"
            )
            watermark_opacity = getattr(job, "watermark_opacity", None) or getattr(
                branding_cfg, "watermark_opacity", 0.65
            )
            progress_bar_position = getattr(job, "progress_bar_position", None) or getattr(
                branding_cfg, "progress_bar_position", "seam"
            )
            progress_bar_color = getattr(job, "progress_bar_color", None) or getattr(
                branding_cfg, "progress_bar_color", "#FFE600"
            )
            voice_boost = getattr(audio_cfg, "voice_boost", True)
            target_lufs = getattr(audio_cfg, "target_lufs", -14.0)
            crop_focus = getattr(job, "crop_focus", None) or "center"

            comp_kwargs: dict[str, Any] = {
                "job_id": job.id,
                "source_clip": Path(job.raw_video_path),  # type: ignore[arg-type]
                "edl": job.edl,
                "background_path": bg_file,
                "subtitles_path": Path(job.subtitles_path),  # type: ignore[arg-type]
                "output_path": master_path,
                "dopamine_level": dopamine,
            }
            extra_comp: dict[str, Any] = {
                "layout_mode": layout_mode,
                "color_grading": color_grading,
                "watermark_text": watermark_text,
                "watermark_position": watermark_position,
                "watermark_opacity": watermark_opacity,
                "progress_bar_position": progress_bar_position,
                "progress_bar_color": progress_bar_color,
                "voice_boost": voice_boost,
                "target_lufs": target_lufs,
                "bg_music_volume": getattr(job, "bgm_volume", None),
                "crop_focus": crop_focus,
                "bg_music_path": getattr(job, "bgm_path", None),
            }
            try:
                c_sig = inspect.signature(self.compositor.compose)
                has_varkw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in c_sig.parameters.values()
                )
                for k, v in extra_comp.items():
                    if has_varkw or k in c_sig.parameters:
                        comp_kwargs[k] = v
            except Exception:
                comp_kwargs.update(extra_comp)

            # Run FFmpeg master compositing
            composed_res = self.compositor.compose(**comp_kwargs)
            # A master that does not decode must never reach review: re-render once, then stop.
            for attempt in range(2):
                check_path = (
                    Path(composed_res) if isinstance(composed_res, (str, Path)) else master_path
                )
                if not check_path.is_file():
                    break
                decode_errors = count_decode_errors(check_path)
                if decode_errors == 0:
                    break
                logger.warning(
                    "Job %s: composited master failed decode check (%d error lines), attempt %d/2",
                    job.id,
                    decode_errors,
                    attempt + 1,
                )
                if attempt == 1:
                    raise AutoShortsError(
                        f"Composited master for job {job.id} does not decode cleanly "
                        f"({decode_errors} error lines). Rerun the job; the output was not sent to review."
                    )
                composed_res = self.compositor.compose(**comp_kwargs)
            final_path = (
                Path(composed_res) if isinstance(composed_res, (str, Path)) else master_path
            )
            if not final_path.is_file() and master_path.is_file():
                final_path = master_path
            if self._caption_engine() == "remotion":
                final_path = self._apply_remotion_captions(job, final_path)
            job.rendered_video_path = str(final_path)
            # Informational only: the ffmpeg-skill delivery check never changes the job status.
            # Two-pass loudness on the finished file (video is copied, audio re-encoded).
            # A failure here keeps the single-pass audio; it never fails the job.
            try:
                normalize_two_pass(Path(final_path), Path(final_path), float(target_lufs))
            except Exception as loud_err:
                logger.warning("Job %s: two-pass loudness skipped: %s", job.id, loud_err)
            delivery = check_delivery(Path(final_path))
            job.record_diagnostic("delivery", ok=delivery["ok"], issues=delivery["issues"])

            # Optional auto-thumbnail extraction
            if hasattr(self.compositor, "extract_thumbnail") and final_path.is_file():
                try:
                    work_dir = get_work_dir() / job.id
                    work_dir.mkdir(parents=True, exist_ok=True)
                    thumb_path = work_dir / f"thumb_{job.id}.jpg"
                    thumb_ts = (
                        job.edl.recommended_thumbnail_timestamp
                        if (
                            job.edl and getattr(job.edl, "recommended_thumbnail_timestamp", 0.0) > 0
                        )
                        else 1.0
                    )
                    t_sig = inspect.signature(self.compositor.extract_thumbnail)
                    if "timestamp_sec" in t_sig.parameters:
                        self.compositor.extract_thumbnail(
                            final_path, thumb_path, timestamp_sec=thumb_ts
                        )
                    elif "timestamp" in t_sig.parameters:
                        self.compositor.extract_thumbnail(
                            final_path, thumb_path, timestamp=thumb_ts
                        )
                    else:
                        self.compositor.extract_thumbnail(final_path, thumb_path)
                    if job.selected_clips:
                        try:
                            job.selected_clips[0].thumbnail_path = str(thumb_path)
                        except (ValueError, TypeError, AttributeError):
                            if hasattr(job.selected_clips[0], "__dict__"):
                                job.selected_clips[0].__dict__["thumbnail_path"] = str(thumb_path)
                except Exception as t_err:
                    logger.warning("Job %s: Auto-thumbnail extraction failed: %s", job.id, t_err)

            # Optional low-resolution fast preview
            if self.config.media.generate_lowres_preview:
                preview_path = output_dir / f"{job.id}_preview.mp4"
                prev_kwargs: dict[str, Any] = {
                    "source_clip": master_path,
                    "output_path": preview_path,
                }
                try:
                    p_sig = inspect.signature(self.compositor.generate_preview)
                    has_varkw = any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in p_sig.parameters.values()
                    )
                    for k, v in extra_comp.items():
                        if has_varkw or k in p_sig.parameters:
                            prev_kwargs[k] = v
                except Exception:
                    pass
                try:
                    self.compositor.generate_preview(**prev_kwargs)
                    job.preview_video_path = str(preview_path)
                except Exception as p_err:
                    logger.warning("Job %s: Low-res preview generation failed: %s", job.id, p_err)

            # Publish a compact editing summary so a finished render is explainable
            # without digging through per-stage logs.
            job.record_diagnostic("render", **self._build_editing_summary(job, final_path))

            if job.status not in (JobStatus.COMPOSITING, JobStatus.READY_FOR_REVIEW):
                job.status = JobStatus.COMPOSITING
            job.transition_to(JobStatus.READY_FOR_REVIEW)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={
                    "master_video": str(master_path),
                    "preview_video": job.preview_video_path,
                    "editing_summary": job.diagnostics.get("render", {}),
                },
            )
            self.state_manager.save_job(job)
            logger.info("Job %s: Composited master Short at %s", job.id, master_path)
        except Exception as e:
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )
            raise

    def rerender_job(self, job_id: str, **overrides: Any) -> Job:
        """Fast 1-click re-render of subtitles and compositing stages skipping re-download and re-scoring.

        Robustly re-renders jobs from READY_FOR_REVIEW, SCHEDULED, COMPLETED, or FAILED.
        """
        job = self.state_manager.get_job(job_id)
        if not job:
            raise AutoShortsError(f"Job {job_id} not found in state database")

        # If job was in FAILED state, clear previous error message to allow fresh retry
        if job.status == JobStatus.FAILED:
            job.error_message = None

        # Apply overrides
        for key in (
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
            "privacy_status",
            "dopamine_level",
            "background_category",
        ):
            if key in overrides and overrides[key] is not None:
                try:
                    setattr(job, key, overrides[key])
                except (ValueError, AttributeError):
                    if hasattr(job, "__dict__"):
                        job.__dict__[key] = overrides[key]

        if "hook_text" in overrides and overrides["hook_text"] is not None:
            ht = str(overrides["hook_text"])
            if job.selected_clips:
                job.selected_clips[0].hook_text = ht
            if job.edl:
                job.edl.recommended_hook = ht

        if "clip_title" in overrides and overrides["clip_title"] is not None:
            ct = str(overrides["clip_title"])
            if job.selected_clips:
                job.selected_clips[0].title = ct
            if job.edl:
                job.edl.recommended_title = ct

        if "background_category" in overrides and overrides["background_category"] is not None:
            bg_cat = str(overrides["background_category"])
            if job.selected_clips:
                job.selected_clips[0].background_style = bg_cat
            if job.edl and hasattr(job.edl, "background_style_recommendation"):
                try:
                    job.edl.background_style_recommendation = bg_cat
                except Exception:
                    pass

        # --- Pre-flight: verify the artifacts this re-render depends on ---------------
        rerender_state = {"raw_source": None, "raw_source_recovered": False, "steps": []}
        raw_path = Path(job.raw_video_path) if job.raw_video_path else None
        if not raw_path or not raw_path.is_file():
            # Check work directory candidates (section_*.mp4 or *_raw.mp4)
            work_dir = get_work_dir() / job.id
            candidates = (
                list(work_dir.glob("section_*.mp4"))
                + list(work_dir.glob("*_raw.mp4"))
                + list(work_dir.glob(f"{job.id}*.mp4"))
            )
            found: Path | None = None
            for cand in candidates:
                if cand.is_file() and cand.stat().st_size > 0:
                    found = cand
                    break
            if found is not None:
                job.raw_video_path = str(found)
                raw_path = found
                rerender_state["raw_source_recovered"] = True
            elif job.selected_clips and (job.source_url or job.video_metadata):
                logger.info("Job %s: Re-extracting missing raw cut video for re-render...", job_id)
                try:
                    with self._render_slot():
                        # The download stage only starts from SEMIF_EVALUATED; a re-render
                        # can begin from READY_FOR_REVIEW, COMPLETED or FAILED.
                        job.status = JobStatus.SEMIF_EVALUATED
                        self._run_download_stage(job)
                except Exception as dl_err:
                    self._mark_failed(job, "download", dl_err)
                    raise
                rerender_state["steps"].append("re_download")
                raw_path = Path(job.raw_video_path) if job.raw_video_path else None
            else:
                raise AutoShortsError(
                    f"Raw cut video not found for job {job_id} and it cannot be regenerated "
                    "(no source URL or video metadata). Re-render requires an existing cut."
                )

        # Verify the recovered source is actually usable before spending a render on it.
        if raw_path is not None:
            try:
                size = raw_path.stat().st_size
            except OSError as stat_err:
                raise AutoShortsError(
                    f"Raw cut for job {job_id} is unreadable ({stat_err}); cannot re-render."
                ) from stat_err
            if size <= 0:
                raise AutoShortsError(
                    f"Raw cut for job {job_id} is empty ({raw_path}); cannot re-render."
                )
            rerender_state["raw_source"] = str(raw_path)

        if not job.selected_clips or not job.selected_clips[0].candidate:
            raise AutoShortsError(
                f"Job {job_id} has no selected clip/window recorded; cannot re-render "
                "without a source window. Re-run the clipping pipeline instead."
            )

        job.record_diagnostic("rerender", **rerender_state)

        # Reset status to allow stage transitions through valid state machine
        job.status = JobStatus.DOWNLOADING_SECTION
        self.state_manager.save_job(job)

        re_stage = "subtitles"
        try:
            with self._render_slot():
                # Re-run Stage 7: Subtitle (.ass) Generation (using cached aligned_words.json if present)
                self._run_subtitles_stage(job, use_cached_words=True)
                rerender_state["steps"].append("subtitles")

                # Re-run Stage 8: Compositing
                re_stage = "compositing"
                self._run_compositing_stage(
                    job,
                    background_category=overrides.get("background_category"),
                    dopamine_level=overrides.get("dopamine_level"),
                )
        except Exception as re_err:
            logger.exception("Job %s fast re-render failed", job.id)
            self._mark_failed(job, re_stage, re_err)
            raise

        rerender_state["steps"].append("compositing")
        job.record_diagnostic("rerender", **rerender_state)
        self.state_manager.save_job(job)
        logger.info(
            "Job %s fast re-render complete (%s); ready for review.",
            job.id,
            ", ".join(rerender_state["steps"]) or "no-op",
        )
        return job

    def auto_trim_job_silence(self, job_id: str, min_gap_sec: float = 0.48) -> dict[str, Any]:
        """Compute tight JumpCut ranges for silences >= min_gap_sec from aligned_words.json."""
        job = self.state_manager.get_job(job_id)
        if not job:
            raise AutoShortsError(f"Job {job_id} not found in state database")

        work_dir = get_work_dir() / job_id
        aligned_words_cache = work_dir / "aligned_words.json"
        words: list[dict[str, Any]] = []
        if aligned_words_cache.is_file():
            try:
                loaded = json.loads(aligned_words_cache.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    words = loaded
            except Exception as e:
                logger.debug("Failed reading aligned_words.json for job %s: %s", job_id, e)

        cuts: list[JumpCut] = []
        detector_fn = None
        for mod_name in (
            "autoshorts.edit_planner.multimodal_planner",
            "autoshorts.edit_planner.edl_schema",
            "autoshorts.media.compositor",
        ):
            try:
                mod = __import__(mod_name, fromlist=["detect_dead_air_cuts"])
                if hasattr(mod, "detect_dead_air_cuts"):
                    detector_fn = mod.detect_dead_air_cuts
                    break
            except Exception:
                pass

        if detector_fn is not None and words:
            try:
                raw_cuts = detector_fn(words, min_gap_sec=min_gap_sec)
                for rc in raw_cuts:
                    if isinstance(rc, JumpCut):
                        cuts.append(rc)
                    elif isinstance(rc, dict):
                        cuts.append(JumpCut(**rc))
            except Exception as d_err:
                logger.debug("detect_dead_air_cuts failed, using fallback gap detector: %s", d_err)
                cuts = []

        if not cuts and len(words) >= 2:
            sorted_words = sorted(words, key=lambda w: float(w.get("start", 0.0)))
            for idx in range(len(sorted_words) - 1):
                prev_end = float(sorted_words[idx].get("end", 0.0))
                next_start = float(sorted_words[idx + 1].get("start", 0.0))
                gap = next_start - prev_end
                if gap >= min_gap_sec:
                    cuts.append(
                        JumpCut(
                            cut_start=round(prev_end, 3),
                            cut_end=round(next_start, 3),
                            reason=f"dead_air ({gap:.2f}s)",
                        )
                    )

        if job.edl is None:
            job.edl = create_empty_edl()
        job.edl.jump_cuts = cuts
        self.state_manager.save_job(job)

        return {
            "job_id": job_id,
            "jump_cuts_added": len(cuts),
            "jump_cuts": [c.model_dump() for c in cuts],
        }

    def _prepare_upload_metadata(self, job: Job) -> tuple[str, str]:
        sel = job.selected_clips[0]
        meta = job.video_metadata or VideoMetadata(
            title="AutoShort Clip",
            channel="AutoShorts",
            channel_url="https://youtube.com",
            video_url=job.source_url,
            duration=45.0,
        )

        title = build_short_title(
            hook=sel.hook_text or sel.title or "Must Watch",
            channel=meta.channel,
            title=meta.title,
            template=self.config.upload.title_template,
        )

        desc = build_short_description(
            title=meta.title,
            channel=meta.channel,
            channel_url=meta.channel_url,
            video_url=meta.video_url,
            hook=sel.hook_text or sel.title or "Watch this high retention excerpt!",
            start=sel.candidate.start,
            end=sel.candidate.end,
            hashtags=build_viral_hashtags(meta.title, sel.hook_text or sel.title or ""),
            template=self.config.upload.description_template,
        )

        return title, desc

    def _run_immediate_upload(self, job: Job) -> UploadResult:
        title, desc = self._prepare_upload_metadata(job)
        if job.status not in (JobStatus.READY_FOR_REVIEW, JobStatus.SCHEDULED):
            job.status = JobStatus.READY_FOR_REVIEW
        job.transition_to(JobStatus.UPLOADING)
        self.state_manager.save_job(job)

        stage_id = self.state_manager.record_stage_start(job.id, "immediate_upload")
        upload_cfg = getattr(self.config, "upload", None)
        # Same rule as the scheduler: an explicit upload.quota_exhausted_fallback wins,
        # otherwise the documented quota.exhaustion_fallback applies.
        fallback_mode = (
            str(upload_cfg.quota_exhausted_fallback)
            if "quota_exhausted_fallback" in getattr(upload_cfg, "model_fields_set", set())
            else str(self.quota_tracker.fallback_mode)
        )
        privacy = (
            getattr(job, "privacy_status", None)
            or getattr(self.config.upload, "default_privacy_status", None)
            or getattr(self.config.upload, "privacy_status", "unlisted")
        )

        use_uploader = self.uploader
        fell_back_to_studio = False
        is_yt_api = (
            isinstance(self.uploader, YouTubeApiUploader)
            or type(self.uploader).__name__ == "YouTubeApiUploader"
        )

        if (
            is_yt_api
            and self.quota_tracker
            and not self.quota_tracker.can_upload()
            and fallback_mode == "studio"
        ):
            logger.warning(
                "Job %s: API quota exhausted before immediate upload. Falling back to YouTube Studio automation.",
                job.id,
            )
            self.state_manager.record_stage_start(job.id, "studio_fallback")
            use_uploader = PlaywrightStudioUploader(config=self.config)
            fell_back_to_studio = True

        category_id = getattr(job, "category_id", None) or getattr(upload_cfg, "category_id", "24")
        default_language = getattr(job, "default_language", None) or getattr(
            upload_cfg, "default_language", "en"
        )
        altered_content = getattr(job, "altered_content", None)
        if altered_content is None:
            altered_content = getattr(upload_cfg, "altered_content", False)
        custom_tags = getattr(job, "custom_tags", None) or getattr(upload_cfg, "custom_tags", [])
        auto_pin_comment = getattr(job, "auto_pin_comment", None)
        if auto_pin_comment is None:
            auto_pin_comment = getattr(upload_cfg, "auto_pin_comment", False)
        pin_comment_text = getattr(job, "pin_comment_text", None) or getattr(
            upload_cfg, "pin_comment_text", None
        )
        playlist_id = getattr(job, "playlist_id", None) or getattr(upload_cfg, "playlist_id", None)
        link_related_video = getattr(job, "link_related_video", None)
        if link_related_video is None:
            link_related_video = getattr(upload_cfg, "link_related_video", True)
        publish_at = getattr(job, "publish_at", None) or getattr(upload_cfg, "publish_at", None)

        # Resolve thumbnail
        thumbnail_path: Path | None = None
        if (
            hasattr(job, "thumbnail_path")
            and job.thumbnail_path
            and Path(job.thumbnail_path).is_file()
        ):
            thumbnail_path = Path(job.thumbnail_path)
        elif job.selected_clips and getattr(job.selected_clips[0], "thumbnail_path", None):
            tp = Path(job.selected_clips[0].thumbnail_path)
            if tp.is_file():
                thumbnail_path = tp
        if not thumbnail_path:
            work_thumb = get_work_dir() / job.id / f"thumb_{job.id}.jpg"
            if work_thumb.is_file():
                thumbnail_path = work_thumb

        tags = ["Shorts", "AutoShorts"]
        if custom_tags:
            for ct in custom_tags:
                if ct not in tags:
                    tags.append(ct)

        try:
            try:
                kwargs: dict[str, Any] = {
                    "path": Path(job.rendered_video_path),  # type: ignore[arg-type]
                    "title": title,
                    "description": desc,
                    "tags": tags,
                    "privacy": privacy,
                }
                if isinstance(use_uploader, PlaywrightStudioUploader):
                    kwargs["privacy_status"] = privacy

                extra_upload_kwargs = {
                    "category_id": category_id,
                    "default_language": default_language,
                    "altered_content": altered_content,
                    "custom_tags": custom_tags,
                    "auto_pin_comment": auto_pin_comment,
                    "pin_comment_text": pin_comment_text,
                    "playlist_id": playlist_id,
                    "publish_at": publish_at,
                    "thumbnail_path": thumbnail_path,
                }
                if link_related_video and getattr(job, "source_url", None):
                    extra_upload_kwargs["related_video_url"] = job.source_url

                try:
                    sig = inspect.signature(use_uploader.upload)
                    has_varkw = any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                    )
                    for k, v in extra_upload_kwargs.items():
                        if v is not None and (has_varkw or k in sig.parameters):
                            kwargs[k] = v
                except Exception:
                    pass

                result = use_uploader.upload(**kwargs)
            except Exception as up_err:
                is_quota_err = isinstance(up_err, (QuotaExceededError, QuotaExhaustedError))
                if is_quota_err and fallback_mode == "studio" and not fell_back_to_studio:
                    logger.warning(
                        "Job %s: Encountered quota exhaustion error (%s). Falling back to YouTube Studio automation.",
                        job.id,
                        up_err,
                    )
                    self.state_manager.record_stage_start(job.id, "studio_fallback")
                    use_uploader = PlaywrightStudioUploader(config=self.config)
                    fell_back_to_studio = True
                    studio_kwargs: dict[str, Any] = {
                        "path": Path(job.rendered_video_path),  # type: ignore[arg-type]
                        "title": title,
                        "description": desc,
                        "tags": tags,
                        "privacy": privacy,
                        "privacy_status": privacy,
                        "altered_content": altered_content,
                        "publish_at": publish_at,
                    }
                    if link_related_video and getattr(job, "source_url", None):
                        studio_kwargs["related_video_url"] = job.source_url
                    result = use_uploader.upload(**studio_kwargs)
                else:
                    raise

            # An unsuccessful result must not reach VERIFYING/COMPLETED. The rendered file is
            # kept, so the job can be retried as an upload from READY_FOR_REVIEW.
            if not _upload_succeeded(result):
                raise UploadError(
                    f"Uploader returned no successful upload for job {job.id} "
                    f"(video_id={getattr(result, 'video_id', None)!r})."
                )

            if fell_back_to_studio and isinstance(result.raw_response, dict):
                result.raw_response["fallback"] = "studio_automation"
                result.raw_response["fallback_reason"] = "quota_exhausted"

            job.upload_result = result
            self.state_manager.save_job(job)

            # Verification
            job.transition_to(JobStatus.VERIFYING)
            self.state_manager.save_job(job)

            # The video is already live at this point: a verification error must never fail the
            # job, because a retry would upload the same video a second time.
            try:
                verified = use_uploader.verify(result.video_id)
            except UploadError as verify_err:
                logger.warning(
                    "Verification raised after upload of %s: %s", result.video_id, verify_err
                )
                verified = False
                result.verification_note = f"Upload succeeded but verification failed: {verify_err}"

            # `verified` must mean "positively confirmed", so a DOM-scraped Studio result
            # is recorded as studio_dom and stays unverified.
            declared_kind = getattr(use_uploader, "last_verification_kind", None)
            kind, confirmed = resolve_verification(declared_kind, bool(verified))
            result.verified = confirmed
            result.verification_kind = kind  # type: ignore[assignment]
            if kind == "studio_dom":
                result.verification_note = (
                    "Studio browser automation succeeded but the result was read from the "
                    "Studio DOM. Confirm the video manually before treating it as published."
                )
            job.upload_result = result
            job.record_diagnostic(
                "upload",
                uploader=type(use_uploader).__name__,
                verification_kind=result.verification_kind,
                verified=result.verified,
                fell_back_to_studio=fell_back_to_studio,
                fallback=fell_back_to_studio or not result.verified,
            )

            job.transition_to(JobStatus.COMPLETED)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts=json.loads(result.model_dump_json()),
            )
            self.state_manager.save_job(job)
            logger.info(
                "Job %s: Uploaded %s (%s) - verification=%s (confirmed=%s)",
                job.id,
                result.video_id,
                result.url,
                result.verification_kind,
                result.verified,
            )

            # Post-upload transactional cleanup. Only delete intermediates when the
            # upload was positively confirmed; an unconfirmed Studio upload keeps them.
            if self.cleaner and getattr(getattr(self.config, "cleanup", None), "enabled", True):
                if not result.verified:
                    logger.warning(
                        "Job %s: Skipping intermediate cleanup because the upload is not "
                        "confirmed (verification=%s).",
                        job.id,
                        result.verification_kind,
                    )
                try:
                    self.cleaner.cleanup_job(job.id, verified=result.verified, upload_result=result)
                    job.transition_to(JobStatus.CLEANED)
                    self.state_manager.save_job(job)
                    logger.info("Job %s: Post-upload cleanup completed.", job.id)
                except Exception as c_err:
                    logger.warning(
                        "Job %s: Post-upload cleanup encountered error: %s", job.id, c_err
                    )

            return result
        except Exception as e:
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )
            # The rendered_video_path is left untouched so a retry can re-upload it.
            self._mark_failed(job, "upload", e)
            raise

    def _run_schedule_upload(self, job: Job) -> None:
        title, desc = self._prepare_upload_metadata(job)
        if job.status not in (JobStatus.READY_FOR_REVIEW, JobStatus.SCHEDULED):
            job.status = JobStatus.READY_FOR_REVIEW
        job.transition_to(JobStatus.SCHEDULED)
        self.state_manager.save_job(job)

        privacy = (
            getattr(job, "privacy_status", None)
            or getattr(self.config.upload, "default_privacy_status", None)
            or getattr(self.config.upload, "privacy_status", "unlisted")
        )
        custom_tags = getattr(job, "custom_tags", None) or getattr(
            getattr(self.config, "upload", None), "custom_tags", []
        )
        tags = ["Shorts", "AutoShorts"]
        if custom_tags:
            for ct in custom_tags:
                if ct not in tags:
                    tags.append(ct)

        upload_cfg = getattr(self.config, "upload", None)
        category_id = getattr(job, "category_id", None) or getattr(upload_cfg, "category_id", "24")
        altered_content = getattr(job, "altered_content", None)
        if altered_content is None:
            altered_content = getattr(upload_cfg, "altered_content", False)
        playlist_id = getattr(job, "playlist_id", None) or getattr(upload_cfg, "playlist_id", None)
        auto_pin_comment = getattr(job, "auto_pin_comment", None)
        if auto_pin_comment is None:
            auto_pin_comment = getattr(upload_cfg, "auto_pin_comment", False)
        pin_comment_text = getattr(job, "pin_comment_text", None) or getattr(
            upload_cfg, "pin_comment_text", None
        )

        thumbnail_path: Path | None = None
        if (
            hasattr(job, "thumbnail_path")
            and job.thumbnail_path
            and Path(job.thumbnail_path).is_file()
        ):
            thumbnail_path = Path(job.thumbnail_path)
        elif job.selected_clips and getattr(job.selected_clips[0], "thumbnail_path", None):
            tp = Path(job.selected_clips[0].thumbnail_path)
            if tp.is_file():
                thumbnail_path = tp

        sched_kwargs: dict[str, Any] = {
            "job_id": job.id,
            "video_path": Path(job.rendered_video_path),  # type: ignore[arg-type]
            "title": title,
            "description": desc,
            "tags": tags,
            "privacy_status": privacy,
            "category_id": category_id,
            "altered_content": altered_content,
            "playlist_id": playlist_id,
            "auto_pin_comment": auto_pin_comment,
            "pin_comment_text": pin_comment_text,
            "thumbnail_path": thumbnail_path,
        }

        # Handle publish_at target time if specified
        target_time = None
        pub_at = getattr(job, "publish_at", None) or getattr(
            getattr(self.config, "upload", None), "publish_at", None
        )
        if pub_at:
            try:
                target_time = datetime.fromisoformat(pub_at)
            except Exception:
                pass

        try:
            s_sig = inspect.signature(self.scheduler.enqueue)
            has_varkw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in s_sig.parameters.values()
            )
            if target_time is not None and (has_varkw or "target_time" in s_sig.parameters):
                sched_kwargs["target_time"] = target_time
        except Exception:
            if target_time is not None:
                sched_kwargs["target_time"] = target_time

        try:
            sched_time = self.scheduler.enqueue(**sched_kwargs)
            job.scheduled_time = sched_time
            self.state_manager.save_job(job)
            logger.info(
                "Job %s: Enqueued into SpreadScheduler for %s", job.id, sched_time.isoformat()
            )
        except Exception as s_err:
            self._mark_failed(job, "upload", s_err)
            raise
