"""Editing pipeline: longform two-pass loudness, bgm_ducks wiring and shorts pacing events."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autoshorts.core.models import JumpCut, ZoomPunchIn
from autoshorts.edit_planner.pacing import build_pacing_events
from autoshorts.longform import render
from autoshorts.longform.types import Section, SectionAudio


def _section_audio(path: Path, duration: float = 1.0) -> SectionAudio:
    path.write_bytes(b"RIFF")
    return SectionAudio(
        section=Section(heading="Part", narration="w0 w1"),
        audio_path=path,
        words=[{"word": "w0", "start": 0.0, "end": 0.2}],
        duration=duration,
    )


# ---- Longform two-pass loudness -------------------------------------------------------------


def test_loudness_normalise_calls_two_pass_with_target(monkeypatch, tmp_path):
    calls: list[tuple[Path, Path, float]] = []

    def fake_two_pass(src, dst, target_lufs):
        calls.append((Path(src), Path(dst), target_lufs))
        Path(dst).write_bytes(b"normalised")
        return Path(dst)

    monkeypatch.setattr(render, "normalize_two_pass", fake_two_pass)
    src = tmp_path / "narration.m4a"
    src.write_bytes(b"raw")

    out = render._loudness_normalise(src, tmp_path)

    assert calls == [(src, tmp_path / "narration_loudnorm.m4a", -14.0)]
    assert render.LOUDNESS_TARGET_LUFS == -14.0
    assert out == tmp_path / "narration_loudnorm.m4a"
    assert out.read_bytes() == b"normalised"


def test_loudness_normalise_failure_keeps_unnormalised_audio(monkeypatch, tmp_path, caplog):
    def boom(src, dst, target_lufs):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(render, "normalize_two_pass", boom)
    src = tmp_path / "narration.m4a"
    src.write_bytes(b"raw")

    with caplog.at_level("WARNING"):
        out = render._loudness_normalise(src, tmp_path)

    assert out == src
    assert any("keeping unnormalised" in rec.message for rec in caplog.records)


# ---- bgm_ducks ------------------------------------------------------------------------------


def test_duck_expression_for_two_spans():
    expr = render._duck_enable_expression([(1.0, 2.5), (4.0, 5.0)])
    assert expr == "between(t,1.00,2.50)+between(t,4.00,5.00)"


def test_load_bgm_ducks_reads_workflow_optimizer_naming(tmp_path):
    edl = tmp_path / "edl.json"
    edl.write_text(
        json.dumps(
            {
                "bgm_ducks": [
                    {"start_sec": 4.0, "end_sec": 5.0, "duck_volume_db": -12.0},
                    {"start_sec": 1.0, "end_sec": 2.5, "reason": "rapid_speech_burst"},
                    {"start_sec": 3.0, "end_sec": 3.0},  # empty span is dropped
                ]
            }
        ),
        encoding="utf-8",
    )
    assert render.load_bgm_ducks(edl) == [(1.0, 2.5), (4.0, 5.0)]


def test_load_bgm_ducks_missing_or_bad_file_returns_empty(tmp_path):
    assert render.load_bgm_ducks(None) == []
    assert render.load_bgm_ducks(tmp_path / "absent.json") == []
    bad = tmp_path / "edl.json"
    bad.write_text("{not json", encoding="utf-8")
    assert render.load_bgm_ducks(bad) == []


def test_concat_audio_applies_duck_volume_in_bed_mix(monkeypatch, tmp_path):
    music = tmp_path / "bed.mp3"
    music.write_bytes(b"x" * 2048)
    sections = [_section_audio(tmp_path / "s0.wav", 2.0)]
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"audio")  # every ffmpeg call writes its output (last arg)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(render, "get_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(render.subprocess, "run", fake_run)

    render._concat_audio(sections, tmp_path, music_bed=music, bgm_ducks=[(0.5, 1.0), (1.5, 1.8)])

    mix_graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert (
        "volume=0.14,volume=0.3:enable='between(t,0.50,1.00)+between(t,1.50,1.80)'[bg]" in mix_graph
    )


def test_concat_audio_without_ducks_keeps_plain_bed(monkeypatch, tmp_path):
    music = tmp_path / "bed.mp3"
    music.write_bytes(b"x" * 2048)
    sections = [_section_audio(tmp_path / "s0.wav", 2.0)]
    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        seen.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"audio")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(render, "get_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(render.subprocess, "run", fake_run)

    render._concat_audio(sections, tmp_path, music_bed=music)

    mix_graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "volume=0.14[bg]" in mix_graph
    assert "enable=" not in mix_graph


# ---- Shorts pacing --------------------------------------------------------------------------


def test_pacing_merges_cuts_and_punches_sorted_by_time():
    cuts = [JumpCut(cut_start=5.0, cut_end=5.5), JumpCut(cut_start=1.0, cut_end=1.4)]
    punches = [
        ZoomPunchIn(timestamp=3.0, scale=1.15, duration=0.8),
        ZoomPunchIn(timestamp=0.5, scale=1.10, duration=0.5),
    ]

    events = build_pacing_events(cuts, punches, clip_duration=20.0)

    assert [(e["kind"], e["start"]) for e in events] == [
        ("zoom_punch", 0.5),
        ("jump_cut", 1.0),
        ("zoom_punch", 3.0),
        ("jump_cut", 5.0),
    ]
    assert set(events[0]) == {"kind", "start", "end", "strength"}
    assert events[0]["end"] == pytest.approx(1.0)
    assert events[0]["strength"] == pytest.approx(1.10)
    assert events[1]["strength"] == 1.0


def test_pacing_clamps_to_clip_bounds_and_drops_empty_spans():
    cuts = [
        {"cut_start": -1.0, "cut_end": 0.5},  # starts before the clip (dict: model forbids it)
        JumpCut(cut_start=9.5, cut_end=12.0),  # runs past the end
        JumpCut(cut_start=10.5, cut_end=11.0),  # entirely after the end: dropped
    ]
    punches = [ZoomPunchIn(timestamp=9.8, scale=1.2, duration=1.0)]

    events = build_pacing_events(cuts, punches, clip_duration=10.0)

    assert events == [
        {"kind": "jump_cut", "start": 0.0, "end": 0.5, "strength": 1.0},
        {"kind": "jump_cut", "start": 9.5, "end": 10.0, "strength": 1.0},
        {"kind": "zoom_punch", "start": 9.8, "end": 10.0, "strength": 1.2},
    ]


def test_pacing_handles_empty_and_invalid_input():
    assert build_pacing_events(None, None) == []
    assert build_pacing_events([], [], clip_duration=10.0) == []
    inverted = [JumpCut(cut_start=4.0, cut_end=2.0)]
    assert build_pacing_events(inverted, [], clip_duration=10.0) == []
    assert build_pacing_events([JumpCut(cut_start=1.0, cut_end=2.0)], [], 0.0) == []
