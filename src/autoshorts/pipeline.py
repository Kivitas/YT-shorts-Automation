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

import json
import logging
import threading
from collections.abc import Sequence
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
from autoshorts.core.exceptions import (
    AutoShortsError,
    DownloadError,
    MetadataFetchError,
    TranscriptNotFoundError,
)
from autoshorts.core.models import (
    Job,
    JobStatus,
    UploadResult,
    VideoMetadata,
)
from autoshorts.core.paths import get_output_dir, get_work_dir
from autoshorts.core.providers import get_provider
from autoshorts.core.state_manager import StateManager
from autoshorts.decision.orchestrator import SemIfDecider
from autoshorts.decision.window_slicer import slice_transcript_windows
from autoshorts.discovery.url_validator import ParsedLink, parse_links_input
from autoshorts.edit_planner.edl_schema import create_empty_edl
from autoshorts.edit_planner.multimodal_planner import MultimodalEditPlanner
from autoshorts.media.ass_styler import write_ass_file
from autoshorts.media.compositor import (
    FasterWhisperTranscriber,
    FFmpegCompositor,
    extract_16khz_mono_wav,
)
from autoshorts.media.dopamine_effects import pick_background_asset
from autoshorts.media.downloader import Downloader as MediaDownloader
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.scheduler.scheduler import SpreadScheduler
from autoshorts.transcript.fetcher import TranscriptFetcher
from autoshorts.uploader.description_builder import build_short_description, build_short_title
from autoshorts.uploader.local_exporter import LocalExportUploader
from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader
from autoshorts.uploader.youtube_api import YouTubeApiUploader

