"""Review round 4: retention covers render variants and the captioned master safely."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from autoshorts.cleanup.retention import (
    delete_output_files,
    job_id_from_output_name,
    select_expired_outputs,
)
from autoshorts.core.exceptions import PathSecurityError

NOW = datetime(2026, 11, 6, 12, 0, 0)
DAY = 86400.0


def _file(directory: Path, name: str, age_days: float, size: int = 10) -> Path:
    path = directory / name
    path.write_bytes(b"x" * size)
    stamp = NOW.timestamp() - age_days * DAY
    os.utime(path, (stamp, stamp))
    return path


@pytest.mark.parametrize(
    ("name", "job"),
    [
        ("job1_master.mp4", "job1"),
        ("job1_master_raea.mp4", "job1"),
        ("job1_master_captioned.mp4", "job1"),
        ("job1_master_pillow_ffmpeg.mp4", "job1"),
        ("job1_preview.mp4", "job1"),
        ("my_job_9_master_raea.mp4", "my_job_9"),
    ],
)
def test_managed_names_resolve_to_their_job(name: str, job: str) -> None:
    assert job_id_from_output_name(name) == job


@pytest.mark.parametrize(
    "name",
    [
        "job1.mp4",
        "job1_master.mov",
        "job1_masterpiece.mp4",
        "_master.mp4",
        "job1_thumb.png",
        "..\\evil_master.mp4",
        "sub/job1_master.mp4",
        "job1_master_.mp4",
    ],
)
def test_unmanaged_or_path_like_names_never_match(name: str) -> None:
    assert job_id_from_output_name(name) is None


def test_variants_and_captioned_are_pruned_by_age_like_masters(tmp_path: Path) -> None:
    old = [
        _file(tmp_path, "j1_master.mp4", 30),
        _file(tmp_path, "j1_master_raea.mp4", 30),
        _file(tmp_path, "j1_master_captioned.mp4", 30),
    ]
    fresh = _file(tmp_path, "j2_master_raea.mp4", 1)
    chosen = select_expired_outputs(tmp_path, NOW, 7, None, [])
    assert set(chosen) == set(old) and fresh not in chosen


def test_size_cap_counts_variants(tmp_path: Path) -> None:
    a = _file(tmp_path, "j1_master_raea.mp4", 5, size=600)
    b = _file(tmp_path, "j2_master.mp4", 4, size=600)
    chosen = select_expired_outputs(tmp_path, NOW, None, 1000 / 1024**3, [])
    assert chosen == [a] and b not in chosen


def test_job_still_rendering_is_never_selected(tmp_path: Path) -> None:
    # The primary is old, but a variant of the same job was written a minute ago: still rendering.
    old_primary = _file(tmp_path, "j1_master.mp4", 40)
    _file(tmp_path, "j1_master_raea.mp4", 60 / DAY)
    chosen = select_expired_outputs(tmp_path, NOW, 7, 0, [])
    assert old_primary not in chosen and chosen == []


def test_protected_job_variants_are_kept(tmp_path: Path) -> None:
    _file(tmp_path, "j1_master_raea.mp4", 90)
    assert select_expired_outputs(tmp_path, NOW, 7, 0, ["j1"]) == []


def test_subdirectories_and_symlinks_are_not_scanned(tmp_path: Path) -> None:
    sub = tmp_path / "j1"
    sub.mkdir()
    _file(sub, "j1_master_raea.mp4", 90)
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir()
    target = _file(outside, "secret_master.mp4", 90)
    link = tmp_path / "evil_master.mp4"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert select_expired_outputs(tmp_path, NOW, 7, None, []) == []


def test_delete_refuses_paths_outside_output_dir(tmp_path: Path) -> None:
    out = tmp_path / "output"
    out.mkdir()
    elsewhere = _file(tmp_path, "j1_master_raea.mp4", 90)
    nested = out / "sub"
    nested.mkdir()
    inner = _file(nested, "j1_master_raea.mp4", 90)
    for bad in (elsewhere, inner):
        with pytest.raises(PathSecurityError):
            delete_output_files(out, [bad], dry_run=True)


def test_delete_dry_run_leaves_files(tmp_path: Path) -> None:
    f = _file(tmp_path, "j1_master_captioned.mp4", 90)
    assert delete_output_files(tmp_path, [f], dry_run=True) == [f]
    assert f.exists()
