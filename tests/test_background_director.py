"""Tests for the Dynamic Background Director in AutoShorts long-form.

No network: everything renders procedurally with PIL.
"""

import time
from pathlib import Path

import pytest
from PIL import Image

from autoshorts.longform import background_director
from autoshorts.longform.background_director import (
    AURORA_GREEN,
    COSMIC_INDIGO,
    ENVIRONMENT_PAINTERS,
    HEIGHT,
    NEON_CYAN,
    NEON_PINK,
    NEON_YELLOW,
    TWILIGHT_DEEP,
    VOID_OBSIDIAN,
    WIDTH,
    BackgroundDirector,
    Environment,
    SentenceBeat,
    _base_gradient,
    _ken_burns_params,
    apply_camera_transform,
    apply_ken_burns,
    build_sentence_beats,
    classify_sentence_environment,
    cubic_hermite,
    get_background_director,
    paint_financial_ledger,
    quintic_hermite,
    split_sentences,
)


# ---------------------------------------------------------------------------
# 3. Palette
# ---------------------------------------------------------------------------
def test_spec_palette_tokens() -> None:
    """The spec void bases and accents carry their exact hex values."""
    assert COSMIC_INDIGO == (8, 9, 20)  # #080914
    assert VOID_OBSIDIAN == (11, 14, 27)  # #0B0E1B
    assert TWILIGHT_DEEP == (18, 22, 43)  # #12162B

    assert NEON_YELLOW == (255, 230, 0)  # Solar Yellow #FFE600
    assert NEON_CYAN == (0, 240, 255)  # Supernova Cyan #00F0FF
    assert NEON_PINK == (255, 42, 133)  # Nebula Magenta #FF2A85
    assert AURORA_GREEN == (0, 229, 153)  # Aurora Green #00E599


def test_void_bases_are_the_gradient_colours() -> None:
    """Each environment's backplate runs from a spec void at the top to a spec void at the bottom."""
    tol = 2
    expectations = {
        Environment.SPACE_COSMIC: (TWILIGHT_DEEP, COSMIC_INDIGO),
        Environment.MOLECULAR_LAB: (None, VOID_OBSIDIAN),
        Environment.CYBER_MATRIX: (None, COSMIC_INDIGO),
        Environment.FINANCIAL_LEDGER: (TWILIGHT_DEEP, VOID_OBSIDIAN),
    }
    for env, (top, bottom) in expectations.items():
        grad = _base_gradient(env)
        if top is not None:
            assert all(
                abs(a - b) <= tol for a, b in zip(grad.getpixel((0, 0)), top, strict=True)
            ), env
        assert all(
            abs(a - b) <= tol for a, b in zip(grad.getpixel((0, HEIGHT - 1)), bottom, strict=True)
        ), env


def test_all_spec_accents_are_painted() -> None:
    """Every spec accent appears as an exact pixel colour in at least one environment render."""
    seen: set[tuple[int, int, int]] = set()
    for env in Environment:
        img = Image.new("RGB", (WIDTH, HEIGHT))
        ENVIRONMENT_PAINTERS[env](img, 1.5, 42)
        seen.update(color for _count, color in img.getcolors(maxcolors=WIDTH * HEIGHT))
    for accent in (NEON_CYAN, NEON_YELLOW, NEON_PINK, AURORA_GREEN):
        assert accent in seen, accent


