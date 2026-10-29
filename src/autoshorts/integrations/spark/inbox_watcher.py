"""Gemini Spark companion integration and watched inbox folder.

ECOSYSTEM STATUS & ARCHITECTURAL STANCE (PLAN.md section 5.6):
As of late 2026, Google provides NO public REST API, SDK, or MCP server for
third-party programmatic execution of Gemini Spark. Spark is a 24/7 autonomous
personal AI agent managed by users inside Google Workspace and Chrome.

Direct programmatic API automation of Spark is UNVERIFIED / FUTURE.
AutoShorts implements a loosely coupled companion architecture:
1. User-pasteable Spark Skill prompts (see spark_prompts.md and generate_spark_skill_prompt()).
2. Watched inbox/ directory for dropped JSON/YAML/Markdown task files (e.g. synced via Google Drive).
3. Web dashboard paste import box for instant manual ingestion.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from autoshorts.core.models import Job, JobStatus
from autoshorts.core.paths import get_inbox_dir

logger = logging.getLogger(__name__)


def parse_timestamp_to_seconds(ts: str | float | None) -> float | None:
    """Parse a timestamp string ('MM:SS', 'HH:MM:SS', '90', 90.0) into float seconds."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts) if ts >= 0 else None

    raw = str(ts).strip()
    if not raw:
        return None

    # Check MM:SS or HH:MM:SS format
    if ":" in raw:
        parts = raw.split(":")
        try:
            if len(parts) == 2:
                mins = float(parts[0])
                secs = float(parts[1])
                return mins * 60.0 + secs
            elif len(parts) == 3:
                hrs = float(parts[0])
                mins = float(parts[1])
                secs = float(parts[2])
                return hrs * 3600.0 + mins * 60.0 + secs
        except ValueError:
            return None

    try:
        val = float(raw)
        return val if val >= 0 else None
    except ValueError:
        return None


class SparkClipHint(BaseModel):
    """Normalized clipping hint or instruction task extracted from Spark output."""

    source_url: str = Field(..., description="YouTube video URL")
    clip_title: str | None = Field(default=None, description="Topic or hook title")
    start_time: str | float | int | None = Field(
        default=None, description="Raw start time string or number"
    )
    end_time: str | float | int | None = Field(
        default=None, description="Raw end time string or number"
    )
    start_sec: float | None = Field(default=None, description="Resolved start time in seconds")
    end_sec: float | None = Field(default=None, description="Resolved end time in seconds")
    hook_text: str | None = Field(default=None, description="First 3-second hook question/card")
    target_background: str = Field(default="minecraft", description="Recommended background theme")
    notes: str | None = Field(default=None, description="Editorial notes or timestamps")
    custom_instructions: str | None = Field(
        default=None, description="Combined operator instructions"
    )

    def model_post_init(self, context: Any, /) -> None:
        """Resolve start_sec and end_sec from start_time and end_time if not provided."""
        if self.start_sec is None and self.start_time is not None:
            self.start_sec = parse_timestamp_to_seconds(self.start_time)
        if self.end_sec is None and self.end_time is not None:
            self.end_sec = parse_timestamp_to_seconds(self.end_time)

    def to_custom_instructions(self) -> str:
        """Format a concise instruction string for pipeline ingestion."""
        parts: list[str] = []
        if self.clip_title:
            parts.append(f"focus: {self.clip_title}")
        if self.start_sec is not None and self.end_sec is not None:
            parts.append(f"window: {self.start_sec:.0f}-{self.end_sec:.0f}s")
        if self.hook_text:
            parts.append(f"hook: {self.hook_text}")
        if self.target_background:
            parts.append(f"bg: {self.target_background}")
        if self.notes:
            parts.append(f"notes: {self.notes}")
        if self.custom_instructions:
            parts.append(self.custom_instructions)
        return " | ".join(parts)


def generate_spark_skill_prompt(
    topic: str = "{TOPIC}",
    target_background: str = "minecraft",
    duration_min: int = 30,
    duration_max: int = 55,
) -> str:
    """Generate ready-to-paste Gemini Spark Skill prompt text block per PLAN.md section 5.6.

    Users paste this prompt into Gemini Spark's persistent prompt memory or conversation.
    """
    return f"""[GEMINI SPARK SKILL: VIRAL SHORT CURATOR]
Role: You are an autonomous YouTube Shorts research agent.
Goal: Monitor selected YouTube channels or search for high-retention long-form videos on topic: {topic}.
Instructions:
1. Identify high-engagement videos released in the past 7 days with duration > 10 minutes.
2. For each video, locate 1 to 2 standalone viral segments ({duration_min}-{duration_max} seconds) with high curiosity hooks, surprising revelations, or emotional peaks.
3. For each candidate segment, format your findings strictly as this JSON block:
{{
  "source_url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "clip_title": "Descriptive Topic Hook",
  "start_time": "MM:SS",
  "end_time": "MM:SS",
  "hook_text": "First 3-second hook question",
  "target_background": "{target_background}",
  "notes": "Key punchline occurs at MM:SS"
}}
"""


