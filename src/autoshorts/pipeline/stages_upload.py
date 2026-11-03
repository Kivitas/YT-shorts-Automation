"""Upload and scheduling stages for ShortsPipeline."""

from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from autoshorts.core.exceptions import QuotaExhaustedError, UploadError
from autoshorts.core.models import (
    Job,
    JobStatus,
    UploadResult,
    VideoMetadata,
    resolve_verification,
)
from autoshorts.media.ffmpeg_tools import check_delivery
from autoshorts.pipeline.common import _upload_succeeded
from autoshorts.uploader.description_builder import (
    MAX_TAGS_SHORTS,
    build_short_description,
    build_short_title,
    build_viral_hashtags,
)
from autoshorts.uploader.errors import QuotaExceededError
from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader
from autoshorts.uploader.youtube_api import YouTubeApiUploader

logger = logging.getLogger("autoshorts.pipeline")


# check.py rows that block an upload. Everything else (duration, fps, loudness, file size,
# resolution shortfall) is logged as a warning and never blocks.
HARD_DELIVERY_CHECKS = frozenset({"aspect", "video codec"})


def _hard_delivery_failures(delivery: dict[str, Any]) -> list[str]:
    """Return the hard failures (wrong frame shape, missing or wrong video codec) in a QC result."""
    rows = delivery.get("raw", {}).get("checks") if isinstance(delivery.get("raw"), dict) else None
    hard: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("check", ""))
        if row.get("status") == "FAIL" and name in HARD_DELIVERY_CHECKS:
            hard.append(f"{name}: {row.get('value')} (expected {row.get('expected')})")
    return hard


class UploadMixin:
    """Stage methods for upload and scheduling."""

    def _delivery_gate(self: Any, job: Job) -> None:
        """QC the rendered file before it is uploaded.

        Blocks (raises UploadError) only on hard failures: wrong aspect or a missing/wrong video
        codec. Other check.py findings are logged as warnings. The result is stored on the job
        under diagnostics["delivery_qc"]. A QC tool that cannot run never blocks the upload.
        """
        path = getattr(job, "rendered_video_path", None)
        if not path:
            return
        delivery = check_delivery(Path(path))
        hard = _hard_delivery_failures(delivery)
        issues = list(delivery.get("issues") or [])
        job.record_diagnostic(
            "delivery_qc",
            ok=bool(delivery.get("ok")),
            hard_failures=hard,
            issues=issues,
        )
        soft = [i for i in issues if i.split(":", 1)[0] not in HARD_DELIVERY_CHECKS]
        if soft:
            logger.warning("Job %s: delivery QC warnings: %s", job.id, "; ".join(soft))
        if hard:
            raise UploadError(f"Job {job.id}: delivery QC blocked upload: {'; '.join(hard)}")

    def _prepare_upload_metadata(self: Any, job: Job) -> tuple[str, str]:
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
            hashtags=build_viral_hashtags(
                meta.title,
                sel.hook_text or sel.title or "",
                max_tags=MAX_TAGS_SHORTS,
            ),
            template=self.config.upload.description_template,
        )

        return title, desc

    def _run_immediate_upload(self: Any, job: Job) -> UploadResult:
        import autoshorts.pipeline as pipeline_mod

        title, desc = self._prepare_upload_metadata(job)
        try:
            self._delivery_gate(job)
        except UploadError as qc_err:
            self._mark_failed(job, "upload", qc_err)
            raise
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
            work_thumb = pipeline_mod.get_work_dir() / job.id / f"thumb_{job.id}.jpg"
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
            except Exception as verify_err:
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
            if not result.verified:
                logger.warning(
                    "Job %s: Skipping intermediate cleanup because the upload is not "
                    "confirmed (verification=%s).",
                    job.id,
                    result.verification_kind,
                )
            elif self.cleaner and getattr(getattr(self.config, "cleanup", None), "enabled", True):
                try:
                    self.cleaner.cleanup_job(
                        job.id,
                        verified=True,
                        upload_result=result,
                        keep_master_mp4=False,
                    )
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

    def _run_schedule_upload(self: Any, job: Job) -> None:
        title, desc = self._prepare_upload_metadata(job)
        try:
            self._delivery_gate(job)
        except UploadError as qc_err:
            self._mark_failed(job, "upload", qc_err)
            raise
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
