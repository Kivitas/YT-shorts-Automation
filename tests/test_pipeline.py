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


def test_pipeline_rerender_job_fast_recomposite(
    test_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    """Verify ShortsPipeline.rerender_job applies overrides and re-runs subtitles and compositing only."""
    import json

    state_mgr = test_env["state_mgr"]
    work_dir = test_env["work_dir"]
    output_dir = test_env["output_dir"]

    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: output_dir)

    job_id = "job_rerender_test"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True, exist_ok=True)
    raw_video = job_work / f"{job_id}_raw.mp4"
    raw_video.write_bytes(b"\x00\x00\x00 fake raw video bytes")

    # Write aligned words cache
    words_data = [
        {"word": "ORIGINAL", "start": 0.0, "end": 0.5, "probability": 1.0},
        {"word": "TRANSCRIPT", "start": 0.5, "end": 1.0, "probability": 1.0},
    ]
    with open(job_work / "aligned_words.json", "w", encoding="utf-8") as f:
        json.dump(words_data, f)

    cand = ClipCandidate(start=0.0, end=30.0, text="ORIGINAL TRANSCRIPT", score=0.9)
    sel = ClipSelection(
        candidate=cand,
        clip_index=0,
        target_duration=30.0,
        background_style="minecraft",
        hook_text="OLD HOOK",
        title="Old Title #Shorts",
    )
    edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=30.0,
        recommended_title="Old Title",
        recommended_hook="OLD HOOK",
    )

    job = Job(
        id=job_id,
        source_url="https://youtube.com/watch?v=rerender123",
        status=JobStatus.READY_FOR_REVIEW,
        raw_video_path=str(raw_video),
        candidate_windows=[cand],
        selected_clips=[sel],
        edl=edl,
    )
    state_mgr.save_job(job)

    mock_compositor = MagicMock()

    def fake_compose(
        job_id, source_clip, edl, background_path, subtitles_path, output_path, **kwargs
    ):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x00\x00 fake re-rendered master")
        return p

    mock_compositor.compose.side_effect = fake_compose

    mock_transcriber = MagicMock()

    pipeline = ShortsPipeline(
        state_manager=state_mgr,
        compositor=mock_compositor,
        transcriber=mock_transcriber,
    )

    rerendered = pipeline.rerender_job(
        job_id=job_id,
        style_preset="minimal",
        layout_mode="full_blur_bg",
        hook_text="BRAND NEW HOOK",
        clip_title="Brand New Title #Shorts",
        background_category="subway",
    )

    assert rerendered.status == JobStatus.READY_FOR_REVIEW
    assert rerendered.style_preset == "minimal"
    assert rerendered.selected_clips[0].hook_text == "BRAND NEW HOOK"
    assert rerendered.selected_clips[0].title == "Brand New Title #Shorts"
    assert rerendered.selected_clips[0].background_style == "subway"
    assert rerendered.edl.recommended_hook == "BRAND NEW HOOK"
    assert rerendered.edl.recommended_title == "Brand New Title #Shorts"
    assert Path(rerendered.rendered_video_path).is_file()

    # Transcriber align_words should NOT have been called because cached aligned_words was used
    mock_transcriber.align_words.assert_not_called()
    mock_compositor.compose.assert_called_once()
    compose_kwargs = mock_compositor.compose.call_args[1]
    assert compose_kwargs["layout_mode"] == "full_blur_bg"


