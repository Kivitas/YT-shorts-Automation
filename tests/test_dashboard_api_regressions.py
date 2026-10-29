"""Regression tests for dashboard REST endpoints found during live API testing.

Each test pins one bug that was reproduced against the running server:
- pacing-audit / snapshots returned 500 (AppConfig has no work_dir)
- spark import returned 500 for unparseable text (should be 400)
- faceless create returned 500 for malformed bodies (should be 400/422)
- transcript PUT accepted non-word items and corrupted aligned_words.json
- music download accepted unknown track ids and wrote synthesized audio files
- retry accepted jobs that are not FAILED (would reprocess finished outputs)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from autoshorts.core.config import load_config
from autoshorts.core.exceptions import InvalidURLError
from autoshorts.core.models import ClipCandidate, ClipSelection, Job, JobStatus
from autoshorts.core.parallel_runner import ParallelJobExecutor
from autoshorts.core.state_manager import StateManager
from autoshorts.dashboard import app as dashboard_app
from autoshorts.dashboard.app import create_app
from autoshorts.quota.quota_tracker import QuotaTracker

BASE_URL = "http://localhost:8765"


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Dashboard app with a real StateManager and a mocked pipeline, sandboxed to tmp_path."""
    state_mgr = StateManager(db_path=tmp_path / "regress.db")

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "config.yaml"
    cfg_file.write_text(
        """
app:
  name: "AutoShorts-OnDevice"
  version: "1.0.0"
llm:
  provider: "local"
  model: null
scheduler:
  enabled: true
upload:
  mode: "api"
  privacy_status: "unlisted"
""",
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True)
    assets_dir = tmp_path / "assets"

    monkeypatch.setattr("autoshorts.core.paths.get_data_dir", lambda: data_dir)
    monkeypatch.setattr("autoshorts.core.paths.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.core.settings_manager.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("autoshorts.core.settings_manager.get_config_dir", lambda: cfg_dir)
    monkeypatch.setattr("autoshorts.core.settings_manager.get_data_dir", lambda: data_dir)
    monkeypatch.setattr("autoshorts.core.config.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("autoshorts.core.config.get_config_dir", lambda: cfg_dir)
    monkeypatch.setattr(dashboard_app, "get_assets_dir", lambda: assets_dir)

    cfg = load_config(cfg_file)
    pipeline = MagicMock()
    pipeline.config = cfg
    pipeline.state_manager = state_mgr
    pipeline.quota_tracker = QuotaTracker(state_manager=state_mgr)
    pipeline.scheduler = MagicMock()
    pipeline.scheduler.is_paused.return_value = False
    pipeline.cleaner = MagicMock()

    app = create_app(
        pipeline=pipeline,
        state_manager=state_mgr,
        config=cfg,
        run_scheduler_worker=False,
    )
    client = TestClient(app, base_url=BASE_URL)
    return {
        "client": client,
        "state_mgr": state_mgr,
        "pipeline": pipeline,
        "cfg_file": cfg_file,
        "work_dir": work_dir,
        "assets_dir": assets_dir,
        "tmp_path": tmp_path,
    }


def _save_job(state_mgr: StateManager, job_id: str, status: JobStatus) -> None:
    state_mgr.save_job(
        Job(
            id=job_id,
            source_url="https://youtube.com/watch?v=regress12345",
            status=status,
        )
    )


def test_pacing_audit_and_snapshots_resolve_work_dir(api: dict[str, Any]) -> None:
    """Both endpoints returned 500 because _resolve_job_work_dir read a missing AppConfig.work_dir."""
    job_id = "job_pacing_123"
    _save_job(api["state_mgr"], job_id, JobStatus.SUBTITLES_GENERATED)
    job_dir = api["work_dir"] / job_id
    job_dir.mkdir(parents=True)
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.4, "probability": 0.99},
        {"word": "world", "start": 0.4, "end": 0.9, "probability": 0.99},
    ]
    (job_dir / "aligned_words.json").write_text(json.dumps(words), encoding="utf-8")

    client: TestClient = api["client"]
    audit = client.get(f"/api/jobs/{job_id}/pacing-audit")
    assert audit.status_code == 200, audit.text
    assert audit.json()["audit"]["word_count"] == 2

    snaps = client.get(f"/api/jobs/{job_id}/snapshots")
    assert snaps.status_code == 200, snaps.text
    assert snaps.json()["snapshots"] == []


def test_spark_import_unparseable_text_returns_400(api: dict[str, Any]) -> None:
    client: TestClient = api["client"]
    res = client.post("/api/spark/import", json={"text": "no youtube tasks in this text at all"})
    assert res.status_code == 400
    assert "Could not parse" in res.json()["detail"]


