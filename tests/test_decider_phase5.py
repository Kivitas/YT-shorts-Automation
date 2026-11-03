"""Tests for decision records (decider.records) and their wiring."""

from __future__ import annotations

import logging

import pytest

from autoshorts.decider import records
from autoshorts.decider.format_classifier import classify_format
from autoshorts.decider.records import (
    MAX_RECORDS,
    DecisionRecord,
    decisions_for_job,
    record_decision,
)


@pytest.fixture(autouse=True)
def _clear_records():
    records._records.clear()
    yield
    records._records.clear()


def _rec(job_id: str | None, name: str = "background_category", value: str = "A") -> DecisionRecord:
    return DecisionRecord(job_id=job_id, name=name, value=value, reason="test", source="heuristic")


def test_record_decision_appends_and_filters_by_job() -> None:
    record_decision(_rec("job-1", value="A"))
    record_decision(_rec("job-2", value="B"))
    record_decision(_rec("job-1", name="clip_window", value="1.00-40.00"))

    job1 = decisions_for_job("job-1")
    assert [r.name for r in job1] == ["background_category", "clip_window"]
    assert all(r.job_id == "job-1" for r in job1)

    job2 = decisions_for_job("job-2")
    assert [r.value for r in job2] == ["B"]

    assert decisions_for_job("missing") == []


def test_decisions_for_job_none_matches_records_without_job_id() -> None:
    record_decision(_rec(None, name="format_class", value="ranking"))
    record_decision(_rec("job-9"))

    unscoped = decisions_for_job(None)
    assert len(unscoped) == 1
    assert unscoped[0].name == "format_class"


def test_bounded_list_keeps_at_most_500() -> None:
    assert MAX_RECORDS == 500
    for i in range(650):
        record_decision(_rec("job-bounded", value=str(i)))

    assert len(records._records) == 500
    kept = decisions_for_job("job-bounded")
    assert len(kept) == 500
    # Oldest entries were evicted; the newest remain.
    assert kept[0].value == "150"
    assert kept[-1].value == "649"


def test_log_line_format_is_stable(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="autoshorts.decider.records"):
        record_decision(
            DecisionRecord(
                job_id="job-42",
                name="layout_mode",
                value="split_50_50",
                reason="config default",
                source="config",
            )
        )

    messages = [r.getMessage() for r in caplog.records if r.name == "autoshorts.decider.records"]
    assert messages == [
        "DECISION job=job-42 name=layout_mode value=split_50_50 source=config reason=config default"
    ]
    assert caplog.records[-1].levelno == logging.INFO


def test_format_classifier_ranking_is_recorded() -> None:
    text = "Number 3 is the worst. Number 2 is next. Number 1 is the best."
    decision = classify_format(text, duration_sec=40.0)

    assert decision.kind == "ranking"
    recorded = [r for r in records._records if r.name == "format_class"]
    assert len(recorded) == 1
    assert recorded[0].value == "ranking"
    assert recorded[0].source == "heuristic"


def test_classify_format_job_id_is_retrievable() -> None:
    text = "Number 3 is the worst. Number 2 is next. Number 1 is the best."
    decision = classify_format(text, duration_sec=40.0, job_id="job_x")

    assert decision.kind == "ranking"
    job_records = decisions_for_job("job_x")
    assert [r.name for r in job_records] == ["format_class"]
    assert job_records[0].job_id == "job_x"
    assert decisions_for_job(None) == []
