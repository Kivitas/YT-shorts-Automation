"""Real-ffmpeg smoke test: cam_gameplay renders a 1080x1920 short with camera top, gameplay below."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from autoshorts.core.config import Settings
from autoshorts.core.render_spec import CamLayoutSpec
from autoshorts.media.compositor import FFmpegCompositor
from autoshorts.media.encoder_probe import get_ffmpeg_path

FFMPEG = get_ffmpeg_path()
FFPROBE = shutil.which("ffprobe") or str(Path(FFMPEG).with_name("ffprobe"))
HAS_FFMPEG = bool(shutil.which(FFMPEG) or Path(FFMPEG).is_file()) and bool(
    shutil.which("ffprobe") or Path(FFPROBE).is_file()
)

pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")

RED_BOX = (0.7, 0.55, 0.28, 0.4)  # normalized facecam region painted solid red in the source


def _run(cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(cmd, capture_output=True, check=True, timeout=120)


def _make_source(path: Path) -> None:
    x, y, w, h = RED_BOX
    box = f"drawbox=x=iw*{x}:y=ih*{y}:w=iw*{w}:h=ih*{h}:color=0xFF0000:t=fill"
    _run(
        [
            FFMPEG, "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3",
            "-f", "lavfi", "-i", "sine=f=440:d=3",
            "-vf", f"format=yuv420p,{box}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
        ]
    )  # fmt: skip


def _mean_rgb(video: Path, crop: str) -> tuple[int, int, int]:
    out = _run(
        [
            FFMPEG, "-v", "error", "-ss", "1.0", "-i", str(video), "-frames:v", "1",
            "-vf", f"{crop},scale=1:1:flags=area", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
        ]
    ).stdout  # fmt: skip
    assert len(out) == 3
    return out[0], out[1], out[2]


def test_cam_gameplay_real_render(tmp_path: Path):
    src = tmp_path / "source.mp4"
    _make_source(src)
    subs = tmp_path / "subs.ass"
    subs.write_text(
        "[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, Alignment, MarginV\n"
        "Style: Default,Arial,60,&H00FFFFFF,2,780\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:02.50,Default,Hello\n",
        encoding="utf-8",
    )
    out = tmp_path / "out" / "short.mp4"
    comp = FFmpegCompositor(
        config=Settings(_env_file=None), ffmpeg_binary=FFMPEG, encoder_preferred="libx264"
    )
    comp.compose(
        job_id="smoke",
        source_clip=src,
        edl=None,
        background_path=None,
        subtitles_path=subs,
        output_path=out,
        dopamine_level="none",
        layout_mode="cam_gameplay",
        cam_layout=CamLayoutSpec(cam_rect=RED_BOX),
        progress_bar_position="seam",
    )

    probe = json.loads(
        _run(
            [
                FFPROBE,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,sample_aspect_ratio",
                "-of",
                "json",
                str(out),
            ]
        ).stdout  # fmt: skip
    )["streams"][0]
    assert (probe["width"], probe["height"]) == (1080, 1920)
    assert probe.get("sample_aspect_ratio", "1:1") in ("1:1", "0:1")

    tr, tg, tb = _mean_rgb(out, "crop=1080:900:0:0")  # inside the top (camera) panel
    br, bg, bb = _mean_rgb(out, "crop=1080:900:0:1020")  # inside the bottom (gameplay) panel
    assert tr > 200 and tg < 60 and tb < 60  # top panel is the solid red facecam region
    assert not (br > 200 and bg < 60 and bb < 60)  # bottom is the busy testsrc2 gameplay
    assert abs(tr - br) + abs(tg - bg) + abs(tb - bb) > 80
