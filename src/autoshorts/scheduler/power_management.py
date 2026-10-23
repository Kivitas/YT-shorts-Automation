"""Cross-platform Power Management and Sleep Prevention during Active Windows.

Asserts Win32 kernel32.SetThreadExecutionState on Windows, or
IOKit IOPMAssertionCreateWithName / caffeinate on macOS,
to prevent idle sleep/hibernation while upload slots are pending in the active daytime window.
"""

from __future__ import annotations

import atexit
import ctypes
import logging
import os
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Win32 Execution State Flags
ES_SYSTEM_REQUIRED: int = 0x00000001
ES_DISPLAY_REQUIRED: int = 0x00000002
ES_AWAYMODE_REQUIRED: int = 0x00000040
ES_CONTINUOUS: int = 0x80000000

# macOS (Darwin) assertion state
_darwin_assertion_id: int | None = None
_darwin_caffeinate_proc: subprocess.Popen[bytes] | subprocess.Popen[str] | None = None


def _prevent_system_sleep_darwin() -> bool:
    """Prevent sleep on macOS using IOKit assertions or caffeinate subprocess."""
    global _darwin_assertion_id, _darwin_caffeinate_proc

    if _darwin_assertion_id is not None or (
        _darwin_caffeinate_proc is not None and _darwin_caffeinate_proc.poll() is None
    ):
        logger.debug("macOS sleep prevention is already active.")
        return True

    # 1. Attempt IOKit IOPMAssertionCreateWithName via ctypes
    try:
        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        k_cf_string_encoding_utf8 = 0x08000100
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
        ]
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        assertion_type = cf.CFStringCreateWithCString(
            None, b"PreventUserIdleSystemSleep", k_cf_string_encoding_utf8
        )
        assertion_name = cf.CFStringCreateWithCString(
            None, b"AutoShorts active processing", k_cf_string_encoding_utf8
        )

        iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
        iokit.IOPMAssertionCreateWithName.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]

        aid = ctypes.c_uint32(0)
        # 255 is kIOPMAssertionLevelOn
        ret = iokit.IOPMAssertionCreateWithName(
            assertion_type, 255, assertion_name, ctypes.byref(aid)
        )
        cf.CFRelease(assertion_type)
        cf.CFRelease(assertion_name)

        if ret == 0:
            _darwin_assertion_id = int(aid.value)
            logger.debug(
                "macOS sleep prevention activated via IOKit (assertion ID: %s)",
                _darwin_assertion_id,
            )
            return True
        logger.warning("IOKit IOPMAssertionCreateWithName returned non-zero error code %d", ret)
    except Exception as exc:
        logger.debug("IOKit assertion unavailable or failed (%s); falling back to caffeinate", exc)

    # 2. Fallback to `caffeinate -i -s -w <pid>`
    try:
        proc = subprocess.Popen(
            ["caffeinate", "-i", "-s", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if proc.poll() is None:
            _darwin_caffeinate_proc = proc
            logger.debug("macOS sleep prevention activated via caffeinate (PID: %s)", proc.pid)
            return True
        logger.warning("caffeinate process exited immediately.")
        return False
    except Exception as exc:
        logger.warning("Failed to spawn caffeinate process on macOS: %s", exc)
        return False


def _allow_system_sleep_darwin() -> bool:
    """Release macOS sleep prevention assertions or terminate caffeinate."""
    global _darwin_assertion_id, _darwin_caffeinate_proc
    success = True

    if _darwin_assertion_id is not None:
        try:
            iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
            iokit.IOPMAssertionRelease.restype = ctypes.c_int32
            iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
            ret = iokit.IOPMAssertionRelease(ctypes.c_uint32(_darwin_assertion_id))
            if ret == 0:
                logger.debug(
                    "macOS IOKit sleep assertion %s released successfully.",
                    _darwin_assertion_id,
                )
            else:
                logger.warning("IOKit IOPMAssertionRelease returned error code %d", ret)
                success = False
        except Exception as exc:
            logger.warning("Failed to release IOKit sleep assertion: %s", exc)
            success = False
        finally:
            _darwin_assertion_id = None

    if _darwin_caffeinate_proc is not None:
        try:
            if _darwin_caffeinate_proc.poll() is None:
                _darwin_caffeinate_proc.terminate()
                try:
                    _darwin_caffeinate_proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    _darwin_caffeinate_proc.kill()
            logger.debug("macOS caffeinate process terminated.")
        except Exception as exc:
            logger.warning("Failed to terminate caffeinate process: %s", exc)
            success = False
        finally:
            _darwin_caffeinate_proc = None

    return success


# Register exit handler to ensure caffeinate/assertions are freed
atexit.register(_allow_system_sleep_darwin)


def prevent_system_sleep() -> bool:
    """Request OS to prevent entering idle system sleep or hibernation.

    Returns:
        True if Win32 / macOS API call succeeded, False on other platforms or failure.
    """
    if sys.platform == "darwin":
        return _prevent_system_sleep_darwin()

    if sys.platform != "win32":
        logger.debug("Non-Windows/macOS platform; sleep prevention is a no-op.")
        return False

    try:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        if result == 0:
            logger.warning("SetThreadExecutionState returned 0 (failed).")
            return False
        logger.debug("Windows sleep prevention activated (SYSTEM_REQUIRED | AWAYMODE).")
        return True
    except Exception as e:
        logger.warning(f"Failed to invoke Win32 SetThreadExecutionState: {e}")
        return False


def allow_system_sleep() -> bool:
    """Clear sleep prevention flags, restoring normal OS idle sleep behavior.

    Returns:
        True if Win32 / macOS API call succeeded, False on other platforms or failure.
    """
    if sys.platform == "darwin":
        return _allow_system_sleep_darwin()

    if sys.platform != "win32":
        logger.debug("Non-Windows/macOS platform; sleep restoration is a no-op.")
        return False

    try:
        flags = ES_CONTINUOUS
        result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        if result == 0:
            logger.warning("SetThreadExecutionState to restore sleep returned 0.")
            return False
        logger.debug("Windows sleep prevention cleared; normal sleep allowed.")
        return True
    except Exception as e:
        logger.warning(f"Failed to restore Win32 SetThreadExecutionState: {e}")
        return False


@contextmanager
def system_sleep_prevented() -> Generator[None, None, None]:
    """Context manager ensuring sleep is prevented during execution and restored on exit."""
    prevent_system_sleep()
    try:
        yield
    finally:
        allow_system_sleep()
