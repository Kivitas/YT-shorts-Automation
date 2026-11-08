"""Tests for the hand-downloaded background-music library. ffprobe is always mocked."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from autoshorts.longform import music_library as ml
from autoshorts.longform.music_library import (
    Track,
    loop_to_duration,
    pick_track,
    scan_library,
    write_attribution,
)


@pytest.fixture(autouse=True)
def fake_ffprobe(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """Durations keyed by file name; any unknown file reads as 120 s."""
    durations: dict[str, float] = {}

    def _probe(path: Path) -> float:
        return durations.get(path.name, 120.0)

    monkeypatch.setattr(ml, "_probe_duration", _probe)
    return durations


def _touch(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(b"not really audio")
    return path


def _sidecar(folder: Path, stem: str, **fields: object) -> None:
    (folder / f"{stem}.json").write_text(json.dumps(fields), encoding="utf-8")


def test_scan_with_json_sidecar_reads_metadata(
    tmp_path: Path, fake_ffprobe: dict[str, float]
) -> None:
    _touch(tmp_path, "calm_loop.mp3")
    _sidecar(
        tmp_path,
        "calm_loop",
        title="Calm Loop",
        artist="Some Artist",
        licence="YouTube Audio Library",
        mood="Calm",
        bpm=92,
    )
    fake_ffprobe["calm_loop.mp3"] = 75.5

    tracks = scan_library(tmp_path)

    assert len(tracks) == 1
    track = tracks[0]
    assert track.title == "Calm Loop"
    assert track.artist == "Some Artist"
    assert track.licence == "YouTube Audio Library"
    assert track.mood == "calm"
    assert track.bpm == 92.0
    assert track.duration == 75.5
    assert track.path == (tmp_path / "calm_loop.mp3").resolve()


def test_scan_with_csv_sidecar(tmp_path: Path) -> None:
    _touch(tmp_path, "Driving.wav")
    _touch(tmp_path, "other.ogg")
    with (tmp_path / "library.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["filename", "title", "artist", "licence", "mood", "bpm"]
        )
        writer.writeheader()
        writer.writerow(
            {
                "filename": "driving.wav",
                "title": "Driving",
                "artist": "A",
                "licence": "CC-BY",
                "mood": "",
                "bpm": "",
            }
        )
        writer.writerow(
            {
                "filename": "other.ogg",
                "title": "Other",
                "artist": "B",
                "licence": "Free",
                "mood": "Upbeat",
                "bpm": "128",
            }
        )

    tracks = {t.filename: t for t in scan_library(tmp_path)}

    assert tracks["Driving.wav"].title == "Driving"
    assert tracks["Driving.wav"].mood is None
    assert tracks["other.ogg"].mood == "upbeat"
    assert tracks["other.ogg"].bpm == 128.0


def test_json_sidecar_wins_over_csv_row(tmp_path: Path) -> None:
    _touch(tmp_path, "a.mp3")
    _sidecar(tmp_path, "a", title="From JSON", licence="Licence J")
    (tmp_path / "library.csv").write_text(
        "filename,title,licence\na.mp3,From CSV,Licence C\n", encoding="utf-8"
    )

    (track,) = scan_library(tmp_path)

    assert track.title == "From JSON"
    assert track.licence == "Licence J"


def test_missing_sidecar_is_excluded_by_default(tmp_path: Path) -> None:
    _touch(tmp_path, "no_sidecar.mp3")
    _touch(tmp_path, "licensed.mp3")
    _sidecar(tmp_path, "licensed", title="Licensed", licence="YouTube Audio Library")

    tracks = scan_library(tmp_path)

    assert [t.filename for t in tracks] == ["licensed.mp3"]


def test_unknown_licence_override_includes_track(tmp_path: Path) -> None:
    _touch(tmp_path, "no_sidecar.mp3")

    (track,) = scan_library(tmp_path, allow_unknown_licence=True)

    assert track.licence == "unknown"
    assert track.title == "no_sidecar"
    assert track.artist == ""


def test_empty_or_unknown_licence_in_sidecar_is_excluded(tmp_path: Path) -> None:
    _touch(tmp_path, "blank.mp3")
    _touch(tmp_path, "explicit.mp3")
    _sidecar(tmp_path, "blank", title="Blank", licence="   ")
    _sidecar(tmp_path, "explicit", title="Explicit", licence="Unknown")

    assert scan_library(tmp_path) == []
    assert len(scan_library(tmp_path, allow_unknown_licence=True)) == 2


def test_hidden_and_unsupported_files_are_ignored(tmp_path: Path) -> None:
    _touch(tmp_path, ".hidden.mp3")
    _touch(tmp_path, "notes.txt")
    _touch(tmp_path, "clip.mp4")
    _touch(tmp_path, "visible.m4a")

    tracks = scan_library(tmp_path, allow_unknown_licence=True)

    assert [t.filename for t in tracks] == ["visible.m4a"]


def test_missing_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_library(tmp_path / "does_not_exist")


def test_file_instead_of_folder_raises(tmp_path: Path) -> None:
    path = _touch(tmp_path, "song.mp3")
    with pytest.raises(NotADirectoryError):
        scan_library(path)


def test_symlink_outside_folder_is_skipped(tmp_path: Path) -> None:
    library = tmp_path / "library"
    outside = tmp_path / "outside"
    library.mkdir()
    outside.mkdir()
    secret = _touch(outside, "outside.mp3")
    _touch(library, "inside.mp3")
    try:
        os.symlink(secret, library / "linked.mp3")
    except OSError:
        pytest.skip("creating symlinks needs extra privileges on this platform")

    tracks = scan_library(library, allow_unknown_licence=True)

    assert [t.filename for t in tracks] == ["inside.mp3"]


def _track(name: str, duration: float, mood: str | None = None) -> Track:
    return Track(
        path=Path(f"/music/{name}"),
        title=name,
        artist="",
        licence="unknown",
        mood=mood,
        bpm=None,
        duration=duration,
    )


def test_pick_track_is_deterministic_and_order_independent() -> None:
    tracks = [_track(f"t{i}.mp3", 60.0) for i in range(6)]

    first = pick_track(tracks, seed=3)
    again = pick_track(list(reversed(tracks)), seed=3)

    assert first is not None
    assert first == again
    assert pick_track([], seed=3) is None


def test_pick_track_prefers_matching_mood() -> None:
    tracks = [
        _track("a.mp3", 60.0, mood="dark"),
        _track("b.mp3", 60.0, mood="calm"),
        _track("c.mp3", 60.0),
    ]

    for seed in range(10):
        picked = pick_track(tracks, mood="Calm", seed=seed)
        assert picked is not None
        assert picked.filename == "b.mp3"


def test_pick_track_falls_back_when_no_mood_matches() -> None:
    tracks = [_track("a.mp3", 60.0, mood="dark")]

    picked = pick_track(tracks, mood="happy", seed=0)

    assert picked is not None
    assert picked.filename == "a.mp3"


def test_pick_track_respects_min_duration_and_unknown_duration() -> None:
    tracks = [_track("short.mp3", 10.0), _track("unknown.mp3", 0.0), _track("long.mp3", 90.0)]

    assert pick_track(tracks, min_duration=30.0).filename == "long.mp3"  # type: ignore[union-attr]
    assert pick_track(tracks, min_duration=100.0) is None
    assert pick_track([_track("unknown.mp3", 0.0)]) is None


def test_write_attribution_lists_every_track(tmp_path: Path) -> None:
    used = [
        Track(Path("/m/a.mp3"), "Alpha", "Artist A", "YouTube Audio Library", None, None, 30.0),
        Track(Path("/m/b.wav"), "Beta", "", "CC-BY 4.0", "calm", None, 40.0),
    ]
    target = tmp_path / "credits" / "credits.txt"

    written = write_attribution(used, target)

    assert written == target
    text = target.read_text(encoding="utf-8")
    assert "Alpha by Artist A" in text
    assert "Licence: YouTube Audio Library" in text
    assert "Beta by unknown artist" in text
    assert "Licence: CC-BY 4.0" in text
    assert "File: a.mp3" in text
    assert "File: b.wav" in text


def test_write_attribution_with_no_tracks(tmp_path: Path) -> None:
    target = write_attribution([], tmp_path / "credits.txt")

    assert "No background music was used." in target.read_text(encoding="utf-8")


def test_loop_to_duration_argv_shape(tmp_path: Path) -> None:
    track = tmp_path / "loop.mp3"
    out = tmp_path / "bgm.m4a"

    argv = loop_to_duration(track, 30, out, ffmpeg_bin="ffmpeg")

    assert all(isinstance(part, str) for part in argv)
    assert argv[0] == "ffmpeg"
    assert argv[argv.index("-stream_loop") + 1] == "-1"
    assert argv[argv.index("-i") + 1] == str(track)
    assert argv[argv.index("-t") + 1] == "30.000"
    assert argv[-1] == str(out)

    audio_filter = argv[argv.index("-af") + 1]
    assert "afade=t=out:st=28.500:d=1.500" in audio_filter
    assert "loudnorm=I=-14" in audio_filter
    assert audio_filter.index("afade") < audio_filter.index("loudnorm")


def test_loop_to_duration_rejects_bad_duration(tmp_path: Path) -> None:
    for bad in (0, -5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            loop_to_duration(tmp_path / "a.mp3", bad, tmp_path / "out.m4a", ffmpeg_bin="ffmpeg")


def test_loop_to_duration_short_clip_fade_starts_at_zero(tmp_path: Path) -> None:
    argv = loop_to_duration(tmp_path / "a.mp3", 1.0, tmp_path / "out.mp3", ffmpeg_bin="ffmpeg")

    assert "afade=t=out:st=0.000:d=1.500" in argv[argv.index("-af") + 1]
    assert "libmp3lame" in argv
