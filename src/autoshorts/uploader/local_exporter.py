"""Local Export Uploader copying rendered master Shorts to output/export."""

from __future__ import annotations

import glob
import json
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from autoshorts.core.models import UploadResult
from autoshorts.core.paths import get_output_dir

logger = logging.getLogger(__name__)

_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_stem(stem: str, max_len: int = 120) -> str:
    """Make a filename stem safe on Windows and POSIX.

    Replaces invalid characters, strips trailing dots/spaces (Windows silently drops them),
    prefixes reserved device names (CON, NUL, COM1, ...) and never returns an empty string.
    """
    cleaned = _INVALID_FILENAME_CHARS.sub("_", stem).strip().rstrip(". ")
    if not cleaned:
        cleaned = "video"
    if cleaned.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:max_len].rstrip(". ") or "video"


def _unique_path(path: Path) -> Path:
    """Return path, or 'name (2).ext', 'name (3).ext', ... if it already exists."""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


class LocalExportUploader:
    """Exports videos to local output directory for manual review or distribution."""

    def __init__(self, export_dir: Path | str | None = None) -> None:
        """Initialize local exporter with target destination directory."""
        if export_dir is not None:
            self.export_dir = Path(export_dir).resolve()
        else:
            self.export_dir = (get_output_dir() / "export").resolve()
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._exported_files: dict[str, Path] = {}
        # Set by verify(); a local export confirms the file, never a YouTube upload.
        self.last_verification_kind: str = "local_export"

    def upload(
        self,
        path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "unlisted",
    ) -> UploadResult:
        """Copy video to output/export and write metadata sidecar."""
        src_path = Path(path).resolve()
        if not src_path.is_file():
            raise FileNotFoundError(f"Source video file not found: {src_path}")

        video_id = f"local_{uuid4().hex[:11]}"
        safe_suffix = _INVALID_FILENAME_CHARS.sub("", src_path.suffix) or ".mp4"
        dest_filename = f"{_safe_stem(src_path.stem)}_{video_id}{safe_suffix}"
        dest_path = _unique_path(self.export_dir / dest_filename)

        # Copy video file
        try:
            shutil.copy2(src_path, dest_path)
        except BaseException:
            # Never leave a truncated export behind (disk full, locked source, Ctrl+C).
            dest_path.unlink(missing_ok=True)
            raise
        self._exported_files[video_id] = dest_path

        # Write metadata sidecar JSON (never overwrite an existing sidecar)
        meta_path = _unique_path(self.export_dir / f"{dest_path.stem}_meta.json")
        metadata = {
            "video_id": video_id,
            "title": title,
            "description": description,
            "tags": tags or [],
            "privacy_status": privacy,
            "exported_at": datetime.now(UTC).isoformat(),
            "source_path": str(src_path),
            "export_path": str(dest_path),
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Local export completed for '{title}' -> {dest_path}.")

        return UploadResult(
            video_id=video_id,
            url=dest_path.as_uri(),
            status="exported",
            # "Verified" here means the exported file is present on disk; there is no
            # YouTube upload to confirm. verification_kind keeps that distinction explicit.
            verified=True,
            uploaded_at=datetime.now(UTC),
            quota_spent=0,
            privacy_status=privacy,
            raw_response={"export_path": str(dest_path), "sidecar_path": str(meta_path)},
            verification_kind="local_export",
            verification_note="Local file export only; nothing was uploaded to YouTube.",
        )

    def verify(self, video_id: str) -> bool:
        """Confirm exported video exists on disk and is non-empty."""
        if not video_id:
            # An empty id would glob-match every exported file and report a false success.
            return False
        dest_path = self._exported_files.get(video_id)
        if dest_path and dest_path.is_file() and dest_path.stat().st_size > 0:
            return True

        # Search export_dir for matching filename pattern if not in memory dict
        matching = [
            m
            for m in self.export_dir.glob(f"*{glob.escape(video_id)}*")
            if m.is_file() and not m.name.endswith("_meta.json")
        ]
        return len(matching) > 0 and matching[0].stat().st_size > 0
