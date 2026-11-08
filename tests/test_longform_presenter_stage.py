"""Unit tests for the Presenter Stage and Auto-Hide module (no network, exact numbers)."""

from __future__ import annotations

import math

import pytest
from PIL import Image

from autoshorts.longform import presenter_stage as ps
from autoshorts.longform.drawer import FLOOR_Y, HEIGHT, WIDTH, viseme_at
from autoshorts.longform.motion import blink
from autoshorts.longform.presenter_stage import (
    ACTIVE_SPEAKER_SCALE,
    AUTO_HIDE_GLIDE_SECONDS,
    AUTO_HIDE_OFFSET_X,
    AUTO_HIDE_SALIENCE_THRESHOLD,
    INACTIVE_SPEAKER_SCALE,
    AutoHideController,
    AutoHideState,
    DuoBanterCoordinator,
    PresenterCharacter,
    PresenterConfig,
    PresenterSlot,
    PresenterStage,
)
from autoshorts.longform.rig import EXPRESSIONS

BG = (14, 18, 38)


def _blank() -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT), BG)


# ---------------------------------------------------------------------------
# Original behaviour
# ---------------------------------------------------------------------------
def test_auto_hide_controller_threshold() -> None:
    ctrl = AutoHideController(
        salience_threshold=0.8,
        glide_duration=0.35,
        glide_offset_x=400.0,
    )
    # Direct instantaneous salience queries
    assert ctrl.compute_hide_factor(0.0, current_salience=0.5) == 0.0
    assert ctrl.compute_hide_factor(0.0, current_salience=0.8) == 0.0
    assert ctrl.compute_hide_factor(0.0, current_salience=0.81) == 1.0
    assert ctrl.compute_hide_factor(0.0, current_salience=1.0) == 1.0


def test_auto_hide_glide_timing() -> None:
    ctrl = AutoHideController(
        salience_threshold=0.8,
        glide_duration=0.35,
        glide_offset_x=400.0,
    )
    ctrl.add_salience_span(start=2.0, end=5.0, salience=0.95)

    # Before span starts: fully on stage
    assert ctrl.compute_hide_factor(1.5) == 0.0
    assert ctrl.compute_hide_factor(2.0) == 0.0

    # During 350ms glide out (2.0s to 2.35s)
    f_mid_out = ctrl.compute_hide_factor(2.0 + 0.35 / 2)
    assert 0.2 < f_mid_out < 0.8

    # Fully hidden
    assert ctrl.compute_hide_factor(2.35) == 1.0
    assert ctrl.compute_hide_factor(3.5) == 1.0
    assert ctrl.compute_hide_factor(5.0) == 1.0

    # During 350ms glide in (5.0s to 5.35s)
    f_mid_in = ctrl.compute_hide_factor(5.0 + 0.35 / 2)
    assert 0.2 < f_mid_in < 0.8

    # Completely back on stage
    assert ctrl.compute_hide_factor(5.35) == 0.0
    assert ctrl.compute_hide_factor(6.0) == 0.0


def test_duo_banter_spotlight_scales() -> None:
    coord = DuoBanterCoordinator(character_a_id="host", character_b_id="guest")
    coord.add_turn("host", start=0.0, end=3.0)
    coord.add_turn("guest", start=3.0, end=6.0)

    weights_host = coord.get_speaker_weights(1.0)
    assert weights_host["host"] == 1.0
    assert weights_host["guest"] == 0.0
    assert round(coord.get_speaker_scale(weights_host["host"]), 2) == ACTIVE_SPEAKER_SCALE
    assert round(coord.get_speaker_scale(weights_host["guest"]), 2) == INACTIVE_SPEAKER_SCALE

    weights_guest = coord.get_speaker_weights(4.5)
    assert weights_guest["host"] == 0.0
    assert weights_guest["guest"] == 1.0
    assert round(coord.get_speaker_scale(weights_guest["guest"]), 2) == ACTIVE_SPEAKER_SCALE
    assert round(coord.get_speaker_scale(weights_guest["host"]), 2) == INACTIVE_SPEAKER_SCALE


