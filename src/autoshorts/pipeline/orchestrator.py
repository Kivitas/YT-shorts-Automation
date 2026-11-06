"""Master pipeline orchestrator for AutoShorts."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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
from autoshorts.core.exceptions import AutoShortsError
from autoshorts.core.job_lock import JobLockedError, job_lock
from autoshorts.core.models import (
    Job,
    JobStatus,
)
from autoshorts.core.providers import get_provider
from autoshorts.core.state_manager import StateManager
from autoshorts.decision.orchestrator import SemIfDecider
from autoshorts.discovery.url_validator import ParsedLink, parse_links_input
from autoshorts.edit_planner.multimodal_planner import MultimodalEditPlanner
from autoshorts.media.compositor import FasterWhisperTranscriber, FFmpegCompositor
from autoshorts.media.downloader import Downloader as MediaDownloader
from autoshorts.media.render_dispatch import MethodLimiter, assign_methods, parse_methods
from autoshorts.pipeline.common import (
    _env_int,
    _is_nonempty_file,
    _job_lock_is_held,
    _resolve_local_source,
)
from autoshorts.pipeline.rerender import RerenderMixin
from autoshorts.pipeline.stages_ingest import IngestMixin
from autoshorts.pipeline.stages_media import MediaMixin
from autoshorts.pipeline.stages_upload import UploadMixin
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.scheduler import SpreadScheduler
from autoshorts.transcript.fetcher import TranscriptFetcher
from autoshorts.uploader.local_exporter import LocalExportUploader
from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader
from autoshorts.uploader.youtube_api import YouTubeApiUploader

logger = logging.getLogger("autoshorts.pipeline")


class ShortsPipeline(IngestMixin, MediaMixin, UploadMixin, RerenderMixin):
    """The master pipeline orchestrator for AutoShorts."""

    _resolve_local_source = staticmethod(_resolve_local_source)

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
            resolved_concurrency = _env_int("AUTOSHORTS_CONCURRENCY", 3)
        else:
            resolved_concurrency = concurrency
        self.concurrency = max(1, resolved_concurrency)
        self._render_lock = threading.Semaphore(self.concurrency)
        self._render_tls = threading.local()
        # Lock order: _render_lock (global) is always taken first, then at most ONE per-method
        # slot at a time inside render_with_fallback. Method slots are leaf locks, so
        # global -> method can never deadlock; the slot is released before falling to the next
        # method in the chain.
        render_cfg = getattr(self.config, "render", None)
        limits = getattr(render_cfg, "max_parallel_per_method", None)
        self._method_limiter = MethodLimiter(limits if isinstance(limits, dict) else None)
        self.render_backends: dict[Any, Any] | None = None  # built lazily; tests may inject

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
            reconciled = self.state_manager.reconcile_stranded_jobs(is_job_active=_job_lock_is_held)
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
            for line in links_input.splitlines():
                stripped = line.strip()
                if stripped and "|" not in stripped and " : " in stripped:
                    parts = stripped.split(" : ", 1)
                    lines.append(f"{parts[0].strip()} | {parts[1].strip()}")
                else:
                    lines.append(line)
            normalized_input = "\n".join(lines)
        elif isinstance(links_input, Sequence) and not isinstance(links_input, (str, bytes)):
            lines = []
            for line in links_input:
                if isinstance(line, str) and "|" not in line and " : " in line:
                    parts = line.split(" : ", 1)
                    lines.append(f"{parts[0].strip()} | {parts[1].strip()}")
                else:
                    lines.append(line)
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
        enable_chyron: bool | None = None,
        chyron_badge: str | None = None,
        chyron_text: str | None = None,
    ) -> Job:
        """Run the pipeline for one job, unless another process is already working on it.

        The CLI and the dashboard share one database, so a queued job can be picked up by both.
        The per-job file lock makes the second process skip it instead of racing the first.
        """
        import autoshorts.pipeline as pipeline_mod

        job = self._resolve_job(job_or_id)
        try:
            with job_lock(pipeline_mod.get_work_dir() / job.id, job.id):
                return self._process_job_locked(
                    job,
                    auto_upload=auto_upload,
                    immediate_upload=immediate_upload,
                    background_category=background_category,
                    dopamine_level=dopamine_level,
                    style_preset=style_preset,
                    privacy_status=privacy_status,
                    enable_chyron=enable_chyron,
                    chyron_badge=chyron_badge,
                    chyron_text=chyron_text,
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
        enable_chyron: bool | None = None,
        chyron_badge: str | None = None,
        chyron_text: str | None = None,
    ) -> Job:
        job = self._resolve_job(job_or_id)

        if style_preset is not None:
            job.style_preset = style_preset
        if privacy_status is not None:
            job.privacy_status = privacy_status
        if enable_chyron is not None:
            job.enable_chyron = enable_chyron
        if chyron_badge is not None:
            job.chyron_badge = chyron_badge
        if chyron_text is not None:
            job.chyron_text = chyron_text

        if enable_chyron is not None or chyron_badge is not None or chyron_text is not None:
            chyron_diag: dict[str, Any] = {}
            if enable_chyron is not None:
                chyron_diag["enable_chyron"] = enable_chyron
            if chyron_badge is not None:
                chyron_diag["chyron_badge"] = chyron_badge
            if chyron_text is not None:
                chyron_diag["chyron_text"] = chyron_text
            job.record_diagnostic("chyron", **chyron_diag)

        # If job is already completed or cleaned, return immediately
        if job.status in (JobStatus.COMPLETED, JobStatus.CLEANED):
            return job

        # Only the local, CPU/IO-heavy stages (yt-dlp cut, EDL, ASS, ffmpeg) take a render
        # slot, one per stage. Metadata, transcript, SemIf and uploads run unthrottled, and
        # no stage holds the slot while calling another stage, so the semaphore is never
        # acquired twice on one thread.
        from autoshorts.decision.orchestrator import get_decision_coalescer

        coalescer = get_decision_coalescer()
        needs_semif = job.status in (
            JobStatus.QUEUED,
            JobStatus.METADATA_FETCHED,
            JobStatus.TRANSCRIBED,
            JobStatus.WINDOWS_SLICED,
        )
        if needs_semif:
            coalescer.register_inflight_job(job.id)

        stage = "queued"
        try:
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
            finally:
                if needs_semif:
                    coalescer.unregister_inflight_job(job.id)

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
                        enable_chyron=enable_chyron,
                        chyron_badge=chyron_badge,
                        chyron_text=chyron_text,
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

        import autoshorts.pipeline as pipeline_mod

        nonempty_fn = getattr(pipeline_mod, "_is_nonempty_file", _is_nonempty_file)

        raw = Path(job.raw_video_path) if job.raw_video_path else None
        if not nonempty_fn(raw):
            return None

        if failed_stage == "upload" and nonempty_fn(
            Path(job.rendered_video_path) if job.rendered_video_path else None
        ):
            return JobStatus.READY_FOR_REVIEW
        if failed_stage == "compositing" and job.edl is not None:
            subs = Path(job.subtitles_path) if job.subtitles_path else None
            if nonempty_fn(subs):
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
        enable_chyron: bool | None = None,
        chyron_badge: str | None = None,
        chyron_text: str | None = None,
        render_method: str | None = None,
        render_methods: Sequence[str] | str | None = None,
        render_strategy: str | None = None,
    ) -> list[Job]:
        """Process a batch of links or Job objects, concurrently when concurrency > 1.

        Render strategies (``render_strategy`` / ``render.strategy``):
        - ``single`` (default): every job uses ``render_method`` (default ``render.method``).
        - ``round_robin``: job i uses ``render_methods[i % len]``; different methods render in
          parallel, each bounded by ``render.max_parallel_per_method``.
        - ``variants``: every job renders once per method; ``render_methods[0]`` is the primary
          (``job.rendered_video_path``, the file the upload step uses) and the others are written
          to ``output/<job>_master_<method>.mp4``.
        """
        if isinstance(jobs_or_links, (str, Path)) or (
            isinstance(jobs_or_links, Sequence)
            and len(jobs_or_links) > 0
            and isinstance(jobs_or_links[0], str)
        ):
            jobs = self.submit_links(jobs_or_links, custom_instructions=custom_instructions)
        else:
            jobs = list(jobs_or_links)  # type: ignore[arg-type]

        self._assign_render_methods(jobs, render_method, render_methods, render_strategy)

        for j in jobs:
            if enable_chyron or chyron_badge is not None or chyron_text is not None:
                if hasattr(j, "enable_chyron") and enable_chyron is not None:
                    j.enable_chyron = enable_chyron
                if hasattr(j, "chyron_badge") and chyron_badge is not None:
                    j.chyron_badge = chyron_badge
                if hasattr(j, "chyron_text") and chyron_text is not None:
                    j.chyron_text = chyron_text
                chyron_dict: dict[str, Any] = {"enable_chyron": bool(enable_chyron)}
                if chyron_badge is not None:
                    chyron_dict["chyron_badge"] = chyron_badge
                if chyron_text is not None:
                    chyron_dict["chyron_text"] = chyron_text
                j.record_diagnostic("chyron", **chyron_dict)
                self.state_manager.save_job(j)

        def _process_single(j: Job) -> Job:
            try:
                return self.process_job(
                    j,
                    auto_upload=auto_upload,
                    immediate_upload=immediate_upload,
                    background_category=background_category,
                    dopamine_level=dopamine_level,
                    style_preset=style_preset,
                    privacy_status=privacy_status,
                    enable_chyron=enable_chyron,
                    chyron_badge=chyron_badge,
                    chyron_text=chyron_text,
                )
            except Exception as err:
                logger.warning("Batch processing encountered error on job %s: %s", j.id, err)
                return self.state_manager.get_job(j.id) or j

        if len(jobs) > 1 and getattr(self, "concurrency", 1) > 1:
            from concurrent.futures import ThreadPoolExecutor

            workers = max(1, int(self.concurrency))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                results = list(executor.map(_process_single, jobs))
        else:
            results = [_process_single(j) for j in jobs]

        return results

    def _assign_render_methods(
        self,
        jobs: Sequence[Job],
        render_method: str | None,
        render_methods: Sequence[str] | str | None,
        render_strategy: str | None,
    ) -> None:
        """Stamp each job with its render method(s) before the worker threads start.

        Done up front on the calling thread so worker threads never race on assignment. Without
        any explicit input or configured methods this is a no-op (default behaviour unchanged).
        """
        cfg = getattr(self.config, "render", None)
        cfg_methods = getattr(cfg, "methods", None)
        cfg_strategy = getattr(cfg, "strategy", "single")
        strategy = render_strategy or (cfg_strategy if isinstance(cfg_strategy, str) else "single")
        strategy = strategy.strip().lower()
        if strategy not in ("single", "round_robin", "variants"):
            raise AutoShortsError(f"Unknown render strategy '{strategy}'")
        methods = parse_methods(
            render_methods
            if render_methods
            else (cfg_methods if isinstance(cfg_methods, list) else None)
        )
        if render_method and (strategy == "single" or not methods):
            # An explicit single method always wins over a configured/passed list for 'single'.
            methods = parse_methods(render_method)[:1] or methods
        if not methods or (strategy == "single" and not render_method and not render_methods):
            return
        if strategy == "single":
            methods = methods[:1]
        assigned = assign_methods(strategy, methods, len(jobs))
        for job, method in zip(jobs, assigned, strict=True):
            job.render_method = method.value
            job.render_variants = [m.value for m in methods] if strategy == "variants" else []
            job.record_diagnostic(
                "render_plan",
                strategy=strategy,
                method=method.value,
                variants=job.render_variants,
            )
            self.state_manager.save_job(job)

    def retry_job(
        self,
        job_id: str,
        auto_upload: bool = False,
        immediate_upload: bool = False,
        background_category: str | None = None,
        dopamine_level: str | None = None,
        style_preset: str | None = None,
        privacy_status: str | None = None,
        enable_chyron: bool | None = None,
        chyron_badge: str | None = None,
        chyron_text: str | None = None,
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
            enable_chyron=enable_chyron,
            chyron_badge=chyron_badge,
            chyron_text=chyron_text,
        )

    def _resolve_job(self, job_or_id: Job | str) -> Job:
        if isinstance(job_or_id, Job):
            return job_or_id
        loaded = self.state_manager.get_job(job_or_id)
        if not loaded:
            raise AutoShortsError(f"Job {job_or_id} not found in state database")
        return loaded