# ---------------------------------------------------------------------------
# 1. Sentence-driven environments
# ---------------------------------------------------------------------------
def test_five_thematic_environments() -> None:
    """Exactly the five spec environments."""
    assert len(Environment) == 5
    assert {e.value for e in Environment} == {
        "space_cosmic",
        "molecular_lab",
        "cyber_matrix",
        "financial_ledger",
        "parchment_map",
    }


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        (
            "Light from distant galaxies takes millions of years to reach us.",
            Environment.SPACE_COSMIC,
        ),
        ("Neutron stars can spin hundreds of times per second.", Environment.SPACE_COSMIC),
        (
            "Enzymes lower the energy barrier of each chemical reaction in the cell.",
            Environment.MOLECULAR_LAB,
        ),
        (
            "Your body replicates its DNA billions of times over a lifetime.",
            Environment.MOLECULAR_LAB,
        ),
        ("Central banks raise interest rates to fight inflation.", Environment.FINANCIAL_LEDGER),
        (
            "Compound interest turns small monthly savings into a large portfolio over decades.",
            Environment.FINANCIAL_LEDGER,
        ),
        ("Most people quit their budget within two months.", Environment.FINANCIAL_LEDGER),
        ("Sales revenue doubled while costs stayed flat.", Environment.FINANCIAL_LEDGER),
        (
            "Hackers exploit a flaw in the network firewall to steal login data.",
            Environment.CYBER_MATRIX,
        ),
        (
            "Neural networks train on massive datasets using gradient descent.",
            Environment.CYBER_MATRIX,
        ),
        (
            "The Roman Empire built roads that linked the continent for centuries.",
            Environment.PARCHMENT_MAP,
        ),
        (
            "Medieval explorers used a compass and a hand-drawn map to cross the ocean.",
            Environment.PARCHMENT_MAP,
        ),
    ],
)
def test_classify_realistic_sentences(sentence: str, expected: Environment) -> None:
    """Realistic narration sentences land in the environment a viewer would expect."""
    assert classify_sentence_environment(sentence) == expected


def test_fallback_rotation_never_repeats_back_to_back() -> None:
    """Keyword-free sentences rotate environments instead of freezing on one."""
    prev = None
    for i, sentence in enumerate(
        ["Hello there friend.", "Good morning to you.", "Nice weather today."]
    ):
        env = classify_sentence_environment(sentence, "", i, 0, prev)
        assert env != prev
        prev = env


def test_split_sentences_keeps_terminal_punctuation() -> None:
    """Sentences are split on terminal punctuation."""
    text = "The universe is vast! Can we comprehend it? We must try. Indeed."
    assert split_sentences(text) == [
        "The universe is vast!",
        "Can we comprehend it?",
        "We must try.",
        "Indeed.",
    ]


def test_scenes_change_environment_per_sentence() -> None:
    """Each sentence gets its own environment, and the rendered frame changes with it."""
    narration = (
        "Galaxies form stars in deep space. Central banks raise interest rates. "
        "Hackers exploit the network firewall."
    )
    beats = build_sentence_beats(narration, duration=9.0)
    assert [b.environment for b in beats] == [
        Environment.SPACE_COSMIC,
        Environment.FINANCIAL_LEDGER,
        Environment.CYBER_MATRIX,
    ]
    director = BackgroundDirector(enable_ken_burns=False)
    mids = [(b.start_time + b.end_time) / 2.0 for b in beats]
    frames = [director.render_frame(t, beats, apply_camera=False).tobytes() for t in mids]
    assert len(set(frames)) == 3


def test_build_sentence_beats_with_words() -> None:
    """Word timestamps drive sentence boundaries when supplied."""
    narration = "Stars form galaxies. Molecules assemble cells."
    words = [
        {"word": "Stars", "start": 0.0, "end": 0.4},
        {"word": "form", "start": 0.5, "end": 0.8},
        {"word": "galaxies.", "start": 0.9, "end": 1.5},
        {"word": "Molecules", "start": 1.8, "end": 2.2},
        {"word": "assemble", "start": 2.3, "end": 2.7},
        {"word": "cells.", "start": 2.8, "end": 3.4},
    ]
    beats = build_sentence_beats(narration, duration=3.5, words=words)
    assert len(beats) == 2
    assert beats[0].environment == Environment.SPACE_COSMIC
    assert beats[1].environment == Environment.MOLECULAR_LAB
    assert beats[0].start_time == pytest.approx(0.0)
    assert beats[1].end_time == pytest.approx(3.5)


def test_build_sentence_beats_proportional() -> None:
    """Without words, sentence durations follow character counts and cover the section."""
    narration = "First simple sentence. Second sentence with much more text to balance."
    beats = build_sentence_beats(narration, duration=4.0, words=None)
    assert len(beats) == 2
    assert beats[0].start_time == 0.0
    assert beats[0].end_time == beats[1].start_time
    assert beats[1].end_time == 4.0


