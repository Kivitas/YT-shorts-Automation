"""Robustness tests for the downloader: retries, classification, cache, concurrency, facade."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from autoshorts.core.exceptions import (
    DownloadError,
    FatalDownloadError,
    InsufficientDiskSpaceError,
    RetryableDownloadError,
)
from autoshorts.media import downloader as dl_mod
from autoshorts.media.downloader import (
    FALLBACK_FORMAT_SPEC,
    Downloader,
    SectionValidation,
    classify_download_error,
    cleanup_stale_temp_files,
    compute_backoff,
    run_command_with_cleanup,
)

URL_A = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
URL_B = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
VALID = SectionValidation(ok=True, reason="ok", has_video=True, has_audio=True, duration=30.0)


def _out_dir_token(cmd: list[str]) -> tuple[Path, str]:
    template = Path(cmd[cmd.index("-o") + 1])
    return template.parent, template.name[len(".tmp_") : -len("_%(ext)s")]


def _ok_run(cmd, **kwargs):
    directory, token = _out_dir_token(cmd)
    (directory / f".tmp_{token}_mp4").write_bytes(b"mp4data")
    return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def no_sleep():
    sleeps: list[float] = []
    with patch.object(dl_mod.time, "sleep", side_effect=sleeps.append):
        yield sleeps


def _downloader(**kw) -> Downloader:
    kw.setdefault("max_retries", 3)
    kw.setdefault("retry_delay_sec", 1.0)
    kw.setdefault("min_free_bytes", 0)
    return Downloader(**kw)


# --- classification ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "severity", "kind"),
    [
        ("ERROR: Private video. Sign in", "fatal", "private"),
        ("ERROR: Video unavailable", "fatal", "removed"),
        ("Sign in to confirm your age", "fatal", "age_gated"),
        ("The uploader has not made this video available in your country", "fatal", "geo_blocked"),
        ("Join this channel to get access to members-only content", "fatal", "members_only"),
        ("HTTP Error 429: Too Many Requests", "rate_limited", "http_429"),
        ("HTTP Error 503: Service Unavailable", "retryable", "network"),
        ("Connection reset by peer", "retryable", "network"),
        ("Requested format is not available", "format", "format_unavailable"),
        ("something strange", "retryable", "unknown"),
    ],
)
def test_classify_download_error(text: str, severity: str, kind: str) -> None:
    got_severity, got_kind, hint = classify_download_error(text)
    assert (got_severity, got_kind) == (severity, kind)
    if severity == "fatal":
        assert hint


def test_classify_timeout_and_missing_binary() -> None:
    assert classify_download_error(subprocess.TimeoutExpired("x", 1))[0] == "retryable"
    assert classify_download_error(FileNotFoundError("yt-dlp"))[:2] == ("fatal", "missing_binary")


# --- backoff ----------------------------------------------------------------------------


def test_backoff_grows_exponentially_with_bounded_jitter() -> None:
    for attempt, nominal in ((1, 1.0), (2, 2.0), (3, 4.0)):
        for _ in range(20):
            assert nominal * 0.75 <= compute_backoff(attempt, 1.0) <= nominal * 1.25
    assert compute_backoff(30, 1.0) <= dl_mod.MAX_BACKOFF_SEC
    assert compute_backoff(1, 1.0, jitter=0.0, rate_limited=True) == 4.0


def test_retry_uses_backoff_without_real_sleep(tmp_path: Path, no_sleep: list[float]) -> None:
    calls = {"n": 0}

    def flaky(cmd, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            return subprocess.CompletedProcess(cmd, 1, "", "HTTP Error 503: Service Unavailable")
        return _ok_run(cmd)

    d = _downloader(section_cache=False)
    with (
        patch("subprocess.run", side_effect=flaky),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        out = d.download_section(URL_A, 0, 30, tmp_path / "cut.mp4")
    assert out.is_file()
    assert calls["n"] == 3
    assert len(no_sleep) == 2 and no_sleep[1] > no_sleep[0] * 1.2
    assert d.last_download_diagnostics["attempt_count"] == 3


def test_rate_limit_waits_longer(tmp_path: Path, no_sleep: list[float]) -> None:
    def limited(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "HTTP Error 429: Too Many Requests")

    d = _downloader(section_cache=False, max_retries=2)
    with patch("subprocess.run", side_effect=limited), pytest.raises(RetryableDownloadError):
        d.download_section(URL_A, 0, 30, tmp_path / "cut.mp4")
    assert no_sleep and no_sleep[0] >= 3.0  # 4x of 1s base, minus jitter


def test_fatal_error_is_not_retried_and_carries_hint(tmp_path: Path, no_sleep: list[float]) -> None:
    calls = {"n": 0}

    def private(cmd, **kwargs):
        calls["n"] += 1
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: Private video")

    d = _downloader(section_cache=False)
    with patch("subprocess.run", side_effect=private), pytest.raises(FatalDownloadError) as ei:
        d.download_section(URL_A, 0, 30, tmp_path / "cut.mp4")
    assert calls["n"] == 1
    assert no_sleep == []
    assert ei.value.reason == "private" and ei.value.hint
    assert isinstance(ei.value, DownloadError)  # backward compatible with old handlers
    assert not list(tmp_path.glob(".tmp_*"))


def test_format_unavailable_switches_to_fallback_selector(
    tmp_path: Path, no_sleep: list[float]
) -> None:
    seen: list[str] = []

    def run(cmd, **kwargs):
        seen.append(cmd[cmd.index("-f") + 1])
        if len(seen) == 1:
            return subprocess.CompletedProcess(cmd, 1, "", "Requested format is not available")
        return _ok_run(cmd)

    d = _downloader(section_cache=False)
    with (
        patch("subprocess.run", side_effect=run),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        d.download_section(URL_A, 0, 30, tmp_path / "cut.mp4")
    assert seen[0] == d.format_spec and seen[1] == FALLBACK_FORMAT_SPEC


def test_command_contains_resume_and_fragment_flags(tmp_path: Path) -> None:
    captured: list[list[str]] = []

    def run(cmd, **kwargs):
        captured.append(list(cmd))
        return _ok_run(cmd)

    d = _downloader(section_cache=False, concurrent_fragments=6)
    with (
        patch("subprocess.run", side_effect=run),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        d.download_section(URL_A, 0, 30, tmp_path / "cut.mp4")
    cmd = captured[0]
    assert "--continue" in cmd
    assert cmd[cmd.index("--concurrent-fragments") + 1] == "6"


# --- disk preflight / stale temp cleanup -------------------------------------------------


def test_disk_space_preflight_blocks_download(tmp_path: Path) -> None:
    d = _downloader(section_cache=False, min_free_bytes=10**15)
    with patch("subprocess.run") as run, pytest.raises(InsufficientDiskSpaceError):
        d.download_section(URL_A, 0, 30, tmp_path / "cut.mp4")
    run.assert_not_called()


def test_stale_temp_cleanup_keeps_fresh_files(tmp_path: Path) -> None:
    import os
    import time

    old = tmp_path / ".tmp_dead_mp4.part"
    old.write_bytes(b"x")
    old_ytdl = tmp_path / "clip.mp4.ytdl"
    old_ytdl.write_bytes(b"x")
    fresh = tmp_path / ".tmp_live_mp4"
    fresh.write_bytes(b"x")
    past = time.time() - 10 * 3600
    for f in (old, old_ytdl):
        os.utime(f, (past, past))
    assert cleanup_stale_temp_files(tmp_path) == 2
    assert fresh.exists() and not old.exists() and not old_ytdl.exists()


# --- section cache ----------------------------------------------------------------------


def test_section_cache_hit_skips_download(tmp_path: Path) -> None:
    runs = {"n": 0}

    def run(cmd, **kwargs):
        runs["n"] += 1
        return _ok_run(cmd)

    d = _downloader()
    with (
        patch("subprocess.run", side_effect=run),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        first = d.download_section(URL_A, 10, 40, tmp_path / "a" / "one.mp4")
        diag: dict = {}
        second = d.download_section(URL_A, 10, 40, tmp_path / "a" / "two.mp4", diag)
    assert runs["n"] == 1
    assert second.read_bytes() == first.read_bytes() == b"mp4data"
    assert diag["cache_hit"] is True and diag["ok"] is True


def test_section_cache_misses_on_different_section_video_or_format(tmp_path: Path) -> None:
    runs = {"n": 0}

    def run(cmd, **kwargs):
        runs["n"] += 1
        return _ok_run(cmd)

    d = _downloader()
    other_fmt = _downloader(format_spec="best")
    with (
        patch("subprocess.run", side_effect=run),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        d.download_section(URL_A, 10, 40, tmp_path / "1.mp4")
        d.download_section(URL_A, 10, 41, tmp_path / "2.mp4")
        d.download_section(URL_B, 10, 40, tmp_path / "3.mp4")
        other_fmt.download_section(URL_A, 10, 40, tmp_path / "4.mp4")
    assert runs["n"] == 4


def test_invalid_cached_file_is_discarded_and_redownloaded(tmp_path: Path) -> None:
    runs = {"n": 0}

    def run(cmd, **kwargs):
        runs["n"] += 1
        return _ok_run(cmd)

    d = _downloader()
    with (
        patch("subprocess.run", side_effect=run),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        d.download_section(URL_A, 0, 30, tmp_path / "1.mp4")
    bad = SectionValidation(ok=False, reason="unreadable")
    with (
        patch("subprocess.run", side_effect=run),
        patch.object(dl_mod, "validate_section_output", side_effect=[bad, VALID]),
    ):
        d.download_section(URL_A, 0, 30, tmp_path / "2.mp4")
    assert runs["n"] == 2


# --- concurrency ------------------------------------------------------------------------


def test_concurrent_downloads_use_unique_temp_names(tmp_path: Path) -> None:
    tokens: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)

    def run(cmd, **kwargs):
        directory, token = _out_dir_token(cmd)
        with lock:
            tokens.append(token)
        try:
            barrier.wait(timeout=5)  # all four are in flight at once in the same directory
        except threading.BrokenBarrierError:
            pass
        (directory / f".tmp_{token}_mp4").write_bytes(token.encode())
        return subprocess.CompletedProcess(cmd, 0, "", "")

    d = _downloader(section_cache=False)
    results: dict[int, bytes] = {}

    def worker(i: int) -> None:
        out = d.download_section(URL_A, i * 10, i * 10 + 5, tmp_path / f"cut{i}.mp4")
        results[i] = out.read_bytes()

    with (
        patch("subprocess.run", side_effect=run),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    assert len(set(tokens)) == 4
    assert sorted(results.values()) == sorted(t.encode() for t in tokens)
    assert not list(tmp_path.glob(".tmp_*"))


def test_identical_concurrent_requests_download_once(tmp_path: Path) -> None:
    runs = {"n": 0}

    def run(cmd, **kwargs):
        runs["n"] += 1
        return _ok_run(cmd)

    d = _downloader()
    with (
        patch("subprocess.run", side_effect=run),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        threads = [
            threading.Thread(target=d.download_section, args=(URL_A, 5, 25, tmp_path / f"v{i}.mp4"))
            for i in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    assert runs["n"] == 1
    assert all((tmp_path / f"v{i}.mp4").is_file() for i in range(3))


# --- timeouts kill process trees --------------------------------------------------------


def test_run_command_none_timeout_is_bounded() -> None:
    captured: dict = {}

    class FakeProc:
        pid = 77
        returncode = 0

        def communicate(self, timeout=None):
            captured["timeout"] = timeout
            return ("", "")

    with patch("subprocess.Popen", return_value=FakeProc()):
        run_command_with_cleanup(["x"], timeout=None)
    assert captured["timeout"] == dl_mod.DEFAULT_COMMAND_TIMEOUT_SEC


def test_timeout_kills_process_tree_and_reaps() -> None:
    class HangingProc:
        pid = 4321
        returncode = None

        def __init__(self) -> None:
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("x", timeout)
            return ("", "")

    with (
        patch("subprocess.Popen", return_value=HangingProc()),
        patch.object(dl_mod, "terminate_process_tree") as kill,
        pytest.raises(subprocess.TimeoutExpired),
    ):
        run_command_with_cleanup(["x"], timeout=1)
    kill.assert_called_once_with(4321)


def test_download_timeout_is_retryable_and_cleans_temp(
    tmp_path: Path, no_sleep: list[float]
) -> None:
    def hang(cmd, **kwargs):
        directory, token = _out_dir_token(cmd)
        (directory / f".tmp_{token}_mp4.part").write_bytes(b"partial")
        raise subprocess.TimeoutExpired(cmd, 1)

    d = _downloader(section_cache=False, max_retries=2)
    with patch("subprocess.run", side_effect=hang), pytest.raises(RetryableDownloadError):
        d.download_section(URL_A, 0, 30, tmp_path / "cut.mp4")
    assert not list(tmp_path.glob(".tmp_*"))
    assert len(no_sleep) == 1


# --- facade -----------------------------------------------------------------------------


def test_ingestion_facade_reexports_same_objects() -> None:
    from autoshorts.ingestion import downloader as facade

    assert facade.Downloader is dl_mod.Downloader
    assert facade.YtDlpDownloader is dl_mod.Downloader
    for name in facade.__all__:
        assert hasattr(facade, name), name
    assert facade.terminate_process_tree is dl_mod.terminate_process_tree
    assert facade.run_command_with_cleanup is dl_mod.run_command_with_cleanup
