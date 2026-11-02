"""Phase 4: upload, scheduling and EDL silent-failure fixes. No network: HTTP and ffprobe are mocked."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autoshorts.core.exceptions import UploadError
from autoshorts.core.models import Job, JobStatus, UploadResult

# --- 1. QC gate ------------------------------------------------------------------------------


class _DiagJob:
    def __init__(self, path: str) -> None:
        self.id = "job_qc"
        self.rendered_video_path = path
        self.diagnostics: dict = {}

    def record_diagnostic(self, section: str, /, **values) -> None:
        self.diagnostics.setdefault(section, {}).update(values)


def _delivery(rows: list[dict], ok: bool) -> dict:
    issues = [
        f"{r['check']}: {r['status']} {r['value']} (expected {r['expected']})"
        for r in rows
        if r["status"] in ("FAIL", "WARN")
    ]
    return {"ok": ok, "issues": issues, "raw": {"ok": ok, "checks": rows}}


def _gate(job, _unused=None):
    from autoshorts.pipeline.stages_upload import UploadMixin

    host = SimpleNamespace()
    return lambda j: UploadMixin._delivery_gate(host, j)


def test_qc_gate_blocks_on_wrong_aspect(tmp_path: Path, monkeypatch):
    from autoshorts.pipeline import stages_upload

    video = tmp_path / "out.mp4"
    video.write_bytes(b"x")
    rows = [{"check": "aspect", "status": "FAIL", "value": "16:9", "expected": "9:16"}]
    monkeypatch.setattr(stages_upload, "check_delivery", lambda p: _delivery(rows, ok=False))
    job = _DiagJob(str(video))
    gate = _gate(job)
    with pytest.raises(UploadError, match="aspect"):
        gate(job)
    assert job.diagnostics["delivery_qc"]["hard_failures"]


def test_qc_gate_blocks_on_missing_video_codec(tmp_path: Path, monkeypatch):
    from autoshorts.pipeline import stages_upload

    video = tmp_path / "out.mp4"
    video.write_bytes(b"x")
    rows = [{"check": "video codec", "status": "FAIL", "value": None, "expected": "h264"}]
    monkeypatch.setattr(stages_upload, "check_delivery", lambda p: _delivery(rows, ok=False))
    job = _DiagJob(str(video))
    gate = _gate(job)
    with pytest.raises(UploadError, match="video codec"):
        gate(job)


def test_qc_gate_only_warns_on_soft_findings(tmp_path: Path, monkeypatch, caplog):
    from autoshorts.pipeline import stages_upload

    video = tmp_path / "out.mp4"
    video.write_bytes(b"x")
    rows = [
        {"check": "duration", "status": "FAIL", "value": "70s", "expected": "<=60s"},
        {"check": "loudness", "status": "WARN", "value": "-9 LUFS", "expected": "-14"},
        {"check": "video codec", "status": "PASS", "value": "h264", "expected": "h264"},
    ]
    monkeypatch.setattr(stages_upload, "check_delivery", lambda p: _delivery(rows, ok=False))
    job = _DiagJob(str(video))
    gate = _gate(job)
    with caplog.at_level("WARNING", logger="autoshorts.pipeline"):
        gate(job)  # must not raise
    assert "delivery QC warnings" in caplog.text
    assert job.diagnostics["delivery_qc"]["ok"] is False
    assert job.diagnostics["delivery_qc"]["hard_failures"] == []


def test_qc_gate_does_not_block_when_check_tool_cannot_run(tmp_path: Path, monkeypatch):
    from autoshorts.pipeline import stages_upload

    video = tmp_path / "out.mp4"
    video.write_bytes(b"x")
    failure = {"ok": False, "issues": ["check.py exited 1 without a JSON result"], "raw": {}}
    monkeypatch.setattr(stages_upload, "check_delivery", lambda p: failure)
    job = _DiagJob(str(video))
    gate = _gate(job)
    gate(job)  # informational tool failure never blocks


# --- 2. EDL planner fallback -----------------------------------------------------------------


class _BrokenProvider:
    def analyze_video(self, path, prompt):
        raise RuntimeError("model down")

    def complete(self, prompt):
        raise RuntimeError("model down")


def test_planner_error_is_flagged_as_fallback(tmp_path: Path):
    from autoshorts.core.config import EditPlannerConfig
    from autoshorts.edit_planner.edl_schema import is_planner_fallback
    from autoshorts.edit_planner.multimodal_planner import MultimodalEditPlanner

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    cfg = EditPlannerConfig(fallback_to_transcript=False)
    planner = MultimodalEditPlanner(provider=_BrokenProvider(), config=cfg)
    edl = planner.generate_edl(clip, transcript_text="hello world")

    assert edl.source == "empty_fallback"
    assert edl.jump_cuts == []
    assert planner.last_planner_fallback is True
    assert is_planner_fallback(edl) is True


def test_planner_success_is_not_flagged(tmp_path: Path):
    from autoshorts.edit_planner.edl_schema import is_planner_fallback
    from autoshorts.edit_planner.multimodal_planner import MultimodalEditPlanner

    class _GoodProvider:
        def complete(self, prompt, **kwargs):
            return '{"hook_in_sec": 0, "hook_out_sec": 30, "jump_cuts": [], "zoom_punch_ins": []}'

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    planner = MultimodalEditPlanner(provider=_GoodProvider())
    edl = planner.generate_edl(clip, transcript_text="hello")
    assert planner.last_planner_fallback is False
    assert is_planner_fallback(edl) is (edl.source == "empty_fallback")


# --- 3. Verify status: unverified upload never COMPLETED -------------------------------------


def test_unverified_upload_slot_is_not_completed(tmp_path: Path):
    from autoshorts.core.state_manager import StateManager
    from autoshorts.quota.quota_tracker import QuotaTracker
    from autoshorts.scheduler.spread_scheduler import SpreadScheduler

    state_mgr = StateManager(db_path=tmp_path / "p4.db")
    uploader = MagicMock()
    uploader.upload.return_value = UploadResult(
        video_id="vid_unverified",
        url="https://youtube.com/shorts/vid_unverified",
        status="uploaded_unconfirmed",
        verified=False,
        verification_kind="unverified",
        quota_spent=0,
    )
    uploader.last_verification_kind = "unverified"
    uploader.verify.return_value = False
    cleaner = MagicMock()
    scheduler = SpreadScheduler(
        state_mgr=state_mgr,
        quota_tracker=QuotaTracker(state_mgr),
        uploader=uploader,
        cleaner=cleaner,
    )
    job_id = "job_unverified"
    video = tmp_path / "v.mp4"
    video.write_bytes(b"data")
    state_mgr.save_job(Job(id=job_id, source_url="https://example.com", status=JobStatus.SCHEDULED))
    state_mgr.enqueue_for_upload(
        job_id=job_id,
        video_path=str(video),
        title="T",
        description="D",
        scheduled_time=datetime.now(UTC),
        slot_jitter_minutes=0,
        privacy_status="unlisted",
        tags=[],
    )

    scheduler.upload_now(job_id)

    row = (
        state_mgr._get_connection()
        .execute("SELECT status FROM scheduler_queue WHERE job_id = ?", (job_id,))
        .fetchone()
    )
    assert row["status"] != "COMPLETED"
    assert row["status"] == "FAILED"
    assert state_mgr.get_job(job_id).status != JobStatus.COMPLETED
    assert cleaner.cleanup_job.call_count == 0


# --- 4. Playlist and pinned comment return typed results -------------------------------------


def _bare_uploader():
    from autoshorts.uploader.youtube_api import YouTubeApiUploader

    up = YouTubeApiUploader.__new__(YouTubeApiUploader)
    up.quota_tracker = None
    return up


def test_playlist_failure_returns_result_with_error(caplog):
    up = _bare_uploader()
    service = MagicMock()
    service.playlistItems.return_value.insert.return_value.execute.side_effect = RuntimeError(
        "403 forbidden"
    )
    up.get_service = lambda: service
    with caplog.at_level("WARNING"):
        res = up.add_to_playlist("abc123DEF45", "PL1")
    assert res.ok is False
    assert "403" in (res.error or "")
    assert res.video_id == "abc123DEF45"
    assert "abc123DEF45" in caplog.text
    assert not res


def test_pinned_comment_failure_returns_result_with_error(caplog):
    up = _bare_uploader()
    service = MagicMock()
    service.commentThreads.return_value.insert.return_value.execute.side_effect = RuntimeError(
        "comments disabled"
    )
    up.get_service = lambda: service
    with caplog.at_level("WARNING"):
        res = up.post_comment("abc123DEF45", "hi")
    assert res.ok is False
    assert res.action == "comment"
    assert "abc123DEF45" in caplog.text


def test_pinned_comment_success_carries_comment_id():
    up = _bare_uploader()
    service = MagicMock()
    service.commentThreads.return_value.insert.return_value.execute.return_value = {"id": "c9"}
    up.get_service = lambda: service
    res = up.post_comment("abc123DEF45", "hi")
    assert res.ok is True and res.value == "c9"


# --- 5. Retry cap ----------------------------------------------------------------------------


def test_resumable_retries_capped_even_with_intermittent_success(monkeypatch):
    from autoshorts.uploader import youtube_api as yt

    monkeypatch.setattr(yt.time, "sleep", lambda s: None)
    up = _bare_uploader()
    up.max_retries = 5

    # Pattern: one failure, one good chunk (no response), repeated. The consecutive counter
    # resets each time, but the total budget must still stop the loop.
    calls = {"n": 0}

    def next_chunk():
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            raise ConnectionError("reset by peer")
        return (None, None)

    request = MagicMock()
    request.next_chunk.side_effect = next_chunk
    with pytest.raises(Exception, match="permanently"):
        up._run_resumable(request)
    failures = (calls["n"] + 1) // 2
    assert failures == yt.MAX_TOTAL_UPLOAD_RETRIES + 1


def test_resumable_returns_response_after_bounded_retries(monkeypatch):
    from autoshorts.uploader import youtube_api as yt

    monkeypatch.setattr(yt.time, "sleep", lambda s: None)
    up = _bare_uploader()
    up.max_retries = 5
    request = MagicMock()
    request.next_chunk.side_effect = [ConnectionError("x"), (None, {"id": "ok"})]
    assert up._run_resumable(request) == {"id": "ok"}


# --- 6. Verifier backoff ---------------------------------------------------------------------


def test_verifier_backoff_is_exponential_and_capped(monkeypatch):
    from autoshorts.uploader import verifier as vf

    sleeps: list[float] = []
    monkeypatch.setattr(vf.time, "sleep", lambda s: sleeps.append(s))
    uploader = MagicMock()
    uploader.verify.return_value = False
    v = vf.YouTubeVerifier(uploader)
    assert v.poll_until_verified("abc123DEF45", max_retries=8, delay_sec=3.0) is False
    assert uploader.verify.call_count == 8
    assert sleeps == [3.0, 6.0, 12.0, 24.0, 48.0, 60.0, 60.0]


def test_verifier_stops_on_success_without_sleeping(monkeypatch):
    from autoshorts.uploader import verifier as vf

    sleeps: list[float] = []
    monkeypatch.setattr(vf.time, "sleep", lambda s: sleeps.append(s))
    uploader = MagicMock()
    uploader.verify.side_effect = [False, True]
    assert vf.YouTubeVerifier(uploader).poll_until_verified("abc123DEF45") is True
    assert sleeps == [3.0]


# --- 7. Long-form cap from config ------------------------------------------------------------


@pytest.fixture
def longform_env(tmp_path: Path, monkeypatch):
    from autoshorts.longform import publish as pub

    counter = tmp_path / "longform_uploads.json"
    monkeypatch.setattr(pub, "_counter_path", lambda: counter)

    uploads: list[dict] = []

    class _FakeLocal:
        def __init__(self, *a, **k):
            pass

        def upload(self, **kwargs):
            uploads.append(kwargs)
            return SimpleNamespace(url="https://example.test/v/1")

    import autoshorts.uploader.local_exporter as le

    monkeypatch.setattr(le, "LocalExportUploader", _FakeLocal)
    return pub, counter, uploads


def _cfg(cap: int):
    upload = SimpleNamespace(
        mode="local",
        longform_daily_cap=cap,
        default_privacy_status="unlisted",
        category_id="27",
    )
    return SimpleNamespace(upload=upload, cleanup=SimpleNamespace(keep_master_mp4=True))


def _result(tmp_path: Path):
    video = tmp_path / "long.mp4"
    video.write_bytes(b"x")
    return SimpleNamespace(
        video_path=str(video),
        title="t",
        description="d",
        hashtags=["a"],
        thumbnail_path=None,
    )


def test_longform_cap_read_from_config(longform_env, tmp_path: Path):
    pub, _counter, uploads = longform_env
    res = _result(tmp_path)
    pub.publish_longform(res, config=_cfg(2))
    pub.publish_longform(res, config=_cfg(2))
    with pytest.raises(RuntimeError, match="cap reached \\(2"):
        pub.publish_longform(res, config=_cfg(2))
    assert len(uploads) == 2


def test_longform_cap_config_overrides_old_hardcoded_value(longform_env, tmp_path: Path):
    pub, _counter, uploads = longform_env
    res = _result(tmp_path)
    for _ in range(5):  # more than the old hard-coded 4, allowed by config cap 20
        pub.publish_longform(res, config=_cfg(20))
    assert len(uploads) == 5


def test_longform_failed_upload_releases_slot(longform_env, tmp_path: Path, monkeypatch):
    pub, counter, _uploads = longform_env
    import autoshorts.uploader.local_exporter as le

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def upload(self, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(le, "LocalExportUploader", _Boom)
    with pytest.raises(RuntimeError, match="network down"):
        pub.publish_longform(_result(tmp_path), config=_cfg(1))
    assert pub.longform_uploads_today() == 0


# --- 8. Rerender keeps planner cuts ----------------------------------------------------------


def test_rerender_keeps_existing_planner_cuts(tmp_path: Path, monkeypatch):
    from autoshorts.core.models import EditDecisionList, JumpCut
    from autoshorts.core.state_manager import StateManager
    from autoshorts.pipeline import rerender as rr

    planner_cut = JumpCut(cut_start=1.0, cut_end=2.0, reason="planner")
    state = StateManager(db_path=tmp_path / "rr.db")
    job = Job(id="job_rr", source_url="https://example.com", status=JobStatus.READY_FOR_REVIEW)
    job.edl = EditDecisionList(jump_cuts=[planner_cut])
    state.save_job(job)

    import autoshorts.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "get_work_dir", lambda: tmp_path / "work")
    (tmp_path / "work" / "job_rr").mkdir(parents=True)
    (tmp_path / "work" / "job_rr" / "aligned_words.json").write_text(
        '[{"word":"a","start":0,"end":1},{"word":"b","start":5,"end":6}]', encoding="utf-8"
    )

    host = SimpleNamespace(state_manager=state)
    out = rr.RerenderMixin.auto_trim_job_silence.__get__(host)("job_rr")
    # The dead-air cut (1.0-5.0s) overlaps the planner cut, so only the planner cut remains.
    assert out["jump_cuts_added"] == 0
    assert [c["reason"] for c in out["jump_cuts"]] == ["planner"]
    assert state.get_job("job_rr").edl.jump_cuts[0].reason == "planner"


def test_rerender_detects_dead_air_when_no_planner_cuts(tmp_path: Path, monkeypatch):
    import autoshorts.pipeline as pipeline_mod
    from autoshorts.core.models import EditDecisionList
    from autoshorts.core.state_manager import StateManager
    from autoshorts.pipeline import rerender as rr

    state = StateManager(db_path=tmp_path / "rr2.db")
    job = Job(id="job_rr2", source_url="https://example.com", status=JobStatus.READY_FOR_REVIEW)
    job.edl = EditDecisionList()
    state.save_job(job)
    monkeypatch.setattr(pipeline_mod, "get_work_dir", lambda: tmp_path / "work")
    (tmp_path / "work" / "job_rr2").mkdir(parents=True)
    (tmp_path / "work" / "job_rr2" / "aligned_words.json").write_text(
        '[{"word":"a","start":0,"end":1},{"word":"b","start":5,"end":6}]', encoding="utf-8"
    )
    host = SimpleNamespace(state_manager=state)
    out = rr.RerenderMixin.auto_trim_job_silence.__get__(host)("job_rr2")
    assert out["jump_cuts_added"] >= 1
    assert "kept_planner_cuts" not in out


# --- 9. optimize-edl response flag -----------------------------------------------------------


def test_optimize_edl_response_declares_rerender_required():
    src = (Path(__file__).resolve().parents[1] / "src/autoshorts/dashboard/app.py").read_text(
        encoding="utf-8"
    )
    start = src.index('@app.post("/api/jobs/{job_id}/optimize-edl")')
    end = src.index('@app.get("/api/jobs/{job_id}/snapshots")', start)
    route = src[start:end]
    assert '"rerender_required": True' in route