logger = logging.getLogger("autoshorts.pipeline")


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
        concurrency: int = 1,
    ) -> None:
        """Initialize pipeline with injected or default components."""
        self.config = config or load_config()
        self.state_manager = state_manager or StateManager()
        self.concurrency = max(1, concurrency)
        self._render_lock = threading.Semaphore(self.concurrency)

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
            self.uploader = LocalExportUploader(export_dir=get_output_dir())
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
                self.uploader = LocalExportUploader(export_dir=get_output_dir())

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
    ) -> Job:
        """Execute end-to-end processing pipeline for a single job with stage caching."""
        job = self._resolve_job(job_or_id)

        # If job is already completed or cleaned, return immediately
        if job.status in (JobStatus.COMPLETED, JobStatus.CLEANED):
            return job

        with self._render_lock:
            try:
                # Stage 1: Metadata Fetch
                if job.status == JobStatus.QUEUED:
                    self._run_metadata_stage(job)

                # Stage 2: Transcript Fetch (with Whisper Fallback)
                if job.status == JobStatus.METADATA_FETCHED:
                    self._run_transcript_stage(job)

                # Stage 3: Window Slicing
                if job.status == JobStatus.TRANSCRIBED:
                    self._run_slicing_stage(job)

                # Stage 4: SemIf Evaluation & Non-overlapping Selection
                if job.status == JobStatus.WINDOWS_SLICED:
                    self._run_semif_stage(job, background_category=background_category)

                # Stage 5: Downloader Precision Cut
                if job.status == JobStatus.SEMIF_EVALUATED:
                    self._run_download_stage(job)

                # Stage 6: Multimodal Edit Planning (EDL)
                if job.status == JobStatus.DOWNLOADING_SECTION and job.edl is None:
                    self._run_edl_stage(job)

                # Stage 7: Subtitle (.ass) Generation
                if job.status == JobStatus.DOWNLOADING_SECTION:
                    self._run_subtitles_stage(job)

                # Stage 8: Compositing (split-screen + dopamine effects) & Preview
                if job.status == JobStatus.SUBTITLES_GENERATED:
                    self._run_compositing_stage(
                        job,
                        background_category=background_category,
                        dopamine_level=dopamine_level,
                    )

                # Stage 9: Upload / Schedule / Review
                if job.status == JobStatus.READY_FOR_REVIEW:
                    if immediate_upload:
                        self._run_immediate_upload(job)
                    elif auto_upload or self.config.job_defaults.auto_approve:
                        self._run_schedule_upload(job)

                return job

            except Exception as e:
                logger.exception("Job %s failed during pipeline execution", job.id)
                job.transition_to(JobStatus.FAILED)
                job.error_message = str(e)
                self.state_manager.save_job(job)
                raise

    def process_batch(
        self,
        jobs_or_links: str | Sequence[str] | Sequence[Job] | Path,
        auto_upload: bool = False,
        immediate_upload: bool = False,
        background_category: str | None = None,
        dopamine_level: str | None = None,
        custom_instructions: str | None = None,
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
    ) -> Job:
        """Reset a failed job to QUEUED and re-execute pipeline."""
        job = self.state_manager.get_job(job_id)
        if not job:
            raise AutoShortsError(f"Job {job_id} not found for retry")

        if job.status == JobStatus.FAILED:
            job.transition_to(JobStatus.QUEUED)
            job.error_message = None
            self.state_manager.save_job(job)

        return self.process_job(
            job,
            auto_upload=auto_upload,
            immediate_upload=immediate_upload,
            background_category=background_category,
            dopamine_level=dopamine_level,
        )

    # -------------------------------------------------------------------------
    # Internal Stage Implementations
    # -------------------------------------------------------------------------

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

        try:
            # 1. Try fetching online subtitles via TranscriptFetcher
            if self.transcript_fetcher:
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

            candidates = slice_transcript_windows(
                transcript=segments,
                window_duration_seconds=float(self.config.job_defaults.max_duration_seconds),
                stride_seconds=float(self.config.job_defaults.window_stride_seconds),
                min_duration_seconds=float(self.config.job_defaults.min_duration_seconds),
                max_duration_seconds=float(self.config.job_defaults.max_duration_seconds),
            )

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
            raw_cut_path = work_dir / f"{job.id}_raw.mp4"

            self.downloader.download_section(
                url=job.source_url,
                start_sec=sel.candidate.start,
                end_sec=sel.candidate.end,
                output_path=raw_cut_path,
            )

            job.raw_video_path = str(raw_cut_path)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={"raw_video_path": str(raw_cut_path)},
            )
            self.state_manager.save_job(job)
            logger.info("Job %s: Precision cut saved to %s", job.id, raw_cut_path)
        except Exception as e:
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )
            raise DownloadError(f"Precision cut download failed for job {job.id}: {e}") from e

    def _run_edl_stage(self, job: Job) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "edit_planning")
        try:
            sel = job.selected_clips[0]
            clip_path = Path(job.raw_video_path) if job.raw_video_path else None
            if not clip_path or not clip_path.is_file():
                job.edl = create_empty_edl()
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

            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={"edl": json.loads(edl.model_dump_json())},
            )
            self.state_manager.save_job(job)
            logger.info(
                "Job %s: Multimodal EDL planned with %d jump cut(s) and %d punch-in(s).",
                job.id,
                len(edl.jump_cuts),
                len(edl.zoom_punch_ins),
            )
        except Exception as e:
            logger.warning(
                "Edit planning failed for job %s: %s; falling back to clean EDL", job.id, e
            )
            job.edl = create_empty_edl()
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )

    def _run_subtitles_stage(self, job: Job) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "subtitles_generation")
        try:
            sel = job.selected_clips[0]
            work_dir = get_work_dir() / job.id
            raw_cut_path = Path(job.raw_video_path) if job.raw_video_path else None
            ass_path = work_dir / f"{job.id}.ass"

            words: list[dict[str, Any]] = []

            # 1. Try extracting 16kHz WAV and running align_words for exact karaoke timing
            if raw_cut_path and raw_cut_path.is_file():
                try:
                    wav_path = work_dir / f"{job.id}_cut.wav"
                    extract_16khz_mono_wav(raw_cut_path, wav_path)
                    aligned = self.transcriber.align_words(wav_path)
                    if aligned:
                        words = aligned
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

            channel_credit = job.video_metadata.channel if job.video_metadata else None
            hook = sel.hook_text or (job.edl.recommended_hook if job.edl else "") or None
            hook_in = job.edl.hook_in_sec if (job.edl and job.edl.hook_in_sec > 0) else 0.0
            total_dur = (
                max(1.0, sel.candidate.duration - hook_in) if sel.candidate.duration else None
            )
            jump_cuts = job.edl.jump_cuts if (job.edl and job.edl.jump_cuts) else None
            style_preset = getattr(self.config.subtitles, "style_preset", "neon")

            write_ass_file(
                output_path=ass_path,
                words=words,
                hook_title=hook,
                config=self.config.subtitles,
                onscreen_credit=channel_credit,
                total_duration_sec=total_dur,
                hook_in_sec=hook_in,
                jump_cuts=jump_cuts,
                style_preset=style_preset,
            )

            job.subtitles_path = str(ass_path)
            job.transition_to(JobStatus.SUBTITLES_GENERATED)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={"subtitles_path": str(ass_path), "words_count": len(words)},
            )
            self.state_manager.save_job(job)
            logger.info("Job %s: Generated ASS subtitles (%d words).", job.id, len(words))
        except Exception as e:
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )
            raise

    def _run_compositing_stage(
        self,
        job: Job,
        background_category: str | None = None,
        dopamine_level: str | None = None,
    ) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "compositing")
        try:
            job.transition_to(JobStatus.COMPOSITING)
            self.state_manager.save_job(job)

            sel = job.selected_clips[0]
            output_dir = get_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            master_path = output_dir / f"{job.id}_master.mp4"

            dopamine = dopamine_level or self.config.job_defaults.dopamine_level
            bg_style = (
                background_category
                or sel.background_style
                or self.config.job_defaults.background_category
            )
            bg_file, _ = pick_background_asset(category=bg_style)

            # Run FFmpeg master compositing
            self.compositor.compose(
                job_id=job.id,
                source_clip=Path(job.raw_video_path),  # type: ignore[arg-type]
                edl=job.edl,
                background_path=bg_file,
                subtitles_path=Path(job.subtitles_path),  # type: ignore[arg-type]
                output_path=master_path,
                dopamine_level=dopamine,
            )
            job.rendered_video_path = str(master_path)

            # Optional low-resolution fast preview
            if self.config.media.generate_lowres_preview:
                preview_path = output_dir / f"{job.id}_preview.mp4"
                try:
                    self.compositor.generate_preview(
                        source_clip=master_path, output_path=preview_path
                    )
                    job.preview_video_path = str(preview_path)
                except Exception as p_err:
                    logger.warning("Job %s: Low-res preview generation failed: %s", job.id, p_err)

            job.transition_to(JobStatus.READY_FOR_REVIEW)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts={
                    "master_video": str(master_path),
                    "preview_video": job.preview_video_path,
                },
            )
            self.state_manager.save_job(job)
            logger.info("Job %s: Composited master Short at %s", job.id, master_path)
        except Exception as e:
            self.state_manager.record_stage_complete(
                stage_id, status="FAILED", artifacts={"error": str(e)}
            )
            raise

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
            template=self.config.upload.description_template,
        )

        return title, desc

    def _run_immediate_upload(self, job: Job) -> UploadResult:
        title, desc = self._prepare_upload_metadata(job)
        job.transition_to(JobStatus.UPLOADING)
        self.state_manager.save_job(job)

        stage_id = self.state_manager.record_stage_start(job.id, "immediate_upload")
        try:
            result = self.uploader.upload(
                path=Path(job.rendered_video_path),  # type: ignore[arg-type]
                title=title,
                description=desc,
                tags=["Shorts", "AutoShorts"],
                privacy=self.config.upload.privacy_status,
            )
            job.upload_result = result
            self.state_manager.save_job(job)

            # Verification
            job.transition_to(JobStatus.VERIFYING)
            self.state_manager.save_job(job)

            verified = self.uploader.verify(result.video_id)
            result.verified = verified
            job.upload_result = result

            job.transition_to(JobStatus.COMPLETED)
            self.state_manager.record_stage_complete(
                stage_id,
                status="COMPLETED",
                artifacts=json.loads(result.model_dump_json()),
            )
            self.state_manager.save_job(job)
            logger.info(
                "Job %s: Uploaded and verified as %s (%s)", job.id, result.video_id, result.url
            )

            # Post-upload transactional cleanup
            if self.cleaner and getattr(getattr(self.config, "cleanup", None), "enabled", True):
                try:
                    self.cleaner.cleanup_job(job.id, verified=True, upload_result=result)
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
            raise

    def _run_schedule_upload(self, job: Job) -> None:
        title, desc = self._prepare_upload_metadata(job)
        job.transition_to(JobStatus.SCHEDULED)
        self.state_manager.save_job(job)

        sched_time = self.scheduler.enqueue(
            job_id=job.id,
            video_path=Path(job.rendered_video_path),  # type: ignore[arg-type]
            title=title,
            description=desc,
            tags=["Shorts", "AutoShorts"],
            privacy_status=self.config.upload.privacy_status,
        )
        job.scheduled_time = sched_time
        self.state_manager.save_job(job)
        logger.info("Job %s: Enqueued into SpreadScheduler for %s", job.id, sched_time.isoformat())
