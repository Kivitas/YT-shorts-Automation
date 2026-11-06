"""Integration tests that run the REAL raea binary (skipped when raea or ffmpeg is missing).

Each render costs ~30s (raea startup), so only two short layouts are rendered, with sources that
raea itself refuses (B-frames; ProRes in .mov) to prove the edit-mezzanine step works.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autoshorts.core.config import RaeaConfig
from autoshorts.core.render_spec import CamLayoutSpec, RenderFailedError, RenderRequest
from autoshorts.media import raea_renderer as rr
from autoshorts.media.encoder_probe import get_ffmpeg_path

RAEA = rr.find_raea_binary()
FFMPEG = get_ffmpeg_path()

pytestmark = pytest.mark.skipif(RAEA is None, reason="raea binary not installed")

# 16:9 colour bars; bottom-right quarter-ish is a solid red "facecam" box.
RED_BOX = "drawbox=x=900:y=420:w=340:h=260:color=red@1:t=fill"


def _make(path: Path, *, codec_args: list[str], dur: int = 2, audio_args: list[str]) -> Path:
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=30:d={dur}"]
    cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={dur}", "-vf", RED_BOX, *codec_args]
    cmd += [*audio_args, "-shortest", str(path)]
    subprocess.run(cmd, check=True, timeout=120, capture_output=True)
    return path


def _pixel(video: Path, x: int, y: int, t: float = 0.8) -> tuple[int, int, int]:
    cmd = [FFMPEG, "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1"]
    cmd += ["-vf", f"crop=2:2:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, check=True, timeout=60, capture_output=True).stdout
    return raw[0], raw[1], raw[2]


def _probe(video: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)]
    return json.loads(subprocess.run(cmd, check=True, timeout=60, capture_output=True).stdout)


@pytest.fixture(scope="module")
def backend() -> rr.RaeaBackend:
    return rr.RaeaBackend(RaeaConfig(run_check=False))


def test_split_layout_with_bframe_source(backend: rr.RaeaBackend, tmp_path: Path) -> None:
    """H.264 with B-frames is refused by raea itself; the mezzanine makes it render."""
    src = _make(
        tmp_path / "src.mp4",
        codec_args=["-c:v", "libx264", "-pix_fmt", "yuv420p", "-bf", "2"],
        audio_args=["-c:a", "aac"],
    )
    req = RenderRequest(
        job_id="it_split",
        source_video=src,
        output_path=tmp_path / "out" / "split.mp4",
        layout_mode="split_50_50",
        branding={"watermark_text": "@channel"},
        extras={"hook_text": "Wait for it"},
    )
    result = backend.render(req)
    out = result.output_path
    assert out.is_file() and list(out.parent.iterdir()) == [out]
    streams = {s["codec_type"]: s for s in _probe(out)["streams"]}
    assert (streams["video"]["width"], streams["video"]["height"]) == (1080, 1920)
    assert streams["audio"]["codec_name"] == "aac"
    assert abs(float(_probe(out)["format"]["duration"]) - 2.0) < 0.6
    # top panel is the source (colour bars), seam is exactly at y=960: bottom panel = source copy
    assert _pixel(out, 20, 100) != (0, 0, 0)


def test_cam_gameplay_with_prores_mov_source(backend: rr.RaeaBackend, tmp_path: Path) -> None:
    """ProRes .mov is refused ("no video track"); cam crop must show the red box on top."""
    src = _make(
        tmp_path / "src.mov",
        codec_args=["-c:v", "prores_ks", "-profile:v", "0", "-pix_fmt", "yuv422p10le"],
        audio_args=["-c:a", "pcm_s16le"],
    )
    spec = CamLayoutSpec(
        top_ratio=0.4,
        cam_rect=(900 / 1280, 420 / 720, 340 / 1280, 260 / 720),  # exactly the red box
        gameplay_rect=(0.0, 0.0, 0.7, 1.0),
    )
    req = RenderRequest(
        job_id="it_cam",
        source_video=src,
        output_path=tmp_path / "out" / "cam.mp4",
        layout_mode="cam_gameplay",
        cam_layout=spec,
    )
    out = backend.render(req).output_path
    assert _pixel(out, 540, 200)[0] > 180 and _pixel(out, 540, 200)[1] < 60  # red on top
    r, g, b = _pixel(out, 540, 1500)  # gameplay area is not the red box
    assert not (r > 180 and g < 60 and b < 60)


def test_refused_source_gives_clear_error_and_no_leftovers(
    backend: rr.RaeaBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the mezzanine is bypassed raea REFUSES a B-frame file; the error names the refusal."""
    src = _make(
        tmp_path / "bf.mp4",
        codec_args=["-c:v", "libx264", "-pix_fmt", "yuv420p", "-bf", "2"],
        audio_args=["-c:a", "aac"],
    )
    monkeypatch.setattr(
        rr.MezzanineCache, "prepare", lambda self, s, **kw: s, raising=True
    )  # feed the raw file straight to raea
    req = RenderRequest(
        job_id="it_refused",
        source_video=src,
        output_path=tmp_path / "out" / "x.mp4",
        layout_mode="full_center_crop",
    )
    with pytest.raises(RenderFailedError, match="(?i)refus"):
        backend.render(req)
    assert not req.output_path.exists()
    assert not list((tmp_path / "out").glob("*"))
