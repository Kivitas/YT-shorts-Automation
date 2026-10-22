"""Path resolution, directory management, and atomic file helpers.

Provides Windows-safe path handling, directory getters, FFmpeg filter path escaping,
path-safety assertions, and atomic file writing.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from autoshorts.core.exceptions import PathSecurityError

PathLike = str | os.PathLike[str] | Path


def get_project_root() -> Path:
    """Return the absolute path to the project root directory.

    Resolves upward from this file until reaching the directory containing pyproject.toml
    or config/config.yaml, falling back to current working directory.
    """
    current = Path(__file__).resolve()
    for parent in [current] + list(current.parents):
        if (parent / "pyproject.toml").is_file() or (parent / "config" / "config.yaml").is_file():
            return parent
    return Path.cwd().resolve()


PROJECT_ROOT = get_project_root()


def get_work_dir(job_id: str | None = None) -> Path:
    """Return the work/ directory path, optionally scoped to a job ID."""
    base = (PROJECT_ROOT / "work").resolve()
    base.mkdir(parents=True, exist_ok=True)
    if job_id:
        job_dir = (base / job_id).resolve()
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir
    return base


def get_output_dir() -> Path:
    """Return the output/ directory for rendered master Shorts."""
    path = (PROJECT_ROOT / "output").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_dir() -> Path:
    """Return the data/ directory for SQLite database, models, and credentials."""
    path = (PROJECT_ROOT / "data").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_inbox_dir() -> Path:
    """Return the inbox/ drop directory for Gemini Spark / external tasks."""
    path = (PROJECT_ROOT / "inbox").resolve()
    path.mkdir(parents=True, exist_ok=True)
    (path / "processed").resolve().mkdir(parents=True, exist_ok=True)
    return path


def get_assets_dir() -> Path:
    """Return the assets/ directory for backgrounds, fonts, music, and sfx."""
    path = (PROJECT_ROOT / "assets").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_config_dir() -> Path:
    """Return the config/ directory."""
    path = (PROJECT_ROOT / "config").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_project_directories() -> None:
    """Ensure all core project directories exist."""
    get_work_dir()
    get_output_dir()
    get_data_dir()
    get_inbox_dir()
    get_assets_dir()
    get_config_dir()
    (PROJECT_ROOT / "data" / "models").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "assets" / "backgrounds" / "minecraft").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "assets" / "backgrounds" / "subway").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "assets" / "music").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "assets" / "sfx").mkdir(parents=True, exist_ok=True)


def escape_ffmpeg_filter_path(file_path: PathLike) -> str:
    r"""Escape a filesystem path for safe use inside an FFmpeg filter argument on Windows.

    FFmpeg treats colons (':') as option delimiters and backslashes ('\') as escape characters.
    On Windows, drive letters (e.g., C:\path) must be converted so the colon is escaped ('C\:/path')
    and backslashes are converted to forward slashes.
    Single quotes are added around the escaped path per FFmpeg documentation.

    Example:
        'C:\work\subs.ass' -> "'C\\:/work/subs.ass'"
    """
    raw_str = str(Path(file_path).resolve())
    # Normalize backslashes to forward slashes
    forward_str = raw_str.replace("\\", "/")
    # Escape colon (e.g. C: -> C\:)
    escaped_str = forward_str.replace(":", r"\:")
    return f"'{escaped_str}'"


# Cleanup safety guard paths per PLAN.md section 9.1
ALLOWED_CLEANUP_ROOTS = [
    (PROJECT_ROOT / "work").resolve(),
    (PROJECT_ROOT / "output").resolve(),
]

FORBIDDEN_PROTECTED_PATHS = [
    (PROJECT_ROOT / "assets").resolve(),
    (PROJECT_ROOT / "data").resolve(),
    (PROJECT_ROOT / "src").resolve(),
    (PROJECT_ROOT / "config").resolve(),
    (PROJECT_ROOT / "logs").resolve(),
    (PROJECT_ROOT / "tests").resolve(),
    (PROJECT_ROOT / "inbox").resolve(),
    PROJECT_ROOT.resolve(),
]


def assert_safe_to_delete(target_path: PathLike) -> None:
    """Enforce strict directory guards before any file deletion or cleanup.

    Intermediate working files inside work/ or output/ are eligible for removal.
    Protected system, asset, data, source, or root directories raise PathSecurityError.
    """
    abs_target = Path(target_path).resolve()

    # Rule 1: Cannot delete the project root or the cleanup root directories themselves
    for root_dir in ALLOWED_CLEANUP_ROOTS + [PROJECT_ROOT.resolve()]:
        if abs_target == root_dir:
            raise PathSecurityError(f"CRITICAL: Attempted deletion of root directory: {abs_target}")

    # Rule 2: Must be strictly inside an allowed root (work/ or output/)
    is_allowed = any(
        abs_target.is_relative_to(allowed) and abs_target != allowed
        for allowed in ALLOWED_CLEANUP_ROOTS
    )
    if not is_allowed:
        raise PathSecurityError(
            f"Path-guard violation: {abs_target} is outside allowed cleanup roots ({ALLOWED_CLEANUP_ROOTS})"
        )

    # Rule 3: Must NOT be inside any forbidden protected subdirectory (e.g. assets, data, src)
    for protected in FORBIDDEN_PROTECTED_PATHS:
        if protected == PROJECT_ROOT.resolve():
            continue
        if abs_target == protected or abs_target.is_relative_to(protected):
            raise PathSecurityError(
                f"CRITICAL: Attempted deletion inside protected directory {protected}: {abs_target}"
            )


def atomic_write(
    target_path: PathLike,
    content: str | bytes,
    mode: str = "w",
    encoding: str = "utf-8",
) -> Path:
    """Atomically write content to a file using a temporary file in the same directory.

    Ensures that partial writes never corrupt destination files, even during power cuts
    or process termination on Windows.

    Args:
        target_path: Destination file path.
        content: String or bytes to write.
        mode: "w" for text, "wb" for binary.
        encoding: Text encoding (ignored if writing bytes).

    Returns:
        The resolved Path of the written file.
    """
    dest = Path(target_path).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    is_binary = "b" in mode or isinstance(content, bytes)
    actual_mode = "wb" if is_binary else "w"

    # Create temporary file in the exact same directory so rename is an atomic filesystem operation
    temp_fd, temp_file_path = tempfile.mkstemp(
        dir=dest.parent,
        prefix=f".{dest.name}.",
        suffix=".tmp",
    )

    try:
        if is_binary:
            binary_data = content if isinstance(content, bytes) else content.encode(encoding)
            with os.fdopen(temp_fd, actual_mode) as f:
                f.write(binary_data)
                f.flush()
                os.fsync(f.fileno())
        else:
            text_data = content if isinstance(content, str) else content.decode(encoding)
            with os.fdopen(temp_fd, actual_mode, encoding=encoding) as f:
                f.write(text_data)
                f.flush()
                os.fsync(f.fileno())

        # Atomic replace on Windows and POSIX
        os.replace(temp_file_path, dest)
        return dest
    except Exception:
        # Cleanup temp file on failure
        if os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except OSError:
                pass
        raise


def resolve_yt_dlp_binary(configured_binary: str = "yt-dlp") -> str:
    """Resolve usable yt-dlp binary command name or absolute path.

    Checks system PATH first. If not found, checks for executable in active
    virtual environment Scripts/ (Windows) or bin/ (POSIX), or sys.executable directory.
    Falls back to configured_binary if not found.
    """
    if shutil.which(configured_binary):
        return configured_binary

    candidates = [
        Path(sys.prefix)
        / ("Scripts" if os.name == "nt" else "bin")
        / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp"),
        Path(sys.executable).parent
        / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())

    return configured_binary

