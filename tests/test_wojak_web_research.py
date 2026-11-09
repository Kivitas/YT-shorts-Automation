"""Tests for Wojak web fact research. All lookups use an injected fake opener (no network)."""

from __future__ import annotations

import json
import logging
import urllib.error
from pathlib import Path

import pytest

from autoshorts.longform import wojak_web_research as wr
from autoshorts.longform.wojak_web_research import (
    FactCard,
    fetch_fact_card,
    fetch_fact_cards,
    summarize_for_caption,
)


class FakeWikipedia:
    """Stands in for the urllib opener. Routes by URL substring; unmatched URLs get a 404."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        for needle, response in self.routes.items():
            if needle in url:
                if isinstance(response, BaseException):
                    raise response
                assert isinstance(response, str)
                return response
        raise http_error(url, 404)


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "test error", hdrs=None, fp=None)


def summary_payload(title: str, extract: str, page_url: str | None = None) -> str:
    payload: dict[str, object] = {"title": title, "extract": extract}
    if page_url is not None:
        payload["content_urls"] = {"desktop": {"page": page_url}}
    return json.dumps(payload)


NO_SEARCH_HITS = json.dumps({"query": {"search": []}})
HOSPITAL_EXTRACT = (
    "A hospital is an institution providing\nmedical and surgical treatment.\n"
    "Hospitals vary widely by country."
)


def make_card(summary: str) -> FactCard:
    return FactCard(topic="T", title="T", summary=summary, source_url="https://example.org/T")


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wr, "RETRY_BACKOFF_SECONDS", 0.0)


# ==============================================================================
# fetch_fact_card
# ==============================================================================


def test_success_returns_fact_card(tmp_path: Path) -> None:
    opener = FakeWikipedia(
        {
            "summary/Hospital": summary_payload(
                "Hospital",
                HOSPITAL_EXTRACT,
                page_url="https://en.wikipedia.org/wiki/Hospital",
            ),
        }
    )

    card = fetch_fact_card("Hospital", cache_dir=tmp_path, opener=opener)

    assert card == FactCard(
        topic="Hospital",
        title="Hospital",
        summary=(
            "A hospital is an institution providing medical and surgical treatment. "
            "Hospitals vary widely by country."
        ),
        source_url="https://en.wikipedia.org/wiki/Hospital",
    )
    assert opener.urls == [
        "https://en.wikipedia.org/api/rest_v1/page/summary/Hospital",
    ]


def test_missing_content_urls_builds_page_url(tmp_path: Path) -> None:
    opener = FakeWikipedia({"summary/Universal": summary_payload("Universal healthcare", "X.")})

    card = fetch_fact_card("Universal healthcare", cache_dir=tmp_path, opener=opener)

    assert card is not None
    assert card.source_url == "https://en.wikipedia.org/wiki/Universal_healthcare"


def test_404_returns_none_and_logs_topic(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    opener = FakeWikipedia({"api.php": NO_SEARCH_HITS})

    with caplog.at_level(logging.INFO, logger=wr.__name__):
        card = fetch_fact_card("Ruritanian Hospitals", cache_dir=tmp_path, opener=opener)

    assert card is None
    assert "Ruritanian Hospitals" in caplog.text


def test_404_falls_back_to_search(tmp_path: Path) -> None:
    opener = FakeWikipedia(
        {
            "api.php": json.dumps({"query": {"search": [{"title": "Hospital"}]}}),
            "summary/Hospital": summary_payload("Hospital", "A hospital is a facility."),
        }
    )

    # "Ruritanian Clinics" has no page of its own, so the summary route must not match it.
    card = fetch_fact_card("Ruritanian Clinics", cache_dir=tmp_path, opener=opener)

    assert card is not None
    assert card.topic == "Ruritanian Clinics"
    assert card.title == "Hospital"
    assert card.summary == "A hospital is a facility."
    assert any("api.php" in url and "srsearch=Ruritanian+Clinics" in url for url in opener.urls)


def test_malformed_json_returns_none_and_logs_topic(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    opener = FakeWikipedia({"summary/Hospital": "<html>Service unavailable</html>"})

    with caplog.at_level(logging.WARNING, logger=wr.__name__):
        card = fetch_fact_card("Hospital", cache_dir=tmp_path, opener=opener)

    assert card is None
    assert "malformed JSON" in caplog.text
    assert "Hospital" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        json.dumps({"title": "Hospital", "extract": ""}),
        json.dumps({"title": "Hospital", "extract": "   \n\t "}),
        json.dumps({"title": "Hospital"}),
        json.dumps(["not", "a", "dict"]),
    ],
)
def test_empty_or_missing_extract_returns_none(tmp_path: Path, body: str) -> None:
    opener = FakeWikipedia({"summary/Hospital": body})

    assert fetch_fact_card("Hospital", cache_dir=tmp_path, opener=opener) is None


def test_network_error_retries_then_returns_none(tmp_path: Path) -> None:
    opener = FakeWikipedia({"summary/Hospital": urllib.error.URLError("no network")})

    card = fetch_fact_card("Hospital", cache_dir=tmp_path, opener=opener)

    assert card is None
    # One attempt plus MAX_RETRIES retries, all against the summary endpoint.
    assert len(opener.urls) == wr.MAX_RETRIES + 1
    assert len(set(opener.urls)) == 1


def test_transient_error_recovers_on_retry(tmp_path: Path) -> None:
    calls: list[str] = []

    def flaky(url: str) -> str:
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return summary_payload("Hospital", "A hospital is a facility.")

    card = fetch_fact_card("Hospital", cache_dir=tmp_path, opener=flaky)

    assert card is not None
    assert card.summary == "A hospital is a facility."
    assert len(calls) == 2


def test_server_error_is_retried(tmp_path: Path) -> None:
    opener = FakeWikipedia({"summary/Hospital": http_error("u", 503)})

    assert fetch_fact_card("Hospital", cache_dir=tmp_path, opener=opener) is None
    assert len(opener.urls) == wr.MAX_RETRIES + 1


def test_client_error_is_not_retried(tmp_path: Path) -> None:
    opener = FakeWikipedia({"summary/Hospital": http_error("u", 403)})

    assert fetch_fact_card("Hospital", cache_dir=tmp_path, opener=opener) is None
    assert len(opener.urls) == 1


def test_unexpected_opener_error_returns_none(tmp_path: Path) -> None:
    opener = FakeWikipedia({"summary/Hospital": RuntimeError("boom")})

    assert fetch_fact_card("Hospital", cache_dir=tmp_path, opener=opener) is None
    assert len(opener.urls) == 1


def test_empty_topic_returns_none_without_calling_opener(tmp_path: Path) -> None:
    opener = FakeWikipedia({})

    assert fetch_fact_card("   ", cache_dir=tmp_path, opener=opener) is None
    assert opener.urls == []


def test_cache_hit_avoids_second_opener_call(tmp_path: Path) -> None:
    first = FakeWikipedia({"summary/Hospital": summary_payload("Hospital", "A hospital.")})
    card = fetch_fact_card("Hospital", cache_dir=tmp_path, opener=first)

    second_calls: list[str] = []

    def must_not_be_called(url: str) -> str:
        second_calls.append(url)
        raise AssertionError("cache should have answered this lookup")

    cached = fetch_fact_card("  hospital ", cache_dir=tmp_path, opener=must_not_be_called)

    assert card is not None
    assert cached is not None
    # The cache stores title, summary and source; the topic reflects the current request.
    assert cached.topic == "hospital"
    assert (cached.title, cached.summary, cached.source_url) == (
        card.title,
        card.summary,
        card.source_url,
    )
    assert len(first.urls) == 1
    assert second_calls == []
    assert any(tmp_path.glob("*.json"))


def test_force_refresh_bypasses_cache(tmp_path: Path) -> None:
    old = FakeWikipedia({"summary/Hospital": summary_payload("Hospital", "Old text.")})
    fetch_fact_card("Hospital", cache_dir=tmp_path, opener=old)

    new = FakeWikipedia({"summary/Hospital": summary_payload("Hospital", "New text.")})
    refreshed = fetch_fact_card("Hospital", cache_dir=tmp_path, opener=new, force_refresh=True)

    assert refreshed is not None
    assert refreshed.summary == "New text."
    assert len(new.urls) == 1

    # The refreshed result replaces the cache entry.
    reread = fetch_fact_card("Hospital", cache_dir=tmp_path, opener=FakeWikipedia({}))
    assert reread is not None
    assert reread.summary == "New text."


def test_failures_are_not_cached(tmp_path: Path) -> None:
    failing = FakeWikipedia({})
    assert fetch_fact_card("Hospital", cache_dir=tmp_path, opener=failing) is None
    assert not any(tmp_path.glob("*.json"))

    working = FakeWikipedia({"summary/Hospital": summary_payload("Hospital", "A hospital.")})
    card = fetch_fact_card("Hospital", cache_dir=tmp_path, opener=working)

    assert card is not None
    assert len(working.urls) == 1


def test_corrupt_cache_entry_is_ignored(tmp_path: Path) -> None:
    cache_path = wr._fact_cache_file(tmp_path, "Hospital")
    cache_path.write_text("{not valid json", encoding="utf-8")
    opener = FakeWikipedia({"summary/Hospital": summary_payload("Hospital", "A hospital.")})

    card = fetch_fact_card("Hospital", cache_dir=tmp_path, opener=opener)

    assert card is not None
    assert card.summary == "A hospital."
    assert len(opener.urls) == 1


# ==============================================================================
# fetch_fact_cards
# ==============================================================================


def test_fetch_fact_cards_skips_failures_and_keeps_order(tmp_path: Path) -> None:
    opener = FakeWikipedia(
        {
            "summary/Alpha": summary_payload("Alpha", "Alpha is first."),
            "summary/Gamma": summary_payload("Gamma", "Gamma is third."),
        }
    )

    cards = fetch_fact_cards(["Gamma", "Beta", "Alpha"], cache_dir=tmp_path, opener=opener)

    assert [card.topic for card in cards] == ["Gamma", "Alpha"]
    assert [card.title for card in cards] == ["Gamma", "Alpha"]


def test_fetch_fact_cards_empty_input_returns_empty_list(tmp_path: Path) -> None:
    assert fetch_fact_cards([], cache_dir=tmp_path, opener=FakeWikipedia({})) == []


# ==============================================================================
# summarize_for_caption
# ==============================================================================


def test_summarize_takes_first_sentence() -> None:
    card = make_card("Hospitals treat patients. They are large. Very large.")

    assert summarize_for_caption(card) == "Hospitals treat patients."


def test_summarize_skips_abbreviation_periods() -> None:
    card = make_card("The U.S. has many hospitals. They vary by state.")

    assert summarize_for_caption(card) == "The U.S. has many hospitals."


def test_summarize_keeps_decimal_numbers_intact() -> None:
    card = make_card("Costs reached 3.5 million dollars. Later they fell.")

    assert summarize_for_caption(card) == "Costs reached 3.5 million dollars."


def test_summarize_respects_max_chars_at_word_boundary() -> None:
    card = make_card("The quick brown fox jumps over the lazy dog.")

    result = summarize_for_caption(card, max_chars=20)

    assert result == "The quick brown fox"
    assert len(result) <= 20
    assert not result.endswith(" ")


def test_summarize_exact_fit_is_unchanged() -> None:
    sentence = "Alpha beta gamma."

    assert summarize_for_caption(make_card(sentence), max_chars=len(sentence)) == sentence


def test_summarize_cut_on_space_leaves_no_trailing_space() -> None:
    result = summarize_for_caption(make_card("Alpha beta gamma delta"), max_chars=10)

    assert result == "Alpha beta"


def test_summarize_single_word_over_limit_returns_empty() -> None:
    result = summarize_for_caption(make_card("Supercalifragilisticexpialidocious is long."), 5)

    assert result == ""


def test_summarize_default_limit_is_120_and_clean() -> None:
    long_summary = " ".join(["word"] * 80) + "."

    result = summarize_for_caption(make_card(long_summary))

    assert len(result) <= 120
    assert result.endswith("word")
    assert not result.endswith(" ")


def test_summarize_normalizes_whitespace() -> None:
    card = make_card("Hospitals\n\n  treat   patients.")

    assert summarize_for_caption(card) == "Hospitals treat patients."


def test_summarize_empty_summary_returns_empty() -> None:
    assert summarize_for_caption(make_card("   ")) == ""


def test_summarize_rejects_nonpositive_max_chars() -> None:
    with pytest.raises(ValueError):
        summarize_for_caption(make_card("Text."), max_chars=0)


# ==============================================================================
# Channel format profiles (yt-dlp listing and caption sampling are injected)
# ==============================================================================

CHANNEL = "https://www.youtube.com/@Wojak-Explainer"
CHANNEL_VIDEOS = "https://www.youtube.com/@Wojak-Explainer/videos"


def raw_entry(video_id: str, title: str, duration: object) -> dict[str, object]:
    return {"_type": "url", "id": video_id, "title": title, "duration": duration}


def fake_lister(entries: list[dict[str, object]], calls: list[tuple[str, int]] | None = None):
    def lister(url: str, limit: int) -> list[dict[str, object]]:
        if calls is not None:
            calls.append((url, limit))
        return entries

    return lister


def fake_captions(by_video: dict[str, list[str]]):
    def fetcher(video_id: str) -> list[str]:
        if video_id not in by_video:
            raise RuntimeError("TranscriptsDisabled")
        return by_video[video_id]

    return fetcher


SAMPLE_ENTRIES = [
    raw_entry("v1", "Types of Hospitals Be Like", 120),
    raw_entry("v2", "Every Country's Hospital Be Like", 180),
    raw_entry("v3", "Every Country's Hospital Be Like \U0001f3e5", 240),
    raw_entry("v4", "Types of Hospitals Be Like", None),
    raw_entry("v5", "Random unrelated title", 60),
]


def test_normalize_channel_videos_url() -> None:
    assert wr.normalize_channel_videos_url("https://www.youtube.com/@SapX") == (
        "https://www.youtube.com/@SapX/videos"
    )
    assert wr.normalize_channel_videos_url("https://www.youtube.com/@SapX/") == (
        "https://www.youtube.com/@SapX/videos"
    )
    assert wr.normalize_channel_videos_url("https://www.youtube.com/@SapX/shorts") == (
        "https://www.youtube.com/@SapX/shorts"
    )


def test_summarize_title_patterns_counts_most_frequent_first() -> None:
    titles = [
        "Types of Hospitals Be Like",
        "Every Country's Hospital Be Like",
        "Every Country's Hospital Be Like \U0001f3e5",
        "Types of Hospitals Be Like",
        "Every Doctor in a Nutshell",
        "Something Be Like Nothing",
        "No template here",
    ]

    patterns = wr.summarize_title_patterns(titles)

    assert patterns == (
        ("Types of {X} Be Like", 2),
        ("Every Country's {X}", 2),
        ("Every {X} in a Nutshell", 1),
        ("{X} Be Like", 1),
    )


def test_build_format_profile_summarises_channel(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []
    profile = wr.build_format_profile(
        CHANNEL,
        cache_dir=tmp_path,
        video_limit=5,
        caption_sample=3,
        lister=fake_lister(SAMPLE_ENTRIES, calls),
        caption_fetcher=fake_captions(
            {
                "v1": ["one", "two", "three", "four"],
                "v2": ["hello there", "bye"],
                "v3": ["x" * 10] * 6,
            }
        ),
    )

    assert calls == [(CHANNEL_VIDEOS, 5)]
    assert profile is not None
    assert profile.channel_url == CHANNEL_VIDEOS
    assert profile.video_count == 5
    # Durations 120, 180, 240, 60 (the None entry is skipped): median is 150.
    assert profile.median_duration_seconds == 150.0
    assert ("Types of {X} Be Like", 2) in profile.title_patterns
    # Three sampled videos with 4, 2 and 6 segments: median 4.
    assert profile.segment_count_estimate == 4
    assert profile.captioned_videos == 3
    # Segment lengths 3,3,5,4 (v1), 11,3 (v2), 10 x6 (v3): median 10 characters.
    assert profile.typical_caption_chars == 10
    assert [video.video_id for video in profile.recent_videos] == ["v1", "v2", "v3", "v4", "v5"]


def test_caption_failures_are_skipped(tmp_path: Path) -> None:
    profile = wr.build_format_profile(
        CHANNEL,
        cache_dir=tmp_path,
        caption_sample=2,
        lister=fake_lister(SAMPLE_ENTRIES),
        caption_fetcher=fake_captions({"v2": ["only", "segments"]}),
    )

    assert profile is not None
    assert profile.captioned_videos == 1
    assert profile.segment_count_estimate == 2


def test_no_captions_leaves_caption_fields_empty(tmp_path: Path) -> None:
    profile = wr.build_format_profile(
        CHANNEL,
        cache_dir=tmp_path,
        lister=fake_lister(SAMPLE_ENTRIES),
        caption_fetcher=fake_captions({}),
    )

    assert profile is not None
    assert profile.captioned_videos == 0
    assert profile.segment_count_estimate is None
    assert profile.typical_caption_chars is None


def test_listing_failure_returns_none(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    def broken(url: str, limit: int) -> list[dict[str, object]]:
        raise OSError("network down")

    with caplog.at_level(logging.WARNING, logger=wr.__name__):
        profile = wr.build_format_profile(CHANNEL, cache_dir=tmp_path, lister=broken)

    assert profile is None
    assert "network down" in caplog.text
    assert not any(tmp_path.glob("*.json"))


def test_empty_listing_returns_none(tmp_path: Path) -> None:
    profile = wr.build_format_profile(
        CHANNEL, cache_dir=tmp_path, lister=fake_lister([]), caption_fetcher=fake_captions({})
    )

    assert profile is None


def test_profile_cache_hit_and_force_refresh(tmp_path: Path) -> None:
    first = wr.build_format_profile(
        CHANNEL,
        cache_dir=tmp_path,
        lister=fake_lister(SAMPLE_ENTRIES),
        caption_fetcher=fake_captions({}),
    )

    def must_not_list(url: str, limit: int) -> list[dict[str, object]]:
        raise AssertionError("cache should answer this lookup")

    cached = wr.build_format_profile(
        CHANNEL, cache_dir=tmp_path, lister=must_not_list, caption_fetcher=fake_captions({})
    )
    assert cached == first

    fresh_entries = [raw_entry("n1", "Every Nurse in a Nutshell", 90)]
    calls: list[tuple[str, int]] = []
    refreshed = wr.build_format_profile(
        CHANNEL,
        cache_dir=tmp_path,
        force_refresh=True,
        lister=fake_lister(fresh_entries, calls),
        caption_fetcher=fake_captions({}),
    )

    assert calls == [(CHANNEL_VIDEOS, wr.DEFAULT_VIDEO_LIMIT)]
    assert refreshed is not None
    assert refreshed.video_count == 1
    assert refreshed.recent_videos[0].title == "Every Nurse in a Nutshell"


def test_profile_cache_round_trips_emoji_titles(tmp_path: Path) -> None:
    entries = [raw_entry("e1", "Types of Hospitals Be Like \U0001f3e5\U0001f489", 100)]
    built = wr.build_format_profile(
        CHANNEL, cache_dir=tmp_path, lister=fake_lister(entries), caption_fetcher=fake_captions({})
    )

    reloaded = wr.build_format_profile(
        CHANNEL,
        cache_dir=tmp_path,
        lister=fake_lister([]),
        caption_fetcher=fake_captions({}),
    )

    assert built is not None
    assert reloaded == built
    assert reloaded is not None
    assert reloaded.recent_videos[0].title.endswith("\U0001f3e5\U0001f489")


def test_malformed_entries_are_ignored(tmp_path: Path) -> None:
    entries: list[dict[str, object]] = [
        {"id": None, "title": "no id"},
        raw_entry("ok1", "Types of Doctors Be Like", "long"),
        raw_entry("ok2", "Types of Doctors Be Like", 30),
    ]

    profile = wr.build_format_profile(
        CHANNEL, cache_dir=tmp_path, lister=fake_lister(entries), caption_fetcher=fake_captions({})
    )

    assert profile is not None
    assert [video.video_id for video in profile.recent_videos] == ["ok1", "ok2"]
    assert profile.median_duration_seconds == 30.0


def test_empty_channel_url_returns_none(tmp_path: Path) -> None:
    assert wr.build_format_profile("  ", cache_dir=tmp_path, lister=fake_lister([])) is None
