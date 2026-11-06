"""Review round 4: variant output cleanup and longform encoder pipe hang protection."""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from autoshorts.cleanup.safety_cleaner import SafetyCleaner
from autoshorts.core.exceptions import PathSecurityError


def _cleaner(tmp_path: Path) -> tuple[SafetyCleaner, Path, Path]:
    work, out = tmp_path / "work", tmp_path / "output"
    work.mkdir()
    out.mkdir()
    cleaner = SafetyCleaner(
        work_dir=work, output_dir=out, allowed_roots=[work, out], permanent=True
    )
    return cleaner, work, out


def _variants(out: Path, job_id: str) -> Path:
    vdir = out / job_id
    vdir.mkdir()
    (vdir / "raea.mp4").write_bytes(b"v1")
    (vdir / "pillow.mp4").write_bytes(b"v2")
    return vdir


def test_cleanup_removes_variant_outputs_with_the_master(tmp_path: Path) -> None:
    cleaner, _work, out = _cleaner(tmp_path)
    (out / "job1_master.mp4").write_bytes(b"m")
    vdir = _variants(out, "job1")
    cleaner.cleanup_job("job1", verified=True, keep_master_mp4=False)
    assert not vdir.exists()
    assert not (out / "job1_master.mp4").exists()


def test_cleanup_keeps_variants_when_master_is_retained(tmp_path: Path) -> None:
    cleaner, _work, out = _cleaner(tmp_path)
    (out / "job1_master.mp4").write_bytes(b"m")
    vdir = _variants(out, "job1")
    cleaner.cleanup_job("job1", verified=True, keep_master_mp4=True)
    assert (vdir / "raea.mp4").exists() and (out / "job1_master.mp4").exists()


def test_cleanup_never_touches_another_jobs_variants(tmp_path: Path) -> None:
    cleaner, _work, out = _cleaner(tmp_path)
    _variants(out, "job1")
    other = _variants(out, "job2")
    cleaner.cleanup_job("job1", verified=True, keep_master_mp4=False)
    assert (other / "raea.mp4").exists()


def test_cleanup_dry_run_lists_variants_without_deleting(tmp_path: Path) -> None:
    cleaner, _work, out = _cleaner(tmp_path)
    vdir = _variants(out, "job1")
    targets = cleaner.cleanup_job("job1", verified=True, keep_master_mp4=False, dry_run=True)
    assert vdir / "raea.mp4" in targets and (vdir / "raea.mp4").exists()


@pytest.mark.parametrize("bad_id", ["..", "../work", ""])
def test_cleanup_rejects_traversing_job_ids(tmp_path: Path, bad_id: str) -> None:
    cleaner, _work, _out = _cleaner(tmp_path)
    try:
        cleaner.cleanup_job(bad_id, verified=True, keep_master_mp4=False, dry_run=True)
    except PathSecurityError:
        pass
    assert (tmp_path / "work").exists() and (tmp_path / "output").exists()


# --- longform encoder: a wedged ffmpeg must not block the render forever -----------------------


class _BlockingStdin:
    """stdin whose write blocks (like a full pipe to a hung ffmpeg) until the process is killed."""

    def __init__(self, released: threading.Event) -> None:
        self._released = released

    def write(self, _data: bytes) -> int:
        self._released.wait(timeout=30)
        raise BrokenPipeError("killed")

    def close(self) -> None:
        pass


class _HungProc:
    def __init__(self) -> None:
        self.released = threading.Event()
        self.stdin = _BlockingStdin(self.released)
        self.stderr = io.BytesIO(b"")
        self.killed = False
        self.returncode = None

    def kill(self) -> None:
        self.killed = True
        self.released.set()

    def wait(self, timeout: float | None = None) -> int:
        if not self.killed:
            raise TimeoutError("still running")
        self.returncode = -9
        return -9


def test_encode_times_out_instead_of_blocking_on_a_hung_ffmpeg(tmp_path: Path, monkeypatch) -> None:
    from autoshorts.longform import render

    proc = _HungProc()
    monkeypatch.setattr(render, "render_scene_frames", lambda *a, **k: iter([b"frame"] * 3))
    monkeypatch.setattr(render, "get_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(render, "_ENCODE_STALL_TIMEOUT_SEC", 0.3)
    out = tmp_path / "o.mp4"
    out.write_bytes(b"partial")
    started = time.monotonic()
    with patch.object(render.subprocess, "Popen", return_value=proc):
        with pytest.raises(RuntimeError, match="stalled"):
            render._encode([], "t", tmp_path / "a.wav", out, ())
    assert time.monotonic() - started < 10
    assert proc.killed and not out.exists()
