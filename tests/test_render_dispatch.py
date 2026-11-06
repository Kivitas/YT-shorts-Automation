"""Tests for the render dispatcher: fallback chain, explicit methods, adapters, config."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from autoshorts.core.config import RenderConfig, Settings
from autoshorts.core.render_spec import (
    FALLBACK_CHAIN,
    CamLayoutSpec,
    RenderFailedError,
    RenderMethod,
    RenderRequest,
    RenderResult,
    RenderUnavailableError,
)
from autoshorts.media.render_dispatch import (
    FfmpegBackend,
    MethodLimiter,
    RemotionBackend,
    RenderExhaustedError,
    assign_methods,
    load_raea_backend,
    ordered_chain,
    parse_methods,
    render_with_fallback,
)


class FakeBackend:
    """Configurable stand-in: behaviour is 'ok', 'fail', 'unavailable', 'empty' or 'raise'."""

    def __init__(self, method: RenderMethod, behaviour: str = "ok") -> None:
        self.method = method
        self.behaviour = behaviour
        self.calls = 0

    def is_available(self) -> bool:
        return self.behaviour != "unavailable"

    def render(self, request: RenderRequest) -> RenderResult:
        self.calls += 1
        if self.behaviour == "fail":
            request.output_path.write_bytes(b"partial")
            raise RenderFailedError(f"{self.method.value} boom")
        if self.behaviour == "raise":
            raise ValueError("unexpected")
        if self.behaviour == "unavail_at_render":
            raise RenderUnavailableError("gpu missing")
        if self.behaviour == "empty":
            request.output_path.write_bytes(b"")
        else:
            request.output_path.write_bytes(b"video")
        return RenderResult(output_path=request.output_path, method_used=self.method)


@pytest.fixture
def request_(tmp_path: Path) -> RenderRequest:
    return RenderRequest(
        job_id="j1", source_video=tmp_path / "src.mp4", output_path=tmp_path / "out.mp4"
    )


def _backends(**behaviours: str) -> dict[RenderMethod, FakeBackend]:
    return {
        RenderMethod(name): FakeBackend(RenderMethod(name), behaviour)
        for name, behaviour in behaviours.items()
    }


def test_auto_prefers_raea_first(request_: RenderRequest) -> None:
    b = _backends(raea="ok", remotion="ok", ffmpeg="ok")
    res = render_with_fallback(request_, RenderMethod.AUTO, b)
    assert res.method_used == RenderMethod.RAEA
    assert res.attempts == [(RenderMethod.RAEA, None)]
    assert b[RenderMethod.REMOTION].calls == 0


def test_auto_falls_through_raea_to_remotion_to_ffmpeg(request_: RenderRequest) -> None:
    b = _backends(raea="unavailable", remotion="fail", ffmpeg="ok")
    res = render_with_fallback(request_, "auto", b)
    assert res.method_used == RenderMethod.FFMPEG
    assert [m for m, _ in res.attempts] == list(FALLBACK_CHAIN)
    assert res.attempts[0][1].startswith("unavailable")
    assert "boom" in (res.attempts[1][1] or "")
    assert res.attempts[2][1] is None
    # Partial output of the failed Remotion attempt was cleaned up before ffmpeg wrote its own.
    assert request_.output_path.read_bytes() == b"video"


def test_missing_raea_backend_is_just_unavailable(request_: RenderRequest) -> None:
    b = _backends(remotion="unavailable", ffmpeg="ok")
    res = render_with_fallback(request_, RenderMethod.AUTO, b)
    assert res.method_used == RenderMethod.FFMPEG
    assert res.attempts[0] == (RenderMethod.RAEA, "unavailable: backend not installed")


def test_explicit_method_tried_first_then_rest_of_chain(request_: RenderRequest) -> None:
    b = _backends(raea="ok", remotion="fail", ffmpeg="ok")
    res = render_with_fallback(request_, RenderMethod.REMOTION, b)
    assert [m for m, _ in res.attempts] == [RenderMethod.REMOTION, RenderMethod.RAEA]
    assert res.method_used == RenderMethod.RAEA


def test_explicit_method_without_fallback_raises(request_: RenderRequest) -> None:
    b = _backends(raea="fail", remotion="ok", ffmpeg="ok")
    with pytest.raises(RenderExhaustedError) as exc:
        render_with_fallback(request_, RenderMethod.RAEA, b, fallback=False)
    assert [m for m, _ in exc.value.attempts] == [RenderMethod.RAEA]
    assert b[RenderMethod.REMOTION].calls == 0


def test_all_fail_raises_with_every_error(request_: RenderRequest) -> None:
    b = _backends(raea="unavailable", remotion="fail", ffmpeg="raise")
    with pytest.raises(RenderFailedError) as exc:
        render_with_fallback(request_, RenderMethod.AUTO, b)
    msg = str(exc.value)
    assert "raea" in msg and "remotion" in msg and "ffmpeg" in msg and "unexpected" in msg
    assert isinstance(exc.value, RenderExhaustedError)
    assert len(exc.value.attempts) == 3
    assert not request_.output_path.exists()  # partial output cleaned


def test_unavailable_raised_during_render_falls_through(request_: RenderRequest) -> None:
    b = _backends(raea="unavail_at_render", ffmpeg="ok")
    res = render_with_fallback(request_, RenderMethod.AUTO, b)
    assert res.method_used == RenderMethod.FFMPEG
    assert "gpu missing" in (res.attempts[0][1] or "")


def test_empty_output_is_rejected_and_falls_through(request_: RenderRequest) -> None:
    b = _backends(raea="empty", ffmpeg="ok")
    res = render_with_fallback(request_, RenderMethod.AUTO, b)
    assert res.method_used == RenderMethod.FFMPEG
    assert "no valid output" in (res.attempts[0][1] or "")


def test_existing_output_is_not_deleted_when_attempt_fails(request_: RenderRequest) -> None:
    request_.output_path.write_bytes(b"previous master")
    b = _backends(raea="raise")
    with pytest.raises(RenderExhaustedError):
        render_with_fallback(request_, RenderMethod.RAEA, b, fallback=False)
    assert request_.output_path.read_bytes() == b"previous master"


def test_ordered_chain_shapes() -> None:
    assert ordered_chain(RenderMethod.AUTO) == list(FALLBACK_CHAIN)
    assert ordered_chain(RenderMethod.FFMPEG) == [
        RenderMethod.FFMPEG,
        RenderMethod.RAEA,
        RenderMethod.REMOTION,
    ]
    assert ordered_chain(RenderMethod.RAEA, fallback=False) == [RenderMethod.RAEA]


def test_parse_methods_and_assignment() -> None:
    assert parse_methods("raea, ffmpeg,raea") == [RenderMethod.RAEA, RenderMethod.FFMPEG]
    assert parse_methods(["Remotion"]) == [RenderMethod.REMOTION]
    assert parse_methods(None) == []
    with pytest.raises(ValueError):
        parse_methods("raea,bogus")
    ms = parse_methods("raea,ffmpeg,remotion")
    assert assign_methods("round_robin", ms, 5) == [ms[0], ms[1], ms[2], ms[0], ms[1]]
    assert assign_methods("variants", ms, 2) == [ms[0], ms[0]]
    assert assign_methods("single", [], 2) == [RenderMethod.AUTO] * 2


# ------------------------------------------------------------------ adapters


class RecordingCompositor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def compose(self, **kwargs: Any) -> Path:
        self.calls.append(kwargs)
        out = Path(kwargs["output_path"])
        out.write_bytes(b"x")
        return out


def test_ffmpeg_backend_wraps_compose_with_identical_kwargs(tmp_path: Path) -> None:
    comp = RecordingCompositor()
    kwargs = {"job_id": "j", "source_clip": tmp_path / "s.mp4", "layout_mode": "split_50_50"}
    req = RenderRequest(
        job_id="j",
        source_video=tmp_path / "s.mp4",
        output_path=tmp_path / "o.mp4",
        extras={"compose_kwargs": kwargs},
    )
    res = FfmpegBackend(comp).render(req)
    assert res.method_used == RenderMethod.FFMPEG
    assert res.output_path == tmp_path / "o.mp4"
    assert comp.calls[0]["job_id"] == "j"
    assert comp.calls[0]["layout_mode"] == "split_50_50"
    assert "cam_layout" not in comp.calls[0]


def test_ffmpeg_backend_passes_cam_layout_when_requested(tmp_path: Path) -> None:
    comp = RecordingCompositor()
    spec = CamLayoutSpec()
    req = RenderRequest(
        job_id="j",
        source_video=tmp_path / "s.mp4",
        output_path=tmp_path / "o.mp4",
        cam_layout=spec,
        extras={"compose_kwargs": {"job_id": "j"}},
    )
    FfmpegBackend(comp).render(req)
    assert comp.calls[0]["cam_layout"] is spec


def test_ffmpeg_backend_wraps_errors_as_render_failed(tmp_path: Path) -> None:
    class Boom:
        def compose(self, **kw: Any) -> Path:
            raise RuntimeError("ffmpeg exploded")

    req = RenderRequest(
        job_id="j",
        source_video=tmp_path,
        output_path=tmp_path / "o.mp4",
        extras={"compose_kwargs": {"job_id": "j"}},
    )
    with pytest.raises(RenderFailedError, match="ffmpeg exploded"):
        FfmpegBackend(Boom()).render(req)


def test_remotion_backend_unavailable_without_caption_plan(tmp_path: Path) -> None:
    be = RemotionBackend(RecordingCompositor(), project_dir=tmp_path)
    assert be.is_available() is False  # no node_modules/.bin/remotion
    req = RenderRequest(
        job_id="j",
        source_video=tmp_path,
        output_path=tmp_path / "o.mp4",
        extras={"compose_kwargs": {"job_id": "j"}},
    )
    with pytest.raises(RenderUnavailableError):
        be.render(req)


def test_remotion_backend_composes_then_overlays(tmp_path: Path, monkeypatch) -> None:
    import autoshorts.media.remotion_overlay as ro

    plan = tmp_path / "j_captions.json"
    plan.write_text("{}")
    hook = tmp_path / "hook.ass"
    hook.write_text("x")
    calls: dict[str, Any] = {}

    def fake_render(props: Path, out: Path, project_dir: Path) -> Path:
        calls["track"] = out
        out.write_bytes(b"t")
        return out

    def fake_overlay(base: Path, track: Path, out: Path, *, ffmpeg_path: str) -> Path:
        calls["overlay"] = (base, track, out, ffmpeg_path)
        out.write_bytes(b"c")
        return out

    monkeypatch.setattr(ro, "is_remotion_ready", lambda *_a, **_k: True)
    monkeypatch.setattr(ro, "render_caption_overlay", fake_render)
    monkeypatch.setattr(ro, "overlay_captions", fake_overlay)
    comp = RecordingCompositor()
    be = RemotionBackend(comp, project_dir=tmp_path, ffmpeg_path="ffmpeg")
    req = RenderRequest(
        job_id="j",
        source_video=tmp_path,
        output_path=tmp_path / "o.mp4",
        caption_track=plan,
        extras={
            "compose_kwargs": {"job_id": "j", "subtitles_path": "full.ass"},
            "hook_only_ass": hook,
        },
    )
    res = be.render(req)
    assert res.method_used == RenderMethod.REMOTION
    assert res.output_path.name == "o_captioned.mp4"
    assert Path(comp.calls[0]["subtitles_path"]) == hook  # words-free ASS used
    assert calls["overlay"][3] == "ffmpeg"


def test_load_raea_backend_missing_module_is_none(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "autoshorts.media.raea_renderer", None)
    assert load_raea_backend(None) is None


def test_load_raea_backend_uses_real_backend_with_settings_raea(monkeypatch) -> None:
    """The dispatcher must hand the real RaeaBackend its RaeaConfig, not the whole Settings."""
    from autoshorts.core.config import Settings
    from autoshorts.core.render_spec import RenderBackend
    from autoshorts.media.raea_renderer import RaeaBackend

    monkeypatch.setenv("AUTOSHORTS_RAEA_BIN", "")
    monkeypatch.setenv("PATH", "")
    settings = Settings()
    backend = load_raea_backend(settings)
    assert isinstance(backend, RaeaBackend)
    assert isinstance(backend, RenderBackend)
    assert backend.config is settings.raea
    assert backend.is_available() is False  # no raea on PATH -> unavailable, never raises

    settings.raea.enabled = False
    assert load_raea_backend(settings).is_available() is False


def test_method_limiter_defaults_and_unlimited() -> None:
    lim = MethodLimiter()
    with lim.slot(RenderMethod.RAEA):
        pass
    unlimited = MethodLimiter({})
    with unlimited.slot(RenderMethod.RAEA), unlimited.slot(RenderMethod.RAEA):
        pass


# ------------------------------------------------------------------ config


def test_render_config_defaults_and_validation() -> None:
    cfg = Settings().render
    assert cfg.method == "auto"
    assert cfg.strategy == "single"
    assert cfg.fallback is True
    assert cfg.methods == []
    assert cfg.max_parallel_per_method == {"raea": 1, "remotion": 1, "ffmpeg": 2}
    with pytest.raises(ValueError):
        RenderConfig(method="gpu")
    with pytest.raises(ValueError):
        RenderConfig(strategy="chaos")
    with pytest.raises(ValueError):
        RenderConfig(methods=["raea", "nope"])
    assert RenderConfig(method=" FFmpeg ").method == "ffmpeg"