def test_presenter_stage_solo_rendering() -> None:
    stage = PresenterStage.create_solo_stage()
    states = stage.update_and_draw(_blank(), t=0.5, current_salience=0.2)
    assert "presenter" in states
    assert states["presenter"].glide_offset_x == 0.0
    assert states["presenter"].hide_factor == 0.0
    assert states["presenter"].scale_multiplier == ACTIVE_SPEAKER_SCALE


def test_presenter_stage_auto_hide_execution() -> None:
    stage = PresenterStage.create_duo_stage(char_a_id="char_a", char_b_id="char_b")
    states = stage.update_and_draw(
        _blank(),
        t=1.0,
        current_salience=0.95,
        active_speaker="char_a",
    )
    # Character A (anchor x=520) slides to absolute x=-400: offset -920
    assert states["char_a"].glide_offset_x == -920.0
    assert states["char_a"].hide_factor == 1.0
    # Character B (anchor x=1400) slides to the mirrored edge x=WIDTH+400: offset +920
    assert states["char_b"].glide_offset_x == 920.0
    assert states["char_b"].hide_factor == 1.0


# ---------------------------------------------------------------------------
# Point 1: solo presenter (idle bob 0.8 Hz, blinks 3-5 s, visemes, brows)
# ---------------------------------------------------------------------------
def test_idle_bob_is_sine_at_0_8_hz() -> None:
    assert ps.IDLE_BOB_HZ == 0.8
    period = 1.0 / 0.8  # 1.25 s
    assert ps.idle_bob_offset(0.0) == pytest.approx(0.0, abs=1e-9)
    assert ps.idle_bob_offset(period) == pytest.approx(0.0, abs=1e-9)
    assert ps.idle_bob_offset(period / 4) == pytest.approx(ps.IDLE_BOB_AMPLITUDE_PX)
    assert ps.idle_bob_offset(3 * period / 4) == pytest.approx(-ps.IDLE_BOB_AMPLITUDE_PX)
    t = 0.37
    assert ps.idle_bob_offset(t) == pytest.approx(
        ps.IDLE_BOB_AMPLITUDE_PX * math.sin(2 * math.pi * 0.8 * t)
    )


def test_blink_interval_is_between_3_and_5_seconds() -> None:
    period = ps.BLINK_PERIOD_SECONDS
    assert 3.0 <= period <= 5.0
    centre = period * 0.62  # motion.BLINK_CENTER_FRACTION
    assert blink(centre) == pytest.approx(0.12)  # closed at the blink instant
    assert blink(centre + period) == pytest.approx(0.12)  # and again one period later
    assert blink(centre + 0.5) == pytest.approx(1.0)  # open between blinks


def test_viseme_shapes_follow_narration_words() -> None:
    assert viseme_at([{"word": "mab", "start": 0.0, "end": 0.5}], 0.0) == "MBP"
    assert viseme_at([{"word": "at", "start": 0.0, "end": 0.5}], 0.0) == "AI"
    assert viseme_at([{"word": "obe", "start": 0.0, "end": 0.5}], 0.0) == "O"
    assert viseme_at([{"word": "tee", "start": 0.0, "end": 0.5}], 0.0) == "E"
    assert viseme_at([{"word": "at", "start": 0.0, "end": 0.5}], 2.0) == "REST"


def test_solo_presenter_receives_narration_viseme() -> None:
    stage = PresenterStage.create_solo_stage(engine="drawer")
    words = [{"word": "at", "start": 0.0, "end": 0.5}]
    states = stage.update_and_draw(_blank(), t=0.0, words=words, draw_stage_floor=False)
    assert states["presenter"].viseme == "AI"
    states = stage.update_and_draw(_blank(), t=0.0, words=None, draw_stage_floor=False)
    assert states["presenter"].viseme == "REST"


def test_brow_expressions_resolve_for_rig_and_drawer() -> None:
    assert set(ps.BROW_EXPRESSIONS) == {"curiosity", "excitement", "skepticism", "surprise"}
    drawer_moods = {"think", "excited", "deadpan", "shock"}
    char = PresenterCharacter(PresenterConfig(character_id="p"))
    for name, (rig_expr, drawer_mood) in ps.BROW_EXPRESSIONS.items():
        assert rig_expr in EXPRESSIONS
        assert drawer_mood in drawer_moods
        char.state.expression = name
        assert char._rig_expression() == rig_expr
        assert char._drawer_mood() == drawer_mood


