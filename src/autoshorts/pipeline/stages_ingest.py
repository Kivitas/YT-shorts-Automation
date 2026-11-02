"""Ingestion stage mixin for ShortsPipeline: metadata, transcript, slicing, and SemIf."""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from typing import Any

import autoshorts.decision.window_slicer as _window_slicer
from autoshorts.core.exceptions import (
    AutoShortsError,
    MetadataFetchError,
    TranscriptNotFoundError,
)
from autoshorts.core.models import (
    Job,
    JobStatus,
    VideoMetadata,
)
from autoshorts.decision.window_slicer import slice_transcript_windows
from autoshorts.media.compositor import extract_16khz_mono_wav
from autoshorts.media.encoder_probe import get_ffmpeg_path
from autoshorts.pipeline.common import _resolve_local_source

logger = logging.getLogger("autoshorts.pipeline")

generate_candidate_windows = getattr(
    _window_slicer, "generate_candidate_windows", slice_transcript_windows
)


class IngestMixin:
    """Stage methods for video ingestion: metadata, transcript, slicing, and SemIf."""

    _resolve_local_source = staticmethod(_resolve_local_source)

    def _run_metadata_stage(self: Any, job: Job) -> None:
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

    def _run_transcript_stage(self: Any, job: Job) -> None:
        import autoshorts.pipeline as pipeline_mod

        stage_id = self.state_manager.record_stage_start(job.id, "transcript_fetch")
        work_dir = pipeline_mod.get_work_dir() / job.id
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
                        "words": ws.get("words", []),
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

    def _load_transcript_segments(self: Any, job: Job) -> list[dict[str, Any]]:
        import autoshorts.pipeline as pipeline_mod

        work_dir = pipeline_mod.get_work_dir() / job.id
        transcript_cache = work_dir / "transcript.json"
        if transcript_cache.is_file():
            with open(transcript_cache, encoding="utf-8") as f:
                return json.load(f)
        return []

    def _run_slicing_stage(self: Any, job: Job) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "window_slicing")
        try:
            segments = self._load_transcript_segments(job)
            if not segments:
                raise AutoShortsError(
                    f"Cannot slice windows: No cached transcript found for job {job.id}"
                )

            import autoshorts.pipeline as pipeline_mod

            slicer_fn = getattr(
                pipeline_mod, "generate_candidate_windows", generate_candidate_windows
            )
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

    def _run_semif_stage(self: Any, job: Job, background_category: str | None = None) -> None:
        stage_id = self.state_manager.record_stage_start(job.id, "semif_evaluation")
        try:
            guidance = ""
            try:
                from autoshorts.analytics.attribution import (
                    PerformanceAttributionEngine,
                    get_winning_guidance_summary,
                )

                if self.state_manager:
                    engine = PerformanceAttributionEngine(self.state_manager)
                    guidance = engine.get_winning_guidance_summary()
                else:
                    guidance = get_winning_guidance_summary()
            except Exception as g_err:
                logger.debug("Failed retrieving winning guidance summary: %s", g_err)

            prompt = job.custom_instructions or self.config.job_defaults.focus_prompt
            if guidance:
                prompt = (
                    f"{prompt}\n\n[Channel Winning Styles Guidance]: {guidance}"
                    if prompt
                    else guidance
                )

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
