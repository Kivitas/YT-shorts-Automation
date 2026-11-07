"""Two explicit modes: Subway/Minecraft background vs streamer cam on top + their gameplay."""

from __future__ import annotations

from pathlib import Path

from autoshorts.cli import build_parser

ROOT = Path(__file__).resolve().parents[1] / "src" / "autoshorts" / "dashboard"


def test_cli_streamer_cam_flag_is_off_by_default() -> None:
    args = build_parser().parse_args(["run", "--links", "https://youtu.be/abc"])
    assert args.streamer_cam is False
    args = build_parser().parse_args(["run", "--links", "https://youtu.be/abc", "--streamer-cam"])
    assert args.streamer_cam is True


def test_dashboard_has_a_video_mode_picker_that_drives_the_layout() -> None:
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="video-mode-select"' in html
    assert 'value="streamer_cam"' in html and 'value="background"' in html
    assert "function onVideoModeChange" in js and "function updateVideoModeUi" in js
    # picking streamer cam must select the cam layout; the background picker is hidden for it
    assert 'layout.value = "cam_gameplay"' in js
    assert 'style.display = camMode ? "none" : ""' in js
