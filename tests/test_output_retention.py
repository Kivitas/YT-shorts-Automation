"""Tests for output/ retention selection and guarded deletion. Uses fake files only."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoshorts.cleanup import retention
from autoshorts.cleanup.retention import (
    delete_output_files,
    job_id_from_output_name,
    select_expired_outputs,
)
from autoshorts.core.config import RetentionConfig
from autoshorts.core.exceptions import PathSecurityError

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
DAY = 86400.0
# A cap of exactly 2048 bytes expressed in GiB, so tests can use tiny fake files.
TINY_GB = 2048 / (1024**3)


def _make(output: Path, name: str, size: int = 100, age_days: float = 0.0) -> Path:
    path = output / name
    path.write_bytes(b"x" * size)
    ts = NOW.timestamp() - age_days * DAY
    os.utime(path, (ts, ts))
    return path


@pytest.fixture
def output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


def test_retention_config_output_defaults() -> None:
    cfg = RetentionConfig()
    assert cfg.output_enabled is False
    assert cfg.output_max_age_days == 14
    assert cfg.output_max_total_gb == 20.0


def test_job_id_parsing_handles_underscores() -> None:
    assert job_id_from_output_name("story_old_1_master.mp4") == "story_old_1"
    assert job_id_from_output_name("longform_x_preview.mp4") == "longform_x"
    assert job_id_from_output_name("notes.txt") is None
    assert job_id_from_output_name("_master.mp4") is None


def test_age_rule_selects_only_files_older_than_max_age(output: Path) -> None:
    old = _make(output, "job_a_master.mp4", age_days=20)
    fresh = _make(output, "job_b_master.mp4", age_days=1)
    borderline = _make(output, "job_c_master.mp4", age_days=14)  # not strictly older

    selected = select_expired_outputs(output, NOW, 14, None, [])

    assert selected == [old]
    assert fresh not in selected and borderline not in selected


def test_size_rule_deletes_oldest_first_until_under_cap(output: Path) -> None:
    oldest = _make(output, "job_1_master.mp4", size=1000, age_days=3)
    middle = _make(output, "job_2_preview.mp4", size=1000, age_days=2)
    newest = _make(output, "job_3_master.mp4", size=1000, age_days=1)

    # Total 3000 bytes, cap 2048: removing the oldest (-> 2000) is enough.
    selected = select_expired_outputs(output, NOW, None, TINY_GB, [])

    assert selected == [oldest]
    assert middle not in selected and newest not in selected


def test_size_rule_removes_several_when_needed(output: Path) -> None:
    a = _make(output, "job_1_master.mp4", size=1000, age_days=3)
    b = _make(output, "job_2_master.mp4", size=1000, age_days=2)
    c = _make(output, "job_3_master.mp4", size=1000, age_days=1)

    # Total 3000, cap 1000: must remove the two oldest, keep the newest.
    selected = select_expired_outputs(output, NOW, None, 1000 / (1024**3), [])

    assert selected == [a, b]
    assert c not in selected


def test_protected_jobs_are_never_selected(output: Path) -> None:
    protected_old = _make(output, "pending_job_master.mp4", size=1000, age_days=60)
    open_old = _make(output, "done_job_master.mp4", size=1000, age_days=60)
    protected_prev = _make(output, "pending_job_preview.mp4", size=1000, age_days=60)

    selected = select_expired_outputs(output, NOW, 14, None, ["pending_job"])

    assert selected == [open_old]
    assert protected_old not in selected and protected_prev not in selected


def test_protected_bytes_count_toward_cap_but_are_not_deleted(output: Path) -> None:
    protected = _make(output, "pending_master.mp4", size=5000, age_days=5)
    removable = _make(output, "done_master.mp4", size=100, age_days=1)

    # Protected file alone exceeds the cap; the removable one is the only candidate.
    selected = select_expired_outputs(output, NOW, None, TINY_GB, ["pending"])

    assert selected == [removable]
    assert protected not in selected


def test_unmanaged_files_are_ignored(output: Path) -> None:
    _make(output, "random_clip.mp4", age_days=400)
    _make(output, "legacy_name.mp4", age_days=400)
    _make(output, "notes.txt", age_days=400)
    (output / "subdir").mkdir()

    assert select_expired_outputs(output, NOW, 14, TINY_GB, []) == []


def test_no_limits_selects_nothing(output: Path) -> None:
    _make(output, "job_1_master.mp4", size=5000, age_days=400)
    assert select_expired_outputs(output, NOW, None, None, []) == []


def test_missing_output_dir_returns_empty(tmp_path: Path) -> None:
    assert select_expired_outputs(tmp_path / "missing", NOW, 1, 1, []) == []


def test_negative_limits_rejected(output: Path) -> None:
    with pytest.raises(ValueError):
        select_expired_outputs(output, NOW, -1, None, [])
    with pytest.raises(ValueError):
        select_expired_outputs(output, NOW, None, -1.0, [])


def test_delete_defaults_to_dry_run(output: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _make(output, "job_1_master.mp4", age_days=30)
    trash = MagicMock()
    monkeypatch.setattr(retention, "_resolve_send2trash", lambda: trash)

    result = delete_output_files(output, [target])

    assert result == [target]
    assert target.exists()
    trash.assert_not_called()


def test_live_delete_uses_send2trash(output: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _make(output, "job_1_master.mp4", age_days=30)
    trash = MagicMock()
    monkeypatch.setattr(retention, "_resolve_send2trash", lambda: trash)

    result = delete_output_files(output, [target], dry_run=False)

    assert result == [target]
    trash.assert_called_once_with(str(target))


def test_delete_rejects_paths_outside_output(
    tmp_path: Path, output: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "job_x_master.mp4"
    outside.write_bytes(b"x")
    trash = MagicMock()
    monkeypatch.setattr(retention, "_resolve_send2trash", lambda: trash)

    with pytest.raises(PathSecurityError):
        delete_output_files(output, [outside], dry_run=False)
    trash.assert_not_called()
    assert outside.exists()


def test_delete_rejects_unmanaged_names(output: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notes = _make(output, "notes.txt", age_days=30)
    trash = MagicMock()
    monkeypatch.setattr(retention, "_resolve_send2trash", lambda: trash)

    with pytest.raises(PathSecurityError):
        delete_output_files(output, [notes], dry_run=False)
    trash.assert_not_called()