def test_faceless_create_rejects_malformed_bodies(api: dict[str, Any]) -> None:
    client: TestClient = api["client"]

    not_json = client.post(
        "/api/faceless/create",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert not_json.status_code == 400

    not_object = client.post("/api/faceless/create", json=["a", "b"])
    assert not_object.status_code == 400

    bad_field = client.post("/api/faceless/create", json={"duration_target_sec": "soon"})
    assert bad_field.status_code == 422
    assert bad_field.json()["detail"]


def test_transcript_put_rejects_non_word_items_and_keeps_file(api: dict[str, Any]) -> None:
    """Items without a string word or numeric start/end were saved, breaking later renders."""
    job_id = "job_transcript_guard"
    _save_job(api["state_mgr"], job_id, JobStatus.SUBTITLES_GENERATED)
    client: TestClient = api["client"]

    good = [{"word": "Welcome", "start": 0.0, "end": 0.4}]
    assert client.put(f"/api/jobs/{job_id}/transcript", json={"words": good}).status_code == 200

    for bad in ([1, "x"], [{"start": 0.0, "end": 0.4}], [{"word": "a", "start": "0", "end": 1}]):
        res = client.put(f"/api/jobs/{job_id}/transcript", json={"words": bad})
        assert res.status_code == 400, bad

    stored = json.loads((api["work_dir"] / job_id / "aligned_words.json").read_text("utf-8"))
    assert stored == good


def test_transcript_put_accepts_frontend_word_shape_with_timings(api: dict[str, Any]) -> None:
    job_id = "job_transcript_timed"
    _save_job(api["state_mgr"], job_id, JobStatus.SUBTITLES_GENERATED)
    client: TestClient = api["client"]
    words = [{"word": " Okay", "start": 1.5, "end": 1.9, "probability": 0.8, "emphasis": True}]
    res = client.put(f"/api/jobs/{job_id}/transcript", json={"words": words})
    assert res.status_code == 200


def test_music_download_rejects_unsafe_or_unknown_track_id(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown ids were synthesized into assets/music/ and returned success; path-like ids escape it."""
    from autoshorts.research_agent import song_recommender

    calls: list[str] = []

    def fake_download(track_id: str, custom_url: str | None = None, **_: Any) -> dict[str, Any]:
        calls.append(track_id)
        return {"success": True, "track_id": track_id}

    monkeypatch.setattr(song_recommender, "download_viral_bgm", fake_download)
    client: TestClient = api["client"]

    for bad_id in ("../evil", "a/b", "x" * 65, "has space"):
        res = client.post("/api/research/music/download", json={"track_id": bad_id})
        assert res.status_code == 400, bad_id

    unknown = client.post("/api/research/music/download", json={"track_id": "__nope__"})
    assert unknown.status_code == 400
    assert calls == []

    known_id = song_recommender.CURATED_VIRAL_BGM[0]["track_id"]
    ok = client.post("/api/research/music/download", json={"track_id": known_id})
    assert ok.status_code == 200
    assert calls == [known_id]


def test_retry_only_accepts_failed_jobs(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrying a READY_FOR_REVIEW job would reprocess its finished outputs."""
    submitted: list[str] = []
    monkeypatch.setattr(
        ParallelJobExecutor,
        "submit_job",
        lambda self, job_or_id, *a, **k: submitted.append(str(job_or_id)),
    )
    state_mgr: StateManager = api["state_mgr"]
    _save_job(state_mgr, "job_ready_001", JobStatus.READY_FOR_REVIEW)
    _save_job(state_mgr, "job_failed_001", JobStatus.FAILED)
    client: TestClient = api["client"]

    blocked = client.post("/api/jobs/job_ready_001/retry")
    assert blocked.status_code == 409
    assert "READY_FOR_REVIEW" in blocked.json()["detail"]
    assert submitted == []

    ok = client.post("/api/jobs/job_failed_001/retry")
    assert ok.status_code == 200
    assert submitted == ["job_failed_001"]


def _save_job_with_clip(state_mgr: StateManager, job_id: str, status: JobStatus) -> None:
    cand = ClipCandidate(start=0.0, end=40.0, text="hello world again", score=0.9)
    state_mgr.save_job(
        Job(
            id=job_id,
            source_url="https://youtube.com/watch?v=regress12345",
            status=status,
            selected_clips=[ClipSelection(candidate=cand, clip_index=0)],
        )
    )


def test_pipeline_handoff_invalid_link_returns_400(api: dict[str, Any]) -> None:
    """submit_links raises InvalidURLError for bad links; the handoff route returned 500."""
    api["pipeline"].submit_links.side_effect = InvalidURLError("not a YouTube link")
    client: TestClient = api["client"]
    res = client.post("/api/research/pipeline-handoff", json={"url": "https://example.com/x"})
    assert res.status_code == 400, res.text
    assert "not a YouTube link" in res.json()["detail"]


def test_patch_long_title_on_job_without_edl_is_truncated(api: dict[str, Any]) -> None:
    """EditDecisionList caps recommended_title at 60 and recommended_hook at 100; longer input gave 500."""
    job_id = "job_long_title"
    _save_job(api["state_mgr"], job_id, JobStatus.READY_FOR_REVIEW)
    client: TestClient = api["client"]
    res = client.patch(f"/api/jobs/{job_id}", json={"clip_title": "T" * 80, "hook_text": "H" * 120})
    assert res.status_code == 200, res.text
    stored = api["state_mgr"].get_job(job_id)
    assert stored is not None and stored.edl is not None
    assert len(stored.edl.recommended_title) == 60
    assert len(stored.edl.recommended_hook) == 100


def test_pacing_audit_and_optimize_work_for_job_with_selected_clip(api: dict[str, Any]) -> None:
    """ClipSelection has no duration attribute, so both endpoints raised AttributeError (500)."""
    job_id = "job_pacing_clip"
    _save_job_with_clip(api["state_mgr"], job_id, JobStatus.SUBTITLES_GENERATED)
    job_dir = api["work_dir"] / job_id
    job_dir.mkdir(parents=True)
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.4, "probability": 0.99},
        {"word": "world", "start": 0.4, "end": 0.9, "probability": 0.99},
    ]
    (job_dir / "aligned_words.json").write_text(json.dumps(words), encoding="utf-8")
    client: TestClient = api["client"]

    audit = client.get(f"/api/jobs/{job_id}/pacing-audit")
    assert audit.status_code == 200, audit.text
    assert audit.json()["audit"]["word_count"] == 2

    optimized = client.post(f"/api/jobs/{job_id}/optimize-edl")
    assert optimized.status_code == 200, optimized.text


