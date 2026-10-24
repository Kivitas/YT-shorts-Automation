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
from autoshorts.core.models import Job, JobStatus
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
        "cfg_file": cfg_file,
        "work_dir": work_dir,
        "assets_dir": assets_dir,
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
