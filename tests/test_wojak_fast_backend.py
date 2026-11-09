"""Tests for the Wojak fast-backend selector and scene-still renderer."""

from pathlib import Path

import pytest
from PIL import Image

from autoshorts.longform import wojak_fast_backend as fb
from autoshorts.longform.wojak_compositor import FRAME_HEIGHT, FRAME_WIDTH, HEADER_HEIGHT


@pytest.fixture
def no_fast_backends(monkeypatch):
    monkeypatch.setattr(fb, "find_raea_binary", lambda *a, **k: None)
    monkeypatch.setattr(fb, "is_remotion_ready", lambda *a, **k: False)


def test_resolve_none_defaults_to_pil(no_fast_backends):
    assert fb.resolve_fast_backend(None) == "pil"


def test_resolve_explicit_pil(no_fast_backends):
    assert fb.resolve_fast_backend("pil") == "pil"


def test_resolve_unknown_string_falls_back_to_pil(no_fast_backends):
    assert fb.resolve_fast_backend("turbo-gpu") == "pil"


def test_resolve_explicit_raea_unavailable_falls_back_to_pil(no_fast_backends):
    assert fb.resolve_fast_backend("raea") == "pil"


def test_auto_both_missing_is_pil(no_fast_backends):
    assert fb.resolve_fast_backend("auto") == "pil"


def test_auto_raea_present_is_raea(monkeypatch):
    monkeypatch.setattr(fb, "find_raea_binary", lambda *a, **k: Path("raea.exe"))
    monkeypatch.setattr(fb, "is_remotion_ready", lambda *a, **k: True)
    assert fb.resolve_fast_backend("auto") == "raea"


def test_auto_only_remotion_ready_is_remotion(monkeypatch):
    monkeypatch.setattr(fb, "find_raea_binary", lambda *a, **k: None)
    monkeypatch.setattr(fb, "is_remotion_ready", lambda *a, **k: True)
    assert fb.resolve_fast_backend("auto") == "remotion"


def test_explicit_remotion_when_ready(monkeypatch):
    monkeypatch.setattr(fb, "find_raea_binary", lambda *a, **k: None)
    monkeypatch.setattr(fb, "is_remotion_ready", lambda *a, **k: True)
    assert fb.resolve_fast_backend("remotion") == "remotion"


def test_selector_never_raises_when_probe_errors(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(fb, "find_raea_binary", boom)
    monkeypatch.setattr(fb, "is_remotion_ready", boom)
    assert fb.resolve_fast_backend("auto") == "pil"
    assert fb.resolve_fast_backend("raea") == "pil"


def test_render_scene_stills_pil_writes_one_png_per_scene(tmp_path):
    bg = Image.new("RGB", (FRAME_WIDTH, FRAME_HEIGHT - HEADER_HEIGHT), (200, 210, 220))
    scenes = [
        {"country": "USA", "caption": "Rent went up.", "background_img": bg},
        {
            "country": "SWEDEN",
            "caption": "Doctor gave me tea.",
            "background_img": bg,
            "character_img": Image.new("RGBA", (300, 500), (255, 0, 0, 255)),
            "character_side": "right",
        },
    ]
    out_dir = tmp_path / "stills"

    paths = fb.render_scene_stills(scenes, out_dir, "pil")

    assert len(paths) == len(scenes)
    assert [p.name for p in paths] == ["scene_000.png", "scene_001.png"]
    for p in paths:
        assert p.is_file()
        with Image.open(p) as img:
            assert img.size == (FRAME_WIDTH, FRAME_HEIGHT)


def test_render_scene_stills_requires_background(tmp_path):
    with pytest.raises(KeyError):
        fb.render_scene_stills([{"country": "USA", "caption": "x"}], tmp_path, "pil")
