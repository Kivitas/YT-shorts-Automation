"""Windows Power Management and Sleep Prevention during Active Windows.

Asserts Win32 kernel32.SetThreadExecutionState to prevent idle sleep/hibernation
while upload slots are pending in the active daytime window (PLAN.md section 8.2).
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Win32 Execution State Flags
ES_SYSTEM_REQUIRED: int = 0x00000001
ES_DISPLAY_REQUIRED: int = 0x00000002
ES_AWAYMODE_REQUIRED: int = 0x00000040
ES_CONTINUOUS: int = 0x80000000


def prevent_system_sleep() -> bool:
    """Request Windows to prevent entering idle system sleep or hibernation.

    Returns:
        True if Win32 API call succeeded, False on non-Windows or failure.
    """
    if sys.platform != "win32":
        logger.debug("Non-Windows platform; sleep prevention is a no-op.")
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
        True if Win32 API call succeeded, False on non-Windows or failure.
    """
    if sys.platform != "win32":
        logger.debug("Non-Windows platform; sleep restoration is a no-op.")
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