def test_trim_rejects_non_finite_times_and_keeps_job(api: dict[str, Any]) -> None:
    """Python's json accepts NaN/Infinity; NaN passed the range checks and broke the JSON response."""
    job_id = "job_trim_nan"
    _save_job_with_clip(api["state_mgr"], job_id, JobStatus.READY_FOR_REVIEW)
    client: TestClient = api["client"]
    headers = {"Content-Type": "application/json"}
    for body in (
        b'{"start_sec": NaN, "end_sec": 5}',
        b'{"start_sec": 0, "end_sec": NaN}',
        b'{"start_sec": -Infinity, "end_sec": 5}',
    ):
        res = client.post(f"/api/jobs/{job_id}/trim", content=body, headers=headers)
        assert res.status_code == 400, body
    stored = api["state_mgr"].get_job(job_id)
    assert stored is not None
    assert stored.selected_clips[0].candidate.end == 40.0


def test_malformed_json_bodies_return_400_not_500(api: dict[str, Any]) -> None:
    client: TestClient = api["client"]
    headers = {"Content-Type": "application/json"}

    assert (
        client.post("/api/brain/orchestrate", content=b"{not json", headers=headers).status_code
        == 400
    )
    assert (
        client.post("/api/brain/orchestrate", content=b"[1, 2]", headers=headers).status_code == 400
    )
    assert (
        client.post("/api/brain/critic/evaluate", content=b"{not json", headers=headers).status_code
        == 400
    )
    assert (
        client.post(
            "/api/brain/critic/evaluate",
            content=b'{"mode": "mode_a", "start_sec": "soon"}',
            headers=headers,
        ).status_code
        == 400
    )

    _save_job(api["state_mgr"], "job_restore_json", JobStatus.READY_FOR_REVIEW)
    restore_url = "/api/jobs/job_restore_json/snapshots/restore"
    assert client.post(restore_url, content=b"{bad", headers=headers).status_code == 400
    assert client.post(restore_url, content=b"[1]", headers=headers).status_code == 400


def test_restore_snapshot_rejects_bad_ids_and_reports_missing(api: dict[str, Any]) -> None:
    _save_job(api["state_mgr"], "job_restore_ids", JobStatus.READY_FOR_REVIEW)
    client: TestClient = api["client"]
    restore_url = "/api/jobs/job_restore_ids/snapshots/restore"

    traversal = client.post(restore_url, json={"snapshot_id": "../../escape"})
    assert traversal.status_code == 400
    missing = client.post(restore_url, json={"snapshot_id": "snap_missing_1"})
    assert missing.status_code == 404, missing.text


def test_transcript_rejects_job_ids_that_are_not_work_dir_names(api: dict[str, Any]) -> None:
    """PUT created work/<any id> directories and wrote files there without checking the job."""
    client: TestClient = api["client"]
    words = [{"word": "x", "start": 0.0, "end": 0.5}]
    res = client.put("/api/jobs/bad.id/transcript", json={"words": words})
    assert res.status_code == 400, res.text
    assert not (api["work_dir"] / "bad.id").exists()
    assert client.get("/api/jobs/bad.id/transcript").status_code == 400


def test_background_download_rejects_path_like_categories(
    api: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Categories went straight into the assets path, so '../' could create folders outside it."""
    from autoshorts.media import background_fetcher

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        background_fetcher,
        "download_preset_backgrounds",
        lambda **kwargs: calls.append(kwargs) or {},
    )
    client: TestClient = api["client"]
    res = client.post("/api/backgrounds/download", json={"categories": ["../../escape"]})
    assert res.status_code == 400, res.text
    assert calls == []


def test_log_stream_records_each_message_once() -> None:
    """The stream handler was attached to both root and 'autoshorts', so every line appeared twice."""
    import logging

    from autoshorts.core.logging_setup import get_log_stream_handler

    handler = get_log_stream_handler()
    handler.clear()
    logging.getLogger("autoshorts.regress.dup").warning("dup-check-%s", "marker")
    lines = [m for m in handler.get_history() if "dup-check-marker" in m]
    assert len(lines) == 1