def test_pipeline_subtitles_caching_and_customization_forwarding(
    test_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    """Verify _run_subtitles_stage saves aligned_words.json and re-reads user transcript modifications."""
    import json

    state_mgr = test_env["state_mgr"]
    work_dir = test_env["work_dir"]
    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)

    job_id = "job_sub_cache_test"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True, exist_ok=True)
    raw_video = job_work / f"{job_id}_raw.mp4"
    raw_video.write_bytes(b"\x00\x00\x00 dummy")

    cand = ClipCandidate(start=0.0, end=10.0, text="first word second word", score=0.8)
    sel = ClipSelection(candidate=cand, clip_index=0)
    job = Job(
        id=job_id,
        source_url="https://youtube.com/watch?v=cachetest",
        status=JobStatus.DOWNLOADING_SECTION,
        raw_video_path=str(raw_video),
        selected_clips=[sel],
        edl=EditDecisionList(hook_in_sec=0.0, hook_out_sec=10.0),
    )
    state_mgr.save_job(job)

    mock_transcriber = MagicMock()
    mock_transcriber.align_words.return_value = [
        {"word": "first", "start": 0.0, "end": 0.5, "probability": 1.0},
        {"word": "word", "start": 0.5, "end": 1.0, "probability": 1.0},
    ]

    pipeline = ShortsPipeline(state_manager=state_mgr, transcriber=mock_transcriber)
    pipeline._run_subtitles_stage(job)

    # 1. aligned_words.json was generated and cached
    cache_path = job_work / "aligned_words.json"
    assert cache_path.is_file()

    # 2. Simulate user editing the word-level transcript
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump([{"word": "MODIFIED_CAPTION", "start": 0.0, "end": 1.0, "probability": 1.0}], f)

    # 3. Re-run subtitles stage with use_cached_words=True
    pipeline._run_subtitles_stage(job, use_cached_words=True)
    ass_content = Path(job.subtitles_path).read_text(encoding="utf-8")
    assert "MODIFIED_CAPTION" in ass_content


def test_real_ffmpeg_processing_editing_rerender_and_upload(
    test_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    """Real FFmpeg integration test: render video, edit subtitles + layout, re-render, and upload."""
    import json
    import subprocess

    import imageio_ffmpeg

    from autoshorts.media.compositor import FFmpegCompositor
    from autoshorts.uploader.local_exporter import LocalExportUploader

    state_mgr = test_env["state_mgr"]
    work_dir = test_env["work_dir"]
    output_dir = test_env["output_dir"]
    export_dir = output_dir / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: output_dir)
    monkeypatch.setattr("autoshorts.core.paths.get_output_dir", lambda: output_dir)
    monkeypatch.setattr("autoshorts.core.paths.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.cleanup.safety_cleaner.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.cleanup.safety_cleaner.get_output_dir", lambda: output_dir)

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    assert ffmpeg_exe and Path(ffmpeg_exe).is_file()

    job_id = "job_real_render_001"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True, exist_ok=True)

    # 1. Generate real 3-second source video (1280x720 25fps) with sine audio
    raw_video = job_work / f"{job_id}_raw.mp4"
    gen_cmd = [
        ffmpeg_exe,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=1280x720:rate=25",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=44100",
        "-t",
        "3.0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(raw_video),
    ]
    subprocess.run(gen_cmd, check=True, capture_output=True)
    assert raw_video.is_file() and raw_video.stat().st_size > 0

    # 2. Generate a real 3-second background video (1080x1920 25fps) for split_50_50
    bg_video = work_dir / "test_bg.mp4"
    bg_cmd = [
        ffmpeg_exe,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=1080x1920:rate=25",
        "-t",
        "3.0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(bg_video),
    ]
    subprocess.run(bg_cmd, check=True, capture_output=True)
    assert bg_video.is_file() and bg_video.stat().st_size > 0

    monkeypatch.setattr("autoshorts.pipeline.pick_background_asset", lambda **kw: (bg_video, 0.0))

    # 3. Create initial Job in DOWNLOADING_SECTION state
    cand = ClipCandidate(start=0.0, end=3.0, text="real ffmpeg test initial subtitles", score=0.85)
    sel = ClipSelection(
        candidate=cand, clip_index=0, hook_text="Initial Hook", title="Initial Title"
    )
    job = Job(
        id=job_id,
        source_url="https://youtube.com/watch?v=realffmpegtest",
        status=JobStatus.DOWNLOADING_SECTION,
        raw_video_path=str(raw_video),
        selected_clips=[sel],
        edl=EditDecisionList(hook_in_sec=0.0, hook_out_sec=3.0),
        layout_mode="split_50_50",
        style_preset="neon",
    )
    state_mgr.save_job(job)

    # 4. Initialize pipeline with real FFmpegCompositor and LocalExportUploader
    uploader = LocalExportUploader(export_dir=export_dir)
    compositor = FFmpegCompositor()
    pipeline = ShortsPipeline(
        state_manager=state_mgr,
        compositor=compositor,
        uploader=uploader,
    )

    # 5. Execute Stage 7: Subtitles generation
    pipeline._run_subtitles_stage(job)
    assert job.status == JobStatus.SUBTITLES_GENERATED
    assert job.subtitles_path is not None
    assert Path(job.subtitles_path).is_file()
    aligned_cache = job_work / "aligned_words.json"
    assert aligned_cache.is_file()

    # 6. Execute Stage 8: Real FFmpeg compositing (split_50_50)
    pipeline._run_compositing_stage(job)
    assert job.status == JobStatus.READY_FOR_REVIEW
    assert job.rendered_video_path is not None
    master_path = Path(job.rendered_video_path)
    assert master_path.is_file() and master_path.stat().st_size > 0

    # 7. Simulate User Editing Transcript & Changing Settings in Modal:
    # Modify aligned_words.json, change layout_mode to full_blur_bg, change style_preset to hormozi
    edited_words = [
        {"word": "HORMOZI_HOOK", "start": 0.0, "end": 1.5, "probability": 1.0},
        {"word": "REBURN_SUCCESS", "start": 1.5, "end": 3.0, "probability": 1.0},
    ]
    with open(aligned_cache, "w", encoding="utf-8") as f:
        json.dump(edited_words, f)

    # 8. Fast Re-Render (⚡ Fast Re-Burn)
    rerendered_job = pipeline.rerender_job(
        job_id,
        style_preset="hormozi",
        layout_mode="full_blur_bg",
        hook_text="Edited Fast Hook",
    )
    assert rerendered_job.status == JobStatus.READY_FOR_REVIEW
    assert rerendered_job.style_preset == "hormozi"
    assert rerendered_job.layout_mode == "full_blur_bg"
    new_ass = Path(rerendered_job.subtitles_path).read_text(encoding="utf-8")
    assert "HORMOZI_HOOK" in new_ass
    assert "REBURN_SUCCESS" in new_ass
    assert Path(rerendered_job.rendered_video_path).is_file()
    assert Path(rerendered_job.rendered_video_path).stat().st_size > 0

    # 9. Upload via LocalExportUploader
    upload_res = pipeline._run_immediate_upload(rerendered_job)
    assert upload_res is not None
    assert upload_res.verified is True
    assert rerendered_job.status in (JobStatus.COMPLETED, JobStatus.CLEANED)
    exported_files = list(export_dir.glob("*.mp4"))
    assert len(exported_files) >= 1
    assert exported_files[0].stat().st_size > 0


