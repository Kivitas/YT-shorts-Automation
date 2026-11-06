"""Regression tests from the cam_gameplay / compositor review.

Covers: staged-subtitle cleanup on every error path, the CPU-retry timing, concurrent compose()
calls, chyron de-duplication (ASS events vs drawbox) and seam placement, cam-panel zoom
punch-ins, caption safe zone for the cam layout, and facecam detection without OpenCV.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from autoshorts.core.config import Settings
from autoshorts.core.models import EditDecisionList, JumpCut, ZoomPunchIn
from autoshorts.core.render_spec import CamLayoutSpec
from autoshorts.media import cam_detect
from autoshorts.media.ass_styler import build_ass_script
from autoshorts.media.cam_layout import (
    build_cam_gameplay_stages,
    caption_margin_v,
    caption_top_percent,
    panel_heights,
    remap_punch_ins_for_cuts,
)
from autoshorts.media.chyron_overlay import (
    ChyronData,
    build_chyron_ass,
    build_chyron_filter,
    calculate_chyron_y,
    has_chyron_events,
    retarget_chyron_ass,
)
from autoshorts.media.compositor import FFmpegCompositor
from autoshorts.media.dopamine_effects import build_complex_filtergraph, get_dopamine_preset
from autoshorts.media.remotion_overlay import write_caption_plan
from tests.test_compositor_compose_characterization import (
    _compositor,
    _FakeFFmpeg,
    _run_spec,
    _Spec,
    _stubbed_environment,
)

RECT = (0.7, 0.55, 0.28, 0.4)
CAM_KW = {
    "layout_mode": "cam_gameplay",
    "cam_layout": CamLayoutSpec(cam_rect=RECT),
    "dopamine_level": "none",
}


def _staged(out_dir: Path) -> list[str]:
    return sorted(p.name for p in out_dir.iterdir() if p.name.endswith("_subs.ass"))


# --- temp-file hygiene -----------------------------------------------------------------------


@pytest.mark.parametrize("failing", ["_build_filtergraph", "_video_encoder_args"])
def test_staged_subtitles_removed_when_a_pre_render_step_raises(tmp_path: Path, failing: str):
    spec = _Spec(name="x", kwargs=dict(CAM_KW), subtitles_name="Bob's captions.ass")
    fake = _FakeFFmpeg()
    with (
        patch.object(FFmpegCompositor, failing, side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError, match="boom"),
    ):
        _run_spec(tmp_path, spec, fake)
    assert _staged(tmp_path / "out") == []
    assert fake.calls == []


def test_staged_subtitles_removed_after_a_failed_render(tmp_path: Path):
    spec = _Spec(name="x", kwargs=dict(CAM_KW), subtitles_name="Bob's captions.ass", outcomes=(1,))
    with pytest.raises(RuntimeError, match="FFmpeg compositing failed"):
        _run_spec(tmp_path, spec, _FakeFFmpeg(outcomes=(1,)))
    assert _staged(tmp_path / "out") == []
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == []


def test_cpu_retry_reports_total_encode_time():
    from autoshorts.media.encoder_probe import EncoderInfo

    qsv = EncoderInfo(codec="h264_qsv", extra_args=[], is_hardware_accelerated=True, verified=True)
    fake = _FakeFFmpeg(outcomes=(1, 0), write_output=False)
    clock = iter([100.0, 105.0, 112.0])
    cmd = ["ffmpeg", "-c:v", "h264_qsv", "-c:a", "aac", "out.mp4"]
    with (
        patch("autoshorts.media.compositor.run_command_with_cleanup", side_effect=fake),
        patch("autoshorts.media.compositor.time.monotonic", side_effect=lambda: next(clock)),
    ):
        proc, elapsed = FFmpegCompositor._run_with_cpu_fallback(cmd, qsv, 10.0)
    assert proc.returncode == 0
    assert elapsed == pytest.approx(12.0)  # first attempt (5s) plus the retry (7s)
    assert "libx264" in fake.calls[1][0]


def test_parallel_composes_do_not_collide_on_staged_subtitles(tmp_path: Path):
    """Variant renders share a job id: each compose must stage and remove its own copy."""
    out = tmp_path / "out"
    out.mkdir()
    sub = tmp_path / "Bob's captions.ass"
    sub.write_text("[Script Info]\nPlayResX: 1080\n", encoding="utf-8")
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    seen: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4, timeout=20)

    def fake_run(cmd: list[str], timeout: Any = None, **_: Any) -> Any:
        graph = cmd[cmd.index("-filter_complex") + 1]
        match = re.search(r"\.tmp_[^/\']*_subs\.ass", graph)
        assert match, graph
        with lock:
            seen.append(match.group(0))
        try:
            barrier.wait()  # all four composes hold their staged file at the same time
        except threading.BrokenBarrierError:
            pass
        assert (out / match.group(0)).exists()
        Path(cmd[-1]).write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    errors: list[BaseException] = []
    comp = _compositor()

    def worker(i: int) -> None:
        try:
            comp.compose(
                job_id="same_job",
                source_clip=src,
                edl=None,
                background_path=None,
                subtitles_path=sub,
                output_path=out / f"v{i}.mp4",
                **CAM_KW,
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    with (
        _stubbed_environment(tmp_path, comp, _FakeFFmpeg(), _Spec(name="p")),
        patch("autoshorts.media.compositor.run_command_with_cleanup", fake_run),
    ):
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
    assert not errors, errors
    assert len(set(seen)) == 4  # four distinct staged files while all were alive
    assert _staged(out) == []


# --- chyron ----------------------------------------------------------------------------------


def _graph(**kw: Any) -> str:
    base: dict[str, Any] = {
        "source_clip_has_audio": True,
        "subtitles_path": Path("subs.ass"),
        "duration_sec": 10.0,
        "dopamine_preset": get_dopamine_preset("none"),
        "layout_mode": "cam_gameplay",
        "cam_layout": CamLayoutSpec(cam_rect=RECT),
        "enable_chyron": True,
        "chyron_text": "BIG NEWS TODAY",
    }
    base.update(kw)
    return build_complex_filtergraph(**base)[0]


def test_chyron_is_not_drawn_twice_when_the_ass_already_has_it():
    assert "drawtext=text=BIG" in _graph()
    assert "drawtext" not in _graph(chyron_in_subtitles=True)


def test_drawbox_chyron_follows_the_cam_seam():
    graph = _graph(cam_layout=CamLayoutSpec(top_ratio=0.3, cam_rect=RECT))
    seam = panel_heights(1920, 0.3)[0]
    banner_y, _ = calculate_chyron_y("seam", "cam_gameplay", 1920, 72, seam_y=seam)
    assert f"drawbox=x=60:y={banner_y}:w=960:h=72" in graph
    assert banner_y < seam


def test_chyron_ass_seam_and_retarget_round_trip():
    chyron = ChyronData(headline_text="HELLO")
    seam = panel_heights(1920, 0.3)[0]
    direct = build_chyron_ass(chyron, 5.0, layout_mode="cam_gameplay", seam_y=seam)
    default = build_chyron_ass(chyron, 5.0, layout_mode="cam_gameplay")
    assert has_chyron_events(direct)
    assert retarget_chyron_ass(default, seam - 960) == direct
    assert retarget_chyron_ass(default, 0) == default
    assert not has_chyron_events("Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,hi")


def test_chyron_filter_without_seam_is_unchanged():
    a, _ = build_chyron_filter(ChyronData(headline_text="X"), layout_mode="split_50_50")
    b, _ = build_chyron_filter(
        ChyronData(headline_text="X"), layout_mode="split_50_50", seam_y=None
    )
    assert a == b


def test_compose_skips_drawbox_chyron_and_moves_ass_events_to_the_seam(tmp_path: Path):
    out = tmp_path / "out"
    seam = panel_heights(1920, 0.3)[0]
    ass = build_chyron_ass(ChyronData(headline_text="HELLO"), 5.0, layout_mode="cam_gameplay")
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], timeout: Any = None, **_: Any) -> Any:
        captured["graph"] = cmd[cmd.index("-filter_complex") + 1]
        staged = [p for p in out.iterdir() if p.name.endswith("_subs.ass")]
        captured["staged"] = staged[0].read_text(encoding="utf-8") if staged else None
        Path(cmd[-1]).write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    spec = _Spec(name="c", kwargs={})
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    sub = tmp_path / "subs.ass"
    sub.write_text(
        "[Script Info]\nPlayResX: 1080\n\n[Events]\nFormat: x\n" + ass.split("[Events]")[1],
        encoding="utf-8",
    )
    sub.write_text(
        "[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n" + ass[ass.index("[V4+ Styles]") :],
        encoding="utf-8",
    )
    comp = _compositor()
    with _stubbed_environment(tmp_path, comp, _FakeFFmpeg(), spec):
        with patch("autoshorts.media.compositor.run_command_with_cleanup", fake_run):
            comp.compose(
                job_id="c",
                source_clip=src,
                edl=None,
                background_path=None,
                subtitles_path=sub,
                output_path=out / "o.mp4",
                layout_mode="cam_gameplay",
                cam_layout=CamLayoutSpec(top_ratio=0.3, cam_rect=RECT),
                dopamine_level="none",
                enable_chyron=True,
                chyron_text="HELLO",
            )
    assert "drawtext" not in captured["graph"]
    assert f"\\pos(60,{seam - 72 - 14})" in captured["staged"]  # headline row moved to the seam
    assert _staged(out) == []


# --- zoom punch-ins on the cam panel ---------------------------------------------------------


def test_remap_punch_ins_shifts_by_the_cut_time_before_them():
    punches = [
        ZoomPunchIn(timestamp=1.0, duration=0.5),
        ZoomPunchIn(timestamp=6.0, duration=0.5),
        ZoomPunchIn(timestamp=4.1, duration=0.4),  # fully inside the 4..5 cut -> dropped
    ]
    cuts = [JumpCut(cut_start=4.0, cut_end=5.0)]
    out = remap_punch_ins_for_cuts(punches, cuts)
    assert [round(p.timestamp, 2) for p in out] == [1.0, 5.0]


def test_cam_panel_zooms_but_gameplay_panel_does_not():
    edl = EditDecisionList(
        hook_in_sec=0.0,
        hook_out_sec=20.0,
        jump_cuts=[JumpCut(cut_start=4.0, cut_end=5.0)],
        zoom_punch_ins=[ZoomPunchIn(timestamp=6.0, duration=0.8)],
    )
    graph = _graph(
        dopamine_preset=get_dopamine_preset("high"), edl=edl, enable_chyron=False, chyron_text=None
    )
    assert "[cam_pre_zoom]crop=w='iw/(1.0+(" in graph
    assert "between(t,5.00,5.80)" in graph  # 6.0 shifted by the 1s cut
    assert "[cam_panel]" in graph
    game = next(s for s in graph.split("; ") if s.endswith("[game_panel]"))
    assert "between" not in game


def test_no_zoom_stage_without_punch_ins():
    stages, _ = build_cam_gameplay_stages(
        CamLayoutSpec(cam_rect=RECT),
        source_label="0:v",
        cam_label=None,
        width=1080,
        height=1920,
        fps=30,
    )
    assert not any("cam_pre_zoom" in s for s in stages)


# --- captions / remotion safe zone -----------------------------------------------------------


@pytest.mark.parametrize("ratio", [0.2, 0.35, 0.5, 0.65, 0.8])
def test_cam_captions_sit_below_the_seam_and_above_the_bottom_ui(ratio: float):
    seam = panel_heights(1920, ratio)[0]
    margin_v = caption_margin_v(ratio, 76)
    text_bottom = 1920 - margin_v
    text_top = text_bottom - 76
    assert text_top > seam  # never on the seam or the face panel
    assert text_bottom <= 1920 * 0.9  # clear of the bottom UI
    assert caption_top_percent(ratio) * 19.2 > seam


def test_ass_uses_the_cam_safe_zone_only_for_cam_layout():
    words = [
        {"word": "hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.5, "end": 1.0},
    ]

    def margin(**kw: Any) -> int:
        ass = build_ass_script(words, **kw)
        style = next(line for line in ass.splitlines() if line.startswith("Style: DopamineYellow"))
        return int(style.split(",")[-2])

    plain = margin()
    cam = margin(layout_mode="cam_gameplay", cam_top_ratio=0.5)
    assert plain == 780
    assert cam == caption_margin_v(0.5, 76)
    assert margin(layout_mode="cam_gameplay", cam_top_ratio=0.8) < cam
    assert margin(layout_mode="split_50_50", cam_top_ratio=0.8) == plain


def test_caption_plan_top_percent(tmp_path: Path):
    path = write_caption_plan(tmp_path / "p.json", [], duration_sec=2.0)
    assert json.loads(path.read_text())["bottomPercent"] == 38
    path = write_caption_plan(tmp_path / "p.json", [], duration_sec=2.0, top_percent=60.5)
    assert json.loads(path.read_text())["bottomPercent"] == 60.5


# --- facecam detection without OpenCV --------------------------------------------------------


def test_detect_without_cv2_is_quiet_and_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(cam_detect, "_import_cv2", lambda: None)
    monkeypatch.setattr(cam_detect, "_no_cv2_logged", False)
    cam_detect.clear_cache()
    with caplog.at_level(logging.DEBUG, logger=cam_detect.logger.name):
        results = [cam_detect.detect_facecam_rect(video) for _ in range(5)]
    assert results == [None] * 5
    assert len([r for r in caplog.records if "cv2" in r.getMessage()]) == 1
    assert not cam_detect._cache  # a miss for a missing dependency must not stick


def test_detect_runs_the_pure_pipeline_with_injected_frames(tmp_path: Path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    cam_detect.clear_cache()
    frames = [(640, 360, None)] * 6
    rect = cam_detect.detect_facecam_rect(
        video,
        samples=6,
        frame_provider=lambda _p, _n: iter(frames),
        detector=lambda _d: [(500, 220, 60, 60)],
    )
    assert rect is not None and rect[0] > 0.6 and rect[1] > 0.4
    cam_detect.clear_cache()


def test_display_size_swaps_for_rotated_streams():
    assert cam_detect._display_size({"width": 1920, "height": 1080}) == (1920, 1080)
    assert cam_detect._display_size({"width": 1920, "height": 1080, "tags": {"rotate": "90"}}) == (
        1080,
        1920,
    )
    side = {"width": 1920, "height": 1080, "side_data_list": [{"rotation": -90}]}
    assert cam_detect._display_size(side) == (1080, 1920)
    assert cam_detect._display_size({"width": 4, "height": 2, "tags": {"rotate": "180"}}) == (4, 2)


def test_detect_threads_share_one_cache_safely(tmp_path: Path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    cam_detect.clear_cache()
    results: list[Any] = []

    def run() -> None:
        results.append(
            cam_detect.detect_facecam_rect(
                video,
                samples=2,
                frame_provider=lambda _p, _n: iter([(100, 100, None)] * 2),
                detector=lambda _d: [(60, 60, 30, 30)],
            )
        )

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(results) == 8 and len(set(results)) == 1
    cam_detect.clear_cache()


# --- real ffmpeg: odd source geometries ------------------------------------------------------

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg not installed")


@needs_ffmpeg
@pytest.mark.parametrize(
    ("size", "rect"),
    [
        ("360x640", (0.5, 0.0, 0.5, 0.25)),  # already vertical
        ("640x270", (0.75, 0.6, 0.25, 0.4)),  # 21:9
        ("160x120", (0.0, 0.0, 0.3, 0.3)),  # tiny 4:3
        ("321x181", (0.9, 0.9, 0.1, 0.1)),  # odd size, rect on the corner
    ],
)
def test_odd_source_geometries_render_exact_canvas(tmp_path: Path, size: str, rect: tuple):
    from autoshorts.media.encoder_probe import get_ffmpeg_path, get_ffprobe_path

    ffmpeg = get_ffmpeg_path()
    src = tmp_path / "s.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=25:d=1",
         "-f", "lavfi", "-i", "sine=d=1", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac",
         "-shortest", str(src)],
        check=True, timeout=60,
    )  # fmt: skip
    subs = tmp_path / "s.ass"
    subs.write_text("[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n", encoding="utf-8")
    comp = FFmpegCompositor(
        config=Settings(_env_file=None), ffmpeg_binary=ffmpeg, encoder_preferred="libx264"
    )
    out = tmp_path / "o" / "o.mp4"
    comp.compose(
        job_id="odd",
        source_clip=src,
        edl=None,
        background_path=None,
        subtitles_path=subs,
        output_path=out,
        layout_mode="cam_gameplay",
        cam_layout=CamLayoutSpec(top_ratio=0.37, cam_rect=rect),
        dopamine_level="none",
    )
    probe = json.loads(
        subprocess.run(
            [get_ffprobe_path(), "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "json", str(out)],
            capture_output=True, check=True, timeout=60,
        ).stdout
    )["streams"][0]  # fmt: skip
    assert (probe["width"], probe["height"]) == (1080, 1920)
    assert [p.name for p in out.parent.iterdir()] == ["o.mp4"]


def test_detect_does_not_cache_a_run_that_decoded_nothing(tmp_path: Path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    cam_detect.clear_cache()
    assert (
        cam_detect.detect_facecam_rect(
            video, frame_provider=lambda _p, _n: iter([]), detector=lambda _d: []
        )
        is None
    )
    assert not cam_detect._cache
    cam_detect.detect_facecam_rect(
        video,
        samples=1,
        frame_provider=lambda _p, _n: iter([(10, 10, None)]),
        detector=lambda _d: [],
    )
    assert len(cam_detect._cache) == 1  # a real "no face" verdict is cached
    cam_detect.clear_cache()


def test_silent_source_with_jump_cuts_gets_a_silent_track_as_short_as_the_picture():
    graph = _graph(
        source_clip_has_audio=False,
        duration_sec=2.3,
        enable_chyron=False,
        chyron_text=None,
        edl=EditDecisionList(
            hook_in_sec=0.0, hook_out_sec=2.3, jump_cuts=[JumpCut(cut_start=1.0, cut_end=1.4)]
        ),
    )
    assert "anullsrc=channel_layout=stereo:sample_rate=44100:d=1.90[outa]" in graph
