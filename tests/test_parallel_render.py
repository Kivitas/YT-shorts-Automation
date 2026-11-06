"""Tests for parallel multi-method rendering: limits, strategies, isolation, CLI and API."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from autoshorts.cli import build_parser
from autoshorts.core.config import Settings
from autoshorts.core.models import Job
from autoshorts.core.render_spec import (
    RenderFailedError,
    RenderMethod,
    RenderRequest,
    RenderResult,
)
from autoshorts.core.state_manager import StateManager
from autoshorts.dashboard.app import create_app
from autoshorts.media.render_dispatch import MethodLimiter, render_with_fallback
from autoshorts.pipeline.orchestrator import ShortsPipeline
from autoshorts.pipeline.stages_media import MediaMixin

TIMEOUT = 2.0


class GateBackend:
    """Backend that tracks concurrency and rendezvouses on an optional barrier."""

    def __init__(self, method: RenderMethod, barrier: threading.Barrier | None = None) -> None:
        self.method = method
        self.barrier = barrier
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.fail = False

    def is_available(self) -> bool:
        return True

    def render(self, request: RenderRequest) -> RenderResult:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.barrier is not None:
                self.barrier.wait(TIMEOUT)
            else:
                threading.Event().wait(0.03)
            if self.fail:
                raise RenderFailedError("planned failure")
            request.output_path.write_bytes(b"v")
            return RenderResult(output_path=request.output_path, method_used=self.method)
        finally:
            with self.lock:
                self.active -= 1


def _req(tmp_path: Path, name: str) -> RenderRequest:
    return RenderRequest(
        job_id=name, source_video=tmp_path / "s.mp4", output_path=tmp_path / f"{name}.mp4"
    )


def _run_threads(fns: list[Any]) -> list[BaseException | None]:
    errors: list[BaseException | None] = [None] * len(fns)

    def wrap(i: int, fn: Any) -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the asserting test
            errors[i] = exc

    threads = [threading.Thread(target=wrap, args=(i, fn)) for i, fn in enumerate(fns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
        assert not t.is_alive(), "render thread hung (possible deadlock)"
    return errors


def test_heavy_method_is_limited_to_one_at_a_time(tmp_path: Path) -> None:
    raea = GateBackend(RenderMethod.RAEA)
    limiter = MethodLimiter({"raea": 1})
    backends = {RenderMethod.RAEA: raea}
    errs = _run_threads(
        [
            lambda i=i: render_with_fallback(
                _req(tmp_path, f"r{i}"),
                RenderMethod.RAEA,
                backends,
                fallback=False,
                limiter=limiter,
            )
            for i in range(4)
        ]
    )
    assert errs == [None] * 4
    assert raea.max_active == 1


def test_ffmpeg_allows_two_concurrent_renders_but_not_three(tmp_path: Path) -> None:
    # Barrier(2) only trips when two renders are inside at once, proving real parallelism.
    ff = GateBackend(RenderMethod.FFMPEG, barrier=threading.Barrier(2))
    limiter = MethodLimiter({"ffmpeg": 2})
    backends = {RenderMethod.FFMPEG: ff}
    errs = _run_threads(
        [
            lambda i=i: render_with_fallback(
                _req(tmp_path, f"f{i}"),
                RenderMethod.FFMPEG,
                backends,
                fallback=False,
                limiter=limiter,
            )
            for i in range(4)
        ]
    )
    assert errs == [None] * 4
    assert ff.max_active == 2


def test_different_methods_render_at_the_same_time(tmp_path: Path) -> None:
    barrier = threading.Barrier(3)
    backends = {
        RenderMethod.RAEA: GateBackend(RenderMethod.RAEA, barrier),
        RenderMethod.REMOTION: GateBackend(RenderMethod.REMOTION, barrier),
        RenderMethod.FFMPEG: GateBackend(RenderMethod.FFMPEG, barrier),
    }
    limiter = MethodLimiter()  # raea:1, remotion:1, ffmpeg:2
    errs = _run_threads(
        [
            lambda m=m: render_with_fallback(
                _req(tmp_path, m.value), m, backends, fallback=False, limiter=limiter
            )
            for m in backends
        ]
    )
    # All three were inside render() together (otherwise the barrier would time out).
    assert errs == [None] * 3


def test_fallback_releases_method_slot_before_next_method(tmp_path: Path) -> None:
    """A failing raea must free its slot so other raea jobs are not blocked forever."""
    raea = GateBackend(RenderMethod.RAEA)
    raea.fail = True
    ff = GateBackend(RenderMethod.FFMPEG)
    backends = {RenderMethod.RAEA: raea, RenderMethod.FFMPEG: ff}
    limiter = MethodLimiter({"raea": 1, "ffmpeg": 1})
    results: list[RenderResult] = []
    errs = _run_threads(
        [
            lambda i=i: results.append(
                render_with_fallback(
                    _req(tmp_path, f"d{i}"), RenderMethod.AUTO, backends, limiter=limiter
                )
            )
            for i in range(4)
        ]
    )
    assert errs == [None] * 4
    assert {r.method_used for r in results} == {RenderMethod.FFMPEG}
    assert raea.max_active == 1 and ff.max_active == 1


def test_one_failing_method_does_not_sink_the_others(tmp_path: Path) -> None:
    bad = GateBackend(RenderMethod.RAEA)
    bad.fail = True
    good = GateBackend(RenderMethod.FFMPEG)
    backends = {RenderMethod.RAEA: bad, RenderMethod.FFMPEG: good}
    outcomes: dict[str, Any] = {}

    def run(name: str, method: RenderMethod) -> None:
        try:
            outcomes[name] = render_with_fallback(
                _req(tmp_path, name), method, backends, fallback=False
            )
        except RenderFailedError as exc:
            outcomes[name] = exc

    _run_threads([lambda: run("a", RenderMethod.RAEA), lambda: run("b", RenderMethod.FFMPEG)])
    assert isinstance(outcomes["a"], RenderFailedError)
    assert outcomes["b"].method_used == RenderMethod.FFMPEG


# ------------------------------------------------------------------ strategies


def _stub_pipeline(config: Settings | None = None) -> SimpleNamespace:
    return SimpleNamespace(config=config or Settings(), state_manager=MagicMock())


def _jobs(n: int) -> list[Job]:
    return [Job(id=f"job{i}", source_url=f"https://youtu.be/v{i}") for i in range(n)]


def test_default_batch_assignment_is_a_noop() -> None:
    jobs = _jobs(3)
    ShortsPipeline._assign_render_methods(_stub_pipeline(), jobs, None, None, None)
    assert all(j.render_method is None and j.render_variants == [] for j in jobs)


def test_round_robin_assigns_one_method_per_job() -> None:
    jobs = _jobs(5)
    stub = _stub_pipeline()
    ShortsPipeline._assign_render_methods(stub, jobs, None, "raea,ffmpeg,remotion", "round_robin")
    assert [j.render_method for j in jobs] == ["raea", "ffmpeg", "remotion", "raea", "ffmpeg"]
    assert all(j.render_variants == [] for j in jobs)
    assert jobs[0].diagnostics["render_plan"]["strategy"] == "round_robin"
    assert stub.state_manager.save_job.call_count == 5


def test_variants_marks_every_method_and_first_is_primary() -> None:
    jobs = _jobs(2)
    ShortsPipeline._assign_render_methods(
        _stub_pipeline(), jobs, None, ["remotion", "ffmpeg"], "variants"
    )
    for j in jobs:
        assert j.render_method == "remotion"
        assert j.render_variants == ["remotion", "ffmpeg"]


def test_single_with_explicit_method_and_config_driven_strategy() -> None:
    jobs = _jobs(2)
    ShortsPipeline._assign_render_methods(_stub_pipeline(), jobs, "ffmpeg", None, None)
    assert [j.render_method for j in jobs] == ["ffmpeg", "ffmpeg"]

    cfg = Settings()
    cfg.render.methods = ["raea", "ffmpeg"]
    cfg.render.strategy = "round_robin"
    jobs = _jobs(3)
    ShortsPipeline._assign_render_methods(_stub_pipeline(cfg), jobs, None, None, None)
    assert [j.render_method for j in jobs] == ["raea", "ffmpeg", "raea"]


def test_unknown_method_or_strategy_is_rejected() -> None:
    with pytest.raises(ValueError):
        ShortsPipeline._assign_render_methods(_stub_pipeline(), _jobs(1), None, "bogus", "single")
    with pytest.raises(Exception, match="strategy"):
        ShortsPipeline._assign_render_methods(_stub_pipeline(), _jobs(1), "ffmpeg", None, "x")


def test_variants_render_each_method_in_parallel_into_job_folder(tmp_path: Path) -> None:
    barrier = threading.Barrier(2)  # proves the two extra variants render concurrently
    backends = {
        RenderMethod.RAEA: GateBackend(RenderMethod.RAEA, barrier),
        RenderMethod.REMOTION: GateBackend(RenderMethod.REMOTION, barrier),
    }
    dispatch_log: list[tuple[str, bool]] = []

    def dispatch(request: RenderRequest, method: RenderMethod, fallback: bool) -> RenderResult:
        dispatch_log.append((method.value, fallback))
        return render_with_fallback(request, method, backends, fallback=fallback)

    stub = SimpleNamespace(_dispatch_render=dispatch)
    job = Job(id="vjob", source_url="https://youtu.be/x")
    job.render_method = "ffmpeg"
    job.render_variants = ["ffmpeg", "raea", "remotion"]
    req = RenderRequest(
        job_id="vjob",
        source_video=tmp_path / "s.mp4",
        output_path=tmp_path / "vjob_master.mp4",
        extras={"compose_kwargs": {"job_id": "vjob"}},
    )
    out = MediaMixin._render_variants(stub, job, req, tmp_path)  # type: ignore[arg-type]
    assert set(out) == {"raea", "remotion"}
    assert out["raea"]["ok"] and out["raea"]["path"] == str(tmp_path / "vjob" / "raea.mp4")
    assert (tmp_path / "vjob" / "remotion.mp4").is_file()
    assert out["remotion"]["method_used"] == "remotion"
    assert all(fallback is False for _, fallback in dispatch_log)  # strict per-method files


def test_variant_failure_is_recorded_without_raising(tmp_path: Path) -> None:
    bad = GateBackend(RenderMethod.RAEA)
    bad.fail = True
    backends = {RenderMethod.RAEA: bad, RenderMethod.REMOTION: GateBackend(RenderMethod.REMOTION)}

    def dispatch(request: RenderRequest, method: RenderMethod, fallback: bool) -> RenderResult:
        return render_with_fallback(request, method, backends, fallback=fallback)

    job = Job(id="vj2", source_url="https://youtu.be/y")
    job.render_variants = ["ffmpeg", "raea", "remotion"]
    req = RenderRequest(
        job_id="vj2", source_video=tmp_path / "s.mp4", output_path=tmp_path / "m.mp4"
    )
    out = MediaMixin._render_variants(
        SimpleNamespace(_dispatch_render=dispatch), job, req, tmp_path
    )  # type: ignore[arg-type]
    assert out["raea"]["ok"] is False and "planned failure" in out["raea"]["error"]
    assert out["remotion"]["ok"] is True


def test_batch_failure_of_one_job_does_not_stop_others() -> None:
    jobs = _jobs(4)
    seen: list[str] = []
    lock = threading.Lock()
    store = MagicMock()
    store.get_job.side_effect = lambda jid: next(j for j in jobs if j.id == jid)

    def process_job(job: Job, **_kw: Any) -> Job:
        with lock:
            seen.append(job.id)
        if job.id == "job1":
            raise RuntimeError("render exploded")
        return job

    pipe = ShortsPipeline.__new__(ShortsPipeline)
    pipe.config = Settings()
    pipe.state_manager = store
    pipe.concurrency = 3
    pipe.process_job = process_job  # type: ignore[method-assign]
    results = pipe.process_batch(jobs, render_strategy="round_robin", render_methods="raea,ffmpeg")
    assert sorted(seen) == ["job0", "job1", "job2", "job3"]
    assert len(results) == 4
    assert [j.render_method for j in jobs] == ["raea", "ffmpeg", "raea", "ffmpeg"]


# ------------------------------------------------------------------ CLI


def test_cli_run_parses_render_flags() -> None:
    args = build_parser().parse_args(
        [
            "run",
            "--links",
            "https://youtu.be/abc",
            "--render-method",
            "raea",
            "--render-methods",
            "raea,remotion,ffmpeg",
            "--render-strategy",
            "variants",
            "--layout",
            "cam_gameplay",
            "--parallel",
            "4",
        ]
    )
    assert args.render_method == "raea"
    assert args.render_methods == ["raea", "remotion", "ffmpeg"]
    assert args.render_strategy == "variants"
    assert args.layout == "cam_gameplay"
    assert args.parallel == 4


def test_cli_run_render_flags_default_to_none_and_reject_bad_values() -> None:
    args = build_parser().parse_args(["run", "--links", "x"])
    assert args.render_method is None and args.render_methods is None
    assert args.render_strategy is None and args.layout is None and args.parallel is None
    for bad in (
        ["--render-method", "gpu"],
        ["--render-methods", "raea,nope"],
        ["--render-strategy", "chaos"],
        ["--parallel", "0"],
        ["--layout", "weird"],
    ):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["run", "--links", "x", *bad])


# ------------------------------------------------------------------ dashboard


@pytest.fixture
def client_and_pipe(tmp_path: Path):
    state_mgr = StateManager(db_path=tmp_path / "d.db")
    pipe = MagicMock()
    pipe.state_manager = state_mgr
    job = Job(id="rj1", source_url="https://youtube.com/watch?v=1234567890a")
    state_mgr.save_job(job)
    pipe.submit_links.return_value = [job]
    app = create_app(pipeline=pipe, state_manager=state_mgr)
    return TestClient(app, base_url="http://localhost:8765"), pipe


def test_dashboard_submit_accepts_render_fields(client_and_pipe) -> None:
    client, pipe = client_and_pipe
    res = client.post(
        "/api/jobs/submit",
        json={
            "links": "https://youtube.com/watch?v=1234567890a",
            "render_method": "raea",
            "render_methods": "raea, ffmpeg",
            "render_strategy": "round_robin",
            "layout_mode": "cam_gameplay",
        },
    )
    assert res.status_code == 200, res.text
    args = pipe._assign_render_methods.call_args.args
    assert args[1] == "raea"
    assert args[2] == ["raea", "ffmpeg"]
    assert args[3] == "round_robin"
    assert res.json()["jobs"][0]["layout_mode"] == "cam_gameplay"


@pytest.mark.parametrize(
    "extra",
    [
        {"render_method": "gpu"},
        {"render_methods": ["raea", "nope"]},
        {"render_strategy": "chaos"},
    ],
)
def test_dashboard_submit_rejects_invalid_render_fields(client_and_pipe, extra) -> None:
    client, pipe = client_and_pipe
    res = client.post(
        "/api/jobs/submit",
        json={"links": "https://youtube.com/watch?v=1234567890a", **extra},
    )
    assert res.status_code == 400
    pipe.submit_links.assert_not_called()


def test_dashboard_submit_without_render_fields_does_not_assign(client_and_pipe) -> None:
    client, pipe = client_and_pipe
    res = client.post("/api/jobs/submit", json={"links": "https://youtube.com/watch?v=1234567890a"})
    assert res.status_code == 200
    pipe._assign_render_methods.assert_not_called()