@pytest.mark.parametrize("env", list(Environment))
def test_all_procedural_painters_render_full_hd(env: Environment) -> None:
    """Every thematic painter outputs a valid 1920x1080 RGB image with varied pixels."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    ENVIRONMENT_PAINTERS[env](img, 1.5, 42)
    assert img.size == (WIDTH, HEIGHT)
    assert img.mode == "RGB"
    assert len(img.tobytes()) == WIDTH * HEIGHT * 3
    assert len(set(img.tobytes()[:300])) > 1


def test_financial_ledger_painter_animates() -> None:
    """The ledger scrolls and the candles move, so consecutive times differ."""
    a = Image.new("RGB", (WIDTH, HEIGHT))
    b = Image.new("RGB", (WIDTH, HEIGHT))
    paint_financial_ledger(a, 0.0)
    paint_financial_ledger(b, 0.5)
    assert a.tobytes() != b.tobytes()


# ---------------------------------------------------------------------------
# 2. Transitions: cubic Hermite (smoothstep) crossfades, 400-600 ms
# ---------------------------------------------------------------------------
def test_cubic_hermite_is_the_spec_smoothstep() -> None:
    """The spec easing t^2 (3 - 2t), with zero slope at both ends and clamping."""
    for u in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
        assert cubic_hermite(u) == pytest.approx(u * u * (3.0 - 2.0 * u))
    assert cubic_hermite(-0.5) == 0.0
    assert cubic_hermite(1.5) == 1.0
    eps = 1e-4
    assert abs((cubic_hermite(eps) - cubic_hermite(0.0)) / eps) < 1e-3
    assert abs((cubic_hermite(1.0) - cubic_hermite(1.0 - eps)) / eps) < 1e-3
    # quintic is kept as an alternative helper but is not the crossfade easing
    assert quintic_hermite(0.5) == 0.5


def test_transition_duration_is_clamped_to_400_600ms() -> None:
    """Crossfade length is held inside the spec window."""
    assert BackgroundDirector().transition_duration == pytest.approx(0.5)
    assert BackgroundDirector(transition_duration=0.1).transition_duration == pytest.approx(0.4)
    assert BackgroundDirector(transition_duration=2.0).transition_duration == pytest.approx(0.6)
    assert BackgroundDirector(transition_duration=0.45).transition_duration == pytest.approx(0.45)


def test_crossfade_uses_cubic_hermite_easing() -> None:
    """Mid-crossfade frames are the blend at k = smoothstep(u), not a linear blend."""
    director = BackgroundDirector(
        transition_duration=0.5, enable_ken_burns=False, enable_transition_glow=False
    )
    beats = [
        SentenceBeat(0, "Deep space void.", 0.0, 2.0, Environment.SPACE_COSMIC),
        SentenceBeat(1, "Molecular chemistry.", 2.0, 4.0, Environment.MOLECULAR_LAB),
    ]
    # Crossfade window is [1.75, 2.25]; t = 1.875 gives u = 0.25.
    t = 1.875
    frame = director.render_frame(t, beats, apply_camera=False)
    a = director.render_environment(Environment.SPACE_COSMIC, t, seed=100)
    b = director.render_environment(Environment.MOLECULAR_LAB, t, seed=101)
    expected = Image.blend(a, b, cubic_hermite(0.25))
    assert frame.tobytes() == expected.tobytes()
    assert frame.tobytes() != Image.blend(a, b, 0.25).tobytes()


def test_director_render_frame_transitions() -> None:
    """Frames in each environment and at the crossfade midpoint are all distinct."""
    director = BackgroundDirector(transition_duration=0.5, enable_ken_burns=False)
    beats = [
        SentenceBeat(0, "Deep space void.", 0.0, 2.0, Environment.SPACE_COSMIC),
        SentenceBeat(1, "Molecular chemistry.", 2.0, 4.0, Environment.MOLECULAR_LAB),
    ]
    frame_space = director.render_frame(t=0.5, beats=beats, apply_camera=False)
    frame_lab = director.render_frame(t=3.5, beats=beats, apply_camera=False)
    frame_mid = director.render_frame(t=2.0, beats=beats, apply_camera=False)
    assert frame_space.size == (WIDTH, HEIGHT)
    assert frame_space.tobytes() != frame_mid.tobytes()
    assert frame_lab.tobytes() != frame_mid.tobytes()


# ---------------------------------------------------------------------------
# 4. Continuous Ken-Burns camera drift
# ---------------------------------------------------------------------------
def test_ken_burns_zoom_runs_1_02_to_1_06_per_scene() -> None:
    """Zoom starts at 1.02x, ends at 1.06x, and rises monotonically through the scene."""
    zoom0, _, _ = _ken_burns_params(0.0)
    zoom1, _, _ = _ken_burns_params(1.0)
    zoom_mid, _, _ = _ken_burns_params(0.5)
    assert zoom0 == pytest.approx(1.02)
    assert zoom1 == pytest.approx(1.06)
    assert zoom_mid == pytest.approx(1.04)
    zooms = [_ken_burns_params(i / 200.0)[0] for i in range(201)]
    assert all(b > a for a, b in zip(zooms, zooms[1:], strict=False))


def test_ken_burns_never_static_within_a_scene() -> None:
    """Every small step through a scene changes the rendered camera frame."""
    base = Image.new("RGB", (WIDTH, HEIGHT), (15, 20, 30))
    base.putpixel((100, 100), (255, 0, 0))
    frames = [
        apply_ken_burns(base, progress=p, drift_angle=0.7).tobytes()
        for p in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    assert len(set(frames)) == len(frames)


def test_ken_burns_drift_applies_zoom_and_pan() -> None:
    """Start and end frames differ in both the zoom and the pan of the feature."""
    base = Image.new("RGB", (WIDTH, HEIGHT), (15, 20, 30))
    base.putpixel((100, 100), (255, 0, 0))
    drifted_0 = apply_ken_burns(base, progress=0.0, zoom_min=1.02, zoom_max=1.06, drift_angle=0.0)
    drifted_1 = apply_ken_burns(base, progress=1.0, zoom_min=1.02, zoom_max=1.06, drift_angle=0.0)
    assert drifted_0.size == (WIDTH, HEIGHT)
    assert drifted_1.size == (WIDTH, HEIGHT)
    assert drifted_0.tobytes() != drifted_1.tobytes()


def test_camera_at_unit_zoom_is_identity() -> None:
    """A zoom of 1.0 with no pan returns the frame unchanged, so the transform is a pure camera move."""
    img = Image.new("RGB", (WIDTH, HEIGHT), (30, 60, 90))
    img.putpixel((500, 300), (200, 10, 10))
    assert apply_camera_transform(img, 1.0).tobytes() == img.tobytes()


def test_beat_camera_is_measured_per_sentence() -> None:
    """Two sentences each run their own 1.02 to 1.06 zoom, so the second restarts near 1.02."""
    director = BackgroundDirector(transition_duration=0.2, enable_ken_burns=True)
    beats = [
        SentenceBeat(0, "Galaxies.", 0.0, 2.0, Environment.SPACE_COSMIC),
        SentenceBeat(1, "Molecules.", 2.0, 4.0, Environment.MOLECULAR_LAB),
    ]
    # Late in beat 0 the camera is near 1.06; right after the boundary it is back near 1.02.
    late = background_director._beat_camera(beats[0], 1.99)[0]
    early_next = background_director._beat_camera(beats[1], 2.01)[0]
    assert late > 1.05
    assert early_next < 1.03
    # And the rendered frames still differ across the boundary.
    assert (
        director.render_frame(1.99, beats).tobytes() != director.render_frame(2.01, beats).tobytes()
    )


# ---------------------------------------------------------------------------
# 5. Wiring into drawer
# ---------------------------------------------------------------------------
def _section_audio(narration: str, duration: float):
    from autoshorts.longform.types import Section, SectionAudio

    return SectionAudio(
        section=Section(heading="Cosmic Chemistry", narration=narration),
        audio_path=Path("unused.wav"),
        words=[],
        duration=duration,
    )


def test_drawer_integration_render_scene_frame() -> None:
    """render_scene_frame accepts a director and returns a full HD RGB frame."""
    from autoshorts.longform import drawer

    sa = _section_audio(
        "Stars forge elements in deep cosmic voids. Chemical bonds organize the living cell.", 3.0
    )
    director = BackgroundDirector(transition_duration=0.5, enable_ken_burns=True)
    frame = drawer.render_scene_frame(
        sa, title="Demo", index=0, t=1.0, background_director=director
    )
    assert isinstance(frame, Image.Image)
    assert frame.size == (WIDTH, HEIGHT)
    assert frame.mode == "RGB"


def test_drawer_background_path_uses_director_and_differs_from_static() -> None:
    """With the director on, drawer calls it and the frame differs from the static navy background."""
    from autoshorts.longform import drawer

    class SpyDirector(BackgroundDirector):
        def __init__(self) -> None:
            super().__init__(transition_duration=0.5, enable_ken_burns=True)
            self.calls = 0

        def render_frame(self, t, beats, apply_camera=True):  # type: ignore[override]
            self.calls += 1
            return super().render_frame(t, beats, apply_camera)

    sa = _section_audio(
        "Galaxies expand across deep space. Hackers exploit the network firewall.", 3.0
    )
    spy = SpyDirector()
    dynamic = drawer.render_scene_frame(sa, title="Demo", index=0, t=1.0, background_director=spy)
    static = drawer.render_scene_frame(sa, title="Demo", index=0, t=1.0)
    assert spy.calls == 1
    assert dynamic.size == static.size == (WIDTH, HEIGHT)
    assert dynamic.tobytes() != static.tobytes()


def test_director_off_keeps_static_background_deterministic() -> None:
    """With no director, the default background path is unchanged and deterministic."""
    from autoshorts.longform import drawer

    sa = _section_audio("Galaxies expand across deep space.", 3.0)
    first = drawer.render_scene_frame(sa, title="Demo", index=0, t=1.0)
    second = drawer.render_scene_frame(sa, title="Demo", index=0, t=1.0)
    assert first.tobytes() == second.tobytes()


def test_drawer_integration_render_scene_frames() -> None:
    """render_scene_frames yields one RGB24 buffer per frame when the director is passed."""
    from autoshorts.longform import drawer

    sections = [
        _section_audio("Galaxies expand across deep space.", 0.2),  # 6 frames at 30fps
    ]
    director = BackgroundDirector(transition_duration=0.4, enable_ken_burns=True)
    frames = list(drawer.render_scene_frames(sections, title="Test", background_director=director))
    assert len(frames) == 6
    assert len(frames[0]) == WIDTH * HEIGHT * 3


def test_drawer_dynamic_scene_keyword() -> None:
    """scenes=('dynamic',) activates the director automatically."""
    from autoshorts.longform import drawer

    sections = [_section_audio("Cyber algorithms map the matrix network.", 0.1)]  # 3 frames
    frames = list(drawer.render_scene_frames(sections, title="Auto Dynamic", scenes=("dynamic",)))
    assert len(frames) == 3
    assert len(frames[0]) == WIDTH * HEIGHT * 3


def test_scene_painters_include_all_thematic_environments() -> None:
    """Every environment, plus the 'dynamic' scene, is registered in drawer.SCENE_PAINTERS."""
    from autoshorts.longform.drawer import SCENE_PAINTERS

    for env_name in (
        "space_cosmic",
        "molecular_lab",
        "cyber_matrix",
        "financial_ledger",
        "parchment_map",
        "dynamic",
    ):
        assert env_name in SCENE_PAINTERS
        img = Image.new("RGB", (WIDTH, HEIGHT))
        SCENE_PAINTERS[env_name](img)
        assert img.size == (WIDTH, HEIGHT)


# ---------------------------------------------------------------------------
# 6. Performance guard (loose; catches pathological regressions only)
# ---------------------------------------------------------------------------
def test_render_frame_is_not_pathological() -> None:
    """A 1920x1080 in-beat frame and a crossfade frame each render well inside a generous budget."""
    director = BackgroundDirector(transition_duration=0.5)
    beats = build_sentence_beats(
        "Galaxies form stars in deep space. Central banks raise interest rates. Hackers exploit the network firewall.",
        9.0,
    )

    def median_ms(t: float) -> float:
        director.render_frame(t, beats)  # warm caches
        samples = []
        for _ in range(3):
            start = time.perf_counter()
            director.render_frame(t, beats)
            samples.append((time.perf_counter() - start) * 1000.0)
        return sorted(samples)[1]

    in_beat = median_ms(1.0)
    crossfade = median_ms(beats[0].end_time)
    assert in_beat < 250.0, in_beat
    assert crossfade < 250.0, crossfade


def test_singleton_director() -> None:
    """The singleton accessor returns the same instance."""
    assert get_background_director() is get_background_director()
