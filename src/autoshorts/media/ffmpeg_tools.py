"""Bridge to the vendored ffmpeg-skill CLI scripts (MIT, see third_party/ffmpeg_skill/THIRD_PARTY.md).

Binary discovery: the scripts resolve ffmpeg/ffprobe with ``shutil.which`` on PATH only
(scripts/_common/runner.py ``ffmpeg_exe``) and expose no flag or environment variable for a
binary path. So ``run_tool`` prepends the directories of the binaries this app already
resolves (``encoder_probe.get_ffmpeg_path`` and ``compositor.get_ffprobe_path``) to PATH in
the child environment only. The parent process environment is never modified.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from autoshorts.core.paths import PROJECT_ROOT
from autoshorts.media.compositor import get_ffprobe_path
from autoshorts.media.encoder_probe import get_ffmpeg_path

logger = logging.getLogger(__name__)

TOOLS_DIR = PROJECT_ROOT / "third_party" / "ffmpeg_skill" / "scripts"
ALLOWED_TOOLS = frozenset(
    {
        "check",
        "loudness",
        "silence",
        "caption",
        "probe",
        "metadata",
        "report",
        "broll",
        "speedramp",
        "cropdetect",
        "scenes",
        "look",
        "graphics",
        "stabilize",
        "cut",
    }
)

PLATFORM_SAFE_ZONES: dict[str, dict[str, float]] = {
    "shorts": {"top": 0.06, "bottom": 0.18, "left": 0.05, "right": 0.12},
    "tiktok": {"top": 0.10, "bottom": 0.22, "left": 0.05, "right": 0.14},
    "reels": {"top": 0.08, "bottom": 0.20, "left": 0.05, "right": 0.12},
    "youtube": {"top": 0.05, "bottom": 0.05, "left": 0.05, "right": 0.05},
}


class FfmpegToolError(RuntimeError):
    """Raised when an ffmpeg-skill script cannot be run (bad name, missing file, timeout)."""


def _child_env() -> dict[str, str]:
    """Environment for a child script: our resolved ffmpeg/ffprobe folders first on PATH."""
    dirs: list[str] = []
    for exe in (get_ffmpeg_path(), get_ffprobe_path()):
        if exe and Path(exe).is_absolute():
            folder = str(Path(exe).parent)
            if folder not in dirs:
                dirs.append(folder)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([*dirs, env.get("PATH", "")])
    return env


def run_tool(
    script_name: str, args: list[str], timeout_sec: float = 600.0
) -> subprocess.CompletedProcess:
    """Run one allow-listed ffmpeg-skill script with ``args`` and return the completed process."""
    if script_name not in ALLOWED_TOOLS:
        raise FfmpegToolError(f"ffmpeg tool {script_name!r} is not in the allow-list")
    script = TOOLS_DIR / f"{script_name}.py"
    if not script.is_file():
        raise FfmpegToolError(f"ffmpeg tool script is missing: {script}")
    try:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=TOOLS_DIR.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
            env=_child_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise FfmpegToolError(
            f"ffmpeg tool {script_name} timed out after {timeout_sec:g}s"
        ) from exc


def _parse_json_doc(text: str) -> dict[str, Any] | None:
    try:
        doc = json.loads(text)
    except (TypeError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _failure(reason: str) -> dict[str, Any]:
    return {"ok": False, "issues": [reason], "raw": {}}


def check_delivery(
    video_path: Path | str, platform: str = "shorts"
) -> dict[str, Any]:
    """Run check.py on ``video_path`` against ``platform`` spec (default: shorts); never raises.

    Returns ``{"ok": bool, "issues": [str], "raw": dict}``. ``issues`` lists every FAIL and WARN
    row from check.py, or the failure reason when no JSON result could be read.
    """
    try:
        # The script runs from another folder, so the path must be absolute.
        args = [str(Path(video_path).resolve()), "--json"]
        if platform:
            args.extend(["--platform", platform])
        result = run_tool("check", args)
        doc = _parse_json_doc(result.stdout) or _parse_json_doc(result.stderr)
        if doc is None:
            return _failure(f"check.py exited {result.returncode} without a JSON result")
        rows = doc.get("checks") if isinstance(doc.get("checks"), list) else []
        issues = [
            f"{row.get('check')}: {row.get('status')} {row.get('value')} (expected {row.get('expected')})"
            for row in rows
            if isinstance(row, dict) and row.get("status") in ("FAIL", "WARN")
        ]
        ok = bool(doc.get("ok")) and result.returncode == 0
        if not ok and not issues:
            err = doc.get("error")
            if isinstance(err, dict):
                err = err.get("message")
            reason = err or doc.get("message") or f"check.py exited {result.returncode}"
            issues = [str(reason)]
        return {"ok": ok, "issues": issues, "raw": doc}
    except Exception as exc:  # informational check: never let it break the pipeline
        logger.warning("ffmpeg delivery check failed for %s: %s", video_path, exc)
        return _failure(f"delivery check could not run: {exc}")


def detect_black_bars(
    video_path: Path | str, seconds: float = 10.0, samples: int = 5
) -> dict[str, Any]:
    """Measure black letterbox/pillarbox bars and report the crop rectangle.

    Returns structured dict with crop info and parsed crop parameters.
    """
    try:
        args = [
            str(Path(video_path).resolve()),
            "--json",
            "--seconds",
            f"{seconds:g}",
            "--samples",
            str(samples),
        ]
        result = run_tool("cropdetect", args)
        doc = _parse_json_doc(result.stdout) or _parse_json_doc(result.stderr)
        if doc is None:
            return {
                "ok": False,
                "crop": None,
                "error": f"cropdetect exited {result.returncode}",
                "raw": {},
            }
        doc["ok"] = result.returncode == 0
        return doc
    except Exception as exc:
        logger.warning("cropdetect failed for %s: %s", video_path, exc)
        return {"ok": False, "crop": None, "error": str(exc), "raw": {}}


def detect_scenes_and_highlights(
    video_path: Path | str, highlights: int = 5, target_duration: float | None = None
) -> dict[str, Any]:
    """Find scene changes, audio peaks, and propose candidate highlight ranges."""
    try:
        args = [str(Path(video_path).resolve()), "--json", "--highlights", str(highlights)]
        if target_duration is not None:
            args.extend(["--target", f"{target_duration:g}"])
        result = run_tool("scenes", args)
        doc = _parse_json_doc(result.stdout) or _parse_json_doc(result.stderr)
        if doc is None:
            return {
                "ok": False,
                "scenes": [],
                "highlights": [],
                "error": f"scenes exited {result.returncode}",
                "raw": {},
            }
        doc["ok"] = result.returncode == 0
        return doc
    except Exception as exc:
        logger.warning("scenes detection failed for %s: %s", video_path, exc)
        return {"ok": False, "scenes": [], "highlights": [], "error": str(exc), "raw": {}}


def detect_speech_silences(
    video_path: Path | str, threshold_db: float = -38.0
) -> dict[str, Any]:
    """Detect speech silences and dead-air intervals in audio track."""
    try:
        args = [
            str(Path(video_path).resolve()),
            "--list",
            "--json",
            "--threshold",
            f"{threshold_db:g}",
        ]
        result = run_tool("silence", args)
        doc = _parse_json_doc(result.stdout) or _parse_json_doc(result.stderr)
        if doc is None:
            return {
                "ok": False,
                "silences": [],
                "keeps": [],
                "error": f"silence exited {result.returncode}",
                "raw": {},
            }
        doc["ok"] = result.returncode == 0
        return doc
    except Exception as exc:
        logger.warning("silence detection failed for %s: %s", video_path, exc)
        return {"ok": False, "silences": [], "keeps": [], "error": str(exc), "raw": {}}


def apply_speed_ramp(
    video_path: Path | str,
    output_path: Path | str,
    segments: list[str] | list[tuple[float, float, float]],
) -> Path:
    """Step through varying speed factors across contiguous clip segments."""
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    args = [str(Path(video_path).resolve()), "-o", str(out)]
    for seg in segments:
        if isinstance(seg, (tuple, list)):
            seg_str = f"{seg[0]:g}-{seg[1]:g}:{seg[2]:g}"
        else:
            seg_str = str(seg)
        args.extend(["--segment", seg_str])

    res = run_tool("speedramp", args)
    if res.returncode != 0:
        raise FfmpegToolError(f"speedramp failed (exit {res.returncode}): {res.stderr}")
    return out


def insert_broll_cutaways(
    main_video: Path | str,
    output_path: Path | str,
    insertions: list[dict[str, Any]],
    audio: str = "a",
) -> Path:
    """Cut away to B-roll clips over A-roll footage while preserving main timeline."""
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    args = [str(Path(main_video).resolve()), "-o", str(out), "--audio", audio]
    for item in insertions:
        clip = item.get("insert") or item.get("clip") or item.get("path")
        if not clip:
            continue
        args.extend(["--insert", str(Path(clip).resolve())])
        args.extend(["--at", f"{item['at']}"])
        if "duration" in item:
            args.extend(["--duration", f"{item['duration']}"])
        elif "end" in item:
            args.extend(["--end", f"{item['end']}"])
        if "from" in item:
            args.extend(["--from", f"{item['from']}"])

    res = run_tool("broll", args)
    if res.returncode != 0:
        raise FfmpegToolError(f"broll failed (exit {res.returncode}): {res.stderr}")
    return out


def render_safe_zone_preview(
    video_path: Path | str,
    output_path: Path | str,
    platform: str = "shorts",
) -> Path:
    """Render preview with shaded platform UI safe-zone guides (Shorts, TikTok, Reels, YouTube)."""
    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    norm_platform = (platform or "shorts").strip().lower()

    if out.suffix.lower() == ".png":
        res = run_tool(
            "look",
            [
                str(Path(video_path).resolve()),
                "-o",
                str(out),
                "--safe",
                norm_platform,
                "--overwrite",
            ],
        )
        if res.returncode == 0 and out.is_file():
            return out

    # For video formats or if look tool did not produce expected file, draw boxes directly with ffmpeg
    z = PLATFORM_SAFE_ZONES.get(norm_platform, PLATFORM_SAFE_ZONES["shorts"])
    boxes: list[str] = []
    for edge, frac in (
        ("top", z["top"]),
        ("bottom", z["bottom"]),
        ("left", z["left"]),
        ("right", z["right"]),
    ):
        if frac <= 0:
            continue
        if edge == "top":
            boxes.append(f"drawbox=x=0:y=0:w=iw:h=ih*{frac:g}:color=red@0.35:t=fill")
        elif edge == "bottom":
            boxes.append(
                f"drawbox=x=0:y=ih*(1-{frac:g}):w=iw:h=ih*{frac:g}:color=red@0.35:t=fill"
            )
        elif edge == "left":
            boxes.append(f"drawbox=x=0:y=0:w=iw*{frac:g}:h=ih:color=red@0.20:t=fill")
        else:
            boxes.append(
                f"drawbox=x=iw*(1-{frac:g}):y=0:w=iw*{frac:g}:h=ih:color=red@0.20:t=fill"
            )

    vf = ",".join(boxes) if boxes else "null"
    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(Path(video_path).resolve()),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "copy",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=_child_env())
    return out


__all__ = [
    "ALLOWED_TOOLS",
    "TOOLS_DIR",
    "FfmpegToolError",
    "apply_speed_ramp",
    "check_delivery",
    "detect_black_bars",
    "detect_scenes_and_highlights",
    "detect_speech_silences",
    "insert_broll_cutaways",
    "render_safe_zone_preview",
    "run_tool",
]
