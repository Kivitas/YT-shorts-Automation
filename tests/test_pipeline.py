"""Unit and end-to-end integration tests for AutoShorts pipeline orchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from autoshorts.cleanup.safety_cleaner import SafetyCleaner
from autoshorts.core.exceptions import DownloadError
from autoshorts.core.models import (
    ClipCandidate,
    ClipSelection,
    EditDecisionList,
    Job,
    JobStatus,
    UploadResult,
    VideoMetadata,
)
from autoshorts.core.state_manager import StateManager
from autoshorts.pipeline import ShortsPipeline
from autoshorts.quota.quota_tracker import QuotaTracker
from autoshorts.transcript.fetcher import NO_CAPTIONS, TranscriptSegment


@pytest.fixture
def test_env(tmp_path: Path):
    """Set up temporary directories and isolated SQLite StateManager."""
    db_path = tmp_path / "test_state.db"
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    state_mgr = StateManager(db_path=db_path)
    quota_tracker = QuotaTracker(state_manager=state_mgr)

    return {
        "state_mgr": state_mgr,
        "quota_tracker": quota_tracker,
        "work_dir": work_dir,
        "output_dir": output_dir,
    }


def test_pipeline_e2e_mocked_success(test_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch):
    """Verify that a job traverses from raw URL to rendered output, upload, and cleanup."""
    state_mgr = test_env["state_mgr"]
    quota_tracker = test_env["quota_tracker"]
    work_dir = test_env["work_dir"]
    output_dir = test_env["output_dir"]

    # Redirect paths to tmp_path
    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: output_dir)
    monkeypatch.setattr("autoshorts.cleanup.safety_cleaner.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.cleanup.safety_cleaner.get_output_dir", lambda: output_dir)

    # 1. Mock Downloader
    mock_downloader = MagicMock()
    mock_downloader.fetch_metadata.return_value = VideoMetadata(
        title="Epic Tech Debate",
        channel="Future Tech",
        channel_url="https://youtube.com/@futuretech",
        video_url="https://youtube.com/watch?v=1234567890a",
        duration=300.0,
        video_id="1234567890a",
    )

    def fake_download_section(url, start_sec, end_sec, output_path):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x00\x00 fake mp4 video")
        return p

    mock_downloader.download_section.side_effect = fake_download_section

    # 2. Mock TranscriptFetcher
    mock_transcript_fetcher = MagicMock()
    mock_transcript_fetcher.fetch_or_signal.return_value = (
        [
            TranscriptSegment(
                start=10.0,
                duration=15.0,
                text="This revolutionary discovery will transform everything we know about computing.",
            ),
            TranscriptSegment(
                start=25.0,
                duration=20.0,
                text="The implications are absolutely massive and nobody saw it coming.",
            ),
        ],
        None,
    )

    # 3. Mock Transcriber (Whisper)
    mock_transcriber = MagicMock()
    mock_transcriber.align_words.return_value = [
        {"word": "This", "start": 0.0, "end": 0.5, "probability": 0.99},
        {"word": "revolutionary", "start": 0.5, "end": 1.2, "probability": 0.99},
        {"word": "discovery", "start": 1.2, "end": 1.8, "probability": 0.99},
    ]

    # 4. Mock Decider
    mock_decider = MagicMock()
    cand = ClipCandidate(
        start=10.0,
        end=45.0,
        text="This revolutionary discovery will transform everything.",
        score=0.92,
    )
    mock_decider.score_candidates.return_value = [cand]
    mock_decider.select_clips.return_value = [
        ClipSelection(
            candidate=cand,
            clip_index=0,
            target_duration=35.0,
            background_style="minecraft",
            hook_text="REVOLUTIONARY DISCOVERY",
            title="Mindblowing Tech Discovery #Shorts",
        )
    ]

    # 5. Mock EditPlanner
    mock_edit_planner = MagicMock()
    mock_edit_planner.generate_edl.return_value = EditDecisionList(
        hook_in_sec=0.5,
        hook_out_sec=34.5,
        recommended_title="Mindblowing Discovery #Shorts",
        recommended_hook="REVOLUTIONARY DISCOVERY",
    )

    # 6. Mock Compositor
    mock_compositor = MagicMock()

    def fake_compose(
        job_id, source_clip, edl, background_path, subtitles_path, output_path, **kwargs
    ):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x00\x00 fake rendered master short")
        return p

    def fake_preview(source_clip, output_path, **kwargs):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x00\x00 fake preview short")
        return p

    mock_compositor.compose.side_effect = fake_compose
    mock_compositor.generate_preview.side_effect = fake_preview

    # 7. Mock Uploader
    mock_uploader = MagicMock()
    mock_uploader.upload.return_value = UploadResult(
        video_id="short_xyz_99",
        url="https://youtube.com/shorts/short_xyz_99",
        status="uploaded",
        verified=True,
        quota_spent=1,
    )
    mock_uploader.verify.return_value = True

    # 8. Cleaner
    cleaner = SafetyCleaner(
        work_dir=work_dir,
        output_dir=output_dir,
        allowed_roots=[work_dir, output_dir],
        state_manager=state_mgr,
        permanent=True,
    )

    # Build Pipeline
    pipeline = ShortsPipeline(
        state_manager=state_mgr,
        downloader=mock_downloader,
        transcript_fetcher=mock_transcript_fetcher,
        transcriber=mock_transcriber,
        decider=mock_decider,
        edit_planner=mock_edit_planner,
        compositor=mock_compositor,
        uploader=mock_uploader,
        cleaner=cleaner,
        quota_tracker=quota_tracker,
    )

    # Submit link
    jobs = pipeline.submit_links("https://www.youtube.com/watch?v=1234567890a : Focus on computing")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.status == JobStatus.QUEUED

    # Execute end-to-end with immediate upload
    processed_job = pipeline.process_job(job.id, immediate_upload=True)

    # Verify final status transitions
    assert processed_job.status in (JobStatus.COMPLETED, JobStatus.CLEANED)
    assert processed_job.upload_result is not None
    assert processed_job.upload_result.video_id == "short_xyz_99"
    assert processed_job.upload_result.verified is True

    # Check stage history in database
    stages = state_mgr.get_job_stages(job.id)
    stage_names = [s["stage_name"] for s in stages]
    assert "metadata_fetch" in stage_names
    assert "transcript_fetch" in stage_names
    assert "window_slicing" in stage_names
    assert "semif_evaluation" in stage_names
    assert "precision_download" in stage_names
    assert "subtitles_generation" in stage_names
    assert "compositing" in stage_names
    assert "immediate_upload" in stage_names

    # Check that all stages completed successfully
    for s in stages:
        assert s["status"] == "COMPLETED"


def test_pipeline_whisper_fallback_when_no_captions(
    test_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    """Verify that when captions are missing, pipeline falls back to FasterWhisper audio transcription."""
    state_mgr = test_env["state_mgr"]
    work_dir = test_env["work_dir"]
    output_dir = test_env["output_dir"]

    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: output_dir)

    mock_downloader = MagicMock()
    mock_downloader.fetch_metadata.return_value = VideoMetadata(
        title="No Caption Video",
        channel="Silent Creator",
        channel_url="https://youtube.com/@silent",
        video_url="https://youtube.com/watch?v=no_captions_1",
        duration=180.0,
    )

    def fake_audio_only(url, output_path):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"audio stream")
        return p

    mock_downloader.download_audio_only.side_effect = fake_audio_only

    mock_transcript_fetcher = MagicMock()
    # Simulate missing captions signal
    mock_transcript_fetcher.fetch_or_signal.return_value = (None, NO_CAPTIONS)

    mock_transcriber = MagicMock()
    mock_transcriber.transcribe.return_value = [
        {
            "start": 5.0,
            "end": 40.0,
            "text": "This audio was extracted and transcribed purely via local Whisper fallback.",
        }
    ]

    pipeline = ShortsPipeline(
        state_manager=state_mgr,
        downloader=mock_downloader,
        transcript_fetcher=mock_transcript_fetcher,
        transcriber=mock_transcriber,
    )

    job = Job(id="job_whisper_fallback", source_url="https://youtube.com/watch?v=no_captions_1")
    state_mgr.save_job(job)

    # Run metadata & transcript stage
    pipeline._run_metadata_stage(job)
    assert job.status == JobStatus.METADATA_FETCHED

    pipeline._run_transcript_stage(job)
    assert job.status == JobStatus.TRANSCRIBED

    mock_downloader.download_audio_only.assert_called_once()
    mock_transcriber.transcribe.assert_called_once()

    cached_segments = pipeline._load_transcript_segments(job)
    assert len(cached_segments) == 1
    assert "local Whisper fallback" in cached_segments[0]["text"]


def test_pipeline_schedule_mode_instead_of_immediate(
    test_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    """Verify that auto_upload=True enqueues job into SpreadScheduler."""
    state_mgr = test_env["state_mgr"]
    quota_tracker = test_env["quota_tracker"]
    work_dir = test_env["work_dir"]
    output_dir = test_env["output_dir"]

    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: output_dir)

    mock_downloader = MagicMock()
    mock_downloader.fetch_metadata.return_value = VideoMetadata(
        title="Schedule Video",
        channel="Host",
        channel_url="https://youtube.com/@host",
        video_url="https://youtube.com/watch?v=sched1",
        duration=120.0,
    )

    def fake_download_section(url, start_sec, end_sec, output_path):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"cut video")
        return p

    mock_downloader.download_section.side_effect = fake_download_section

    mock_transcript_fetcher = MagicMock()
    mock_transcript_fetcher.fetch_or_signal.return_value = (
        [
            TranscriptSegment(
                start=0.0, duration=40.0, text="Scheduled video excerpt transcript text."
            )
        ],
        None,
    )

    mock_compositor = MagicMock()

    def fake_compose(
        job_id, source_clip, edl, background_path, subtitles_path, output_path, **kwargs
    ):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"master short")
        return p

    mock_compositor.compose.side_effect = fake_compose
    mock_compositor.generate_preview.return_value = output_dir / "preview.mp4"

    mock_uploader = MagicMock()

    pipeline = ShortsPipeline(
        state_manager=state_mgr,
        downloader=mock_downloader,
        transcript_fetcher=mock_transcript_fetcher,
        compositor=mock_compositor,
        uploader=mock_uploader,
        quota_tracker=quota_tracker,
    )

    job = Job(id="job_sched_test", source_url="https://youtube.com/watch?v=sched1")
    state_mgr.save_job(job)

    # Process with auto_upload=True (enqueues to scheduler)
    finished = pipeline.process_job(job, auto_upload=True, immediate_upload=False)

    assert finished.status == JobStatus.SCHEDULED
    assert finished.scheduled_time is not None

    pending_slots = state_mgr.get_pending_scheduled_slots()
    assert any(s["job_id"] == "job_sched_test" for s in pending_slots)
    # mock uploader shouldn't have been called directly yet
    assert mock_uploader.upload.call_count == 0


def test_pipeline_manual_review_mode(test_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch):
    """Verify that when neither immediate nor auto_upload is set, job stops at READY_FOR_REVIEW."""
    state_mgr = test_env["state_mgr"]
    work_dir = test_env["work_dir"]
    output_dir = test_env["output_dir"]

    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: output_dir)

    mock_downloader = MagicMock()
    mock_downloader.fetch_metadata.return_value = VideoMetadata(
        title="Review Mode Video",
        channel="Host",
        channel_url="https://youtube.com/@host",
        video_url="https://youtube.com/watch?v=review1",
        duration=100.0,
    )
    mock_downloader.download_section.return_value = work_dir / "cut.mp4"

    mock_transcript_fetcher = MagicMock()
    mock_transcript_fetcher.fetch_or_signal.return_value = (
        [TranscriptSegment(start=0.0, duration=35.0, text="Review mode sample text content here.")],
        None,
    )

    mock_compositor = MagicMock()
    mock_compositor.compose.return_value = output_dir / "master.mp4"
    mock_compositor.generate_preview.return_value = output_dir / "preview.mp4"

    pipeline = ShortsPipeline(
        state_manager=state_mgr,
        downloader=mock_downloader,
        transcript_fetcher=mock_transcript_fetcher,
        compositor=mock_compositor,
    )

    job = Job(id="job_review_test", source_url="https://youtube.com/watch?v=review1")
    state_mgr.save_job(job)

    # Process without upload
    result = pipeline.process_job(job, auto_upload=False, immediate_upload=False)
    assert result.status == JobStatus.READY_FOR_REVIEW


def test_pipeline_failure_and_resume(test_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch):
    """Verify that failure transitions to FAILED and can be resumed/retried."""
    state_mgr = test_env["state_mgr"]
    work_dir = test_env["work_dir"]
    output_dir = test_env["output_dir"]

    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: output_dir)

    mock_downloader = MagicMock()
    mock_downloader.fetch_metadata.return_value = VideoMetadata(
        title="Fail Video",
        channel="Host",
        channel_url="https://youtube.com/@host",
        video_url="https://youtube.com/watch?v=fail1",
        duration=120.0,
    )
    # First attempt: download_section raises DownloadError
    mock_downloader.download_section.side_effect = DownloadError("Simulated network timeout")

    mock_transcript_fetcher = MagicMock()
    mock_transcript_fetcher.fetch_or_signal.return_value = (
        [TranscriptSegment(start=0.0, duration=35.0, text="Text for failure test.")],
        None,
    )

    mock_compositor = MagicMock()

    pipeline = ShortsPipeline(
        state_manager=state_mgr,
        downloader=mock_downloader,
        transcript_fetcher=mock_transcript_fetcher,
        compositor=mock_compositor,
    )

    job = Job(id="job_fail_test", source_url="https://youtube.com/watch?v=fail1")
    state_mgr.save_job(job)

    with pytest.raises(DownloadError):
        pipeline.process_job(job)

    failed_job = state_mgr.get_job("job_fail_test")
    assert failed_job is not None
    assert failed_job.status == JobStatus.FAILED
    assert "Simulated network timeout" in (failed_job.error_message or "")

    # Now fix the download_section and retry
    def fake_download_section_ok(url, start_sec, end_sec, output_path):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"cut video")
        return p

    mock_downloader.download_section.side_effect = fake_download_section_ok
    mock_compositor.compose.return_value = output_dir / "master.mp4"

    resumed_job = pipeline.retry_job("job_fail_test")
    assert resumed_job.status == JobStatus.READY_FOR_REVIEW
    assert resumed_job.error_message is None


def test_pipeline_batch_processing(test_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch):
    """Verify batch processing processes multiple links sequentially."""
    state_mgr = test_env["state_mgr"]
    work_dir = test_env["work_dir"]
    output_dir = test_env["output_dir"]

    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: output_dir)

    mock_downloader = MagicMock()
    mock_downloader.fetch_metadata.side_effect = lambda url: VideoMetadata(
        title=f"Video for {url}",
        channel="Batch Creator",
        channel_url="https://youtube.com/@batch",
        video_url=url,
        duration=100.0,
    )

    def fake_dl(url, start_sec, end_sec, output_path):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"cut")
        return p

    mock_downloader.download_section.side_effect = fake_dl

    mock_transcript_fetcher = MagicMock()
    mock_transcript_fetcher.fetch_or_signal.return_value = (
        [TranscriptSegment(start=0.0, duration=35.0, text="Batch clip excerpt text.")],
        None,
    )

    mock_compositor = MagicMock()
    mock_compositor.compose.side_effect = lambda job_id, **kw: output_dir / f"{job_id}_master.mp4"

    pipeline = ShortsPipeline(
        state_manager=state_mgr,
        downloader=mock_downloader,
        transcript_fetcher=mock_transcript_fetcher,
        compositor=mock_compositor,
    )

    links = [
        "https://www.youtube.com/watch?v=batch111111",
        "https://www.youtube.com/watch?v=batch222222",
    ]

    results = pipeline.process_batch(links)
    assert len(results) == 2
    assert results[0].status == JobStatus.READY_FOR_REVIEW
    assert results[1].status == JobStatus.READY_FOR_REVIEW


def test_pipeline_concurrency_throttling(test_env: dict[str, Any]):
    """Verify pipeline initializes with bounded semaphore for rendering concurrency."""
    pipeline = ShortsPipeline(concurrency=1)
    assert pipeline.concurrency == 1
    # Check that acquiring the semaphore decrements it
    assert pipeline._render_lock.acquire(blocking=False) is True
    # Second acquisition without release must be blocked
    assert pipeline._render_lock.acquire(blocking=False) is False
    pipeline._render_lock.release()


def test_pipeline_startup_reconciles_stranded_jobs_and_wires_cleaner(test_env: dict[str, Any]):
    """Verify that ShortsPipeline initialization reconciles stranded jobs and wires cleaner into scheduler."""
    state_mgr = test_env["state_mgr"]

    # Pre-populate a stranded job from an interrupted previous run
    stranded = Job(
        id="stranded_compositing_job",
        source_url="https://youtube.com/watch?v=stranded1",
        status=JobStatus.COMPOSITING,
    )
    state_mgr.save_job(stranded)

    # Initialize pipeline
    pipeline = ShortsPipeline(state_manager=state_mgr)

    # 1. Cleaner wired into scheduler
    assert pipeline.scheduler.cleaner is not None
    assert pipeline.scheduler.cleaner is pipeline.cleaner

    # 2. Stranded job was transitioned to FAILED
    reloaded = state_mgr.get_job("stranded_compositing_job")
    assert reloaded is not None
    assert reloaded.status == JobStatus.FAILED
    assert reloaded.error_message == "Interrupted by application restart; ready for retry"


def test_pipeline_instantiates_studio_uploader_when_configured(test_env: dict[str, Any]):
    """Verify ShortsPipeline instantiates PlaywrightStudioUploader when upload.mode is studio."""
    from autoshorts.core.config import Settings
    from autoshorts.uploader.studio_playwright import PlaywrightStudioUploader

    state_mgr = test_env["state_mgr"]
    config = Settings()
    config.upload.mode = "studio"

    pipeline = ShortsPipeline(config=config, state_manager=state_mgr)
    assert isinstance(pipeline.uploader, PlaywrightStudioUploader)
    assert pipeline.scheduler.uploader is pipeline.uploader
