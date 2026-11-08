"""Background-music library built from tracks the user downloads by hand.

The user downloads MP3s from YouTube Studio > Audio Library and drops them into
``assets/music/library/``. This module only scans that folder, picks a track and builds the
ffmpeg command that fits it to a video. It never downloads or scrapes anything.

Optional sidecar metadata, placed next to the audio files:
  * ``<stem>.json``: one object with any of the keys title, artist, licence, mood, bpm.
  * ``library.csv``: a header row with a ``filename`` column plus the same keys. The row whose
    filename equals the audio file name supplies the fields.
A JSON sidecar wins over a CSV row for the same track. A track without licence information
gets licence ``'unknown'`` and is excluded unless ``allow_unknown_licence=True``.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import random
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".wav", ".m4a", ".ogg"})
SIDECAR_CSV_NAME = "library.csv"
LICENCE_UNKNOWN = "unknown"
LOUDNESS_TARGET_LUFS = -14.0
FADE_OUT_SECONDS = 1.5
_PROBE_TIMEOUT_S = 20.0

# Per-output-format audio encoder options. Unknown suffixes fall back to AAC.
_CODEC_ARGS_BY_SUFFIX: dict[str, list[str]] = {
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    ".wav": ["-c:a", "pcm_s16le"],
}


@dataclass(frozen=True)
class Track:
    """One usable background-music file plus its sidecar metadata."""

    path: Path
    title: str
    artist: str
    licence: str
    mood: str | None
    bpm: float | None
    duration: float

    @property
    def filename(self) -> str:
        return self.path.name


def scan_library(folder: Path | str, *, allow_unknown_licence: bool = False) -> list[Track]:
    """List the usable tracks in ``folder`` (top level only, sorted by file name).

    Raises FileNotFoundError when the folder is missing and NotADirectoryError when it is a
    file. Hidden files, unsupported extensions and symlinks that resolve outside the folder
    are skipped. Tracks whose licence is unknown are skipped unless
    ``allow_unknown_licence`` is True. A track whose duration cannot be read gets duration 0.0.
    """
    library = Path(folder)
    if not library.exists():
        raise FileNotFoundError(f"Music library folder does not exist: {library}")
    if not library.is_dir():
        raise NotADirectoryError(f"Music library path is not a folder: {library}")

    root = library.resolve()
    csv_rows = _read_csv_sidecar(library, root)
    tracks: list[Track] = []
    for entry in sorted(library.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith(".") or entry.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        audio = _inside_library(entry, root)
        if audio is None:
            continue

        raw = _read_json_sidecar(library, root, entry.stem)
        if raw is None:
            raw = csv_rows.get(entry.name.lower(), {})
        fields = _fields_from(raw, entry.stem)
        if fields["licence"] == LICENCE_UNKNOWN and not allow_unknown_licence:
            logger.info(
                "Excluding %s: no licence recorded (set allow_unknown_licence to use it)",
                entry.name,
            )
            continue

        tracks.append(
            Track(
                path=audio,
                title=fields["title"],
                artist=fields["artist"],
                licence=fields["licence"],
                mood=fields["mood"],
                bpm=fields["bpm"],
                duration=_probe_duration(audio),
            )
        )
    return tracks


def pick_track(
    tracks: Iterable[Track],
    mood: str | None = None,
    min_duration: float = 0.0,
    seed: int = 0,
) -> Track | None:
    """Choose one track deterministically, or return None when nothing qualifies.

    Tracks with an unknown duration (0.0) or shorter than ``min_duration`` are ignored. When
    ``mood`` is given and at least one track matches it, only matching tracks are considered.
    The result depends only on the set of tracks, ``mood`` and ``seed``, not on input order.
    """
    usable = sorted(
        (t for t in tracks if t.duration > 0 and t.duration >= min_duration),
        key=lambda t: (t.filename.lower(), str(t.path)),
    )
    if not usable:
        return None
    if mood:
        wanted = mood.strip().lower()
        preferred = [t for t in usable if t.mood == wanted]
        if preferred:
            usable = preferred
    rng = random.Random(seed)
    return usable[rng.randrange(len(usable))]


def write_attribution(tracks_used: Iterable[Track], path: Path | str) -> Path:
    """Write a plain-text credits file listing title, artist and licence for each track."""
    target = Path(path)
    tracks = list(tracks_used)
    lines = ["Background music credits", ""]
    if not tracks:
        lines.append("No background music was used.")
    for track in tracks:
        lines.append(f"{track.title} by {track.artist or 'unknown artist'}")
        lines.append(f"  Licence: {track.licence}")
        lines.append(f"  File: {track.filename}")
        lines.append("")
    lines.append("Recorded for transparency. YouTube Audio Library tracks do not require credit.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def loop_to_duration(
    track_path: Path | str,
    duration: float,
    out_path: Path | str,
    *,
    ffmpeg_bin: str | None = None,
) -> list[str]:
    """Return the ffmpeg argv that loops ``track_path`` to ``duration`` seconds.

    The output gets a 1.5 s fade-out over its last seconds and loudness normalisation to
    -14 LUFS via loudnorm. The command is a list for shell-free execution; it is not run here.
    """
    seconds = float(duration)
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"duration must be a positive finite number of seconds, got {duration!r}")

    out = Path(out_path)
    fade_start = max(0.0, seconds - FADE_OUT_SECONDS)
    audio_filter = ",".join(
        [
            f"afade=t=out:st={fade_start:.3f}:d={FADE_OUT_SECONDS:.3f}",
            f"loudnorm=I={LOUDNESS_TARGET_LUFS:g}:TP=-1.5:LRA=11",
        ]
    )
    codec_args = _CODEC_ARGS_BY_SUFFIX.get(out.suffix.lower(), _CODEC_ARGS_BY_SUFFIX[".m4a"])
    return [
        ffmpeg_bin or _resolve_ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(track_path),
        "-t",
        f"{seconds:.3f}",
        "-vn",
        "-af",
        audio_filter,
        *codec_args,
        str(out),
    ]


# --- helpers -----------------------------------------------------------------


def _inside_library(path: Path, root: Path) -> Path | None:
    """Return the resolved file path if it is a regular file inside ``root``, else None."""
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        logger.warning("Skipping %s: it resolves outside the music library", path.name)
        return None
    return resolved if resolved.is_file() else None


def _read_json_sidecar(folder: Path, root: Path, stem: str) -> dict[str, Any] | None:
    sidecar = _inside_library(folder / f"{stem}.json", root)
    if sidecar is None:
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        logger.warning("Ignoring unreadable sidecar %s: %s", sidecar.name, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("Ignoring sidecar %s: expected a JSON object", sidecar.name)
        return None
    return _lower_keys(data)


def _read_csv_sidecar(folder: Path, root: Path) -> dict[str, dict[str, Any]]:
    """Map lower-cased audio file name to its library.csv row (empty when absent)."""
    csv_path = _inside_library(folder / SIDECAR_CSV_NAME, root)
    if csv_path is None:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    try:
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                lowered = _lower_keys(row)
                name = _clean_text(lowered.get("filename"))
                if name:
                    rows[name.lower()] = lowered
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        logger.warning("Ignoring unreadable %s: %s", SIDECAR_CSV_NAME, exc)
        return {}
    return rows


def _lower_keys(data: Mapping[Any, Any]) -> dict[str, Any]:
    return {str(key).strip().lower(): value for key, value in data.items() if key is not None}


def _clean_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _parse_bpm(value: object) -> float | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        bpm = float(text)
    except ValueError:
        return None
    return bpm if math.isfinite(bpm) and bpm > 0 else None


def _fields_from(raw: Mapping[str, Any], stem: str) -> dict[str, Any]:
    licence = _clean_text(raw.get("licence"))
    if not licence or licence.lower() == LICENCE_UNKNOWN:
        licence = LICENCE_UNKNOWN
    return {
        "title": _clean_text(raw.get("title")) or stem,
        "artist": _clean_text(raw.get("artist")),
        "licence": licence,
        "mood": _clean_text(raw.get("mood")).lower() or None,
        "bpm": _parse_bpm(raw.get("bpm")),
    }


def _probe_duration(path: Path) -> float:
    """Duration in seconds, or 0.0 when it cannot be read."""
    try:
        from autoshorts.media.compositor import probe_media_duration
    except ImportError:
        return _ffprobe_duration(path)
    try:
        return float(probe_media_duration(path, default=0.0))
    except (OSError, TypeError, ValueError):
        return 0.0


def _ffprobe_duration(path: Path) -> float:
    try:
        from autoshorts.media.encoder_probe import get_ffprobe_path

        ffprobe = get_ffprobe_path()
    except ImportError:
        ffprobe = "ffprobe"
    if not ffprobe:
        return 0.0
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if proc.returncode != 0:
        return 0.0
    try:
        value = float(proc.stdout.strip())
    except ValueError:
        return 0.0
    return value if math.isfinite(value) and value > 0 else 0.0


def _resolve_ffmpeg() -> str:
    try:
        from autoshorts.media.encoder_probe import get_ffmpeg_path

        return get_ffmpeg_path()
    except ImportError:
        return "ffmpeg"
