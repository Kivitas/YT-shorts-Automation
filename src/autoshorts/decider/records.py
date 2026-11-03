"""Decision records: one structured, logged entry per pipeline decision.

Every decision made by the decider/decision layer can be recorded with
``record_decision``. Each record is logged at INFO in a stable format::

    DECISION job=<job_id> name=<name> value=<value> source=<source> reason=<reason>

and kept in a bounded in-memory list (last ``MAX_RECORDS`` entries) that
``decisions_for_job`` filters by job id.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger("autoshorts.decider.records")

DecisionSource = Literal["llm", "heuristic", "config", "default"]

MAX_RECORDS = 500
DECISION_LOG_FORMAT = "DECISION job=%s name=%s value=%s source=%s reason=%s"


@dataclass(frozen=True)
class DecisionRecord:
    """A single recorded decision and why it was taken."""

    job_id: str | None
    name: str
    value: str
    reason: str
    source: DecisionSource


_records: deque[DecisionRecord] = deque(maxlen=MAX_RECORDS)
_lock = threading.Lock()


def record_decision(record: DecisionRecord) -> None:
    """Log a decision at INFO and append it to the bounded in-memory list."""
    logger.info(
        DECISION_LOG_FORMAT,
        record.job_id,
        record.name,
        record.value,
        record.source,
        record.reason,
    )
    with _lock:
        _records.append(record)


def decisions_for_job(job_id: str | None) -> list[DecisionRecord]:
    """Return recorded decisions whose job_id equals ``job_id``, oldest first."""
    with _lock:
        return [r for r in _records if r.job_id == job_id]


__all__ = [
    "DECISION_LOG_FORMAT",
    "MAX_RECORDS",
    "DecisionRecord",
    "DecisionSource",
    "decisions_for_job",
    "record_decision",
]
