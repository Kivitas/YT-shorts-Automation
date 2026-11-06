"""Re-rendering and trim stages for ShortsPipeline."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from autoshorts.core.exceptions import AutoShortsError
from autoshorts.core.job_lock import job_lock
from autoshorts.core.models import Job, JobStatus, JumpCut
from autoshorts.edit_planner.edl_schema import create_empty_edl

logger = logging.getLogger("autoshorts.pipeline")


class RerenderMixin:
    """Stage methods for fast re-rendering and silence auto-trim."""

    def rerender_job(self: Any, job_id: str, **overrides: Any) -> Job:
        """Re-render under the per-job lock so it never races a running pipeline pass.

        Raises JobLockedError when another process or thread is already working on the job.
        """
        import autoshorts.pipeline as pipeline_mod

        with job_lock(pipeline_mod.get_work_dir() / job_id, job_id):
            return self._rerender_job_locked(job_id, **overrides)

    def _rerender_job_locked(self: Any, job_id: str, **overrides: Any) -> Job:
        """Fast 1-click re-render of subtitles and compositing stages skipping re-download and re-scoring.

        Robustly re-renders jobs from READY_FOR_REVIEW, SCHEDULED, COMPLETED, or FAILED.
        """
        import autoshorts.pipeline as pipeline_mod

        job = self.state_manager.get_job(job_id)
        if not job:
            raise AutoShortsError(f"Job {job_id} not found in state database")

        # If job was in FAILED state, clear previous error message to allow fresh retry
        if job.status == JobStatus.FAILED:
            job.error_message = None

        # Validate render overrides before touching the job, so a bad value changes nothing.
        from typing import get_args

        from autoshorts.core.config import LayoutMode
        from autoshorts.media.render_dispatch import parse_methods

        layout_override = overrides.get("layout_mode")
        if layout_override is not None and layout_override not in get_args(LayoutMode):
            raise AutoShortsError(
                f"Unknown layout_mode '{layout_override}'. Valid: {', '.join(get_args(LayoutMode))}"
            )
        method_override: str | None = None
        if overrides.get("render_method"):
            try:
                parsed = parse_methods(str(overrides["render_method"]))
            except ValueError as exc:
                raise AutoShortsError(str(exc)) from exc
            method_override = parsed[0].value if parsed else None

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

        if method_override is not None:
            # A one-off method for this re-render; variants of the original batch are dropped.
            job.render_method = method_override
            job.render_variants = []

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
        rerender_state: dict[str, Any] = {
            "raw_source": None,
            "raw_source_recovered": False,
            "steps": [],
        }
        raw_path = Path(job.raw_video_path) if job.raw_video_path else None
        if not raw_path or not raw_path.is_file():
            # Check work directory candidates (section_*.mp4 or *_raw.mp4)
            work_dir = pipeline_mod.get_work_dir() / job.id
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

    def auto_trim_job_silence(self: Any, job_id: str, min_gap_sec: float = 0.48) -> dict[str, Any]:
        """Compute tight JumpCut ranges for silences >= min_gap_sec from aligned_words.json."""
        import autoshorts.pipeline as pipeline_mod

        job = self.state_manager.get_job(job_id)
        if not job:
            raise AutoShortsError(f"Job {job_id} not found in state database")

        work_dir = pipeline_mod.get_work_dir() / job_id
        aligned_words_cache = work_dir / "aligned_words.json"
        words: list[dict[str, Any]] = []
        if aligned_words_cache.is_file():
            try:
                loaded = json.loads(aligned_words_cache.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    words = loaded
            except Exception as e:
                logger.debug("Failed reading aligned_words.json for job %s: %s", job_id, e)

        # Planner cuts are kept. Dead-air cuts are added only where they do not overlap them.
        existing_cuts: list[JumpCut] = list(job.edl.jump_cuts) if job.edl is not None else []

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

        new_cuts = [
            c
            for c in cuts
            if not any(c.cut_start < e.cut_end and e.cut_start < c.cut_end for e in existing_cuts)
        ]
        merged = sorted([*existing_cuts, *new_cuts], key=lambda c: c.cut_start)

        if job.edl is None:
            job.edl = create_empty_edl()
        job.edl.jump_cuts = merged
        self.state_manager.save_job(job)

        return {
            "job_id": job_id,
            "jump_cuts_added": len(new_cuts),
            "jump_cuts": [c.model_dump() for c in merged],
        }
