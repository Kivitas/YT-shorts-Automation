"""Tests for the download/edit/upload hardening pass.



Covers the behaviours that previously let bad output pass silently:



- section downloads that resolve to the wrong stream class are rejected

- the local-source shortcut requires an explicit origin and is recorded

- an empty edit plan is reported as a placeholder instead of a success

- subtitle timing provenance is recorded

- re-render refuses to proceed on missing/empty artifacts

- "verified" means positively confirmed, and Studio DOM is explicitly weaker

"""



from __future__ import annotationsfrom pathlib import Pathfrom unittest.mock import MagicMock, patchimport pytestfrom autoshorts.core.exceptions import AutoShortsErrorfrom autoshorts.core.models import (    CONFIRMING_VERIFICATION_KINDS,    UploadResult,    resolve_verification,)from autoshorts.edit_planner.edl_schema import clamp_edl, create_empty_edlfrom autoshorts.media.downloader import (    SECTION_NO_AUDIO,    SECTION_NO_VIDEO,    SECTION_OK,    Downloader,    SectionValidation,    validate_section_output,)# =============================================================================

# 1. Section output validation

# =============================================================================





def _probe(video: bool, audio: bool, duration: float = 40.0):

    return {"has_video": video, "has_audio": audio, "duration": duration}





def test_validate_section_accepts_video_and_audio(tmp_path: Path):

    f = tmp_path / "cut.mp4"

    f.write_bytes(b"payload")

    with patch(

        "autoshorts.media.downloader.probe_video_stream_info",

        return_value=_probe(True, True),

    ):

        result = validate_section_output(f)



    assert result.ok is True

    assert result.reason == SECTION_OK

    assert result.has_video and result.has_audio

    assert result.size_bytes == len(b"payload")





def test_validate_section_rejects_audio_only(tmp_path: Path):

    """A successful yt-dlp exit that yields audio only must not be accepted."""

    f = tmp_path / "cut.mp4"

    f.write_bytes(b"audio only")

    with patch(

        "autoshorts.media.downloader.probe_video_stream_info",

        return_value=_probe(False, True),

    ):

        result = validate_section_output(f)



    assert result.ok is False

    assert result.reason == SECTION_NO_VIDEO

    assert "no video stream" in result.detail





def test_validate_section_rejects_missing_audio(tmp_path: Path):

    f = tmp_path / "cut.mp4"

    f.write_bytes(b"video only")

    with patch(

        "autoshorts.media.downloader.probe_video_stream_info",

        return_value=_probe(True, False),

    ):

        result = validate_section_output(f)



    assert result.ok is False

    assert result.reason == SECTION_NO_AUDIO





def test_validate_section_rejects_missing_and_empty_files(tmp_path: Path):

    assert validate_section_output(tmp_path / "nope.mp4").ok is False



    empty = tmp_path / "empty.mp4"

    empty.write_bytes(b"")

    assert validate_section_output(empty).ok is False





def test_validate_section_rejects_absurdly_short_output(tmp_path: Path):

    f = tmp_path / "cut.mp4"

    f.write_bytes(b"payload")

    with patch(

        "autoshorts.media.downloader.probe_video_stream_info",

        return_value=_probe(True, True, duration=0.05),

    ):

        result = validate_section_output(f)



    assert result.ok is False

    assert "shorter than" in result.detail





def test_download_section_retries_then_fails_on_persistent_audio_only(tmp_path: Path):

    """The final attempt must raise, never silently accept a wrong output class."""

    target = tmp_path / "cut.mp4"



    def fake_run(cmd, **kwargs):

        import subprocess



        o_idx = cmd.index("-o")

        Path(cmd[o_idx + 1].replace("%(ext)s", "mp4")).write_bytes(b"audio only")

        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")



    bad = SectionValidation(ok=False, reason=SECTION_NO_VIDEO, detail="audio only")

    dl = Downloader(max_retries=2, retry_delay_sec=0.01)

    with (

        patch("subprocess.run", side_effect=fake_run),

        patch("autoshorts.media.downloader.validate_section_output", return_value=bad),

        pytest.raises(Exception) as exc_info,

    ):

        dl.download_section("https://youtu.be/dQw4w9WgXcQ", 0.0, 30.0, target)



    message = str(exc_info.value)

    assert "after 2 attempt(s)" in message

    assert "missing_video" in message

    assert "Attempt trace" in message

    # The rejected output must be cleaned up, not left behind as a usable cut.

    assert not target.exists()

    assert dl.last_download_diagnostics["attempt_count"] == 2





