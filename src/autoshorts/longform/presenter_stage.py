"""Presenter stage and dynamic auto-hide system for longform explainer animations.

Features:
- Expressive 2D presenter character(s) that move, gesture, and explain dynamically as
  backgrounds change.
- Multi-character support: Character A & Character B duo banter with active speaker
  spotlighting (scale 1.05x vs 0.95x, floor spotlight, listening reaction poses).
- Auto-hide logic: When complex diagrams, infographics, or meme visuals appear
  (graphic_salience > 0.8), the presenter smoothly glides off-screen (x: -400px over 350ms)
  so the viewer can focus entirely on the graphic and subtitles.
- Seamless integration with autoshorts.longform.rig and autoshorts.longform.drawer.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from PIL import Image, ImageDraw

from autoshorts.core.theme import (
    ACCENT_CORAL,
    BG_SURFACE,
    ease_in_out_cubic,
)
from autoshorts.longform.drawer import (
    FLOOR_Y,
    WIDTH,
    draw_character,
    viseme_at,
)
from autoshorts.longform.drawer import (
    POSES as DRAWER_POSES,
)
from autoshorts.longform.motion import (
    blink,
)
from autoshorts.longform.rig import (
    CREAM,
    EXPRESSIONS,
    POSES,
    SHIRT,
    SHIRT_SHADE,
    draw_rig,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AUTO_HIDE_SALIENCE_THRESHOLD = 0.8
AUTO_HIDE_GLIDE_MS = 350.0  # 350ms smooth glide
AUTO_HIDE_GLIDE_SECONDS = AUTO_HIDE_GLIDE_MS / 1000.0  # 0.35s
AUTO_HIDE_OFFSET_X = 400.0  # distance past the frame edge the presenter glides to
# Absolute x the left-side presenter glides to (-400 px, fully off-screen). The right-side
# presenter glides to the mirrored edge, WIDTH + 400, so it never crosses the partner.
AUTO_HIDE_TARGET_X = -400.0

ACTIVE_SPEAKER_SCALE = 1.05  # 1.05x spotlight
INACTIVE_SPEAKER_SCALE = 0.95  # 0.95x listening
LISTENER_OPACITY = 0.75  # listeners dim to 0.75 opacity, speaker at 1.0
SPEAKER_TWEEN_SECONDS = 0.25  # 250ms scale/opacity tween between turns

IDLE_BOB_HZ = 0.8  # idle breathing bob frequency
IDLE_BOB_AMPLITUDE_PX = 1.5
BLINK_PERIOD_SECONDS = 3.4  # blink every 3-5 s (motion.blink period)

# Stage positions (hip x in pixels on the 1920x1080 longform canvas). Duo banter places
# character A at x=520 (left) and B at x=1400 (right), symmetric about the centre x=960.
DUO_ANCHOR_A_X = 520.0
DUO_ANCHOR_B_X = 1400.0
SLOT_ANCHORS: dict[str, tuple[float, float]] = {
    "left": (DUO_ANCHOR_A_X, FLOOR_Y),
    "right": (DUO_ANCHOR_B_X, FLOOR_Y),
    "center": (960.0, FLOOR_Y),
    "solo_left": (480.0, FLOOR_Y),
    "solo_right": (1440.0, FLOOR_Y),
}

# Brow expressions requested by name, mapped to the rig and drawer equivalents.
# Rig names are keys of rig.EXPRESSIONS; drawer names are moods understood by _brow().
BROW_EXPRESSIONS: dict[str, tuple[str, str]] = {
    # name: (rig expression, drawer mood)
    "curiosity": ("confused", "think"),
    "excitement": ("excited", "excited"),
    "skepticism": ("smug", "deadpan"),
    "surprise": ("surprised", "shock"),
}

VISEMES = ("REST", "AI", "O", "E", "MBP")

# Gestures mapped to rig poses
EXPLAIN_GESTURES = (
    "gesture_open_hands",
    "gesture_point_up",
    "point",
    "thumbs_up",
    "gesture_shrug",
)

# Rig gesture names mapped to the drawer engine's joint poses (drawer.POSES keys).
DRAWER_POSE_FOR: dict[str, str] = {
    "gesture_open_hands": "present",
    "gesture_point_up": "point",
    "point": "point",
    "thumbs_up": "lift",
    "gesture_shrug": "shrug",
}

LISTENING_POSES = (
    "think",
    "stand",
    "gesture_shrug",
    "look_left",
    "look_right",
)


class PresenterSlot(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    CENTER = "center"
    SOLO_LEFT = "solo_left"
    SOLO_RIGHT = "solo_right"


class AutoHideState(StrEnum):
    ON_STAGE = "on_stage"
    GLIDING_OUT = "gliding_out"
    HIDDEN = "hidden"
    GLIDING_IN = "gliding_in"


# ---------------------------------------------------------------------------
# Auto-Hide Controller
# ---------------------------------------------------------------------------
@dataclass
class SalienceSpan:
    """Interval of high graphic salience (infographic, diagram, meme)."""

    start: float
    end: float
    salience: float = 1.0


class AutoHideController:
    """Controls smooth off-screen gliding (x: -400px over 350ms) when graphic_salience > 0.8.

    Guarantees viewers have 100% unobstructed focus on complex diagrams, charts,
    and lower-third subtitles.
    """

    def __init__(
        self,
        salience_threshold: float = AUTO_HIDE_SALIENCE_THRESHOLD,
        glide_duration: float = AUTO_HIDE_GLIDE_SECONDS,
        glide_offset_x: float = AUTO_HIDE_OFFSET_X,
        easing_fn: Callable[[float], float] = ease_in_out_cubic,
    ) -> None:
        self.salience_threshold = salience_threshold
        self.glide_duration = max(0.05, glide_duration)
        self.glide_offset_x = glide_offset_x
        self.easing = easing_fn
        self.spans: list[SalienceSpan] = []

    def add_salience_span(self, start: float, end: float, salience: float = 1.0) -> None:
        """Register a time interval where graphic salience is active."""
        if end > start:
            self.spans.append(SalienceSpan(start=start, end=end, salience=salience))
            self.spans.sort(key=lambda s: s.start)

    def get_salience_at(self, t: float) -> float:
        """Query instantaneous graphic salience at time t."""
        max_s = 0.0
        for span in self.spans:
            if span.start <= t <= span.end:
                max_s = max(max_s, span.salience)
        return max_s

    def compute_hide_factor(self, t: float, current_salience: float | None = None) -> float:
        """Calculate the normalized hide progress in [0.0, 1.0].

        0.0 = completely on-stage (resting position).
        1.0 = completely glided off-screen (displaced by -400px).
        """
        # An explicit realtime salience is a hard switch: > threshold dismisses, otherwise shows.
        if current_salience is not None:
            return 1.0 if current_salience > self.salience_threshold else 0.0

        if not self.spans:
            return 0.0

        # Spans drive the 350ms glide. Narration (salience <= threshold) never dismisses.
        best_factor = 0.0
        for span in self.spans:
            if span.salience <= self.salience_threshold:
                continue

            # Before span:
            if t < span.start:
                continue

            # Entering glide: span.start <= t <= span.start + glide_duration
            if t < span.start + self.glide_duration:
                p = (t - span.start) / self.glide_duration
                factor = self.easing(min(1.0, max(0.0, p)))
                best_factor = max(best_factor, factor)
            # Fully hidden during the high-salience body:
            elif t <= span.end:
                best_factor = max(best_factor, 1.0)
            # Exiting glide: span.end < t <= span.end + glide_duration
            elif t <= span.end + self.glide_duration:
                p = (t - span.end) / self.glide_duration
                factor = 1.0 - self.easing(min(1.0, max(0.0, p)))
                best_factor = max(best_factor, factor)

        return best_factor

    def get_glide_offset(
        self,
        t: float,
        slot_direction: float = -1.0,
        current_salience: float | None = None,
    ) -> tuple[float, float, float]:
        """Return (dx, dy, hide_factor).

        slot_direction: -1.0 glides off to screen left (dx = -400px),
                        +1.0 glides off to screen right (dx = +400px).
        """
        hide_factor = self.compute_hide_factor(t, current_salience=current_salience)
        dx = slot_direction * self.glide_offset_x * hide_factor
        dy = 0.0
        return (dx, dy, hide_factor)


# ---------------------------------------------------------------------------
# Presenter Character Configuration & State
# ---------------------------------------------------------------------------
@dataclass
class PresenterConfig:
    """Styling and structural parameters for an expressive 2D presenter."""

    character_id: str
    name: str = "Presenter"
    slot: PresenterSlot | str = PresenterSlot.LEFT
    anchor: tuple[float, float] = (440.0, FLOOR_Y)
    base_scale: float = 1.0  # Rig body scale (~1.0 for Kurzgesagt rig, ~1.9 for drawer)
    engine: str = "rig"  # "rig" or "drawer"
    shirt_color: tuple[int, int, int] = SHIRT
    shirt_shade: tuple[int, int, int] = SHIRT_SHADE
    skin_color: tuple[int, int, int] = CREAM
    hair_color: tuple[int, int, int] = (92, 52, 28)
    headband: bool = False
    mirror: bool = False
    default_pose: str = "stand"
    default_expression: str = "neutral"
    glide_direction: float = -1.0  # -1.0 off left, +1.0 off right


@dataclass
class CharacterState:
    """Live animation frame state for one presenter."""

    pose: str = "stand"
    expression: str = "neutral"
    viseme: str = "REST"
    scale_multiplier: float = 1.0
    is_speaking: bool = False
    head_turn: float = 0.0
    hand_lift: float = 0.0
    pointer_stick: bool = False
    glide_offset_x: float = 0.0
    hide_factor: float = 0.0
    opacity: float = 1.0


def idle_bob_offset(t: float) -> float:
    """Vertical idle breathing bob in px: a sine at IDLE_BOB_HZ (0.8 Hz)."""
    return math.sin(2.0 * math.pi * IDLE_BOB_HZ * t) * IDLE_BOB_AMPLITUDE_PX


def _apply_opacity(layer: Image.Image, opacity: float) -> None:
    """Scale the alpha channel of an RGBA layer in place by ``opacity`` (0 to 1)."""
    alpha = layer.getchannel("A").point(lambda v: int(round(v * opacity)))
    layer.putalpha(alpha)


# ---------------------------------------------------------------------------
# Duo Banter Coordinator
# ---------------------------------------------------------------------------
@dataclass
class BanterTurn:
    """One speaking turn in a dialogue/banter exchange."""

    speaker_id: str
    start: float
    end: float
    text: str = ""
    mood: str = "happy"
    gesture: str | None = None


class DuoBanterCoordinator:
    """Coordinates two-character parody banter, turn-taking, and spotlight scaling."""

    def __init__(
        self,
        character_a_id: str = "char_a",
        character_b_id: str = "char_b",
        tween_duration: float = SPEAKER_TWEEN_SECONDS,
    ) -> None:
        self.char_a = character_a_id
        self.char_b = character_b_id
        self.tween_duration = max(0.05, tween_duration)
        self.turns: list[BanterTurn] = []

    def add_turn(
        self,
        speaker_id: str,
        start: float,
        end: float,
        text: str = "",
        mood: str = "happy",
        gesture: str | None = None,
    ) -> None:
        self.turns.append(
            BanterTurn(
                speaker_id=speaker_id,
                start=start,
                end=end,
                text=text,
                mood=mood,
                gesture=gesture,
            )
        )
        self.turns.sort(key=lambda t: t.start)

    def get_active_turn(self, t: float) -> BanterTurn | None:
        for turn in self.turns:
            if turn.start <= t <= turn.end:
                return turn
        return None

    def get_speaker_weights(self, t: float) -> dict[str, float]:
        """Compute smooth speaker prominence weights in [0.0, 1.0].

        1.0 means fully active speaker.
        0.0 means fully listening partner.
        """
        active_turn = self.get_active_turn(t)
        active_id = active_turn.speaker_id if active_turn else None

        # Base default: if no one is explicitly speaking, neutral 0.5
        if not self.turns:
            return {self.char_a: 0.5, self.char_b: 0.5}

        # Find recent transitions for smooth tweening
        weight_a = 0.0
        weight_b = 0.0

        # Only the transcript's active turn gets weight; it ramps 0 -> 1 over the tween so the
        # speaker never flips to the partner. The partner stays at 0 (listening).
        if active_turn is not None:
            dt = t - active_turn.start
            prog = min(1.0, dt / self.tween_duration)
            ramp = ease_in_out_cubic(prog)
            if active_id == self.char_a:
                weight_a = ramp
            elif active_id == self.char_b:
                weight_b = ramp

        return {self.char_a: weight_a, self.char_b: weight_b}

    def get_speaker_scale(self, weight: float) -> float:
        """Calculate scale from weight: 0.0 -> 0.95x, 1.0 -> 1.05x."""
        return INACTIVE_SPEAKER_SCALE + (ACTIVE_SPEAKER_SCALE - INACTIVE_SPEAKER_SCALE) * weight


# ---------------------------------------------------------------------------
# Presenter Character
# ---------------------------------------------------------------------------
class PresenterCharacter:
    """Expressive 2D presenter character that renders articulately on the stage."""

    def __init__(self, config: PresenterConfig) -> None:
        self.cfg = config
        self.state = CharacterState(
            pose=config.default_pose,
            expression=config.default_expression,
        )

    def update(
        self,
        t: float,
        is_speaking: bool,
        scale_mult: float,
        hide_dx: float,
        hide_factor: float,
        custom_pose: str | None = None,
        custom_expr: str | None = None,
        viseme: str = "REST",
        head_turn: float = 0.0,
        pointer_stick: bool = False,
        opacity: float = 1.0,
    ) -> None:
        """Update character state for time t."""
        self.state.is_speaking = is_speaking
        self.state.scale_multiplier = scale_mult
        self.state.glide_offset_x = hide_dx
        self.state.hide_factor = hide_factor
        self.state.viseme = viseme if viseme in VISEMES else "REST"
        self.state.head_turn = head_turn
        self.state.pointer_stick = pointer_stick
        self.state.opacity = min(1.0, max(0.0, opacity))

        if custom_pose:
            self.state.pose = custom_pose
        elif is_speaking:
            # Dynamic cadence gesture cycling
            cycle_idx = int((t * 0.9) % len(EXPLAIN_GESTURES))
            self.state.pose = EXPLAIN_GESTURES[cycle_idx]
        else:
            self.state.pose = self.cfg.default_pose

        if custom_expr:
            self.state.expression = custom_expr
        elif is_speaking:
            self.state.expression = "happy" if (int(t * 1.5) % 2 == 0) else "focus"
        else:
            self.state.expression = self.cfg.default_expression

    def _rig_expression(self) -> str:
        name = BROW_EXPRESSIONS.get(self.state.expression, (self.state.expression, ""))[0]
        return name if name in EXPRESSIONS else "neutral"

    def _drawer_mood(self) -> str:
        if self.state.expression in BROW_EXPRESSIONS:
            return BROW_EXPRESSIONS[self.state.expression][1]
        return self.state.expression

    def _draw_figure(self, target: Image.Image, t: float, x: float, y: float, scale: float) -> None:
        if self.cfg.engine == "drawer":
            # Autoshorts drawer flat-vector round-headed presenter
            drawer_name = DRAWER_POSE_FOR.get(self.state.pose, self.state.pose)
            drawer_pose = DRAWER_POSES.get(drawer_name, DRAWER_POSES["stand"])
            draw_character(
                draw=ImageDraw.Draw(target),
                pose=drawer_pose,
                origin=(x, y),
                scale=scale,
                shirt=self.cfg.shirt_color,
                skin=self.cfg.skin_color,
                hair=self.cfg.hair_color,
                mood=self._drawer_mood(),
                flip=-1 if self.cfg.mirror else 1,
                blink=(blink(t) < 0.25),
                viseme=self.state.viseme,
                pointer_stick=self.state.pointer_stick,
                is_speaking=self.state.is_speaking,
            )
        else:
            # Autoshorts rig.py Kurzgesagt-style articulated figure
            pose_name = self.state.pose if self.state.pose in POSES else "stand"
            draw_rig(
                img=target,
                pose=pose_name,
                origin=(x, y),
                scale=scale,
                expression=self._rig_expression(),
                headband=self.cfg.headband,
                t=t,
                mirror=self.cfg.mirror,
                head_turn=self.state.head_turn,
                hand_lift=self.state.hand_lift,
            )

    def draw(self, img: Image.Image, t: float) -> None:
        """Render character onto image canvas, taking into account auto-hide, scale and opacity."""
        # Fully dismissed (or fully transparent) characters draw nothing at all.
        if self.state.hide_factor >= 0.999 or self.state.opacity <= 0.0:
            return

        effective_x = self.cfg.anchor[0] + self.state.glide_offset_x
        # Off-canvas check: if character bounds are completely out of view
        if effective_x < -300 or effective_x > WIDTH + 300:
            return

        effective_y = self.cfg.anchor[1] + idle_bob_offset(t)
        scale = self.cfg.base_scale * self.state.scale_multiplier

        if self.state.opacity >= 0.999:
            self._draw_figure(img, t, effective_x, effective_y, scale)
            return

        # Listeners are dimmed: draw on a transparent layer, scale its alpha, then composite.
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        self._draw_figure(layer, t, effective_x, effective_y, scale)
        _apply_opacity(layer, self.state.opacity)
        if img.mode == "RGBA":
            img.alpha_composite(layer)
        else:
            base = img.convert("RGBA")
            base.alpha_composite(layer)
            img.paste(base.convert(img.mode))


# ---------------------------------------------------------------------------
# Presenter Stage Manager
# ---------------------------------------------------------------------------
class PresenterStage:
    """Master Presenter Stage managing multi-character banter and auto-hide logic.

    Features:
    - Auto-hide controller: when graphic_salience > 0.8, characters glide off-screen
      (x: -400px over 350ms) to ensure diagrams, charts, and subtitles have 100% focus.
    - Duo banter spotlighting: Active speaker scales to 1.05x with vibrant gestures &
      floor spotlight; listening character scales to 0.95x in reactive listening pose.
    - Seamless multi-character composition onto 1920x1080 canvas.
    """

    def __init__(
        self,
        characters: list[PresenterConfig] | None = None,
        auto_hide_controller: AutoHideController | None = None,
        banter_coordinator: DuoBanterCoordinator | None = None,
    ) -> None:
        self.auto_hide = auto_hide_controller or AutoHideController()
        self.banter = banter_coordinator

        self.characters: dict[str, PresenterCharacter] = {}
        if characters:
            for cfg in characters:
                self.add_character(cfg)

    def add_character(self, config: PresenterConfig) -> PresenterCharacter:
        char = PresenterCharacter(config)
        self.characters[config.character_id] = char
        return char

    @classmethod
    def create_duo_stage(
        cls,
        char_a_id: str = "character_a",
        char_b_id: str = "character_b",
        char_a_name: str = "Host",
        char_b_name: str = "Skeptic",
        engine: str = "rig",
    ) -> PresenterStage:
        """Factory: Create standard 2-character duo banter stage."""
        config_a = PresenterConfig(
            character_id=char_a_id,
            name=char_a_name,
            slot=PresenterSlot.LEFT,
            anchor=SLOT_ANCHORS["left"],
            base_scale=1.0 if engine == "rig" else 1.9,
            engine=engine,
            shirt_color=SHIRT,
            mirror=False,  # faces right toward partner
            glide_direction=-1.0,  # glides off left (-400px)
        )
        config_b = PresenterConfig(
            character_id=char_b_id,
            name=char_b_name,
            slot=PresenterSlot.RIGHT,
            anchor=SLOT_ANCHORS["right"],
            base_scale=1.0 if engine == "rig" else 1.9,
            engine=engine,
            shirt_color=ACCENT_CORAL,
            mirror=True,  # faces left toward partner
            glide_direction=1.0,  # glides off right (+400px)
        )
        coordinator = DuoBanterCoordinator(
            character_a_id=char_a_id,
            character_b_id=char_b_id,
        )
        return cls(
            characters=[config_a, config_b],
            banter_coordinator=coordinator,
        )

    @classmethod
    def create_solo_stage(
        cls,
        character_id: str = "presenter",
        name: str = "Presenter",
        slot: str = "solo_left",
        engine: str = "rig",
    ) -> PresenterStage:
        """Factory: Create standard solo explainer presenter stage."""
        anchor = SLOT_ANCHORS.get(slot, SLOT_ANCHORS["solo_left"])
        cfg = PresenterConfig(
            character_id=character_id,
            name=name,
            slot=slot,
            anchor=anchor,
            base_scale=1.0 if engine == "rig" else 1.9,
            engine=engine,
            shirt_color=SHIRT,
            mirror=False,
            glide_direction=-1.0,  # glides off left (-400px)
        )
        return cls(characters=[cfg])

    def _draw_floor_spotlight(
        self,
        img: Image.Image,
        center_x: float,
        floor_y: float,
        scale: float,
        is_active: bool,
        alpha_mult: float = 1.0,
    ) -> None:
        """Draw an atmospheric floor shadow or active speaker spotlight."""
        if alpha_mult <= 0.01:
            return

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)

        # Contact shadow (always present under feet)
        rx = 48.0 * scale
        ry = 12.0 * scale
        shadow_alpha = int(70 * alpha_mult)
        d.ellipse(
            [center_x - rx, floor_y - ry, center_x + rx, floor_y + ry],
            fill=(0, 0, 0, shadow_alpha),
        )

        # Active speaker golden luminous spotlight disc
        if is_active:
            spot_rx = 70.0 * scale
            spot_ry = 18.0 * scale
            spot_alpha = int(95 * alpha_mult)
            # Outer halo
            d.ellipse(
                [center_x - spot_rx, floor_y - spot_ry, center_x + spot_rx, floor_y + spot_ry],
                fill=(255, 215, 80, int(spot_alpha * 0.4)),
            )
            # Core bright ring
            core_rx = 46.0 * scale
            core_ry = 12.0 * scale
            d.ellipse(
                [center_x - core_rx, floor_y - core_ry, center_x + core_rx, floor_y + core_ry],
                fill=(255, 230, 110, spot_alpha),
            )

        if img.mode == "RGBA":
            img.alpha_composite(overlay)
        else:
            base = img.convert("RGBA")
            base.alpha_composite(overlay)
            img.paste(base.convert(img.mode))

    def update_and_draw(
        self,
        img: Image.Image,
        t: float,
        current_salience: float | None = None,
        active_speaker: str | None = None,
        words: Sequence[dict[str, Any]] | None = None,
        custom_poses: dict[str, str] | None = None,
        custom_expressions: dict[str, str] | None = None,
        draw_stage_floor: bool = True,
    ) -> dict[str, CharacterState]:
        """Update all characters and draw the stage in place onto img.

        Returns dict of {char_id: CharacterState}.
        """
        # 1. Graphic salience & auto-hide factor. An explicit salience is a realtime switch;
        # otherwise the registered salience spans drive the 350 ms glide.
        hide_factor = self.auto_hide.compute_hide_factor(t, current_salience=current_salience)

        # 2. Speaker spotlight weights
        speaker_weights: dict[str, float] = {}
        if active_speaker:
            for cid in self.characters:
                speaker_weights[cid] = 1.0 if cid == active_speaker else 0.0
        elif self.banter:
            speaker_weights = self.banter.get_speaker_weights(t)
        else:
            # Default: all presenters active
            for cid in self.characters:
                speaker_weights[cid] = 1.0

        is_duo = len(self.characters) > 1
        active_turn = self.banter.get_active_turn(t) if self.banter else None
        # Narration words drive mouth shapes; only the character marked as speaking mouths them.
        viseme_now = viseme_at(list(words), t) if words else "REST"

        # Optional stage floor line
        if draw_stage_floor:
            d = ImageDraw.Draw(img)
            d.line([(0, FLOOR_Y), (WIDTH, FLOOR_Y)], fill=BG_SURFACE, width=2)

        results: dict[str, CharacterState] = {}

        # 3. Update & render each character
        for char_id, char in self.characters.items():
            cfg = char.cfg
            weight = speaker_weights.get(char_id, 0.5)
            is_speaking = weight > 0.5

            # Active scale: 1.05x, Inactive scale: 0.95x
            scale_mult = (
                INACTIVE_SPEAKER_SCALE + (ACTIVE_SPEAKER_SCALE - INACTIVE_SPEAKER_SCALE) * weight
            )

            # Listeners dim to LISTENER_OPACITY; the speaker is at full brightness.
            opacity = LISTENER_OPACITY + (1.0 - LISTENER_OPACITY) * weight if is_duo else 1.0

            # Glide to the off-screen target: left side -> x=-400, right side -> x=WIDTH+400.
            target_x = AUTO_HIDE_TARGET_X if cfg.glide_direction < 0 else WIDTH - AUTO_HIDE_TARGET_X
            dx = (target_x - cfg.anchor[0]) * hide_factor

            # Head turn: in duo banter, inactive speaker looks toward active partner
            head_turn = 0.0
            if is_duo:
                if not is_speaking:
                    head_turn = 0.4 if cfg.slot == PresenterSlot.LEFT else -0.4
                else:
                    head_turn = 0.1 * math.sin(t * 2.0)

            # Mouth shapes only while this character is the one speaking.
            viseme = viseme_now if is_speaking else "REST"

            # Expression: explicit override, else the transcript turn's mood for the speaker.
            expr = (custom_expressions or {}).get(char_id)
            if expr is None and active_turn is not None and active_turn.speaker_id == char_id:
                expr = active_turn.mood if active_turn.mood in BROW_EXPRESSIONS else None
            pose = (custom_poses or {}).get(char_id)

            char.update(
                t=t,
                is_speaking=is_speaking,
                scale_mult=scale_mult,
                hide_dx=dx,
                hide_factor=hide_factor,
                custom_pose=pose,
                custom_expr=expr,
                head_turn=head_turn,
                viseme=viseme,
                opacity=opacity,
            )

            # Draw floor spotlight/shadow
            if hide_factor < 0.999:
                curr_x = cfg.anchor[0] + dx
                spotlight_alpha = 1.0 - hide_factor
                self._draw_floor_spotlight(
                    img=img,
                    center_x=curr_x,
                    floor_y=cfg.anchor[1],
                    scale=cfg.base_scale * scale_mult,
                    is_active=is_speaking,
                    alpha_mult=spotlight_alpha,
                )

            # Draw character figure
            char.draw(img, t)
            results[char_id] = char.state

        return results


def presenter_from_config(enabled: bool) -> PresenterStage | None:
    """Build the default solo presenter stage when the config toggle is on, else None.

    The solo narrator is used because the drawer already has the narration words, so mouth
    shapes and the idle bob work without transcript turns. The drawer engine renders the
    viseme mouth shapes. Callers pass the result to ``drawer.render_scene_frames(presenter=...)``;
    None leaves frames unchanged.
    """
    if not enabled:
        return None
    return PresenterStage.create_solo_stage(engine="drawer")
