"""Curated long-form video idea cards, ranked deterministically from the built-in topic catalogue."""

from __future__ import annotations

import re
import zlib
from collections.abc import Iterable
from dataclasses import dataclass

from autoshorts.longform.topics import GENRE_KEYS, MAX_TITLE_CHARS, TOPICS, TopicSpec, title_for
from autoshorts.longform.types import VALID_VISUAL_MODES

# Visual modes that have a renderer (every valid mode except the generic "auto" placeholder).
RENDERED_VISUAL_MODES = frozenset(m for m in VALID_VISUAL_MODES if m != "auto")

RICHNESS_CAP = 6  # title keywords beyond this add no score
RICHNESS_WEIGHT = 1
RENDERER_BONUS = 2
VARIETY_PENALTY = 4  # per already-picked card of the same genre

HOOK_TEMPLATES = (
    "What most people miss about {a}, and why {b} changes the picture.",
    "{a} explained through {b}: the part nobody tells you.",
    "Can {a} and {b} really explain what you see every day?",
    "Forget the surface of {a}, because {b} is where the real story starts.",
    "The surprising link between {a} and {b} that changes how you see it.",
)


@dataclass(frozen=True)
class IdeaCard:
    key: str
    title: str
    hook: str
    genre: str
    visual_mode: str
    score: int
    reasons: tuple[str, ...]


def _normalize_key(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(value).strip().lower())


def _normalize_genre(genre: str) -> str:
    key = _normalize_key(genre)
    if key not in GENRE_KEYS:
        raise ValueError(f"Unknown genre {genre!r}; expected one of {', '.join(GENRE_KEYS)}")
    return key


def _title_for(spec: TopicSpec) -> str:
    """Use the curated label as the card title, trimmed to the title length limit."""
    label = " ".join(spec.label.split())
    if len(label) <= MAX_TITLE_CHARS:
        return label
    return title_for(spec.key)


def _hook_for(spec: TopicSpec) -> str:
    """Build a one-line hook from the first two title keywords, using a fixed template per key."""
    keywords = list(spec.title_keywords)
    if len(keywords) < 2:
        return spec.curated_hook or f"Here is what {spec.label} really means."
    a, b = keywords[0].lower(), keywords[1].lower()
    index = zlib.crc32(spec.key.encode("utf-8")) % len(HOOK_TEMPLATES)
    return HOOK_TEMPLATES[index].format(a=a, b=b)


def _base_score(spec: TopicSpec) -> tuple[int, list[str]]:
    reasons: list[str] = []
    richness = min(len(spec.title_keywords), RICHNESS_CAP) * RICHNESS_WEIGHT
    reasons.append(f"{len(spec.title_keywords)} title keywords")
    score = richness
    if spec.visual_mode in RENDERED_VISUAL_MODES:
        score += RENDERER_BONUS
        reasons.append(f"renderer ready: {spec.visual_mode}")
    return score, reasons


def generate_ideas(
    count: int = 10,
    genre: str | None = None,
    exclude: Iterable[str] | str = (),
) -> list[IdeaCard]:
    """Return up to `count` idea cards, preferring a new genre for each next pick.

    Ties are broken by topic key, so the same inputs always give the same list.
    """
    if count <= 0:
        return []
    wanted_genre = _normalize_genre(genre) if genre else None
    if isinstance(exclude, str):
        exclude = (exclude,)
    excluded = {_normalize_key(k) for k in exclude}

    candidates: dict[str, tuple[TopicSpec, int, list[str]]] = {}
    for key in sorted(TOPICS):
        if key in excluded:
            continue
        spec = TOPICS[key]
        if wanted_genre and spec.genre != wanted_genre:
            continue
        base, reasons = _base_score(spec)
        candidates[key] = (spec, base, reasons)

    picked: list[IdeaCard] = []
    genre_counts: dict[str, int] = {}
    while candidates and len(picked) < count:
        best_key = None
        best_total = None
        for key in sorted(candidates):
            spec, base, _ = candidates[key]
            total = base - VARIETY_PENALTY * genre_counts.get(spec.genre, 0)
            if best_total is None or total > best_total:
                best_key, best_total = key, total
        assert best_key is not None and best_total is not None
        spec, _, reasons = candidates.pop(best_key)
        prior = genre_counts.get(spec.genre, 0)
        if prior == 0:
            reasons = [*reasons, "new genre in list"]
        else:
            reasons = [*reasons, f"{prior} earlier {spec.genre} pick(s)"]
        if wanted_genre:
            reasons = [*reasons, f"genre match: {wanted_genre}"]
        genre_counts[spec.genre] = prior + 1
        picked.append(
            IdeaCard(
                key=spec.key,
                title=_title_for(spec),
                hook=_hook_for(spec),
                genre=spec.genre,
                visual_mode=spec.visual_mode,
                score=max(best_total, 0),
                reasons=tuple(reasons),
            )
        )
    return picked
