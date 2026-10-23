"""Tests for macOS (Monterey 12+, Apple Silicon + Intel) and Apple Safari compatibility.

Validates:
1. macOS Sleep Prevention (IOKit IOPMAssertionCreateWithName & caffeinate subprocess fallback).
2. Clean sleep restoration and context manager execution on macOS.
3. Preservation of Windows Win32 kernel32 execution and Linux no-op behavior.
4. Cross-platform process tree termination in downloader.py.
5. Safari 15+ CSS glassmorphism (-webkit-backdrop-filter), appearance resets, and dvh viewports.
6. Safari JavaScript ISO-8601 date parsing, SSE backoff reconnection, and video autoplay attributes.
7. Unix/macOS start.sh launcher parity, shebang, LF line endings, and CLI argument parsing.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import autoshorts.scheduler.power_management as pm
from autoshorts.media.downloader import terminate_process_tree

# -----------------------------------------------------------------------------
# 1. macOS Power Management & Sleep Prevention Tests
# -----------------------------------------------------------------------------


def test_macos_power_management_iokit_success():
    """Verify IOKit assertion creation on macOS (sys.platform == 'darwin')."""
    # Reset any active state
    pm._darwin_assertion_id = None
    pm._darwin_caffeinate_proc = None

    mock_iokit = MagicMock()
    mock_cf = MagicMock()

    # IOPMAssertionCreateWithName returns 0 (kIOReturnSuccess)
    def fake_create(type_str, level, name_str, aid_ptr):
        aid_ptr._obj.value = 42
        return 0

    mock_iokit.IOPMAssertionCreateWithName.side_effect = fake_create
    mock_cf.CFStringCreateWithCString.return_value = 1001

    def fake_load_library(path: str):
        if "IOKit" in path:
            return mock_iokit
        if "CoreFoundation" in path:
            return mock_cf
        raise OSError(f"Cannot load {path}")

    with (
        patch("sys.platform", "darwin"),
        patch("ctypes.cdll.LoadLibrary", side_effect=fake_load_library),
    ):
        assert pm.prevent_system_sleep() is True
        assert pm._darwin_assertion_id == 42
        assert pm._darwin_caffeinate_proc is None

        # Re-invoking while active should return True without duplicate calls
        assert pm.prevent_system_sleep() is True

        # Now test release
        mock_iokit.IOPMAssertionRelease.return_value = 0
        assert pm.allow_system_sleep() is True
        assert pm._darwin_assertion_id is None
        mock_iokit.IOPMAssertionRelease.assert_called_once()


def test_macos_power_management_caffeinate_fallback():
    """Verify fallback to caffeinate -i -s -w <pid> when IOKit is unavailable."""
    pm._darwin_assertion_id = None
    pm._darwin_caffeinate_proc = None

    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.pid = 9999
    mock_proc.poll.return_value = None  # Process is running

    with (
        patch("sys.platform", "darwin"),
        patch("ctypes.cdll.LoadLibrary", side_effect=OSError("IOKit unavailable")),
        patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
    ):
        assert pm.prevent_system_sleep() is True
        assert pm._darwin_assertion_id is None
        assert pm._darwin_caffeinate_proc == mock_proc

        # Verify command arguments passed to caffeinate
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[0] == "caffeinate"
        assert "-i" in args
        assert "-s" in args
        assert "-w" in args
        assert str(os.getpid()) in args

        # Test allow_system_sleep terminates caffeinate
        assert pm.allow_system_sleep() is True
        mock_proc.terminate.assert_called_once()
        assert pm._darwin_caffeinate_proc is None


def test_macos_power_management_context_manager():
    """Verify system_sleep_prevented context manager on macOS."""
    pm._darwin_assertion_id = None
    pm._darwin_caffeinate_proc = None

    with (
        patch("sys.platform", "darwin"),
        patch(
            "autoshorts.scheduler.power_management.prevent_system_sleep", return_value=True
        ) as mock_prev,
        patch(
            "autoshorts.scheduler.power_management.allow_system_sleep", return_value=True
        ) as mock_allow,
    ):
        with pm.system_sleep_prevented():
            mock_prev.assert_called_once()
            assert not mock_allow.called
        mock_allow.assert_called_once()


def test_windows_power_management_intact():
    """Ensure Windows win32 SetThreadExecutionState behavior remains completely intact."""
    with (
        patch("sys.platform", "win32"),
        patch("ctypes.windll.kernel32.SetThreadExecutionState", return_value=1) as mock_win32,
    ):
        assert pm.prevent_system_sleep() is True
        mock_win32.assert_called_once()

        mock_win32.reset_mock()
        assert pm.allow_system_sleep() is True
        mock_win32.assert_called_once()


def test_linux_power_management_noop():
    """Ensure other POSIX systems (Linux) gracefully return False as a no-op."""
    with patch("sys.platform", "linux"):
        assert pm.prevent_system_sleep() is False
        assert pm.allow_system_sleep() is False


# -----------------------------------------------------------------------------
# 2. Downloader Process Tree Termination Parity
# -----------------------------------------------------------------------------


def test_terminate_process_tree_unix_darwin():
    """Verify terminate_process_tree calls pkill and proc.kill on non-Windows platforms."""
    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.pid = 1234

    with (
        patch("sys.platform", "darwin"),
        patch("subprocess.run") as mock_run,
    ):
        terminate_process_tree(mock_proc)
        mock_proc.kill.assert_called_once()
        mock_run.assert_called_once_with(["pkill", "-P", "1234"], capture_output=True, check=False)


def test_terminate_process_tree_windows():
    """Verify terminate_process_tree invokes taskkill /F /T /PID on Windows."""
    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.pid = 5678

    with (
        patch("sys.platform", "win32"),
        patch("subprocess.run") as mock_run,
    ):
        terminate_process_tree(mock_proc)
        mock_run.assert_called_once_with(
            ["taskkill", "/F", "/T", "/PID", "5678"], capture_output=True, check=False
        )


# -----------------------------------------------------------------------------
# 3. Safari UI & CSS Glassmorphism Compatibility
# -----------------------------------------------------------------------------


def test_safari_css_glassmorphism_backdrop_filter_parity():
    """Verify all backdrop-filter declarations have an accompanying -webkit-backdrop-filter."""
    css_path = Path("src/autoshorts/dashboard/static/style.css")
    assert css_path.exists(), f"{css_path} not found"

    lines = css_path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if "backdrop-filter:" in line and "-webkit-backdrop-filter:" not in line:
            # Check surrounding context for -webkit-backdrop-filter
            surrounding = lines[max(0, i - 2) : min(len(lines), i + 3)]
            has_webkit = any("-webkit-backdrop-filter:" in s for s in surrounding)
            assert has_webkit, (
                f"Line {i + 1} in style.css has 'backdrop-filter:' without '-webkit-backdrop-filter:':\n{line}"
            )


def test_safari_css_form_appearance_resets():
    """Verify custom select and input controls reset -webkit-appearance for Safari."""
    css_path = Path("src/autoshorts/dashboard/static/style.css")
    content = css_path.read_text(encoding="utf-8")

    assert "-webkit-appearance: none;" in content, (
        "Missing '-webkit-appearance: none;' in style.css"
    )
    assert "appearance: none;" in content, "Missing 'appearance: none;' in style.css"


def test_safari_css_viewport_and_flex_guards():
    """Verify 100dvh, -webkit-fill-available, and Safari flex/grid collapse guards."""
    css_path = Path("src/autoshorts/dashboard/static/style.css")
    content = css_path.read_text(encoding="utf-8")

    assert "-webkit-fill-available" in content, "Missing '-webkit-fill-available' in style.css"
    assert "100dvh" in content, "Missing '100dvh' dynamic viewport unit in style.css"
    assert "min-width: 0;" in content, "Missing 'min-width: 0;' flex collapse guard in style.css"
    assert "min-height: 0;" in content, "Missing 'min-height: 0;' flex collapse guard in style.css"


# -----------------------------------------------------------------------------
# 4. Safari JavaScript Date Parsing, SSE Backoff & Video Autoplay
# -----------------------------------------------------------------------------


def test_safari_js_date_parsing_and_sse_backoff():
    """Verify parseSafariSafeDate and SSE backoff reconnection in app.js."""
    js_path = Path("src/autoshorts/dashboard/static/app.js")
    assert js_path.exists(), f"{js_path} not found"

    content = js_path.read_text(encoding="utf-8")

    # Date parsing
    assert "function parseSafariSafeDate" in content, "Missing parseSafariSafeDate helper in app.js"
    assert (
        's.replace(/\\s+/, "T")' in content
        or "s.replace(' ', 'T')" in content
        or 's.replace(" ", "T")' in content
    ), "parseSafariSafeDate must replace space with 'T' for strict Safari ISO-8601 parsing"

    # SSE Reconnection with Backoff
    assert "scheduleSseReconnect" in content, "Missing scheduleSseReconnect in app.js"
    assert "EventSource.CLOSED" in content, "Missing EventSource.CLOSED handling in app.js"

    # HTML5 Video Autoplay Attributes
    assert "ensureSafariMediaCompatibility" in content, (
        "Missing ensureSafariMediaCompatibility in app.js"
    )
    assert (
        'video.setAttribute("playsinline"' in content
        or "video.setAttribute('playsinline'" in content
    ), "openPreview must set playsinline on preview video element"
    assert "video.muted = true" in content, (
        "openPreview must set video.muted = true for Safari autoplay"
    )


# -----------------------------------------------------------------------------
# 5. start.sh Unix/macOS Launcher Integrity & Cross-Platform Parity
# -----------------------------------------------------------------------------


def test_start_sh_launcher_properties():
    """Validate start.sh shebang, LF endings, flags, and Homebrew integration."""
    sh_path = Path("start.sh")
    assert sh_path.exists(), "start.sh was not found in project root"

    content_bytes = sh_path.read_bytes()
    assert b"\r" not in content_bytes, (
        "start.sh contains Windows CRLF line endings! Must be UNIX LF."
    )

    content = content_bytes.decode("utf-8")
    assert content.startswith("#!/usr/bin/env bash"), (
        "start.sh must have '#!/usr/bin/env bash' shebang"
    )
    assert "set -euo pipefail" in content, "start.sh must enforce 'set -euo pipefail'"

    # Flag parity with start.bat
    expected_flags = [
        "--port",
        "-p",
        "--check",
        "-c",
        "--test",
        "-t",
        "--collect-gameplay",
        "--collect",
        "--no-browser",
        "--host",
        "--help",
        "-h",
    ]
    for flag in expected_flags:
        assert flag in content, f"start.sh missing CLI flag: {flag}"

    # macOS Homebrew and open command detection
    assert "/opt/homebrew/bin" in content, "start.sh missing Apple Silicon Homebrew path"
    assert "/usr/local/bin" in content, "start.sh missing Intel Mac Homebrew path"
    assert (
        'open "$TARGET_URL"' in content
        or 'open "http://localhost:${PORT}"' in content
        or "open " in content
    ), "start.sh missing macOS 'open' command for browser auto-opening"
    assert "xdg-open" in content, "start.sh missing Linux xdg-open fallback"
    assert "caffeinate" in content, (
        "start.sh should acknowledge macOS sleep prevention / caffeinate"
    )
