"""Tests for the watching/ranking format classifier."""

from autoshorts.core.models import EditDecisionList
from autoshorts.decider.format_classifier import classify_format


def test_watching_transcript():
    t = "Look at this guy trying to jump the gap. Watch what happens next, it is wild."
    d = classify_format(t, 30.0)
    assert d.kind == "watching"
    assert d.rank_count is None


def test_ranking_five_to_one():
    t = (
        "Ranking the worst to best plays. Number five, the missed dunk. "
        "Number four, the airball. Number three, the bad pass. "
        "Number two, the buzzer shot. And number one, the game winner."
    )
    d = classify_format(t, 40.0)
    assert d.kind == "ranking"
    assert d.rank_count == 5
    assert 0.5 < d.confidence <= 1.0


def test_hash_and_digits_countdown():
    t = "#6 first. #5 next. #4 then. #3 wow. #2 crazy. #1 best."
    d = classify_format(t, 35.0)
    assert d.kind == "ranking"
    assert d.rank_count == 6


def test_single_number_one_stays_watching():
    d = classify_format("This is the number one rule of cooking, top five tips later.", 30.0)
    assert d.kind == "watching"


def test_two_ranks_watching():
    d = classify_format("Number two is bad but number one is worse, ranked last place.", 30.0)
    assert d.kind == "watching"


def test_ascending_order_not_ranking():
    d = classify_format("Number one, number two, number three, number four.", 30.0)
    assert d.kind == "watching"


def test_edl_default_edit_format():
    assert EditDecisionList().edit_format == "standard"


def test_top_five_with_digit_countdown_reasons():
    t = "Top 5 worst plays. 5. The airball. 4. The brick. 3. The fumble. 2. The trip. 1. The dunk fail."
    d = classify_format(t, 40.0)
    assert d.kind == "ranking"
    assert d.rank_count == 5
    assert any("cues" in r and "top_5" in r for r in d.reasons)
    assert any("descending run" in r for r in d.reasons)


def test_list_of_two_is_watching():
    d = classify_format("Number two was funny, but number one was a total disaster. Top two moments.", 30.0)
    assert d.kind == "watching"
    assert d.rank_count is None


def test_cue_without_countdown_is_watching():
    d = classify_format("He ranked first in class, then third, then second at the finals.", 30.0)
    assert d.kind == "watching"


def test_zigzag_numbers_without_cue_is_watching():
    d = classify_format("Number three is fine. Number one is odd. Number four is long. Number two ends.", 30.0)
    assert d.kind == "watching"


def test_empty_text_watching_reason():
    d = classify_format("", 30.0)
    assert d.kind == "watching"
    assert d.reasons
