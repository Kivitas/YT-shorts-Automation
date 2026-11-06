"""Media processing stages for ShortsPipeline: download, EDL planning, subtitles, and compositing."""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from typing import Any

from autoshorts.core.exceptions import AutoShortsError, DownloadError
from autoshorts.core.models import Job, JobStatus
from autoshorts.core.render_spec import (
    CAM_GAMEPLAY_LAYOUT,
    RenderMethod,
    RenderRequest,
    RenderResult,
)
from autoshorts.decider.format_classifier import classify_format
from autoshorts.decider.style import choose_style
from autoshorts.edit_planner.edl_schema import create_empty_edl
from autoshorts.media.ass_styler import write_ass_file
from autoshorts.media.audio_master import normalize_two_pass
from autoshorts.media.cam_layout import cam_layout_from_config, resolve_cam_layout
from autoshorts.media.compositor import extract_16khz_mono_wav
from autoshorts.media.encoder_probe import get_ffmpeg_path
from autoshorts.media.ffmpeg_tools import check_delivery
from autoshorts.media.ranking_overlay import (
    build_ranking_ass,
    merge_ass_documents,
    ranking_timeline,
)
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
from autoshorts.media.render_dispatch import (
    MethodLimiter,
    build_default_backends,
    coerce_method,
    render_with_fallback,
)
from autoshorts.pipeline.common import _is_nonempty_file, _resolve_local_source

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
    import autoshorts.pipeline as pipeline_mod
    from autoshorts.core.paths import get_assets_dir
    from autoshorts.media.background_fetcher import (
        BACKGROUND_VIDEO_EXTENSIONS,
        is_valid_background_file,
        normalize_category,
    )

    # If pick_background_asset has been monkeypatched in tests, honor the mock
    pick_fn = getattr(pipeline_mod, "pick_background_asset", _de.pick_background_asset)
    if pick_fn is not _de.pick_background_asset:
        try:
            return pick_fn(category=category, assets_dir=assets_dir)
        except TypeError:
            return pick_fn()

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
        manifest_items: list[Any] = []
        if manifest_file.is_file():
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    # Collector writes "clips"; older manifests used "videos" or "files".
                    manifest_items = (
                        data.get("clips") or data.get("videos") or data.get("files") or []
                    )
                elif isinstance(data, list):
                    manifest_items = data
            except Exception as m_err:
                logger.debug("Failed to parse background manifest.json in %s: %s", bg_dir, m_err)
        elif manifest_l.is_file():
            try:
                for line in manifest_l.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        manifest_items.append(json.loads(line.strip()))
            except Exception as ml_err:
                logger.debug("Failed to parse background manifest.jsonl in %s: %s", bg_dir, ml_err)

        for item in manifest_items:
            fname = (
                item.get("filename") or item.get("path") if isinstance(item, dict) else str(item)
            )
            if fname:
                fp = bg_dir / fname if not Path(fname).is_absolute() else Path(fname)
                if is_valid_background_file(fp):
                    candidates.append(fp)

        # 2. If no manifest entries, scan directory directly for video files
        if not candidates:
            candidates = [
                p
                for p in bg_dir.iterdir()
                if p.suffix.lower() in BACKGROUND_VIDEO_EXTENSIONS and is_valid_background_file(p)
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
    return pick_fn(category=clean_cat, assets_dir=assets_dir, auto_fetch=False)


class MediaMixin:
    """Stage methods for media processing: download, EDL, subtitles, compositing."""

    _resolve_local_source = staticmethod(_resolve_local_source)

    def _run_download_stage(self: Any, job: Job) -> None:
        import autoshorts.pipeline as pipeline_mod

        stage_id = self.state_manager.record_stage_start(job.id, "precision_download")
        try:
            job.transition_to(JobStatus.DOWNLOADING_SECTION)
            self.state_manager.save_job(job)

            sel = job.selected_clips[0]
            work_dir = pipeline_mod.get_work_dir() / job.id
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

    def _run_edl_stage(self: Any, job: Job) -> None:
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

            # Wire LayaRhythmEngine if BGM track exists
            if getattr(job, "bgm_path", None) and job.edl and job.edl.zoom_punch_ins:
                try:
                    from autoshorts.media.rhythm_engine import LayaRhythmEngine

                    rhythm_engine = LayaRhythmEngine()
                    grid = rhythm_engine.analyze_rhythm(job.bgm_path)
                    job.edl.zoom_punch_ins = rhythm_engine.snap_zoom_punch_ins(
                        job.edl.zoom_punch_ins,
                        beats=grid.beats,
                        confidence=grid.confidence,
                    )
                except Exception as re_err:
                    logger.debug("Rhythm engine beat snapping skipped in EDL stage: %s", re_err)

            # Route the edit: countdown clips get the ranking edit (no gameplay panel).
            try:
                fmt = classify_format(
                    sel.candidate.text,
                    float(edl.hook_out_sec - edl.hook_in_sec),
                    getattr(sel.candidate, "words", None),
                    job_id=job.id,
                )
                edl.edit_format = "ranking" if fmt.kind == "ranking" else "standard"
                job.record_diagnostic(
                    "format",
                    kind=fmt.kind,
                    confidence=fmt.confidence,
                    rank_count=fmt.rank_count,
                    reasons=fmt.reasons,
                )
            except Exception as fmt_err:  # pragma: no cover - never block editing
                logger.debug("Format classification skipped: %s", fmt_err)

            # The selected clip's title and hook win. EDL suggestions are only logged.
            if edl.recommended_title and edl.recommended_title != sel.title:
                logger.info(
                    "Job %s: EDL suggests title %r; keeping selected title %r.",
                    job.id,
                    edl.recommended_title,
                    sel.title,
                )
            if edl.recommended_hook and edl.recommended_hook != sel.hook_text:
                logger.info(
                    "Job %s: EDL suggests hook %r; keeping selected hook %r.",
                    job.id,
                    edl.recommended_hook,
                    sel.hook_text,
                )

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

    def _run_subtitles_stage(self: Any, job: Job, use_cached_words: bool = False) -> None:
        import autoshorts.pipeline as pipeline_mod

        stage_id = self.state_manager.record_stage_start(job.id, "subtitles_generation")
        try:
            sel = job.selected_clips[0]
            work_dir = pipeline_mod.get_work_dir() / job.id
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
                    with open(aligned_words_cache, encoding="utf-8") as f:
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
            style_choice = choose_style(
                {
                    "job_id": job.id,
                    "duration_sec": sel.candidate.duration,
                    "format_kind": getattr(job.edl, "edit_format", None),
                    "source_kind": getattr(job, "source_kind", None),
                    "job_layout_mode": getattr(job, "layout_mode", None),
                    "job_style_preset": getattr(job, "style_preset", None),
                    "config_layout_mode": getattr(
                        getattr(self.config, "branding", None), "layout_mode", None
                    )
                    or getattr(getattr(self.config, "media", None), "layout_mode", None),
                    "config_style_preset": getattr(self.config.subtitles, "style_preset", "neon"),
                }
            )
            style_preset = style_choice.style_preset
            job.record_diagnostic(
                "style",
                layout_mode=style_choice.layout_mode,
                style_preset=style_preset,
                reasons=style_choice.reasons,
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
            engine_is_remotion = self._caption_engine() == "remotion"
            plan_written = False
            if engine_is_remotion or self._remotion_plan_wanted(job):
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
                plan_written = True
                if engine_is_remotion:
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
            if plan_written and not engine_is_remotion and ass_words:
                # The Remotion render method needs a words-free ASS so word captions are not
                # drawn twice; every other method keeps using the full ASS above.
                hook_ass_path = ass_path.with_name(f"{ass_path.stem}_hook{ass_path.suffix}")
                write_ass_file(**{**ass_kwargs, "output_path": hook_ass_path, "words": []})
                job.record_diagnostic("subtitles", hook_only_ass=str(hook_ass_path))

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

    def _validate_compositing_inputs(self: Any, job: Job) -> None:
        """Fail early, with a clear message, when the compositor's inputs are unusable.

        A missing or empty section cut is an error here. A missing subtitle file is simply
        regenerated, since the subtitle stage is cheap and does not depend on the render.
        """
        import autoshorts.pipeline as pipeline_mod

        nonempty_fn = getattr(pipeline_mod, "_is_nonempty_file", _is_nonempty_file)

        raw = Path(job.raw_video_path) if job.raw_video_path else None
        if not nonempty_fn(raw):
            if job.selected_clips:
                logger.info(
                    "Job %s: section cut missing from disk (%s); re-downloading section...",
                    job.id,
                    raw,
                )
                try:
                    self._run_download_stage(job)
                    raw = Path(job.raw_video_path) if job.raw_video_path else None
                except Exception as dl_err:
                    logger.warning(
                        "Job %s: automatic re-download attempt failed: %s", job.id, dl_err
                    )

        if not nonempty_fn(raw):
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
        if not nonempty_fn(subs):
            logger.warning(
                "Job %s: subtitles file missing; regenerating before compositing.", job.id
            )
            self._run_subtitles_stage(job)

    def _caption_engine(self: Any) -> str:
        subtitles_cfg = getattr(self.config, "subtitles", None)
        return getattr(subtitles_cfg, "caption_engine", "ass") or "ass"

    def _render_settings(self: Any, job: Job) -> tuple[RenderMethod, bool]:
        """Resolve (method, allow_fallback) for a job.

        caption_engine='remotion' (legacy setting) forces the Remotion method with no
        fallback, because its ASS has no word captions to fall back to.
        """
        if self._caption_engine() == "remotion":
            return RenderMethod.REMOTION, False
        cfg = getattr(self.config, "render", None)
        cfg_method = getattr(cfg, "method", None)
        method = coerce_method(
            getattr(job, "render_method", None)
            or (cfg_method if isinstance(cfg_method, str) else None),
            RenderMethod.AUTO,
        )
        fallback = getattr(cfg, "fallback", True)
        return method, fallback if isinstance(fallback, bool) else True

    def _remotion_plan_wanted(self: Any, job: Job) -> bool:
        """True when a Remotion caption plan may be needed (explicit use, or raea fallback)."""
        method, _ = self._render_settings(job)
        cfg = getattr(self.config, "render", None)
        cfg_methods = getattr(cfg, "methods", None)
        extra = (list(cfg_methods) if isinstance(cfg_methods, list) else []) + list(
            getattr(job, "render_variants", None) or []
        )
        if method == RenderMethod.REMOTION or "remotion" in extra:
            return True
        if method == RenderMethod.AUTO and is_remotion_ready(REMOTION_PROJECT_DIR):
            import importlib.util

            try:
                return importlib.util.find_spec("autoshorts.media.raea_renderer") is not None
            except (ImportError, ValueError):
                return False
        return False

    def _render_backends(self: Any) -> dict[RenderMethod, Any]:
        backends = getattr(self, "render_backends", None)
        if not isinstance(backends, dict):
            backends = build_default_backends(self.compositor, self.config)
            self.render_backends = backends
        return backends

    def _dispatch_render(
        self: Any, request: RenderRequest, method: RenderMethod, fallback: bool
    ) -> RenderResult:
        limiter = getattr(self, "_method_limiter", None)
        return render_with_fallback(
            request,
            method,
            self._render_backends(),
            fallback=fallback,
            limiter=limiter if isinstance(limiter, MethodLimiter) else None,
        )

    def _render_variants(
        self: Any, job: Job, request: RenderRequest, output_dir: Path
    ) -> dict[str, Any]:
        """Render the extra methods of strategy 'variants' in parallel into output/<job>/.

        Each variant is strict (no fallback) so the file really comes from its method; a
        failing variant is recorded and never fails the job.
        """
        import dataclasses
        from concurrent.futures import ThreadPoolExecutor

        extra = [m for m in (job.render_variants or [])[1:] if m != "auto"]
        results: dict[str, Any] = {}
        if not extra:
            return results
        var_dir = output_dir / job.id
        var_dir.mkdir(parents=True, exist_ok=True)

        def _one(name: str) -> tuple[str, dict[str, Any]]:
            kwargs = dict(request.extras.get("compose_kwargs") or {})
            kwargs["job_id"] = f"{job.id}_{name}"
            req = dataclasses.replace(
                request,
                output_path=var_dir / f"{name}.mp4",
                extras={**request.extras, "compose_kwargs": kwargs},
            )
            try:
                res = self._dispatch_render(req, coerce_method(name, RenderMethod.FFMPEG), False)
                return name, {
                    "ok": True,
                    "path": str(res.output_path),
                    "method_used": res.method_used.value,
                }
            except Exception as err:
                logger.warning("Job %s: variant %s failed: %s", job.id, name, err)
                return name, {"ok": False, "error": str(err)}

        with ThreadPoolExecutor(max_workers=len(extra)) as pool:
            for name, info in pool.map(_one, extra):
                results[name] = info
        return results

    def _apply_remotion_captions(self: Any, job: Job, base_path: Path) -> Path:
        """Render the Remotion caption track and composite it onto the composed Short."""
        import autoshorts.pipeline as pipeline_mod

        plan_path = pipeline_mod.get_work_dir() / job.id / f"{job.id}_captions.json"
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

    def _prepare_ranking_overlay(self: Any, job: Job) -> Path | None:
        """Return a subtitles file with rank cards for a ranking clip, or None for watching clips."""
        import autoshorts.pipeline as pipeline_mod

        edl = job.edl
        if edl is None or getattr(edl, "edit_format", "standard") != "ranking":
            return None
        if not job.subtitles_path:
            return None

        fmt = job.diagnostics.get("format") if isinstance(job.diagnostics, dict) else None
        rank_count = fmt.get("rank_count") if isinstance(fmt, dict) else None
        rank_count = int(rank_count) if rank_count else 5
        duration = float(edl.hook_out_sec - edl.hook_in_sec)
        if duration <= 0 and job.video_metadata:
            duration = float(job.video_metadata.duration)
        if duration <= 0:
            return None

        base = Path(job.subtitles_path).read_text(encoding="utf-8")
        overlay = build_ranking_ass(ranking_timeline(rank_count, duration))
        work_dir = pipeline_mod.get_work_dir() / job.id
        work_dir.mkdir(parents=True, exist_ok=True)
        merged_path = work_dir / f"{job.id}_ranking.ass"
        merged_path.write_text(merge_ass_documents(base, overlay), encoding="utf-8")
        logger.info("Job %s: ranking edit with %d rank cards", job.id, rank_count)
        return merged_path

    def _prepare_chyron_overlay(
        self: Any,
        job: Job,
        layout_mode: str,
        current_subtitles_path: Path | str | None,
        **kwargs: Any,
    ) -> tuple[Path | None, dict[str, Any]]:
        """Inject broadcast lower-third chyron overlay into ASS subtitles if requested."""
        import autoshorts.pipeline as pipeline_mod
        from autoshorts.media.chyron_overlay import (
            ChyronData,
            derive_automated_chyron,
            merge_chyron_into_ass,
        )

        branding_cfg = getattr(self.config, "branding", None)
        chyron_diag = job.diagnostics.get("chyron") if isinstance(job.diagnostics, dict) else None
        if not isinstance(chyron_diag, dict):
            chyron_diag = {}

        enable_chyron = (
            kwargs.get("enable_chyron")
            or getattr(job, "enable_chyron", None)
            or chyron_diag.get("enable_chyron")
            or (job.diagnostics.get("enable_chyron") if isinstance(job.diagnostics, dict) else None)
            or getattr(branding_cfg, "enable_chyron", False)
            or bool(
                kwargs.get("chyron_text")
                or getattr(job, "chyron_text", None)
                or chyron_diag.get("chyron_text")
            )
        )
        enable_chyron = bool(enable_chyron)

        chyron_badge = (
            kwargs.get("chyron_badge")
            or getattr(job, "chyron_badge", None)
            or chyron_diag.get("chyron_badge")
            or getattr(branding_cfg, "chyron_badge", "BREAKING NEWS")
            or "BREAKING NEWS"
        )
        chyron_text = (
            kwargs.get("chyron_text")
            or getattr(job, "chyron_text", None)
            or chyron_diag.get("chyron_text")
        )
        chyron_style = (
            kwargs.get("chyron_style")
            or getattr(job, "chyron_style", None)
            or chyron_diag.get("chyron_style")
            or getattr(branding_cfg, "chyron_style", "broadcast_red")
            or "broadcast_red"
        )

        chyron_params: dict[str, Any] = {
            "enable_chyron": enable_chyron,
            "chyron_badge": chyron_badge,
            "chyron_text": chyron_text,
            "chyron_style": chyron_style,
        }

        if not enable_chyron:
            return None, chyron_params

        # Derive automated chyron if custom headline was not explicitly provided
        title = job.video_metadata.title if job.video_metadata else None
        hook = getattr(job, "hook_text", None)
        if not hook and job.edl and getattr(job.edl, "hook_text", None):
            hook = job.edl.hook_text
        if not hook and job.selected_clips:
            hook = getattr(job.selected_clips[0], "hook_title", None) or getattr(
                job.selected_clips[0], "reason", None
            )

        if chyron_text:
            chyron_obj = ChyronData(
                category_badge=str(chyron_badge),
                headline_text=str(chyron_text),
                style=str(chyron_style),
            )
        else:
            chyron_obj = derive_automated_chyron(
                title=title,
                hook_text=hook,
                default_badge=str(chyron_badge),
                style=str(chyron_style),
            )

        chyron_params["chyron_badge"] = chyron_obj.category_badge
        chyron_params["chyron_text"] = chyron_obj.headline_text
        chyron_params["chyron_style"] = chyron_obj.style

        # Total duration for chyron
        total_sec = 0.0
        if job.edl and (job.edl.hook_out_sec - job.edl.hook_in_sec) > 0:
            total_sec = float(job.edl.hook_out_sec - job.edl.hook_in_sec)
        elif job.video_metadata and job.video_metadata.duration > 0:
            total_sec = float(job.video_metadata.duration)
        if total_sec <= 0:
            total_sec = 30.0

        merged_path = None
        if current_subtitles_path and Path(current_subtitles_path).is_file():
            try:
                base_ass = Path(current_subtitles_path).read_text(encoding="utf-8")
                merged_ass = merge_chyron_into_ass(
                    base_ass,
                    chyron_obj,
                    total_duration_sec=total_sec,
                    layout_mode=layout_mode,
                )
                work_dir = pipeline_mod.get_work_dir() / job.id
                work_dir.mkdir(parents=True, exist_ok=True)
                merged_path = work_dir / f"{job.id}_chyron.ass"
                merged_path.write_text(merged_ass, encoding="utf-8")
                logger.info(
                    "Job %s: lower-third chyron overlay merged into ASS subtitles [%s: %s]",
                    job.id,
                    chyron_obj.category_badge,
                    chyron_obj.headline_text,
                )
            except Exception as exc:
                logger.warning("Job %s: failed to merge chyron into ASS subtitles: %s", job.id, exc)

        job.record_diagnostic(
            "chyron",
            enabled=True,
            badge=chyron_obj.category_badge,
            headline=chyron_obj.headline_text,
            style=chyron_obj.style,
        )
        return merged_path, chyron_params

    def _run_compositing_stage(
        self: Any,
        job: Job,
        background_category: str | None = None,
        dopamine_level: str | None = None,
        **kwargs: Any,
    ) -> None:
        import autoshorts.pipeline as pipeline_mod

        stage_id = self.state_manager.record_stage_start(job.id, "compositing")
        try:
            self._validate_compositing_inputs(job)
            if job.status not in (JobStatus.SUBTITLES_GENERATED, JobStatus.COMPOSITING):
                job.status = JobStatus.SUBTITLES_GENERATED
            job.transition_to(JobStatus.COMPOSITING)
            self.state_manager.save_job(job)

            sel = job.selected_clips[0]
            output_dir = pipeline_mod.get_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            master_path = output_dir / f"{job.id}_master.mp4"

            dopamine = (
                dopamine_level
                or getattr(job, "dopamine_level", None)
                or self.config.job_defaults.dopamine_level
            )
            # A stale "none" (from a preset) must not hide a real category the user picked.
            bg_chain = [
                background_category,
                getattr(job, "background_category", None),
                sel.background_style,
            ]
            bg_real = [c for c in bg_chain if c and str(c).strip().lower() != "none"]
            if bg_real:
                bg_style = bg_real[0]
            elif any(c and str(c).strip().lower() == "none" for c in bg_chain):
                bg_style = "none"
            else:
                bg_style = self.config.job_defaults.background_category
            import autoshorts.pipeline as pipeline_mod

            branding_cfg = getattr(self.config, "branding", None)
            style_diag = job.diagnostics.get("style") if isinstance(job.diagnostics, dict) else None
            diag_layout = style_diag.get("layout_mode") if isinstance(style_diag, dict) else None
            layout_mode = (
                diag_layout
                or getattr(job, "layout_mode", None)
                or getattr(branding_cfg, "layout_mode", None)
                or getattr(self.config.media, "layout_mode", "split_50_50")
            )
            cam_layout = None
            if layout_mode == CAM_GAMEPLAY_LAYOUT:
                # Resolve the camera region once, here, so every render backend (raea,
                # Remotion, FFmpeg) sees the same final layout. No region -> split layout.
                cam_cfg = getattr(branding_cfg, "cam_layout", None)
                cam_layout = resolve_cam_layout(
                    cam_layout_from_config(cam_cfg) if cam_cfg is not None else None,
                    source=Path(job.raw_video_path),  # type: ignore[arg-type]
                    auto_detect=bool(getattr(cam_cfg, "auto_detect", True)),
                )
                if cam_layout is None:
                    layout_mode = "split_50_50"
            if layout_mode == CAM_GAMEPLAY_LAYOUT:
                # Camera on top, gameplay from the source: no gameplay background is needed.
                bg_file, bg_offset = None, None
            else:
                bg_fn = getattr(
                    pipeline_mod, "select_gameplay_background", select_gameplay_background
                )
                bg_file, bg_offset = bg_fn(category=bg_style)

            audio_cfg = getattr(self.config, "audio_mastering", None)
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
                "background_offset": bg_offset,
                "subtitles_path": Path(job.subtitles_path),  # type: ignore[arg-type]
                "output_path": master_path,
                "dopamine_level": dopamine,
            }
            ranking_subs = self._prepare_ranking_overlay(job)
            if ranking_subs is not None:
                # Ranking edit: no gameplay panel, captions plus the rank-card overlay.
                comp_kwargs["background_path"] = None
                comp_kwargs["subtitles_path"] = ranking_subs
                layout_mode = "full_center_crop"

            chyron_subs, chyron_info = self._prepare_chyron_overlay(
                job,
                layout_mode=layout_mode,
                current_subtitles_path=comp_kwargs.get("subtitles_path"),
                **kwargs,
            )
            if chyron_subs is not None:
                comp_kwargs["subtitles_path"] = chyron_subs
            bgm_file = getattr(job, "bgm_path", None)
            duck_release_ms: int | None = None
            if bgm_file:
                try:
                    from autoshorts.media.rhythm_engine import LayaRhythmEngine

                    rhythm_engine = LayaRhythmEngine()
                    grid = rhythm_engine.analyze_rhythm(bgm_file)
                    duck_release_ms = int(grid.ducking_release_ms)
                    # zoom_punch_ins were already beat-snapped in the EDL stage.
                    job.record_diagnostic(
                        "rhythm",
                        duck_release_ms=duck_release_ms,
                        bpm=grid.bpm,
                        confidence=grid.confidence,
                    )
                except Exception as r_err:
                    logger.debug("LayaRhythmEngine analysis skipped: %s", r_err)

            extra_comp: dict[str, Any] = {
                "layout_mode": layout_mode,
                **({"cam_layout": cam_layout} if cam_layout is not None else {}),
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
                "bg_music_path": bgm_file,
                "rhythm_ducking_release": duck_release_ms,
                "duck_release_ms": duck_release_ms,
                "enable_chyron": chyron_info.get("enable_chyron", False),
                "chyron_badge": chyron_info.get("chyron_badge"),
                "chyron_text": chyron_info.get("chyron_text"),
                "chyron_style": chyron_info.get("chyron_style"),
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

            # Dispatch the render through the method chain (raea -> remotion -> ffmpeg).
            render_method, render_fallback = self._render_settings(job)
            plan_path = pipeline_mod.get_work_dir() / job.id / f"{job.id}_captions.json"
            hook_info = (
                job.diagnostics.get("subtitles") if isinstance(job.diagnostics, dict) else None
            )
            hook_ass = hook_info.get("hook_only_ass") if isinstance(hook_info, dict) else None
            render_request = RenderRequest(
                job_id=job.id,
                source_video=Path(job.raw_video_path),  # type: ignore[arg-type]
                output_path=master_path,
                layout_mode=str(layout_mode),
                cam_layout=cam_layout,
                background_video=Path(bg_file) if bg_file else None,
                subtitle_ass=Path(comp_kwargs["subtitles_path"]),
                caption_track=plan_path if plan_path.is_file() else None,
                edl=job.edl.model_dump(mode="json") if hasattr(job.edl, "model_dump") else None,
                branding={
                    "color_grading": color_grading,
                    "watermark_text": watermark_text,
                    "watermark_position": watermark_position,
                    "watermark_opacity": watermark_opacity,
                    "progress_bar_position": progress_bar_position,
                    "progress_bar_color": progress_bar_color,
                },
                extras={"compose_kwargs": comp_kwargs, "hook_only_ass": hook_ass},
            )
            variant_results: dict[str, Any] = {}
            variant_thread = None
            if len(job.render_variants or []) > 1:
                import threading

                def _run_variants() -> None:
                    variant_results.update(self._render_variants(job, render_request, output_dir))

                variant_thread = threading.Thread(target=_run_variants, daemon=True)
                variant_thread.start()

            render_res = self._dispatch_render(render_request, render_method, render_fallback)
            composed_res = render_res.output_path
            # A master that does not decode must never reach review: re-render once, then stop.
            for attempt in range(2):
                check_path = (
                    Path(composed_res) if isinstance(composed_res, (str, Path)) else master_path
                )
                if not check_path.is_file():
                    break
                decode_errors = pipeline_mod.count_decode_errors(check_path)
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
                render_res = self._dispatch_render(render_request, render_method, render_fallback)
                composed_res = render_res.output_path
            final_path = (
                Path(composed_res) if isinstance(composed_res, (str, Path)) else master_path
            )
            if not final_path.is_file() and master_path.is_file():
                final_path = master_path
            method_info: dict[str, Any] = {
                "requested": render_method.value,
                "method_used": render_res.method_used.value,
                "attempts": [{"method": m.value, "error": e} for m, e in render_res.attempts],
                "duration_sec": round(render_res.duration_sec, 2),
                "fallback_used": any(e for _, e in render_res.attempts),
            }
            job.record_diagnostic("render_dispatch", **method_info)
            if render_res.method_used == RenderMethod.REMOTION:
                job.record_diagnostic(
                    "compositing",
                    caption_engine="remotion",
                    caption_track=str(plan_path.with_suffix(".webm")),
                )
            if variant_thread is not None:
                variant_thread.join()
                if final_path.is_file():
                    import shutil

                    primary_copy = output_dir / job.id / f"{render_res.method_used.value}.mp4"
                    primary_copy.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(final_path, primary_copy)
                        variant_results[render_res.method_used.value] = {
                            "ok": True,
                            "path": str(primary_copy),
                            "method_used": render_res.method_used.value,
                            "primary": True,
                        }
                    except OSError as copy_err:
                        logger.warning("Job %s: primary variant copy failed: %s", job.id, copy_err)
                job.record_diagnostic("render_variants", **variant_results)
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
                    work_dir = pipeline_mod.get_work_dir() / job.id
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
            job.record_diagnostic(
                "render",
                method_used=method_info["method_used"],
                attempts=method_info["attempts"],
            )

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

    _run_compose_stage = _run_compositing_stage
