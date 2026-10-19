"""Slot generator for daily upload pacing across active daytime window.

Generates scheduled upload timestamps with non-fixed uniform random gaps
between min_gap_minutes and max_gap_minutes (PLAN.md section 8.2).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta

from autoshorts.core.config import ActiveWindowConfig, SchedulerConfig


class SlotGenerator:
    """Computes jittered upload slots within active daytime windows."""

    def __init__(
        self,
        config: SchedulerConfig | None = None,
        min_gap_minutes: int | None = None,
        max_gap_minutes: int | None = None,
        active_window: ActiveWindowConfig | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize slot generator with gap bounds and active window.

        Args:
            config: Full SchedulerConfig (optional).
            min_gap_minutes: Lower bound for uniform random gap in minutes.
            max_gap_minutes: Upper bound for uniform random gap in minutes.
            active_window: ActiveWindowConfig defining daily start and end times.
            rng: Injectable random.Random instance for deterministic testing.
            clock: Injectable clock function returning datetime.
        """
        cfg = config or SchedulerConfig()
        self.min_gap_minutes = (
            min_gap_minutes if min_gap_minutes is not None else cfg.min_gap_minutes
        )
        self.max_gap_minutes = (
            max_gap_minutes if max_gap_minutes is not None else cfg.max_gap_minutes
        )
        self.active_window = active_window or cfg.active_window
        self.rng = rng or random.Random()
        self.clock = clock or (lambda: datetime.now(UTC))

        # Parse window boundary times
        self.start_time = self._parse_time_str(self.active_window.start)
        self.end_time = self._parse_time_str(self.active_window.end)

    @staticmethod
    def _parse_time_str(time_str: str) -> time:
        """Parse HH:MM string to datetime.time."""
        parts = [int(p) for p in time_str.split(":")]
        return time(hour=parts[0], minute=parts[1])

    def is_within_active_window(self, dt: datetime | None = None) -> bool:
        """Return True if given datetime (or clock now) falls inside active window."""
        target = dt or self.clock()
        target_time = target.time()
        return self.start_time <= target_time <= self.end_time

    def get_window_bounds_for_date(self, target_date: datetime) -> tuple[datetime, datetime]:
        """Return (window_start, window_end) datetimes for the given date."""
        tz = target_date.tzinfo
        w_start = target_date.replace(
            hour=self.start_time.hour,
            minute=self.start_time.minute,
            second=0,
            microsecond=0,
            tzinfo=tz,
        )
        w_end = target_date.replace(
            hour=self.end_time.hour,
            minute=self.end_time.minute,
            second=59,
            microsecond=999999,
            tzinfo=tz,
        )
        return w_start, w_end

    def next_slot_after(self, after: datetime) -> datetime:
        """Compute the next valid slot strictly after the given timestamp.

        Picks a uniform random gap in [min_gap_minutes, max_gap_minutes].
        If resulting time falls past the active window, moves to the next day's window start.
        """
        gap_sec = self.rng.uniform(self.min_gap_minutes * 60, self.max_gap_minutes * 60)
        candidate = after + timedelta(seconds=gap_sec)

        w_start, w_end = self.get_window_bounds_for_date(candidate)

        if candidate < w_start:
            # Before today's window start: align to window start plus a small random gap
            first_gap_sec = self.rng.uniform(self.min_gap_minutes * 60, self.max_gap_minutes * 60)
            return w_start + timedelta(seconds=first_gap_sec)
        elif candidate > w_end:
            # Past today's window end: rollover to tomorrow's window start plus random gap
            tomorrow = candidate + timedelta(days=1)
            next_start, _ = self.get_window_bounds_for_date(tomorrow)
            first_gap_sec = self.rng.uniform(self.min_gap_minutes * 60, self.max_gap_minutes * 60)
            return next_start + timedelta(seconds=first_gap_sec)
        else:
            return candidate

    def generate_slots(
        self,
        count: int,
        start_time: datetime | None = None,
        respect_daily_boundary: bool = False,
    ) -> list[datetime]:
        """Generate a series of upload slots with uniform random gaps.

        Args:
            count: Number of slots to schedule.
            start_time: Starting anchor datetime (defaults to clock now).
            respect_daily_boundary: If True, stop when current day's active window ends.

        Returns:
            List of sorted datetime objects representing upload slots.
        """
        if count <= 0:
            return []

        anchor = start_time or self.clock()
        slots: list[datetime] = []
        current = anchor

        for _ in range(count):
            next_slot = self.next_slot_after(current)
            if respect_daily_boundary:
                _, w_end = self.get_window_bounds_for_date(anchor)
                if next_slot > w_end:
                    break
            slots.append(next_slot)
            current = next_slot

        return slots