def test_download_section_succeeds_after_retry_and_records_diagnostics(tmp_path: Path):

    target = tmp_path / "cut.mp4"

    attempts: list[list[str]] = []



    def fake_run(cmd, **kwargs):

        import subprocess



        if "yt-dlp" in str(cmd[0]):

            attempts.append(list(cmd))

        o_idx = cmd.index("-o")

        Path(cmd[o_idx + 1].replace("%(ext)s", "mp4")).write_bytes(b"payload")

        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")



    good = SectionValidation(

        ok=True, reason=SECTION_OK, has_video=True, has_audio=True, duration=30.0

    )

    dl = Downloader(max_retries=3, retry_delay_sec=0.01)

    diagnostics: dict = {}

    with (

        patch("subprocess.run", side_effect=fake_run),

        patch("autoshorts.media.downloader.validate_section_output", return_value=good),

    ):

        result = dl.download_section(

            "https://youtu.be/dQw4w9WgXcQ", 0.0, 30.0, target, diagnostics=diagnostics

        )



    assert result == target

    assert diagnostics["attempt_count"] == 1

    assert diagnostics["validation"]["has_audio"] is True

    assert attempts[0].count("--download-sections") == 1

    # First attempt must not pre-emptively override the player client.

    assert "--extractor-args" not in attempts[0]





def test_default_format_spec_always_pairs_video_with_audio():

    """Every fallback tier must request a video+audio pair (or progressive muxed)."""

    from autoshorts.media.downloader import DEFAULT_FORMAT_SPEC



    for branch in DEFAULT_FORMAT_SPEC.split("/"):

        if not branch:

            continue

        has_video = (

            "video" in branch

            or branch.lstrip("best").startswith(("[", "best"))

            or branch.startswith("best[")

        )

        if "bestvideo" in branch:

            assert "+bestaudio" in branch, f"unpaired video branch: {branch}"

        if "bestvideo" not in branch and "+" in branch:

            pytest.fail(f"unexpected unpaired pair branch: {branch}")

        assert has_video or branch.startswith("best")





# =============================================================================

# 2. Origin explicit-ness

# =============================================================================





def test_job_origin_kind_controls_local_source_detection(tmp_path: Path):

    from autoshorts.pipeline import ShortsPipeline



    local_file = tmp_path / "clip.mp4"

    local_file.write_bytes(b"video")



    # A YouTube job must never be reinterpreted as local, even if a same-named file exists.

    yt_job = MagicMock()

    yt_job.origin_kind = "youtube"

    yt_job.raw_video_path = None

    yt_job.source_url = str(local_file)

    assert ShortsPipeline._resolve_local_source(yt_job) is None



    # Explicit local origin resolves the file.

    local_job = MagicMock()

    local_job.origin_kind = "local_file"

    local_job.raw_video_path = str(local_file)

    local_job.source_url = ""

    assert ShortsPipeline._resolve_local_source(local_job) == local_file.resolve()



    # A URL-shaped source is never treated as a path.

    url_job = MagicMock()

    url_job.origin_kind = None

    url_job.raw_video_path = None

    url_job.source_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    assert ShortsPipeline._resolve_local_source(url_job) is None





# =============================================================================

# 3. Editing provenance

# =============================================================================





def test_empty_edl_is_tagged_as_placeholder():

    edl = create_empty_edl(clip_duration=40.0, fallback_reason="no provider")

    assert edl.source == "empty_fallback"

    assert edl.fallback_reason == "no provider"

    assert edl.is_placeholder is True

    assert edl.has_micro_edits is False





def test_clamp_edl_preserves_provenance():

    edl = create_empty_edl(clip_duration=40.0, fallback_reason="schema failed")

    clamped = clamp_edl(edl, clip_duration=40.0)

    assert clamped.source == "empty_fallback"

    assert clamped.fallback_reason == "schema failed"





def test_planner_records_why_it_degraded():

    from autoshorts.edit_planner.multimodal_planner import MultimodalEditPlanner



    planner = MultimodalEditPlanner(provider=None)

    edl = planner.generate_edl(Path("missing.mp4"), transcript_text="hello there")

    assert edl.source == "empty_fallback"

    assert planner.last_source == "empty_fallback"

    assert planner.last_fallback_reason





