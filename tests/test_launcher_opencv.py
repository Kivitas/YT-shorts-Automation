"""The facecam auto-detect dependency (OpenCV) must stay pinned and auto-installed."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_pyproject_pins_opencv_below_5() -> None:
    # OpenCV 5.x dropped CascadeClassifier and the bundled Haar face models.
    match = re.search(r'"opencv-python-headless([^"]*)"', _read("pyproject.toml"))
    assert match, "opencv-python-headless must be a core dependency"
    assert "<5" in match.group(1)


def test_launchers_install_opencv_when_missing() -> None:
    for name in ("start.bat", "start.sh"):
        text = _read(name)
        assert "cv2.CascadeClassifier" in text, name
        assert "opencv-python-headless>=4.9.0,<5" in text, name


def test_bat_md_documents_current_launcher() -> None:
    bat_md = _read("bat.md")
    assert "opencv-python-headless>=4.9.0,<5" in bat_md