def test_each_brow_expression_renders_a_different_face() -> None:
    frames = []
    for name in ps.BROW_EXPRESSIONS:
        stage = PresenterStage.create_solo_stage(engine="rig")
        img = _blank()
        stage.update_and_draw(
            img,
            t=0.5,
            current_salience=0.2,
            custom_expressions={"presenter": name},
            draw_stage_floor=False,
        )
        frames.append(img.tobytes())
    assert len(set(frames)) == len(frames)


# ---------------------------------------------------------------------------
# Point 2: duo banter geometry, spotlight scale, listener dimming, gaze, handoff
# ---------------------------------------------------------------------------
def test_duo_anchor_positions_on_canvas() -> None:
    assert ps.SLOT_ANCHORS["left"][0] == 520.0  # character A
    assert ps.SLOT_ANCHORS["right"][0] == 1400.0  # character B
    assert ps.SLOT_ANCHORS["left"][0] + ps.SLOT_ANCHORS["right"][0] == WIDTH  # symmetric
    stage = PresenterStage.create_duo_stage()
    assert stage.characters["character_a"].cfg.anchor[0] == 520.0
    assert stage.characters["character_b"].cfg.anchor[0] == 1400.0


def test_duo_spotlight_scale_opacity_and_gaze() -> None:
    stage = PresenterStage.create_duo_stage(char_a_id="char_a", char_b_id="char_b")
    stage.banter.add_turn("char_a", start=0.0, end=3.0)
    stage.banter.add_turn("char_b", start=3.0, end=6.0)

    states = stage.update_and_draw(_blank(), t=1.0, draw_stage_floor=False)  # A speaks
    assert states["char_a"].scale_multiplier == pytest.approx(1.05)
    assert states["char_a"].opacity == pytest.approx(1.0)
    assert states["char_b"].scale_multiplier == pytest.approx(0.95)
    assert states["char_b"].opacity == pytest.approx(0.75)
    assert states["char_b"].head_turn == pytest.approx(-0.4)  # B (right) looks left at A

    states = stage.update_and_draw(_blank(), t=4.5, draw_stage_floor=False)  # B speaks
    assert states["char_b"].scale_multiplier == pytest.approx(1.05)
    assert states["char_b"].opacity == pytest.approx(1.0)
    assert states["char_a"].scale_multiplier == pytest.approx(0.95)
    assert states["char_a"].opacity == pytest.approx(0.75)
    assert states["char_a"].head_turn == pytest.approx(0.4)  # A (left) looks right at B


def test_duo_handoff_follows_transcript_turns_without_inversion() -> None:
    coord = DuoBanterCoordinator(character_a_id="a", character_b_id="b")
    coord.add_turn("a", start=0.0, end=2.0)
    coord.add_turn("b", start=3.0, end=5.0)
    # At the start of A's turn nobody has weight yet; B must not take over.
    assert coord.get_speaker_weights(0.0) == {"a": 0.0, "b": 0.0}
    # Ramp over the 250 ms tween.
    assert coord.get_speaker_weights(0.125)["a"] == pytest.approx(0.5)
    assert coord.get_speaker_weights(0.25) == {"a": 1.0, "b": 0.0}
    # Gap between turns: both listening.
    assert coord.get_speaker_weights(2.5) == {"a": 0.0, "b": 0.0}
    # B's turn: B speaks, A listens.
    assert coord.get_speaker_weights(4.0) == {"a": 0.0, "b": 1.0}


def test_listener_is_never_marked_speaking_during_partner_turn() -> None:
    stage = PresenterStage.create_duo_stage(char_a_id="a", char_b_id="b")
    stage.banter.add_turn("a", start=0.0, end=2.0)
    for t in (0.0, 0.1, 0.5, 1.5):
        states = stage.update_and_draw(_blank(), t=t, draw_stage_floor=False)
        assert states["b"].is_speaking is False, t


