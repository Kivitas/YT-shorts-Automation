"""Compositing-stage wiring: layout resolution, variants, rerender overrides, batch assignment."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from autoshorts.core.exceptions import AutoShortsError
from autoshorts.core.models import (
    ClipCandidate,
    ClipSelection,
    EditDecisionList,
    Job,
    JobStatus,
)
from autoshorts.core.render_spec import (
    CamLayoutSpec,
    RenderFailedError,
    RenderMethod,
    RenderRequest,
    RenderResult,
)
from autoshorts.core.state_manager import StateManager
from autoshorts.pipeline import ShortsPipeline


@pytest.fixture(autouse=True)
def _no_decode_check(monkeypatch):
    monkeypatch.setattr("autoshorts.pipeline.count_decode_errors", lambda *a, **k: 0)


class RecordingBackend:
    def __init__(self, method: RenderMethod, *, fail: bool = False, delay: float = 0.0) -> None:
        self.method = method
        self.fail = fail
        self.delay = delay
        self.requests: list[RenderRequest] = []
        self.finished = threading.Event()

    def is_available(self) -> bool:
        return True

    def render(self, request: RenderRequest) -> RenderResult:
        self.requests.append(request)
        try:
            if self.delay:
                threading.Event().wait(self.delay)
            if self.fail:
                raise RenderFailedError(f"{self.method.value} exploded")
            request.output_path.write_bytes(b"video-" + self.method.value.encode())
            return RenderResult(output_path=request.output_path, method_used=self.method)
        finally:
            self.finished.set()


def _setup(tmp_path: Path, monkeypatch, *, layout: str | None = None, **job_kw: Any):
    work_dir, output_dir = tmp_path / "work", tmp_path / "output"
    work_dir.mkdir()
    output_dir.mkdir()
    monkeypatch.setattr("autoshorts.pipeline.get_work_dir", lambda: work_dir)
    monkeypatch.setattr("autoshorts.pipeline.get_output_dir", lambda: output_dir)
    state_mgr = StateManager(db_path=tmp_path / "s.db")
    job_id = "jobx"
    job_work = work_dir / job_id
    job_work.mkdir()
    raw = job_work / f"{job_id}_raw.mp4"
    raw.write_bytes(b"\x00\x00\x00 raw")
    (job_work / "aligned_words.json").write_text(
        json.dumps([{"word": "HI", "start": 0.0, "end": 0.5, "probability": 1.0}]),
        encoding="utf-8",
    )
    cand = ClipCandidate(start=0.0, end=30.0, text="HI", score=0.9)
    sel = ClipSelection(
        candidate=cand, clip_index=0, target_duration=30.0, background_style="minecraft"
    )
    edl = EditDecisionList(hook_in_sec=0.0, hook_out_sec=30.0, recommended_title="T")
    job = Job(
        id=job_id,
        source_url="https://youtube.com/watch?v=abc1234567a",
        status=JobStatus.READY_FOR_REVIEW,
        raw_video_path=str(raw),
        candidate_windows=[cand],
        selected_clips=[sel],
        edl=edl,
        **job_kw,
    )
    if layout:
        job.layout_mode = layout
    state_mgr.save_job(job)
    pipe = ShortsPipeline(
        state_manager=state_mgr, compositor=MagicMock(), transcriber=MagicMock(), concurrency=1
    )
    return pipe, state_mgr, output_dir


def _inject(pipe: ShortsPipeline, **backends: RecordingBackend) -> None:
    pipe.render_backends = {RenderMethod(k): v for k, v in backends.items()}


def test_cam_gameplay_without_camera_region_falls_back_and_selects_background(
    tmp_path: Path, monkeypatch
) -> None:
    pipe, _, _ = _setup(tmp_path, monkeypatch, layout="cam_gameplay")
    bg_calls: list[str] = []
    monkeypatch.setattr(
        "autoshorts.pipeline.select_gameplay_background",
        lambda category: (bg_calls.append(category), (tmp_path / "bg.mp4", 3.0))[1],
        raising=False,
    )
    monkeypatch.setattr("autoshorts.pipeline.stages_media.resolve_cam_layout", lambda *a, **k: None)
    ff = RecordingBackend(RenderMethod.FFMPEG)
    _inject(pipe, ffmpeg=ff)
    job = pipe.rerender_job("jobx", render_method="ffmpeg")
    req = ff.requests[0]
    assert req.layout_mode == "split_50_50" and req.cam_layout is None
    assert req.background_video == tmp_path / "bg.mp4" and bg_calls
    assert job.layout_mode == "split_50_50"  # stored layout == rendered layout
    assert job.diagnostics["render_layout"] == {
        "requested": "cam_gameplay",
        "final": "split_50_50",
        "cam_fallback": True,
    }


def test_cam_gameplay_with_region_keeps_layout_and_needs_no_background(
    tmp_path: Path, monkeypatch
) -> None:
    pipe, _, _ = _setup(tmp_path, monkeypatch, layout="cam_gameplay")
    spec = CamLayoutSpec(cam_rect=(0.7, 0.0, 0.3, 0.3))
    monkeypatch.setattr("autoshorts.pipeline.stages_media.resolve_cam_layout", lambda *a, **k: spec)
    bg = MagicMock(side_effect=AssertionError("no background expected"))
    monkeypatch.setattr("autoshorts.pipeline.select_gameplay_background", bg, raising=False)
    ff = RecordingBackend(RenderMethod.FFMPEG)
    _inject(pipe, ffmpeg=ff)
    job = pipe.rerender_job("jobx", render_method="ffmpeg")
    req = ff.requests[0]
    assert req.layout_mode == "cam_gameplay" and req.cam_layout == spec
    assert req.background_video is None
    assert job.layout_mode == "cam_gameplay"
    assert job.diagnostics["render_layout"]["cam_fallback"] is False


def test_variants_are_named_for_cleanup_and_primary_is_not_copied(
    tmp_path: Path, monkeypatch
) -> None:
    pipe, _, out = _setup(
        tmp_path, monkeypatch, render_method="ffmpeg", render_variants=["ffmpeg", "raea"]
    )
    ff, raea = RecordingBackend(RenderMethod.FFMPEG), RecordingBackend(RenderMethod.RAEA)
    _inject(pipe, ffmpeg=ff, raea=raea)
    job = pipe.rerender_job("jobx")
    assert (out / "jobx_master_raea.mp4").read_bytes() == b"video-raea"
    assert not (out / "jobx").exists()  # no per-job folder cleanup would never visit
    variants = job.diagnostics["render_variants"]
    assert variants["raea"]["path"] == str(out / "jobx_master_raea.mp4")
    assert variants["ffmpeg"]["primary"] is True
    assert variants["ffmpeg"]["path"] == job.rendered_video_path
    assert not (out / "jobx_master_ffmpeg.mp4").exists()


def test_failed_primary_still_joins_variant_threads(tmp_path: Path, monkeypatch) -> None:
    pipe, _, _ = _setup(
        tmp_path, monkeypatch, render_method="ffmpeg", render_variants=["ffmpeg", "raea"]
    )
    ff = RecordingBackend(RenderMethod.FFMPEG, fail=True)
    raea = RecordingBackend(RenderMethod.RAEA, delay=0.15)
    _inject(pipe, ffmpeg=ff, raea=raea)
    pipe.config.render.fallback = False
    with pytest.raises(RenderFailedError):
        pipe.rerender_job("jobx")
    assert raea.finished.is_set(), "variant render must be finished before the job fails"


def test_rerender_rejects_unknown_layout_and_method_without_touching_job(
    tmp_path: Path, monkeypatch
) -> None:
    pipe, store, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(AutoShortsError, match="layout_mode"):
        pipe.rerender_job("jobx", layout_mode="sideways")
    with pytest.raises(AutoShortsError, match="Unknown render method"):
        pipe.rerender_job("jobx", render_method="gpu")
    assert store.get_job("jobx").layout_mode == "split_50_50"


def test_rerender_render_method_override_is_applied(tmp_path: Path, monkeypatch) -> None:
    pipe, _, _ = _setup(
        tmp_path, monkeypatch, render_method="raea", render_variants=["raea", "ffmpeg"]
    )
    ff = RecordingBackend(RenderMethod.FFMPEG)
    _inject(pipe, ffmpeg=ff)
    job = pipe.rerender_job("jobx", render_method="FFmpeg")
    assert job.render_method == "ffmpeg" and job.render_variants == []
    assert job.diagnostics["render_dispatch"]["method_used"] == "ffmpeg"


def test_rerender_atomic_replace_keeps_previous_master_when_everything_fails(
    tmp_path: Path, monkeypatch
) -> None:
    pipe, _, out = _setup(tmp_path, monkeypatch)
    master = out / "jobx_master.mp4"
    master.write_bytes(b"previous master")
    _inject(pipe, ffmpeg=RecordingBackend(RenderMethod.FFMPEG, fail=True))
    with pytest.raises(RenderFailedError):
        pipe.rerender_job("jobx", render_method="ffmpeg")
    assert master.read_bytes() == b"previous master"
    assert [p.name for p in out.iterdir() if "rendering" in p.name] == []
