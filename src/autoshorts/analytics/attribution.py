"""Closed-Loop Performance Attribution Engine for YouTube Shorts.

Joins video analytics telemetry with pipeline job configurations to compute the
Shorts Viral Score (SVS, 0-100), identify high-performing layout modes, caption
styles, and background styles, and recommend high-confidence Winning Recipes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from autoshorts.analytics.models import StyleBreakdownItem, VideoAnalyticsRecord, WinningRecipe
from autoshorts.core.models import Job
from autoshorts.core.state_manager import StateManager

logger = logging.getLogger(__name__)


class PerformanceAttributionEngine:
    """Computes closed-loop attribution metrics, dimension breakdowns, and winning recipes."""

    def __init__(self, state_manager: StateManager) -> None:
        """Initialize PerformanceAttributionEngine with a StateManager instance."""
        self.state_manager = state_manager

    # -------------------------------------------------------------------------
    # Join Analytics Records with Job Metadata
    # -------------------------------------------------------------------------

    def get_records_with_jobs(self) -> list[tuple[VideoAnalyticsRecord, Job | None]]:
        """Retrieve all recorded analytics records paired with their originating Job model."""
        analytics_rows = self.state_manager.list_video_analytics(limit=500)
        jobs = self.state_manager.list_jobs(limit=500)

        jobs_by_id: dict[str, Job] = {job.id: job for job in jobs}
        jobs_by_vid: dict[str, Job] = {}
        for job in jobs:
            if job.upload_result and job.upload_result.video_id:
                jobs_by_vid[job.upload_result.video_id] = job
            elif job.video_metadata and job.video_metadata.video_id:
                jobs_by_vid[job.video_metadata.video_id] = job

        paired: list[tuple[VideoAnalyticsRecord, Job | None]] = []
        for row in analytics_rows:
            try:
                rec = VideoAnalyticsRecord.model_validate(row)
            except Exception as e:
                logger.debug(f"Failed to validate analytics row {row.get('video_id')}: {e}")
                continue

            job = jobs_by_id.get(rec.job_id) or jobs_by_vid.get(rec.video_id)
            paired.append((rec, job))

        return paired

    def extract_style_attributes(self, job: Job | None) -> dict[str, str]:
        """Extract canonical style attributes from a Job instance with safe fallbacks."""
        if job is None:
            return {
                "layout_mode": "split_50_50",
                "style_preset": "hormozi",
                "background_category": "subway",
                "color_grading": "none",
            }

        layout = getattr(job, "layout_mode", None) or "split_50_50"
        preset = getattr(job, "style_preset", None) or "hormozi"
        color = getattr(job, "color_grading", None) or "none"

        bg = "none"
        if (
            job.selected_clips
            and len(job.selected_clips) > 0
            and job.selected_clips[0].background_style
        ):
            bg = job.selected_clips[0].background_style
        elif job.edl and getattr(job.edl, "background_style_recommendation", None):
            bg = job.edl.background_style_recommendation
        elif hasattr(job, "background_category") and job.background_category:
            bg = job.background_category
        else:
            bg = "subway" if "split" in layout else "none"

        return {
            "layout_mode": str(layout),
            "style_preset": str(preset),
            "background_category": str(bg),
            "color_grading": str(color),
        }

    # -------------------------------------------------------------------------
    # Dimension Performance Breakdowns
    # -------------------------------------------------------------------------

    def get_breakdowns(self) -> dict[str, list[StyleBreakdownItem]]:
        """Compute performance breakdowns grouped by layout_mode, style_preset, and background_category."""
        pairs = self.get_records_with_jobs()
        dimensions = ["layout_mode", "style_preset", "background_category"]
        breakdowns: dict[str, list[StyleBreakdownItem]] = {}

        for dim in dimensions:
            groups: dict[str, list[VideoAnalyticsRecord]] = defaultdict(list)
            for rec, job in pairs:
                attrs = self.extract_style_attributes(job)
                dim_val = attrs.get(dim, "default")
                groups[dim_val].append(rec)

            items: list[StyleBreakdownItem] = []
            for key, group in groups.items():
                sample_count = len(group)
                mean_views = round(sum(r.views for r in group) / sample_count, 1)
                mean_likes = round(sum(r.likes for r in group) / sample_count, 1)

                rets = [r.avg_view_percentage for r in group if r.avg_view_percentage > 0]
                mean_ret = round(sum(rets) / len(rets), 1) if rets else 0.0

                swipes = [r.viewed_vs_swiped_pct for r in group if r.viewed_vs_swiped_pct > 0]
                mean_viewed = round(sum(swipes) / len(swipes), 1) if swipes else 0.0

                mean_svs = round(sum(r.svs_score for r in group) / sample_count, 1)

                items.append(
                    StyleBreakdownItem(
                        key=key,
                        dimension=dim,
                        sample_count=sample_count,
                        mean_views=mean_views,
                        mean_likes=mean_likes,
                        mean_retention_pct=mean_ret,
                        mean_viewed_pct=mean_viewed,
                        svs_score=mean_svs,
                    )
                )

            # Order by highest SVS score first
            items.sort(key=lambda x: x.svs_score, reverse=True)
            breakdowns[dim] = items

        return breakdowns

    # -------------------------------------------------------------------------
    # Bayesian Winning Recipe Derivation
    # -------------------------------------------------------------------------

    def get_winning_recipe(self) -> WinningRecipe:
        """Derive the top-performing style recipe using Bayesian confidence shrinkage."""
        pairs = self.get_records_with_jobs()
        if not pairs:
            return WinningRecipe(
                layout_mode="split_50_50",
                style_preset="hormozi",
                background_category="subway",
                color_grading="none",
                confidence="default",
                sample_size=0,
                avg_retention_pct=0.0,
                avg_viewed_pct=0.0,
                avg_views=0.0,
                uplift_summary="Default high-retention baseline",
                rationale="Split 50/50 with Hormozi captions and Subway Surfers delivers the highest initial hook rate based on benchmark averages.",
                source_job_id=None,
            )

        channel_svs_list = [r.svs_score for r, _ in pairs]
        channel_mean_svs = sum(channel_svs_list) / len(channel_svs_list)
        channel_mean_views = sum(r.views for r, _ in pairs) / len(pairs)
        all_rets = [r.avg_view_percentage for r, _ in pairs if r.avg_view_percentage > 0]
        channel_mean_ret = (sum(all_rets) / len(all_rets)) if all_rets else 70.0

        # Group by composite tuple: (layout_mode, style_preset, background_category, color_grading)
        combos: dict[tuple[str, str, str, str], list[tuple[VideoAnalyticsRecord, Job | None]]] = (
            defaultdict(list)
        )
        for rec, job in pairs:
            attrs = self.extract_style_attributes(job)
            combo_key = (
                attrs["layout_mode"],
                attrs["style_preset"],
                attrs["background_category"],
                attrs["color_grading"],
            )
            combos[combo_key].append((rec, job))

        best_combo: tuple[str, str, str, str] | None = None
        best_bayes_svs = -1.0
        best_combo_pairs: list[tuple[VideoAnalyticsRecord, Job | None]] = []

        # Bayesian shrinkage: prior weight m = 2.0 pulls low-sample combinations toward channel mean
        m = 2.0
        for combo_key, c_pairs in combos.items():
            n = len(c_pairs)
            observed_mean_svs = sum(r.svs_score for r, _ in c_pairs) / n
            bayes_svs = (n / (n + m)) * observed_mean_svs + (m / (n + m)) * channel_mean_svs
            if bayes_svs > best_bayes_svs:
                best_bayes_svs = bayes_svs
                best_combo = combo_key
                best_combo_pairs = c_pairs

        if not best_combo:
            best_combo = ("split_50_50", "hormozi", "subway", "none")
            best_bayes_svs = channel_mean_svs

        layout_mode, style_preset, background_category, color_grading = best_combo
        n = len(best_combo_pairs)

        # Confidence level
        if n >= 8:
            confidence = "high"
        elif n >= 3:
            confidence = "medium"
        elif n >= 1:
            confidence = "preliminary"
        else:
            confidence = "default"

        combo_views = [r.views for r, _ in best_combo_pairs]
        avg_views = round(sum(combo_views) / n, 1) if n > 0 else 0.0

        combo_rets = [
            r.avg_view_percentage for r, _ in best_combo_pairs if r.avg_view_percentage > 0
        ]
        avg_ret = round(sum(combo_rets) / len(combo_rets), 1) if combo_rets else 0.0

        combo_swipes = [
            r.viewed_vs_swiped_pct for r, _ in best_combo_pairs if r.viewed_vs_swiped_pct > 0
        ]
        avg_swipe = round(sum(combo_swipes) / len(combo_swipes), 1) if combo_swipes else 0.0

        best_pair = (
            max(best_combo_pairs, key=lambda p: p[0].svs_score) if best_combo_pairs else None
        )
        source_job_id = None
        if best_pair:
            rec, job = best_pair
            source_job_id = job.id if job else (rec.job_id or None)

        if channel_mean_ret > 0 and avg_ret > channel_mean_ret:
            uplift_diff = round(((avg_ret - channel_mean_ret) / channel_mean_ret) * 100.0, 1)
            uplift_summary = f"+{uplift_diff}% retention vs channel avg"
        elif channel_mean_views > 0 and avg_views > channel_mean_views:
            uplift_diff = round(((avg_views - channel_mean_views) / channel_mean_views) * 100.0, 1)
            uplift_summary = f"+{uplift_diff}% views vs channel avg"
        else:
            uplift_summary = f"SVS {round(best_bayes_svs, 1)}/100 (Top performing recipe)"

        rationale = (
            f"Winning recipe combining '{layout_mode}' layout, '{style_preset}' subtitles, and '{background_category}' "
            f"background scored {round(best_bayes_svs, 1)} SVS across {n} sample(s)."
        )

        return WinningRecipe(
            layout_mode=layout_mode,
            style_preset=style_preset,
            background_category=background_category,
            color_grading=color_grading,
            confidence=confidence,
            sample_size=n,
            avg_retention_pct=avg_ret,
            avg_viewed_pct=avg_swipe,
            avg_views=avg_views,
            uplift_summary=uplift_summary,
            rationale=rationale,
            source_job_id=source_job_id,
        )

    # -------------------------------------------------------------------------
    # 1-Click Clone Job Style
    # -------------------------------------------------------------------------

    def clone_job_style(self, job_id: str) -> dict[str, Any]:
        """Extract and clone the complete rendering and styling parameters of an existing Job."""
        job = self.state_manager.get_job(job_id)
        if not job:
            return {
                "layout_mode": "split_50_50",
                "style_preset": "hormozi",
                "background_category": "subway",
                "color_grading": "none",
                "watermark_text": None,
                "watermark_position": "top_right",
                "progress_bar_position": "seam",
                "text_transform": "uppercase",
                "subtitle_position": "center",
                "hook_banner_style": "neon_box",
                "enable_emojis": True,
            }

        bg = "subway"
        if (
            job.selected_clips
            and len(job.selected_clips) > 0
            and job.selected_clips[0].background_style
        ):
            bg = job.selected_clips[0].background_style
        elif job.edl and getattr(job.edl, "background_style_recommendation", None):
            bg = job.edl.background_style_recommendation
        elif hasattr(job, "background_category") and job.background_category:
            bg = job.background_category

        return {
            "layout_mode": getattr(job, "layout_mode", "split_50_50"),
            "style_preset": getattr(job, "style_preset", "hormozi"),
            "background_category": bg,
            "color_grading": getattr(job, "color_grading", "none"),
            "watermark_text": getattr(job, "watermark_text", None),
            "watermark_position": getattr(job, "watermark_position", "top_right"),
            "progress_bar_position": getattr(job, "progress_bar_position", "seam"),
            "text_transform": getattr(job, "text_transform", "uppercase"),
            "subtitle_position": getattr(job, "subtitle_position", "center"),
            "hook_banner_style": getattr(job, "hook_banner_style", "neon_box"),
            "enable_emojis": getattr(job, "enable_emojis", True),
        }


__all__ = ["PerformanceAttributionEngine"]