def test_planner_marks_real_plan_source():

    from autoshorts.edit_planner.multimodal_planner import MultimodalEditPlanner



    payload = (

        '{"hook_in_sec": 0.0, "hook_out_sec": 20.0, "jump_cuts": [], "zoom_punch_ins": [],'

        ' "caption_corrections": [], "recommended_thumbnail_timestamp": 1.0,'

        ' "recommended_title": "A Title #Shorts", "recommended_hook": "Watch this",'

        ' "background_style_recommendation": "minecraft"}'

    )



    class _TranscriptOnlyProvider:

        """Provider exposing only complete(), i.e. no multimodal video analysis."""



        def complete(self, prompt: str, **kwargs: object) -> str:

            return payload



    provider = _TranscriptOnlyProvider()

    planner = MultimodalEditPlanner(provider=provider)

    with patch(

        "autoshorts.edit_planner.multimodal_planner.probe_clip_duration",

        return_value=30.0,

    ):

        edl = planner.generate_edl(Path("clip.mp4"), transcript_text="text")



    assert edl.source == "transcript"

    assert edl.is_placeholder is False





# =============================================================================

# 4. Verification semantics

# =============================================================================





@pytest.mark.parametrize(

    "declared,verified,expected_kind,expected_confirmed",

    [

        ("api_confirmed", True, "api_confirmed", True),

        ("local_export", True, "local_export", True),

        ("studio_dom", True, "studio_dom", False),

        ("studio_dom", False, "unverified", False),

        ("api_confirmed", False, "unverified", False),

        (None, True, "api_confirmed", True),

        (None, False, "unverified", False),

        ("garbage", True, "api_confirmed", True),

    ],

)

def test_resolve_verification_matrix(declared, verified, expected_kind, expected_confirmed):

    kind, confirmed = resolve_verification(declared, verified)

    assert kind == expected_kind

    assert confirmed is expected_confirmed





def test_studio_dom_is_never_confirming():

    assert "studio_dom" not in CONFIRMING_VERIFICATION_KINDS





def test_upload_result_defaults_to_unverified():

    result = UploadResult(video_id="abc", url="https://youtube.com/shorts/abc")

    assert result.verified is False

    assert result.verification_kind == "unverified"





# =============================================================================

# 5. Re-render preflight

# =============================================================================





def _make_pipeline(tmp_path: Path):

    from autoshorts.core.state_manager import StateManager    from autoshorts.pipeline import ShortsPipeline



    state = StateManager(db_path=tmp_path / "state.db")

    return ShortsPipeline(state_manager=state)





def test_rerender_refuses_without_selected_clip(tmp_path: Path, monkeypatch):

    from autoshorts.core.models import Job, JobStatus



    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: tmp_path / "work")

    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: tmp_path / "output")



    pipe = _make_pipeline(tmp_path)

    clip = tmp_path / "raw.mp4"

    clip.write_bytes(b"video")



    job = Job(

        id="rerender_noclip",

        source_url="",

        status=JobStatus.READY_FOR_REVIEW,

        raw_video_path=str(clip),

        selected_clips=[],

    )

    pipe.state_manager.save_job(job)



    with pytest.raises(AutoShortsError):

        pipe.rerender_job("rerender_noclip")





def test_rerender_refuses_on_empty_raw_cut(tmp_path: Path, monkeypatch):

    from autoshorts.core.models import ClipCandidate, ClipSelection, Job, JobStatus



    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: tmp_path / "work")

    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: tmp_path / "output")



    pipe = _make_pipeline(tmp_path)

    empty = tmp_path / "empty_raw.mp4"

    empty.write_bytes(b"")



    job = Job(

        id="rerender_empty",

        source_url="",

        status=JobStatus.READY_FOR_REVIEW,

        raw_video_path=str(empty),

        selected_clips=[

            ClipSelection(candidate=ClipCandidate(start=0.0, end=20.0, text="hi there"))

        ],

    )

    pipe.state_manager.save_job(job)



    # Empty file + no way to regenerate => hard, explicit failure rather than a bad render.

    with pytest.raises(AutoShortsError):

        pipe.rerender_job("rerender_empty")








# =============================================================================
# Track 4 Scheduler, Attribution & Rhythm Hardening Tests
# =============================================================================