def _normalize_dict_to_hint(d: dict[str, Any]) -> SparkClipHint | None:
    """Convert a dictionary with loose key names to a validated SparkClipHint."""
    if not isinstance(d, dict):
        return None

    # Resolve URL from candidate keys
    url = (
        d.get("source_url")
        or d.get("url")
        or d.get("video_url")
        or d.get("link")
        or d.get("youtube_url")
    )
    if not url or not isinstance(url, str):
        return None

    url_str = url.strip()
    if not ("youtube.com" in url_str or "youtu.be" in url_str or url_str.startswith("http")):
        return None

    title = d.get("clip_title") or d.get("title") or d.get("topic")
    start_t = d.get("start_time") or d.get("start")
    end_t = d.get("end_time") or d.get("end")
    hook = d.get("hook_text") or d.get("hook")
    bg = d.get("target_background") or d.get("background") or d.get("bg") or "minecraft"
    notes = d.get("notes") or d.get("note") or d.get("description")
    instr = d.get("custom_instructions") or d.get("instructions")

    return SparkClipHint(
        source_url=url_str,
        clip_title=str(title).strip() if title else None,
        start_time=start_t,
        end_time=end_t,
        hook_text=str(hook).strip() if hook else None,
        target_background=str(bg).strip() if bg else "minecraft",
        notes=str(notes).strip() if notes else None,
        custom_instructions=str(instr).strip() if instr else None,
    )


