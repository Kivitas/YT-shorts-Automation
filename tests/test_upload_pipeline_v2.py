"""Tests for longform chapters, description caps, tag limits, title hashtags and publish validation.

All uploaders are fakes; nothing calls YouTube or any other network API.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoshorts.longform.publish as pub
from autoshorts.longform.types import LongformResult
from autoshorts.uploader.description_builder import (
    MAX_DESCRIPTION_BYTES,
    MAX_TAGS_LONGFORM,
    MAX_TAGS_SHORTS,
    build_chapter_lines,
    build_longform_description,
    dedupe_tags,
    fit_longform_description,
)
from autoshorts.uploader.metadata_builder import cap_tags, generate_title_variants

SAFETY = "General self-improvement ideas only. This is not medical or mental health advice."


def _nbytes(text: str) -> int:
    return len(text.encode("utf-8"))


# ---- chapters -------------------------------------------------------------------------


def test_chapters_are_sorted_and_first_starts_at_zero():
    lines = build_chapter_lines([(65, "Late"), (0, "Intro"), (30, "Early")])
    # 30 is only 30s after 0 so it is kept; order is by start time.
    assert lines == ["00:00 Intro", "00:30 Early", "01:05 Late"]


def test_first_chapter_is_pinned_to_zero_even_if_it_starts_later():
    lines = build_chapter_lines([(12, "Opening"), (40, "Middle")])
    assert lines[0] == "00:00 Opening"
    assert lines[1] == "00:40 Middle"


def test_short_sections_closer_than_ten_seconds_are_dropped():
    lines = build_chapter_lines([(0, "A"), (4, "B"), (9.9, "C"), (10, "D"), (15, "E"), (25, "F")])
    assert lines == ["00:00 A", "00:10 D", "00:25 F"]


def test_invalid_and_empty_sections_are_skipped():
    lines = build_chapter_lines([(0, "Keep"), ("nan?", "Bad"), (float("inf"), "Inf"), (30, "  ")])
    assert lines == ["00:00 Keep"]


# ---- description assembly and caps ------------------------------------------------------


def test_longform_description_adds_chapters_and_sources_footer():
    desc = build_longform_description(
        "Body hook.",
        sections=[(0, "Intro"), (20, "Core idea")],
        safety_note=SAFETY,
    )
    assert desc.startswith("Body hook.")
    assert "Chapters:\n00:00 Intro\n00:20 Core idea" in desc
    assert desc.endswith(f"Sources and disclaimer:\n{SAFETY}")


def test_footer_not_duplicated_when_safety_note_already_in_body():
    body = f"Hook.\n\n1. Point\n\n{SAFETY}\n\n#shorts"
    desc = build_longform_description(body, safety_note=SAFETY)
    assert desc.count(SAFETY) == 1
    assert "Sources and disclaimer:" not in desc


def test_no_chapters_or_safety_note_leaves_body_unchanged():
    assert build_longform_description("Plain body") == "Plain body"


def test_description_cap_truncates_chapters_before_disclaimer():
    sections = [(i * 15, f"Section heading number {i} with some words") for i in range(200)]
    body = "Hook line for the video. " * 5
    desc = build_longform_description(body, sections=sections, safety_note=SAFETY)

    assert _nbytes(desc) <= MAX_DESCRIPTION_BYTES
    assert desc.endswith(f"Sources and disclaimer:\n{SAFETY}")
    assert "Chapters:" in desc  # some chapters survive the cut
    assert "Section heading number 199" not in desc  # the tail chapters were removed
    assert desc.startswith("Hook line for the video.")  # the body was not shortened


def test_cap_drops_chapters_entirely_before_touching_body():
    body = "Body text stays whole. " * 3
    sections = [(i * 12, "X" * 80) for i in range(80)]
    desc = fit_longform_description(
        build_longform_description(body, sections=sections, safety_note=SAFETY),
        protected=SAFETY,
    )
    assert _nbytes(desc) <= MAX_DESCRIPTION_BYTES
    assert desc.endswith(SAFETY)


def test_cap_shortens_body_only_when_disclaimer_alone_leaves_room():
    body = "word " * 2000
    desc = build_longform_description(body, safety_note=SAFETY)
    assert _nbytes(desc) <= MAX_DESCRIPTION_BYTES
    assert desc.endswith(SAFETY)
    assert desc.count("...") >= 1


def test_fit_keeps_text_that_already_fits():
    text = "short description"
    assert fit_longform_description(text) == text


# ---- tags ---------------------------------------------------------------------------


def test_tags_dedupe_case_insensitively_keeping_first_spelling():
    assert dedupe_tags(["Focus", "focus", "FOCUS", "#habits", "habits "], 15) == [
        "Focus",
        "habits",
    ]


def test_longform_tags_capped_at_fifteen():
    raw = [f"tag{i}" for i in range(30)]
    tags = cap_tags(raw, longform=True)
    assert len(tags) == MAX_TAGS_LONGFORM == 15
    assert tags[0] == "tag0"


def test_shorts_tags_capped_at_ten():
    raw = [f"Tag{i}" for i in range(30)] + ["tag1"]
    tags = cap_tags(raw, longform=False)
    assert len(tags) == MAX_TAGS_SHORTS == 10
    assert len({t.casefold() for t in tags}) == 10


# ---- title hashtags -----------------------------------------------------------------


def test_title_variants_keep_only_shorts_hashtag_and_fit_limit():
    variants = generate_title_variants(
        hook="Focus beats #Motivation every time #Habits #Discipline " * 3,
        channel="Chan",
        title="The #Secret of #Success",
    )
    assert len(variants) == 3
    for v in variants:
        assert len(v["title"]) < 100
        assert v["title"].endswith("#Shorts")
        assert v["title"].count("#") == 1


# ---- publish validation -------------------------------------------------------------


def _result(tmp_path: Path, **over) -> LongformResult:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    base = dict(
        topic="discipline",
        title="Discipline: small habits",
        video_path=video,
        thumbnail_path=tmp_path / "t.png",
        duration=120.0,
        description="Hook.\n\n1. Motivation fades\n\n" + SAFETY,
        hashtags=["discipline"],
        provider="api",
    )
    base.update(over)
    return LongformResult(**base)


def test_validate_warns_and_falls_back_on_empty_title(tmp_path, caplog):
    res = _result(tmp_path)
    with caplog.at_level(logging.WARNING, logger=pub.logger.name):
        title, _desc, _tags = pub.validate_longform_upload(
            res, "   ", res.description, ["a", "b", "c"]
        )
    assert title == "Discipline"
    assert any("title" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_validate_truncates_overlong_title_with_warning(tmp_path, caplog):
    res = _result(tmp_path)
    long_title = "Word " * 40
    with caplog.at_level(logging.WARNING, logger=pub.logger.name):
        title, _d, _t = pub.validate_longform_upload(
            res, long_title, res.description, ["a", "b", "c"]
        )
    assert len(title) <= 100
    assert title.endswith("...")
    assert any("field: title" in r.getMessage() for r in caplog.records)


def test_validate_fills_short_tag_list_and_warns_when_still_short(tmp_path, caplog):
    res = _result(tmp_path, title="Quiet", topic="")
    with caplog.at_level(logging.WARNING, logger=pub.logger.name):
        _t, _d, tags = pub.validate_longform_upload(res, "Quiet", res.description, ["x", "X"])
    assert len(tags) == 2
    assert any("tags" in r.getMessage() for r in caplog.records)


def _fake_uploader(record: dict):
    class FakeUploader:
        def __init__(self, **kw):
            pass

        def upload(self, **kw):
            record.update(kw)
            return SimpleNamespace(url="https://youtube.com/watch?v=abc")

    return FakeUploader


def _cfg(**upload_extra):
    upload = SimpleNamespace(default_privacy_status="unlisted", category_id="27", **upload_extra)
    return SimpleNamespace(upload=upload)


@pytest.fixture
def counter(tmp_path, monkeypatch):
    path = tmp_path / "longform_uploads.json"
    monkeypatch.setattr(pub, "_counter_path", lambda: path)
    return path


def test_publish_scheduled_sets_publish_at_and_private(counter, tmp_path, monkeypatch):
    import autoshorts.quota.quota_tracker as qt
    import autoshorts.uploader.youtube_api as yt

    seen: dict = {}
    monkeypatch.setattr(yt, "YouTubeApiUploader", _fake_uploader(seen))
    monkeypatch.setattr(qt, "QuotaTracker", lambda **kw: object())
    pub.publish_longform(
        _result(tmp_path),
        config=_cfg(publish_at="2030-01-01T12:00:00+00:00"),
        state_manager=object(),
    )
    assert seen["privacy_status"] == "private"
    assert seen["publish_at"] == "2030-01-01T12:00:00+00:00"


def test_publish_unscheduled_keeps_default_privacy_and_no_publish_at(
    counter, tmp_path, monkeypatch
):
    import autoshorts.quota.quota_tracker as qt
    import autoshorts.uploader.youtube_api as yt

    seen: dict = {}
    monkeypatch.setattr(yt, "YouTubeApiUploader", _fake_uploader(seen))
    monkeypatch.setattr(qt, "QuotaTracker", lambda **kw: object())
    pub.publish_longform(_result(tmp_path), config=_cfg(), state_manager=object())
    assert seen["privacy_status"] == "unlisted"
    assert "publish_at" not in seen


def test_publish_sends_validated_title_description_and_tags(counter, tmp_path, monkeypatch):
    import autoshorts.quota.quota_tracker as qt
    import autoshorts.uploader.youtube_api as yt

    seen: dict = {}
    monkeypatch.setattr(yt, "YouTubeApiUploader", _fake_uploader(seen))
    monkeypatch.setattr(qt, "QuotaTracker", lambda **kw: object())
    res = _result(tmp_path, hashtags=["Focus", "focus", "habits", "routine"])
    pub.publish_longform(res, config=_cfg(), state_manager=object())
    assert seen["title"] == "Discipline: small habits"
    assert _nbytes(seen["description"]) <= MAX_DESCRIPTION_BYTES
    assert seen["tags"] == ["Focus", "habits", "routine"]
