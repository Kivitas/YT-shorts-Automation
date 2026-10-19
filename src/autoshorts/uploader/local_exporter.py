"""Local Export Uploader copying rendered master Shorts to output/export."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from autoshorts.core.models import UploadResult
from autoshorts.core.paths import get_output_dir

logger = logging.getLogger(__name__)


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
        dest_filename = f"{src_path.stem}_{video_id}{src_path.suffix}"
        dest_path = self.export_dir / dest_filename

        # Copy video file
        shutil.copy2(src_path, dest_path)
        self._exported_files[video_id] = dest_path

        # Write metadata sidecar JSON
        meta_path = self.export_dir / f"{dest_path.stem}_meta.json"
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
            url=f"file:///{dest_path.as_posix()}",
            status="exported",
            verified=True,
            uploaded_at=datetime.now(UTC),
            quota_spent=0,
            privacy_status=privacy,
            raw_response={"export_path": str(dest_path), "sidecar_path": str(meta_path)},
        )

    def verify(self, video_id: str) -> bool:
        """Confirm exported video exists on disk and is non-empty."""
        dest_path = self._exported_files.get(video_id)
        if dest_path and dest_path.is_file() and dest_path.stat().st_size > 0:
            return True

        # Search export_dir for matching filename pattern if not in memory dict
        matching = list(self.export_dir.glob(f"*{video_id}*.mp4"))
        return len(matching) > 0 and matching[0].stat().st_size > 0
