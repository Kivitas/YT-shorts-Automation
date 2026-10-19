"""Tests for Gemini Spark companion integration, prompt generation, and inbox watcher."""

import json
from pathlib import Path

import pytest

from autoshorts.core.models import JobStatus
from autoshorts.core.state_manager import StateManager
from autoshorts.integrations.spark.inbox_watcher import (
    SparkInboxWatcher,
    generate_spark_skill_prompt,
    parse_spark_import,
    parse_timestamp_to_seconds,
)


def test_timestamp_parsing_variants():
    """Verify conversion of various timestamp formats into seconds."""
    assert parse_timestamp_to_seconds("01:30") == 90.0
    assert parse_timestamp_to_seconds("0:45") == 45.0
    assert parse_timestamp_to_seconds("01:02:03") == 3723.0
    assert parse_timestamp_to_seconds(90) == 90.0
    assert parse_timestamp_to_seconds(45.5) == 45.5
    assert parse_timestamp_to_seconds("120") == 120.0
    assert parse_timestamp_to_seconds(None) is None
    assert parse_timestamp_to_seconds("invalid") is None
    assert parse_timestamp_to_seconds(-10) is None


def test_parse_spark_json_single():
    """Verify parsing of a single JSON task block from Spark."""
    raw_json = json.dumps(
        {
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "clip_title": "Never Gonna Give You Up Peak",
            "start_time": "01:15",
            "end_time": "02:00",
            "hook_text": "Did you know this 80s hit?",
            "target_background": "subway",
            "notes": "Iconic dance moment",
        }
    )

    hints = parse_spark_import(raw_json)
    assert len(hints) == 1
    hint = hints[0]
    assert hint.source_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert hint.clip_title == "Never Gonna Give You Up Peak"
    assert hint.start_sec == 75.0
    assert hint.end_sec == 120.0
    assert hint.target_background == "subway"
    assert "Did you know" in hint.hook_text

    # Custom instruction assembly
    instructions = hint.to_custom_instructions()
    assert "focus: Never Gonna Give You Up Peak" in instructions
    assert "window: 75-120s" in instructions
    assert "bg: subway" in instructions


def test_parse_spark_json_array():
    """Verify parsing of an array of JSON objects from Spark."""
    raw_array = json.dumps(
        [
            {
                "source_url": "https://www.youtube.com/watch?v=vid1",
                "clip_title": "Moment 1",
                "start_time": "00:30",
                "end_time": "01:10",
            },
            {
                "source_url": "https://www.youtube.com/watch?v=vid2",
                "clip_title": "Moment 2",
                "start_time": "05:00",
                "end_time": "05:45",
            },
        ]
    )

    hints = parse_spark_import(raw_array)
    assert len(hints) == 2
    assert hints[0].source_url == "https://www.youtube.com/watch?v=vid1"
    assert hints[0].start_sec == 30.0
    assert hints[1].source_url == "https://www.youtube.com/watch?v=vid2"
    assert hints[1].start_sec == 300.0


def test_parse_spark_yaml():
    """Verify parsing of YAML format tasks."""
    raw_yaml = """
- source_url: https://www.youtube.com/watch?v=xyz123
  clip_title: YAML Highlight
  start_time: "02:10"
  end_time: "02:55"
  hook_text: Incredible insight
  target_background: minecraft
"""

    hints = parse_spark_import(raw_yaml)
    assert len(hints) == 1
    hint = hints[0]
    assert hint.source_url == "https://www.youtube.com/watch?v=xyz123"
    assert hint.start_sec == 130.0
    assert hint.end_sec == 175.0
    assert hint.target_background == "minecraft"


def test_parse_spark_markdown_code_blocks():
    """Verify parsing Markdown text with embedded ```json code fences."""
    markdown_doc = """# Research Results from Gemini Spark

Here are the best candidate segments for your viral shorts:

```json
{
  "source_url": "https://www.youtube.com/watch?v=md_vid",
  "clip_title": "AI Breakthrough Explained",
  "start_time": "03:15",
  "end_time": "04:00",
  "hook_text": "Is AGI closer than we think?",
  "target_background": "subway"
}
```

Let me know if you would like me to find more clips!
"""

    hints = parse_spark_import(markdown_doc)
    assert len(hints) == 1
    assert hints[0].source_url == "https://www.youtube.com/watch?v=md_vid"
    assert hints[0].clip_title == "AI Breakthrough Explained"
    assert hints[0].start_sec == 195.0


