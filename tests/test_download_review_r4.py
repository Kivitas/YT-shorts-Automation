"""Review round 4: downloader parallelism, cache bounds, classification and argv safety."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from autoshorts.core.exceptions import DownloadError, FatalDownloadError, RetryableDownloadError
from autoshorts.media import downloader as dl_mod
from autoshorts.media.downloader import (
    Downloader,
    SectionValidation,
    classify_download_error,
    cleanup_stale_temp_files,
)

URL_A = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
VALID = SectionValidation(ok=True, reason="ok", has_video=True, has_audio=True, duration=30.0)


def _ok_run(cmd, **kwargs):
    template = Path(cmd[cmd.index("-o") + 1])
    token = template.name[len(".tmp_") : -len("_%(ext)s")]
    (template.parent / f".tmp_{token}_mp4").write_bytes(b"mp4data")
    return subprocess.CompletedProcess(cmd, 0, "", "")


def _dl(**kw) -> Downloader:
    kw.setdefault("min_free_bytes", 0)
    kw.setdefault("retry_delay_sec", 0.1)
    return Downloader(**kw)


# --- classification -----------------------------------------------------------------------


def test_soft_block_video_unavailable_is_not_fatal() -> None:
    text = "ERROR: [youtube] abc: Video unavailable. This content isn't available, try again later."
    severity, _kind, _hint = classify_download_error(text)
    assert severity in ("rate_limited", "retryable")


def test_genuinely_removed_video_is_still_fatal() -> None:
    assert classify_download_error("ERROR: [youtube] abc: Video unavailable")[0] == "fatal"


def test_ssl_word_boundary_does_not_match_inside_words() -> None:
    # "classless" contains "ssl" case-insensitively; it is not a network SSL error
    assert classify_download_error("some classless failure")[1] == "unknown"
    assert classify_download_error("[SSL: CERTIFICATE_VERIFY_FAILED]")[1] == "network"


# --- argv safety ------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["--exec", "-x", "chrome\nfoo"])
def test_cookies_spec_cannot_inject_options(bad: str) -> None:
    with pytest.raises(ValueError):
        _dl(cookies_from_browser=bad)


def test_cookies_spec_valid_values_pass() -> None:
    assert _dl(cookies_from_browser="chrome").cookies_from_browser == "chrome"
    assert _dl(cookies_from_browser="firefox:default").cookies_from_browser == "firefox:default"


def test_infinite_section_is_a_download_error_not_an_overflow(tmp_path: Path) -> None:
    d = _dl()
    with pytest.raises(DownloadError):
        d.download_section(URL_A, 0, float("inf"), tmp_path / "x.mp4")


# --- key lock registry --------------------------------------------------------------------


def test_key_lock_registry_does_not_grow() -> None:
    for i in range(50):
        with dl_mod._key_lock(f"k{i}"):
            pass
    assert dl_mod._KEY_LOCKS == {}


def test_key_lock_still_serialises_same_key() -> None:
    order: list[str] = []

    def worker(tag: str) -> None:
        with dl_mod._key_lock("same"):
            order.append(f"{tag}-in")
            time.sleep(0.05)
            order.append(f"{tag}-out")

    threads = [threading.Thread(target=worker, args=(t,)) for t in "ab"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert order[0][0] == order[1][0] and order[2][0] == order[3][0]
    assert dl_mod._KEY_LOCKS == {}


# --- cache size cap / LRU -------------------------------------------------------------------


def _entry(directory: Path, name: str, size: int, age_sec: float) -> Path:
    path = directory / name
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_sec
    os.utime(path, (stamp, stamp))
    return path


def test_prune_cache_evicts_least_recently_used_to_fit_cap(tmp_path: Path) -> None:
    old = _entry(tmp_path, "old.mp4", 100, 300)
    mid = _entry(tmp_path, "mid.mp4", 100, 200)
    new = _entry(tmp_path, "new.mp4", 100, 100)
    dl_mod._prune_cache(tmp_path, max_bytes=150)
    assert new.exists() and not old.exists() and not mid.exists()


def test_prune_cache_never_removes_the_entry_just_stored(tmp_path: Path) -> None:
    keep = _entry(tmp_path, "keep.mp4", 500, 900)
    _entry(tmp_path, "other.mp4", 10, 10)
    dl_mod._prune_cache(tmp_path, max_bytes=100, keep=keep)
    assert keep.exists()


def test_cache_hit_refreshes_recency(tmp_path: Path) -> None:
    d = _dl(cache_max_bytes=None)
    with (
        patch("subprocess.run", side_effect=_ok_run),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        d.download_section(URL_A, 10, 40, tmp_path / "a" / "one.mp4")
        cached = next((tmp_path / "a" / ".section_cache").glob("*.mp4"))
        stamp = time.time() - 3 * 24 * 3600
        os.utime(cached, (stamp, stamp))
        d.download_section(URL_A, 10, 40, tmp_path / "a" / "two.mp4")
    assert cached.stat().st_mtime > stamp + 3600


def test_store_in_cache_applies_size_cap(tmp_path: Path) -> None:
    d = _dl(cache_max_bytes=10)
    with (
        patch("subprocess.run", side_effect=_ok_run),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        d.download_section(URL_A, 10, 40, tmp_path / "a" / "one.mp4")
        d.download_section(URL_A, 50, 80, tmp_path / "a" / "two.mp4")
    cached = list((tmp_path / "a" / ".section_cache").glob("*.mp4"))
    assert len(cached) == 1  # the older entry was evicted; the one just stored is kept


# --- stale temp cleanup never touches a live download ----------------------------------------


def test_stale_cleanup_skips_files_of_an_active_download(tmp_path: Path) -> None:
    live = _entry(tmp_path, ".tmp_livetok1_mp4.part", 5, 5 * 3600)
    dead = _entry(tmp_path, ".tmp_deadtok2_mp4.part", 5, 5 * 3600)
    with dl_mod._ACTIVE_TOKENS_GUARD:
        dl_mod._ACTIVE_TEMP_TOKENS.add("livetok1")
    try:
        removed = cleanup_stale_temp_files(tmp_path)
    finally:
        with dl_mod._ACTIVE_TOKENS_GUARD:
            dl_mod._ACTIVE_TEMP_TOKENS.discard("livetok1")
    assert removed == 1 and live.exists() and not dead.exists()


def test_active_token_is_released_after_download(tmp_path: Path) -> None:
    d = _dl(section_cache=False)
    with (
        patch("subprocess.run", side_effect=_ok_run),
        patch.object(dl_mod, "validate_section_output", return_value=VALID),
    ):
        d.download_section(URL_A, 10, 40, tmp_path / "o.mp4")
    assert dl_mod._ACTIVE_TEMP_TOKENS == set()


# --- audio-only error types ---------------------------------------------------------------------


def test_audio_only_fatal_error_is_typed_fatal(tmp_path: Path) -> None:
    d = _dl()

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: Private video. Sign in")

    with patch("subprocess.run", side_effect=run):
        with pytest.raises(FatalDownloadError) as err:
            d.download_audio_only(URL_A, tmp_path / "a.m4a")
    assert err.value.reason == "private"


def test_audio_only_exhausted_retries_is_retryable(tmp_path: Path) -> None:
    d = _dl(max_retries=2)

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "HTTP Error 503: Service Unavailable")

    with patch("subprocess.run", side_effect=run), patch.object(dl_mod.time, "sleep"):
        with pytest.raises(RetryableDownloadError):
            d.download_audio_only(URL_A, tmp_path / "a.m4a")
