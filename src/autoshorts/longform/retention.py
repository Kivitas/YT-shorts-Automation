"""Pure retention helpers: pattern-interrupt cadence and hook-window checks.

Nothing here renders or touches the pipeline. The functions take plain numbers and
text so they can be unit-tested and wired in later.
"""

from __future__ import annotations

import math
import re

PATTERN_INTERRUPT_MIN_SEC = 2.5
PATTERN_INTERRUPT_MAX_SEC = 3.5
DEFAULT_PATTERN_INTERRUPT_SEC = 3.0
HOOK_WINDOW_SEC = 3.0
DEFAULT_WORDS_PER_SECOND = 2.6


def pattern_interrupt_times(
    duration_sec: float,
    cadence: float = DEFAULT_PATTERN_INTERRUPT_SEC,
    *,
    start: float | None = None,
) -> list[float]:
    """Return zoom/SFX/background-shift times, one every ``cadence`` seconds.

    The first event lands at ``start`` (default: one cadence in, so the hook owns
    0-cadence). Times are strictly below ``duration_sec``. ``cadence`` must lie in
    2.5-3.5 seconds.
    """
    if not (PATTERN_INTERRUPT_MIN_SEC <= cadence <= PATTERN_INTERRUPT_MAX_SEC):
        raise ValueError(
            f"cadence must be between {PATTERN_INTERRUPT_MIN_SEC} and "
            f"{PATTERN_INTERRUPT_MAX_SEC} seconds, got {cadence}"
        )
    if not math.isfinite(duration_sec) or duration_sec <= 0:
        return []
    first = cadence if start is None else float(start)
    if first < 0:
        raise ValueError("start must be non-negative")
    times: list[float] = []
    t = first
    while t < duration_sec:
        times.append(round(t, 3))
        t += cadence
    return times


def _normalise_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def hook_in_first_seconds(
    narration: str,
    hook: str,
    *,
    window_sec: float = HOOK_WINDOW_SEC,
    words_per_second: float = DEFAULT_WORDS_PER_SECOND,
    probe_words: int = 3,
) -> bool:
    """True when the first ``probe_words`` words of ``hook`` are spoken within the window.

    The spoken window is estimated as ``window_sec * words_per_second`` words from the
    start of the narration.
    """
    hook_words = _normalise_words(hook)[: max(1, probe_words)]
    if not hook_words:
        return False
    spoken_budget = max(1, int(window_sec * words_per_second))
    spoken = _normalise_words(narration)[:spoken_budget]
    needle = hook_words
    size = len(needle)
    return any(spoken[i : i + size] == needle for i in range(len(spoken) - size + 1))


__all__ = [
    "DEFAULT_PATTERN_INTERRUPT_SEC",
    "HOOK_WINDOW_SEC",
    "PATTERN_INTERRUPT_MAX_SEC",
    "PATTERN_INTERRUPT_MIN_SEC",
    "hook_in_first_seconds",
    "pattern_interrupt_times",
]