def test_scheduler_destructive_cleanup_prevented_on_unconfirmed_studio_result(tmp_path: Path):
    """Verify unconfirmed Studio DOM scrapes (result.verified == False) NEVER trigger cleaner.cleanup_job."""
    from datetime import UTC, datetime    from autoshorts.core.models import Job, JobStatus, UploadResult    from autoshorts.core.state_manager import StateManager    from autoshorts.quota.quota_tracker import QuotaTracker    from autoshorts.scheduler.spread_scheduler import SpreadScheduler

    db_path = tmp_path / "test.db"
    state_mgr = StateManager(db_path=db_path)
    tracker = QuotaTracker(state_mgr)

    # Cleaner mock that should NOT be called
    mock_cleaner = MagicMock()

    # Uploader returning studio_dom unconfirmed result
    mock_uploader = MagicMock()
    mock_uploader.upload.return_value = UploadResult(
        video_id="studio123",
        url="https://youtube.com/shorts/studio123",
        status="uploaded_unconfirmed",
        verified=False,
        verification_kind="studio_dom",
        quota_spent=0,
    )
    mock_uploader.last_verification_kind = "studio_dom"
    # verify() returns True (DOM scrape found video), but resolve_verification marks confirmed=False!
    mock_uploader.verify.return_value = True

    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=tracker,
        uploader=mock_uploader,
        cleaner=mock_cleaner,
    )

    job_id = "job_studio_cleanup"
    dummy_file = tmp_path / "video.mp4"
    dummy_file.write_bytes(b"video data")

    job = Job(id=job_id, source_url="https://example.com", status=JobStatus.SCHEDULED)
    state_mgr.save_job(job)

    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path=str(dummy_file),
        title="Test Title",
        description="Test Desc",
        scheduled_time=datetime.now(UTC),
        slot_jitter_minutes=0,
        privacy_status="unlisted",
        tags=[],
    )

    result = scheduler.upload_now(job_id)
    assert result.video_id == "studio123"
    assert result.verified is False  # unconfirmed

    # Cleaner must NEVER have been called because result.verified is False!
    assert mock_cleaner.cleanup_job.call_count == 0
    # The file remains intact
    assert dummy_file.exists()


def test_state_manager_atomic_slot_claiming(tmp_path: Path):
    """Verify claim_scheduled_slot atomically claims SCHEDULED slot and rejects duplicate claims."""
    from datetime import UTC, datetime    from autoshorts.core.models import Job, JobStatus    from autoshorts.core.state_manager import StateManager

    state_mgr = StateManager(db_path=tmp_path / "test_claim.db")
    job_id = "job_claim_1"
    state_mgr.save_job(Job(id=job_id, source_url="https://example.com", status=JobStatus.SCHEDULED))

    slot_id = state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path="video.mp4",
        title="Title",
        description="Desc",
        scheduled_time=datetime.now(UTC),
        slot_jitter_minutes=0,
        privacy_status="unlisted",
        tags=[],
    )

    # First claim succeeds
    claimed_first = state_mgr.claim_scheduled_slot(slot_id)
    assert claimed_first is True

    # Second claim by another worker/thread fails
    claimed_second = state_mgr.claim_scheduled_slot(slot_id)
    assert claimed_second is False


def test_attribution_auto_tune_and_winning_guidance_summary(tmp_path: Path, monkeypatch):
    """Verify auto_tune_config_from_winners persists to data/autotune_winners.json and guidance summary renders."""
    from autoshorts.analytics.attribution import (        PerformanceAttributionEngine,        get_winning_guidance_summary,    )    from autoshorts.core.config import Settings    from autoshorts.core.models import Job, JobStatus, UploadResult    from autoshorts.core.state_manager import StateManager

    monkeypatch.setattr("autoshorts.core.paths.get_data_dir", lambda: tmp_path)

    state_mgr = StateManager(db_path=tmp_path / "test_attr.db")

    # Insert a job with layout and styles
    job = Job(
        id="job_attr_1",
        source_url="https://example.com",
        status=JobStatus.COMPLETED,
        layout_mode="split_50_50",
        style_preset="hormozi",
        upload_result=UploadResult(video_id="vid_attr_1", url="url", status="completed"),
    )
    state_mgr.save_job(job)

    # Insert analytics record with high performance
    state_mgr.upsert_video_analytics({
        "video_id": "vid_attr_1",
        "views": 10000,
        "likes": 800,
        "avg_view_duration_sec": 45.0,
        "avg_view_percentage": 85.0,
        "viewed_vs_swiped_pct": 80.0,
        "job_id": "job_attr_1",
    })

    engine = PerformanceAttributionEngine(state_mgr)
    settings = Settings()
    res = engine.auto_tune_config_from_winners(settings)

    assert res["applied"] is True
    winners_file = tmp_path / "autotune_winners.json"
    assert winners_file.is_file()

    guidance = engine.get_winning_guidance_summary()
    assert "Historical Channel Top Performers" in guidance
    assert "split_50_50" in guidance
    assert "hormozi" in guidance

    # Standalone function also reads from file
    summary = get_winning_guidance_summary(data_dir=tmp_path)
    assert "Historical Channel Top Performers" in summary
    assert "split_50_50" in summary


