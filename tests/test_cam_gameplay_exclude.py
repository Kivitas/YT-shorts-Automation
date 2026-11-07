"""Gameplay panel excludes the facecam overlay so the camera is not shown twice."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoshorts.core.config import CamLayoutConfig
from autoshorts.core.render_spec import CamLayoutSpec
from autoshorts.media.cam_layout import (
    cam_layout_from_config,
    gameplay_rect_excluding_cam,
    resolve_cam_layout,
)

SRC = Path("source.mp4")


def _area(r: tuple[float, float, float, float]) -> float:
    return r[2] * r[3]


@pytest.mark.parametrize(
    ("cam", "expected"),
    [
        ((0.68, 0.61, 0.30, 0.36), (0.0, 0.0, 0.68, 1.0)),  # bottom-right -> left strip
        ((0.02, 0.03, 0.18, 0.23), (0.2, 0.0, 0.8, 1.0)),  # top-left -> right strip
        ((0.0, 0.0, 1.0, 0.25), (0.0, 0.25, 1.0, 0.75)),  # top banner -> band below
        ((0.0, 0.8, 1.0, 0.2), (0.0, 0.0, 1.0, 0.8)),  # bottom banner -> band above
    ],
)
def test_picks_largest_strip_clear_of_the_camera(cam, expected) -> None:
    got = gameplay_rect_excluding_cam(cam)
    assert got == pytest.approx(expected, abs=1e-3)
    gx, gy, gw, gh = got
    cx, cy, cw, ch = cam
    # no overlap with the camera rect
    assert (
        gx + gw <= cx + 1e-6 or cx + cw <= gx + 1e-6 or gy + gh <= cy + 1e-6 or cy + ch <= gy + 1e-6
    )


def test_camera_filling_the_frame_keeps_whole_frame() -> None:
    assert gameplay_rect_excluding_cam((0.0, 0.0, 1.0, 1.0)) is None
    assert gameplay_rect_excluding_cam((0.05, 0.05, 0.9, 0.9)) is None  # only slivers remain


def test_resolver_applies_the_exclusion_in_crop_mode() -> None:
    spec = CamLayoutSpec(cam_rect=(0.68, 0.61, 0.30, 0.36), exclude_cam_from_gameplay=True)
    got = resolve_cam_layout(spec, source=SRC, auto_detect=False)
    assert got is not None
    assert got.cam_rect == (0.68, 0.61, 0.30, 0.36)
    assert got.gameplay_rect == pytest.approx((0.0, 0.0, 0.68, 1.0), abs=1e-3)


def test_resolver_uses_detected_rect_for_the_exclusion() -> None:
    spec = CamLayoutSpec(exclude_cam_from_gameplay=True)
    got = resolve_cam_layout(spec, source=SRC, detect=lambda _p: (0.02, 0.03, 0.18, 0.23))
    assert got is not None and got.gameplay_rect == pytest.approx((0.2, 0.0, 0.8, 1.0), abs=1e-3)


def test_explicit_gameplay_rect_and_flag_off_are_left_alone() -> None:
    cam = (0.68, 0.61, 0.30, 0.36)
    explicit = CamLayoutSpec(
        cam_rect=cam, gameplay_rect=(0.1, 0.1, 0.5, 0.5), exclude_cam_from_gameplay=True
    )
    assert resolve_cam_layout(explicit, source=SRC, auto_detect=False).gameplay_rect == (
        0.1,
        0.1,
        0.5,
        0.5,
    )
    off = CamLayoutSpec(cam_rect=cam)  # dataclass default: off, so existing renders are unchanged
    assert resolve_cam_layout(off, source=SRC, auto_detect=False).gameplay_rect is None


def test_file_mode_camera_keeps_the_whole_frame_for_gameplay(tmp_path: Path) -> None:
    cam_file = tmp_path / "cam.mp4"
    cam_file.write_bytes(b"x")
    spec = CamLayoutSpec(cam_source="file", cam_file=cam_file, exclude_cam_from_gameplay=True)
    got = resolve_cam_layout(spec, source=SRC, auto_detect=False)
    assert got is not None and got.cam_source == "file" and got.gameplay_rect is None


def test_config_defaults_to_excluding_and_flows_into_the_spec() -> None:
    assert CamLayoutConfig().exclude_cam_from_gameplay is True
    assert cam_layout_from_config(CamLayoutConfig()).exclude_cam_from_gameplay is True
    off = CamLayoutConfig(exclude_cam_from_gameplay=False)
    assert cam_layout_from_config(off).exclude_cam_from_gameplay is False
