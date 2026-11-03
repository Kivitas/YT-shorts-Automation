from __future__ import annotations

import pytest

from autoshorts.longform.ideas import IdeaCard, generate_ideas
from autoshorts.longform.topics import GENRE_KEYS, MAX_TITLE_CHARS, TOPICS
from autoshorts.longform.types import VALID_VISUAL_MODES


def test_returns_requested_count_or_fewer() -> None:
    cards = generate_ideas(count=10)
    assert len(cards) == min(10, len(TOPICS))
    assert all(isinstance(card, IdeaCard) for card in cards)
    assert generate_ideas(count=0) == []
    assert len(generate_ideas(count=500)) == len(TOPICS)


def test_no_duplicate_keys() -> None:
    cards = generate_ideas(count=len(TOPICS))
    keys = [card.key for card in cards]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("genre", GENRE_KEYS)
def test_genre_filter(genre: str) -> None:
    cards = generate_ideas(count=10, genre=genre)
    assert cards, f"expected curated topics for genre {genre}"
    assert all(card.genre == genre for card in cards)


def test_genre_filter_accepts_dashes_and_case() -> None:
    cards = generate_ideas(count=10, genre="Self-Improvement")
    assert cards
    assert all(card.genre == "self_improvement" for card in cards)


def test_unknown_genre_raises() -> None:
    with pytest.raises(ValueError):
        generate_ideas(genre="cooking")


def test_exclude_removes_keys() -> None:
    everything = generate_ideas(count=len(TOPICS))
    excluded = [everything[0].key, everything[1].key]
    cards = generate_ideas(count=len(TOPICS), exclude=excluded)
    keys = {card.key for card in cards}
    assert not keys.intersection(excluded)
    assert len(cards) == len(TOPICS) - len(excluded)


def test_exclude_accepts_single_string() -> None:
    cards = generate_ideas(count=len(TOPICS), exclude="roman_roads")
    assert "roman_roads" not in {card.key for card in cards}


def test_exclude_combined_with_genre() -> None:
    cards = generate_ideas(count=10, genre="history", exclude=("roman_roads",))
    assert cards
    assert "roman_roads" not in {card.key for card in cards}
    assert all(card.genre == "history" for card in cards)


def test_deterministic_output() -> None:
    first = generate_ideas(count=10)
    second = generate_ideas(count=10)
    assert first == second


def test_variety_across_genres_in_top_list() -> None:
    cards = generate_ideas(count=len(GENRE_KEYS))
    genres = [card.genre for card in cards]
    assert len(set(genres)) == len(GENRE_KEYS)


def test_cards_have_valid_fields() -> None:
    for card in generate_ideas(count=len(TOPICS)):
        assert card.title and len(card.title) <= MAX_TITLE_CHARS
        assert card.hook and card.hook[-1] in ".?"
        assert "\n" not in card.hook
        assert card.visual_mode in VALID_VISUAL_MODES
        assert card.reasons
        assert card.score >= 0


def test_every_topic_has_safety_note() -> None:
    for key, spec in TOPICS.items():
        assert spec.safety_note.strip(), f"{key} is missing a safety_note"