# ---------------------------------------------------------------------------
# Point 3: auto-dismiss thresholds, 350 ms glide, <= 400 ms hide and show
# ---------------------------------------------------------------------------
def test_salience_threshold_on_realistic_inputs() -> None:
    assert AUTO_HIDE_SALIENCE_THRESHOLD == 0.8
    ctrl = AutoHideController()
    # Narration and ordinary speech keep the presenter on stage.
    for s in (0.0, 0.3, 0.6, 0.8):
        assert ctrl.compute_hide_factor(1.0, current_salience=s) == 0.0, s
    # Charts, infographics and memes (> 0.8) dismiss it.
    for s in (0.81, 0.9, 0.95, 1.0):
        assert ctrl.compute_hide_factor(1.0, current_salience=s) == 1.0, s


def test_narration_spans_never_dismiss() -> None:
    ctrl = AutoHideController()
    ctrl.add_salience_span(0.0, 10.0, salience=0.5)  # narration-level salience
    ctrl.add_salience_span(2.0, 4.0, salience=0.8)  # exactly at threshold: still shown
    for t in (0.0, 2.0, 3.0, 4.0, 9.0):
        assert ctrl.compute_hide_factor(t) == 0.0, t


def test_glide_is_350ms_and_hide_show_within_400ms() -> None:
    ctrl = AutoHideController()
    assert AUTO_HIDE_GLIDE_SECONDS == pytest.approx(0.35)
    assert AUTO_HIDE_OFFSET_X == 400.0
    ctrl.add_salience_span(2.0, 5.0, salience=0.95)
    assert ctrl.compute_hide_factor(2.0) == 0.0
    assert ctrl.compute_hide_factor(2.0 + 0.175) == pytest.approx(0.5)  # cubic midpoint
    assert ctrl.compute_hide_factor(2.0 + 0.35) == pytest.approx(1.0)  # hidden at 350 ms
    assert ctrl.compute_hide_factor(2.0 + 0.4) == pytest.approx(1.0)
    assert ctrl.compute_hide_factor(5.0 + 0.35) == pytest.approx(0.0)  # shown at 350 ms
    assert ctrl.compute_hide_factor(5.0 + 0.4) == pytest.approx(0.0)


def test_realtime_salience_overrides_spans() -> None:
    ctrl = AutoHideController()
    ctrl.add_salience_span(2.0, 5.0, salience=0.95)
    assert ctrl.compute_hide_factor(10.0, current_salience=0.95) == 1.0
    assert ctrl.compute_hide_factor(3.0, current_salience=0.2) == 0.0


def test_stage_glide_targets_minus_400_px_and_restores() -> None:
    stage = PresenterStage.create_solo_stage(engine="rig")
    stage.auto_hide.add_salience_span(2.0, 5.0, salience=0.95)
    anchor = stage.characters["presenter"].cfg.anchor[0]
    s = stage.update_and_draw(_blank(), t=3.0, draw_stage_floor=False)["presenter"]
    assert anchor + s.glide_offset_x == pytest.approx(-400.0)  # absolute target x
    assert s.hide_factor == 1.0
    s = stage.update_and_draw(_blank(), t=5.5, draw_stage_floor=False)["presenter"]
    assert s.glide_offset_x == pytest.approx(0.0)  # back on stage
    assert s.hide_factor == 0.0


def test_stage_auto_hide_reports_states() -> None:
    assert {s.value for s in AutoHideState} == {"on_stage", "gliding_out", "hidden", "gliding_in"}
    assert PresenterSlot.LEFT.value == "left"


# ---------------------------------------------------------------------------
# Opacity: listener dimming composites a faded figure
# ---------------------------------------------------------------------------
def test_dimmed_figure_is_fainter_than_full_figure() -> None:
    cfg = PresenterConfig(character_id="p", engine="rig", anchor=(520.0, FLOOR_Y))
    box = (300, FLOOR_Y - 300, 740, FLOOR_Y)

    def deviation(opacity: float) -> int:
        img = _blank()
        char = PresenterCharacter(cfg)
        char.update(
            t=0.5,
            is_speaking=False,
            scale_mult=1.0,
            hide_dx=0.0,
            hide_factor=0.0,
            opacity=opacity,
        )
        char.draw(img, 0.5)
        data = img.crop(box).tobytes()
        background = bytes(BG) * (len(data) // 3)
        return sum(abs(a - b) for a, b in zip(data, background, strict=True))

    full = deviation(1.0)
    dim = deviation(0.75)
    assert full > 0
    assert 0 < dim < full
