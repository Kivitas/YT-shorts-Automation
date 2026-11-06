"""cam_gameplay layout: geometry, filtergraph, config, fallback, and compose() argv goldens."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autoshorts.core.config import CamLayoutConfig, Settings, VisualBrandingConfig
from autoshorts.core.models import EditDecisionList, JumpCut
from autoshorts.core.render_spec import CAM_GAMEPLAY_LAYOUT, CamLayoutSpec
from autoshorts.media import cam_detect
from autoshorts.media.cam_layout import (
    build_cam_gameplay_stages,
    cam_layout_from_config,
    crop_rect_filter,
    panel_heights,
    resolve_cam_layout,
)
from autoshorts.media.dopamine_effects import build_complex_filtergraph, get_dopamine_preset
from tests.test_compositor_compose_characterization import _record, _Spec

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "cam_layout_argv_golden.json"
UPDATE_ENV = "AUTOSHORTS_UPDATE_CAM_GOLDEN"
RECT = (0.7, 0.55, 0.28, 0.4)


# --- geometry -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (0.5, (960, 960)),
        (0.4, (768, 1152)),
        (0.55, (1056, 864)),
        (0.05, (384, 1536)),
        (0.99, (1536, 384)),
    ],
)
def test_panel_heights_are_even_and_sum_to_canvas(ratio: float, expected: tuple[int, int]):
    top, bottom = panel_heights(1920, ratio)
    assert (top, bottom) == expected
    assert top % 2 == 0 and bottom % 2 == 0 and top + bottom == 1920


def test_crop_rect_filter_whole_frame_is_empty():
    assert crop_rect_filter((0.0, 0.0, 1.0, 1.0)) == ""
    assert crop_rect_filter(RECT) == "crop=w=iw*0.2800:h=ih*0.4000:x=iw*0.7000:y=ih*0.5500,"


def test_crop_mode_stages_exact():
    spec = CamLayoutSpec(cam_rect=RECT, mirror_cam=True)
    stages, label = build_cam_gameplay_stages(
        spec, source_label="0:v", cam_label=None, width=1080, height=1920, fps=30
    )
    assert label == "stacked"
    assert stages == [
        "[0:v]split=2[cam_src][game_src]",
        "[cam_src]crop=w=iw*0.2800:h=ih*0.4000:x=iw*0.7000:y=ih*0.5500,"
        "scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,hflip,setsar=1,fps=30"
        "[cam_panel]",
        "[game_src]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1,"
        "fps=30[game_panel]",
        "[cam_panel][game_panel]vstack=inputs=2[stacked]",
    ]


def test_file_mode_stages_use_second_input_and_gameplay_rect():
    spec = CamLayoutSpec(
        cam_source="file",
        cam_file=Path("cam.mp4"),
        gameplay_rect=(0.0, 0.0, 0.7, 1.0),
        top_ratio=0.4,
    )
    stages, _ = build_cam_gameplay_stages(
        spec, source_label="0:v", cam_label="1:v", width=1080, height=1920, fps=30
    )
    assert stages[0].startswith("[1:v]scale=1080:768:")
    assert stages[1].startswith("[0:v]crop=w=iw*0.7000:h=ih*1.0000:x=iw*0.0000:y=ih*0.0000,")
    assert "scale=1080:1152:" in stages[1]


def test_crop_mode_without_rect_raises_for_the_resolver_to_handle():
    with pytest.raises(ValueError):
        build_cam_gameplay_stages(
            CamLayoutSpec(), source_label="0:v", cam_label=None, width=1080, height=1920, fps=30
        )


# --- full filtergraph -----------------------------------------------------------------------


def _graph(spec: CamLayoutSpec | None, **kw: Any) -> tuple[str, str, str]:
    return build_complex_filtergraph(
        source_clip_has_audio=True,
        subtitles_path=Path("subs.ass"),
        duration_sec=30.0,
        dopamine_preset=get_dopamine_preset("medium"),
        layout_mode=CAM_GAMEPLAY_LAYOUT,
        cam_layout=spec,
        **kw,
    )


def test_filtergraph_has_exact_panels_seam_bar_and_captions():
    graph, out_v, _ = _graph(
        CamLayoutSpec(cam_rect=RECT, top_ratio=0.4), progress_bar_position="seam"
    )
    assert out_v == "[outv]"
    assert "scale=1080:768:force_original_aspect_ratio=increase,crop=1080:768" in graph
    assert "scale=1080:1152:force_original_aspect_ratio=increase,crop=1080:1152" in graph
    assert "[cam_panel][game_panel]vstack=inputs=2[stacked]" in graph
    # Bar centered on the 768px seam (12px bar), captions burned last.
    assert "overlay=x='-1080+(1080*t/30.00)':y=762" in graph
    assert graph.count("setsar=1") == 2
    assert "subtitles=" in graph.split("[with_bar]")[-1]


def test_filtergraph_without_spec_degrades_to_default_split():
    graph, _, _ = _graph(None)
    assert "[1:v]" in graph and "cam_panel" not in graph


def test_filtergraph_jump_cuts_cut_both_cam_streams():
    edl = EditDecisionList(jump_cuts=[JumpCut(cut_start=1.0, cut_end=2.0)])
    spec = CamLayoutSpec(cam_source="file", cam_file=Path("cam.mp4"))
    graph, _, _ = _graph(spec, edl=edl)
    assert "[0:v]select='not(between(t,1.00,2.00))',setpts=N/FRAME_RATE/TB[v_base]" in graph
    assert "[1:v]select='not(between(t,1.00,2.00))',setpts=N/FRAME_RATE/TB[cam_base]" in graph
    assert "[cam_base]scale=" in graph and "[v_base]scale=" in graph
    assert "[a_cut]" in graph


# --- config ---------------------------------------------------------------------------------


def test_config_defaults_and_branding_field():
    cfg = VisualBrandingConfig()
    assert cfg.cam_layout == CamLayoutConfig()
    assert cfg.cam_layout.auto_detect is True and cfg.cam_layout.top_ratio == 0.5
    VisualBrandingConfig(layout_mode="cam_gameplay")


@pytest.mark.parametrize(
    "bad",
    [
        {"top_ratio": 0.1},
        {"top_ratio": 0.95},
        {"cam_rect": (0.9, 0.0, 0.3, 0.3)},
        {"cam_rect": (0.1, 0.1, 0.0, 0.3)},
        {"gameplay_rect": (-0.1, 0.0, 0.5, 0.5)},
        {"cam_source": "file"},
        {"cam_source": "screen"},
    ],
)
def test_config_rejects_invalid_cam_layout(bad: dict[str, Any]):
    with pytest.raises(ValidationError):
        CamLayoutConfig(**bad)


def test_spec_from_config_round_trip():
    cfg = CamLayoutConfig(
        top_ratio=0.45,
        cam_rect=RECT,
        gameplay_rect=(0, 0, 1, 1),
        mirror_cam=True,
        cam_file="c.mp4",
    )
    assert cam_layout_from_config(cfg) == CamLayoutSpec(
        top_ratio=0.45,
        cam_source="crop",
        cam_rect=RECT,
        cam_file=Path("c.mp4"),
        gameplay_rect=(0.0, 0.0, 1.0, 1.0),
        mirror_cam=True,
    )


def test_settings_load_cam_layout_from_dict():
    s = Settings(
        _env_file=None,
        branding={"layout_mode": "cam_gameplay", "cam_layout": {"cam_rect": list(RECT)}},
    )
    assert s.branding.cam_layout.cam_rect == RECT


# --- resolution / graceful fallback ---------------------------------------------------------


def test_resolve_prefers_explicit_rect_over_detection():
    def _boom(_p: Path) -> Any:
        raise AssertionError("detect must not run")

    spec = resolve_cam_layout(CamLayoutSpec(cam_rect=RECT), source=Path("s.mp4"), detect=_boom)
    assert spec is not None and spec.cam_rect == RECT


def test_resolve_auto_detects_and_clamps_top_ratio():
    spec = resolve_cam_layout(
        CamLayoutSpec(top_ratio=0.9),
        source=Path("s.mp4"),
        detect=lambda _p: (0.7, 0.6, 0.3, 0.4),
    )
    assert spec is not None and spec.cam_rect == (0.7, 0.6, 0.3, 0.4) and spec.top_ratio == 0.8


def test_resolve_returns_none_without_rect_or_detector_hit():
    assert resolve_cam_layout(CamLayoutSpec(), source=Path("s.mp4"), detect=lambda _p: None) is None
    assert resolve_cam_layout(None, source=Path("s.mp4")) is None
    assert resolve_cam_layout(CamLayoutSpec(), source=Path("s.mp4"), auto_detect=False) is None


def test_resolve_missing_cam_file_falls_back_to_crop_or_none(tmp_path: Path):
    spec = CamLayoutSpec(cam_source="file", cam_file=tmp_path / "nope.mp4", cam_rect=RECT)
    got = resolve_cam_layout(spec, source=Path("s.mp4"))
    assert got is not None and got.cam_source == "crop" and got.cam_file is None
    no_rect = CamLayoutSpec(cam_source="file")
    assert resolve_cam_layout(no_rect, source=Path("s.mp4"), auto_detect=False) is None


def test_resolve_never_raises_when_detector_explodes():
    def _boom(_p: Path) -> Any:
        raise RuntimeError("cv2 exploded")

    assert resolve_cam_layout(CamLayoutSpec(), source=Path("s.mp4"), detect=_boom) is None


# --- cam_detect (fake frames / fake detector) -----------------------------------------------


def _fake_frames(n: int, w: int = 640, h: int = 360) -> list[tuple[int, int, str]]:
    return [(w, h, f"frame{i}") for i in range(n)]


def test_detect_expands_corner_face_to_snapped_cam_rect():
    # Face in the bottom-right corner overlay of a 640x360 frame (the box is shifted in-frame).
    def detector(_f: Any) -> list[tuple[int, int, int, int]]:
        return [(550, 280, 60, 60), (5, 5, 10, 10)]

    rect = cam_detect.find_facecam_rect(_fake_frames(8), detector, 8)
    assert rect is not None
    x, y, w, h = rect
    assert x + w == pytest.approx(1.0) and y + h == pytest.approx(1.0)  # snapped to the corner
    assert 0.6 < x < 0.8 and 0.5 < y < 0.75
    assert all(0.0 <= v <= 1.0 for v in rect)


def test_detect_whole_frame_talking_head_returns_full_frame():
    rect = cam_detect.find_facecam_rect(_fake_frames(6), lambda _f: [(170, 60, 300, 240)], 6)
    assert rect == (0.0, 0.0, 1.0, 1.0)


def test_detect_rejects_unstable_or_rare_detections():
    jumpy = iter([[(10, 10, 40, 40)], [(500, 250, 40, 40)], [], [], [], [], [], []])
    assert cam_detect.find_facecam_rect(_fake_frames(8), lambda _f: next(jumpy), 8) is None
    assert cam_detect.find_facecam_rect(_fake_frames(8), lambda _f: [], 8) is None


def test_sample_timestamps_stay_inside_clip():
    ts = cam_detect.sample_timestamps(100.0, 4)
    assert len(ts) == 4 and ts == sorted(ts) and 5 < ts[0] and ts[-1] < 95
    assert cam_detect.sample_timestamps(0.0, 3) == [0.0]


def test_detect_facecam_rect_caches_per_path_mtime_size(tmp_path: Path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"1234")
    calls = {"n": 0}

    def provider(_p: Path, _n: int) -> list[tuple[int, int, str]]:
        calls["n"] += 1
        return _fake_frames(4)

    def detector(_f: Any) -> list[tuple[int, int, int, int]]:
        return [(520, 230, 60, 60)]

    real = cam_detect.detect_facecam_rect
    cam_detect.clear_cache()
    first = real(video, 4, frame_provider=provider, detector=detector)
    second = real(video, 4, frame_provider=provider, detector=detector)
    assert first is not None and first == second and calls["n"] == 1
    video.write_bytes(b"12345678")  # size changed -> new key
    real(video, 4, frame_provider=provider, detector=detector)
    assert calls["n"] == 2
    cam_detect.clear_cache()


def test_detect_returns_none_without_cv2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(cam_detect, "_import_cv2", lambda: None)
    assert cam_detect.detect_facecam_rect(video, use_cache=False) is None


# --- compose() argv goldens (stubbed ffmpeg) ------------------------------------------------


@pytest.fixture
def no_real_detection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cam_detect, "detect_facecam_rect", lambda *_a, **_k: None)


def _scenarios(tmp: Path) -> dict[str, _Spec]:
    cam = tmp / "cam.mp4"
    cam.write_bytes(b"camera bytes")
    cut_edl = EditDecisionList(jump_cuts=[JumpCut(cut_start=2.0, cut_end=3.5)])
    base = {"layout_mode": CAM_GAMEPLAY_LAYOUT, "dopamine_level": "medium"}

    def make(name: str, spec: CamLayoutSpec | None, **extra: Any) -> _Spec:
        edl = extra.pop("edl", None)
        return _Spec(
            name=name, kwargs={**base, "cam_layout": spec, **extra}, edl=edl, background="none"
        )

    return {
        "cam_crop_seam_bar": make(
            "cam_crop_seam_bar", CamLayoutSpec(cam_rect=RECT), progress_bar_position="seam"
        ),
        "cam_crop_mirror_ratio_watermark_chyron": make(
            "cam_crop_mirror_ratio_watermark_chyron",
            CamLayoutSpec(cam_rect=RECT, top_ratio=0.4, mirror_cam=True),
            watermark_text="@streamer",
            progress_bar_position="seam",
            chyron_text="Clutch of the year",
            enable_chyron=True,
        ),
        "cam_file_with_gameplay_rect": make(
            "cam_file_with_gameplay_rect",
            CamLayoutSpec(cam_source="file", cam_file=cam, gameplay_rect=(0.0, 0.0, 0.75, 1.0)),
        ),
        "cam_crop_jump_cuts": make("cam_crop_jump_cuts", CamLayoutSpec(cam_rect=RECT), edl=cut_edl),
        "cam_no_region_falls_back_to_split": make(
            "cam_no_region_falls_back_to_split", CamLayoutSpec()
        ),
    }


SCENARIO_NAMES = [
    "cam_crop_seam_bar",
    "cam_crop_mirror_ratio_watermark_chyron",
    "cam_file_with_gameplay_rect",
    "cam_crop_jump_cuts",
    "cam_no_region_falls_back_to_split",
]


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_cam_compose_argv_matches_golden(name: str, tmp_path: Path, no_real_detection: None):
    record = _record(tmp_path, _scenarios(tmp_path)[name])
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8")) if GOLDEN_PATH.is_file() else {}
    if os.environ.get(UPDATE_ENV) == "1":
        golden[name] = record
        GOLDEN_PATH.write_text(
            json.dumps(golden, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return
    assert name in golden, f"No golden for {name}; regenerate with {UPDATE_ENV}=1"
    assert record == golden[name]


def test_cam_compose_inputs_have_no_background(tmp_path: Path, no_real_detection: None):
    argv = _record(tmp_path, _scenarios(tmp_path)["cam_crop_seam_bar"])["calls"][0]["argv"]
    assert argv.count("-i") == 1  # source only; no gameplay or lavfi input
    assert "lavfi" not in argv


def test_cam_compose_fallback_uses_split_with_background(tmp_path: Path, no_real_detection: None):
    scenario = _scenarios(tmp_path)["cam_no_region_falls_back_to_split"]
    argv = _record(tmp_path, scenario)["calls"][0]["argv"]
    graph = argv[argv.index("-filter_complex") + 1]
    assert "cam_panel" not in graph and "[1:v]scale=1080:960" in graph