def parse_spark_import(text: str) -> list[SparkClipHint]:
    """Parse pasted text, Markdown, JSON, or YAML from Gemini Spark into structured hints.

    Supports:
    - Raw JSON (single object or list of objects)
    - Markdown documents containing ```json or ```yaml code blocks
    - Raw YAML dictionaries or lists
    - Plain-text lines with pipe-delimited instruction parameters

    Raises:
        ValueError: If no valid YouTube tasks can be parsed.
    """
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Input text is empty")

    hints: list[SparkClipHint] = []

    # 1. Check for markdown code blocks (e.g. ```json ... ``` or ```yaml ... ```)
    code_blocks = re.findall(r"```(?:json|yaml|yml)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE)
    candidate_texts = code_blocks if code_blocks else [cleaned]

    for candidate in candidate_texts:
        c_strip = candidate.strip()
        # Attempt JSON parse
        try:
            parsed_json = json.loads(c_strip)
            if isinstance(parsed_json, list):
                for item in parsed_json:
                    hint = _normalize_dict_to_hint(item)
                    if hint:
                        hints.append(hint)
            elif isinstance(parsed_json, dict):
                hint = _normalize_dict_to_hint(parsed_json)
                if hint:
                    hints.append(hint)
            if hints:
                return hints
        except Exception:
            pass

        # Attempt YAML parse
        try:
            parsed_yaml = yaml.safe_load(c_strip)
            if isinstance(parsed_yaml, list):
                for item in parsed_yaml:
                    hint = _normalize_dict_to_hint(item)
                    if hint:
                        hints.append(hint)
            elif isinstance(parsed_yaml, dict):
                hint = _normalize_dict_to_hint(parsed_yaml)
                if hint:
                    hints.append(hint)
            if hints:
                return hints
        except Exception:
            pass

    # 2. Line-by-line fallback for pipe-delimited text or raw URL lists
    lines = cleaned.splitlines()
    for line in lines:
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("#"):
            continue

        if "youtube.com" in line_clean or "youtu.be" in line_clean:
            # Check for pipe delimiters: url | key: val | key: val
            parts = [p.strip() for p in line_clean.split("|")]
            url_part = parts[0]
            # Match URL
            match = re.search(r"https?://[^\s]+", url_part)
            if match:
                url = match.group(0)
                other_instructions = " | ".join(parts[1:]) if len(parts) > 1 else None
                hints.append(
                    SparkClipHint(
                        source_url=url,
                        custom_instructions=other_instructions,
                    )
                )

    if not hints:
        raise ValueError("Could not parse any valid YouTube tasks from input text")

    return hints


class SparkInboxWatcher:
    """Monitors inbox/ directory for dropped task specification files."""

    def __init__(
        self,
        inbox_dir: Path | str | None = None,
        state_manager: Any | None = None,
        poll_interval_sec: int = 10,
        on_jobs_parsed: Callable[[list[SparkClipHint], Path], None] | None = None,
    ) -> None:
        """Initialize the inbox watcher.

        Args:
            inbox_dir: Watched folder path. Defaults to get_inbox_dir().
            state_manager: Optional StateManager to auto-enqueue jobs.
            poll_interval_sec: Polling interval when running daemon loop.
            on_jobs_parsed: Optional callback invoked with (hints, file_path).
        """
        self.inbox_dir = Path(inbox_dir).resolve() if inbox_dir else get_inbox_dir()
        self.processed_dir = (self.inbox_dir / "processed").resolve()
        self.failed_dir = (self.inbox_dir / "failed").resolve()

        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

        self.state_manager = state_manager
        self.poll_interval_sec = max(1, poll_interval_sec)
        self.on_jobs_parsed = on_jobs_parsed

        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        """Return True if background watcher thread is currently running."""
        return self._running

    def poll_once(self) -> list[tuple[Path, list[SparkClipHint]]]:
        """Execute a single polling sweep over the watched inbox folder.

        Processes eligible files (.json, .yaml, .yml, .md, .txt), moves valid files
        to inbox/processed/, and moves invalid files to inbox/failed/ with reason file.

        Returns:
            List of tuples of (processed_file_path, parsed_hints).
        """
        valid_extensions = {".json", ".yaml", ".yml", ".md", ".txt"}
        results: list[tuple[Path, list[SparkClipHint]]] = []

        if not self.inbox_dir.exists():
            return results

        def _mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                # Vanished between listing and sorting; it is skipped in the loop below.
                return 0.0

        # Serialize sweeps so an overlapping caller cannot parse and enqueue the same file twice.
        with self._poll_lock:
            # Exclude processed/ and failed/ subfolders
            entries = sorted(
                [
                    p
                    for p in self.inbox_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in valid_extensions
                ],
                key=_mtime,
            )
            for file_path in entries:
                self._process_entry(file_path, results)
        return results

    def _process_entry(
        self,
        file_path: Path,
        results: list[tuple[Path, list[SparkClipHint]]],
    ) -> None:
        """Parse and route one inbox file; a file removed mid-sweep is skipped, never fatal."""
        if not file_path.is_file():
            logger.info("Inbox file vanished before processing, skipping: %s", file_path.name)
            return
        try:
            content = file_path.read_text(encoding="utf-8")
            hints = parse_spark_import(content)

            # Auto-enqueue to state manager if provided
            if self.state_manager is not None:
                for hint in hints:
                    job = Job(
                        source_url=hint.source_url,
                        custom_instructions=hint.to_custom_instructions(),
                        status=JobStatus.QUEUED,
                    )
                    self.state_manager.save_job(job)

            # Invoke custom callback if provided
            if self.on_jobs_parsed is not None:
                try:
                    self.on_jobs_parsed(hints, file_path)
                except Exception as cb_err:
                    logger.error("Callback error during inbox processing: %s", cb_err)

            # Move file to inbox/processed/
            dest_path = self._move_file_safe(file_path, self.processed_dir)
            results.append((dest_path, hints))
            logger.info("Successfully ingested Spark file: %s -> %s", file_path.name, dest_path)

        except Exception as err:
            if not file_path.exists():
                # Removed or moved by someone else mid-processing; nothing left to quarantine.
                logger.info(
                    "Inbox file '%s' disappeared during processing: %s", file_path.name, err
                )
                return
            logger.warning("Failed to parse inbox file '%s': %s", file_path.name, err)
            # Move to failed/ and write reason file
            try:
                dest_path = self._move_file_safe(file_path, self.failed_dir)
            except OSError as move_err:
                logger.warning("Could not quarantine inbox file '%s': %s", file_path.name, move_err)
                return
            reason_file = self.failed_dir / f"{dest_path.name}.reason.txt"
            try:
                reason_content = (
                    f"Timestamp: {datetime.now(UTC).isoformat()}\n"
                    f"File: {file_path.name}\n"
                    f"Error: {err}\n"
                )
                reason_file.write_text(reason_content, encoding="utf-8")
            except Exception as write_err:
                logger.debug("Could not write failure reason file: %s", write_err)

    def _move_file_safe(self, source: Path, destination_dir: Path) -> Path:
        """Safely move a file into destination_dir, avoiding name collisions."""
        destination_dir.mkdir(parents=True, exist_ok=True)
        dest = destination_dir / source.name
        if dest.exists():
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
            dest = destination_dir / f"{source.stem}_{timestamp}{source.suffix}"

        shutil.move(str(source), str(dest))
        return dest

    def start(self) -> None:
        """Start the background inbox polling thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="SparkInboxWatcher"
            )
            self._thread.start()
            logger.info(
                "SparkInboxWatcher started polling %s every %ds",
                self.inbox_dir,
                self.poll_interval_sec,
            )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background inbox polling thread."""
        with self._lock:
            if not self._running:
                return
            self._running = False

        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
            logger.info("SparkInboxWatcher stopped")

    def _run_loop(self) -> None:
        """Background loop executing poll_once periodically."""
        while self._running:
            try:
                self.poll_once()
            except Exception as e:
                logger.error("Unexpected error in inbox watcher loop: %s", e)

            # Sleep in small increments for responsive shutdown
            for _ in range(self.poll_interval_sec * 10):
                if not self._running:
                    break
                time.sleep(0.1)
