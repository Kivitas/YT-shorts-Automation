"""Wiring tests: the presenter stage inside the real longform frame path (no network)."""

from __future__ import annotations

import wave
from pathlib import Path

import yaml

from autoshorts.core.config import RenderConfig
from autoshorts.longform.drawer import FLOOR_Y, render_scene_frames
from autoshorts.longform.presenter_stage import PresenterStage, presenter_from_config
from autoshorts.longform.types import Section, SectionAudio

FRAME_BYTES = 1920 * 1080 * 3
# Solo presenter anchor is x=480 (drawer engine, 1.9x scale). Region covers the whole figure.
PRESENTER_BOX = (200, FLOOR_Y - 520, 760, FLOOR_Y + 20)
CONFIG_EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "config.example.yaml"


def _wav(path: Path, seconds: float) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


def _section(tmp_path: Path, duration: float = 1.0) -> list[SectionAudio]:
    words = [{"word": f"w{k}", "start": k * 0.2, "end": k * 0.2 + 0.18} for k in range(5)]
    return [
        SectionAudio(
            section=Section(heading="Part 1", narration="w0 w1 w2 w3 w4"),
            audio_path=_wav(tmp_path / "s0.wav", duration),
            words=words,
            duration=duration,
        )
    ]


def _crop_bytes(frame: bytes, box: tuple[int, int, int, int]) -> bytes:
    x0, y0, x1, y1 = box
    rows = []
    for y in range(y0, y1):
        start = (y * 1920 + x0) * 3
        rows.append(frame[start : start + (x1 - x0) * 3])
    return b"".join(rows)


def _render_first_frame(sections, presenter: PresenterStage | None) -> bytes:
    frames = list(
        render_scene_frames(sections, "Wiring", max_seconds_per_section=0.1, presenter=presenter)
    )
    assert frames, "no frames rendered"
    assert all(len(f) == FRAME_BYTES for f in frames)
    return frames[0]


def test_presenter_default_off_returns_no_stage() -> None:
    assert presenter_from_config(False) is None
    assert presenter_from_config(True) is not None
    assert RenderConfig().longform_presenter_stage is False


def test_example_config_documents_toggle_default_off() -> None:
    data = yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8"))
    assert data["render"]["longform_presenter_stage"] is False


def test_presenter_visible_vs_dismissed_frames_differ_and_region_is_empty(tmp_path: Path) -> None:
    sections = _section(tmp_path)

    baseline = _render_first_frame(sections, presenter=None)

    visible = presenter_from_config(True)
    assert visible is not None
    visible_frame = _render_first_frame(sections, presenter=visible)

    dismissed = presenter_from_config(True)
    assert dismissed is not None
    # A graphic (salience 0.95) starts before the section, so the presenter is fully dismissed at t=0.
    dismissed.auto_hide.add_salience_span(-1.0, 10.0, salience=0.95)
    dismissed_frame = _render_first_frame(sections, presenter=dismissed)

    # Presenter visible: frame differs from the no-presenter frame in the presenter region.
    assert visible_frame != baseline
    assert _crop_bytes(visible_frame, PRESENTER_BOX) != _crop_bytes(baseline, PRESENTER_BOX)

    # Presenter dismissed: the presenter region is empty (identical to no presenter), and the
    # whole frame, subtitles included, is identical to the no-presenter frame.
    assert _crop_bytes(dismissed_frame, PRESENTER_BOX) == _crop_bytes(baseline, PRESENTER_BOX)
    assert dismissed_frame == baseline
    assert dismissed_frame != visible_frame


def test_default_off_frames_match_plain_call(tmp_path: Path) -> None:
    sections = _section(tmp_path)
    plain = _render_first_frame(sections, presenter=None)
    explicit_off = _render_first_frame(sections, presenter=presenter_from_config(False))
    assert plain == explicit_off
