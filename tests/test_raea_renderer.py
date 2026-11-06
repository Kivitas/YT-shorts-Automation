"""Tests for the raea render backend (no real raea binary: subprocess layer is faked)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autoshorts.core.config import RaeaConfig, Settings
from autoshorts.core.render_spec import (
    CamLayoutSpec,
    RenderBackend,
    RenderFailedError,
    RenderMethod,
    RenderRequest,
    RenderUnavailableError,
)
from autoshorts.media import raea_renderer as rr

GOOD_INFO = {
    "width": 1080,
    "height": 1920,
    "duration": 10.0,
    "has_video": True,
    "has_audio": True,
}


def fake_probe(path: Path) -> dict[str, Any]:
    name = Path(path).name
    if name.startswith("src"):
        return {**GOOD_INFO, "width": 1920, "height": 1080, "duration": 20.0}
    if name.startswith("bg"):
        return {**GOOD_INFO, "width": 1080, "height": 1920, "duration": 8.0}
    if name.startswith("cam"):
        return {**GOOD_INFO, "width": 1280, "height": 720, "duration": 20.0}
    return dict(GOOD_INFO)


@pytest.fixture
def media(tmp_path: Path) -> dict[str, Path]:
    files = {}
    for name in ("src.mp4", "bg.mp4", "cam.mp4"):
        p = tmp_path / name
        p.write_bytes(b"x")
        files[name.split(".")[0]] = p
    return files


@pytest.fixture
def request_(tmp_path: Path, media: dict[str, Path]) -> RenderRequest:
    plan = tmp_path / "captions.json"
    plan.write_text(
        json.dumps(
            {
                "words": [
                    {"text": "hello", "start": 0.5, "end": 1.0},
                    {"text": "world", "start": 1.0, "end": 1.5},
                ],
                "durationSec": 10,
                "wordsPerPage": 2,
                "bottomPercent": 38,
            }
        ),
        encoding="utf-8",
    )
    return RenderRequest(
        job_id="job1",
        source_video=media["src"],
        output_path=tmp_path / "out" / "final.mp4",
        background_video=media["bg"],
        caption_track=plan,
        branding={
            "watermark_text": "@me",
            "progress_bar_color": "#FF0000",
            "style_preset": "hormozi",
            "enable_chyron": True,
            "chyron_badge": "LIVE",
        },
        extras={"hook_text": "Wait for it"},
    )


@pytest.fixture(autouse=True)
def _patch_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rr, "probe_video_stream_info", fake_probe)


# ---------------------------------------------------------------- availability


def test_backend_satisfies_protocol() -> None:
    backend = rr.RaeaBackend()
    assert isinstance(backend, RenderBackend)
    assert backend.method is RenderMethod.RAEA


def test_unavailable_when_no_binary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(rr.ENV_BINARY, raising=False)
    monkeypatch.setattr(rr.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(rr.Path, "home", classmethod(lambda cls: tmp_path))
    assert rr.RaeaBackend().is_available() is False


def test_available_via_config_and_version_probe_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exe = tmp_path / "raea.exe"
    exe.write_bytes(b"")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: float | None = None, **kw: Any):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "raea 0.1.0\n", "")

    monkeypatch.setattr(rr, "run_command_with_cleanup", fake_run)
    backend = rr.RaeaBackend(RaeaConfig(binary=str(exe)))
    assert backend.is_available() and backend.is_available()
    assert len(calls) == 1 and calls[0][1] == "--version"
    assert backend.version == "raea 0.1.0"


def test_env_var_and_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = tmp_path / "myraea"
    exe.write_bytes(b"")
    monkeypatch.setenv(rr.ENV_BINARY, str(exe))
    monkeypatch.setattr(
        rr,
        "run_command_with_cleanup",
        lambda cmd, timeout=None, **kw: subprocess.CompletedProcess(cmd, 0, "v", ""),
    )
    assert rr.RaeaBackend().is_available() is True
    assert rr.RaeaBackend(RaeaConfig(enabled=False)).is_available() is False


def test_render_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch, request_: RenderRequest, tmp_path: Path
) -> None:
    monkeypatch.setattr(rr, "find_raea_binary", lambda *_a: None)
    with pytest.raises(RenderUnavailableError):
        rr.RaeaBackend().render(request_)


# ---------------------------------------------------------------- render flow


def _backend_with_fake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    behaviour: str = "ok",
    config: RaeaConfig | None = None,
) -> tuple[rr.RaeaBackend, list[list[str]]]:
    exe = tmp_path / "raea"
    exe.write_bytes(b"")
    cmds: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: float | None = None, **kw: Any):
        cmds.append(cmd)
        if cmd[1] == "render":
            out = Path(cmd[cmd.index("-o") + 1])
            if behaviour == "timeout":
                raise subprocess.TimeoutExpired(cmd, timeout or 0)
            if behaviour == "fail":
                out.write_bytes(b"partial")
                return subprocess.CompletedProcess(cmd, 3, "", "boom: shader")
            if behaviour != "nofile":
                out.write_bytes(b"mp4data")
            # the generated composition must exist with baked props while raea runs
            comp = Path(cmd[2])
            assert comp.read_text(encoding="utf-8").startswith(f"const {rr.PROPS_CONST} = {{")
            assert (Path(kw["cwd"]) / "media" / "source.mp4").exists()
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1] == "check":
            return subprocess.CompletedProcess(cmd, 1, "P1 clipped text", "")
        return subprocess.CompletedProcess(cmd, 0, "raea 1.0", "")

    monkeypatch.setattr(rr, "run_command_with_cleanup", fake_run)
    cfg = config or RaeaConfig(binary=str(exe))
    cfg.binary = str(exe)
    return rr.RaeaBackend(cfg), cmds


def test_render_success_is_atomic_and_cli_is_exact(
    monkeypatch: pytest.MonkeyPatch, request_: RenderRequest, tmp_path: Path
) -> None:
    backend, cmds = _backend_with_fake(monkeypatch, tmp_path)
    result = backend.render(request_)
    assert result.method_used is RenderMethod.RAEA
    assert result.output_path.read_bytes() == b"mp4data"
    assert list(result.output_path.parent.iterdir()) == [result.output_path]  # no tmp left
    cmd = cmds[-1]
    assert cmd[1] == "render" and cmd[cmd.index("--composition") + 1] == "short"
    assert cmd[cmd.index("--w") + 1] == "1080" and cmd[cmd.index("--h") + 1] == "1920"
    assert "--fps" in cmd and cmd[cmd.index("-o") + 1] != str(request_.output_path)


def test_extra_args_and_timeout_scaling(
    monkeypatch: pytest.MonkeyPatch, request_: RenderRequest, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}
    backend, _ = _backend_with_fake(
        monkeypatch, tmp_path, config=RaeaConfig(extra_args=["--quality", "high"])
    )
    inner = rr.run_command_with_cleanup

    def spy(cmd: list[str], timeout: float | None = None, **kw: Any):
        if cmd[1] == "render":
            seen["timeout"], seen["cmd"] = timeout, cmd
        return inner(cmd, timeout=timeout, **kw)

    monkeypatch.setattr(rr, "run_command_with_cleanup", spy)
    backend.render(request_)
    assert seen["cmd"][-2:] == ["--quality", "high"]
    assert seen["timeout"] == pytest.approx(120.0)


def test_nonzero_exit_fails_and_leaves_nothing(
    monkeypatch: pytest.MonkeyPatch, request_: RenderRequest, tmp_path: Path
) -> None:
    backend, _ = _backend_with_fake(monkeypatch, tmp_path, "fail")
    with pytest.raises(RenderFailedError, match="boom"):
        backend.render(request_)
    assert not request_.output_path.exists()
    assert list(request_.output_path.parent.iterdir()) == []


def test_timeout_fails(
    monkeypatch: pytest.MonkeyPatch, request_: RenderRequest, tmp_path: Path
) -> None:
    backend, _ = _backend_with_fake(monkeypatch, tmp_path, "timeout")
    with pytest.raises(RenderFailedError, match="timed out"):
        backend.render(request_)
    assert not request_.output_path.exists()


def test_missing_output_fails(
    monkeypatch: pytest.MonkeyPatch, request_: RenderRequest, tmp_path: Path
) -> None:
    backend, _ = _backend_with_fake(monkeypatch, tmp_path, "nofile")
    with pytest.raises(RenderFailedError, match="no output"):
        backend.render(request_)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"width": 720, "height": 1280}, "expected 1080x1920"),
        ({"has_audio": False}, "no audio"),
        ({"has_video": False}, "no video"),
        ({"duration": 2.0}, "duration"),
    ],
)
def test_bad_output_rejected(
    monkeypatch: pytest.MonkeyPatch,
    request_: RenderRequest,
    tmp_path: Path,
    override: dict[str, Any],
    message: str,
) -> None:
    backend, _ = _backend_with_fake(monkeypatch, tmp_path)

    def probe(path: Path) -> dict[str, Any]:
        info = fake_probe(path)
        if Path(path).name.endswith(".tmp.mp4"):
            info = {**info, "duration": 10.0, **override}
        return info

    monkeypatch.setattr(rr, "probe_video_stream_info", probe)
    with pytest.raises(RenderFailedError, match=message):
        backend.render(request_)
    assert not request_.output_path.exists()


def test_run_check_records_diagnostics(
    monkeypatch: pytest.MonkeyPatch, request_: RenderRequest, tmp_path: Path
) -> None:
    backend, cmds = _backend_with_fake(monkeypatch, tmp_path, config=RaeaConfig(run_check=True))
    backend.render(request_)
    check = [c for c in cmds if c[1] == "check"][0]
    assert "--shape" in check and "9:16" in check
    diag = request_.extras["raea_check"]
    assert diag["passed"] is False and diag["exit_code"] == 1
    assert request_.output_path.exists()  # findings never fail the render


# ---------------------------------------------------------------- props


def test_props_split_layouts(request_: RenderRequest) -> None:
    props = rr.build_props(request_, probe=fake_probe)
    assert props["layout"] == "split_50_50" and props["seamY"] == 960
    top, bottom = props["panels"]
    assert top["box"] == {"x": 0, "y": 0, "w": 1080, "h": 960} and top["audio"]
    assert bottom["media"] == "background" and bottom["box"]["y"] == 960
    assert props["durationInFrames"] == 300 and props["fps"] == 30
    assert props["captions"]["wordsPerPage"] == 2 and props["captions"]["preset"] == "hormozi"
    assert props["hook"] == {"text": "Wait for it", "durationSec": 3.0}
    assert props["watermark"]["text"] == "@me"
    assert props["progressBar"]["color"] == "#FF0000" and props["progressBar"]["y"] == 960 - 6
    assert props["chyron"]["badge"] == "LIVE" and props["chyron"]["text"] == "Wait for it"
    json.dumps(props)  # serialisable

    request_.layout_mode = "split_70_30"
    props = rr.build_props(request_, probe=fake_probe)
    assert props["seamY"] == 1344
    assert props["panels"][0]["box"]["h"] == 1344 and props["panels"][1]["box"]["h"] == 576


@pytest.mark.parametrize(
    ("layout", "n_panels"),
    [("full_blur_bg", 2), ("full_center_crop", 1), ("pip_gameplay", 2)],
)
def test_props_other_layouts(request_: RenderRequest, layout: str, n_panels: int) -> None:
    request_.layout_mode = layout
    props = rr.build_props(request_, probe=fake_probe)
    assert len(props["panels"]) == n_panels
    assert sum(1 for p in props["panels"] if p["audio"]) == 1
    if layout == "full_blur_bg":
        assert props["panels"][0]["blur"] > 0 and props["panels"][1]["video"]["width"] == 1080
    if layout == "pip_gameplay":
        assert props["panels"][1]["media"] == "background" and props["panels"][1]["radius"] > 0


def test_props_cam_gameplay_crop(request_: RenderRequest) -> None:
    request_.layout_mode = "cam_gameplay"
    request_.cam_layout = CamLayoutSpec(
        top_ratio=0.4,
        cam_rect=(0.75, 0.0, 0.25, 0.25),  # 480x270 in the 1920x1080 source
        gameplay_rect=(0.0, 0.0, 0.75, 1.0),
        mirror_cam=True,
    )
    props = rr.build_props(request_, probe=fake_probe)
    cam, game = props["panels"]
    assert props["seamY"] == 768
    assert cam["box"] == {"x": 0, "y": 0, "w": 1080, "h": 768} and cam["mirror"]
    assert cam["media"] == "source" and not cam["audio"] and game["audio"]
    # cam crop 480x270 covers 1080x768 -> scale 768/270 = 2.844; source 1920x1080 scaled
    v = cam["video"]
    assert v["height"] == pytest.approx(1080 * 768 / 270, abs=0.1)
    assert v["width"] == pytest.approx(1920 * 768 / 270, abs=0.1)
    # the crop centre (0.875, 0.125 of the source) must land on the panel centre
    assert v["left"] + 0.875 * v["width"] == pytest.approx(540, abs=0.1)
    assert v["top"] + 0.125 * v["height"] == pytest.approx(384, abs=0.1)
    assert game["box"]["y"] == 768 and game["box"]["h"] == 1920 - 768


def test_props_cam_gameplay_from_file_and_whole_frame(
    request_: RenderRequest, media: dict[str, Path]
) -> None:
    request_.layout_mode = "cam_gameplay"
    request_.cam_layout = CamLayoutSpec(cam_source="file", cam_file=media["cam"])
    props = rr.build_props(request_, probe=fake_probe)
    cam, game = props["panels"]
    assert cam["media"] == "cam" and "cam" in props["media"]
    assert game["media"] == "source" and props["seamY"] == 960
    # gameplay_rect None = whole frame: crop centre is the frame centre
    v = game["video"]
    assert v["left"] + v["width"] / 2 == pytest.approx(540, abs=0.1)

    request_.cam_layout = CamLayoutSpec(cam_source="file", cam_file=Path("missing.mp4"))
    with pytest.raises(RenderFailedError, match="cam_file"):
        rr.build_props(request_, probe=fake_probe)


def test_props_errors_and_segments(request_: RenderRequest) -> None:
    request_.layout_mode = "bogus"
    with pytest.raises(RenderFailedError, match="unsupported"):
        rr.build_props(request_, probe=fake_probe)
    request_.layout_mode = "split_50_50"
    request_.edl = {"jump_cuts": [{"cut_start": 2.0, "cut_end": 3.0}]}
    props = rr.build_props(request_, probe=fake_probe)
    assert props["segments"] == [
        {"start": 0.0, "end": 2.0, "outStart": 0.0},
        {"start": 3.0, "end": 10.0, "outStart": 2.0},
    ]
    request_.source_video = Path("nope.mp4")
    with pytest.raises(RenderFailedError, match="missing"):
        rr.build_props(request_, probe=fake_probe)


def test_props_without_optional_parts(request_: RenderRequest) -> None:
    request_.caption_track = None
    request_.background_video = None
    request_.branding = {"progress_bar_position": "none"}
    request_.extras = {}
    props = rr.build_props(request_, probe=fake_probe)
    assert props["captions"] is None and props["hook"] is None and props["chyron"] is None
    assert props["progressBar"] is None and props["watermark"] is None
    assert props["panels"][1]["media"] == "source" and props["panels"][1]["blur"] > 0


# ---------------------------------------------------------------- tsx / config


def test_tsx_template_structure(request_: RenderRequest, tmp_path: Path) -> None:
    text = rr.TEMPLATE_PATH.read_text(encoding="utf-8")
    for token in (
        "registerRoot",
        "Composition",
        "1080",
        "__SHORTS_PROPS__",
        "HookCard",
        "ProgressBar",
    ):
        assert token in text
    for pair in ("{}", "()", "[]"):
        assert text.count(pair[0]) == text.count(pair[1]), pair
    comp = rr.write_job_composition(rr.build_props(request_, probe=fake_probe), tmp_path / "j.tsx")
    body = comp.read_text(encoding="utf-8")
    first, rest = body.split("\n", 1)
    assert first.startswith("const __SHORTS_PROPS__ = {") and first.endswith("};")
    assert rest == text


def test_config_defaults_and_settings_field() -> None:
    cfg = RaeaConfig()
    assert cfg.enabled and cfg.binary is None and not cfg.run_check and cfg.extra_args == []
    assert cfg.timeout_sec_per_clip_sec > 0
    assert Settings.model_fields["raea"].annotation is RaeaConfig
