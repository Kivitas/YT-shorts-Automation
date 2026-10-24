"""Regression tests for the master CLI (src/autoshorts/cli.py)."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autoshorts import cli
from autoshorts.core.models import JobStatus


def _clean_args(**overrides: object) -> argparse.Namespace:
    base = {"days": 1, "dry_run": True, "job_id": None, "force": False}
    base.update(overrides)
    return argparse.Namespace(**base)


def test_clean_force_never_targets_in_progress_or_unreviewed_jobs() -> None:
    """--force must not widen the purge set beyond COMPLETED/CLEANED jobs."""
    jobs = [
        SimpleNamespace(id="done", status=JobStatus.COMPLETED),
        SimpleNamespace(id="cleaned", status=JobStatus.CLEANED),
        SimpleNamespace(id="review", status=JobStatus.READY_FOR_REVIEW),
        SimpleNamespace(id="running", status=JobStatus.DOWNLOADING_SECTION),
        SimpleNamespace(id="failed", status=JobStatus.FAILED),
    ]
    state = MagicMock()
    state.list_jobs.return_value = jobs
    cleaner = MagicMock()
    cleaner.cleanup_job.return_value = []

    with patch.object(cli, "SafetyCleaner", return_value=cleaner):
        rc = cli.cmd_clean(_clean_args(force=True), MagicMock(), state)

    assert rc == 0
    called_ids = [c.kwargs["job_id"] for c in cleaner.cleanup_job.call_args_list]
    assert called_ids == ["done", "cleaned"]
    # --force is still forwarded so the cleaner can bypass upload verification.
    assert all(c.kwargs["force"] is True for c in cleaner.cleanup_job.call_args_list)


def test_clean_without_force_uses_same_job_filter() -> None:
    jobs = [
        SimpleNamespace(id="review", status=JobStatus.READY_FOR_REVIEW),
        SimpleNamespace(id="done", status=JobStatus.COMPLETED),
    ]
    state = MagicMock()
    state.list_jobs.return_value = jobs
    cleaner = MagicMock()
    cleaner.cleanup_job.return_value = []

    with patch.object(cli, "SafetyCleaner", return_value=cleaner):
        cli.cmd_clean(_clean_args(force=False), MagicMock(), state)

    called_ids = [c.kwargs["job_id"] for c in cleaner.cleanup_job.call_args_list]
    assert called_ids == ["done"]


def test_log_level_accepts_lowercase_and_rejects_unknown() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["--log-level", "debug", "quota"]).log_level == "DEBUG"
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--log-level", "BOGUS", "quota"])
    assert exc.value.code == 2


def test_open_edge_can_be_disabled_and_defaults_on() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["dashboard"]).open_edge is True
    assert parser.parse_args(["dashboard", "--no-open-edge"]).open_edge is False
    assert parser.parse_args(["dashboard", "--open-edge"]).open_edge is True


def test_missing_config_file_is_an_error_not_silent_defaults(tmp_path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    with patch.object(cli, "load_config") as load, pytest.raises(SystemExit) as exc:
        cli.main(["--config", str(missing), "quota"])
    assert exc.value.code == 2
    load.assert_not_called()
