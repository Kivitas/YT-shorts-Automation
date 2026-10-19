"""Scheduler package for AutoShorts."""

from autoshorts.scheduler.power_management import (
    allow_system_sleep,
    prevent_system_sleep,
    system_sleep_prevented,
)
from autoshorts.scheduler.scheduler import (
    SchedulerCapError,
    SchedulerQuotaError,
    SchedulerRateLimitError,
    SpreadScheduler,
)
from autoshorts.scheduler.slot_generator import SlotGenerator

__all__ = [
    "SchedulerCapError",
    "SchedulerQuotaError",
    "SchedulerRateLimitError",
    "SlotGenerator",
    "SpreadScheduler",
    "allow_system_sleep",
    "prevent_system_sleep",
    "system_sleep_prevented",
]
