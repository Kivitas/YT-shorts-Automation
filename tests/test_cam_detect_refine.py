"""Facecam detection: real-world proportions and snapping to the overlay's edges.

The numbers come from measuring a real webcam shot: the overlay is ~3.5x the Haar face width
but ~2.3x its height, with the face centred.
"""

from __future__ import annotations

import numpy as np
import pytest

from autoshorts.media import cam_detect

W, H = 640, 360


def _box_blur(a: np.ndarray, k: int) -> np.ndarray:
    """Cheap separable box blur (numpy only, no cv2)."""
    for axis in (0, 1):
        c = np.cumsum(
            np.pad(a, [(k, k) if i == axis else (0, 0) for i in (0, 1)], mode="edge"), axis=axis
        )
        a = (
            np.take(c, range(2 * k, c.shape[axis]), axis=axis)
            - np.take(c, range(0, c.shape[axis] - 2 * k), axis=axis)
        ) / (2 * k)
    return a


def _frames(n: int, overlay: tuple[int, int, int, int] | None, seed: int = 0) -> list:
    """Smooth, changing 'gameplay' with a static webcam-like overlay box (x0, y0, x1, y1)."""
    out = []
    for i in range(n):
        rng = np.random.default_rng(seed + i)
        game = 128 + _box_blur(_box_blur(rng.normal(0, 90, size=(H, W)), 9), 9)  # changes per frame
        if overlay:
            x0, y0, x1, y1 = overlay
            yy, xx = np.mgrid[y0:y1, x0:x1]
            game[y0:y1, x0:x1] = 80 + 25 * np.sin(xx / 9.0) * np.cos(yy / 11.0)
        out.append((W, H, np.clip(game, 0, 255).astype(np.uint8)))
    return out


def _face_in(overlay: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = overlay
    cw, ch = x1 - x0, y1 - y0
    fw, fh = round(cw / 3.5), round(ch / 2.3)
    return (x0 + (cw - fw) // 2, y0 + (ch - fh) // 2, fw, fh)


def _px(rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, w, h = rect
    return (x * W, y * H, (x + w) * W, (y + h) * H)


def test_expansion_matches_measured_webcam_proportions() -> None:
    # Face 86x86 px inside a 299x200 webcam crop (measured): overlay ~3.5x wide, ~2.3x tall.
    face = (96 / 299, 55 / 200, 86 / 299, 86 / 200)
    x, y, w, h = cam_detect.expand_face_to_cam_rect(face)
    assert w == pytest.approx(1.0, abs=0.03)  # the crop is the whole frame width here
    assert h == pytest.approx(1.0, abs=0.06)
    # in a larger frame the same face is 3.4x wide / 2.4x tall of the face box, centred
    fx, fy, fw, fh = 0.4, 0.4, 0.08, 0.14
    x, y, w, h = cam_detect.expand_face_to_cam_rect((fx, fy, fw, fh))
    assert w == pytest.approx(fw * 3.4, abs=1e-3) and h == pytest.approx(fh * 2.4, abs=1e-3)
    assert x + w / 2 == pytest.approx(fx + fw / 2, abs=1e-3)


@pytest.mark.parametrize(
    "overlay",
    [(440, 220, 616, 336), (24, 18, 200, 140), (400, 12, 620, 150)],
)
def test_edges_snap_to_the_real_overlay_box(overlay: tuple[int, int, int, int]) -> None:
    face = _face_in(overlay)
    frames = _frames(8, overlay)
    rect = cam_detect.find_facecam_rect(frames, lambda _d: [face], 8)
    assert rect is not None
    gx0, gy0, gx1, gy1 = _px(rect)
    ox0, oy0, ox1, oy1 = overlay
    for got, want in ((gx0, ox0), (gy0, oy0), (gx1, ox1), (gy1, oy1)):
        assert abs(got - want) <= 4, (rect, overlay)


def test_refinement_keeps_estimate_when_no_edge_exists() -> None:
    face = (280 / W, 150 / H, 60 / W, 50 / H)
    flat = [np.full((H, W), 120, dtype=np.uint8) for _ in range(6)]
    est = cam_detect.expand_face_to_cam_rect(face)
    assert cam_detect.refine_rect_to_edges(flat, est, face) == est


def test_refinement_ignores_non_array_frames() -> None:
    rect = (0.5, 0.5, 0.3, 0.3)
    assert cam_detect.refine_rect_to_edges(["a", "b"], rect, (0.55, 0.55, 0.1, 0.1)) == rect


def test_nearest_edge_wins_over_stronger_interior_edge() -> None:
    # Border at x=616 is low contrast; a much stronger straight edge sits inside the overlay.
    overlay = (440, 220, 616, 336)
    frames = []
    for i in range(8):
        rng = np.random.default_rng(i)
        g = rng.integers(100, 130, size=(H, W), dtype=np.uint8)
        g[220:336, 440:616] = 112  # border contrast ~ the noise floor
        g[220:336, 540:560] = 250  # strong interior stripe (like a door frame)
        frames.append((W, H, g))
    face = _face_in(overlay)
    rect = cam_detect.find_facecam_rect(frames, lambda _d: [face], 8)
    assert rect is not None
    # whatever it snaps to, the right side must not collapse onto the interior stripe
    assert _px(rect)[2] > 580
