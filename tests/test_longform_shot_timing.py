"""Shot cuts must not restart a section's beat: the beat runs on section time."""

from __future__ import annotations

from pathlib import Path

from autoshorts.longform import drawer
from autoshorts.longform.beats import plan_beat
from autoshorts.longform.drawer import Shot
from autoshorts.longform.types import Section, SectionAudio


def test_every_shot_of_a_section_draws_the_beat_on_section_time(monkeypatch) -> None:
    calls: list[tuple[float, float]] = []

    def record(img, plan, t, start, end, narration, words=None, *, text=True):
        calls.append((start, end))

    def record_text(img, plan, t, start, end, narration):
        calls.append((start, end))

    monkeypatch.setattr(drawer, "draw_beat", record)
    monkeypatch.setattr(drawer, "draw_beat_text", record_text)
    sa = SectionAudio(
        section=Section(heading="Focus", narration="Look at the desk. Start small today."),
        audio_path=Path("unused.wav"),
        words=[],
        duration=6.0,
    )
    plan = plan_beat(0, sa.section.heading, sa.section.narration, "narrator", False)

    for shot in (Shot(0.0, 2.0, "wide"), Shot(2.0, 4.0, "close"), Shot(4.0, 6.0, "insert")):
        drawer._draw_scene(sa, "Demo", 0, plan, shot, 3.0, 1.0, sa.duration)

    # Each shot draws the scene and the text layer, both on section time.
    assert calls == [(0.0, 6.0)] * 6
