"""Stage-level tests for ShortsPipeline: render-slot scope, failure resume and input guards.

Stage internals are replaced with fakes, so these run without ffmpeg, yt-dlp or the network.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from autoshorts.core.exceptions import AutoShortsError, UploadError
from autoshorts.core.models import (
    ClipCandidate,
    ClipSelection,
    Job,
    JobStatus,
    UploadResult,
)
from autoshorts.core.state_manager import StateManager
from autoshorts.pipeline import ShortsPipeline

SOURCE = "https://www.youtube.com/watch?v=stagetest1"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    work = tmp_path / "work"
    out = tmp_path / "output"
    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: out)
    state = StateManager(db_path=tmp_path / "state.db")
    # concurrency=1 makes any leaked or doubly-acquired slot block immediately.
    pipe = ShortsPipeline(
        state_manager=state,
        concurrency=1,
        downloader=MagicMock(),
        transcript_fetcher=MagicMock(),
        compositor=MagicMock(),
        uploader=MagicMock(),
    )
    return SimpleNamespace(pipe=pipe, state=state, work=work, out=out, tmp=tmp_path)


def _lock_held(pipe: ShortsPipeline) -> bool:
    """True when the render semaphore is currently taken (probed without blocking)."""
    acquired = pipe._render_lock.acquire(blocking=False)
    if acquired:
        pipe._render_lock.release()
    return not acquired


def _clip() -> ClipSelection:
    return ClipSelection(candidate=ClipCandidate(start=0.0, end=20.0, text="hi there"))


def _write(path: Path, data: bytes = b"video") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _run_with_timeout(fn: Callable[[], Any], timeout: float = 10.0) -> Any:
    """Run ``fn`` on a worker thread so a deadlock fails the test instead of hanging it."""
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = fn()
        except BaseException as err:  # re-raised below on the test thread
            outcome["error"] = err

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    assert not worker.is_alive(), "call did not return: render slot was acquired twice"
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


# --- render slot scope ----------------------------------------------------------------


def test_metadata_and_transcript_run_without_render_slot(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
):
    seen: dict[str, bool] = {}

    def fake_metadata(job: Job) -> None:
        seen["metadata"] = _lock_held(env.pipe)
        job.transition_to(JobStatus.METADATA_FETCHED)

    def fake_transcript(job: Job) -> None:
        seen["transcript"] = _lock_held(env.pipe)
        raise RuntimeError("stop after transcript")

    monkeypatch.setattr(env.pipe, "_run_metadata_stage", fake_metadata)
    monkeypatch.setattr(env.pipe, "_run_transcript_stage", fake_transcript)

    job = Job(id="stage_meta", source_url=SOURCE)
    env.state.save_job(job)
    with pytest.raises(RuntimeError, match="stop after transcript"):
        env.pipe.process_job(job)

    assert seen == {"metadata": False, "transcript": False}


def test_render_slot_is_released_when_download_raises(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
):
    seen: dict[str, bool] = {}

    def failing_download(job: Job) -> None:
        seen["download"] = _lock_held(env.pipe)
        raise RuntimeError("yt-dlp exploded")

    monkeypatch.setattr(env.pipe, "_run_download_stage", failing_download)

    job = Job(
        id="stage_boom",
        source_url=SOURCE,
        status=JobStatus.SEMIF_EVALUATED,
        selected_clips=[_clip()],
    )
    env.state.save_job(job)
    with pytest.raises(RuntimeError, match="yt-dlp exploded"):
        env.pipe.process_job(job)

    assert seen["download"] is True  # the download really ran under the slot
    assert _lock_held(env.pipe) is False  # and the exception released it
    failed = env.state.get_job("stage_boom")
    assert failed is not None
    assert failed.status == JobStatus.FAILED
    assert failed.diagnostics["pipeline"]["failed_stage"] == "download"


def test_upload_stage_runs_without_render_slot(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
):
    seen: dict[str, bool] = {}

    def fake_upload(job: Job) -> UploadResult:
        seen["upload"] = _lock_held(env.pipe)
        return UploadResult(video_id="abc123DEF45", url="https://youtube.com/shorts/abc123DEF45")

    monkeypatch.setattr(env.pipe, "_run_immediate_upload", fake_upload)
    rendered = _write(env.out / "stage_up_master.mp4", b"mp4")
    job = Job(
        id="stage_up",
        source_url=SOURCE,
        status=JobStatus.READY_FOR_REVIEW,
        selected_clips=[_clip()],
        rendered_video_path=str(rendered),
    )
    env.state.save_job(job)

    env.pipe.process_job(job, immediate_upload=True)

    assert seen == {"upload": False}


def test_nested_render_slot_does_not_double_acquire(env: SimpleNamespace):
    def nested() -> bool:
        with env.pipe._render_slot(), env.pipe._render_slot():
            return _lock_held(env.pipe)

    assert _run_with_timeout(nested) is True
    assert _lock_held(env.pipe) is False


def test_rerender_redownloads_and_composites_each_under_one_slot(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
):
    seen: dict[str, bool] = {}

    def fake_download(job: Job) -> None:
        seen["download"] = _lock_held(env.pipe)
        assert job.status == JobStatus.SEMIF_EVALUATED
        raw = _write(env.work / job.id / f"{job.id}_raw.mp4")
        job.raw_video_path = str(raw)
        job.transition_to(JobStatus.DOWNLOADING_SECTION)

    def fake_subtitles(job: Job, use_cached_words: bool = False) -> None:
        seen["subtitles"] = _lock_held(env.pipe)
        ass = _write(env.work / job.id / f"{job.id}.ass", b"[Script Info]")
        job.subtitles_path = str(ass)
        job.transition_to(JobStatus.SUBTITLES_GENERATED)

    def fake_compositing(job: Job, **kwargs: Any) -> None:
        seen["compositing"] = _lock_held(env.pipe)
        job.rendered_video_path = str(_write(env.out / f"{job.id}_master.mp4", b"mp4"))
        job.status = JobStatus.READY_FOR_REVIEW

    monkeypatch.setattr(env.pipe, "_run_download_stage", fake_download)
    monkeypatch.setattr(env.pipe, "_run_subtitles_stage", fake_subtitles)
    monkeypatch.setattr(env.pipe, "_run_compositing_stage", fake_compositing)

    # Raw cut is recorded but missing, so the re-render must re-download it first. The job is
    # READY_FOR_REVIEW, a state the download stage cannot transition out of directly.
    job = Job(
        id="stage_rr",
        source_url=SOURCE,
        status=JobStatus.READY_FOR_REVIEW,
        selected_clips=[_clip()],
        raw_video_path=str(env.work / "stage_rr" / "gone_raw.mp4"),
    )
    env.state.save_job(job)

    result = _run_with_timeout(lambda: env.pipe.rerender_job("stage_rr"))

    assert seen == {"download": True, "subtitles": True, "compositing": True}
    assert result.status == JobStatus.READY_FOR_REVIEW
    assert _lock_held(env.pipe) is False


def test_rerender_failure_marks_failed_and_releases_slot(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
):
    raw = _write(env.work / "stage_rrf" / "stage_rrf_raw.mp4")

    def fake_subtitles(job: Job, use_cached_words: bool = False) -> None:
        ass = _write(env.work / job.id / f"{job.id}.ass", b"[Script Info]")
        job.subtitles_path = str(ass)
        job.transition_to(JobStatus.SUBTITLES_GENERATED)

    def failing_compositing(job: Job, **kwargs: Any) -> None:
        raise RuntimeError("ffmpeg crashed")

    monkeypatch.setattr(env.pipe, "_run_subtitles_stage", fake_subtitles)
    monkeypatch.setattr(env.pipe, "_run_compositing_stage", failing_compositing)

    job = Job(
        id="stage_rrf",
        source_url=SOURCE,
        status=JobStatus.READY_FOR_REVIEW,
        selected_clips=[_clip()],
        raw_video_path=str(raw),
    )
    env.state.save_job(job)

    with pytest.raises(RuntimeError, match="ffmpeg crashed"):
        env.pipe.rerender_job("stage_rrf")

    assert _lock_held(env.pipe) is False
    failed = env.state.get_job("stage_rrf")
    assert failed is not None
    assert failed.status == JobStatus.FAILED
    assert failed.diagnostics["pipeline"]["failed_stage"] == "compositing"


# --- failure handling and resume ------------------------------------------------------


def test_retry_resumes_at_upload_when_render_survived(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
):
    raw = _write(env.work / "stage_resume" / "stage_resume_raw.mp4")
    rendered = _write(env.out / "stage_resume_master.mp4", b"mp4")
    job = Job(
        id="stage_resume",
        source_url=SOURCE,
        status=JobStatus.FAILED,
        selected_clips=[_clip()],
        raw_video_path=str(raw),
        rendered_video_path=str(rendered),
        error_message="upload timed out",
    )
    job.record_diagnostic("pipeline", failed_stage="upload")
    env.state.save_job(job)

    metadata = MagicMock(side_effect=AssertionError("metadata must not re-run"))
    monkeypatch.setattr(env.pipe, "_run_metadata_stage", metadata)
    seen_status: list[JobStatus] = []

    def fake_upload(job: Job) -> UploadResult:
        seen_status.append(job.status)
        return UploadResult(video_id="abc123DEF45", url="https://youtube.com/shorts/abc123DEF45")

    monkeypatch.setattr(env.pipe, "_run_immediate_upload", fake_upload)

    env.pipe.retry_job("stage_resume", immediate_upload=True)

    metadata.assert_not_called()
    assert seen_status == [JobStatus.READY_FOR_REVIEW]
    saved = env.state.get_job("stage_resume")
    assert saved is not None
    assert saved.rendered_video_path == str(rendered)


def test_retry_restarts_from_queued_when_artifacts_are_missing(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
):
    job = Job(
        id="stage_gone",
        source_url=SOURCE,
        status=JobStatus.FAILED,
        selected_clips=[_clip()],
        raw_video_path=str(env.work / "stage_gone" / "deleted_raw.mp4"),
    )
    job.record_diagnostic("pipeline", failed_stage="compositing")
    env.state.save_job(job)

    def metadata_reached(job: Job) -> None:
        raise RuntimeError("metadata reached")

    monkeypatch.setattr(env.pipe, "_run_metadata_stage", metadata_reached)

    # The cut is gone, so the job must restart at metadata rather than crash in compositing.
    with pytest.raises(RuntimeError, match="metadata reached"):
        env.pipe.retry_job("stage_gone")
    saved = env.state.get_job("stage_gone")
    assert saved is not None
    assert saved.diagnostics["pipeline"]["resume_from"] == JobStatus.QUEUED.value


@pytest.mark.parametrize(
    "result",
    [
        UploadResult(video_id="", url="https://youtube.com/shorts/"),
        SimpleNamespace(success=False, video_id="abc123DEF45", url="x", raw_response={}),
    ],
    ids=["empty_video_id", "success_false"],
)
def test_unsuccessful_upload_result_is_not_completed_and_keeps_render(
    env: SimpleNamespace, result: Any
):
    rendered = _write(env.out / "stage_badup_master.mp4", b"mp4")
    env.pipe.uploader.upload.return_value = result
    job = Job(
        id="stage_badup",
        source_url=SOURCE,
        status=JobStatus.READY_FOR_REVIEW,
        selected_clips=[_clip()],
        rendered_video_path=str(rendered),
    )
    env.state.save_job(job)

    with pytest.raises(UploadError):
        env.pipe.process_job(job, immediate_upload=True)

    saved = env.state.get_job("stage_badup")
    assert saved is not None
    assert saved.status == JobStatus.FAILED
    assert saved.status != JobStatus.COMPLETED
    assert saved.rendered_video_path == str(rendered)
    assert saved.upload_result is None
    assert saved.diagnostics["pipeline"]["failed_stage"] == "upload"
    env.pipe.uploader.verify.assert_not_called()


# --- stage input guards ---------------------------------------------------------------


def test_compositing_rejects_missing_section_before_ffmpeg(env: SimpleNamespace):
    job = Job(
        id="stage_nocut",
        source_url=SOURCE,
        status=JobStatus.SUBTITLES_GENERATED,
        selected_clips=[_clip()],
        raw_video_path=str(env.work / "stage_nocut" / "missing.mp4"),
    )
    env.state.save_job(job)

    with pytest.raises(AutoShortsError, match="downloaded section is missing or empty"):
        env.pipe.process_job(job)

    env.pipe.compositor.compose.assert_not_called()
    assert env.state.get_job("stage_nocut").diagnostics["pipeline"]["failed_stage"] == "compositing"


def test_compositing_rejects_empty_section(env: SimpleNamespace):
    raw = _write(env.work / "stage_empty" / "stage_empty_raw.mp4", b"")
    job = Job(
        id="stage_empty",
        source_url=SOURCE,
        status=JobStatus.SUBTITLES_GENERATED,
        selected_clips=[_clip()],
        raw_video_path=str(raw),
    )
    env.state.save_job(job)

    with pytest.raises(AutoShortsError, match="missing or empty"):
        env.pipe.process_job(job)
    env.pipe.compositor.compose.assert_not_called()


def test_compositing_rejects_section_without_video_stream(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
):
    raw = _write(env.work / "stage_audio" / "stage_audio_raw.mp4")
    monkeypatch.setattr(
        "autoshorts.media.downloader.probe_video_stream_info",
        lambda path: {"has_video": False, "duration": 30.0},
    )
    job = Job(
        id="stage_audio",
        source_url=SOURCE,
        status=JobStatus.SUBTITLES_GENERATED,
        selected_clips=[_clip()],
        raw_video_path=str(raw),
    )
    env.state.save_job(job)

    with pytest.raises(AutoShortsError, match="no video stream"):
        env.pipe.process_job(job)
    env.pipe.compositor.compose.assert_not_called()


def test_compositing_regenerates_missing_subtitles(
    env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
):
    raw = _write(env.work / "stage_subs" / "stage_subs_raw.mp4")
    regenerated: list[str] = []

    def fake_subtitles(job: Job, use_cached_words: bool = False) -> None:
        regenerated.append(job.id)
        ass = _write(env.work / job.id / f"{job.id}.ass", b"[Script Info]")
        job.subtitles_path = str(ass)

    monkeypatch.setattr(env.pipe, "_run_subtitles_stage", fake_subtitles)
    env.pipe.compositor.compose.return_value = env.out / "stage_subs_master.mp4"
    job = Job(
        id="stage_subs",
        source_url=SOURCE,
        status=JobStatus.SUBTITLES_GENERATED,
        selected_clips=[_clip()],
        raw_video_path=str(raw),
        subtitles_path=str(env.work / "stage_subs" / "deleted.ass"),
    )
    env.state.save_job(job)

    result = env.pipe.process_job(job)

    assert regenerated == ["stage_subs"]
    assert result.status == JobStatus.READY_FOR_REVIEW
    env.pipe.compositor.compose.assert_called_once()


def test_record_diagnostic_accepts_section_as_a_data_key():
    """Downloader diagnostics carry a 'section' key; it must not clash with the section name."""
    from autoshorts.core.models import Job

    job = Job(source_url="https://www.youtube.com/watch?v=aqz-KE-bpKQ")
    job.record_diagnostic("download", section="*00:01.000-00:50.000", mode="yt_dlp")
    assert job.diagnostics["download"]["section"] == "*00:01.000-00:50.000"