def test_pipeline_auto_trim_silence_smart_crop_and_candidates_endpoints(
    test_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
):
    """Verify auto_trim_job_silence, crop_focus/auto-thumbnail forwarding, and candidate/silence API endpoints."""
    import json

    from fastapi.testclient import TestClient

    from autoshorts.dashboard.app import create_app

    state_mgr = test_env["state_mgr"]
    work_dir = test_env["work_dir"]
    output_dir = test_env["output_dir"]

    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: output_dir)
    monkeypatch.setattr("autoshorts.core.paths.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.core.paths.get_output_dir", lambda: output_dir)

    job_id = "job_autotrim_001"
    job_work = work_dir / job_id
    job_work.mkdir(parents=True, exist_ok=True)
    raw_video = job_work / f"{job_id}_raw.mp4"
    raw_video.write_bytes(b"\x00\x00\x00 fake video")
    sub_file = job_work / f"{job_id}.ass"
    sub_file.write_text("[Script Info]\nTitle: Test\n", encoding="utf-8")

    # Write aligned_words.json with two silences >= 0.48s (0.5 -> 1.3s and 2.0 -> 2.8s)
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.5, "probability": 0.99},
        {"word": "world", "start": 1.3, "end": 2.0, "probability": 0.99},
        {"word": "today", "start": 2.8, "end": 3.4, "probability": 0.99},
    ]
    (job_work / "aligned_words.json").write_text(json.dumps(words), encoding="utf-8")

    cand1 = ClipCandidate(
        start=0.0, end=35.0, text="First candidate window text", score=0.91, reason="Top Hook"
    )
    cand2 = ClipCandidate(
        start=40.0, end=80.0, text="Second candidate window text", score=0.84, reason="Runner Up"
    )
    sel = ClipSelection(
        candidate=cand1,
        clip_index=0,
        target_duration=35.0,
        hook_text="UNBELIEVABLE SECRET",
        title="Unbelievable Secret #Shorts",
    )
    job = Job(
        id=job_id,
        source_url="https://youtube.com/watch?v=autotrim1",
        status=JobStatus.SUBTITLES_GENERATED,
        raw_video_path=str(raw_video),
        subtitles_path=str(sub_file),
        candidate_windows=[cand1, cand2],
        selected_clips=[sel],
        video_metadata=VideoMetadata(
            title="Deep Dive Podcast",
            channel="Tech Insider",
            channel_url="https://youtube.com/@techinsider",
            video_url="https://youtube.com/watch?v=autotrim1",
            duration=120.0,
        ),
    )
    job.__dict__["crop_focus"] = "face"
    state_mgr.save_job(job)

    mock_compositor = MagicMock()

    def fake_compose(
        job_id, source_clip, edl, background_path, subtitles_path, output_path, **kwargs
    ):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00\x00\x00 master video")
        return p

    def fake_extract_thumb(video_path, output_path, **kwargs):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xd8\xff fake jpeg")
        return p

    mock_compositor.compose.side_effect = fake_compose
    mock_compositor.extract_thumbnail.side_effect = fake_extract_thumb

    pipe = ShortsPipeline(state_manager=state_mgr, compositor=mock_compositor)

    # 1. Test auto_trim_job_silence directly
    trim_res = pipe.auto_trim_job_silence(job_id, min_gap_sec=0.48)
    assert trim_res["job_id"] == job_id
    assert trim_res["jump_cuts_added"] == 2
    assert len(trim_res["jump_cuts"]) == 2
    assert trim_res["jump_cuts"][0]["cut_start"] == 0.5
    assert trim_res["jump_cuts"][0]["cut_end"] == 1.3

    # 2. Test _run_compositing_stage forwards crop_focus and extracts thumbnail
    reloaded = state_mgr.get_job(job_id)
    assert reloaded is not None
    reloaded.__dict__["crop_focus"] = "face"
    pipe._run_compositing_stage(reloaded)
    comp_kwargs = mock_compositor.compose.call_args[1]
    assert comp_kwargs.get("crop_focus") == "face"
    mock_compositor.extract_thumbnail.assert_called_once()
    assert getattr(reloaded.selected_clips[0], "thumbnail_path", None) == str(
        job_work / f"thumb_{job_id}.jpg"
    )

    # 3. Test FastAPI endpoints for candidates, select-clip, and auto-trim-silence
    app = create_app(pipeline=pipe, state_manager=state_mgr, run_scheduler_worker=False)
    client = TestClient(app)

    r_cands = client.get(f"/api/jobs/{job_id}/candidates")
    assert r_cands.status_code == 200
    cands_data = r_cands.json()
    assert cands_data["success"] is True
    assert len(cands_data["candidates"]) == 2
    assert len(cands_data["candidates"][0]["title_variants"]) == 3
    assert len(cands_data["title_variants"]) == 3

    # Switch active clip to candidate index 1 and apply a title variant
    chosen_variant = cands_data["candidates"][1]["title_variants"][1]
    r_sel = client.post(
        f"/api/jobs/{job_id}/select-clip",
        json={"candidate_index": 1, "title": chosen_variant},
    )
    assert r_sel.status_code == 200
    sel_data = r_sel.json()
    assert sel_data["selected_clip"]["clip_index"] == 1
    assert sel_data["selected_clip"]["title"] == chosen_variant

    # Call auto-trim-silence endpoint
    r_trim = client.post(
        f"/api/jobs/{job_id}/auto-trim-silence",
        json={"min_gap_sec": 0.5},
    )
    assert r_trim.status_code == 200
    assert r_trim.json()["jump_cuts_added"] == 2