def test_parse_spark_pipe_delimited_text():
    """Verify parsing line-by-line pipe-delimited text from manual operator paste."""
    text_data = """
https://www.youtube.com/watch?v=line1 | clips: 2 | bg: subway | focus: funny reactions
https://youtu.be/line2 | focus: deep wisdom
"""

    hints = parse_spark_import(text_data)
    assert len(hints) == 2
    assert hints[0].source_url == "https://www.youtube.com/watch?v=line1"
    assert "bg: subway" in hints[0].custom_instructions
    assert hints[1].source_url == "https://youtu.be/line2"


def test_parse_spark_empty_or_invalid_raises():
    """Verify empty input or unparseable text raises ValueError."""
    with pytest.raises(ValueError, match="empty"):
        parse_spark_import("   ")

    with pytest.raises(ValueError, match="Could not parse"):
        parse_spark_import("Random rambling with no YouTube links or valid structure whatsoever.")


def test_spark_skill_prompt_generator():
    """Verify prompt generator constructs template per PLAN.md section 5.6."""
    prompt = generate_spark_skill_prompt(
        topic="Quantum Computing",
        target_background="minecraft",
        duration_min=30,
        duration_max=50,
    )
    assert "[GEMINI SPARK SKILL: VIRAL SHORT CURATOR]" in prompt
    assert "Quantum Computing" in prompt
    assert '"target_background": "minecraft"' in prompt
    assert "30-50 seconds" in prompt


def test_inbox_watcher_poll_and_move_success(tmp_path: Path):
    """Verify watcher parses valid inbox files and moves them to inbox/processed/."""
    inbox_dir = tmp_path / "inbox"
    watcher = SparkInboxWatcher(inbox_dir=inbox_dir)

    # Place a valid json file in inbox/
    task_file = inbox_dir / "spark_task_1.json"
    task_file.write_text(
        json.dumps(
            {
                "source_url": "https://www.youtube.com/watch?v=inbox_vid",
                "clip_title": "Inbox Discovery",
                "start_time": "01:00",
                "end_time": "01:45",
            }
        ),
        encoding="utf-8",
    )

    results = watcher.poll_once()
    assert len(results) == 1
    dest_path, hints = results[0]

    # File moved to processed/
    assert not task_file.exists()
    assert dest_path.is_file()
    assert dest_path.parent == watcher.processed_dir
    assert len(hints) == 1
    assert hints[0].clip_title == "Inbox Discovery"


def test_inbox_watcher_invalid_file_moves_to_failed(tmp_path: Path):
    """Verify watcher moves unparseable files to inbox/failed/ with reason file."""
    inbox_dir = tmp_path / "inbox"
    watcher = SparkInboxWatcher(inbox_dir=inbox_dir)

    invalid_file = inbox_dir / "bad_task.json"
    invalid_file.write_text("Not valid JSON at all: {[[", encoding="utf-8")

    results = watcher.poll_once()
    assert len(results) == 0

    assert not invalid_file.exists()
    failed_target = watcher.failed_dir / "bad_task.json"
    assert failed_target.is_file()

    reason_file = watcher.failed_dir / "bad_task.json.reason.txt"
    assert reason_file.is_file()
    assert "Error:" in reason_file.read_text(encoding="utf-8")


def test_inbox_watcher_with_state_manager(tmp_path: Path):
    """Verify watcher automatically enqueues jobs in StateManager when provided."""
    inbox_dir = tmp_path / "inbox"
    db_path = tmp_path / "test_state.db"
    sm = StateManager(db_path)

    watcher = SparkInboxWatcher(inbox_dir=inbox_dir, state_manager=sm)

    task_file = inbox_dir / "spark_batch.json"
    task_file.write_text(
        json.dumps(
            [
                {"source_url": "https://www.youtube.com/watch?v=batch1", "clip_title": "Batch 1"},
                {"source_url": "https://www.youtube.com/watch?v=batch2", "clip_title": "Batch 2"},
            ]
        ),
        encoding="utf-8",
    )

    results = watcher.poll_once()
    assert len(results) == 1

    # Verify both jobs were saved to SQLite state manager with status QUEUED
    queued_jobs = sm.list_jobs(status=JobStatus.QUEUED)
    assert len(queued_jobs) == 2
    urls = [j.source_url for j in queued_jobs]
    assert "https://www.youtube.com/watch?v=batch1" in urls
    assert "https://www.youtube.com/watch?v=batch2" in urls


def test_spark_docstring_unverified_future_notice():
    """Verify module docstring explicitly declares direct Spark automation as unverified/future."""
    import autoshorts.integrations.spark.inbox_watcher as watcher_module

    assert watcher_module.__doc__ is not None
    assert "UNVERIFIED" in watcher_module.__doc__
    assert "FUTURE" in watcher_module.__doc__
    assert "NO public REST API" in watcher_module.__doc__
