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
    {"check", "loudness", "silence", "caption", "probe", "metadata", "report"}
)


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


def check_delivery(video_path: Path) -> dict[str, Any]:
    """Run check.py (default platform spec) on ``video_path``; never raises.

    Returns ``{"ok": bool, "issues": [str], "raw": dict}``. ``issues`` lists every FAIL and WARN
    row from check.py, or the failure reason when no JSON result could be read.
    """
    try:
        # The script runs from another folder, so the path must be absolute.
        result = run_tool("check", [str(Path(video_path).resolve()), "--json"])
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
