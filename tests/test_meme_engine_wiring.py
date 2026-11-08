"""Wiring tests: the Types Of... meme engine inside the real longform render path."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autoshorts.core.theme import ACCENT_CORAL, BG_DEEP
from autoshorts.longform.drawer import HEIGHT, WIDTH, render_scene_frames
from autoshorts.longform.meme_engine import (
    ESCALATION_FREEZE_S,
    TypesOfMemeOverlay,
    TypesOfMemeVignette,
    VignetteConfig,
    resolve_meme_overlay,
)
from autoshorts.longform.types import Section, SectionAudio

# Badge sits at the top centre; radar card is centred at (1420, 520) on the right.
BADGE_REGION = (600, 50, 1320, 140)  # x0, y0, x1, y1
RADAR_REGION = (1190, 300, 1650, 740)


def _count_near(img: Image.Image, region, rgb, tol: int = 40) -> int:
    x0, y0, x1, y1 = region
    arr = np.asarray(img.convert("RGB"))[y0:y1, x0:x1].astype(int)
    dist = np.abs(arr - np.array(rgb)).sum(axis=2)
    return int((dist < tol).sum())


def _vignette_config() -> VignetteConfig:
    return VignetteConfig(
        type_number=1,
        archetype_title="THE OVERTHINKER",
        tagline="Rewrote the opening line 40 times.",
        stats={"Stamina": 12.0, "Caffeine": 100.0, "Anxiety": 100.0, "Speed": 15.0},
        display={"Caffeine": "999%", "Anxiety": "Max"},
        freeze_time=ESCALATION_FREEZE_S,
        total_duration=7.0,
        accent_color=ACCENT_CORAL,
    )


def test_meme_engine_default_is_off(monkeypatch) -> None:
    monkeypatch.delenv("AUTOSHORTS_MEME_ENGINE", raising=False)
    assert resolve_meme_overlay(None) is None


def test_meme_engine_env_var_enables(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSHORTS_MEME_ENGINE", "1")
    overlay = resolve_meme_overlay(None)
    assert isinstance(overlay, TypesOfMemeOverlay)
    assert 3 <= overlay.archetype_count <= 5


def test_explicit_flag_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("AUTOSHORTS_MEME_ENGINE", "1")
    assert resolve_meme_overlay(False) is None
    monkeypatch.delenv("AUTOSHORTS_MEME_ENGINE", raising=False)
    assert resolve_meme_overlay(True) is not None


def test_vignette_frame_has_badge_and_radar_pixels() -> None:
    vignette = TypesOfMemeVignette(_vignette_config())
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_DEEP)
    # Freeze + 1.0s: badge fully popped in, radar fully revealed.
    vignette.draw_frame(img, t=ESCALATION_FREEZE_S + 1.0)

    badge_px = _count_near(img, BADGE_REGION, ACCENT_CORAL)
    # The radar card uses the vignette accent colour (coral by default), like the badge.
    radar_px = _count_near(img, RADAR_REGION, ACCENT_CORAL)
    assert badge_px > 100, f"badge pill missing (coral pixels={badge_px})"
    assert radar_px > 100, f"radar card missing (coral pixels={radar_px})"


def test_vignette_badge_and_radar_absent_before_entrance() -> None:
    vignette = TypesOfMemeVignette(_vignette_config())
    img = Image.new("RGB", (WIDTH, HEIGHT), BG_DEEP)
    vignette.draw_frame(img, t=ESCALATION_FREEZE_S - 0.5)
    assert _count_near(img, BADGE_REGION, ACCENT_CORAL) == 0
    assert _count_near(img, RADAR_REGION, ACCENT_CORAL) == 0


def test_overlay_draws_only_inside_active_segment() -> None:
    overlay = TypesOfMemeOverlay(archetype_count=3, segment_seconds=7.0)
    blank = Image.new("RGB", (WIDTH, HEIGHT), BG_DEEP)

    outside = blank.copy()
    assert overlay.apply(outside, t=3 * 7.0 + 1.0) is False  # past the last segment
    assert outside.tobytes() == blank.tobytes()

    inside = blank.copy()
    assert overlay.apply(inside, t=ESCALATION_FREEZE_S + 1.0) is True
    assert inside.tobytes() != blank.tobytes()


def _wav(path: Path, seconds: float) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


def _section(tmp_path: Path, duration: float) -> list[SectionAudio]:
    words = [{"word": f"w{k}", "start": k * 0.2, "end": k * 0.2 + 0.18} for k in range(5)]
    return [
        SectionAudio(
            section=Section(heading="Part 1", narration="w0 w1 w2 w3 w4"),
            audio_path=_wav(tmp_path / "s0.wav", duration),
            words=words,
            duration=duration,
        )
    ]


def _frame_image(raw: bytes) -> Image.Image:
    return Image.frombytes("RGB", (WIDTH, HEIGHT), raw)


def test_render_path_draws_vignette_only_when_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("AUTOSHORTS_MEME_ENGINE", raising=False)
    secs = _section(tmp_path, duration=3.6)  # badge at 2.52s, radar full at 3.4s

    off = list(render_scene_frames(secs, "Wiring", meme_engine=False))
    default = list(render_scene_frames(secs, "Wiring"))
    on = list(render_scene_frames(secs, "Wiring", meme_engine=True))

    assert len(off) == len(on) == 108
    # Default (env unset) is byte-identical to explicit off: the path is unchanged.
    assert default[-1] == off[-1]
    assert default[0] == off[0]

    # The first archetype segment starts at t=0 (action scene before the freeze), so the
    # overlay is already drawing on frame 0 when enabled.
    assert on[0] != off[0]

    # Last frame: vignette badge and radar are present only when enabled.
    off_img, on_img = _frame_image(off[-1]), _frame_image(on[-1])
    assert (
        _count_near(on_img, BADGE_REGION, ACCENT_CORAL)
        > _count_near(off_img, BADGE_REGION, ACCENT_CORAL) + 100
    )
    assert (
        _count_near(on_img, RADAR_REGION, ACCENT_CORAL)
        > _count_near(off_img, RADAR_REGION, ACCENT_CORAL) + 100
    )


@pytest.mark.parametrize("value", ["", "0", "false"])
def test_env_falsey_values_keep_engine_off(monkeypatch, value) -> None:
    monkeypatch.setenv("AUTOSHORTS_MEME_ENGINE", value)
    assert resolve_meme_overlay(None) is None