def test_pipeline_process_batch_parallel(tmp_path: Path, monkeypatch):
    """Verify process_batch processes in parallel using ThreadPoolExecutor and preserves job order."""
    import threading    import time    from autoshorts.core.models import Job, JobStatus

    pipe = _make_pipeline(tmp_path)
    pipe.concurrency = 3

    jobs = [
        Job(id=f"batch_job_{i}", source_url=f"https://example.com/{i}", status=JobStatus.COMPLETED)
        for i in range(4)
    ]
    for j in jobs:
        pipe.state_manager.save_job(j)

    thread_ids = set()

    def fake_process_job(job, **kwargs):
        thread_ids.add(threading.get_ident())
        time.sleep(0.02)
        job.custom_instructions = f"processed_{job.id}"
        return job

    monkeypatch.setattr(pipe, "process_job", fake_process_job)

    results = pipe.process_batch(jobs)

    # Verify results preserved order
    assert [r.id for r in results] == [f"batch_job_{i}" for i in range(4)]
    assert all(r.custom_instructions == f"processed_{r.id}" for r in results)
    # Verify concurrency was used (more than 1 thread processed jobs)
    assert len(thread_ids) > 1


def test_pipeline_laya_rhythm_engine_integration(tmp_path: Path, monkeypatch):
    """Verify LayaRhythmEngine snaps zoom punches and computes duck_release_ms when bgm_path is set."""
    from autoshorts.core.models import (        ClipCandidate,        ClipSelection,        EditDecisionList,        Job,        JobStatus,        ZoomPunchIn,    )

    pipe = _make_pipeline(tmp_path)

    dummy_bgm = tmp_path / "bgm.wav"
    dummy_bgm.write_bytes(b"dummy bgm audio")

    job = Job(
        id="job_rhythm",
        source_url="https://example.com",
        status=JobStatus.DOWNLOADING_SECTION,
        raw_video_path=str(tmp_path / "raw.mp4"),
        subtitles_path=str(tmp_path / "subs.ass"),
        bgm_path=str(dummy_bgm),
        selected_clips=[
            ClipSelection(candidate=ClipCandidate(start=0.0, end=10.0, text="hello rhythm"))
        ],
        edl=EditDecisionList(
            zoom_punch_ins=[ZoomPunchIn(timestamp=1.03, scale=1.15, duration=0.5)],
        ),
    )
    (tmp_path / "raw.mp4").write_bytes(b"video")
    (tmp_path / "subs.ass").write_bytes(b"subs")
    pipe.state_manager.save_job(job)

    # Mock compositor
    compositor_kwargs = {}

    def fake_compose(**kwargs):
        compositor_kwargs.update(kwargs)
        out = tmp_path / "master.mp4"
        out.write_bytes(b"master")
        return out

    monkeypatch.setattr(pipe.compositor, "compose", fake_compose)
    monkeypatch.setattr("autoshorts.pipeline.count_decode_errors", lambda p: 0)

    pipe._run_compositing_stage(job)

    # Verify duck_release_ms was calculated and passed to compositor
    assert "rhythm_ducking_release" in compositor_kwargs
    assert compositor_kwargs["rhythm_ducking_release"] is not None
    assert compositor_kwargs["rhythm_ducking_release"] > 0
    # Verify rhythm diagnostic was recorded
    diag = job.diagnostics.get("rhythm")
    assert diag is not None
    assert diag["duck_release_ms"] == compositor_kwargs["rhythm_ducking_release"]
